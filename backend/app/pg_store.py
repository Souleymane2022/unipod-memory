"""Stockage PostgreSQL + pgvector (ex. Neon relié à Vercel).

Une table par collection : texte du passage, métadonnées (source, date, auteur…) et vecteur d'embedding.
La recherche utilise la distance cosinus de pgvector (``<=>``), comme ChromaDB.
Les vecteurs sont envoyés sous forme texte ``'[0.1,0.2,…]'::vector`` : aucune dépendance en plus de psycopg.
"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .chunker import Chunk
from .config import Settings
from .parsers import ParsedDocument
from .store import BaseStore

log = logging.getLogger(__name__)

META_COLUMNS = ("source", "doc_id", "doc_type", "title", "author", "date", "date_end",
                "chunk_index", "n_chunks", "word_count", "ingested_at")


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


class PgVectorStore(BaseStore):
    kind = "postgres"

    def __init__(self, settings: Settings):
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL manquant pour le stockage PostgreSQL")
        self.url = settings.database_url
        # Nom de table sûr, dérivé de la collection (ex. unipods_memory -> unipods_memory_chunks)
        self.table = re.sub(r"[^a-z0-9_]", "_", settings.collection_name.lower()) + "_chunks"
        super().__init__(settings)
        self.dim = len(self.embed(["dimension"])[0])
        self._init_schema()

    @contextmanager
    def _conn(self):
        # Une connexion par opération : adapté au serverless (Neon gère le pool côté serveur).
        with psycopg.connect(self.url, autocommit=True, connect_timeout=15) as conn:
            yield conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    doc_type TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    date TEXT NOT NULL DEFAULT '',
                    date_end TEXT NOT NULL DEFAULT '',
                    chunk_index INTEGER NOT NULL,
                    n_chunks INTEGER NOT NULL,
                    word_count INTEGER NOT NULL,
                    ingested_at TEXT NOT NULL,
                    document TEXT NOT NULL,
                    embedding vector({self.dim}) NOT NULL
                )""")
            conn.execute(f"CREATE INDEX IF NOT EXISTS {self.table}_source_idx ON {self.table} (source)")
            conn.execute(f"CREATE TABLE IF NOT EXISTS {self.table}_events "
                         "(id BIGSERIAL PRIMARY KEY, event JSONB NOT NULL)")

    def reset(self) -> None:
        with self._conn() as conn:
            conn.execute(f"DROP TABLE IF EXISTS {self.table}")
        self._init_schema()

    def delete_source(self, source: str) -> int:
        with self._conn() as conn:
            return conn.execute(f"DELETE FROM {self.table} WHERE source = %s", (source,)).rowcount

    def add_document(self, doc: ParsedDocument, chunks: list[Chunk]) -> dict[str, Any]:
        """Ajoute (ou remplace) tous les chunks d'une source, dans une seule transaction."""
        doc_id, records = self._records(doc, chunks)
        vectors = self.embed([text for _, text, _ in records]) if records else []
        cols = ", ".join(("id",) + META_COLUMNS + ("document", "embedding"))
        placeholders = ", ".join(["%s"] * (len(META_COLUMNS) + 2) + ["%s::vector"])
        with self._conn() as conn, conn.transaction():
            replaced = conn.execute(f"DELETE FROM {self.table} WHERE source = %s", (doc.source,)).rowcount
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {self.table} ({cols}) VALUES ({placeholders})",
                    [(id_, *[meta[c] for c in META_COLUMNS], text, _vec(vec))
                     for (id_, text, meta), vec in zip(records, vectors)],
                )
        return {"source": doc.source, "doc_id": doc_id, "doc_type": doc.doc_type, "chunks": len(records),
                "replaced_chunks": replaced}

    def _rows(self, rows) -> list[dict[str, Any]]:
        out = []
        for row in rows:
            id_, *meta_values, text = row[: 2 + len(META_COLUMNS)]
            out.append({"id": id_, "text": text, "metadata": dict(zip(META_COLUMNS, meta_values)),
                        **({"similarity": float(row[-1])} if len(row) > 2 + len(META_COLUMNS) else {})})
        return out

    def query(self, texts: str | list[str], n: int) -> list[dict[str, Any]]:
        texts = [texts] if isinstance(texts, str) else texts
        cols = ", ".join(("id",) + META_COLUMNS + ("document",))
        per_query = []
        with self._conn() as conn:
            for vec in self.embed(texts):
                v = _vec(vec)
                rows = conn.execute(
                    f"SELECT {cols}, 1 - (embedding <=> %s::vector) FROM {self.table} "
                    f"ORDER BY embedding <=> %s::vector LIMIT %s", (v, v, n)).fetchall()
                per_query.append(self._rows(rows))
        return self._merge_hits(per_query)

    def get_source_chunks(self, source: str) -> list[dict[str, Any]]:
        cols = ", ".join(("id",) + META_COLUMNS + ("document",))
        with self._conn() as conn:
            rows = conn.execute(f"SELECT {cols} FROM {self.table} WHERE source = %s ORDER BY chunk_index",
                                (source,)).fetchall()
        return self._rows(rows)

    def list_documents(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(f"SELECT {', '.join(META_COLUMNS)} FROM {self.table}").fetchall()
        return self._summarize_documents([dict(zip(META_COLUMNS, r)) for r in rows])

    def log_event(self, event: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(f"INSERT INTO {self.table}_events (event) VALUES (%s)", (Jsonb(event),))
            conn.execute(f"DELETE FROM {self.table}_events WHERE id <= "
                         f"(SELECT max(id) - 50 FROM {self.table}_events)")  # on ne garde que les 50 derniers

    def recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(f"SELECT event FROM {self.table}_events ORDER BY id DESC LIMIT %s",
                                (limit,)).fetchall()
        return [r[0] for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute(f"SELECT count(*) FROM {self.table}").fetchone()[0]

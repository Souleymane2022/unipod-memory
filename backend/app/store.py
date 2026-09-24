"""Base vectorielle locale ChromaDB (persistante sur disque)."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.utils import embedding_functions

from .chunker import Chunk
from .config import Settings
from .parsers import ParsedDocument

log = logging.getLogger(__name__)


def build_embedding_function(settings: Settings):
    backend = settings.embedding_backend.lower()
    if backend in ("sentence-transformers", "sentence_transformers", "st"):
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=settings.embedding_model or "paraphrase-multilingual-MiniLM-L12-v2"
        )
    if backend == "openai":
        return embedding_functions.OpenAIEmbeddingFunction(
            api_key=settings.openai_api_key,
            model_name=settings.embedding_model or "text-embedding-3-small",
        )
    # Par défaut : all-MiniLM-L6-v2 en ONNX, exécuté localement, sans clé ni torch.
    if settings.model_cache_dir:  # ex. /tmp sur Vercel, où ~/.cache n'est pas inscriptible
        onnx = embedding_functions.ONNXMiniLM_L6_V2
        onnx.DOWNLOAD_PATH = Path(settings.model_cache_dir) / onnx.MODEL_NAME
    return embedding_functions.DefaultEmbeddingFunction()


class VectorStore:
    def __init__(self, settings: Settings):
        settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_function = build_embedding_function(settings)
        self.client = chromadb.PersistentClient(
            path=str(settings.chroma_dir), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(
            name=settings.collection_name,
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine"},
        )
        if settings.model_cache_dir:
            self._free_model_archive()

    def _free_model_archive(self) -> None:
        """Charge le modèle puis supprime l'archive .tar.gz (~80 Mo) pour économiser /tmp."""
        self.embedding_function(["initialisation"])
        onnx = embedding_functions.ONNXMiniLM_L6_V2
        archive = Path(onnx.DOWNLOAD_PATH) / onnx.ARCHIVE_FILENAME
        archive.unlink(missing_ok=True)

    @staticmethod
    def doc_id_for(source: str) -> str:
        return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]

    def delete_source(self, source: str) -> int:
        existing = self.collection.get(where={"source": source}, include=[])
        if existing["ids"]:
            self.collection.delete(ids=existing["ids"])
        return len(existing["ids"])

    def add_document(self, doc: ParsedDocument, chunks: list[Chunk]) -> dict[str, Any]:
        """Ajoute (ou remplace) tous les chunks d'une source."""
        replaced = self.delete_source(doc.source)
        doc_id = self.doc_id_for(doc.source)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ids, texts, metas = [], [], []
        for c in chunks:
            ids.append(f"{doc_id}:{c.index}")
            texts.append(c.text)
            metas.append(
                {
                    "source": doc.source,
                    "doc_id": doc_id,
                    "doc_type": doc.doc_type,
                    "title": doc.title,
                    "author": ", ".join(c.authors),
                    "date": c.date_start,
                    "date_end": c.date_end,
                    "chunk_index": c.index,
                    "n_chunks": len(chunks),
                    "word_count": c.word_count,
                    "ingested_at": now,
                }
            )
        if ids:
            self.collection.add(ids=ids, documents=texts, metadatas=metas)
        return {"source": doc.source, "doc_id": doc_id, "doc_type": doc.doc_type, "chunks": len(ids),
                "replaced_chunks": replaced}

    def query(self, texts: str | list[str], n: int) -> list[dict[str, Any]]:
        """Plus proches voisins ; avec plusieurs requêtes, garde la meilleure similarité par chunk."""
        count = self.collection.count()
        if count == 0:
            return []
        texts = [texts] if isinstance(texts, str) else texts
        res = self.collection.query(
            query_texts=texts, n_results=min(n, count), include=["documents", "metadatas", "distances"]
        )
        hits: dict[str, dict[str, Any]] = {}
        for q in range(len(texts)):
            for id_, doc, meta, dist in zip(res["ids"][q], res["documents"][q], res["metadatas"][q],
                                            res["distances"][q]):
                sim = 1.0 - float(dist)
                if id_ not in hits or sim > hits[id_]["similarity"]:
                    hits[id_] = {"id": id_, "text": doc, "metadata": meta, "similarity": sim}
        return sorted(hits.values(), key=lambda h: h["similarity"], reverse=True)

    def get_source_chunks(self, source: str) -> list[dict[str, Any]]:
        res = self.collection.get(where={"source": source}, include=["documents", "metadatas"])
        items = [{"id": i, "text": d, "metadata": m} for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])]
        return sorted(items, key=lambda x: x["metadata"].get("chunk_index", 0))

    def list_documents(self) -> list[dict[str, Any]]:
        res = self.collection.get(include=["metadatas"])
        docs: dict[str, dict[str, Any]] = {}
        for meta in res["metadatas"]:
            d = docs.setdefault(
                meta["source"],
                {"source": meta["source"], "doc_type": meta.get("doc_type", ""), "title": meta.get("title", ""),
                 "chunks": 0, "words": 0, "authors": set(), "date_start": meta.get("date", ""),
                 "date_end": meta.get("date_end", ""), "ingested_at": meta.get("ingested_at", "")},
            )
            d["chunks"] += 1
            d["words"] += int(meta.get("word_count", 0))
            for a in (meta.get("author") or "").split(", "):
                if a:
                    d["authors"].add(a)
            if meta.get("date") and (not d["date_start"] or meta["date"] < d["date_start"]):
                d["date_start"] = meta["date"]
            if meta.get("date_end") and meta["date_end"] > d["date_end"]:
                d["date_end"] = meta["date_end"]
        out = []
        for d in docs.values():
            d["authors"] = sorted(d["authors"])
            out.append(d)
        return sorted(out, key=lambda d: d["source"])

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self.embedding_function(texts)]

    def count(self) -> int:
        return self.collection.count()

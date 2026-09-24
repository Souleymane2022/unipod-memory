"""API FastAPI de UniPods Memory.

Lancement (depuis la racine du dépôt) :  uvicorn backend.app.main:app --reload
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chunker import chunk_document
from .config import ROOT_DIR, get_settings
from .insights import summarize
from .llm import LLMClient
from .parsers import DOC_TYPES, extract_text, parse_document
from .rag import RAGEngine
from .store import VectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("unipods")

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".log", ".csv"}
FRONTEND_DIR = ROOT_DIR / "frontend"


class Services:
    def __init__(self):
        self.settings = get_settings()
        self.store = VectorStore(self.settings)
        self.llm = LLMClient(self.settings)
        self.rag = RAGEngine(self.settings, self.store, self.llm)


@lru_cache
def services() -> Services:
    return Services()


def ingest_bytes(filename: str, data: bytes, doc_type: str | None = None, author: str | None = None,
                 date: str | None = None, save: bool = True) -> dict:
    """Pipeline d'ingestion commun à l'API, au CLI et au bot."""
    s = services()
    source = Path(filename).name
    if Path(source).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Extension non supportée pour {source} (acceptées : {sorted(ALLOWED_EXTENSIONS)})")
    try:
        text = extract_text(source, data)
    except Exception as exc:  # PDF corrompu, etc.
        raise HTTPException(400, f"Lecture impossible de {source} : {exc}") from exc
    if not text.strip():
        raise HTTPException(400, f"{source} ne contient pas de texte exploitable (PDF scanné ?)")
    if save:
        s.settings.upload_dir.mkdir(parents=True, exist_ok=True)
        (s.settings.upload_dir / source).write_bytes(data)
    doc = parse_document(source, text, doc_type=doc_type or None, author=author or None, date=date or None)
    chunks = chunk_document(doc, s.settings.chunk_min_words, s.settings.chunk_max_words)
    result = s.store.add_document(doc, chunks)
    result.update({"units": len(doc.units), "date": doc.date, "author": doc.author,
                   "chunk_words": [c.word_count for c in chunks]})
    log.info("Ingestion %s : %s chunks (%s)", source, len(chunks), doc.doc_type)
    return result


# --------------------------------------------------------------------------- API
app = FastAPI(
    title="UniPods Memory",
    description="Chatbot de mémoire collective : chats, transcriptions de réunions et documents (RAG + ChromaDB).",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["Quelle est la date limite de dépôt des projets ?"])
    top_k: Optional[int] = Field(None, ge=1, le=10)


class TextIngestRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1, examples=["notes_reunion.txt"])
    doc_type: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None


class SummarizeRequest(BaseModel):
    source: Optional[str] = Field(None, description="Nom d'un document déjà indexé")
    text: Optional[str] = Field(None, description="Ou texte brut à résumer")


@app.get("/api/health")
def health():
    s = services()
    return {"status": "ok", "chunks": s.store.count(), "llm": s.llm.describe(),
            "embeddings": s.settings.embedding_backend}


@app.post("/api/ingest")
async def ingest(
    files: list[UploadFile] = File(..., description="Fichiers .txt, .md ou .pdf"),
    doc_type: Optional[str] = Form(None, description="auto (vide), chat, transcript ou document"),
    author: Optional[str] = Form(None),
    date: Optional[str] = Form(None, description="AAAA-MM-JJ"),
):
    if doc_type and doc_type not in DOC_TYPES and doc_type != "auto":
        raise HTTPException(400, f"doc_type doit être l'un de {DOC_TYPES}")
    results = []
    for f in files:
        data = await f.read()
        results.append(ingest_bytes(f.filename or "document.txt", data,
                                    None if doc_type == "auto" else doc_type, author, date))
    return {"ingested": results, "total_chunks": services().store.count()}


@app.post("/api/ingest/text")
def ingest_text(req: TextIngestRequest):
    source = req.source if Path(req.source).suffix else f"{req.source}.txt"
    return ingest_bytes(source, req.text.encode("utf-8"), req.doc_type, req.author, req.date)


@app.post("/api/ask")
def ask(req: AskRequest):
    return services().rag.answer(req.question, req.top_k)


@app.get("/api/documents")
def documents():
    return {"documents": services().store.list_documents()}


@app.delete("/api/documents/{source}")
def delete_document(source: str):
    n = services().store.delete_source(source)
    if not n:
        raise HTTPException(404, f"Document introuvable : {source}")
    return {"deleted_chunks": n, "source": source}


@app.post("/api/summarize")
def summarize_endpoint(req: SummarizeRequest):
    s = services()
    if req.text:
        text, source = req.text, None
    elif req.source:
        chunks = s.store.get_source_chunks(req.source)
        if not chunks:
            raise HTTPException(404, f"Document introuvable : {req.source}")
        text, source = "\n".join(c["text"] for c in chunks), req.source
    else:
        raise HTTPException(400, "Fournir 'source' ou 'text'")
    result = summarize(text, s.llm)
    result["source"] = source
    return result


# --------------------------------------------------------------------------- frontend
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")

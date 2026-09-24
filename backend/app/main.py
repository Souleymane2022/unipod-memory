"""API FastAPI de UniPods Memory.

Lancement (depuis la racine du dépôt) :  uvicorn backend.app.main:app --reload
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chunker import chunk_document
from .config import ROOT_DIR, get_settings
from .channels import router as channels_router
from .i18n import DEFAULT_LANG, msg, normalize_lang
from .insights import summarize
from .llm import LLMClient
from .parsers import DOC_TYPES, extract_text, parse_document
from .rag import RAGEngine
from .store import create_store
from .translate import Translator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("unipods")

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".odt", ".html", ".htm", ".log", ".csv"}
FRONTEND_DIR = ROOT_DIR / "frontend"
SAMPLES_DIR = ROOT_DIR / "data" / "samples"


class Services:
    def __init__(self):
        self.settings = get_settings()
        self.store = create_store(self.settings)
        self.llm = LLMClient(self.settings)
        self.translator = Translator(self.settings, self.llm)
        self.rag = RAGEngine(self.settings, self.store, self.llm, self.translator)
        if self.settings.auto_seed and self.store.count() == 0:
            self.seed()

    def seed(self) -> None:
        """Indexe le jeu de démo (data/samples) : utile sur un hébergement sans disque persistant."""
        for path in sorted(SAMPLES_DIR.glob("*")):
            if path.suffix.lower() in ALLOWED_EXTENSIONS:
                self.ingest(path.name, path.read_bytes(), save=False)
        log.info("Jeu de démo indexé : %s chunks", self.store.count())

    def ingest(self, filename: str, data: bytes, doc_type: str | None = None, author: str | None = None,
               date: str | None = None, save: bool = True, lang: str | None = None) -> dict:
        """Pipeline d'ingestion commun à l'API, au CLI et au bot."""
        lang = normalize_lang(lang) or DEFAULT_LANG
        source = Path(filename).name
        if Path(source).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, msg("unsupported_ext", lang).format(
                source=source, formats=", ".join(sorted(ALLOWED_EXTENSIONS))))
        try:
            text = extract_text(source, data)
        except Exception as exc:  # PDF corrompu, docx invalide, etc.
            raise HTTPException(400, msg("unreadable", lang).format(source=source, error=exc)) from exc
        if not text.strip():
            raise HTTPException(400, msg("no_text", lang).format(source=source))
        if save:
            self.settings.upload_dir.mkdir(parents=True, exist_ok=True)
            (self.settings.upload_dir / source).write_bytes(data)
        doc = parse_document(source, text, doc_type=doc_type or None, author=author or None, date=date or None)
        chunks = chunk_document(doc, self.settings.chunk_min_words, self.settings.chunk_max_words)
        result = self.store.add_document(doc, chunks)
        result.update({"units": len(doc.units), "date": doc.date, "author": doc.author,
                       "chunk_words": [c.word_count for c in chunks]})
        log.info("Ingestion %s : %s chunks (%s)", source, len(chunks), doc.doc_type)
        return result


_services: Services | None = None
_services_lock = threading.Lock()


def services() -> Services:
    """Instance unique, créée une seule fois même si plusieurs requêtes arrivent en même temps
    (au chargement, la page appelle /api/health et /api/documents en parallèle)."""
    global _services
    if _services is None:
        with _services_lock:
            if _services is None:
                _services = Services()
    return _services


def _reset_services() -> None:
    global _services
    with _services_lock:
        _services = None


services.cache_clear = _reset_services  # compatibilité (tests, CLI)


def ingest_bytes(filename: str, data: bytes, doc_type: str | None = None, author: str | None = None,
                 date: str | None = None, save: bool = True, lang: str | None = None) -> dict:
    return services().ingest(filename, data, doc_type, author, date, save, lang)


# --------------------------------------------------------------------------- API
app = FastAPI(
    title="UniPods Memory",
    description="Chatbot de mémoire collective : chats, transcriptions de réunions et documents (RAG + ChromaDB).",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(channels_router)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, examples=["Quelle est la date limite de dépôt des projets ?"])
    top_k: Optional[int] = Field(None, ge=1, le=10)
    lang: Optional[str] = Field(None, description="Langue de la réponse : fr ou en (défaut : langue de la question)")


class TextIngestRequest(BaseModel):
    text: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1, examples=["notes_reunion.txt"])
    doc_type: Optional[str] = None
    author: Optional[str] = None
    date: Optional[str] = None


class SummarizeRequest(BaseModel):
    source: Optional[str] = Field(None, description="Nom d'un document déjà indexé")
    text: Optional[str] = Field(None, description="Ou texte brut à résumer")
    lang: Optional[str] = Field(None, description="Langue du résumé rédigé par le LLM : fr ou en")


@app.get("/api/ping")
def ping():
    """Répond sans initialiser la base ni le modèle : permet de vérifier que la fonction tourne."""
    return {"status": "ok"}


@app.get("/api/health")
def health():
    try:
        s = services()
    except Exception as exc:  # erreur d'initialisation (dépendance, disque, téléchargement du modèle…)
        log.exception("Échec d'initialisation")
        return JSONResponse(status_code=503, content={
            "status": "error", "detail": f"{type(exc).__name__}: {exc}"[:1000]})
    return {"status": "ok", "chunks": s.store.count(), "llm": s.llm.describe(),
            "embeddings": s.settings.embedding_backend, "store": s.store.kind,
            "ephemeral_storage": s.store.ephemeral,
            "translation": s.translator.provider,
            "channels": {"whatsapp": bool(s.settings.whatsapp_token and s.settings.whatsapp_phone_number_id),
                         "telegram": bool(s.settings.telegram_bot_token)},
            # Commit déployé (fourni par Vercel) : permet de vérifier quelle version tourne
            "version": (os.getenv("VERCEL_GIT_COMMIT_SHA") or "")[:7] or "local"}


@app.post("/api/ingest")
async def ingest(
    files: list[UploadFile] = File(..., description="Fichiers .txt, .md, .pdf, .docx, .odt ou .html"),
    doc_type: Optional[str] = Form(None, description="auto (vide), chat, transcript ou document"),
    author: Optional[str] = Form(None),
    date: Optional[str] = Form(None, description="AAAA-MM-JJ"),
    lang: Optional[str] = Form(None, description="Langue des messages d'erreur : fr ou en"),
):
    if doc_type and doc_type not in DOC_TYPES and doc_type != "auto":
        raise HTTPException(400, f"doc_type doit être l'un de {DOC_TYPES}")
    results = []
    for f in files:
        data = await f.read()
        results.append(ingest_bytes(f.filename or "document.txt", data,
                                    None if doc_type == "auto" else doc_type, author, date, lang=lang))
    return {"ingested": results, "total_chunks": services().store.count()}


@app.post("/api/ingest/text")
def ingest_text(req: TextIngestRequest):
    source = req.source if Path(req.source).suffix else f"{req.source}.txt"
    return ingest_bytes(source, req.text.encode("utf-8"), req.doc_type, req.author, req.date)


@app.post("/api/ask")
def ask(req: AskRequest):
    return services().rag.answer(req.question, req.top_k, req.lang)


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
    result = summarize(text, s.llm, req.lang, s.translator)
    result["source"] = source
    return result


# --------------------------------------------------------------------------- frontend
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")

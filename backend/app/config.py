"""Configuration chargée depuis les variables d'environnement (fichier .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

# Sur Vercel (serverless), seul /tmp est inscriptible et il est effacé entre deux démarrages à froid.
ON_VERCEL = bool(os.getenv("VERCEL"))
_TMP = "/tmp/unipods"
if ON_VERCEL:
    # Certaines bibliothèques (onnxruntime…) écrivent dans ~/.cache, en lecture seule sur Vercel.
    os.makedirs(_TMP, exist_ok=True)
    os.environ["HOME"] = _TMP


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT_DIR / p


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass
class Settings:
    # Stockage
    chroma_dir: Path = field(default_factory=lambda: _path(
        os.getenv("CHROMA_DIR", f"{_TMP}/chroma" if ON_VERCEL else "data/chroma")))
    upload_dir: Path = field(default_factory=lambda: _path(
        os.getenv("UPLOAD_DIR", f"{_TMP}/uploads" if ON_VERCEL else "data/uploads")))
    # Dossier du modèle d'embedding ONNX (vide = ~/.cache/chroma, le défaut de ChromaDB)
    model_cache_dir: str = field(default_factory=lambda: os.getenv(
        "MODEL_CACHE_DIR", f"{_TMP}/models" if ON_VERCEL else ""))
    # Indexe automatiquement data/samples au démarrage si la base est vide (activé par défaut sur Vercel)
    auto_seed: bool = field(default_factory=lambda: os.getenv(
        "AUTO_SEED", "1" if ON_VERCEL else "0").lower() in ("1", "true", "yes"))
    ephemeral_storage: bool = ON_VERCEL
    collection_name: str = field(default_factory=lambda: os.getenv("COLLECTION_NAME", "unipods_memory"))

    # Embeddings : "default" (ONNX all-MiniLM-L6-v2 local, gratuit),
    # "sentence-transformers" (modèle HF local) ou "openai".
    embedding_backend: str = field(default_factory=lambda: os.getenv("EMBEDDING_BACKEND", "default"))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", ""))

    # Découpage
    chunk_min_words: int = field(default_factory=lambda: _int("CHUNK_MIN_WORDS", 300))
    chunk_max_words: int = field(default_factory=lambda: _int("CHUNK_MAX_WORDS", 500))

    # Recherche
    top_k: int = field(default_factory=lambda: _int("TOP_K", 4))
    min_relevance: float = field(default_factory=lambda: _float("MIN_RELEVANCE", 0.30))

    # LLM : "auto", "none", "anthropic", "openai" (ou tout serveur compatible : Groq, Mistral…), "ollama"
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "auto").lower())
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", ""))
    llm_timeout: float = field(default_factory=lambda: _float("LLM_TIMEOUT", 60))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))

    # Bot Telegram (optionnel)
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    api_url: str = field(default_factory=lambda: os.getenv("API_URL", "http://localhost:8000"))


def get_settings() -> Settings:
    return Settings()

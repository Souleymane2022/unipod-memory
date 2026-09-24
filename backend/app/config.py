"""Configuration chargée depuis les variables d'environnement (fichier .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


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
    chroma_dir: Path = field(default_factory=lambda: _path(os.getenv("CHROMA_DIR", "data/chroma")))
    upload_dir: Path = field(default_factory=lambda: _path(os.getenv("UPLOAD_DIR", "data/uploads")))
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

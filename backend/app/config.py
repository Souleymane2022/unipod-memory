"""Configuration chargée depuis les variables d'environnement (fichier .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    """Variable d'environnement ; une valeur vide (ex. `TOP_K=` copié depuis .env.example) = non définie."""
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


# Sur Vercel (serverless), seul /tmp est inscriptible et il est effacé entre deux démarrages à froid.
ON_VERCEL = bool(_env("VERCEL"))
_TMP = "/tmp/unipods"
if ON_VERCEL:
    # Certaines bibliothèques (onnxruntime…) écrivent dans ~/.cache, en lecture seule sur Vercel.
    os.makedirs(_TMP, exist_ok=True)
    os.environ["HOME"] = _TMP


def _find_database_url() -> str:
    """Adresse PostgreSQL : DATABASE_URL, POSTGRES_URL, ou toute variable <PRÉFIXE>_URL / _DATABASE_URL
    contenant une adresse postgres:// (l'intégration Neon de Vercel permet un préfixe, ex. STORAGE_URL)."""
    for name in ("DATABASE_URL", "POSTGRES_URL"):
        if _env(name):
            return _env(name)
    candidates = sorted(
        (name for name, value in os.environ.items()
         if name.endswith("_URL") and value.strip().startswith(("postgres://", "postgresql://"))
         and "UNPOOLED" not in name and "NON_POOLING" not in name and "PRISMA" not in name),
        key=lambda n: (not n.endswith("DATABASE_URL"), not n.endswith("POSTGRES_URL"), n),
    )
    return os.environ[candidates[0]].strip() if candidates else ""


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT_DIR / p


def _storage_path(name: str, local_default: str) -> Path:
    """Dossier inscriptible : sur Vercel, tout chemin hors de /tmp est ramené sous /tmp/unipods."""
    value = _env(name, local_default)
    if ON_VERCEL and not value.startswith("/tmp"):
        return Path(_TMP) / Path(value).name
    return _path(value)


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)).replace(",", "."))
    except ValueError:
        return default


@dataclass
class Settings:
    # Stockage
    chroma_dir: Path = field(default_factory=lambda: _storage_path("CHROMA_DIR", "data/chroma"))
    upload_dir: Path = field(default_factory=lambda: _storage_path("UPLOAD_DIR", "data/uploads"))
    # Dossier du modèle d'embedding ONNX (vide = ~/.cache/chroma, le défaut de ChromaDB)
    model_cache_dir: str = field(default_factory=lambda: str(_storage_path("MODEL_CACHE_DIR", "models"))
                                 if ON_VERCEL else _env("MODEL_CACHE_DIR"))
    # Indexe automatiquement data/samples au démarrage si la base est vide (activé par défaut sur Vercel)
    auto_seed: bool = field(default_factory=lambda: _env(
        "AUTO_SEED", "1" if ON_VERCEL else "0").lower() in ("1", "true", "yes"))
    ephemeral_storage: bool = ON_VERCEL
    # Base vectorielle : auto (PostgreSQL si DATABASE_URL, sinon ChromaDB) | chroma | postgres
    vector_store: str = field(default_factory=lambda: _env("VECTOR_STORE", "auto").lower())
    # PostgreSQL + pgvector (Neon via l'intégration Vercel crée DATABASE_URL et POSTGRES_URL)
    database_url: str = field(default_factory=_find_database_url)
    collection_name: str = field(default_factory=lambda: _env("COLLECTION_NAME", "unipods_memory"))

    # Embeddings : "default" (ONNX all-MiniLM-L6-v2 local, gratuit),
    # "sentence-transformers" (modèle HF local) ou "openai".
    embedding_backend: str = field(default_factory=lambda: _env("EMBEDDING_BACKEND", "default"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", ""))

    # Découpage
    chunk_min_words: int = field(default_factory=lambda: _int("CHUNK_MIN_WORDS", 300))
    chunk_max_words: int = field(default_factory=lambda: _int("CHUNK_MAX_WORDS", 500))

    # Recherche
    top_k: int = field(default_factory=lambda: _int("TOP_K", 4))
    min_relevance: float = field(default_factory=lambda: _float("MIN_RELEVANCE", 0.30))

    # LLM : "auto", "none", "anthropic", "openai" (ou tout serveur compatible : Groq, Mistral…), "ollama"
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "auto").lower())
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", ""))
    llm_timeout: float = field(default_factory=lambda: _float("LLM_TIMEOUT", 60))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", ""))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"))

    # Traduction des citations (voir translate.py) : auto | llm | mymemory | none
    translation_provider: str = field(default_factory=lambda: _env("TRANSLATION_PROVIDER", "auto").lower())
    mymemory_email: str = field(default_factory=lambda: _env("MYMEMORY_EMAIL"))
    mymemory_url: str = field(default_factory=lambda: _env("MYMEMORY_URL", "https://api.mymemory.translated.net/get"))

    # Bot Telegram (optionnel)
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", ""))
    # Mode webhook (Vercel) : secret partagé avec Telegram, et clé de /api/telegram/setup
    telegram_webhook_secret: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_SECRET"))

    # WhatsApp Cloud API (Meta) — voir channels.py
    whatsapp_token: str = field(default_factory=lambda: _env("WHATSAPP_TOKEN"))
    whatsapp_phone_number_id: str = field(default_factory=lambda: _env("WHATSAPP_PHONE_NUMBER_ID"))
    whatsapp_verify_token: str = field(default_factory=lambda: _env("WHATSAPP_VERIFY_TOKEN"))
    whatsapp_app_secret: str = field(default_factory=lambda: _env("WHATSAPP_APP_SECRET"))
    whatsapp_api_version: str = field(default_factory=lambda: _env("WHATSAPP_API_VERSION", "v23.0"))
    # Facultatif : numéros autorisés (format international sans +, séparés par des virgules)
    whatsapp_allowed_numbers: set = field(default_factory=lambda: {
        "".join(c for c in n if c.isdigit()) for n in _env("WHATSAPP_ALLOWED_NUMBERS").split(",") if n.strip()})
    # Contact affiché sur /privacy et /data-deletion (exigé par Meta pour publier l'app WhatsApp)
    contact_email: str = field(default_factory=lambda: _env("CONTACT_EMAIL"))
    api_url: str = field(default_factory=lambda: _env("API_URL", "http://localhost:8000"))


def get_settings() -> Settings:
    return Settings()

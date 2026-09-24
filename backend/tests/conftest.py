import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _postgres_url(tmp: Path) -> str:
    """PostgreSQL + pgvector embarqué (paquet pgserver) ; test ignoré s'il n'est pas installé."""
    pgserver = pytest.importorskip("pgserver", reason="pgserver non installé : tests PostgreSQL ignorés")
    return pgserver.get_server(tmp / "pg", cleanup_mode="stop").get_uri()


@pytest.fixture(scope="session", params=["chroma", "postgres"])
def client(request, tmp_path_factory):
    """API sans LLM, alimentée avec data/samples ; tous les tests d'API tournent sur ChromaDB puis PostgreSQL."""
    tmp = tmp_path_factory.mktemp(f"unipods_{request.param}")
    os.environ.update({"CHROMA_DIR": str(tmp / "chroma"), "UPLOAD_DIR": str(tmp / "uploads"),
                       "LLM_PROVIDER": "none", "COLLECTION_NAME": "test_memory",
                       "TRANSLATION_PROVIDER": "none",  # pas d'appel réseau ; traduction testée avec des doublures
                       "VECTOR_STORE": request.param,
                       "DATABASE_URL": _postgres_url(tmp) if request.param == "postgres" else ""})
    from fastapi.testclient import TestClient

    from backend.app import main

    main.services.cache_clear()
    c = TestClient(main.app)
    assert main.services().store.kind == request.param
    files = [("files", (p.name, p.read_bytes(), "text/plain")) for p in sorted((ROOT / "data" / "samples").glob("*"))]
    r = c.post("/api/ingest", files=files)
    assert r.status_code == 200, r.text
    yield c
    main.services.cache_clear()

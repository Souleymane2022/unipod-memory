import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    """API avec une base Chroma temporaire, sans LLM, alimentée avec data/samples."""
    tmp = tmp_path_factory.mktemp("unipods")
    os.environ.update({"CHROMA_DIR": str(tmp / "chroma"), "UPLOAD_DIR": str(tmp / "uploads"),
                       "LLM_PROVIDER": "none", "COLLECTION_NAME": "test_memory"})
    from fastapi.testclient import TestClient

    from backend.app import main

    main.services.cache_clear()
    c = TestClient(main.app)
    files = [("files", (p.name, p.read_bytes(), "text/plain")) for p in sorted((ROOT / "data" / "samples").glob("*"))]
    r = c.post("/api/ingest", files=files)
    assert r.status_code == 200, r.text
    yield c
    main.services.cache_clear()

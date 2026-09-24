"""Stockage PostgreSQL : la mémoire survit aux redémarrages (cas de Vercel + Neon)."""
import pytest

from backend.app import main


def test_documents_survive_restart(client, monkeypatch):
    s = main.services()
    if s.store.kind != "postgres":
        pytest.skip("persistance entre instances : propre au stockage PostgreSQL")
    assert s.store.ephemeral is False and client.get("/api/health").json()["store"] == "postgres"
    r = client.post("/api/ingest/text", json={"source": "persistance.txt",
                                              "text": "Le code du casier des badges est 7319."})
    assert r.status_code == 200
    before = s.store.count()

    # « Redémarrage » : nouvelle instance (comme un démarrage à froid Vercel), avec réindexation auto activée
    monkeypatch.setenv("AUTO_SEED", "1")
    main.services.cache_clear()
    s2 = main.services()
    assert s2 is not s and s2.store.count() == before  # rien de perdu, pas de doublon de la démo
    a = client.post("/api/ask", json={"question": "Quel est le code du casier des badges ?"}).json()
    assert a["found"] and "7319" in a["answer"] and a["sources"][0]["source"] == "persistance.txt"
    client.delete("/api/documents/persistance.txt")

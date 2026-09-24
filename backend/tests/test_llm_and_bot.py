"""Chemins LLM et Telegram testés avec des doublures (aucune clé ni réseau nécessaire)."""
from backend.app import main
from backend.app.llm import LLMClient, LLMError
from backend.app.rag import NOT_FOUND_MARKER
from backend.app.telegram_bot import handle


def _with_fake_llm(monkeypatch, reply):
    rag = main.services().rag
    monkeypatch.setattr(rag.llm, "provider", "anthropic")
    monkeypatch.setattr(rag.llm, "model", "fake")

    def complete(system, user, max_tokens=800):
        assert "Extraits" in user and "[1]" in user  # le contexte numéroté est bien transmis
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(rag.llm, "complete", complete)
    return rag


def test_llm_answer_keeps_only_cited_sources(client, monkeypatch):
    rag = _with_fake_llm(monkeypatch, "La date limite est le 10 octobre 2026 à 23h59 [1].")
    r = rag.answer("Quelle est la date limite de dépôt des projets pour le hackathon ?")
    assert r["found"] and r["mode"].startswith("llm:")
    assert [s["ref"] for s in r["sources"]] == [1]


def test_llm_not_found_marker(client, monkeypatch):
    rag = _with_fake_llm(monkeypatch, NOT_FOUND_MARKER)
    r = rag.answer("Quels sont les prix du hackathon ?")
    assert not r["found"] and r["sources"] == []


def test_llm_failure_falls_back_to_extractive(client, monkeypatch):
    rag = _with_fake_llm(monkeypatch, LLMError("quota dépassé"))
    r = rag.answer("Quels sont les prix du hackathon ?")
    assert r["found"] and r["mode"] == "extractive" and "quota" in r["warning"]
    assert "1 500 000 FCFA" in r["answer"]


def test_llm_provider_auto_detection(monkeypatch):
    from backend.app.config import Settings

    s = Settings()
    s.llm_provider, s.anthropic_api_key, s.openai_api_key = "auto", "", ""
    assert not LLMClient(s).enabled
    s.openai_api_key = "sk-test"
    assert LLMClient(s).provider == "openai"


def test_telegram_handler(client):
    api = client  # TestClient est un httpx.Client
    reply = handle("Comment réserver une machine du fablab ?", api)
    assert "48 heures" in reply and "guide_fablab_unipod.md" in reply
    assert "Décisions" in handle("/resume reunion_mensuelle_2026-09-15.txt", api)
    assert "UniPods Memory" in handle("/start", api)

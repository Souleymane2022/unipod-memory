"""Traduction des citations (MyMemory simulé : aucun appel réseau)."""
import httpx
import pytest

from backend.app import main
from backend.app.config import Settings
from backend.app.i18n import msg
from backend.app.llm import LLMClient
from backend.app.translate import Translator

FAKE = {
    "La date limite de dépôt des projets pour le hackathon est le 10 octobre 2026 à 23h59.":
        "The deadline for submitting hackathon projects is October 10, 2026 at 11:59 pm.",
}


def _fake_get(url, params=None, timeout=None):
    q = params["q"]
    text = next((v for k, v in FAKE.items() if k in q), f"EN({q[:20]})")
    return httpx.Response(200, json={"responseData": {"translatedText": text}, "responseStatus": 200},
                          request=httpx.Request("GET", url))


def _translator(monkeypatch, provider="mymemory", fake=_fake_get):
    s = Settings()
    s.translation_provider, s.llm_provider = provider, "none"
    monkeypatch.setattr(httpx, "get", fake)
    return Translator(s, LLMClient(s))


@pytest.fixture
def mymemory(client, monkeypatch):
    rag = main.services().rag
    monkeypatch.setattr(rag, "translator", _translator(monkeypatch))
    return rag


def test_english_answer_is_translated(client, mymemory):
    r = client.post("/api/ask", json={"question": "What is the deadline to submit hackathon projects?"}).json()
    assert r["found"]
    assert r["answer"].startswith(msg("intro", "en") + " " + msg("machine_translation", "en"))
    assert "Awa Diallo:" in r["answer"]  # auteur/date conservés
    assert "10 octobre" in r["sources"][0]["excerpt"]  # l'extrait original reste dans la source


def test_french_question_in_french_is_not_translated(client, mymemory):
    r = client.post("/api/ask", json={"question": "Comment réserver une machine du fablab ?", "lang": "fr"}).json()
    assert r["found"] and msg("machine_translation", "fr") not in r["answer"]


def test_translation_failure_keeps_original(client, monkeypatch):
    def quota(url, params=None, timeout=None):
        return httpx.Response(200, json={"responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL"},
                                         "responseStatus": 429}, request=httpx.Request("GET", url))
    rag = main.services().rag
    monkeypatch.setattr(rag, "translator", _translator(monkeypatch, fake=quota))
    r = client.post("/api/ask", json={"question": "What are the hackathon prizes?"}).json()
    assert r["found"] and msg("original_language", "en") in r["answer"] and "1 500 000 FCFA" in r["answer"]


def test_summary_translated(client, monkeypatch):
    monkeypatch.setattr(main.services(), "translator", _translator(monkeypatch))
    r = client.post("/api/summarize", json={"source": "reunion_mensuelle_2026-09-15.txt", "lang": "en"}).json()
    assert r["translated"] and r["summary"].startswith("EN(")
    assert all(d.startswith("EN(") for d in r["decisions"])


def test_long_text_is_split_for_mymemory(monkeypatch):
    calls = []

    def fake(url, params=None, timeout=None):
        calls.append(params["q"])
        return _fake_get(url, params, timeout)

    t = _translator(monkeypatch, fake=fake)
    assert t.translate("mot " * 300, "fr", "en")
    assert len(calls) >= 3 and all(len(q) <= 450 for q in calls)


def test_provider_resolution(monkeypatch):
    s = Settings()
    s.llm_provider, s.translation_provider = "none", "auto"
    assert Translator(s, LLMClient(s)).provider == "mymemory"
    s.translation_provider = "none"
    t = Translator(s, LLMClient(s))
    assert not t.enabled and t.translate("bonjour", "fr", "en") is None


def test_failure_pauses_translation(monkeypatch):
    calls = []

    def down(url, params=None, timeout=None):
        calls.append(1)
        raise httpx.ConnectError("réseau indisponible")

    t = _translator(monkeypatch, fake=down)
    assert t.translate("bonjour le monde", "fr", "en") is None
    assert t.translate("autre phrase", "fr", "en") is None
    assert len(calls) == 1  # pas de nouvel essai pendant la pause

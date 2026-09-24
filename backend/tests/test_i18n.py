"""Bilinguisme : questions en anglais sur des sources en français, réponses dans la langue choisie."""
import pytest

from backend.app.i18n import detect_lang, msg


def _ask(client, question, lang=None):
    body = {"question": question}
    if lang:
        body["lang"] = lang
    r = client.post("/api/ask", json=body)
    assert r.status_code == 200
    return r.json()


def test_detect_lang():
    assert detect_lang("What is the deadline to submit projects?") == "en"
    assert detect_lang("Quelle est la date limite de dépôt ?") == "fr"


@pytest.mark.parametrize("question,source,expected", [
    ("What is the deadline to submit hackathon projects?", "chat_general_septembre.txt", "10 octobre 2026"),
    ("What was decided about the 3D printer during the meeting?", "reunion_mensuelle_2026-09-15.txt", "deux heures"),
    ("How do I book a fablab machine?", "guide_fablab_unipod.md", "48 heures"),
    ("What are the hackathon prizes?", "chat_general_septembre.txt", "1 500 000 FCFA"),
])
def test_english_question_finds_french_source(client, question, source, expected):
    r = _ask(client, question)
    assert r["found"] and r["lang"] == "en"
    assert r["sources"][0]["source"] == source
    assert expected in r["answer"]
    assert r["answer"].startswith(msg("intro", "en"))
    assert msg("original_language", "en") in r["answer"]  # citations françaises signalées


@pytest.mark.parametrize("question", [
    "What is the director's salary?", "Who won the 2022 World Cup?", "What is the weather tomorrow?",
])
def test_english_unknown_is_not_hallucinated(client, question):
    r = _ask(client, question)
    assert not r["found"] and r["sources"] == []
    assert r["answer"] == msg("not_found", "en")


def test_lang_parameter_overrides_question_language(client):
    r = _ask(client, "Comment réserver une machine du fablab ?", lang="en")
    assert r["found"] and r["lang"] == "en" and r["answer"].startswith(msg("intro", "en"))
    r = _ask(client, "Who won the 2022 World Cup?", lang="fr")
    assert r["answer"] == msg("not_found", "fr")

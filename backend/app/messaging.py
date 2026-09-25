"""Logique commune aux messageries (WhatsApp, Telegram) : commandes, réponses, mise en forme.

Les messages arrivent par webhook sur l'API (voir channels.py) et sont traités directement par le moteur,
sans passer par HTTP. Langue : détectée automatiquement à partir du message (français ou anglais).
"""
from __future__ import annotations

import re
from typing import Any

from .i18n import detect_lang

MAX_CHARS = 4000  # WhatsApp et Telegram limitent un message à 4 096 caractères

HELP = {
    "fr": (
        "Je suis *UniPods Memory* 🧠, la mémoire du groupe.\n"
        "Posez-moi une question sur les messages, les réunions ou les documents : je réponds en citant mes sources.\n\n"
        "Exemples :\n"
        "• Quand a lieu la prochaine réunion ?\n"
        "• Résume le document METI\n"
        "🎤 Vous pouvez aussi m'envoyer un *message vocal* : je réponds par écrit et à voix haute.\n"
        "🎨 Et je crée des images : *génère une image de …*\n\n"
        "Commandes :\n"
        "• *documents* : liste des sources indexées\n"
        "• *résumé <nom du fichier>* : résumé, décisions et tâches\n"
        "• *aide* : ce message\n\n"
        "You can also write in English."
    ),
    "en": (
        "I'm *UniPods Memory* 🧠, the group's memory.\n"
        "Ask me about the group's messages, meetings or documents: I answer with sources.\n\n"
        "Examples:\n"
        "• When is the next meeting?\n"
        "• Summarize the METI document\n"
        "🎤 You can also send me a *voice message*: I reply in writing and out loud.\n"
        "🎨 And I create images: *generate an image of …*\n\n"
        "Commands:\n"
        "• *documents*: list indexed sources\n"
        "• *summary <file name>*: summary, decisions and tasks\n"
        "• *help*: this message\n\n"
        "Vous pouvez aussi écrire en français."
    ),
}
TEXT = {
    "fr": {"no_docs": "Aucun document indexé.", "docs": "📚 Documents indexés :", "sources": "📚 Sources :",
           "summary": "📝 Résumé", "decisions": "✅ Décisions", "tasks": "📌 Tâches", "none": "aucune",
           "unknown_doc": "Document introuvable : « {name} ». Envoyez *documents* pour voir la liste.",
           "usage": "Usage : *résumé <nom du fichier>* (envoyez *documents* pour voir la liste).",
           "text_only": "Je ne lis que les messages texte pour l'instant. Posez votre question par écrit 🙂"},
    "en": {"no_docs": "No document indexed yet.", "docs": "📚 Indexed documents:", "sources": "📚 Sources:",
           "summary": "📝 Summary", "decisions": "✅ Decisions", "tasks": "📌 Tasks", "none": "none",
           "unknown_doc": "Document not found: \"{name}\". Send *documents* to see the list.",
           "usage": "Usage: *summary <file name>* (send *documents* to see the list).",
           "text_only": "I can only read text messages for now. Please type your question 🙂"},
}

HELP_RE = re.compile(r"^/?(start|aide|help|menu|\?)\s*$", re.IGNORECASE)
DOCS_RE = re.compile(r"^/?(documents?|docs|sources)\s*$", re.IGNORECASE)
SUMMARY_RE = re.compile(r"^/?(r[ée]sum[ée]?|summary|summarize)(?:\s+(.*))?$", re.IGNORECASE)


def to_channel_format(text: str, channel: str) -> str:
    """Markdown des LLM (**gras**) -> *gras* WhatsApp ; texte brut pour Telegram (pas de parse_mode)."""
    if channel == "whatsapp":
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    else:
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("*", "")
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    return text[:MAX_CHARS]


def format_answer(r: dict[str, Any], lang: str) -> str:
    lines = [r["answer"]]
    if r.get("sources"):
        lines += ["", TEXT[lang]["sources"]]
        for s in r["sources"]:
            meta = " · ".join(x for x in [s.get("cited_date"), s.get("timestamp"), s.get("cited_author")] if x)
            lines.append(f"[{s['ref']}] {s['source']}" + (f" ({meta})" if meta else ""))
    return "\n".join(lines)


def format_summary(r: dict[str, Any], lang: str) -> str:
    t = TEXT[lang]
    out = [f"{t['summary']} — {r.get('source') or ''}".rstrip(" —"), r["summary"], "", t["decisions"]]
    out += [f"• {d}" for d in r["decisions"]] or [f"• {t['none']}"]
    out += ["", t["tasks"]]
    out += [f"• {x['task']}" + (f" — {x['owner']}" if x.get("owner") else "")
            + (f" ({x['deadline']})" if x.get("deadline") else "") for x in r["tasks"]] or [f"• {t['none']}"]
    return "\n".join(out)


def _find_document(name: str, docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Recherche tolérante : nom exact, puis nom contenant tous les mots tapés (« meti info » suffit)."""
    name_l = name.lower().strip(" «»\"'")
    for d in docs:
        if d["source"].lower() == name_l:
            return d
    words = [w for w in re.findall(r"\w+", name_l) if len(w) > 1]
    matches = [d for d in docs if words and all(w in (d["source"] + " " + d.get("title", "")).lower() for w in words)]
    return matches[0] if len(matches) == 1 else None


def reply(text: str, channel: str, services) -> str:
    """Réponse à un message texte reçu sur une messagerie."""
    text = (text or "").strip()
    lang = detect_lang(text) if text else "fr"
    if not text or HELP_RE.match(text):
        return to_channel_format(HELP[lang if text else "fr"], channel)
    if DOCS_RE.match(text):
        docs = services.store.list_documents()
        if not docs:
            return TEXT[lang]["no_docs"]
        return to_channel_format("\n".join([TEXT[lang]["docs"]] + [f"• {d['source']}" for d in docs]), channel)
    if m := SUMMARY_RE.match(text):
        from .insights import summarize

        if not m.group(2):
            return to_channel_format(TEXT[lang]["usage"], channel)
        doc = _find_document(m.group(2), services.store.list_documents())
        if doc is None:
            # « résume le document METI » : phrase libre, gérée par le moteur (questions sur un document)
            return to_channel_format(format_answer(services.rag.answer(text), lang), channel)
        chunks = services.store.get_source_chunks(doc["source"])
        r = summarize("\n".join(c["text"] for c in chunks), services.llm, lang, services.translator)
        r["source"] = doc["source"]
        return to_channel_format(format_summary(r, lang), channel)
    r = services.rag.answer(text)
    return to_channel_format(format_answer(r, r.get("lang", lang)), channel)


def error_reply() -> str:
    return ("Désolé, une erreur est survenue. Réessayez dans un instant.\n"
            "Sorry, something went wrong. Please try again in a moment.")


VOICE = {
    "heard": {"fr": "🎤 J'ai entendu : « {text} »", "en": "🎤 I heard: \"{text}\""},
    "empty": {"fr": "🎤 Je n'ai rien entendu d'intelligible dans ce message vocal. Pouvez-vous répéter ou écrire votre question ?",
              "en": "🎤 I couldn't make out anything in this voice message. Could you repeat or type your question?"},
    "failed": {"fr": "🎤 Je n'ai pas réussi à écouter ce message vocal. Réessayez dans un instant ou écrivez votre question en texte.",
               "en": "🎤 I couldn't process this voice message. Try again in a moment or type your question."},
    "unavailable": {"fr": "🎤 Les messages vocaux ne sont pas activés (aucun modèle de transcription configuré). "
                          "Écrivez votre question en texte 🙂",
                    "en": "🎤 Voice messages are not enabled (no transcription model configured). "
                          "Please type your question 🙂"},
}


IMAGE = {
    "caption": {"fr": "🎨 Image générée pour : « {prompt} »", "en": "🎨 Image generated for: \"{prompt}\""},
    "ask": {"fr": "🎨 Que voulez-vous que je dessine ? Exemple : *génère une image d'un fablab futuriste au Tchad*",
            "en": "🎨 What should I draw? Example: *generate an image of a futuristic fablab in Chad*"},
    "failed": {"fr": "🎨 Je n'ai pas pu générer l'image pour le moment. Réessayez dans un instant avec une autre description.",
               "en": "🎨 I couldn't generate the image right now. Try again in a moment with another description."},
}


def image_text(key: str, lang: str, **kw) -> str:
    return IMAGE[key][lang if lang in ("fr", "en") else "fr"].format(**kw)


def voice_text(key: str, lang: str | None = None, **kw) -> str:
    """Message lié aux notes vocales ; sans langue connue, bilingue FR puis EN."""
    if lang:
        return VOICE[key][lang].format(**kw)
    return VOICE[key]["fr"].format(**kw) + "\n" + VOICE[key]["en"].format(**kw)


def non_text_reply() -> str:
    return TEXT["fr"]["text_only"] + "\n" + TEXT["en"]["text_only"]

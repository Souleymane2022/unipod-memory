"""Bot Telegram optionnel (long polling, sans dépendance supplémentaire).

Il relaie les questions vers l'API UniPods Memory (qui doit tourner) et renvoie la réponse citée.

    TELEGRAM_BOT_TOKEN=xxx API_URL=http://localhost:8000 python -m backend.app.telegram_bot

Commandes : /start, /aide, /resume <nom_du_fichier>, ou simplement une question.
"""
from __future__ import annotations

import logging
import time

import httpx

from .config import get_settings

log = logging.getLogger("unipods.telegram")
HELP = (
    "Je suis UniPods Memory 🧠 : posez-moi une question sur les messages du groupe, les réunions "
    "ou les documents, je réponds en citant mes sources.\n"
    "I'm UniPods Memory 🧠: ask me about the group's messages, meetings or documents, in French or English — "
    "I answer with sources.\n\n"
    "• /resume <fichier|file> : résumé, décisions et tâches / summary, decisions and tasks\n"
    "• /documents : sources indexées / indexed sources"
)


def format_answer(r: dict) -> str:
    lines = [r["answer"]]
    if r.get("sources"):
        lines.append("\n📚 Sources:")
        for s in r["sources"]:
            meta = " · ".join(x for x in [s.get("cited_date"), s.get("timestamp"), s.get("cited_author")] if x)
            lines.append(f"[{s['ref']}] {s['source']} ({meta})")
    return "\n".join(lines)[:4000]


def format_summary(r: dict) -> str:
    out = [f"📝 Résumé : {r['summary']}", "", "✅ Décisions :"]
    out += [f"• {d}" for d in r["decisions"]] or ["• aucune"]
    out += ["", "📌 Tâches :"]
    out += [f"• {t['task']}" + (f" — {t['owner']}" if t.get("owner") else "") for t in r["tasks"]] or ["• aucune"]
    return "\n".join(out)[:4000]


def handle(text: str, api: httpx.Client) -> str:
    text = text.strip()
    if text.startswith(("/start", "/aide", "/help")):
        return HELP
    if text.startswith("/documents"):
        docs = api.get("/api/documents").json()["documents"]
        return "\n".join(f"• {d['source']} ({d['doc_type']})" for d in docs) or "Aucun document indexé."
    if text.startswith("/resume"):
        source = text.partition(" ")[2].strip()
        if not source:
            return "Usage : /resume <nom_du_fichier> (voir /documents)"
        r = api.post("/api/summarize", json={"source": source})
        return format_summary(r.json()) if r.status_code == 200 else r.json().get("detail", "Erreur")
    return format_answer(api.post("/api/ask", json={"question": text}).json())


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant dans .env")
    tg = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    api = httpx.Client(base_url=settings.api_url, timeout=120)
    offset = 0
    log.info("Bot démarré, API = %s", settings.api_url)
    while True:
        try:
            updates = httpx.get(f"{tg}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=40).json()
            for upd in updates.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if not msg.get("text"):
                    continue
                reply = handle(msg["text"], api)
                httpx.post(f"{tg}/sendMessage", json={"chat_id": msg["chat"]["id"], "text": reply,
                                                        "reply_to_message_id": msg["message_id"]}, timeout=30)
        except httpx.HTTPError as exc:
            log.warning("Erreur réseau : %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()

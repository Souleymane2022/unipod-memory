"""Bot Telegram en mode « long polling », pour un serveur classique ou un PC (pas pour Vercel).

Sur Vercel, utilisez plutôt le mode webhook (channels.py) : il suffit d'ouvrir une fois
https://<site>/api/telegram/setup?key=<TELEGRAM_WEBHOOK_SECRET>.

    TELEGRAM_BOT_TOKEN=xxx python -m backend.app.telegram_bot

Les messages sont traités directement par le moteur (même logique que WhatsApp et le webhook Telegram).
"""
from __future__ import annotations

import logging
import time

import httpx

from . import channels
from .messaging import non_text_reply, reply

log = logging.getLogger("unipods.telegram")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from .main import services

    svc = services()
    token = svc.settings.telegram_bot_token
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN manquant dans .env")
    tg = f"https://api.telegram.org/bot{token}"
    httpx.post(f"{tg}/deleteWebhook", timeout=20)  # le long polling est incompatible avec un webhook actif
    offset = 0
    log.info("Bot Telegram démarré (long polling)")
    while True:
        try:
            updates = httpx.get(f"{tg}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=40).json()
            for upd in updates.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if "chat" not in msg:
                    continue
                text, audio, spoken = msg.get("text"), msg.get("voice") or msg.get("audio"), None
                if audio and audio.get("file_id"):  # note vocale : même traitement que le webhook
                    body, spoken = channels._voice_turn(
                        lambda: channels._tg_download(svc.settings, audio["file_id"]),
                        audio.get("mime_type", "audio/ogg"), "telegram", svc, str(msg["chat"]["id"]))
                else:
                    body = reply(text, "telegram", svc) if text else non_text_reply()
                httpx.post(f"{tg}/sendMessage", json={"chat_id": msg["chat"]["id"], "text": body,
                                                       "reply_to_message_id": msg["message_id"]}, timeout=30)
                if mp3 := channels._voice_audio(spoken, "telegram", svc, str(msg["chat"]["id"])):
                    channels._tg_send_voice(svc.settings, msg["chat"]["id"], mp3, msg["message_id"])
        except httpx.HTTPError as exc:
            log.warning("Erreur réseau : %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()

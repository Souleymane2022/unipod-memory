"""Webhooks des messageries : WhatsApp Cloud API (Meta) et Telegram.

WhatsApp
    GET  /api/whatsapp/webhook   vérification par Meta (hub.challenge) avec WHATSAPP_VERIFY_TOKEN
    POST /api/whatsapp/webhook   messages entrants ; signature X-Hub-Signature-256 vérifiée avec WHATSAPP_APP_SECRET
Telegram
    POST /api/telegram/webhook   messages entrants ; en-tête secret vérifié avec TELEGRAM_WEBHOOK_SECRET
    GET  /api/telegram/setup?key=<TELEGRAM_WEBHOOK_SECRET>   enregistre le webhook auprès de Telegram (une fois)

Le traitement est synchrone (quelques secondes avec un LLM) puis on renvoie 200 : adapté au serverless.
Meta renvoie parfois un même message : les identifiants déjà traités sont ignorés.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .messaging import error_reply, non_text_reply, reply

log = logging.getLogger("unipods.channels")
router = APIRouter()


class _Seen:
    """Petit cache des identifiants de messages déjà traités (renvois de Meta/Telegram)."""

    def __init__(self, size: int = 500):
        self.size = size
        self.ids: OrderedDict[str, None] = OrderedDict()

    def check_and_add(self, key: str) -> bool:
        if key in self.ids:
            return True
        self.ids[key] = None
        if len(self.ids) > self.size:
            self.ids.popitem(last=False)
        return False


_seen = _Seen()

# Journal des derniers événements (affiché dans /api/health) pour diagnostiquer sans accès aux logs.
# Numéros masqués : seuls les 4 derniers chiffres apparaissent.
RECENT_EVENTS: deque = deque(maxlen=10)


def _event(channel: str, status: str, sender: str = "", detail: str = "") -> None:
    ev = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), "channel": channel,
          "status": status, "from": f"…{sender[-4:]}" if sender else "", "detail": detail[:200]}
    RECENT_EVENTS.appendleft(ev)
    try:  # en serverless, chaque requête peut tomber sur une instance différente : on garde aussi l'événement en base
        _services().store.log_event(ev)
    except Exception as exc:
        log.warning("Journal d'événements non enregistré : %s", exc)


def _services():
    from .main import services

    return services()


def _safe_reply(text: str | None, channel: str, svc) -> str:
    """Réponse au message ; texte absent (audio, image…) -> invitation à écrire ; erreur -> excuse."""
    if text is None:
        return non_text_reply()
    try:
        return reply(text, channel, svc)
    except Exception:  # le webhook doit toujours répondre 200
        log.exception("Erreur pendant le traitement d'un message %s", channel)
        return error_reply()


# --------------------------------------------------------------------------- WhatsApp
def _wa_send(settings, to: str, body: str, reply_to: str | None = None) -> None:
    url = (f"https://graph.facebook.com/{settings.whatsapp_api_version}/"
           f"{settings.whatsapp_phone_number_id}/messages")
    payload: dict[str, Any] = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                               "type": "text", "text": {"body": body, "preview_url": False}}
    if reply_to:
        payload["context"] = {"message_id": reply_to}
    try:
        r = httpx.post(url, json=payload, timeout=20,
                       headers={"Authorization": f"Bearer {settings.whatsapp_token}"})
    except httpx.HTTPError as exc:  # ne jamais faire échouer le webhook : Meta renverrait le message en boucle
        log.error("Envoi WhatsApp impossible (réseau) : %s", exc)
        _event("whatsapp", "erreur_envoi", to, f"réseau : {exc}")
        return
    if r.status_code >= 400:
        log.error("Envoi WhatsApp impossible (HTTP %s) : %s", r.status_code, r.text[:300])
        _event("whatsapp", "erreur_envoi", to, f"HTTP {r.status_code} : {r.text[:180]}")
    else:
        _event("whatsapp", "réponse_envoyée", to)


def _wa_signature_ok(settings, raw: bytes, header: str | None) -> bool:
    if not settings.whatsapp_app_secret:
        log.warning("WHATSAPP_APP_SECRET non défini : signature des webhooks non vérifiée")
        return True
    expected = "sha256=" + hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return bool(header) and hmac.compare_digest(expected, header)


@router.get("/api/whatsapp/webhook", include_in_schema=False)
def whatsapp_verify(request: Request):
    # Lecture directe de la configuration, sans initialiser le moteur (base, modèle) : Meta attend une
    # réponse immédiate, et un démarrage à froid Vercel prendrait plusieurs secondes.
    from .config import get_settings

    s = get_settings()
    p = request.query_params
    if (p.get("hub.mode") == "subscribe" and s.whatsapp_verify_token
            and hmac.compare_digest(p.get("hub.verify_token", ""), s.whatsapp_verify_token)):
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Jeton de vérification invalide")


@router.post("/api/whatsapp/webhook", include_in_schema=False)
async def whatsapp_webhook(request: Request):
    svc = _services()
    s = svc.settings
    raw = await request.body()
    if not _wa_signature_ok(s, raw, request.headers.get("x-hub-signature-256")):
        _event("whatsapp", "signature_invalide", detail="vérifier WHATSAPP_APP_SECRET")
        raise HTTPException(401, "Signature invalide")
    if not (s.whatsapp_token and s.whatsapp_phone_number_id):
        log.error("WhatsApp non configuré (WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID)")
        _event("whatsapp", "non_configuré", detail="WHATSAPP_TOKEN ou WHATSAPP_PHONE_NUMBER_ID manquant")
        return {"status": "ignored"}
    data = await request.json()
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []):
                sender, msg_id = m.get("from"), m.get("id", "")
                if not sender or _seen.check_and_add(f"wa:{msg_id}"):
                    continue
                if s.whatsapp_allowed_numbers and sender not in s.whatsapp_allowed_numbers:
                    log.info("Message WhatsApp ignoré (numéro non autorisé) : %s", sender)
                    _event("whatsapp", "numéro_non_autorisé", sender,
                           f"expéditeur …{sender[-4:]} absent de WHATSAPP_ALLOWED_NUMBERS "
                           f"({len(s.whatsapp_allowed_numbers)} numéro(s) configuré(s))")
                    continue
                _event("whatsapp", "message_reçu", sender, m.get("type", ""))
                body = _safe_reply(m["text"].get("body", "") if m.get("type") == "text" else None, "whatsapp", svc)
                _wa_send(s, sender, body, reply_to=msg_id)
    return {"status": "ok"}  # 200 rapide, sinon Meta renvoie le message


# --------------------------------------------------------------------------- Telegram
def _tg_api(settings, method: str, **payload) -> dict:
    try:
        r = httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}", json=payload, timeout=20)
    except httpx.HTTPError as exc:
        log.error("Telegram %s impossible (réseau) : %s", method, exc)
        return {"ok": False, "description": str(exc)}
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not data.get("ok"):
        log.error("Telegram %s a échoué : %s", method, r.text[:300])
    return data


@router.post("/api/telegram/webhook", include_in_schema=False)
async def telegram_webhook(request: Request):
    svc = _services()
    s = svc.settings
    if not s.telegram_bot_token:
        return {"status": "ignored"}
    if s.telegram_webhook_secret and not hmac.compare_digest(
            request.headers.get("x-telegram-bot-api-secret-token", ""), s.telegram_webhook_secret):
        raise HTTPException(401, "Secret invalide")
    update = await request.json()
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None or _seen.check_and_add(f"tg:{update.get('update_id')}"):
        return {"status": "ok"}
    body = _safe_reply(msg.get("text"), "telegram", svc)
    _tg_api(s, "sendMessage", chat_id=chat_id, text=body, reply_to_message_id=msg.get("message_id"))
    return {"status": "ok"}


@router.get("/api/telegram/setup", include_in_schema=False)
def telegram_setup(request: Request, key: str = ""):
    """À ouvrir une fois dans le navigateur : https://<site>/api/telegram/setup?key=<TELEGRAM_WEBHOOK_SECRET>"""
    s = _services().settings
    if not s.telegram_bot_token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN manquant")
    if not s.telegram_webhook_secret or not hmac.compare_digest(key, s.telegram_webhook_secret):
        raise HTTPException(403, "Paramètre key invalide (doit valoir TELEGRAM_WEBHOOK_SECRET)")
    url = str(request.base_url).rstrip("/").replace("http://", "https://") + "/api/telegram/webhook"
    result = _tg_api(s, "setWebhook", url=url, secret_token=s.telegram_webhook_secret,
                     allowed_updates=["message", "edited_message"], drop_pending_updates=True)
    return {"webhook": url, "telegram": result}

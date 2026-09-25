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
import re
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

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


@router.get("/api/whatsapp/qr.svg", include_in_schema=False)
def whatsapp_qr(lang: str = "fr"):
    """QR code qui ouvre la discussion WhatsApp avec le bot (message pré-rempli « aide » / « help »)."""
    import io

    import segno

    from .config import get_settings

    number = "".join(c for c in get_settings().whatsapp_display_number if c.isdigit())
    if not number:
        raise HTTPException(404, "WHATSAPP_DISPLAY_NUMBER non défini")
    buf = io.BytesIO()
    segno.make(f"https://wa.me/{number}?text={'help' if lang == 'en' else 'aide'}", error="m").save(
        buf, kind="svg", scale=6, border=2, dark="#0b3d5c", light="#ffffff")
    return Response(buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=3600"})


PROFILE_ABOUT = "Mémoire collective UniPod 🧠 — posez vos questions (FR/EN)"
PROFILE_DESCRIPTION = ("UniPods Memory répond aux questions de la communauté UniPod à partir des messages, réunions et "
                       "documents, en citant ses sources. "
                       "Envoyez « aide » pour commencer.")


@router.get("/api/whatsapp/setup-profile", include_in_schema=False)
def whatsapp_setup_profile(key: str = "", app_id: str = ""):
    """À ouvrir une fois : met le logo UniPod en photo de profil du bot et remplit sa description.
    https://<site>/api/whatsapp/setup-profile?key=<WHATSAPP_VERIFY_TOKEN>&app_id=<ID de l'app Meta>"""
    from .config import ROOT_DIR, get_settings

    s = get_settings()
    if not s.whatsapp_verify_token or not hmac.compare_digest(key, s.whatsapp_verify_token):
        raise HTTPException(403, "Paramètre key invalide (doit valoir WHATSAPP_VERIFY_TOKEN)")
    app_id = app_id or s.whatsapp_app_id
    if not (s.whatsapp_token and s.whatsapp_phone_number_id and app_id):
        raise HTTPException(400, "WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID et app_id (ou WHATSAPP_APP_ID) sont requis")
    graph = f"https://graph.facebook.com/{s.whatsapp_api_version}"
    image = (ROOT_DIR / "frontend" / "whatsapp-profile.jpg").read_bytes()
    steps: dict[str, Any] = {}
    try:
        # 1. Téléversement « reprenable » de l'image -> identifiant (handle) utilisable par WhatsApp
        r = httpx.post(f"{graph}/{app_id}/uploads", timeout=30,
                       params={"file_name": "unipod-logo.jpg", "file_length": len(image), "file_type": "image/jpeg",
                               "access_token": s.whatsapp_token})
        steps["upload_session"] = r.json()
        upload_id = steps["upload_session"].get("id")
        if not upload_id:
            return {"ok": False, "steps": steps}
        r = httpx.post(f"{graph}/{upload_id}", content=image, timeout=60,
                       headers={"Authorization": f"OAuth {s.whatsapp_token}", "file_offset": "0"})
        steps["upload"] = r.json()
        handle = steps["upload"].get("h")
        if not handle:
            return {"ok": False, "steps": steps}
        # 2. Profil WhatsApp Business : photo, « à propos » et description
        r = httpx.post(f"{graph}/{s.whatsapp_phone_number_id}/whatsapp_business_profile", timeout=30,
                       headers={"Authorization": f"Bearer {s.whatsapp_token}"},
                       json={"messaging_product": "whatsapp", "profile_picture_handle": handle,
                             "about": PROFILE_ABOUT, "description": PROFILE_DESCRIPTION})
        steps["profile"] = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "steps": steps}
    return {"ok": bool(steps["profile"].get("success")), "steps": steps}


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
    sender = str((msg.get("from") or {}).get("id", ""))
    text = msg.get("text")
    if text:
        # Dans un groupe, les commandes arrivent sous la forme « /aide@NomDuBot »
        text = re.sub(r"^(/\w+)@\w+", r"\1", text.strip())
    _event("telegram", "message_reçu", sender, "texte" if text else "non textuel")
    body = _safe_reply(text, "telegram", svc)
    result = _tg_api(s, "sendMessage", chat_id=chat_id, text=body, reply_to_message_id=msg.get("message_id"))
    _event("telegram", "réponse_envoyée" if result.get("ok") else "erreur_envoi", sender,
           "" if result.get("ok") else str(result.get("description", ""))[:180])
    return {"status": "ok"}


TELEGRAM_COMMANDS = {
    "fr": [{"command": "aide", "description": "Comment utiliser UniPods Memory"},
           {"command": "documents", "description": "Liste des sources indexées"},
           {"command": "resume", "description": "Résumé d'un document : /resume <nom>"}],
    "en": [{"command": "help", "description": "How to use UniPods Memory"},
           {"command": "documents", "description": "List indexed sources"},
           {"command": "resume", "description": "Summarize a document: /resume <name>"}],
}
TELEGRAM_DESCRIPTION = {
    "fr": ("🧠 UniPods Memory, la mémoire collective de la communauté UniPod. Posez vos questions sur les messages, "
           "réunions et documents du groupe : je réponds en citant mes sources, en français ou en anglais."),
    "en": ("🧠 UniPods Memory, the UniPod community's collective memory. Ask about the group's messages, meetings "
           "and documents: I answer with sources, in English or French."),
}
TELEGRAM_SHORT = {"fr": "La mémoire collective de la communauté UniPod 🧠",
                  "en": "The UniPod community's collective memory 🧠"}


@router.get("/api/telegram/release", include_in_schema=False)
def telegram_release(key: str = ""):
    """Libère le bot actuellement configuré (TELEGRAM_BOT_TOKEN) : supprime le webhook vers ce site, le menu
    de commandes et les descriptions posés par /api/telegram/setup. Utile si le mauvais bot a été branché.
    https://<site>/api/telegram/release?key=<TELEGRAM_WEBHOOK_SECRET>"""
    from .config import get_settings

    s = get_settings()
    if not s.telegram_bot_token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN manquant")
    if not s.telegram_webhook_secret or not hmac.compare_digest(key, s.telegram_webhook_secret):
        raise HTTPException(403, "Paramètre key invalide (doit valoir TELEGRAM_WEBHOOK_SECRET)")
    done = {"webhook_supprimé": _tg_api(s, "deleteWebhook").get("ok")}
    for lang, code in (("fr", None), ("en", "en")):
        extra = {"language_code": code} if code else {}
        done[f"commandes_{lang}"] = _tg_api(s, "deleteMyCommands", **extra).get("ok")
        done[f"description_{lang}"] = _tg_api(s, "setMyDescription", description="", **extra).get("ok")
        done[f"description_courte_{lang}"] = _tg_api(s, "setMyShortDescription", short_description="",
                                                     **extra).get("ok")
    me = _tg_api(s, "getMe").get("result")
    return {"bot_libéré": me.get("username") if isinstance(me, dict) else None, "résultat": done,
            "suite": "Remplacez TELEGRAM_BOT_TOKEN et TELEGRAM_BOT_USERNAME dans Vercel par ceux du nouveau bot, "
                     "redéployez, puis ouvrez /api/telegram/setup?key=…"}


@router.get("/api/telegram/qr.svg", include_in_schema=False)
def telegram_qr():
    """QR code qui ouvre la discussion Telegram avec le bot."""
    import io

    import segno

    from .config import get_settings

    username = get_settings().telegram_bot_username
    if not username:
        raise HTTPException(404, "TELEGRAM_BOT_USERNAME non défini")
    buf = io.BytesIO()
    segno.make(f"https://t.me/{username}", error="m").save(buf, kind="svg", scale=6, border=2,
                                                          dark="#0b3d5c", light="#ffffff")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


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
    # Menu des commandes et description du bot, en français (défaut) et en anglais
    profile = {}
    for lang, code in (("fr", None), ("en", "en")):
        extra = {"language_code": code} if code else {}
        profile[f"commands_{lang}"] = _tg_api(s, "setMyCommands", commands=TELEGRAM_COMMANDS[lang], **extra).get("ok")
        profile[f"description_{lang}"] = _tg_api(s, "setMyDescription", description=TELEGRAM_DESCRIPTION[lang],
                                                 **extra).get("ok")
        profile[f"short_description_{lang}"] = _tg_api(s, "setMyShortDescription",
                                                       short_description=TELEGRAM_SHORT[lang], **extra).get("ok")
    me = _tg_api(s, "getMe").get("result")
    me = me if isinstance(me, dict) else {}
    return {"webhook": url, "telegram": result, "profile": profile, "bot": me.get("username"),
            "hint": None if s.telegram_bot_username else
            f"Ajoutez TELEGRAM_BOT_USERNAME={me.get('username', '<nom du bot>')} dans Vercel pour afficher le bot sur le site"}

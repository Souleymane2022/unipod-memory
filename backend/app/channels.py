"""Webhooks des messageries : WhatsApp Cloud API (Meta) et Telegram.

WhatsApp
    GET  /api/whatsapp/webhook   vérification par Meta (hub.challenge) avec WHATSAPP_VERIFY_TOKEN
    POST /api/whatsapp/webhook   messages entrants ; signature X-Hub-Signature-256 vérifiée avec WHATSAPP_APP_SECRET
Telegram
    POST /api/telegram/webhook   messages entrants ; en-tête secret vérifié avec TELEGRAM_WEBHOOK_SECRET
    GET  /api/telegram/setup?key=<TELEGRAM_WEBHOOK_SECRET>   enregistre le webhook auprès de Telegram (une fois)

Messages vocaux (voice.py) : la note vocale est téléchargée, transcrite, puis traitée comme une question écrite.
Le bot répond par écrit (« 🎤 J'ai entendu : … » + réponse citée) puis, si possible, par une note vocale.

Images : « génère une image de… » (écrit ou vocal) -> image générée (images.py) envoyée avec une légende.

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

from . import images, voice
from .i18n import detect_lang
from .messaging import error_reply, image_text, non_text_reply, reply, voice_text

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


def _image_turn(text: str, channel: str, svc, sender: str) -> tuple[str, tuple[bytes, str] | None] | None:
    """Demande d'image -> (légende ou message d'erreur, (octets, type MIME) ou None) ; None si ce n'en est pas une."""
    prompt = images.image_prompt(text)
    if prompt is None:
        return None
    lang = detect_lang(text)
    if not prompt:
        return image_text("ask", lang), None
    try:
        data, mime, provider = images.generate_image(prompt, svc.llm)
    except Exception as exc:
        log.warning("Image %s impossible : %s", channel, exc)
        _event(channel, "image_erreur", sender, str(exc))
        return image_text("failed", lang), None
    _event(channel, "image_générée", sender, provider)
    return image_text("caption", lang, prompt=prompt), (data, mime)


def _voice_turn(download, mime: str, channel: str, svc, sender: str) -> tuple[str, str | None, tuple | None]:
    """Note vocale -> (réponse écrite, texte à lire en vocal ou None, image demandée ou None).
    Ne lève jamais d'exception."""
    if not voice.can_transcribe(svc.llm):
        _event(channel, "vocal_non_activé", sender, f"fournisseur LLM : {svc.llm.provider}")
        return voice_text("unavailable"), None, None
    try:
        heard = voice.transcribe(download(), mime, svc.llm)
    except Exception as exc:
        log.warning("Note vocale %s non transcrite : %s", channel, exc)
        _event(channel, "vocal_erreur", sender, str(exc))
        return voice_text("failed"), None, None
    if not heard:
        return voice_text("empty"), None, None
    _event(channel, "vocal_transcrit", sender, f"{len(heard.split())} mots")
    header = voice_text("heard", detect_lang(heard), text=heard)
    if img := _image_turn(heard, channel, svc, sender):
        return header + "\n\n" + img[0], None, img[1]
    answer = _safe_reply(heard, channel, svc)
    return header + "\n\n" + answer, answer, None


def _voice_audio(answer: str | None, channel: str, svc, sender: str) -> bytes | None:
    """Réponse vocale MP3, ou None (désactivée, quota, erreur) : le message écrit suffit alors."""
    if not answer or not voice.can_speak(svc.llm):
        return None
    try:
        return voice.synthesize_mp3(answer, svc.llm)
    except Exception as exc:
        log.warning("Réponse vocale %s impossible : %s", channel, exc)
        _event(channel, "voix_erreur", sender, str(exc))
        return None


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


def _wa_download(settings, media_id: str) -> bytes:
    """Contenu d'un média reçu (note vocale) : l'API renvoie d'abord une URL temporaire, protégée par le jeton."""
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    meta = httpx.get(f"https://graph.facebook.com/{settings.whatsapp_api_version}/{media_id}", headers=headers, timeout=20)
    meta.raise_for_status()
    r = httpx.get(meta.json()["url"], headers=headers, timeout=30)
    r.raise_for_status()
    return r.content


def _wa_send_media(settings, to: str, data: bytes, mime: str, kind: str, caption: str = "",
                   reply_to: str | None = None) -> bool:
    """Téléverse un média (audio MP3, image) puis l'envoie ; kind = "audio" ou "image"."""
    base = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    ext = mime.split("/")[-1].replace("mpeg", "mp3").replace("jpeg", "jpg")
    try:
        up = httpx.post(f"{base}/media", headers=headers, timeout=30,
                        data={"messaging_product": "whatsapp", "type": mime},
                        files={"file": (f"unipods.{ext}", data, mime)})
        up.raise_for_status()
        media: dict[str, Any] = {"id": up.json()["id"]}
        if caption and kind == "image":
            media["caption"] = caption[:1024]
        payload: dict[str, Any] = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                                   "type": kind, kind: media}
        if reply_to:
            payload["context"] = {"message_id": reply_to}
        r = httpx.post(f"{base}/messages", headers=headers, timeout=20, json=payload)
        r.raise_for_status()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.error("Envoi %s WhatsApp impossible : %s", kind, exc)
        _event("whatsapp", "voix_erreur" if kind == "audio" else "image_erreur", to, f"envoi : {exc}")
        return False
    _event("whatsapp", "voix_envoyée" if kind == "audio" else "image_envoyée", to)
    return True


def _wa_send_audio(settings, to: str, mp3: bytes) -> None:
    _wa_send_media(settings, to, mp3, "audio/mpeg", "audio")


def _wa_send_image(settings, to: str, image: tuple[bytes, str], caption: str, reply_to: str | None = None) -> None:
    if not _wa_send_media(settings, to, image[0], image[1], "image", caption, reply_to):
        _wa_send(settings, to, image_text("failed", detect_lang(caption)), reply_to=reply_to)


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
                if m.get("type") == "audio" and (m.get("audio") or {}).get("id"):
                    audio = m["audio"]
                    body, spoken, image = _voice_turn(lambda: _wa_download(s, audio["id"]),
                                                      audio.get("mime_type", ""), "whatsapp", svc, sender)
                    _wa_send(s, sender, body, reply_to=msg_id)
                    if image:
                        _wa_send_image(s, sender, image, body.rsplit("\n\n", 1)[-1])
                    if mp3 := _voice_audio(spoken, "whatsapp", svc, sender):
                        _wa_send_audio(s, sender, mp3)
                    continue
                text = m["text"].get("body", "") if m.get("type") == "text" else None
                if text and (img := _image_turn(text, "whatsapp", svc, sender)):
                    caption, image = img
                    if image:
                        _wa_send_image(s, sender, image, caption, reply_to=msg_id)
                    else:
                        _wa_send(s, sender, caption, reply_to=msg_id)
                    continue
                body = _safe_reply(text, "whatsapp", svc)
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


def _tg_download(settings, file_id: str) -> bytes:
    info = _tg_api(settings, "getFile", file_id=file_id)
    path = (info.get("result") or {}).get("file_path")
    if not path:
        raise RuntimeError(f"getFile : {info.get('description', 'fichier introuvable')}")
    r = httpx.get(f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{path}", timeout=30)
    r.raise_for_status()
    return r.content


def _tg_send_voice(settings, chat_id, mp3: bytes, reply_to: int | None = None) -> None:
    data = {"chat_id": str(chat_id)}
    if reply_to:
        data["reply_to_message_id"] = str(reply_to)
    try:
        r = httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendVoice", data=data,
                       files={"voice": ("reponse.mp3", mp3, "audio/mpeg")}, timeout=30)
        ok = r.status_code < 400
    except httpx.HTTPError as exc:
        ok, r = False, None
        log.error("sendVoice impossible : %s", exc)
    _event("telegram", "voix_envoyée" if ok else "voix_erreur", str(chat_id),
           "" if ok else (r.text[:180] if r is not None else "réseau"))


def _tg_send_photo(settings, chat_id, image: tuple[bytes, str], caption: str, reply_to: int | None = None) -> bool:
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:1024]
    if reply_to:
        data["reply_to_message_id"] = str(reply_to)
    ext = image[1].split("/")[-1].replace("jpeg", "jpg")
    try:
        r = httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto", data=data,
                       files={"photo": (f"unipods.{ext}", image[0], image[1])}, timeout=30)
        ok = r.status_code < 400
    except httpx.HTTPError as exc:
        ok, r = False, None
        log.error("sendPhoto impossible : %s", exc)
    _event("telegram", "image_envoyée" if ok else "image_erreur", str(chat_id),
           "" if ok else (r.text[:180] if r is not None else "réseau"))
    return ok


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
    audio = msg.get("voice") or msg.get("audio")
    if audio and audio.get("file_id"):
        _event("telegram", "message_reçu", sender, "vocal")
        _tg_api(s, "sendChatAction", chat_id=chat_id, action="typing")
        body, spoken, image = _voice_turn(lambda: _tg_download(s, audio["file_id"]),
                                          audio.get("mime_type", "audio/ogg"), "telegram", svc, sender)
        result = _tg_api(s, "sendMessage", chat_id=chat_id, text=body, reply_to_message_id=msg.get("message_id"))
        _event("telegram", "réponse_envoyée" if result.get("ok") else "erreur_envoi", sender,
               "" if result.get("ok") else str(result.get("description", ""))[:180])
        if image:
            _tg_send_photo(s, chat_id, image, "")
        if mp3 := _voice_audio(spoken, "telegram", svc, sender):
            _tg_send_voice(s, chat_id, mp3, msg.get("message_id"))
        return {"status": "ok"}
    text = msg.get("text")
    if text:
        # Dans un groupe, les commandes arrivent sous la forme « /aide@NomDuBot »
        text = re.sub(r"^(/\w+)@\w+", r"\1", text.strip())
    _event("telegram", "message_reçu", sender, "texte" if text else "non textuel")
    if text and images.image_prompt(text):
        _tg_api(s, "sendChatAction", chat_id=chat_id, action="upload_photo")
    if text and (img := _image_turn(text, "telegram", svc, sender)):
        caption, image = img
        if image and _tg_send_photo(s, chat_id, image, caption, msg.get("message_id")):
            return {"status": "ok"}
        body = caption if not image else image_text("failed", detect_lang(text))
    else:
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
    # Sécurité : on vérifie d'abord à quel bot appartient le jeton, pour ne jamais rediriger un autre bot par erreur
    me = _tg_api(s, "getMe").get("result")
    me = me if isinstance(me, dict) else {}
    actual = me.get("username") or ""
    expected = s.telegram_bot_username
    if not expected or actual.lower() != expected.lower():
        raise HTTPException(409, (
            f"Le jeton TELEGRAM_BOT_TOKEN appartient au bot @{actual or '?'}, mais TELEGRAM_BOT_USERNAME vaut "
            f"« {expected or '(vide)'} ». Rien n'a été modifié. Mettez dans Vercel le jeton du bot voulu et son nom "
            f"(sans @), redéployez, puis rouvrez ce lien."))
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
    return {"webhook": url, "telegram": result, "profile": profile, "bot": actual}

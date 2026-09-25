"""Génération d'images : détection des demandes, Gemini puis repli Pollinations, envoi WhatsApp / Telegram / site."""
import base64

import httpx
import pytest

from backend.app import channels, images, voice
from backend.app.config import Settings
from backend.app.llm import LLMClient

from .test_channels import _post_wa, _wa_payload  # noqa: F401
from .test_channels import tg, wa  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\nfake-image"


@pytest.mark.parametrize("text, prompt", [
    ("génère une image d'un robot au Tchad", "un robot au Tchad"),
    ("Génère-moi une image de l'UniPod", "l'UniPod"),
    ("/image un chat astronaute", "un chat astronaute"),
    ("/image@UniPodsMemory2026Bot lion", "lion"),
    ("Dessine-moi un mouton", "un mouton"),
    ("Create an image of a 3D printer", "a 3D printer"),
    ("image: coucher de soleil à N'Djamena", "coucher de soleil à N'Djamena"),
    ("Fais une affiche pour le hackathon", "affiche pour le hackathon"),
    ("génère une image", ""),
])
def test_image_requests_detected(text, prompt):
    assert images.image_prompt(text) == prompt


@pytest.mark.parametrize("text", ["Quelle est la date limite ?", "Qui a créé l'image de marque ?",
                                  "Comment faire une photo de groupe ?", "Résume le document METI",
                                  "Show me the image policy?", "drawing tools?"])
def test_normal_questions_are_not_image_requests(text):
    assert images.image_prompt(text) is None


def _llm(monkeypatch, key="g-key"):
    monkeypatch.setenv("GEMINI_API_KEY", key)
    monkeypatch.setenv("LLM_PROVIDER", "gemini" if key else "none")
    return LLMClient(Settings())


def test_gemini_image(monkeypatch):
    llm = _llm(monkeypatch)
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "Voici"}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}]}}]},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(images.httpx, "post", fake_post)
    assert images.generate_image("un robot", llm) == (PNG, "image/png", "gemini")
    assert calls[0][0].endswith("/models/gemini-2.5-flash-image:generateContent")
    assert calls[0][1]["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]


def test_falls_back_to_pollinations_when_gemini_quota_is_zero(monkeypatch):
    llm = _llm(monkeypatch)
    monkeypatch.setattr(images.httpx, "post", lambda url, **kw: httpx.Response(
        429, json={"error": {"message": "limit: 0"}}, request=httpx.Request("POST", url)))
    got = {}

    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=None):
        got.update(url=url, params=params)
        return httpx.Response(200, content=b"JPEG", headers={"content-type": "image/jpeg"},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(images.httpx, "get", fake_get)
    assert images.generate_image("un chat astronaute", llm) == (b"JPEG", "image/jpeg", "pollinations")
    assert got["url"].endswith("/prompt/un%20chat%20astronaute") and got["params"]["nologo"] == "true"


def test_no_generator_raises(monkeypatch):
    monkeypatch.setenv("IMAGE_FALLBACK", "none")
    with pytest.raises(images.ImageError):
        images.generate_image("x", _llm(monkeypatch, key=""))


@pytest.fixture
def fake_image(monkeypatch):
    monkeypatch.setattr(images, "generate_image", lambda prompt, llm: (PNG, "image/png", "gemini"))


def test_site_returns_generated_image(client, fake_image):
    r = client.post("/api/ask", json={"question": "Génère une image d'un fablab futuriste", "lang": "fr"}).json()
    assert r["mode"] == "image" and r["image"].startswith("data:image/png;base64,")
    assert base64.b64decode(r["image"].split(",", 1)[1]) == PNG
    assert r["answer"] == "🎨 Image générée pour : « un fablab futuriste »"
    empty = client.post("/api/ask", json={"question": "génère une image", "lang": "en"}).json()
    assert "What should I draw" in empty["answer"] and "image" not in empty


def test_site_image_failure_is_graceful(client, monkeypatch):
    def fail(prompt, llm):
        raise images.ImageError("quota")

    monkeypatch.setattr(images, "generate_image", fail)
    r = client.post("/api/ask", json={"question": "draw a lion", "lang": "en"}).json()
    assert r["found"] is False and "couldn't generate" in r["answer"]


def test_whatsapp_image_request(client, wa, fake_image, monkeypatch):
    posts = []

    def fake_post(url, json=None, headers=None, timeout=None, data=None, files=None):
        posts.append({"url": url, "json": json, "files": files})
        body = {"id": "media-img"} if url.endswith("/media") else {"messages": [{"id": "wamid.out"}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(channels.httpx, "post", fake_post)
    _post_wa(client, _wa_payload("/image un chat astronaute", msg_id="i1"))
    upload, message = posts
    assert upload["url"].endswith("/media") and upload["files"]["file"] == ("unipods.png", PNG, "image/png")
    assert message["json"]["type"] == "image" and message["json"]["image"]["id"] == "media-img"
    assert "un chat astronaute" in message["json"]["image"]["caption"]
    assert message["json"]["context"] == {"message_id": "i1"}


def test_telegram_image_request_and_voice_image(client, tg, fake_image, monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None, data=None, files=None):
        calls.append({"url": url, "json": json, "data": data, "files": files})
        result = {"file_path": "voice/f.oga"} if url.endswith("/getFile") else True
        return httpx.Response(200, json={"ok": True, "result": result}, request=httpx.Request("POST", url))

    monkeypatch.setattr(channels.httpx, "post", fake_post)
    hdr = {"X-Telegram-Bot-Api-Secret-Token": "tg-secret"}
    update = {"update_id": 90, "message": {"message_id": 3, "chat": {"id": 42, "type": "private"},
                                           "from": {"id": 42}, "text": "Dessine-moi un mouton"}}
    assert client.post("/api/telegram/webhook", json=update, headers=hdr).status_code == 200
    photo = calls[-1]
    assert photo["url"].endswith("/sendPhoto") and photo["files"]["photo"][1] == PNG
    assert photo["data"]["caption"] == "🎨 Image générée pour : « un mouton »"

    # Demande d'image par message vocal : transcription, texte, puis image (pas de réponse vocale)
    calls.clear()
    monkeypatch.setattr(voice, "can_transcribe", lambda llm: True)
    monkeypatch.setattr(voice, "can_speak", lambda llm: True)
    monkeypatch.setattr(voice, "transcribe", lambda a, m, llm: "Génère une image d'un lion")
    monkeypatch.setattr(channels.httpx, "get", lambda url, **kw: httpx.Response(
        200, content=b"OggS", request=httpx.Request("GET", url)))
    update = {"update_id": 91, "message": {"message_id": 4, "chat": {"id": 42, "type": "private"},
                                           "from": {"id": 42}, "voice": {"file_id": "F", "mime_type": "audio/ogg"}}}
    assert client.post("/api/telegram/webhook", json=update, headers=hdr).status_code == 200
    methods = [c["url"].rsplit("/", 1)[-1] for c in calls]
    assert methods == ["sendChatAction", "getFile", "sendMessage", "sendPhoto"]
    assert "un lion" in calls[2]["json"]["text"]

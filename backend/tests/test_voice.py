"""Messages vocaux : transcription (Gemini / Whisper), réponse vocale MP3 et webhooks WhatsApp / Telegram."""
import base64
import math
import struct
from types import SimpleNamespace

import httpx
import pytest

from backend.app import channels, main, voice
from backend.app.llm import LLMClient
from backend.app.config import Settings

from .test_channels import SECRET, _post_wa, _wa_payload  # noqa: F401  (fixtures réutilisées)
from .test_channels import tg, wa  # noqa: F401


def _pcm(seconds=0.3, rate=24000):
    return b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(rate * seconds)))


def _gemini_llm(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    return LLMClient(Settings())


def test_transcribe_with_gemini_sends_inline_audio(monkeypatch):
    llm = _gemini_llm(monkeypatch)
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        calls.append((url, json, headers))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "Quelle est la date limite du hackathon ?"}]}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(voice.httpx, "post", fake_post)
    assert voice.transcribe(b"OggS...", "audio/ogg; codecs=opus", llm) == "Quelle est la date limite du hackathon ?"
    url, body, headers = calls[0]
    assert url.endswith(f"/models/{llm.model}:generateContent") and headers["x-goog-api-key"] == "g-key"
    inline = body["contents"][0]["parts"][1]["inline_data"]
    assert inline["mime_type"] == "audio/ogg" and base64.b64decode(inline["data"]) == b"OggS..."


def test_transcribe_falls_back_to_next_model_on_daily_quota(monkeypatch):
    llm = _gemini_llm(monkeypatch)
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        seen.append(url)
        if len(seen) == 1:
            return httpx.Response(429, json={"error": {"message": "GenerateRequestsPerDay exceeded"}},
                                  request=httpx.Request("POST", url))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "EMPTY"}]}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(voice.httpx, "post", fake_post)
    assert voice.transcribe(b"x", "audio/ogg", llm) == ""  # rien d'intelligible
    assert len(seen) == 2 and seen[0] != seen[1]


def test_transcribe_errors_become_voice_error(monkeypatch):
    llm = _gemini_llm(monkeypatch)
    monkeypatch.setattr(voice.httpx, "post", lambda url, **kw: httpx.Response(
        400, json={"error": {"message": "bad audio"}}, request=httpx.Request("POST", url)))
    with pytest.raises(voice.VoiceError):
        voice.transcribe(b"x", "audio/ogg", llm)
    with pytest.raises(voice.VoiceError):
        voice.transcribe(b"x" * (voice.MAX_AUDIO_BYTES + 1), "audio/ogg", llm)


def test_whisper_transcription_for_openai_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    llm = LLMClient(Settings())
    got = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None, **kw):
        got.update(url=url, data=data, files=files)
        return httpx.Response(200, json={"text": "What is the deadline?"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(voice.httpx, "post", fake_post)
    assert voice.transcribe(b"audio", "audio/ogg", llm) == "What is the deadline?"
    assert got["url"].endswith("/audio/transcriptions") and got["data"]["model"] == "whisper-1"
    assert got["files"]["file"][0] == "voice.ogg"


def test_speakable_drops_sources_markup_and_emojis():
    text = "La date limite est le *10 octobre 2026* [1].\n\n📚 Sources :\n[1] chat_general.txt"
    assert voice.speakable(text) == "La date limite est le 10 octobre 2026."
    long = "Phrase courte. " * 100
    assert len(voice.speakable(long)) <= voice.MAX_SPOKEN_CHARS and voice.speakable(long).endswith(".")


def test_synthesize_mp3_from_gemini_tts(monkeypatch):
    llm = _gemini_llm(monkeypatch)
    pcm = _pcm()
    bodies = []

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        bodies.append((url, json))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}]},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(voice.httpx, "post", fake_post)
    assert voice.can_speak(llm)
    mp3 = voice.synthesize_mp3("La date limite est le 10 octobre [1].\n📚 Sources : x", llm)
    assert mp3[:3] == b"ID3" or mp3[0] == 0xFF  # trame MP3
    url, body = bodies[0]
    assert url.endswith(f"/models/{llm.settings.tts_model}:generateContent")
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert body["contents"][0]["parts"][0]["text"] == "La date limite est le 10 octobre."


def test_tts_falls_back_when_model_retired(monkeypatch):
    llm = _gemini_llm(monkeypatch)
    urls = []

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        urls.append(url)
        if len(urls) == 1:
            return httpx.Response(404, json={"error": {"message": "model not found"}}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": "audio/L16;rate=24000", "data": base64.b64encode(_pcm(0.1)).decode()}}]}}]},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(voice.httpx, "post", fake_post)
    assert voice.synthesize_mp3("Bonjour.", llm)
    assert len(urls) == 2 and urls[0] != urls[1]


def test_voice_replies_can_be_disabled(monkeypatch):
    monkeypatch.setenv("VOICE_REPLIES", "false")
    assert not voice.can_speak(_gemini_llm(monkeypatch))


@pytest.fixture
def voice_on(monkeypatch):
    """Transcription et synthèse simulées (le moteur de réponse reste le vrai, en mode extractif)."""
    monkeypatch.setattr(voice, "can_transcribe", lambda llm: True)
    monkeypatch.setattr(voice, "can_speak", lambda llm: True)
    monkeypatch.setattr(voice, "transcribe", lambda audio, mime, llm: "Comment réserver une machine du fablab ?")
    monkeypatch.setattr(voice, "synthesize_mp3", lambda text, llm: b"MP3:" + text.encode())
    gets = []

    def fake_get(url, headers=None, timeout=None, **kw):
        gets.append(url)
        body = {"url": "https://lookaside.fbsbx.com/media/abc"} if "graph.facebook.com" in url else None
        return httpx.Response(200, json=body, request=httpx.Request("GET", url)) if body else \
            httpx.Response(200, content=b"OggS-audio", request=httpx.Request("GET", url))

    monkeypatch.setattr(channels.httpx, "get", fake_get)
    return gets


def test_whatsapp_voice_note_gets_text_and_voice_answer(client, wa, voice_on, monkeypatch):
    posts = []

    def fake_post(url, json=None, headers=None, timeout=None, data=None, files=None):
        posts.append({"url": url, "json": json, "data": data, "files": files})
        body = {"id": "media-out"} if url.endswith("/media") else {"messages": [{"id": "wamid.out"}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(channels.httpx, "post", fake_post)
    assert _post_wa(client, _wa_payload(mtype="audio", msg_id="v1")).status_code == 200
    assert voice_on == ["https://graph.facebook.com/v23.0/media-1", "https://lookaside.fbsbx.com/media/abc"]
    text_msg, upload, audio_msg = posts
    body = text_msg["json"]["text"]["body"]
    assert body.startswith("🎤 J'ai entendu : « Comment réserver une machine du fablab ? »") and "48 heures" in body
    assert upload["url"].endswith("/1234567890/media") and upload["files"]["file"][2] == "audio/mpeg"
    assert audio_msg["json"]["type"] == "audio" and audio_msg["json"]["audio"] == {"id": "media-out"}


def test_whatsapp_voice_without_transcription_invites_to_type(client, wa):
    _post_wa(client, _wa_payload(mtype="audio", msg_id="v2"))
    assert len(wa) == 1 and "texte" in wa[0]["json"]["text"]["body"]


def test_whatsapp_voice_transcription_failure_still_answers(client, wa, voice_on, monkeypatch):
    def fail(audio, mime, llm):
        raise voice.VoiceError("quota")

    monkeypatch.setattr(voice, "transcribe", fail)
    assert _post_wa(client, _wa_payload(mtype="audio", msg_id="v3")).status_code == 200
    assert len(wa) == 1 and "Je n'ai pas réussi" in wa[0]["json"]["text"]["body"]


def test_telegram_voice_note(client, tg, voice_on, monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None, data=None, files=None):
        calls.append({"url": url, "json": json, "data": data, "files": files})
        result = {"file_path": "voice/file_1.oga"} if url.endswith("/getFile") else True
        return httpx.Response(200, json={"ok": True, "result": result}, request=httpx.Request("POST", url))

    monkeypatch.setattr(channels.httpx, "post", fake_post)
    update = {"update_id": 77, "message": {"message_id": 9, "chat": {"id": 42, "type": "private"},
                                           "from": {"id": 42}, "voice": {"file_id": "F1", "mime_type": "audio/ogg",
                                                                         "duration": 3}}}
    r = client.post("/api/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
    assert r.status_code == 200
    assert voice_on == ["https://api.telegram.org/file/bot123:ABC/voice/file_1.oga"]
    methods = [c["url"].rsplit("/", 1)[-1] for c in calls]
    assert methods == ["sendChatAction", "getFile", "sendMessage", "sendVoice"]
    assert "48 heures" in calls[2]["json"]["text"] and calls[2]["json"]["text"].startswith("🎤")
    assert calls[3]["data"]["chat_id"] == "42" and calls[3]["files"]["voice"][1].startswith(b"MP3:")

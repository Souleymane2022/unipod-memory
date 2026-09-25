"""Messages vocaux sur WhatsApp et Telegram.

- Transcription de la note vocale : Gemini (audio envoyé directement au modèle, API native) ou, avec
  LLM_PROVIDER=openai, l'API Whisper (/audio/transcriptions, aussi proposée par Groq).
- Réponse vocale : synthèse Gemini TTS (PCM 24 kHz) encodée en MP3 (lameenc), envoyée en plus de la
  réponse écrite, qui reste la référence (sources citées). Si la synthèse échoue (quota, modèle), seule la
  réponse écrite part : la voix ne bloque jamais le bot.
"""
from __future__ import annotations

import base64
import logging
import re

import httpx

from .llm import LLMError, raise_for_status

log = logging.getLogger("unipods.voice")

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
MAX_AUDIO_BYTES = 15 * 1024 * 1024  # limite des données « inline » de Gemini (20 Mo), avec marge
MAX_SPOKEN_CHARS = 700  # réponse vocale courte : le détail et les sources restent dans le message écrit
TTS_VOICE = "Kore"
# Si le modèle TTS configuré est retiré (404) ou au quota du jour, on essaie les suivants.
TTS_FALLBACK_MODELS = ("gemini-2.5-flash-preview-tts", "gemini-2.5-flash-tts", "gemini-2.5-pro-preview-tts")

TRANSCRIBE_PROMPT = (
    "Transcribe this voice message word for word, in its original language (usually French or English). "
    "Output only the transcription, without quotes or comments. If there is no intelligible speech, output "
    "exactly: EMPTY")


class VoiceError(RuntimeError):
    pass


def can_transcribe(llm) -> bool:
    return llm.provider in ("gemini", "openai")


def transcribe(audio: bytes, mime: str, llm) -> str:
    """Texte de la note vocale ('' si rien d'intelligible). Lève VoiceError si la transcription échoue."""
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceError("audio trop long")
    mime = (mime or "audio/ogg").split(";")[0].strip()
    try:
        if llm.provider == "gemini":
            text = llm.with_models(lambda: _gemini_transcribe(audio, mime, llm))
        elif llm.provider == "openai":
            text = _whisper_transcribe(audio, mime, llm)
        else:
            raise VoiceError(f"transcription non disponible avec le fournisseur {llm.provider}")
    except (LLMError, httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise VoiceError(str(exc)) from exc
    text = text.strip().strip('"«» ').strip()
    return "" if text.upper().rstrip(".") == "EMPTY" else text


def _gemini_post(llm, model: str, body: dict) -> dict:
    r = httpx.post(f"{GEMINI_API}/models/{model}:generateContent", json=body, timeout=llm.settings.llm_timeout,
                   headers={"x-goog-api-key": llm.settings.gemini_api_key})
    raise_for_status(r, "gemini")
    return r.json()


def _gemini_transcribe(audio: bytes, mime: str, llm) -> str:
    data = _gemini_post(llm, llm.model, {
        "contents": [{"parts": [{"text": TRANSCRIBE_PROMPT},
                                {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}}]}],
        "generationConfig": {"temperature": 0},
    })
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _whisper_transcribe(audio: bytes, mime: str, llm) -> str:
    base = (llm.settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
    ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/aac": "aac",
           "audio/amr": "amr", "audio/wav": "wav"}.get(mime, "ogg")
    r = httpx.post(f"{base}/audio/transcriptions", timeout=llm.settings.llm_timeout,
                   headers={"Authorization": f"Bearer {llm.settings.openai_api_key}"},
                   data={"model": llm.settings.stt_model or "whisper-1"},
                   files={"file": (f"voice.{ext}", audio, mime)})
    raise_for_status(r, "openai")
    return r.json()["text"]


# --------------------------------------------------------------------------- réponse vocale
def speakable(text: str) -> str:
    """Partie de la réponse à lire : sans la liste des sources, les renvois [1] ni la mise en forme."""
    text = re.split(r"\n\s*📚", text, maxsplit=1)[0]
    text = re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", text)
    text = re.sub(r"[*_`#>•]", " ", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", " ", text)  # émojis
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,])", r"\1", text)
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[: end + 1] if end > MAX_SPOKEN_CHARS // 3 else cut.rsplit(" ", 1)[0] + "…"
    return text


def can_speak(llm) -> bool:
    if llm.provider != "gemini" or not llm.settings.voice_replies:
        return False
    try:
        import lameenc  # noqa: F401
    except ImportError:
        return False
    return True


def synthesize_mp3(text: str, llm) -> bytes:
    """Note vocale MP3 de la réponse (Gemini TTS). Lève VoiceError en cas d'échec."""
    text = speakable(text)
    if not text:
        raise VoiceError("rien à lire")
    body = {"contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": TTS_VOICE}}}}}
    models = [llm.settings.tts_model] + [m for m in TTS_FALLBACK_MODELS if m != llm.settings.tts_model]
    for model in models:
        try:
            data = _gemini_post(llm, model, body)
            part = next(p["inlineData"] for p in data["candidates"][0]["content"]["parts"] if "inlineData" in p)
            break
        except LLMError as exc:
            if not (exc.status == 404 or exc.daily_quota) or model == models[-1]:
                raise VoiceError(f"synthèse vocale ({model}) : {exc}") from exc
            log.warning("Modèle TTS %s indisponible, essai du suivant : %s", model, exc)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, StopIteration) as exc:
            raise VoiceError(f"synthèse vocale ({model}) : {exc}") from exc
    pcm = base64.b64decode(part["data"])
    rate = int(m.group(1)) if (m := re.search(r"rate=(\d+)", part.get("mimeType", ""))) else 24000
    return pcm_to_mp3(pcm, rate)


def pcm_to_mp3(pcm: bytes, rate: int = 24000) -> bytes:
    import lameenc

    enc = lameenc.Encoder()
    enc.set_bit_rate(48)
    enc.set_in_sample_rate(rate)
    enc.set_channels(1)
    enc.set_quality(5)
    return bytes(enc.encode(pcm) + enc.flush())

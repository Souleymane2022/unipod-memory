"""Traduction des extraits cités quand la langue de l'utilisateur diffère de celle de la source.

Fournisseurs (TRANSLATION_PROVIDER) :
- auto     : le LLM s'il est configuré, sinon MyMemory (défaut)
- llm      : le LLM configuré (Anthropic / OpenAI-compatible / Ollama)
- mymemory : API gratuite https://mymemory.translated.net, sans clé (≈5 000 caractères/jour,
             ≈50 000 avec MYMEMORY_EMAIL). Les phrases traduites sont envoyées à ce service.
- none     : pas de traduction, les citations restent dans leur langue d'origine.

En cas d'échec (quota, réseau), on renvoie None et l'appelant garde le texte original.
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .config import Settings
from .llm import LLMClient, LLMError

log = logging.getLogger(__name__)

RETRY_AFTER_S = 300  # après un échec (quota, réseau), on n'essaie plus pendant 5 min pour ne pas ralentir les réponses
MAX_QUERY_CHARS = 450  # MyMemory limite chaque requête à 500 octets
LANG_NAMES = {"fr": "French", "en": "English"}


class Translator:
    def __init__(self, settings: Settings, llm: LLMClient):
        self.settings = settings
        self.llm = llm
        provider = settings.translation_provider
        if provider == "auto":
            provider = "llm" if llm.enabled else "mymemory"
        if provider == "llm" and not llm.enabled:
            provider = "none"
        self.provider = provider
        self._cache: dict[tuple[str, str, str], str] = {}
        self._paused_until = 0.0

    @property
    def enabled(self) -> bool:
        return self.provider in ("llm", "mymemory")

    def translate(self, text: str, src: str, tgt: str) -> str | None:
        text = text.strip()
        if not text or not self.enabled:
            return None
        if src == tgt:
            return text
        key = (text, src, tgt)
        if key in self._cache:
            return self._cache[key]
        if time.monotonic() < self._paused_until:
            return None
        try:
            out = self._llm(text, src, tgt) if self.provider == "llm" else self._mymemory(text, src, tgt)
        except (httpx.HTTPError, LLMError, ValueError, KeyError) as exc:
            log.warning("Traduction impossible (%s), suspendue %ss : %s", self.provider, RETRY_AFTER_S, exc)
            self._paused_until = time.monotonic() + RETRY_AFTER_S
            return None
        if out:
            self._cache[key] = out
        return out

    def translate_many(self, texts: list[str], src_langs: list[str], tgt: str) -> list[str | None]:
        """Plusieurs traductions ; None pour chaque échec.

        Avec un LLM : une seule requête pour tout le lot (les offres gratuites limitent le nombre de
        requêtes par minute). Avec MyMemory : requêtes en parallèle (une par texte)."""
        if not texts:
            return []
        if self.provider == "llm" and len(texts) > 1:
            return self._llm_batch(texts, src_langs, tgt)
        with ThreadPoolExecutor(max_workers=min(4, len(texts))) as pool:
            return list(pool.map(lambda args: self.translate(args[0], args[1], tgt), zip(texts, src_langs)))

    # ------------------------------------------------------------------ fournisseurs
    def _llm(self, text: str, src: str, tgt: str) -> str | None:
        system = (f"Translate the user's text from {LANG_NAMES[src]} to {LANG_NAMES[tgt]}. "
                  "Output only the translation, keep names, dates, numbers and amounts unchanged.")
        return self.llm.complete(system, text, max_tokens=600).strip() or None

    def _llm_batch(self, texts: list[str], src_langs: list[str], tgt: str) -> list[str | None]:
        results: list[str | None] = [None] * len(texts)
        todo = []
        for i, (text, src) in enumerate(zip(texts, src_langs)):
            key = (text.strip(), src, tgt)
            if src == tgt:
                results[i] = text
            elif key in self._cache:
                results[i] = self._cache[key]
            else:
                todo.append(i)
        if not todo or time.monotonic() < self._paused_until:
            return results
        system = (f"Translate each string of the JSON array into {LANG_NAMES[tgt]}. Keep names, dates, numbers and "
                  "amounts unchanged. Answer ONLY with a JSON array of strings, same length and same order.")
        payload = json.dumps([texts[i] for i in todo], ensure_ascii=False)
        try:
            raw = self.llm.complete(system, payload, max_tokens=300 + 2 * len(payload))
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            out = json.loads(m.group(0) if m else raw)
            if not isinstance(out, list) or len(out) != len(todo):
                raise ValueError(f"réponse de {len(out) if isinstance(out, list) else '?'} éléments au lieu de {len(todo)}")
        except (LLMError, ValueError) as exc:
            log.warning("Traduction groupée impossible, suspendue %ss : %s", RETRY_AFTER_S, exc)
            self._paused_until = time.monotonic() + RETRY_AFTER_S
            return results
        for i, tr in zip(todo, out):
            if isinstance(tr, str) and tr.strip():
                results[i] = tr.strip()
                self._cache[(texts[i].strip(), src_langs[i], tgt)] = results[i]
        return results

    def _mymemory(self, text: str, src: str, tgt: str) -> str | None:
        parts = []
        for piece in _split(text, MAX_QUERY_CHARS):
            params = {"q": piece, "langpair": f"{src}|{tgt}"}
            if self.settings.mymemory_email:
                params["de"] = self.settings.mymemory_email
            r = httpx.get(self.settings.mymemory_url, params=params, timeout=8)
            r.raise_for_status()
            data = r.json()
            translated = (data.get("responseData") or {}).get("translatedText") or ""
            if str(data.get("responseStatus")) != "200" or not translated or "MYMEMORY WARNING" in translated.upper():
                raise ValueError(f"MyMemory: {data.get('responseDetails') or data.get('responseStatus')}")
            parts.append(translated)
        return " ".join(parts) or None


def _split(text: str, limit: int) -> list[str]:
    """Découpe en morceaux ≤ limit caractères, aux espaces."""
    out, buf = [], ""
    for word in text.split():
        if buf and len(buf) + 1 + len(word) > limit:
            out.append(buf)
            buf = word
        else:
            buf = f"{buf} {word}".strip()
    if buf:
        out.append(buf)
    return out

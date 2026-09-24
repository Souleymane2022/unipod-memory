"""Client LLM optionnel. Aucun fournisseur n'est obligatoire : sans clé, le chatbot
fonctionne en mode extractif (il cite directement les passages trouvés).

Fournisseurs :
- anthropic : ANTHROPIC_API_KEY
- gemini    : GEMINI_API_KEY (Google AI Studio, offre gratuite), via l'API compatible OpenAI de Google
- openai    : OPENAI_API_KEY (+ LLM_BASE_URL pour tout serveur compatible : Groq, Mistral, OpenRouter…)
- ollama    : modèle local gratuit (LLM_BASE_URL par défaut http://localhost:11434/v1)
"""
from __future__ import annotations

import logging
import time

import httpx

from .config import Settings

log = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
# Chaîne de secours : chaque modèle a son propre quota gratuit quotidien (ex. 20 requêtes/jour pour
# gemini-3.6-flash). Quand le quota du jour d'un modèle est épuisé, on passe au suivant.
GEMINI_FALLBACK_MODELS = ("gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-flash-latest")
EXHAUSTED_PAUSE_S = 3600  # un modèle au quota du jour épuisé est mis de côté 1 h
# Les modèles Gemini récents « réfléchissent » avant de répondre et ces tokens comptent dans max_tokens :
# on ajoute une marge pour ne pas obtenir une réponse vide ou tronquée.
GEMINI_THINKING_MARGIN = 3000

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    # Alias qui suit la dernière version « lite » : quota gratuit plus large et jamais « retiré »
    "gemini": "gemini-flash-lite-latest",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1",
}


# Erreurs temporaires (surcharge, limite de requêtes par minute de l'offre gratuite) : on réessaie.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRY_DELAYS_S = (2, 5)
MAX_RETRY_AFTER_S = 10


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None,
                 daily_quota: bool = False):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.daily_quota = daily_quota  # quota du jour épuisé : inutile de réessayer avec ce modèle


def _is_daily_quota(r: httpx.Response) -> bool:
    return r.status_code == 429 and ("PerDay" in r.text or "per day" in r.text.lower())


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return float(r.headers.get("retry-after", ""))
    except ValueError:
        return None


def _error_message(r: httpx.Response) -> str:
    """Message d'erreur lisible (Google renvoie [{"error": {...}}], OpenAI/Anthropic {"error": {...}})."""
    try:
        data = r.json()
        if isinstance(data, list) and data:
            data = data[0]
        err = data.get("error", data)
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return str(msg or r.text)[:300]
    except ValueError:
        return r.text[:300]


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        provider = settings.llm_provider
        if provider == "auto":
            if settings.anthropic_api_key:
                provider = "anthropic"
            elif settings.gemini_api_key:
                provider = "gemini"
            elif settings.openai_api_key:
                provider = "openai"
            else:
                provider = "none"
        self.provider = provider
        self.model = settings.llm_model or DEFAULT_MODELS.get(provider, "")
        self._models = [self.model]
        if provider == "gemini":
            self._models += [m for m in GEMINI_FALLBACK_MODELS if m != self.model]
        self._unavailable_until: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self.provider in ("anthropic", "gemini", "openai", "ollama")

    def describe(self) -> str:
        return f"{self.provider}:{self.model}" if self.enabled else "none"

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        """Appel au LLM, avec 2 nouvelles tentatives sur les erreurs temporaires (429/5xx)."""
        if not self.enabled:
            raise LLMError("Aucun LLM configuré")
        for attempt, delay in enumerate((*RETRY_DELAYS_S, None)):
            try:
                return self._complete_once(system, user, max_tokens)
            except LLMError as exc:
                if delay is None or exc.status not in RETRYABLE_STATUS or exc.daily_quota:
                    raise
                wait = min(exc.retry_after or delay, MAX_RETRY_AFTER_S)
                log.info("LLM %s indisponible (HTTP %s), nouvel essai dans %ss", self.provider, exc.status, wait)
                time.sleep(wait)
        raise AssertionError("inaccessible")

    def _complete_once(self, system: str, user: str, max_tokens: int) -> str:
        """Essaie les modèles disponibles dans l'ordre. Un modèle retiré (404) ou dont le quota du jour est
        épuisé est mis de côté et on passe au suivant (Gemini uniquement)."""
        now = time.monotonic()
        candidates = [m for m in self._models if self._unavailable_until.get(m, 0) <= now]
        if not candidates:
            raise LLMError(f"{self.provider} : quota du jour épuisé pour tous les modèles ({', '.join(self._models)})",
                           429, daily_quota=True)
        last: LLMError | None = None
        for model in candidates:
            self.model = model
            try:
                return self._call(system, user, max_tokens)
            except LLMError as exc:
                if self.provider != "gemini" or not (exc.status == 404 or exc.daily_quota):
                    raise
                pause = 30 * 24 * 3600 if exc.status == 404 else EXHAUSTED_PAUSE_S
                self._unavailable_until[model] = time.monotonic() + pause
                log.warning("Modèle %s indisponible (%s), essai du suivant", model,
                            "retiré" if exc.status == 404 else "quota du jour épuisé")
                last = exc
        raise last

    def _call(self, system: str, user: str, max_tokens: int) -> str:
        try:
            if self.provider == "anthropic":
                return self._anthropic(system, user, max_tokens)
            return self._openai_compatible(system, user, max_tokens)
        except httpx.HTTPError as exc:
            raise LLMError(f"Erreur d'appel au LLM ({self.provider}) : {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:  # réponse JSON inattendue
            raise LLMError(f"Réponse inattendue du LLM ({self.provider}) : {exc!r}") from exc

    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        base = self.settings.llm_base_url or "https://api.anthropic.com"
        r = httpx.post(
            f"{base.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": self.settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.settings.llm_timeout,
        )
        if r.status_code >= 400:
            raise LLMError(f"Anthropic HTTP {r.status_code}: {_error_message(r)}", r.status_code, _retry_after(r))
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()

    def _openai_compatible(self, system: str, user: str, max_tokens: int) -> str:
        if self.provider == "ollama":
            base = self.settings.llm_base_url or "http://localhost:11434/v1"
            headers = {}
        elif self.provider == "gemini":
            base = self.settings.llm_base_url or GEMINI_BASE_URL
            headers = {"Authorization": f"Bearer {self.settings.gemini_api_key}"}
            max_tokens += GEMINI_THINKING_MARGIN
        else:
            base = self.settings.llm_base_url or "https://api.openai.com/v1"
            headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        r = httpx.post(
            f"{base.rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=self.settings.llm_timeout,
        )
        if r.status_code >= 400:
            raise LLMError(f"{self.provider} HTTP {r.status_code}: {_error_message(r)}", r.status_code,
                           _retry_after(r), daily_quota=_is_daily_quota(r))
        choice = r.json()["choices"][0]
        content = (choice.get("message") or {}).get("content") or ""
        if not content.strip():
            raise LLMError(f"{self.provider} : réponse vide (finish_reason={choice.get('finish_reason')})")
        return content.strip()

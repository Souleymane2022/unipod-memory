"""Client LLM optionnel. Aucun fournisseur n'est obligatoire : sans clé, le chatbot
fonctionne en mode extractif (il cite directement les passages trouvés).

Fournisseurs :
- anthropic : ANTHROPIC_API_KEY
- openai    : OPENAI_API_KEY (+ LLM_BASE_URL pour tout serveur compatible : Groq, Mistral, OpenRouter…)
- ollama    : modèle local gratuit (LLM_BASE_URL par défaut http://localhost:11434/v1)
"""
from __future__ import annotations

import logging

import httpx

from .config import Settings

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1",
}


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        provider = settings.llm_provider
        if provider == "auto":
            if settings.anthropic_api_key:
                provider = "anthropic"
            elif settings.openai_api_key:
                provider = "openai"
            else:
                provider = "none"
        self.provider = provider
        self.model = settings.llm_model or DEFAULT_MODELS.get(provider, "")

    @property
    def enabled(self) -> bool:
        return self.provider in ("anthropic", "openai", "ollama")

    def describe(self) -> str:
        return f"{self.provider}:{self.model}" if self.enabled else "none"

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        if not self.enabled:
            raise LLMError("Aucun LLM configuré")
        try:
            if self.provider == "anthropic":
                return self._anthropic(system, user, max_tokens)
            return self._openai_compatible(system, user, max_tokens)
        except httpx.HTTPError as exc:
            raise LLMError(f"Erreur d'appel au LLM ({self.provider}) : {exc}") from exc

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
            raise LLMError(f"Anthropic HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()

    def _openai_compatible(self, system: str, user: str, max_tokens: int) -> str:
        if self.provider == "ollama":
            base = self.settings.llm_base_url or "http://localhost:11434/v1"
            headers = {}
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
            raise LLMError(f"{self.provider} HTTP {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"].strip()

"""Fournisseur Gemini (API compatible OpenAI de Google) — appels HTTP simulés."""
import httpx
import pytest

from backend.app.config import Settings
from backend.app.llm import GEMINI_BASE_URL, LLMClient, LLMError
from backend.app.translate import Translator


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    """Les nouveaux essais sur 429/503 attendent quelques secondes : inutile dans les tests."""
    import backend.app.llm as llm_mod
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)


def _settings(**kw):
    s = Settings()
    s.llm_provider, s.anthropic_api_key, s.openai_api_key, s.gemini_api_key, s.llm_base_url, s.llm_model = \
        "auto", "", "", "", "", ""
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _fake_post(calls, content="Réponse [1].", status=200, finish="stop"):
    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        body = {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}]} \
            if status == 200 else [{"error": {"code": status, "message": "Resource has been exhausted"}}]
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))
    return post


def test_auto_detects_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-key")
    assert Settings().gemini_api_key == "g-key"
    llm = LLMClient(_settings(gemini_api_key="g-key"))
    assert llm.provider == "gemini" and llm.model == "gemini-3.6-flash" and llm.enabled
    # Anthropic reste prioritaire si les deux clés sont présentes
    assert LLMClient(_settings(gemini_api_key="g", anthropic_api_key="a")).provider == "anthropic"


def test_gemini_request_format(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", _fake_post(calls))
    llm = LLMClient(_settings(llm_provider="gemini", gemini_api_key="g-key", llm_model="gemini-2.5-pro"))
    assert llm.complete("système", "question", max_tokens=800) == "Réponse [1]."
    c = calls[0]
    assert c["url"] == f"{GEMINI_BASE_URL}/chat/completions"
    assert c["headers"]["Authorization"] == "Bearer g-key"
    assert c["json"]["model"] == "gemini-2.5-pro"
    assert c["json"]["max_tokens"] > 800  # marge pour la « réflexion » des modèles récents
    assert c["json"]["messages"][0] == {"role": "system", "content": "système"}


@pytest.mark.parametrize("content,status", [(None, 200), ("", 200), ("x", 429)])
def test_gemini_empty_or_error_raises_llm_error(monkeypatch, content, status):
    monkeypatch.setattr(httpx, "post", _fake_post([], content=content, status=status, finish="length"))
    with pytest.raises(LLMError):
        LLMClient(_settings(llm_provider="gemini", gemini_api_key="k")).complete("s", "u")


def test_translation_uses_gemini_when_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", _fake_post(calls, content="The deadline is October 10."))
    s = _settings(gemini_api_key="k")
    s.translation_provider = "auto"
    t = Translator(s, LLMClient(s))
    assert t.provider == "llm"
    assert t.translate("La date limite est le 10 octobre.", "fr", "en") == "The deadline is October 10."
    assert "English" in calls[0]["json"]["messages"][0]["content"]


def test_error_message_is_readable(monkeypatch):
    monkeypatch.setattr(httpx, "post", _fake_post([], status=429))
    with pytest.raises(LLMError, match=r"gemini HTTP 429: Resource has been exhausted$"):
        LLMClient(_settings(llm_provider="gemini", gemini_api_key="k")).complete("s", "u")


def test_retry_on_overload_then_success(monkeypatch):
    calls, statuses = [], iter([503, 429, 200])

    def post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        st = next(statuses)
        body = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]} if st == 200 \
            else [{"error": {"code": st, "message": "busy"}}]
        return httpx.Response(st, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert LLMClient(_settings(llm_provider="gemini", gemini_api_key="k")).complete("s", "u") == "OK"
    assert len(calls) == 3


def test_no_retry_on_invalid_key(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", _fake_post(calls, status=400))
    with pytest.raises(LLMError):
        LLMClient(_settings(llm_provider="gemini", gemini_api_key="k")).complete("s", "u")
    assert len(calls) == 1


def test_llm_translation_is_batched_in_one_call(monkeypatch):
    import json as _json
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        items = _json.loads(json["messages"][1]["content"])
        content = _json.dumps([f"EN:{x}" for x in items])
        return httpx.Response(200, json={"choices": [{"message": {"content": f"```json\n{content}\n```"}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    s = _settings(gemini_api_key="k")
    s.translation_provider = "auto"
    t = Translator(s, LLMClient(s))
    out = t.translate_many(["un", "deux", "trois", "four"], ["fr", "fr", "fr", "en"], "en")
    assert out == ["EN:un", "EN:deux", "EN:trois", "four"]
    assert len(calls) == 1
    assert t.translate_many(["un"], ["fr"], "en") == ["EN:un"] and len(calls) == 1  # cache


def test_retired_gemini_model_falls_back_to_default(monkeypatch):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json["model"])
        if json["model"] == "gemini-2.5-flash":
            return httpx.Response(404, json=[{"error": {"code": 404, "message": "no longer available to new users"}}],
                                  request=httpx.Request("POST", url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    llm = LLMClient(_settings(llm_provider="gemini", gemini_api_key="k", llm_model="gemini-2.5-flash"))
    assert llm.complete("s", "u") == "OK"
    assert calls == ["gemini-2.5-flash", "gemini-3.6-flash"] and llm.model == "gemini-3.6-flash"
    assert llm.complete("s", "u") == "OK" and calls[-1] == "gemini-3.6-flash"  # la bascule est retenue

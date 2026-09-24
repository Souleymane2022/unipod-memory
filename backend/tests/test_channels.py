"""Webhooks WhatsApp (Meta Cloud API) et Telegram, avec des requêtes au format officiel (API simulées)."""
import hashlib
import hmac
import json

import httpx
import pytest

from backend.app import channels, main

SECRET = "app-secret"


@pytest.fixture
def wa(client, monkeypatch):
    s = main.services().settings
    for k, v in {"whatsapp_token": "wa-token", "whatsapp_phone_number_id": "1234567890",
                 "whatsapp_verify_token": "verif-123", "whatsapp_app_secret": SECRET,
                 "whatsapp_allowed_numbers": set(), "whatsapp_api_version": "v23.0"}.items():
        monkeypatch.setattr(s, k, v)
    sent = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append({"url": url, "json": json, "headers": headers})
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(channels.httpx, "post", fake_post)
    monkeypatch.setattr(channels, "_seen", channels._Seen())
    return sent


def _wa_payload(text=None, msg_id="wamid.1", sender="23566000000", mtype="text"):
    m = {"from": sender, "id": msg_id, "timestamp": "1790000000", "type": mtype}
    if mtype == "text":
        m["text"] = {"body": text}
    else:
        m[mtype] = {"id": "media-1", "mime_type": "audio/ogg"}
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "15550000000", "phone_number_id": "1234567890"},
        "contacts": [{"profile": {"name": "Awa"}, "wa_id": sender}], "messages": [m]}}]}]}


def _post_wa(client, payload, secret=SECRET):
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/whatsapp/webhook", content=raw,
                       headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})


def test_whatsapp_verification(client, wa):
    ok = client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verif-123",
                                                     "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "faux",
                                                      "hub.challenge": "x"})
    assert bad.status_code == 403


def test_whatsapp_question_gets_cited_answer(client, wa):
    r = _post_wa(client, _wa_payload("Quelle est la date limite de dépôt des projets pour le hackathon ?"))
    assert r.status_code == 200
    assert len(wa) == 1
    out = wa[0]
    assert out["url"] == "https://graph.facebook.com/v23.0/1234567890/messages"
    assert out["headers"]["Authorization"] == "Bearer wa-token"
    assert out["json"]["to"] == "23566000000" and out["json"]["context"] == {"message_id": "wamid.1"}
    body = out["json"]["text"]["body"]
    assert "10 octobre 2026" in body and "chat_general_septembre.txt" in body and len(body) <= 4096


def test_whatsapp_rejects_bad_signature_and_ignores_duplicates(client, wa):
    assert _post_wa(client, _wa_payload("Bonjour"), secret="faux").status_code == 401
    assert wa == []
    payload = _wa_payload("Comment réserver une machine du fablab ?", msg_id="wamid.dup")
    _post_wa(client, payload)
    _post_wa(client, payload)  # Meta renvoie le même message
    assert len(wa) == 1 and "48 heures" in wa[0]["json"]["text"]["body"]


def test_whatsapp_commands_and_non_text(client, wa):
    _post_wa(client, _wa_payload("aide", msg_id="m1"))
    assert "UniPods Memory" in wa[-1]["json"]["text"]["body"]
    _post_wa(client, _wa_payload("documents", msg_id="m2"))
    assert "guide_fablab_unipod.md" in wa[-1]["json"]["text"]["body"]
    _post_wa(client, _wa_payload("résumé reunion mensuelle", msg_id="m3"))  # nom partiel
    body = wa[-1]["json"]["text"]["body"]
    assert "reunion_mensuelle_2026-09-15.txt" in body and "Moussa Kane" in body
    _post_wa(client, _wa_payload(mtype="audio", msg_id="m4"))
    assert "texte" in wa[-1]["json"]["text"]["body"]
    _post_wa(client, _wa_payload("Hello", msg_id="m5"))
    assert "I'm" in wa[-1]["json"]["text"]["body"] or "Hello" in wa[-1]["json"]["text"]["body"]


def test_whatsapp_allowed_numbers(client, wa, monkeypatch):
    monkeypatch.setattr(main.services().settings, "whatsapp_allowed_numbers", {"23566111111"})
    _post_wa(client, _wa_payload("aide", sender="23566000000", msg_id="x1"))
    assert wa == []
    _post_wa(client, _wa_payload("aide", sender="23566111111", msg_id="x2"))
    assert len(wa) == 1


def test_whatsapp_bold_markdown_converted():
    from backend.app.messaging import to_channel_format
    assert to_channel_format("Le **10 octobre** [1]", "whatsapp") == "Le *10 octobre* [1]"
    assert to_channel_format("Le **10 octobre** [1]", "telegram") == "Le 10 octobre [1]"


@pytest.fixture
def tg(client, monkeypatch):
    s = main.services().settings
    monkeypatch.setattr(s, "telegram_bot_token", "123:ABC")
    monkeypatch.setattr(s, "telegram_webhook_secret", "tg-secret")
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json})
        return httpx.Response(200, json={"ok": True, "result": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(channels.httpx, "post", fake_post)
    monkeypatch.setattr(channels, "_seen", channels._Seen())
    return calls


def test_telegram_webhook(client, tg):
    update = {"update_id": 10, "message": {"message_id": 5, "chat": {"id": 42, "type": "private"},
                                           "from": {"id": 42, "first_name": "Awa"}, "text": "Quels sont les prix du hackathon ?"}}
    assert client.post("/api/telegram/webhook", json=update).status_code == 401  # secret manquant
    r = client.post("/api/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret"})
    assert r.status_code == 200
    assert tg[-1]["url"] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert tg[-1]["json"]["chat_id"] == 42 and "1 500 000 FCFA" in tg[-1]["json"]["text"]


def test_telegram_setup(client, tg):
    assert client.get("/api/telegram/setup", params={"key": "faux"}).status_code == 403
    r = client.get("/api/telegram/setup", params={"key": "tg-secret"}).json()
    assert r["webhook"].startswith("https://") and r["webhook"].endswith("/api/telegram/webhook")
    assert tg[-1]["url"].endswith("/setWebhook") and tg[-1]["json"]["secret_token"] == "tg-secret"


def test_webhook_never_fails_on_network_or_engine_errors(client, wa, monkeypatch):
    def down(url, json=None, headers=None, timeout=None):
        raise httpx.ConnectError("réseau coupé")

    monkeypatch.setattr(channels.httpx, "post", down)
    assert _post_wa(client, _wa_payload("aide", msg_id="n1")).status_code == 200

    sent = []
    monkeypatch.setattr(channels.httpx, "post", lambda url, json=None, headers=None, timeout=None: sent.append(json)
                        or httpx.Response(200, json={}, request=httpx.Request("POST", url)))

    def boom(*a, **k):
        raise RuntimeError("panne du moteur")

    monkeypatch.setattr(channels, "reply", boom)
    assert _post_wa(client, _wa_payload("question", msg_id="n2")).status_code == 200
    assert "Désolé" in sent[-1]["text"]["body"]


def test_legal_pages_for_meta(client, monkeypatch):
    for path in ("/privacy", "/confidentialite", "/data-deletion", "/suppression-donnees"):
        r = client.get(path)
        assert r.status_code == 200 and "UniPods Memory" in r.text and "{{" not in r.text
    assert "Privacy Policy" in client.get("/privacy").text and "Politique de confidentialité" in client.get("/privacy").text
    monkeypatch.setattr(main.services().settings, "contact_email", "contact@example.org")
    monkeypatch.setattr("backend.app.main.get_settings", lambda: main.services().settings)
    assert 'mailto:contact@example.org' in client.get("/data-deletion").text


def test_health_reports_whatsapp_config_without_secrets(client, wa):
    h = client.get("/api/health").json()["whatsapp_config"]
    assert h == {"WHATSAPP_TOKEN": True, "WHATSAPP_PHONE_NUMBER_ID": True, "WHATSAPP_APP_SECRET": True,
                 "WHATSAPP_VERIFY_TOKEN_length": len("verif-123"), "WHATSAPP_ALLOWED_NUMBERS_count": 0}
    assert "verif-123" not in client.get("/api/health").text and "wa-token" not in client.get("/api/health").text


def test_recent_events_in_health(client, wa, monkeypatch):
    channels.RECENT_EVENTS.clear()
    if main.services().store.kind == "postgres":  # journal persistant : repartir d'une table vide
        with main.services().store._conn() as conn:
            conn.execute(f"DELETE FROM {main.services().store.table}_events")
    monkeypatch.setattr(main.services().settings, "whatsapp_allowed_numbers", {"23566111111"})
    _post_wa(client, _wa_payload("aide", sender="23566000000", msg_id="e1"))
    _post_wa(client, _wa_payload("aide", sender="23566111111", msg_id="e2"))
    events = client.get("/api/health").json()["recent_messages"]
    statuses = [e["status"] for e in events]
    assert statuses[:3] == ["réponse_envoyée", "message_reçu", "numéro_non_autorisé"]
    assert events[2]["from"] == "…0000" and "23566000000" not in json.dumps(events)


def test_events_persist_across_instances_with_postgres(client, wa, monkeypatch):
    store = main.services().store
    if store.kind != "postgres":
        pytest.skip("journal persistant : propre au stockage PostgreSQL")
    _post_wa(client, _wa_payload("aide", msg_id="persist-1"))
    channels.RECENT_EVENTS.clear()  # autre instance serverless : mémoire vide
    events = client.get("/api/health").json()["recent_messages"]
    assert events and events[0]["status"] == "réponse_envoyée"

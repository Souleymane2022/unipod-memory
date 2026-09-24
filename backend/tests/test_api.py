import io

from pypdf import PdfWriter


def test_health(client):
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and r["chunks"] >= 3


def test_ping_and_health_error_reporting(client, monkeypatch):
    from backend.app import main

    assert client.get("/api/ping").json() == {"status": "ok"}

    def broken():
        raise RuntimeError("disque en lecture seule")

    monkeypatch.setattr(main, "services", broken)
    r = client.get("/api/health")
    assert r.status_code == 503 and "disque en lecture seule" in r.json()["detail"]


def test_concurrent_first_requests_share_one_instance(client):
    """Régression : deux requêtes simultanées au démarrage créaient deux clients Chroma (KeyError)."""
    from concurrent.futures import ThreadPoolExecutor

    from backend.app import main

    main.services.cache_clear()
    with ThreadPoolExecutor(8) as pool:
        instances = list(pool.map(lambda _: main.services(), range(8)))
    assert all(i is instances[0] for i in instances)
    assert client.get("/api/health").status_code == 200


def test_documents_listed_with_metadata(client):
    docs = {d["source"]: d for d in client.get("/api/documents").json()["documents"]}
    assert docs["chat_general_septembre.txt"]["doc_type"] == "chat"
    assert docs["reunion_mensuelle_2026-09-15.txt"]["date_start"] == "2026-09-15"
    assert "Awa Diallo" in docs["chat_general_septembre.txt"]["authors"]


def _ask(client, q):
    r = client.post("/api/ask", json={"question": q})
    assert r.status_code == 200
    return r.json()


def test_answer_from_chat_with_citation(client):
    r = _ask(client, "Quelle est la date limite de dépôt des projets pour le hackathon ?")
    assert r["found"]
    assert "10 octobre 2026" in r["answer"]
    src = r["sources"][0]
    assert src["source"] == "chat_general_septembre.txt"
    assert src["cited_author"] == "Awa Diallo" and src["cited_date"] == "2026-09-08" and src["timestamp"] == "09:30"
    assert "10 octobre" in src["excerpt"]


def test_answer_from_meeting_transcript(client):
    r = _ask(client, "Qu'est-ce qui a été décidé pour l'imprimante 3D pendant la réunion ?")
    assert r["found"]
    assert "deux heures" in r["answer"]
    src = r["sources"][0]
    assert src["source"] == "reunion_mensuelle_2026-09-15.txt"
    assert src["cited_date"] == "2026-09-15" and src["timestamp"] == "00:07:15" and src["cited_author"] == "Awa Diallo"


def test_answer_from_document(client):
    r = _ask(client, "Comment réserver une machine du fablab ?")
    assert r["found"]
    assert "48 heures" in r["answer"]
    assert r["sources"][0]["source"] == "guide_fablab_unipod.md"


def test_unknown_information_is_not_hallucinated(client):
    for q in ["Quel est le salaire du directeur de l'UniPod ?", "Qui a gagné la coupe du monde 2022 ?"]:
        r = _ask(client, q)
        assert not r["found"], q
        assert "pas trouvé" in r["answer"] and r["sources"] == []


def test_ingest_text_then_ask_and_delete(client):
    r = client.post("/api/ingest/text", json={
        "source": "annonce_cafe", "doc_type": "document", "author": "Brahim Saleh", "date": "2026-09-20",
        "text": "La machine à café de l'UniPod est installée au deuxième étage, à côté de la salle de conférence."})
    assert r.status_code == 200 and r.json()["chunks"] == 1
    a = _ask(client, "Où se trouve la machine à café ?")
    assert a["found"] and a["sources"][0]["source"] == "annonce_cafe.txt"
    assert a["sources"][0]["author"] == "Brahim Saleh"
    assert client.delete("/api/documents/annonce_cafe.txt").json()["deleted_chunks"] == 1
    assert not _ask(client, "Où se trouve la machine à café ?")["found"]


def test_ingest_pdf(client):
    # PDF minimal généré à la volée avec un texte simple
    content = b"BT /F1 12 Tf 72 720 Td (Le code de la porte du laboratoire est 4829.) Tj ET"
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    stream = DecodedStreamObject()
    stream.set_data(content)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    page[NameObject("/Contents")] = writer._add_object(stream)
    buf = io.BytesIO()
    writer.write(buf)
    r = client.post("/api/ingest", files=[("files", ("labo.pdf", buf.getvalue(), "application/pdf"))])
    assert r.status_code == 200, r.text
    a = _ask(client, "Quel est le code de la porte du laboratoire ?")
    assert a["found"] and "4829" in a["answer"] and a["sources"][0]["source"] == "labo.pdf"


def test_reject_unsupported_file(client):
    r = client.post("/api/ingest", files=[("files", ("image.png", b"\x89PNG", "image/png"))])
    assert r.status_code == 400


def test_summarize_meeting(client):
    r = client.post("/api/summarize", json={"source": "reunion_mensuelle_2026-09-15.txt"}).json()
    assert r["summary"]
    assert len(r["decisions"]) >= 3
    owners = {t["owner"] for t in r["tasks"]}
    assert {"Moussa Kane", "Dr. Hassan Ali", "Fatimé Abakar"} <= owners
    assert any(t["deadline"] == "avant le 30 septembre" for t in r["tasks"])


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "UniPods Memory" in r.text

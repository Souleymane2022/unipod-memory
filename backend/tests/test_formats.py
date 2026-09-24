"""Formats bureautiques (docx, odt, html) et messages d'erreur d'ingestion bilingues."""
import io
import zipfile


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    return _zip({
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "word/document.xml": '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                             f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>',
    })


def make_odt(paragraphs: list[str]) -> bytes:
    body = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    return _zip({
        "mimetype": "application/vnd.oasis.opendocument.text",
        "content.xml": '<?xml version="1.0"?><office:document-content '
                       'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                       f"<office:body><office:text>{body}</office:text></office:body></office:document-content>",
    })


def _ingest(client, name, data, lang=None):
    extra = {"lang": lang} if lang else {}
    return client.post("/api/ingest", files=[("files", (name, data, "application/octet-stream"))], data=extra)


def _ask(client, q):
    return client.post("/api/ask", json={"question": q}).json()


def test_docx_ingest_and_ask(client):
    r = _ingest(client, "Compte rendu bureau.DOCX", make_docx([
        "Titre : Compte rendu du bureau", "Date : 2026-09-22",
        "Le bureau a décidé que la cérémonie de clôture du hackathon aura lieu au Palais des Congrès.",
    ]))
    assert r.status_code == 200, r.text
    assert r.json()["ingested"][0]["date"] == "2026-09-22"
    a = _ask(client, "Où aura lieu la cérémonie de clôture ?")
    assert a["found"] and "Palais des Congrès" in a["answer"]
    assert a["sources"][0]["source"] == "Compte rendu bureau.DOCX"


def test_odt_and_html_ingest(client):
    assert _ingest(client, "note.odt", make_odt(["Le mot de passe de l'imprimante laser est Kalahari7."])).status_code == 200
    html = b"<html><head><style>p{}</style><script>var x=1;</script></head><body><h1>Parking</h1>" \
           b"<p>Le parking des v\xc3\xa9los se trouve derri\xc3\xa8re le b\xc3\xa2timent B.</p></body></html>"
    assert _ingest(client, "infos.html", html).status_code == 200
    assert "Kalahari7" in _ask(client, "Quel est le mot de passe de l'imprimante laser ?")["answer"]
    a = _ask(client, "Où se trouve le parking des vélos ?")
    assert a["found"] and "bâtiment B" in a["answer"] and "var x" not in a["answer"]


def test_ingest_errors_are_bilingual(client):
    r = _ingest(client, "photo.jpg", b"\xff\xd8", lang="en")
    assert r.status_code == 400 and "Unsupported format" in r.json()["detail"] and ".docx" in r.json()["detail"]
    r = _ingest(client, "photo.jpg", b"\xff\xd8", lang="fr")
    assert "Format non pris en charge" in r.json()["detail"]
    r = _ingest(client, "casse.docx", b"pas un zip", lang="en")
    assert r.status_code == 400 and "Could not read" in r.json()["detail"]

from backend.app.chunker import chunk_document
from backend.app.parsers import parse_document


def test_detect_chat_whatsapp_format():
    text = "08/09/2026 09:12 - Awa: Bonjour\n08/09/2026 09:13 - Moussa: Salut\nsuite du message"
    doc = parse_document("wa.txt", text)
    assert doc.doc_type == "chat"
    assert [u.author for u in doc.units] == ["Awa", "Moussa"]
    assert doc.units[0].date == "2026-09-08"
    assert doc.units[1].text.endswith("suite du message")


def test_transcript_header_metadata():
    text = "Titre : Point hebdo\nDate : 15/09/2026\n\n[00:00:10] Awa: Bonjour\n[00:01:00] Brahim: Ordre du jour"
    doc = parse_document("r.txt", text)
    assert doc.doc_type == "transcript"
    assert doc.title == "Point hebdo"
    assert doc.date == "2026-09-15"
    assert all(u.date == "2026-09-15" for u in doc.units)


def test_document_paragraphs_and_form_metadata():
    doc = parse_document("guide.md", "Premier paragraphe.\n\nSecond paragraphe.", author="Équipe", date="2026-01-02")
    assert doc.doc_type == "document"
    assert len(doc.units) == 2 and doc.units[0].author == "Équipe"


def test_chunks_between_300_and_500_words():
    para = " ".join(["mot"] * 90) + "."
    text = "\n\n".join([para] * 20)  # 1 800 mots
    chunks = chunk_document(parse_document("long.txt", text), 300, 500)
    assert len(chunks) >= 4
    assert all(300 <= c.word_count <= 500 for c in chunks), [c.word_count for c in chunks]


def test_very_long_paragraph_is_split():
    text = " ".join(f"mot{i}" for i in range(1300))
    chunks = chunk_document(parse_document("bloc.txt", text), 300, 500)
    assert all(c.word_count <= 500 for c in chunks)
    assert sum(c.word_count for c in chunks) == 1300

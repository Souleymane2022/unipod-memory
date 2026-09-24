"""Lecture des fichiers (txt, md, pdf) et découpage en unités avec métadonnées.

Formats reconnus automatiquement :
- chat       : ``[2026-09-08 09:12] Awa: message``  ou export WhatsApp ``08/09/2026 09:12 - Awa: message``
- transcript : ``[00:04:05] Awa: texte``  ou ``Awa (00:04): texte``
- document   : tout le reste (découpé par paragraphes)

Un en-tête optionnel en début de fichier (``Titre :``, ``Date :``, ``Auteur :``,
``Participants :``, ``Type :``) est lu comme métadonnées.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from pypdf import PdfReader

DOC_TYPES = ("chat", "transcript", "document")

HEADER_RE = re.compile(
    r"^\s*(titre|title|date|auteur|author|auteurs|participants|type)\s*:\s*(.+?)\s*$", re.IGNORECASE
)
CHAT_ISO_RE = re.compile(
    r"^\[?(\d{4}-\d{2}-\d{2})[ T,]+(\d{1,2}:\d{2})(?::\d{2})?\]?\s*[-–]?\s*([^:\[\]]{1,60}?):\s*(.*)$"
)
CHAT_DMY_RE = re.compile(
    r"^\[?(\d{1,2})/(\d{1,2})/(\d{2,4}),?\s+(\d{1,2}:\d{2})(?::\d{2})?\]?\s*[-–]?\s*([^:\[\]]{1,60}?):\s*(.*)$"
)
TRANSCRIPT_RE = re.compile(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*[-–]?\s*([^:\[\]]{1,60}?):\s*(.*)$")
TRANSCRIPT_ALT_RE = re.compile(r"^([^:\[\]()]{1,60}?)\s*\((\d{1,2}:\d{2}(?::\d{2})?)\)\s*:\s*(.*)$")


@dataclass
class Unit:
    """Plus petite unité de sens : un message, une intervention ou un paragraphe."""

    text: str
    author: str = ""
    date: str = ""


@dataclass
class ParsedDocument:
    source: str
    doc_type: str
    title: str = ""
    author: str = ""
    date: str = ""
    units: list[Unit] = field(default_factory=list)


def extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n\n".join(pages)
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _norm_year(y: str) -> str:
    return y if len(y) == 4 else f"20{y}"


def normalize_date(value: str) -> str:
    value = value.strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", value)
    if m:
        d, mo, y = m.groups()
        return f"{_norm_year(y)}-{int(mo):02d}-{int(d):02d}"
    return value


def _read_header(lines: list[str]) -> tuple[dict[str, str], int]:
    """Lit les lignes ``Clé : valeur`` en tête de fichier. Retourne (en-tête, index du corps)."""
    header: dict[str, str] = {}
    body_start = 0
    for idx, line in enumerate(lines[:15]):
        if not line.strip():
            continue
        m = HEADER_RE.match(line)
        if not m:
            break
        key = m.group(1).lower()
        key = {"title": "titre", "author": "auteur", "auteurs": "auteur"}.get(key, key)
        header[key] = m.group(2)
        body_start = idx + 1
    return header, body_start


def detect_type(lines: list[str]) -> str:
    content = [l for l in lines if l.strip()]
    if not content:
        return "document"
    chat = sum(1 for l in content if CHAT_ISO_RE.match(l) or CHAT_DMY_RE.match(l))
    tr = sum(1 for l in content if TRANSCRIPT_RE.match(l) or TRANSCRIPT_ALT_RE.match(l))
    if chat / len(content) >= 0.3:
        return "chat"
    if tr / len(content) >= 0.3:
        return "transcript"
    return "document"


def _speaker_units(lines: list[str], doc_type: str, default_date: str) -> list[Unit]:
    units: list[Unit] = []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        unit = None
        if m := CHAT_ISO_RE.match(line):
            date, time, author, msg = m.groups()
            unit = Unit(f"[{date} {time}] {author.strip()}: {msg}", author.strip(), date)
        elif m := CHAT_DMY_RE.match(line):
            d, mo, y, time, author, msg = m.groups()
            date = f"{_norm_year(y)}-{int(mo):02d}-{int(d):02d}"
            unit = Unit(f"[{date} {time}] {author.strip()}: {msg}", author.strip(), date)
        elif m := TRANSCRIPT_RE.match(line):
            ts, author, msg = m.groups()
            unit = Unit(f"[{ts}] {author.strip()}: {msg}", author.strip(), default_date)
        elif m := TRANSCRIPT_ALT_RE.match(line):
            author, ts, msg = m.groups()
            unit = Unit(f"[{ts}] {author.strip()}: {msg}", author.strip(), default_date)
        if unit:
            units.append(unit)
        elif units:  # ligne de continuation d'un message multi-lignes
            units[-1].text += " " + line.strip()
        else:
            units.append(Unit(line.strip(), "", default_date))
    return units


def _paragraph_units(lines: list[str], author: str, date: str) -> list[Unit]:
    paragraphs, buf = [], []
    for line in lines:
        if line.strip():
            buf.append(line.strip())
        elif buf:
            paragraphs.append(" ".join(buf))
            buf = []
    if buf:
        paragraphs.append(" ".join(buf))
    return [Unit(p, author, date) for p in paragraphs]


def parse_document(
    source: str,
    text: str,
    doc_type: str | None = None,
    author: str | None = None,
    date: str | None = None,
) -> ParsedDocument:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header, start = _read_header(lines)
    body = lines[start:]

    header_type = header.get("type", "").lower()
    if doc_type in DOC_TYPES:
        dtype = doc_type
    elif header_type in DOC_TYPES:
        dtype = header_type
    else:
        dtype = detect_type(body)

    doc_author = (author or header.get("auteur") or header.get("participants") or "").strip()
    doc_date = normalize_date(date or header.get("date") or "")

    if dtype in ("chat", "transcript"):
        units = _speaker_units(body, dtype, doc_date)
    else:
        units = _paragraph_units(body, doc_author, doc_date)

    if not doc_date:
        dates = sorted(u.date for u in units if u.date)
        doc_date = dates[0] if dates else ""

    return ParsedDocument(
        source=source,
        doc_type=dtype,
        title=header.get("titre", ""),
        author=doc_author,
        date=doc_date,
        units=units,
    )

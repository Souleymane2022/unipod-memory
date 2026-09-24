"""Découpage en chunks de ~300-500 mots, en respectant les frontières de messages/paragraphes."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .parsers import ParsedDocument, Unit
from .textutils import split_sentences, word_count


@dataclass
class Chunk:
    text: str
    index: int
    authors: list[str]
    date_start: str
    date_end: str
    word_count: int


def _split_long_unit(unit: Unit, max_words: int) -> list[Unit]:
    """Découpe une unité trop longue par phrases puis, si besoin, par mots."""
    pieces: list[Unit] = []
    buf: list[str] = []
    for sent in split_sentences(unit.text):
        words = sent.split()
        while len(words) > max_words:
            if buf:
                pieces.append(Unit(" ".join(buf), unit.author, unit.date))
                buf = []
            pieces.append(Unit(" ".join(words[:max_words]), unit.author, unit.date))
            words = words[max_words:]
        if len(buf) + len(words) > max_words and buf:
            pieces.append(Unit(" ".join(buf), unit.author, unit.date))
            buf = []
        buf.extend(words)
    if buf:
        pieces.append(Unit(" ".join(buf), unit.author, unit.date))
    return pieces


def _make_chunk(units: list[Unit], index: int, doc: ParsedDocument) -> Chunk:
    sep = "\n" if doc.doc_type in ("chat", "transcript") else "\n\n"
    text = sep.join(u.text for u in units)
    authors: list[str] = []
    for u in units:
        if u.author and u.author not in authors:
            authors.append(u.author)
    if not authors and doc.author:
        authors = [doc.author]
    dates = sorted(u.date for u in units if u.date) or ([doc.date] if doc.date else [""])
    return Chunk(text, index, authors, dates[0], dates[-1], word_count(text))


def chunk_document(doc: ParsedDocument, min_words: int = 300, max_words: int = 500) -> list[Chunk]:
    """Regroupe les unités en chunks de taille équilibrée entre ``min_words`` et ``max_words``.

    On calcule le nombre de chunks nécessaire puis une taille cible égale pour chacun, ce qui évite
    un dernier chunk minuscule. Seul un document de moins de ``min_words`` mots donne un chunk plus court.
    """
    units: list[Unit] = []
    for u in doc.units:
        units.extend(_split_long_unit(u, max_words) if word_count(u.text) > max_words else [u])
    if not units:
        return []

    total = sum(word_count(u.text) for u in units)
    n_chunks = max(1, math.ceil(total / max_words))
    target = max(min_words, total / n_chunks)

    groups: list[list[Unit]] = []
    current: list[Unit] = []
    size = 0
    for u in units:
        n = word_count(u.text)
        remaining_groups = n_chunks - len(groups) - 1
        if current and remaining_groups > 0 and (size + n > max_words or size + n / 2 > target):
            groups.append(current)
            current, size = [], 0
        current.append(u)
        size += n
    if current:
        groups.append(current)

    # Sécurité : si le dernier groupe dépasse le max (unités très inégales), on le redécoupe.
    final: list[list[Unit]] = []
    for g in groups:
        buf, sz = [], 0
        for u in g:
            n = word_count(u.text)
            if buf and sz + n > max_words:
                final.append(buf)
                buf, sz = [], 0
            buf.append(u)
            sz += n
        if buf:
            final.append(buf)

    return [_make_chunk(g, i, doc) for i, g in enumerate(final)]

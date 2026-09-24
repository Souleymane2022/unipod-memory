"""Petits outils texte : normalisation, mots-clés, découpage en phrases."""
from __future__ import annotations

import math
import re
import unicodedata

STOPWORDS = set(
    """
    a à au aux avec ce ces cet cette dans de des du elle elles en et eux il ils je la le les leur leurs lui ma mais me
    même mes moi mon ne nos notre nous on ou où par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un une
    vos votre vous y c d j l m n s t est sont été être avoir ai as avons avez ont était sera seront fait faire faut
    quel quelle quels quelles quoi comment quand combien pourquoi est-ce ceci cela ça tout tous toute toutes très plus
    moins aussi alors donc si non oui peut peuvent doit doivent il-y-a y-a-t-il qu'est-ce exactement bien
    the of and to in is are was were be for on at by with what when who how which where why do does did a an it this that
    """.split()
)

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)
_SENT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Ý0-9«\"(\[])")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


_SUFFIXES = (
    "issements", "issement", "ations", "ation", "ements", "ement", "ments", "ment", "euses", "euse",
    "ances", "ance", "ences", "ence", "ités", "ité", "ions", "ion", "ées", "ée", "és",
    "ers", "er", "ez", "é", "es", "e", "s", "x",
)


def stem(word: str) -> str:
    """Racinisation légère du français (suppression de suffixes courants)."""
    w = word.lower().strip("'")
    if "'" in w:  # l'imprimante -> imprimante
        w = w.split("'")[-1]
    w = strip_accents(w)
    for suf in (strip_accents(x) for x in _SUFFIXES):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def keywords(text: str) -> set[str]:
    out = set()
    for tok in _WORD_RE.findall(text.lower()):
        tok = tok.split("'")[-1]
        if len(tok) < 2 or tok in STOPWORDS or strip_accents(tok) in STOPWORDS:
            continue
        out.add(stem(tok))
    return out


def idf_weights(query_kws: set[str], texts: list[str]) -> dict[str, float]:
    """IDF (formule BM25) des mots-clés de la question, calculée sur les textes candidats.

    Un mot présent partout (ex. « UniPod ») pèse presque zéro ; un mot rare pèse beaucoup.
    """
    n = len(texts)
    text_kws = [keywords(t) for t in texts]
    return {k: math.log(1 + (n - df + 0.5) / (df + 0.5))
            for k in query_kws for df in [sum(1 for tk in text_kws if k in tk)]}


def lexical_overlap(query: str, text: str, idf: dict[str, float] | None = None) -> float:
    """Part (pondérée par l'IDF si fournie) des mots-clés de la question présents dans le texte (0..1)."""
    q = keywords(query)
    if not q:
        return 0.0
    t = keywords(text)
    if not idf:
        return len(q & t) / len(q)
    total = sum(idf.get(k, 1.0) for k in q) or 1.0
    return sum(idf.get(k, 1.0) for k in q & t) / total


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Une ligne de chat/transcription = une unité ; sinon on découpe en phrases.
        if re.match(r"^\[[^\]]+\]\s*[^:]{1,60}:", line):
            out.append(line)
        else:
            out.extend(s.strip() for s in _SENT_RE.split(line) if s.strip())
    return out


def word_count(text: str) -> int:
    return len(text.split())

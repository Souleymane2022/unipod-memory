"""Petits outils texte : normalisation, mots-clés, découpage en phrases."""
from __future__ import annotations

import math
import re
import unicodedata

from .i18n import translations

STOPWORDS = set(
    """
    a à au aux avec ce ces cet cette dans de des du elle elles en et eux il ils je la le les leur leurs lui ma mais me
    même mes moi mon ne nos notre nous on ou où par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un une
    vos votre vous y c d j l m n s t est sont été être avoir ai as avons avez ont était sera seront fait faire faut
    quel quelle quels quelles quoi comment quand combien pourquoi est-ce ceci cela ça tout tous toute toutes très plus
    moins aussi alors donc si non oui peut peuvent doit doivent il-y-a y-a-t-il qu'est-ce exactement bien
    the of and to in is are was were be for on at by with what when who how which where why do does did a an it this that
    i me my we our you your they them their he she his her its there here about during from into over under than then
    can could would should will shall may might have has had been being am any some all each every other such only
    also just so as if or but not no yes get got make made many much tell please know there's what's
    """.split()
)

# Mots qui décrivent le *type* de réponse attendue (« le montant de… », « the name of… ») et non le sujet :
# ils ne sont pas exigés dans les sources, sinon « Quel est le montant de la bourse ? » serait refusé
# alors que le document dit seulement « bourse de 500 000 FCFA ».
ANSWER_TYPE_WORDS = set(
    """
    montant somme nombre date dates heure heures moment lieu endroit nom noms personne raison façon manière
    type genre sorte liste détail détails information informations info infos chose choses savoir connaître
    trouver dire possible besoin exactement actuellement
    amount number date dates time hour hours place name names person reason way kind type sort list detail
    details information info thing things need find know tell exactly currently
    """.split()
) | set(
    # mots « méta » qui parlent du document plutôt que de son contenu
    """
    document documents doc docs fichier fichiers pdf texte dit disent parle parlent contient contiennent contenu
    indexé indexée indexer indexe lindexer viens vient venez ajouté ajoutée ajouter envoyé envoyée uploadé mis
    say says said talk talks contain contains content file files uploaded added indexed just
    """.split()
)

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)
_SENT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Ý0-9«\"(\[])")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


_SUFFIXES = (
    "issements", "issement", "ations", "ation", "ements", "ement", "ments", "ment", "euses", "euse",
    "ances", "ance", "ences", "ence", "ités", "ité", "ions", "ion", "ées", "ée", "és",
    "trices", "trice", "teurs", "teur",  # coordinateur / coordinatrice -> coordina
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


Concept = frozenset[str]


def query_concepts(query: str) -> list[Concept]:
    """Mots-clés de la question, chacun avec ses équivalents dans l'autre langue (FR <-> EN).

    Ex. « prizes » -> {priz, prix} : un texte français contenant « prix » couvre ce concept.
    """
    concepts: list[Concept] = []
    for tok in _WORD_RE.findall(query.lower()):
        tok = tok.split("'")[-1]
        if len(tok) < 2 or tok in STOPWORDS or strip_accents(tok) in STOPWORDS or tok in ANSWER_TYPE_WORDS:
            continue
        alts = {stem(tok)}
        for tr in translations(tok):
            alts.update(stem(w) for w in _WORD_RE.findall(tr.lower()) if w not in STOPWORDS)
        concept = frozenset(alts)
        if concept not in concepts:
            concepts.append(concept)
    return concepts


def concept_idf(concepts: list[Concept], texts: list[str]) -> list[float]:
    """IDF (formule BM25) de chaque concept, calculée sur les textes candidats.

    Un mot présent partout (ex. « UniPod ») pèse presque zéro ; un mot rare pèse beaucoup.
    """
    n = len(texts)
    text_kws = [keywords(t) for t in texts]
    return [math.log(1 + (n - df + 0.5) / (df + 0.5))
            for c in concepts for df in [sum(1 for tk in text_kws if c & tk)]]


def concept_overlap(concepts: list[Concept], text: str, weights: list[float] | None = None) -> float:
    """Part (pondérée par l'IDF si fournie) des concepts de la question présents dans le texte (0..1)."""
    if not concepts:
        return 0.0
    weights = weights or [1.0] * len(concepts)
    t = keywords(text)
    total = sum(weights) or 1.0
    return sum(w for c, w in zip(concepts, weights) if c & t) / total


def lexical_overlap(query: str, text: str) -> float:
    return concept_overlap(query_concepts(query), text)


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

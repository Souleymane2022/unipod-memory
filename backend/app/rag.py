"""Recherche hybride (vectorielle + lexicale) et génération de réponse citée."""
from __future__ import annotations

import logging
import math
import re
from typing import Any

from .config import Settings
from .llm import LLMClient, LLMError
from .store import VectorStore
from .textutils import idf_weights, keywords, lexical_overlap, split_sentences

log = logging.getLogger(__name__)

NOT_FOUND = (
    "Je n'ai pas trouvé cette information dans la mémoire du groupe "
    "(messages, transcriptions de réunions et documents indexés). "
    "Elle n'est donc pas disponible pour le moment : vous pouvez poser la question dans le groupe "
    "ou ajouter le document concerné."
)
NOT_FOUND_MARKER = "INFORMATION_NON_DISPONIBLE"

SYSTEM_PROMPT = f"""Tu es UniPods Memory, l'assistant de mémoire collective d'une communauté UniPod.
Tu réponds en français, de façon concise, UNIQUEMENT à partir des extraits numérotés fournis
(messages de groupe, transcriptions de réunions, documents).
Règles strictes :
- Chaque affirmation doit être suivie de la référence de l'extrait utilisé, au format [1], [2]...
- N'invente rien et n'utilise pas tes connaissances générales.
- Si les extraits ne contiennent pas la réponse, réponds exactement : {NOT_FOUND_MARKER}
- Si la réponse n'est que partielle, donne ce qui est trouvé et précise ce qui manque.
- Mentionne qui a dit quoi et quand lorsque c'est utile (auteur, date)."""

# Pondération du score hybride
W_VECTOR, W_LEXICAL = 0.55, 0.45
MIN_LEXICAL = 0.45
STRONG_SIMILARITY = 0.6


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class RAGEngine:
    def __init__(self, settings: Settings, store: VectorStore, llm: LLMClient):
        self.settings = settings
        self.store = store
        self.llm = llm

    # ------------------------------------------------------------------ recherche
    def retrieve(self, question: str, top_k: int | None = None) -> list[dict[str, Any]]:
        top_k = top_k or self.settings.top_k
        candidates = self.store.query(question, n=max(20, top_k * 5))
        idf = idf_weights(keywords(question), [h["text"] for h in candidates])
        for hit in candidates:
            lex = lexical_overlap(question, hit["text"], idf)
            hit["lexical"] = lex
            hit["score"] = W_VECTOR * max(hit["similarity"], 0.0) + W_LEXICAL * lex
        candidates.sort(key=lambda h: h["score"], reverse=True)
        return candidates[:top_k]

    def _is_relevant(self, hit: dict[str, Any], question: str) -> bool:
        # Il faut un score suffisant ET au moins un mot-clé significatif en commun
        # (ou une similarité sémantique très forte) : c'est notre garde-fou anti-hallucination.
        if hit["score"] < self.settings.min_relevance:
            return False
        # Couverture lexicale pondérée par l'IDF : les mots rares/spécifiques de la question
        # (ex. « salaire ») doivent être présents, sauf si la similarité sémantique est très forte.
        if keywords(question) and hit["lexical"] < MIN_LEXICAL and hit["similarity"] < STRONG_SIMILARITY:
            return False
        return True

    # ------------------------------------------------------------------ extraits
    def best_sentences(self, question: str, hits: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
        """Sélectionne les phrases/messages les plus pertinents dans les chunks retenus."""
        cands: list[dict[str, Any]] = []
        for ref, hit in enumerate(hits, start=1):
            for s in split_sentences(hit["text"]):
                if len(s.split()) < 3 or s.startswith("#"):
                    continue
                cands.append({"ref": ref, "text": s, "hit": hit})
        if not cands:
            return []
        vecs = self.store.embed([question] + [c["text"] for c in cands])
        qv = vecs[0]
        idf = idf_weights(keywords(question), [c["text"] for c in cands])
        for c, v in zip(cands, vecs[1:]):
            c["similarity"] = _cosine(qv, v)
            c["lexical"] = lexical_overlap(question, c["text"], idf)
            c["score"] = W_VECTOR * c["similarity"] + W_LEXICAL * c["lexical"] + 0.05 * c["hit"]["score"]
            if c["text"].rstrip().endswith("?"):  # une question n'est pas une réponse
                c["score"] *= 0.6
        cands.sort(key=lambda c: c["score"], reverse=True)
        best = cands[0]["score"]
        chosen = [c for c in cands if c["score"] >= best * 0.8 and c["lexical"] > 0]
        answers_only = [c for c in chosen if not c["text"].rstrip().endswith("?")]
        chosen = (answers_only or chosen)[:limit]
        return chosen or cands[:1]

    # ------------------------------------------------------------------ réponse
    @staticmethod
    def _citation(ref: int, hit: dict[str, Any], excerpt: str) -> dict[str, Any]:
        m = hit["metadata"]
        # Auteur / date / horodatage exacts de la phrase citée (message de chat ou intervention en réunion)
        cited_author, cited_date, timestamp = m.get("author", ""), m.get("date", ""), ""
        lm = re.match(r"^\[(?:(\d{4}-\d{2}-\d{2}) )?(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:]{1,60}):", excerpt)
        if lm:
            cited_date = lm.group(1) or cited_date
            timestamp, cited_author = lm.group(2), lm.group(3).strip()
        return {
            "cited_author": cited_author,
            "cited_date": cited_date,
            "timestamp": timestamp,
            "ref": ref,
            "source": m.get("source"),
            "title": m.get("title", ""),
            "doc_type": m.get("doc_type"),
            "author": m.get("author", ""),
            "date": m.get("date", ""),
            "date_end": m.get("date_end", ""),
            "chunk": f"{m.get('chunk_index', 0) + 1}/{m.get('n_chunks', 1)}",
            "excerpt": excerpt,
            "score": round(hit["score"], 3),
        }

    def answer(self, question: str, top_k: int | None = None) -> dict[str, Any]:
        question = question.strip()
        if not question:
            return {"question": question, "answer": "Merci de poser une question.", "found": False,
                    "sources": [], "mode": "none"}

        hits = [h for h in self.retrieve(question, top_k) if self._is_relevant(h, question)]
        if not hits:
            return {"question": question, "answer": NOT_FOUND, "found": False, "sources": [], "mode": "none"}

        sentences = self.best_sentences(question, hits)
        excerpt_by_ref: dict[int, list[str]] = {}
        for s in sentences:
            excerpt_by_ref.setdefault(s["ref"], []).append(s["text"])

        warning = None
        if self.llm.enabled:
            try:
                return self._answer_with_llm(question, hits, excerpt_by_ref)
            except LLMError as exc:
                log.warning("LLM indisponible, bascule en mode extractif : %s", exc)
                warning = str(exc)

        result = self._answer_extractive(question, hits, sentences, excerpt_by_ref)
        if warning:
            result["warning"] = warning
        return result

    def _answer_extractive(self, question, hits, sentences, excerpt_by_ref) -> dict[str, Any]:
        lines = ["Voici ce que dit la mémoire du groupe :"]
        used_refs: list[int] = []
        for s in sentences:
            meta = s["hit"]["metadata"]
            text = s["text"]
            ctx = []
            if not re.match(r"^\[[^\]]+\]", text):  # la ligne de chat contient déjà auteur + date
                if meta.get("author"):
                    ctx.append(meta["author"])
                if meta.get("date"):
                    ctx.append(meta["date"])
            suffix = f" ({', '.join(ctx)})" if ctx else ""
            lines.append(f"• « {text} »{suffix} [{s['ref']}]")
            if s["ref"] not in used_refs:
                used_refs.append(s["ref"])
        sources = [self._citation(r, hits[r - 1], " … ".join(excerpt_by_ref.get(r, []))) for r in used_refs]
        return {"question": question, "answer": "\n".join(lines), "found": True, "sources": sources,
                "mode": "extractive"}

    def _answer_with_llm(self, question, hits, excerpt_by_ref) -> dict[str, Any]:
        blocks = []
        for i, h in enumerate(hits, start=1):
            m = h["metadata"]
            blocks.append(
                f"[{i}] source={m.get('source')} | type={m.get('doc_type')} | auteur(s)={m.get('author') or 'inconnu'}"
                f" | date={m.get('date') or 'inconnue'}\n{h['text']}"
            )
        user = "Extraits :\n\n" + "\n\n---\n\n".join(blocks) + f"\n\nQuestion : {question}"
        text = self.llm.complete(SYSTEM_PROMPT, user)
        if NOT_FOUND_MARKER in text or not text:
            return {"question": question, "answer": NOT_FOUND, "found": False, "sources": [],
                    "mode": f"llm:{self.llm.describe()}"}
        refs = sorted({int(r) for r in re.findall(r"\[(\d+)\]", text) if 1 <= int(r) <= len(hits)})
        if not refs:  # pas de citation : on rattache au meilleur extrait plutôt que de répondre sans source
            refs = [1]
        sources = []
        for r in refs:
            h = hits[r - 1]
            excerpt = " … ".join(excerpt_by_ref.get(r, [])) or self.best_sentences(question, [h], 1)[0]["text"]
            sources.append(self._citation(r, h, excerpt))
        return {"question": question, "answer": text, "found": True, "sources": sources,
                "mode": f"llm:{self.llm.describe()}"}

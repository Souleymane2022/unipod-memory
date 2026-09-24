"""Bonus : résumé automatique d'une conversation/réunion + extraction des décisions et tâches.

Avec un LLM configuré, le résumé est rédigé par le modèle. Sans LLM, on utilise des
heuristiques transparentes (phrases centrales + motifs linguistiques de décisions/tâches).
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

from .i18n import DEFAULT_LANG, normalize_lang
from .llm import LLMClient, LLMError
from .textutils import keywords, split_sentences

log = logging.getLogger(__name__)

LINE_RE = re.compile(r"^\[(?P<when>[^\]]+)\]\s*(?P<who>[^:]{1,60}):\s*(?P<msg>.*)$")

DECISION_PATTERNS = re.compile(
    r"\b(c'est décidé|décidé|décision|on valide|validé|valide la|adopté|approuvé|convenu|retenu|"
    r"est confirmé|c'est confirmé|sont réservés|sera décidé)\b",
    re.IGNORECASE,
)
TASK_PATTERNS = re.compile(
    r"\b(je vais|je m'en occupe|je m'occupe|je peux animer|je publierai|vous vous chargez|tu peux t'en occuper|"
    r"se charge|est responsable|responsable de|à faire|todo|action\s*:|avant le|d'ici le)\b",
    re.IGNORECASE,
)
DEADLINE_RE = re.compile(
    r"\b(avant le|d'ici le|au plus tard|le)\s+((?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+)?"
    r"(\d{1,2}(?:er)?\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)"
    r"(?:\s+\d{4})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)",
    re.IGNORECASE,
)

SUMMARY_PROMPT = """Tu analyses une conversation de groupe ou une transcription de réunion d'une communauté UniPod.
Rédige tout le contenu en {language}.
Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour, de la forme :
{{"summary": "résumé en 3 à 6 phrases",
 "decisions": ["décision 1", "..."],
 "tasks": [{{"task": "...", "owner": "personne ou vide", "deadline": "échéance ou vide"}}]}}
N'invente rien : n'utilise que le contenu fourni."""


def _parse_lines(text: str) -> list[dict[str, str]]:
    out = []
    for s in split_sentences(text):
        m = LINE_RE.match(s)
        if m:
            out.append({"when": m["when"], "who": m["who"].strip(), "msg": m["msg"].strip()})
        else:
            out.append({"when": "", "who": "", "msg": s})
    return out


def _match_participant(name: str, participants: list[str]) -> str:
    name_l = name.lower().rstrip(".")
    for p in participants:
        if name_l == p.lower() or name_l in p.lower().split() or p.lower().startswith(name_l):
            return p
    return name


def _owner(item: dict[str, str], participants: list[str]) -> str:
    msg = item["msg"]
    # « Moussa, tu peux t'en occuper ? » / « Dr. Hassan, vous vous chargez… » -> la personne interpellée
    m = re.search(r"([A-ZÀ-Ý][\w.]*(?:\s[A-ZÀ-Ý][\w-]+)?),\s*(?:tu|vous)\b", msg)
    if m:
        return _match_participant(m.group(1), participants)
    m = re.search(r"\b([A-ZÀ-Ý][\w-]+(?:\s[A-ZÀ-Ý][\w-]+)?)\s+est responsable", msg)
    if m:
        return _match_participant(m.group(1), participants)
    return item["who"]


def _extractive(text: str, max_sentences: int = 5) -> dict[str, Any]:
    items = _parse_lines(text)
    participants = []
    for it in items:
        if it["who"] and it["who"] not in participants:
            participants.append(it["who"])

    # Résumé : phrases les plus « centrales » (mots-clés fréquents), remises dans l'ordre d'origine.
    freq = Counter(k for it in items for k in keywords(it["msg"]))
    scored = []
    for i, it in enumerate(items):
        kws = keywords(it["msg"])
        if len(kws) < 4:
            continue
        score = sum(freq[k] for k in kws) / (len(kws) ** 0.5)
        if DECISION_PATTERNS.search(it["msg"]):
            score *= 1.3
        scored.append((score, i))
    top = sorted(i for _, i in sorted(scored, reverse=True)[:max_sentences])
    summary = " ".join(
        (f"{items[i]['who']} : " if items[i]["who"] else "") + items[i]["msg"] for i in top
    )

    decisions, tasks = [], []
    for it in items:
        msg = it["msg"]
        who = f" ({it['who']})" if it["who"] else ""
        if DECISION_PATTERNS.search(msg) and "?" not in msg:
            decisions.append(msg + who)
        if len(msg.split()) < 6:  # « Oui, je m'en occupe. » : simple confirmation de la tâche précédente
            continue
        if (TASK_PATTERNS.search(msg) and not msg.endswith("?")) or re.search(r"(occuper|chargez).*\?$", msg):
            dl = DEADLINE_RE.search(msg)
            tasks.append({"task": msg, "owner": _owner(it, participants),
                          "deadline": dl.group(0) if dl else ""})
    return {"summary": summary, "decisions": decisions, "tasks": tasks, "participants": participants}


def summarize(text: str, llm: LLMClient, lang: str | None = None) -> dict[str, Any]:
    """Sans LLM, le résumé reprend des phrases de la source (donc dans sa langue d'origine)."""
    lang = normalize_lang(lang) or DEFAULT_LANG
    base = _extractive(text)
    if llm.enabled:
        try:
            language = "anglais (English)" if lang == "en" else "français"
            raw = llm.complete(SUMMARY_PROMPT.format(language=language), text[:60000], max_tokens=1500)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(m.group(0) if m else raw)
            return {
                "summary": data.get("summary", ""),
                "decisions": data.get("decisions", []),
                "tasks": data.get("tasks", []),
                "participants": base["participants"],
                "mode": f"llm:{llm.describe()}",
            }
        except (LLMError, ValueError, AttributeError) as exc:
            log.warning("Résumé LLM impossible, bascule extractive : %s", exc)
            base["warning"] = str(exc)
    base["mode"] = "extractive"
    return base

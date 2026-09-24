"""Bilinguisme français / anglais.

- Messages de l'API dans la langue de l'utilisateur (``fr`` ou ``en``).
- Détection simple de la langue d'un texte (mots-outils).
- Lexique anglais -> français pour la recherche inter-langues : une question en anglais
  peut retrouver des messages/documents en français (et inversement) sans modèle
  multilingue. Chaque mot de la question devient un « concept » dont les traductions
  comptent comme équivalentes lors du recouvrement lexical.
"""
from __future__ import annotations

import re

LANGS = ("fr", "en")
DEFAULT_LANG = "fr"

MESSAGES = {
    "fr": {
        "not_found": (
            "Je n'ai pas trouvé cette information dans la mémoire du groupe "
            "(messages, transcriptions de réunions et documents indexés). "
            "Elle n'est donc pas disponible pour le moment : vous pouvez poser la question dans le groupe "
            "ou ajouter le document concerné."
        ),
        "empty_question": "Merci de poser une question.",
        "intro": "Voici ce que dit la mémoire du groupe :",
        "original_language": "(citations dans leur langue d'origine)",
    },
    "en": {
        "not_found": (
            "I couldn't find this information in the group's memory "
            "(indexed messages, meeting transcripts and documents), so it isn't available yet. "
            "You can ask the question in the group or add the relevant document."
        ),
        "empty_question": "Please ask a question.",
        "intro": "Here is what the group's memory says:",
        "original_language": "(quotes are in their original language)",
    },
}


def normalize_lang(lang: str | None) -> str | None:
    if not lang:
        return None
    lang = lang.lower()[:2]
    return lang if lang in LANGS else None


def msg(key: str, lang: str) -> str:
    return MESSAGES.get(lang, MESSAGES[DEFAULT_LANG])[key]


_FR_MARKERS = set(
    "le la les un une des du de et est sont pour dans sur avec que qui quoi quel quelle quels quelles "
    "comment quand combien pourquoi où nous vous ils elles ce cette ces au aux pas été être avoir a".split()
)
_EN_MARKERS = set(
    "the a an and is are was were for in on with that which what who whom how when where why "
    "do does did we you they this these those of to be been have has can will".split()
)
_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ']+")


def detect_lang(text: str) -> str:
    tokens = [t.lower().split("'")[-1] for t in _TOKEN_RE.findall(text)]
    fr = sum(t in _FR_MARKERS for t in tokens) + sum(1 for c in text if c in "éèêàùçôîâ")
    en = sum(t in _EN_MARKERS for t in tokens)
    return "en" if en > fr else "fr"


# --------------------------------------------------------------------------- lexique EN -> FR
# Vocabulaire courant d'une communauté (réunions, événements, lieux, matériel, organisation).
# Les valeurs sont des mots français équivalents (formes simples ; la racinisation fait le reste).
EN_FR: dict[str, list[str]] = {
    # temps, dates, planning
    "deadline": ["limite", "échéance", "date"], "due": ["limite", "échéance"], "date": ["date"],
    "time": ["heure", "horaire", "temps"], "hour": ["heure"], "hours": ["heures", "horaires"],
    "day": ["jour"], "week": ["semaine"], "month": ["mois"], "year": ["année", "an"],
    "today": ["aujourd'hui"], "tomorrow": ["demain"], "yesterday": ["hier"], "next": ["prochaine", "prochain"],
    "last": ["dernier", "dernière"], "previous": ["précédent", "précédente"], "schedule": ["horaire", "planning", "calendrier"],
    "calendar": ["calendrier"], "open": ["ouvert", "ouvre", "ouverture"], "opening": ["ouverture", "ouvert"],
    "close": ["fermé", "ferme", "fermeture"], "closed": ["fermé"], "saturday": ["samedi"], "sunday": ["dimanche"],
    "monday": ["lundi"], "tuesday": ["mardi"], "wednesday": ["mercredi"], "thursday": ["jeudi"], "friday": ["vendredi"],
    "weekend": ["week-end", "samedi", "dimanche"], "holiday": ["férié", "vacances"], "start": ["début", "commence"],
    "end": ["fin"], "until": ["jusqu'à"], "before": ["avant"], "after": ["après"], "late": ["tard", "retard"],
    "early": ["tôt"], "duration": ["durée"], "long": ["durée", "long"], "minutes": ["minutes"],
    "october": ["octobre"], "september": ["septembre"], "november": ["novembre"], "december": ["décembre"],
    "january": ["janvier"], "february": ["février"], "march": ["mars"], "april": ["avril"], "may": ["mai"],
    "june": ["juin"], "july": ["juillet"], "august": ["août"],
    # réunions, décisions, tâches
    "meeting": ["réunion", "séance"], "meetings": ["réunions"], "monthly": ["mensuelle", "mensuel"],
    "weekly": ["hebdomadaire", "hebdo"], "agenda": ["ordre", "jour"], "decide": ["décidé", "décision"],
    "decided": ["décidé", "décision", "valide"], "decision": ["décision", "décidé"], "decisions": ["décisions"],
    "agreed": ["convenu", "accord", "validé"], "approved": ["approuvé", "validé"], "vote": ["vote"],
    "task": ["tâche"], "tasks": ["tâches"], "action": ["action"], "responsible": ["responsable", "charge"],
    "charge": ["charge", "responsable"], "owner": ["responsable"], "summary": ["résumé"], "report": ["compte-rendu", "rapport"],
    "recording": ["enregistrement", "transcription"], "recorded": ["enregistrée", "enregistré"],
    "transcript": ["transcription"], "discuss": ["discuté"], "discussed": ["discuté"], "said": ["dit"],
    "who": ["qui"], "participants": ["participants"], "attend": ["participer", "présent"],
    # événements, hackathon, compétition
    "hackathon": ["hackathon"], "event": ["événement"], "competition": ["concours", "compétition"],
    "prize": ["prix"], "prizes": ["prix"], "award": ["prix"], "reward": ["prix", "récompense"], "winner": ["gagnant"],
    "win": ["gagner"], "team": ["équipe"], "teams": ["équipes"], "member": ["membre"], "members": ["membres"],
    "participant": ["participant"], "alone": ["seul"], "solo": ["seul"], "individual": ["individuel", "seul"],
    "register": ["inscrire", "inscription", "inscriptions"], "registration": ["inscription", "inscriptions"],
    "sign": ["inscrire", "inscription"], "signup": ["inscription"], "enroll": ["inscrire"], "apply": ["candidature", "postuler"],
    "application": ["candidature"], "submit": ["dépôt", "soumettre", "déposer"], "submission": ["dépôt", "soumission"],
    "project": ["projet"], "projects": ["projets"], "idea": ["idée"], "theme": ["thème"], "topic": ["thème", "sujet"],
    "track": ["axe"], "tracks": ["axes"], "mentor": ["mentor"], "mentors": ["mentors"], "jury": ["jury"],
    "judge": ["jury"], "judges": ["jury"], "partner": ["partenaire"], "partners": ["partenaires"],
    "climate": ["climat"], "energy": ["énergie"], "solar": ["solaire"], "water": ["eau"], "agriculture": ["agriculture"],
    "women": ["féminine", "femmes"], "female": ["féminine"], "incubation": ["incubation"], "startup": ["startup", "entreprise"],
    "training": ["formation"], "workshop": ["atelier", "formation"], "session": ["séance", "session"],
    "course": ["formation", "cours"], "online": ["ligne"], "information": ["information"], "info": ["information"],
    # lieux, matériel, fablab
    "fablab": ["fablab"], "lab": ["laboratoire", "fablab"], "room": ["salle"], "hall": ["salle"],
    "place": ["lieu", "salle"], "where": ["où"], "location": ["lieu"], "building": ["bâtiment"], "floor": ["étage"],
    "office": ["bureau"], "reception": ["accueil"], "front": ["accueil"], "desk": ["accueil", "bureau"],
    "machine": ["machine"], "machines": ["machines"], "printer": ["imprimante"], "printing": ["impression"],
    "print": ["imprimer", "impression"], "prints": ["impressions"], "3d": ["3d"], "laser": ["laser"],
    "cutter": ["découpeuse"], "cut": ["découper", "découpe"], "cutting": ["découpe"], "milling": ["fraiseuse"],
    "tool": ["outil"], "tools": ["outils"], "equipment": ["matériel", "équipement"], "material": ["matériel", "matériau"],
    "materials": ["matériaux", "matériel"], "hardware": ["matériel"], "components": ["composants"],
    "electronics": ["électronique"], "filament": ["filament"], "free": ["gratuit", "gratuitement", "offert"],
    "cost": ["coût", "prix", "frais"], "price": ["prix"], "pay": ["payer", "frais"], "fee": ["frais"],
    "book": ["réserver", "réservation"], "booking": ["réservation"], "reserve": ["réserver"], "reservation": ["réservation"],
    "borrow": ["emprunter", "prêt"], "loan": ["prêt"], "rules": ["règles"], "rule": ["règle"], "safety": ["sécurité"],
    "security": ["sécurité"], "glasses": ["lunettes"], "goggles": ["lunettes"], "mandatory": ["obligatoire"],
    "required": ["obligatoire"], "allowed": ["autorisé", "interdit"], "forbidden": ["interdit"], "pvc": ["pvc"],
    "badge": ["badge"], "computer": ["ordinateur"], "laptop": ["ordinateur", "portable"], "bring": ["apporter"],
    "wifi": ["wifi", "réseau"], "network": ["réseau"], "password": ["mot", "passe"], "internet": ["wifi", "réseau"],
    "coffee": ["café"], "food": ["repas"], "meal": ["repas"], "meals": ["repas"], "lunch": ["midi", "repas"],
    "dinner": ["soir", "repas"], "allergies": ["allergies"], "paper": ["papier"], "pages": ["pages"],
    "door": ["porte"], "code": ["code"], "key": ["clé"], "contact": ["contact", "joignable"], "email": ["e-mail", "mail"],
    "phone": ["téléphone"], "manager": ["responsable"], "director": ["directeur"], "coordinator": ["coordinateur"],
    # argent, budget
    "budget": ["budget"], "money": ["argent", "budget"], "spent": ["dépensé"], "spend": ["dépenser"],
    "funding": ["financement"], "reserved": ["réservés", "réserve"], "purchase": ["achat", "acheter"],
    "buy": ["acheter", "achat"], "quote": ["devis"], "quotes": ["devis"], "salary": ["salaire"], "fcfa": ["fcfa"],
    # communication
    "group": ["groupe"], "message": ["message"], "messages": ["messages"], "chat": ["chat", "groupe"],
    "pinned": ["épinglés"], "announcement": ["annonce"], "announced": ["annoncé", "annoncés"], "link": ["lien"],
    "form": ["formulaire"], "document": ["document"], "documents": ["documents"], "guide": ["guide"],
    "chatbot": ["chatbot"], "bot": ["chatbot", "bot"], "question": ["question"], "answer": ["réponse"],
    # quantités, divers
    "how": ["comment"], "many": ["combien"], "much": ["combien"], "number": ["nombre"], "maximum": ["maximum"],
    "minimum": ["minimum"], "limit": ["limite"], "people": ["personnes"], "persons": ["personnes"], "size": ["taille"],
    "second": ["deuxième"], "first": ["premier", "1er"], "third": ["troisième"], "new": ["nouveau", "nouvelle"],
    "change": ["changé", "changement"], "changed": ["changé"], "need": ["faut", "besoin"], "must": ["doit", "faut"],
    "use": ["utiliser", "utilisation"], "using": ["utilisation"], "access": ["accès"], "join": ["rejoindre"],
    "help": ["aide"], "support": ["soutien", "aide"], "goal": ["objectif"], "target": ["objectif"],
    "registered": ["inscrites", "inscrits"], "enrolled": ["inscrits"], "confirmed": ["confirmé", "confirmés"],
    "list": ["liste"], "published": ["publiée", "publié"], "provided": ["fourni", "fournit"], "provide": ["fournir"],
    "name": ["nom", "appelle"], "named": ["appelle", "nommé"], "called": ["appelle"], "participate": ["participer", "participation"],
    "participation": ["participation"], "come": ["venir"], "go": ["aller"], "find": ["trouver"], "see": ["voir"],
    "ask": ["demander", "poser"], "send": ["envoyer"], "share": ["partager"], "added": ["ajoutée", "ajouté"],
    "add": ["ajouter"], "allergy": ["allergies"], "organize": ["organiser"], "organized": ["organisé"],
    "president": ["président"], "country": ["pays"], "city": ["ville"], "world": ["monde"], "cup": ["coupe"],
    "recipe": ["recette"], "weather": ["météo"], "ticket": ["billet"], "flight": ["avion", "vol"],
}


def _en_base_forms(token: str) -> list[str]:
    forms = [token]
    for suf, rep in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"), ("d", "")):
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            forms.append(token[: -len(suf)] + rep)
    return forms


def _build_fr_en() -> dict[str, list[str]]:
    rev: dict[str, list[str]] = {}
    for en, frs in EN_FR.items():
        for fr in frs:
            rev.setdefault(fr.lower(), [])
            if en not in rev[fr.lower()]:
                rev[fr.lower()].append(en)
    return rev


FR_EN = _build_fr_en()


def translations(token: str) -> list[str]:
    """Équivalents dans l'autre langue d'un mot (EN -> FR ou FR -> EN)."""
    t = token.lower()
    for form in _en_base_forms(t):
        if form in EN_FR:
            return EN_FR[form]
    return FR_EN.get(t, [])


def expand_query(question: str) -> str:
    """Question + traductions de ses mots : sert à la recherche vectorielle inter-langues."""
    extra: list[str] = []
    for tok in _TOKEN_RE.findall(question):
        for tr in translations(tok.split("'")[-1]):
            if tr not in extra:
                extra.append(tr)
    return f"{question} {' '.join(extra)}" if extra else question

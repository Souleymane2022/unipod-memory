"""Génération d'images à la demande (« génère une image de… », « /image … », « draw a… »).

1. Gemini (modèle image, IMAGE_MODEL) avec la clé GEMINI_API_KEY ;
2. repli gratuit, sans clé : Pollinations (IMAGE_FALLBACK=pollinations ; « none » pour le désactiver),
   utile car l'offre gratuite de Gemini peut ne pas inclure la génération d'images.
"""
from __future__ import annotations

import base64
import logging
import re
from urllib.parse import quote

import httpx

from .llm import LLMError, raise_for_status

log = logging.getLogger("unipods.images")

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
IMAGE_FALLBACK_MODELS = ("gemini-2.5-flash-image", "gemini-2.5-flash-image-preview")
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"
MAX_PROMPT_CHARS = 500

_VERB = (r"(?:g[ée]n[èée]re[rz]?|cr[ée]{1,2}[rz]?|fai[st]|faire|dessine[rz]?|produi[st]|"
         r"generate|create|make|draw|render|paint)")
_NOUN = r"(?:image|images|illustration|photo|dessin|picture|pic|drawing|logo|affiche|poster|visuel|visual)"
IMAGE_RE = re.compile(
    r"^\s*(?:/(?:image|img|draw|dessin)(?:@\w+)?\b"                       # commande /image
    rf"|(?:peux[- ]tu\s+|pourrais[- ]tu\s+|can\s+you\s+|could\s+you\s+|please\s+|stp\s+)?"
    rf"{_VERB}(?:[- ]?(?:moi|nous|me|us))?\s+(?:une?\s+|an?\s+|des\s+|the\s+|some\s+)?(?P<noun>{_NOUN})\b"
    rf"|(?P<noun2>{_NOUN})\s*:"                                            # image : …
    r"|dessine(?:[- ]moi)?\b|draw(?:\s+me)?\b)"                            # dessine un chat
    r"[\s:,-]*(?:(?:d'|de\s+|du\s+|des\s+|of\s+|about\s+|sur\s+|avec\s+|with\s+|représentant\s+|showing\s+))?"
    r"(?P<subject>.*)$",
    re.IGNORECASE | re.DOTALL)
_GENERIC = {"image", "images", "photo", "picture", "pic", "visuel", "visual"}


class ImageError(RuntimeError):
    pass


def image_prompt(text: str) -> str | None:
    """Description de l'image demandée, ou None si le message n'est pas une demande d'image."""
    m = IMAGE_RE.match(text or "")
    if not m:
        return None
    prompt = m.group("subject").strip().strip(" .?!")
    noun = (m.group("noun") or m.group("noun2") or "").lower()
    if prompt and noun and noun not in _GENERIC:  # « une affiche pour le hackathon » : le type d'image compte
        prompt = f"{noun} {prompt}"
    return prompt[:MAX_PROMPT_CHARS]


def generate_image(prompt: str, llm) -> tuple[bytes, str, str]:
    """(octets, type MIME, fournisseur). Lève ImageError si aucun fournisseur n'a réussi."""
    errors = []
    if llm.settings.gemini_api_key:
        try:
            data, mime = _gemini_image(prompt, llm)
            return data, mime, "gemini"
        except ImageError as exc:
            errors.append(str(exc))
            log.warning("Image Gemini impossible, repli : %s", exc)
    if llm.settings.image_fallback == "pollinations":
        try:
            return (*_pollinations_image(prompt, llm.settings), "pollinations")
        except ImageError as exc:
            errors.append(str(exc))
    raise ImageError(" ; ".join(errors) or "aucun générateur d'images configuré")


def _gemini_image(prompt: str, llm) -> tuple[bytes, str]:
    s = llm.settings
    models = [s.image_model] + [m for m in IMAGE_FALLBACK_MODELS if m != s.image_model]
    body = {"contents": [{"parts": [{"text": f"Generate an image: {prompt}"}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
    last = None
    for model in models:
        try:
            r = httpx.post(f"{GEMINI_API}/models/{model}:generateContent", json=body, timeout=max(s.llm_timeout, 60),
                           headers={"x-goog-api-key": s.gemini_api_key})
            raise_for_status(r, "gemini")
            parts = r.json()["candidates"][0]["content"]["parts"]
            img = next((p.get("inlineData") or p.get("inline_data")) for p in parts
                       if p.get("inlineData") or p.get("inline_data"))
            return base64.b64decode(img["data"]), img.get("mimeType") or img.get("mime_type") or "image/png"
        except LLMError as exc:
            last = exc
            if exc.status not in (400, 403, 404, 429):  # modèle absent, non autorisé ou quota : suivant
                break
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, StopIteration) as exc:
            last = exc  # pas d'image dans la réponse (refus, filtre de sécurité…)
    raise ImageError(f"Gemini : {last}")


def _pollinations_image(prompt: str, settings) -> tuple[bytes, str]:
    headers = {"Authorization": f"Bearer {settings.pollinations_token}"} if settings.pollinations_token else None
    try:
        r = httpx.get(POLLINATIONS_URL + quote(prompt, safe=""), params={"width": 1024, "height": 1024,
                      "nologo": "true"}, headers=headers, timeout=max(settings.llm_timeout, 90), follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise ImageError(f"Pollinations : {exc}") from exc
    mime = r.headers.get("content-type", "").split(";")[0]
    if not mime.startswith("image/") or not r.content:
        raise ImageError(f"Pollinations : réponse inattendue ({mime or 'vide'})")
    return r.content, mime

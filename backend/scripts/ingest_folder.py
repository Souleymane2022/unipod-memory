"""Indexe tous les fichiers .txt/.md/.pdf d'un dossier (par défaut data/samples).

Usage (depuis la racine du dépôt) :
    python -m backend.scripts.ingest_folder                 # data/samples
    python -m backend.scripts.ingest_folder data/mon_dossier --type chat
    python -m backend.scripts.ingest_folder --reset         # vide la base avant
"""
from __future__ import annotations

import argparse
from pathlib import Path

from backend.app.config import ROOT_DIR
from backend.app.main import ALLOWED_EXTENSIONS, ingest_bytes, services


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", default=str(ROOT_DIR / "data" / "samples"))
    parser.add_argument("--type", dest="doc_type", choices=["chat", "transcript", "document"], default=None)
    parser.add_argument("--author", default=None)
    parser.add_argument("--date", default=None)
    parser.add_argument("--reset", action="store_true", help="Supprime toute la collection avant l'import")
    args = parser.parse_args()

    s = services()
    if args.reset:
        s.store.reset()
        print(f"Base réinitialisée ({s.store.kind}).")

    folder = Path(args.folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS)
    if not files:
        print(f"Aucun fichier compatible dans {folder}")
        return
    for path in files:
        r = ingest_bytes(path.name, path.read_bytes(), args.doc_type, args.author, args.date, save=False)
        print(f"✔ {r['source']:<45} type={r['doc_type']:<10} chunks={r['chunks']} mots/chunk={r['chunk_words']}")
    print(f"Total : {s.store.count()} chunks dans la base.")


if __name__ == "__main__":
    main()

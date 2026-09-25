# UniPods Memory — chatbot de mémoire collective

Dans un grand groupe (400+ membres), on rate des messages, on repose des questions déjà répondues,
on manque des réunions. **UniPods Memory** indexe les **messages du groupe**, les **transcriptions de réunions**
et les **documents**, puis répond directement aux questions **en citant la source exacte**
(fichier, auteur, date, horodatage, extrait). Si l'information n'existe pas, il le dit — **sans inventer**.

## Démo en ligne

| Canal | Accès | Pour qui |
|---|---|---|
| 🌐 **Site web** (FR/EN) | <https://unipod-memory-iota.vercel.app> | ouvert à tous |
| ✈️ **Telegram** | [@UniPodsMemory2026Bot](https://t.me/UniPodsMemory2026Bot) | ouvert à tous |
| 💬 **WhatsApp** | +1 555 191 7088 | **mode test Meta** : seuls les numéros autorisés reçoivent une réponse (5 max.) |

Essayez par exemple : « Que dit le document METI ? », « Quand a lieu la prochaine réunion ? »,
« What are the hackathon prizes? », ou une question hors sujet (« Quel est le salaire du directeur ? ») pour voir
le refus honnête. Sur WhatsApp et Telegram : `aide` / `/aide`, `documents`, `résumé <nom du fichier>`.

État du service : <https://unipod-memory-iota.vercel.app/api/health> (version déployée, LLM, base, derniers messages reçus).

![Démo questions/réponses](docs/demo_questions.png)

## Fonctionnalités

| | Fonctionnalité | État |
|---|---|---|
| ✅ | Ingestion `.txt` / `.md` / `.pdf` / `.docx` (Word) / `.odt` / `.html` (API multipart, API texte JSON, CLI, interface web) | fait |
| ✅ | Détection auto du type : chat (format `[AAAA-MM-JJ HH:MM] Nom: …` ou export WhatsApp), transcription (`[HH:MM:SS] Nom: …`), document | fait |
| ✅ | Chunks de 300–500 mots, équilibrés (à un message près, car on ne coupe jamais un message ou une intervention) ; métadonnées source, date(s), auteur(s), type, titre | fait |
| ✅ | Embeddings locaux gratuits (ONNX all-MiniLM-L6-v2) ; base vectorielle **ChromaDB** en local, **PostgreSQL + pgvector (Neon)** en production sur Vercel (mémoire permanente) | fait |
| ✅ | Q/R RAG : recherche hybride (vectorielle + lexicale pondérée IDF), citations avec auteur/date/heure exacts | fait |
| ✅ | Garde-fou anti-hallucination : seuil de pertinence + réponse « information non disponible » | fait |
| ✅ | Génération par LLM **optionnelle** : **Gemini** (offre gratuite, utilisé en production), Anthropic, OpenAI ou compatible, Ollama local ; sinon mode extractif | fait |
| ✅ | Interface web (Q/R, ajout/suppression de documents, résumé) | fait |
| ✅ | **Chatbot WhatsApp** (API Cloud officielle de Meta, webhook signé) — en ligne, réponses reçues sur un vrai téléphone ; numéro de test Meta (destinataires autorisés uniquement) | fait |
| ✅ | **Chatbot Telegram** (webhook sur Vercel, menu de commandes FR/EN, groupes) — en ligne, ouvert à tous | fait |
| ✅ | **Bilingue français / anglais** : interface FR/EN, réponses dans la langue choisie, questions en anglais sur des sources en français (et inversement) | fait |
| ✅ | **Chatbot vocal** (site) : 🎤 poser la question à voix haute, 🔊 réponse lue à voix haute (lecture automatique quand la question est dictée ; chaque phrase avec la voix de sa langue). API vocales du navigateur, sans clé ni coût — dictée sur Chrome/Edge/Safari | fait |
| ✅ | **Messages vocaux WhatsApp & Telegram** : la note vocale est transcrite par Gemini (ou Whisper avec `LLM_PROVIDER=openai`), le bot répond par écrit (« 🎤 J'ai entendu : … » + réponse sourcée) puis par une **note vocale** (Gemini TTS encodé en MP3). Si la voix échoue (quota), la réponse écrite part quand même | fait |
| ✅ | **Génération d'images** (site, WhatsApp, Telegram, à l'écrit ou par message vocal) : « génère une image de… », « dessine-moi… », `/image …`, « draw a… » → image créée par Gemini (`gemini-2.5-flash-image`), avec repli gratuit sans clé sur Pollinations si le quota Gemini ne le permet pas | fait |
| ✅ | **Bonus** : résumé d'une conversation/réunion + décisions + tâches (responsable, échéance) | fait |

## Architecture

```
/backend
  app/
    main.py          API FastAPI + service du frontend
    config.py        paramètres (.env)
    parsers.py       lecture txt/md/pdf, détection chat/transcription/document, en-têtes (Titre, Date, Auteur…)
    chunker.py       découpage 300–500 mots
    store.py         ChromaDB (persistant) + fonction d'embedding + choix automatique de la base
    pg_store.py      PostgreSQL + pgvector (Neon sur Vercel), actif si DATABASE_URL
    rag.py           recherche hybride, seuil de pertinence, réponse citée (LLM ou extractive)
    llm.py           client LLM optionnel (httpx, sans SDK)
    insights.py      bonus : résumé, décisions, tâches
    textutils.py     mots-clés, racinisation FR, IDF, découpage en phrases
    i18n.py          messages FR/EN, détection de langue, lexique bilingue pour la recherche inter-langues
    translate.py     traduction des citations (LLM ou MyMemory gratuit), repli sur le texte original
    channels.py      webhooks WhatsApp (Meta Cloud API) et Telegram
    messaging.py     commandes et mise en forme communes aux messageries
    telegram_bot.py  bot Telegram en long polling (hors Vercel)
  scripts/ingest_folder.py   indexation d'un dossier en ligne de commande
  tests/             154 tests pytest (API sur ChromaDB et PostgreSQL, parsing, PDF/docx/odt/html, anti-hallucination, bilinguisme,
                     traduction simulée, LLM simulé, bot)
/data
  samples/           jeu de démo : chat du groupe, transcription de réunion, guide du fablab
  chroma/            base vectorielle (générée, ignorée par git)
  uploads/           copies des fichiers envoyés via l'API (ignorées par git)
/frontend            index.html + app.js + style.css (vanilla, servi par FastAPI)
```

**Pipeline** : fichier → extraction texte → unités (message / intervention / paragraphe) avec auteur et date →
chunks de 300–500 mots → embeddings → ChromaDB.
**Question** → 20 candidats par similarité cosinus → re-classement hybride
`0,55 × similarité + 0,45 × recouvrement lexical pondéré IDF` → filtre de pertinence →
sélection des phrases les plus pertinentes → réponse (LLM contraint aux extraits numérotés, ou extraits cités tels quels).

## Installation

Prérequis : Python 3.10+ (testé en 3.11).

```bash
git clone <ce dépôt> && cd unipod-memory
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # facultatif : aucune clé n'est obligatoire
```

Au premier lancement, ChromaDB télécharge une seule fois le modèle d'embedding ONNX (~80 Mo) dans `~/.cache/chroma`.

## Lancement

```bash
# 1. Indexer le jeu de démo (ou vos propres fichiers)
python -m backend.scripts.ingest_folder --reset

# 2. Démarrer l'API + l'interface web
uvicorn backend.app.main:app --reload --port 8000
```

- Interface : http://localhost:8000
- Documentation interactive de l'API (Swagger) : http://localhost:8000/docs

Tests : `python -m pytest backend/tests -q`

### Déployer sur Vercel

Le dépôt est prêt pour Vercel (préréglage **FastAPI**, répertoire racine `./`) :
`pyproject.toml` déclare le point d'entrée (`[tool.vercel] entrypoint = "backend.app.main:app"`) et les dépendances.
⚠️ Vercel lit `pyproject.toml` et **ignore `requirements.txt`** : garder les deux listes synchronisées.

Quand la variable `VERCEL` est présente, l'application s'adapte automatiquement :
- base ChromaDB, fichiers envoyés et modèle d'embedding stockés dans `/tmp/unipods` (seul dossier inscriptible) ;
- le jeu de démo `data/samples` est **indexé automatiquement** à chaque démarrage à froid (`AUTO_SEED=1`).

#### Mémoire permanente avec Neon (PostgreSQL) — recommandé sur Vercel

Sans base de données, Vercel efface les documents ajoutés à chaque redémarrage. Avec **Neon** (PostgreSQL + pgvector,
offre gratuite), la mémoire est permanente et partagée par toutes les instances :

1. Projet Vercel → onglet **Storage** → **Create Database** → **Neon** (ou *Marketplace → Neon*) → relier au projet.
   Vercel ajoute automatiquement la variable **`DATABASE_URL`** (et `POSTGRES_URL`).
2. **Redéployer**. Le statut affiche alors « base PostgreSQL » et ne mentionne plus le stockage temporaire.
3. Au premier démarrage, la table est créée et le jeu de démo indexé ; ensuite, tout document ajouté est conservé.

Côté code, `DATABASE_URL` présent ⇒ `backend/app/pg_store.py` (une table `unipods_memory_chunks` : texte, métadonnées et
vecteur pgvector, recherche par distance cosinus). Sans `DATABASE_URL`, c'est **ChromaDB** local, comme avant.
Les embeddings sont calculés de la même façon dans les deux cas. `VECTOR_STORE=chroma|postgres` force un choix.
Tests : toute la suite d'API tourne sur les deux bases (PostgreSQL embarqué via le paquet `pgserver`).

Limites à connaître (hébergement serverless) :
- **sans Neon, stockage temporaire** : un document ajouté via l'interface peut disparaître au redémarrage d'une instance,
  et deux instances ne partagent pas la même base. Pour une mémoire durable, ajoutez les fichiers dans
  `data/samples/` et redéployez, ou hébergez le backend sur un service avec disque persistant (Render, Railway, Fly.io…) ;
- **premier appel lent** (démarrage à froid) : installation des dépendances restantes + téléchargement du modèle (~80 Mo) + indexation ;
- les clés LLM éventuelles se déclarent dans *Settings → Environment Variables* du projet Vercel.

### Activer un LLM (optionnel)

Sans LLM, le bot répond en **mode extractif** : il cite mot pour mot les messages/phrases pertinents.
Avec un LLM, il rédige une réponse synthétique, toujours contrainte aux extraits et avec références `[1]`, `[2]`.

#### Gemini (Google) — offre gratuite, recommandé pour la démo

1. Créer une clé sur [Google AI Studio](https://aistudio.google.com) → *Get API key* (compte Google, pas de carte bancaire).
2. Ajouter **une seule variable** (dans `.env`, ou sur Vercel : *Settings → Environment Variables*, puis redéployer) :
   ```bash
   GEMINI_API_KEY=AQ....        # GOOGLE_API_KEY est aussi accepté
   # facultatif : LLM_MODEL=… pour imposer un modèle (par défaut : gemini-flash-lite-latest)
   ```
3. Le statut en haut de la page affiche alors `LLM : gemini:gemini-flash-lite-latest`.

**Quotas gratuits** : chaque modèle Gemini a son propre quota quotidien, parfois très bas (ex. 20 requêtes/jour pour
`gemini-3.6-flash`). L'application utilise d'abord `gemini-flash-lite-latest`, puis bascule automatiquement sur
`gemini-3.5-flash-lite`, `gemini-3.6-flash` et `gemini-flash-latest` quand le quota du jour d'un modèle est épuisé.
Si tous sont épuisés, elle repasse en mode extractif sans attendre, jusqu'à la remise à zéro du quota.

Ce que ça change : réponses rédigées en français **ou** en anglais, résumés rédigés, et la traduction des citations
passe par Gemini (plus besoin de MyMemory). Si Gemini refuse (clé invalide, quota gratuit atteint), l'application
revient automatiquement au mode extractif et affiche la raison sous la réponse.

⚠️ Sur l'offre **gratuite**, Google peut utiliser les textes envoyés pour améliorer ses produits (voir les conditions
de Google AI Studio). Parfait pour les données de démo ; pour de vraies conversations privées, préférer l'offre payante
de Gemini, l'API Claude, ou Ollama en local.

#### Autres fournisseurs

```bash
# Anthropic
ANTHROPIC_API_KEY=sk-ant-...          # LLM_MODEL par défaut : claude-sonnet-5
# ou OpenAI / tout serveur compatible (Groq, Mistral, OpenRouter…)
OPENAI_API_KEY=sk-...  LLM_BASE_URL=https://api.groq.com/openai/v1  LLM_MODEL=llama-3.1-8b-instant
# ou 100 % local et gratuit avec Ollama
LLM_PROVIDER=ollama  LLM_MODEL=llama3.1
```

Si l'appel au LLM échoue (quota, réseau), l'API bascule automatiquement en mode extractif et renvoie un `warning`.

### Chatbot WhatsApp (optionnel, API officielle de Meta)

Les membres écrivent au numéro WhatsApp du bot et reçoivent la réponse citée (FR/EN), comme sur le site.
Commandes : `aide`, `documents`, `résumé <nom du fichier>` (un morceau du nom suffit), ou une question libre.
Le bot ne peut pas lire un groupe WhatsApp en direct (limite de l'API) : pour alimenter la mémoire,
*Exporter la discussion* du groupe (sans médias) et envoyer le `.txt` dans l'onglet « Ajouter des documents ».

1. Sur [developers.facebook.com](https://developers.facebook.com) : *Créer une app* → type **Business** → ajouter le produit **WhatsApp**.
2. Dans *WhatsApp → API Setup* : noter le **Phone number ID**, générer un **access token**, et ajouter
   (jusqu'à 5) numéros destinataires de test. Meta fournit un numéro de test gratuit.
3. Dans Vercel → *Environment Variables* : `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
   `WHATSAPP_VERIFY_TOKEN` (une phrase secrète de votre choix), `WHATSAPP_APP_SECRET`
   (*Paramètres de l'app → Général → Clé secrète*), puis **redéployer**.
4. Dans *WhatsApp → Configuration → Webhook* : URL `https://<votre-site>/api/whatsapp/webhook`,
   *Verify token* = la même phrase secrète → **Vérifier et enregistrer**, puis **s'abonner au champ `messages`**.
5. Envoyer « aide » au numéro de test depuis un numéro autorisé.

**Pièges rencontrés (vérifiés en conditions réelles)** — `GET /api/health` → `recent_messages` montre les derniers
messages reçus/envoyés (journal stocké en base, numéros masqués) et permet de savoir où ça bloque :

| Symptôme | Cause | Solution |
|---|---|---|
| Aucun événement dans `recent_messages` | app non **publiée**, ou app non abonnée au compte WhatsApp, ou champ `messages` non abonné | publier l'app (URL `/privacy` et `/data-deletion` exigées) ; Explorateur de l'API Graph : `POST <WABA_ID>/subscribed_apps` (token utilisateur) et `POST <APP_ID>/subscriptions?object=whatsapp_business_account&fields=messages&callback_url=…&verify_token=…` (token d'app) |
| « Callback verification failed » / `Jeton de vérification invalide` | phrase différente de `WHATSAPP_VERIFY_TOKEN` | tester `…/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=<phrase>&hub.challenge=12345` → doit afficher `12345` |
| `signature_invalide` | `WHATSAPP_APP_SECRET` mal copié | recopier la clé secrète (ou retirer la variable le temps de tester) |
| `erreur_envoi` **131030** | numéro de test : destinataire non autorisé | l'ajouter dans *Étape 1. Faites un essai → Destinataire* (5 numéros max) |
| `erreur_envoi` **401 / 190** | token expiré (24 h) | régénérer le token, mettre à jour `WHATSAPP_TOKEN`, redéployer |

⚠️ Le jeton affiché dans *API Setup* expire au bout de 24 h : pour une utilisation durable, créer un
*utilisateur système* dans Meta Business Suite et générer un jeton permanent. `WHATSAPP_ALLOWED_NUMBERS` limite
l'accès à certains numéros. Tarifs : répondre à un utilisateur qui écrit en premier est en général gratuit
(voir la grille tarifaire actuelle de Meta).

### Bot Telegram (optionnel)

**Sur Vercel (webhook)** :
1. Créer un bot avec [@BotFather](https://t.me/BotFather) (`/newbot`) et copier son jeton.
2. Dans Vercel : `TELEGRAM_BOT_TOKEN` et `TELEGRAM_WEBHOOK_SECRET` (phrase secrète de votre choix, lettres/chiffres), redéployer.
3. Ouvrir **une fois** `https://<votre-site>/api/telegram/setup?key=<TELEGRAM_WEBHOOK_SECRET>` : le bot est actif.

**Sur un PC ou serveur classique (long polling)** : `TELEGRAM_BOT_TOKEN=... python -m backend.app.telegram_bot`.
Mêmes commandes que WhatsApp (`/aide`, `/documents`, `/resume <fichier>` ou une question).

## Ajouter de nouveaux documents

Trois façons (un fichier portant le même nom **remplace** l'ancienne version, pas de doublons) :

1. **Interface web** → onglet « Ajouter des documents » (type, auteur et date optionnels).
2. **API** :
   ```bash
   curl -X POST localhost:8000/api/ingest \
        -F "files=@export_whatsapp.txt" -F "files=@compte_rendu.pdf" \
        -F "doc_type=auto" -F "date=2026-09-20"
   # texte brut (ex. depuis un autre bot) :
   curl -X POST localhost:8000/api/ingest/text -H 'Content-Type: application/json' \
        -d '{"source":"annonce.txt","text":"...","author":"Awa","date":"2026-09-20"}'
   ```
3. **Ligne de commande** : déposer les fichiers dans un dossier puis
   `python -m backend.scripts.ingest_folder data/mon_dossier [--type chat] [--author ...] [--date ...]`.

**Formats conseillés**
- Chat : `[2026-09-08 09:12] Awa Diallo: message` ou export WhatsApp `08/09/2026 09:12 - Awa: message`
  (les lignes de continuation sont rattachées au message précédent).
- Transcription : en-tête optionnel `Titre : …` / `Date : 2026-09-15` / `Participants : …`, puis `[00:04:05] Nom: texte`.
- Document : en-tête optionnel `Titre :`, `Auteur :`, `Date :`, puis texte libre (paragraphes).
- PDF : texte extrait avec pypdf (les PDF scannés sans couche texte sont refusés avec un message clair).
- Word `.docx`, LibreOffice `.odt`, pages `.html` : texte extrait sans dépendance supplémentaire.
- Sur Vercel, un fichier ne peut pas dépasser ~4 Mo (limite d'envoi de l'hébergement) : l'interface prévient avant l'envoi.
- Pertinence : les mots qui décrivent le *type* de réponse (« montant », « nombre », « date », « nom »…) ne sont pas
  exigés dans la source ; les mots du *sujet* (« bourse », « salaire »…) le sont — c'est le garde-fou anti-invention.

Autres endpoints : `GET /api/documents`, `DELETE /api/documents/{source}`, `POST /api/summarize`
(`{"source": "..."}` ou `{"text": "..."}`), `GET /api/health`.

## Exemples de questions / réponses (testés, mode extractif sans LLM)

Sorties réelles de `POST /api/ask` sur le jeu de démo (aussi vérifiées par `backend/tests/test_api.py`).

**1. Question tirée des messages du groupe**
> **Q :** Quelle est la date limite de dépôt des projets pour le hackathon ?
>
> **R :** Voici ce que dit la mémoire du groupe :
> • « [2026-09-08 09:30] Awa Diallo: Les inscriptions se font uniquement via le formulaire en ligne partagé dans les messages épinglés. La date limite de dépôt des projets pour le hackathon est le 10 octobre 2026 à 23h59. Après cette date, aucune candidature ne sera acceptée. » [1]
>
> **Source [1]** : `chat_general_septembre.txt` · Messages · Awa Diallo · 2026-09-08 09:30 · passage 1/2

**2. Question sur une réunion manquée**
> **Q :** Qu'est-ce qui a été décidé pour l'imprimante 3D pendant la réunion ?
>
> **R :** Voici ce que dit la mémoire du groupe :
> • « [00:07:15] Awa Diallo: Donc on valide la décision suivante : les impressions 3D seront limitées à des créneaux de deux heures maximum, réservés via le formulaire du fablab, à partir du 1er octobre. L'achat d'une deuxième imprimante sera décidé à la prochaine réunion, selon les devis. » [1]
>
> **Source [1]** : `reunion_mensuelle_2026-09-15.txt` · Réunion · Awa Diallo · 2026-09-15 à 00:07:15 · passage 1/2

**3. Question sur un document**
> **Q :** Comment réserver une machine du fablab ?
>
> **R :** Voici ce que dit la mémoire du groupe :
> • « Pour réserver une machine ou un poste de travail du fablab, il faut remplir le formulaire de réservation en ligne au moins 48 heures à l'avance. » (Équipe de coordination UniPod, 2026-09-01) [1]
>
> **Source [1]** : `guide_fablab_unipod.md` · Document · Équipe de coordination UniPod · 2026-09-01 · passage 1/1

**4. Information absente → pas d'hallucination**
> **Q :** Quel est le salaire du directeur de l'UniPod ?
>
> **R :** Je n'ai pas trouvé cette information dans la mémoire du groupe (messages, transcriptions de réunions et documents indexés). Elle n'est donc pas disponible pour le moment : vous pouvez poser la question dans le groupe ou ajouter le document concerné. — `found: false`, aucune source.

Autres questions qui fonctionnent sur la démo : « Quels sont les prix du hackathon ? », « À quelle heure ouvre l'UniPod le samedi ? »,
« Combien de filament gratuit par mois ? », « Est-ce qu'on peut découper du PVC au laser ? », « Quand a lieu la prochaine réunion mensuelle ? »,
« Combien d'équipes sont inscrites ? ». Refusées à juste titre : « Qui a gagné la coupe du monde 2022 ? », « Qui est le président du Tchad ? ».

### Bonus : résumé de réunion

`POST /api/summarize {"source": "reunion_mensuelle_2026-09-15.txt"}` → résumé, **3 décisions**
(500 000 FCFA réservés au prototypage, créneaux d'impression 3D de 2 h, transcriptions ajoutées sous 48 h)
et **7 tâches** avec responsable et échéance (ex. *Moussa Kane — 3 devis d'imprimante 3D avant le 30 septembre* ;
*Dr. Hassan Ali — confirmer le jury avant le 5 octobre*).

![Démo résumé](docs/demo_resume.png)

## Bilingue français / anglais

- **Interface** : bouton **FR / EN** dans l'en-tête. La langue du navigateur est choisie par défaut, puis le choix est mémorisé.
- **Réponses** : dans la langue de l'interface (champ `lang` de `POST /api/ask` et `/api/summarize` : `fr` ou `en`).
  Sans `lang`, l'API répond dans la langue détectée de la question (utile pour le bot Telegram).
- **Questions inter-langues** : une question en anglais retrouve les messages et documents en français, et inversement.
  Sans modèle multilingue (trop lourd pour Vercel), chaque mot de la question est associé à ses traductions via un lexique
  intégré (`backend/app/i18n.py`, environ 350 mots courants : réunions, événements, lieux, matériel, budget…) ;
  la recherche vectorielle est aussi lancée sur la question « enrichie » de ses traductions.
- **Traduction des citations** : quand la source n'est pas dans la langue de l'utilisateur, les phrases citées
  sont traduites (le texte original reste visible dans la carte « source »). Par défaut (`TRANSLATION_PROVIDER=auto`) :
  le LLM s'il est configuré, sinon l'API gratuite **MyMemory** (sans clé, ~5 000 caractères/jour, ~50 000 avec
  `MYMEMORY_EMAIL`). ⚠️ Les phrases traduites sont alors envoyées à `api.mymemory.translated.net` :
  mettre `TRANSLATION_PROVIDER=none` pour l'interdire. En cas d'échec (quota, réseau), la citation reste dans sa
  langue d'origine et la réponse le signale. Les résumés extractifs sont traduits de la même façon.
- **Changer de langue** traduit aussi la conversation en cours : les dernières questions sont reposées dans la nouvelle langue.
- **Avec un LLM**, la réponse est directement rédigée dans la langue demandée, avec les mêmes citations.

Exemples testés (mode extractif) :

> **Q :** What is the deadline to submit hackathon projects?
> **R :** Here is what the group's memory says: (quotes are in their original language)
> • « [2026-09-08 09:30] Awa Diallo: … La date limite de dépôt des projets pour le hackathon est le 10 octobre 2026 à 23h59. … » [1]

> **Q :** What is the director's salary? → *I couldn't find this information in the group's memory…* (aucune source)

Sur 26 questions de test (13 en anglais et 4 en français, avec réponse ; 9 hors sujet, dans les deux langues),
les 26 obtiennent le bon résultat. Le lexique a été complété à partir de ces questions : un mot absent du lexique
(vocabulaire très spécifique) peut empêcher une question en anglais de retrouver une source française.
Pour l'améliorer, ajouter des entrées dans `EN_FR` (`backend/app/i18n.py`), ou utiliser un modèle d'embedding
multilingue (`EMBEDDING_BACKEND=sentence-transformers`, hors Vercel).

## Réglages utiles (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `MIN_RELEVANCE` | `0.30` | score hybride minimal pour qu'un passage soit utilisé (plus haut = plus prudent) |
| `TOP_K` | `4` | nombre de passages envoyés au LLM |
| `CHUNK_MIN_WORDS` / `CHUNK_MAX_WORDS` | `300` / `500` | taille des chunks |
| `EMBEDDING_BACKEND` | `default` | `sentence-transformers` (ex. `paraphrase-multilingual-MiniLM-L12-v2`, meilleur en français) ou `openai` — **réindexer avec `--reset` après changement** |
| `CHROMA_DIR` | `data/chroma` | emplacement de la base |
| `VOICE_REPLIES` | `true` | réponse en note vocale aux messages vocaux WhatsApp/Telegram (`false` = réponse écrite seulement, économise le quota Gemini) |
| `IMAGE_MODEL` | `gemini-2.5-flash-image` | modèle Gemini de génération d'images |
| `IMAGE_FALLBACK` | `pollinations` | repli gratuit si Gemini refuse (quota) ; `none` pour le désactiver. `POLLINATIONS_TOKEN` facultatif |
| `TTS_MODEL` | `gemini-2.5-flash-preview-tts` | modèle de synthèse vocale Gemini (repli automatique sur d'autres modèles TTS si retiré) |

## Bilan

**Ce qui fonctionne, vérifié en conditions réelles** (déploiement Vercel + Neon + Gemini) :
- ingestion de vrais documents (dont un PDF de programme de 3 pages), mémoire **permanente** dans PostgreSQL/pgvector ;
- questions/réponses avec **citation de la source exacte** (fichier, auteur, date, heure, extrait), en français et en anglais,
  y compris une question en français sur un document en anglais ;
- réponses rédigées par **Gemini** (offre gratuite, avec bascule automatique entre modèles quand un quota du jour est
  épuisé), et **refus honnête** quand l'information n'existe pas (vérifié sur des questions hors sujet) ;
- résumé d'un document ou d'une réunion, avec décisions et tâches (responsable, échéance) ;
- **chatbot WhatsApp** : messages reçus et réponses envoyées sur un vrai téléphone ;
- **chatbot Telegram** : webhook, menu de commandes et descriptions configurés automatiquement.

**Vérifié par 154 tests automatisés** : toute la suite d'API tourne sur ChromaDB **et** sur PostgreSQL ; les webhooks
WhatsApp/Telegram sont testés avec des messages au format officiel ; les LLM, la traduction et les quotas sont simulés.

**Limites connues**
- WhatsApp utilise le **numéro de test de Meta** : seuls les numéros ajoutés comme destinataires (5 max.) reçoivent
  une réponse, et le jeton d'accès temporaire expire au bout de 24 h. Pour ouvrir le bot à tous : enregistrer un vrai
  numéro (carte SIM dédiée) et créer un jeton permanent (utilisateur système). Le site et Telegram sont ouverts à tous.
- Le quota gratuit de Gemini est limité par jour : au-delà, les réponses passent en mode extractif (citations des
  sources, sans rédaction) jusqu'à la remise à zéro du quota.
- Sans LLM, les résumés sont extractifs (phrases clés + motifs linguistiques) : utiles, mais moins fluides.
- Le modèle d'embedding par défaut est surtout anglophone ; la recherche hybride (lexicale FR/EN + IDF) et le lexique
  bilingue compensent, mais pour de gros corpus multilingues un modèle `sentence-transformers` multilingue serait préférable.

**Prochaines étapes possibles**
- « **Qu'est-ce que j'ai manqué ?** » : résumé de rattrapage sur une période (depuis hier, cette semaine), toutes sources confondues.
- **Priorisation** des informations (important / bon à savoir / discussion) et **détection des changements**
  (« la date limite, d'abord vendredi, a été avancée à jeudi »).
- Questions de suivi sur un résumé, et **tableau de bord admin** (questions fréquentes, questions sans réponse).
- Vrai numéro WhatsApp (ouvert à tous) et indexation des documents envoyés directement au bot.
- Connecteurs directs : export Telegram/WhatsApp automatique, Google Drive, transcription audio des réunions (Whisper local).
- Filtres dans les questions (« cette semaine », « d'après la réunion du 15 ») via les métadonnées de date/type.
- Authentification et espaces par groupe ; journal des questions sans réponse pour enrichir la FAQ.
- Évaluation continue (jeu de questions de référence) et réglage automatique du seuil de pertinence.
- Reranker cross-encoder multilingue et mémoire de conversation (questions de suivi).

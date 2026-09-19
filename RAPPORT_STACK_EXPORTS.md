# Rapport — Stack de génération de fichiers Office/PDF dans JejeGPT

Date : 18/09/2026 · Projet : `C:\Python\Labs\JejeGPT` (Django 5.2.17, Python 3.14.6, Windows, SQLite)

## 1. Objectif et périmètre acté

Demande : depuis la fenêtre de chat, pouvoir demander un fichier (ex. « crée un PDF avec diverses
infos ») et que JejeGPT le génère avec la même stack qu'en environnement assisté :
`python-docx` (DOCX), `openpyxl` (XLSX), `python-pptx` (PPTX), `ReportLab` (PDF).

Choix validés avant implémentation :
- **Priorité Office/PDF** (lot data/images : `pandas`, `Pillow` avancé, `matplotlib` → reporté) ;
- **Persistance MEDIA** (`MEDIA_ROOT` + modèle `Export`, pas de streaming éphémère) ;
- Exigences : normaliser `requirements.txt`, fixer `PyYAML`, smoke tests binaires, garde-fous sécu.

## 2. Analyse initiale (ce qui a été constaté)

- `requirements.txt` en **UTF-16 LE avec BOM** (908 octets) + `CRLF` : source de diffs/bruit éditeurs.
- **Piège PyYAML** : `types-PyYAML` installé mais runtime `PyYAML` absent → `import yaml` levait.
- Aucune infra fichier : pas de `MEDIA_ROOT`, pas de `FileField`, seul export existant =
  `data_export()` JSON en `attachment`.
- Structure monolithique `chat/` + `config/` : point d'accroche naturel = nouveau package
  `chat/exports/` + modèle `Export` + routes `exports/…`, sans toucher à `providers.py`.
- Contraintes : PowerShell 5.1 (pas de `&&`), SQLite sync (pas de worker → génération synchrone
  assumée en V1), frontend CDN-only (génération 100 % server-side).

## 3. Réalisation phase par phase (tests après chaque phase)

### Phase 0 — Normalisation base : OK
- `requirements.txt` converti **UTF-16 → UTF-8 sans BOM** (vérifié octets `97 110 121` = `any…`).
- Ajout `PyYAML==6.0.3`, `import yaml` OK aux côtés de Django 5.2.17.
- Test : `pip install -r requirements.txt --dry-run` exit 0.

### Phase 1 — Installation Office/PDF : OK
- Installés et épinglés : `python-docx==1.2.0`, `reportlab==5.0.1`, `python-pptx==1.0.2`,
  `openpyxl==3.1.5` (+ `pillow==12.3.0`, `lxml==6.1.3` en transitifs), tous compatibles Python 3.14.6.
- Test : génération minimale in-memory (`BytesIO`) des 4 formats → magics `PK` / `%PDF-` OK.
- `requirements.txt` figé via `pip freeze` (35 lignes, UTF-8 sans BOM).

### Phase 2 — Socle Django + persistance MEDIA : OK (7 tests)
- `config/settings.py` : `MEDIA_ROOT` privé (jamais servi statiquement), `MEDIA_URL`,
  `JEJEGPT_EXPORTS_MAX_INPUT_CHARS` (100 000), `JEJEGPT_EXPORTS_MAX_BYTES` (20 Mo),
  `JEJEGPT_EXPORTS_QUOTA_BYTES` (200 Mo), `JEJEGPT_EXPORTS_RETENTION_DAYS` (30 j).
- Modèle `Export` (migration `chat/migrations/0008_add_export.py`, **appliquée à `db.sqlite3`**) :
  FK `message` CASCADE `related_name="exports"`, `format` (pdf/docx/xlsx/pptx),
  `download_token` UUID unique, `FileField(upload_to="exports/%Y/%m/")`, `file_name` assaini,
  `size`, `created_at`. Conversation accessible via `message.conversation` (pas de FK redondante).
- `chat/exports/core.py` : `safe_name()` (accents FR conservés, anti path-traversal),
  `check_size()`, `check_quota()`.
- `chat/signals.py` + `ChatConfig.ready()` : suppression du fichier sur `post_delete` du `Export`
  (couvre les cascades Message/Conversation) via `transaction.on_commit`.
- `ExportAdmin` tout readonly ; commande `purge_exports` (dry-run par défaut, `--apply` explicite).
- Correctifs en cours de phase (3 échecs initiaux → 7/7) : double préfixe `upload_to` dans les tests,
  `reverse()` avec `kwargs`, signal déplacé de `Message` vers `Export` (les lignes cascadées
  n'existent plus au `post_delete` du parent).

### Phase 3 — Générateurs sécurisés : OK (5 tests)
- `chat/exports/document.py` : parseur Markdown déterministe sans HTML (titres H1-H3, paragraphes,
  listes, tableaux, blocs de code) + `sanitize_cell()` anti-injection Excel.
- `docx_gen.py` : Titre, Titres 1-3, paragraphes, puces, tableaux `Light Grid`, code Consolas,
  pied de page « Généré par JejeGPT ».
- `xlsx_gen.py` : feuille `Contenu` + une feuille `Tableau N` par tableau, largeurs, nombres typés,
  lignes **Total `=SUM(…)` / Moyenne `=AVERAGE(…)`** sur colonnes numériques.
- `pptx_gen.py` : slide de titre + une slide par section/liste/tableau (tableaux natifs).
- `pdf_gen.py` : ReportLab Platypus (page de garde, titres, tableaux grillagés, code, pagination
  « JejeGPT — page N »), échappement HTML systématique (`&`, `<`, `>`).
- `service.py` : `create_export()` = validation format + longueur d'entrée + build `BytesIO` +
  `check_size`/`check_quota` + sauvegarde `FileField` ; `detect_format()` utilitaire.
- Tests : binaires réellement ré-ouverts (`Document`/`load_workbook`/`Presentation`/`%PDF-`),
  sanitization `'=1+1` → `'=1+1` littéral, refus du vide.

### Phase 4 — Branchement chat + UI : OK (6 tests, puis 10 avec la recette)
- Vues : `export_create` (`POST messages/<mid>/export/<fmt>/` → 302 vers le téléchargement,
  erreur via `django.contrib.messages`), `export_download` (`GET exports/<uuid>/` →
  `FileResponse attachment` avec `file_name`).
- Helper `_messages_ctx()` (avec `prefetch_related("exports")`) mutualisé sur les 5 rendus du thread.
- `thread.html` : boutons **Exporter : pdf · docx · xlsx · pptx** sous chaque réponse + liens
  `⬇ <nom>` vers les exports existants.
- Onglet Données : compteurs exports/taille + liste des 20 derniers avec liens.
- Correction en cours de phase : filtre `split` inexistant en templates Django → formulaires explicites.

### Phase 5 — Vérification finale : OK
- `python manage.py check` : 0 problème.
- `makemigrations --check --dry-run` : `No changes detected`.
- `ruff check chat config` : propre (2 lignes >120 et imports triés corrigés en cours de route).
- `pytest -q` : **108 passed** (86 baseline préservés + 22 nouveaux), dont recette paramétrée
  bout-en-bout sur les 4 formats (création POST → téléchargement → magic bytes + `attachment`).

## 4. Fichiers créés / modifiés

Créés : `chat/exports/{document,core,docx_gen,xlsx_gen,pptx_gen,pdf_gen,service}.py`,
`chat/signals.py`, `chat/management/commands/purge_exports.py`,
`chat/migrations/0008_add_export.py`, `chat/tests/{test_exports,test_generators,test_export_views}.py`,
ce rapport.
Modifiés : `config/settings.py`, `chat/{models,admin,apps,views,urls}.py`, `requirements.txt`,
`templates/chat/partials/{thread,settings_data}.html`.

## 5. Utilisation

1. `.\.venv\Scripts\Activate.ps1`, `python manage.py migrate` (déjà appliqué en local), `runserver`.
2. Dans une conversation, sous une réponse : **Exporter → pdf/docx/xlsx/pptx**, puis `⬇` le fichier.
3. Onglet Paramètres → Données : exports récents + compteurs.
4. Purge : `python manage.py purge_exports` (dry-run) puis `--apply`.
5. Limites : `JEJEGPT_EXPORTS_{MAX_INPUT_CHARS,MAX_BYTES,QUOTA_BYTES,RETENTION_DAYS}`.

## 6. Sécurité (garde-fous implémentés et testés)

- Téléchargements toujours `attachment` (jamais `inline`).
- Formules Excel neutralisées (`= + - @` + tab/CR en tête → préfixe `'`).
- Pas de HTML/XXE : parseur sans HTML, échappement ReportLab, pas de conversion HTML→PDF.
- Noms assainis (accents gardés, `/ \ ..` éliminés), UUID non devinables, quota + taille max,
  fichiers supprimés avec leurs lignes (signal `on_commit`).

## 8. V2 — Déclenchement en langage naturel (18/09/2026)

Constat (capture) : « Génère moi un fichier pdf… » recevait « je ne peux pas joindre de PDF »
+ un script `fpdf2` à exécuter à la main. Corrigé :
- `service.detect_export_request()` : format (pdf/docx/xlsx/pptx, + word/excel/powerpoint/
  présentation/tableur) **et** verbe d'export (génère, crée, fais, prépare, fichier, export…).
  « C'est quoi un pdf ? » → `None` (pas de faux positif).
- `_maybe_auto_export()` branché sur `message_send` (POST) et `message_stream` (SSE) :
  le fichier est généré depuis la réponse et son lien `⬇` apparaît dessous. Idempotent par
  format, silencieux en échec (boutons manuels en repli), rien sur erreur provider ni régénération.
- Consigne ajoutée au system prompt par défaut : ne jamais dire « je ne peux pas joindre de
  fichier », produire directement le contenu Markdown (source de l'export).
- Tests : `test_auto_export.py` (18 cas : détection ×13, send ±intention, erreur provider,
  régénération, SSE, idempotence). Suite : **126 passed**, `ruff`/`check` verts.
- Note : les conversations existantes gardent leur ancien system prompt → **démarrer une
  nouvelle conversation** pour en bénéficier.

## 7. Non vérifié / limites / risques résiduels
- Génération **synchrone** : un très gros export bloque la requête (SQLite, pas de worker) —
  borné par `MAX_INPUT_CHARS`/`MAX_BYTES`, à passer en tâche de fond si besoin.
- Alertes LSP/pyright visibles dans l'éditeur = **faux positifs préexistants** (config sans
  plugin `django-stubs`, cf. README §LSP) ; la vérité = `check` + `pytest` + `ruff`, tous verts.
- Lot 2 hors périmètre : `pandas`, `Pillow` avancé, `matplotlib`, SVG/ZIP, HTML→PDF.

## 9. Tâches planifiées style ChatGPT (18/09/2026)

« Fais-moi un résumé des actualités tech tous les jours à 10h » → tâche auto, run à 10h,
résultat dans une conversation dédiée + pastille non-lu sur « Planifiées ».

- **Modèles** (`0010_scheduled_tasks`, migré) : `ScheduledTask` (nom, consigne, fréquence
  daily/weekly/once, jours, heure, once_at, provider/modèle, web_search, enabled, conversation
  OneToOne, failures, next_run) + `ScheduledRun` (tâche, message, succès, erreur, seen).
  Settings `JEJEGPT_SCHEDULER_{ENABLED,TICK_SECONDS=30,MAX_TASKS=20,MAX_FAILURES=3}`.
- **Parsing 100 % LLM** (`scheduler/parse.py`) : JSON strict validé (fréquence, HH:MM, jours 0-6,
  once_at futur) ; incompréhensible → message de clarification, jamais de tâche. Web auto si
  « actus/actualités/news » et modèle compatible.
- **Moteur** (`scheduler/engine.py`) : `compute_next_run` (strictement > now, Europe/Paris),
  `run_due_tasks` + `run_task_now`, claim anti-doublon par UPDATE conditionnel, échecs → run
  d'erreur, auto-disable à 3, manqués sautés (once dépassé = désactivé sans exécuter).
  Ticker thread daemon démarré dans `ChatConfig.ready()` ssi runserver (enfant reload ou
  --noreload), jamais sous pytest/autres commandes — recette : thread `jejegpt-scheduler` vivant.
- **Chat** : détection d'intention (`tous les jours`, `rappelle-moi`, `planifie`…) dans
  `message_send` + `message_stream` → tâche créée **en pause** + message de confirmation avec lien
  `/planifiees/` (pas d'activation surprise = pas de coût LLM caché).
- **UI** : page `/planifiees/` (liste, résumé humain, prochain run, activer/couper, exécuter
  maintenant, supprimer, historique, formulaire manuel), sidebar « Planifiées » cliquable avec
  pastille non-lu, ouverture de la conversation = marqué lu.
- **Tests** : `test_scheduled_{parse,engine,views}.py` (38 cas). Suite : **167 passed**,
  `ruff`/`check`/`makemigrations --check` verts.
- **Limites** : tourne seulement quand JejeGPT est lancé ; pas de cron libre ni intervalles
  < 1 jour ; une seule heure par tâche ; pas de quota mensuel (chaque run = appel facturé).

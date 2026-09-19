# JejeGPT — clone ChatGPT local (Django + HTMX + Tailwind + Preline + Alpine)

## Environnement (première fois)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Usage quotidien : activer le `.venv` (`.\.venv\Scripts\Activate.ps1`), puis les commandes
ci-dessous. Sans activation, préfixer avec `.\.venv\Scripts\python.exe`.

## Démarrage
```powershell
python manage.py migrate
python manage.py runserver
```
Ouvrir http://127.0.0.1:8000 — trois providers sont préconfigurés (clés vides,
à saisir dans ⚙️ Paramètres → Providers et modèles, onglet accessible via
l'engrenage sur la carte utilisateur) :
- OpenRouter : `https://openrouter.ai/api/v1` — 100 % compatible, clé `sk-or-...` sur openrouter.ai
- OpenCode Zen : `https://opencode.ai/zen/v1` — passerelle, clé sur opencode.ai/zen
- OpenCode Go : `https://opencode.ai/zen/go/v1` — abonnement $10/mois, clé Zen/Go
- Local : ajoutez LM Studio (`http://localhost:1234/v1`) ou Ollama (`http://localhost:11434/v1`)

⚠️ Zen/Go exposent plusieurs formats d'API, tous supportés : `chat/completions`
(GLM, Kimi…), `/responses` (Muse Spark, GPT, Grok) et `/messages`
(Claude, Qwen Plus/Max, MiniMax). La route est détectée via le cache
(`Actualiser`) + table de secours (doc OpenCode du 17/09/2026, peut se périmer) ;
le picker badge chaque modèle (`/responses`, `/messages`).
Si `Actualiser` ne liste rien, ajoutez le `model_id` manuellement
(champ « + » sous chaque provider).

Note : les modèles thinking (ex. muse-spark) répondent après un bloc `reasoning`
ignoré à l'affichage ; un champ `"error": null` dans une réponse `completed`
n'est pas une erreur.

## Go/Zen : headers, limites, console
- Chaque requête chat envoie `User-Agent: JejeGPT/1.0` + `x-opencode-session`
  (= id de la conversation, stable) comme l'exige la doc Go (routage, cache, anti-abus).
- Limites Go : plafonds $ par modèle et par mois (5h = 20 %, hebdo = 50 %,
  mensuel = 100 %). Au-delà → requêtes bloquées : le message 429 indique la
  console (opencode.ai/auth), les modèles gratuits (`union-alpha`, illimité)
  et l'option *Use balance* (crédits Zen). Suivi conso : console uniquement.

Puis **Actualiser** pour lister les modèles (`GET /models`), choisir provider + modèle,
écrire un message et **Envoyer** : les tokens **streament en direct dans le thread**
(SSE `/conversations/<id>/stream/?prompt=...`, bouton ⏹ Stop, ↻ Régénérer).
Sans JS : repli POST bloquant.

## Comportement du stream (style ChatGPT)
- À l'envoi, la question remonte en haut ; les tokens suivent **seulement si tu
  es déjà en bas** (sticky) ; remonter coupe le suivi, pastille « ↓ Dernière
  réponse » pour y retourner ; fin de stream sans saut si tu lis plus haut.
- Icônes effort/web/réflexion **toujours visibles** : actives si supportées,
  grisées sinon (« non supporté » si connu, « non vérifié » + lien Paramètres
  sinon — ex. modèle inconnu comme omen-alpha). Thinking non coupable sur
  Responses (tooltip dédié) ; web actif sur toute la route Responses.

## Paramètres (`/parametres/`, engrenage sur la carte user)
Onglets fonctionnels : **Général** (infos espace local, **modèle par défaut**
appliqué aux nouvelles conversations), **Personnalisation**
(titre/température/system prompt de la conversation courante),
**Providers et modèles** (ajout, Actualiser `/models`, activer/désactiver,
modification URL/clé — champ vide = inchangée, suppression ; clé jamais réaffichée),
**Gestion des données** (stats, export JSON, tout effacer en gardant les providers),
**Utilisation** (KPI, heatmap, par modèle, dernières requêtes).
Autres onglets : badge « Bientôt ».
**Style des réponses** : les nouvelles conversations héritent d'un system prompt
imposé (Markdown structuré : titres, listes, tableaux — `JEJEGPT_DEFAULT_SYSTEM_PROMPT`,
vide = aucun) ; bouton « ✨ Style structuré » dans Personnalisation pour les anciennes.

## Options modèle (effort, recherche web, réflexion)
- Le composer affiche **toujours les 3 icônes** : actives si supportées,
  grisées sinon (tooltip : « non supporté » ou « capacités non vérifiées —
  à déclarer dans Paramètres → Providers »). Fini les disparitions mystères.
- Effort (Auto/low/medium/high/xhigh, pastille persistante), recherche web
  (toggle, outil natif Responses ; actif sur toute la route Responses depuis
  le 17/09/2026), réflexion (toggle route Messages uniquement — non coupable
  via l'API Responses, le tooltip l'explique).
- Mapping : Responses → `reasoning.effort` (+ outil `web_search`) ;
  Messages → `thinking` budget (`disabled` si coupé, temperature forcée à 1 si actif)
  + outil web ; route chat : aucun paramètre standard (contrôles masqués).
- Source : table statique datée du 17/09/2026 appliquée **à la création** du cache,
  réglable par modèle dans Paramètres → Providers (cases effort/web/réflexion,
  jamais écrasées par Actualiser). Options stockées par conversation.

## Métriques d'usage (tokens, vitesse, durées)
- Capturées à chaque réponse sur les 3 routes (sync + stream) : entrée, sortie,
  total, durée aller-retour, succès/erreur. Stream chat : `stream_options.include_usage`
  envoyé ; sans `usage` provider, les compteurs restent à 0 (normal).
- Définitions : entrée = prompt + historique envoyés (facturés) ; sortie = texte
  généré (facturé) ; vitesse = sortie / durée, mesurée côté client ; les chiffres
  provider (console) peuvent différer légèrement. Pas d'estimation $.
- Conservation intégrale (lignes minuscules) ; pages : onglet Utilisation
  (période 7/30/90 j) + dernière réponse sous le composer (tooltips inclus).

## Vérifications
```powershell
python manage.py check
python -m ruff check chat config
python -m pytest chat/tests/test_chat.py -q
```

## Notes techniques
- Tailwind via CDN : l'avertissement « should not be used in production » est
  normal (app locale, pas de build). Coloration : highlight.js **build
  navigateur** (cdnjs `highlight.min.js` — le build `lib/common` de jsdelivr
  exige `require` et crash dans le navigateur).

## Note LSP (alertes de types)
Sélectionnez l'interpréteur du `.venv` dans l'éditeur (VS Code : `Python: Select Interpreter`
→ `.venv`). `django-stubs` est installé et `pyrightconfig.json` pointe vers le `.venv` :
zéro erreur attendue. Les relations inverses sont annotées via `RelatedManager`
(import `TYPE_CHECKING`, sans effet runtime) et `Conversation.get_absolute_url()` existe :
si une alerte persiste, c'est l'éditeur qui n'utilise pas le `.venv`, pas le code.

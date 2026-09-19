# JejeGPT — ce qui est transposable des plateformes (ChatGPT, Claude, Mistral, Gemini, Perplexity, Kimi, Grok, Qwen, DeepSeek)

Analyse de faisabilité au **18/09/2026**, à partir de la cartographie des ~50 fonctions
concurrentes. Contexte projet : **Django local-first, mono-utilisateur, SQLite**,
providers OpenAI-compatibles (chat / Responses / Messages), exécution synchrone + SSE,
planificateur in-process, exports Office locaux.

Légende des verdicts : ✅ déjà présent · 🟢 faisable (s'appuie sur l'existant) ·
🟡 faisable mais coûteux · 🔴 déconseillé / hors périmètre local.

## 1. Déjà présent dans JejeGPT

| Fonction concurrente | État JejeGPT |
| --- | --- |
| Chat conversationnel, stream, stop, régénérer | ✅ SSE + repli POST, suivi de lecture, pastille « dernière réponse » |
| Sélecteur de modèles multi-providers | ✅ cache `/models` + ajout manuel + badges de route |
| Thinking / effort configurable | ✅ effort Auto→xhigh, réflexion Messages, pastilles de support |
| Recherche Web + citations cliquables | ✅ toggle natif Responses, consigne « Sources : » sur les tâches web |
| Feedback pouces + métriques d'usage | ✅ feedback, tokens, vitesse, heatmap, par modèle |
| Création de fichiers (DOCX/PDF/XLSX/PPTX) | ✅ service d'exports + auto-export sur réponses et tâches |
| Tâches planifiées | ✅ quotidien/hebdo/une fois, run manuel, brouillon, duplication, relance, reset échecs, historique, pastille non-lus |
| Consignes réutilisables (ancêtre des Skills) | ✅ gabarits `{{variables}}` + assistant de remplissage + seed « Recherche approfondie » |
| UI blanche type ChatGPT + mobile + taille de texte | ✅ tiroir mobile, paliers 87–125 %, police Roboto |

## 2. Transposable à court terme (P0 — quick wins)

| Fonction | Commentaire | Effort |
| --- | --- | --- |
| **Projets** (conversations + instructions partagées) | Modèle `Project`, FK conversation, instructions injectées en system prompt. Pas de fichiers dans un 1er temps. | Moyen |
| **Mémoire inter-conversations** | Table de faits persistants éditable, injectée en system. Attention au gonflement du contexte : limite + purge. | Petit |
| **Deep Research simplifiée** | Boucle N appels (recherche → lecture → reformulation → synthèse) via l'outil web natif + gabarit dédié. Bornes anti-coût indispensables. | Moyen |
| **Tâches mensuelles + rattrapage PC éteint** | Extension du moteur d'échéances (`compute_next_run`) + politique catch-up au démarrage. | Petit–moyen |
| **Monitoring / veille avec état** | Tâche planifiée + hash/extraits du run précédent, signalement des nouveautés uniquement. | Moyen |
| **Déclenchement « sur événement » local** | Pas de vrais webhooks en local : polling via le ticker existant (surveillance URL/RSS/fichier). Le présenter comme tel. | Moyen |
| **Recherche restreinte à certains sites** | Consigne + gabarit (ex. `site:` imposés), sans nouveau code ou presque. | Petit |
| **Réflexion sur l'usage (type Reflection)** | Étendre l'onglet Utilisation existant (catégories, tendances). Données déjà collectées. | Petit |
| **Quiz / infographies / résumés** | Via LLM + exports existants (pas de moteur dédié). | Petit |

## 3. Transposable à moyen terme (P1)

| Fonction | Commentaire | Effort |
| --- | --- | --- |
| **Skills « prompt packs »** | Généraliser les gabarits (versionnés, partageables en JSON). Les skills avec scripts restent sans exécution : documentation seule. | Moyen |
| **Client MCP** | Connecter outils locaux/standard via MCP en Python. Auth et sandbox à cadrer ; commencer en lecture seule. | Moyen–élevé |
| **Connecteurs (Gmail, Drive, GitHub…)** | OAuth + tokens + callbacks localhost : lourd pour un mono-user local ; viser 1–2 connecteurs max, lecture seule d'abord. | Élevé |
| **Analyse CSV/Excel via LLM** | Upload + injection d'extraits (pas d'exécution). Les gros fichiers exigent chunking. | Moyen |
| **Génération d'images** | Possible si un provider exposé le supporte (nouveau pipeline binaire, pas de texte). Vérifier modèle par modèle. | Moyen |
| **Coding assistant lecture seule** | Explorer repo, expliquer, proposer des patchs à appliquer à la main. Sans exécution shell : sûr et utile. | Moyen |
| **Monitoring de prix/pages** | Cas particulier du monitoring (P0) : polling + seuil + pastille. | Moyen |

## 4. Déconseillé / hors périmètre (P2 ou non)

| Fonction | Pourquoi |
| --- | --- |
| Computer use / navigateur autonome / Playwright piloté | Fragile, coûteux en tokens, surface de sécurité énorme pour un poste local |
| Agents persistants 24/7, swarm multi-agents (300 sous-agents) | Architecture distribuée + coûts : incompatible avec un butter local SQLite et un ticker in-process |
| Sandbox d'exécution de code (interpréteur Python/TS) | Risque d'exécution arbitraire ; exigerait conteneur/désamorçage — démesuré ici |
| Exécution shell/git distante, cloud workers | Suppose infra + auth + isolation ; le projet est volontairement sans déploiement |
| Voix temps réel / caméra / partage d'écran live | Temps réel + modèles spécialisés + pérmissions navigateur : nouveau produit |
| Génération vidéo, Audio Overviews, RAG NotebookLM-like | Modèles/infra propriétaires Google introuvables via providers génériques |
| Finances/Santé via Plaid & co, bases premium | OAuth lourds + données sensibles + conformité : à éviter en local sans besoin métier |
| Collaboration multi-utilisateurs, partage, marketplace | Le projet est mono-utilisateur local ; l'export JSON/fichiers couvre le besoin |
| Mémoire projet type RAG 500 PDF | Index vectoriel + embeddings : pile supplémentaire (pgvector ou lib locale) ; envisager seulement si besoin réel |

## 5. Feuille de route recommandée

1. **P0a** — Projets + mémoire (socle de tout le reste, réutilise `AppSettings`/conversations).
2. **P0b** — Deep Research simplifiée + gabarit dédié (différenciant, vitrine « agent »).
3. **P0c** — Tâches mensuelles, rattrapage au démarrage, monitoring avec diff (crédibilise les planifiées).
4. **P1** — Skills packs partageables, puis **un seul** connecteur en lecture seule (GitHub ou Gmail) pour valider le pattern avant tout OAuth supplémentaire.
5. **Jalons d'arrêt** : si ni RAG ni exécution ne sont demandés par l'usage réel, ne pas les construire (coût/risque sans bénéficiaire).

## 6. Risques transverses à garder en tête

- **Coûts** : toute boucle agentique (deep research, monitoring fréquent, swarm) multiplie les appels ; ajouter plafonds + confirmation au-delà d'un seuil.
- **Contexte** : mémoire/projets/RAG gonflent le prompt ; prévoir troncature et compteurs visibles.
- **Scheduler** : l'in-process meurt avec le PC (assumé) ; le rattrapage au démarrage doit rester explicite (« échéance manquée exécutée / sautée »).
- **Secrets** : clés providers jamais réaffichées (déjà le cas) ; tout connecteur OAuth doit suivre la même règle + chiffrement au repos avant usage sérieux.
- **SQLite** : suffit pour l'usage prévu ; un passage vectoriel ou multi-worker imposerait PostgreSQL — à ne décider que sur besoin mesuré.

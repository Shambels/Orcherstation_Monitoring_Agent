# Langfuse Explorer

Kit d'exploration autonome de l'interface **Langfuse v4**. C'est l'annexe de la formation « Orchestration d'agents & Monitoring : LangGraph + Langfuse ».

Le kit démarre un Langfuse **seul**, sans LLM, sans GPU et sans Qdrant. Il y injecte sept jours d'activité d'un helpdesk propulsé par des agents. Les traces sont synthétiques mais réalistes, et **identiques sur tous les postes**. Le guide `guide/index.html` sert ensuite à explorer l'interface écran par écran, à travers 33 missions dont les réponses sont vérifiées.

| | |
|---|---|
| Langfuse | 4.46.0 auto-hébergé, mode v4 (`events_only`) |
| SDK Python | langfuse 4.15.6, OpenTelemetry 1.45.0 (mêmes versions que le cours) |
| Machine | Docker, 8 Go de RAM minimum, 16 Go conseillés, environ 6 Go de disque. **GPU inutile.** |
| Réseau | Nécessaire au premier lancement (images Docker, paquets Python), puis inutile |

## Démarrer

Prérequis : **Docker Desktop** (ou Docker Engine) démarré, et **uv**.

```powershell
# Windows (PowerShell)
.\start.ps1
# si les scripts sont bloqués :
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

```bash
# Linux / macOS / WSL
./start.sh
```

Le script enchaîne quatre étapes :
1. Il génère `.env` avec des secrets aléatoires propres au poste.
2. Il démarre Langfuse. Le premier démarrage prend 2 à 4 minutes.
3. Il attend que Langfuse réponde.
4. Il injecte les données de démonstration.

À la fin, il affiche l'adresse (**http://localhost:3100**), le compte (`explorer@helpdesk.local`) et le mot de passe, puis ouvre le guide.

> Première chose à faire dans l'UI : **régler la période sur 7 jours**. Par défaut, elle ne couvre qu'une journée.

| Commande | Effet |
|---|---|
| `start -Reset` / `--reset` | Repart de zéro : supprime **uniquement** les volumes du projet `langfuse-explorer` |
| `start -NoSeed` / `--no-seed` | Démarre sans injecter de données |
| `start -Stop` / `--stop` | Arrête le kit (les données sont conservées) |
| `docker compose down -v` | Supprime le kit et ses données |

## Cohabitation avec la stack du cours

Le kit est **isolé** de la stack `infrastructure/` du cours :
- projet Compose `langfuse-explorer`, donc des volumes et un réseau distincts ;
- interface sur le port **3100** (le cours utilise le 3000) ;
- aucun autre port publié, à part MinIO sur `127.0.0.1:9190`.

Les deux peuvent tourner en même temps. Avec moins de 16 Go de RAM, mieux vaut en arrêter un.

## Contenu

```
langfuse-explorer/
├── start.ps1, start.sh        démarrage en une commande
├── docker-compose.yml         Langfuse 4.46.0 + Postgres, ClickHouse, Redis, MinIO
├── generate_env.py            secrets aléatoires + clés API pré-provisionnées
├── seed/
│   ├── seed_langfuse.py       injection (idempotente, options --dry-run, --compresser)
│   ├── moteur.py              construction des traces → spans OpenTelemetry
│   ├── scenarios.py           assemblage déterministe + faits attendus
│   ├── scenarios/*.yaml       LES DONNÉES : référentiel, signatures, trafic, expériences
│   └── data/                  jeu d'évaluation et articles KB du cours (fictifs)
├── guide/
│   ├── index.html             le guide à distribuer (hors ligne, un seul fichier + screens/)
│   ├── screens/               captures générées par tools/capture_screens.py
│   └── source/                ecrans.yaml, missions.yaml, template.html
├── formateur/                 NE PAS DISTRIBUER : solutions.html, faits.json
└── tools/
    ├── verify.py              vérifie que l'instance contient ce que les missions attendent
    ├── capture_screens.py     régénère les captures (Playwright)
    ├── build_guide.py         régénère guide/index.html et le corrigé
    └── test_seed_hors_ligne.py teste le seed sans Langfuse (faux collecteur OTLP)
```

## Ce que contient le seed

Il injecte 134 traces (124 en production, 10 en formation) et 1 491 observations, plus 60 traces d'expériences. Elles sont réparties sur 7 jours.

| Scénario | Notion travaillée |
|---|---|
| Trace nominale VPN (KB-001) | lire un arbre d'observations, retriever, prompt lié |
| Incident ERP P1 : interrupt, validation N2, reprise | sessions, human-in-the-loop, supervision humaine (AI Act) |
| Ping instable : 2 timeouts puis succès | WARNING, retries, timeline |
| Boucle de routage → `GraphRecursionError` | trace en ERROR, prompt fautif (v2), commentaire |
| Erreur d'outil 503 avec repli | observation en ERROR ≠ trace en échec |
| Données personnelles en clair, puis masquées | RGPD (minimisation, masquage avant l'envoi) |
| Injection détectée, puis injection indirecte réussie | garde-fous, score `securite`, incident NIS2 |
| Synthèse sur gemma4:12b avec 58 000 tokens | FinOps, prix des modèles locaux |
| Plan B Ollama sur CPU | latence, métadonnées d'infrastructure |
| Conversation Teams sur 4 tours | session, pouce baissé puis levé |
| Release v1.4.0 avec le prompt v2 | régression détectable par release (dashboards) |
| Dataset de 20 cas et 3 expériences | comparer 8b et 3b, prompt v2 et v3 |

Les scénarios sont **des données** (`seed/scenarios/*.yaml`). Pour en ajouter un : modifier le YAML, puis `uv run --project seed tools/build_guide.py`. Ce script échoue si une réponse de mission ne correspond plus aux données.

## Procédure formateur (avant de distribuer)

```bash
./start.sh                                                      # ou .\start.ps1
uv run --project seed tools/verify.py                           # 18 contrôles au vert
uv run --project seed --group captures playwright install chromium
uv run --project seed --group captures tools/capture_screens.py # captures réelles du guide
```

Ensuite, distribuer le dossier **sans** `formateur/`, `.env` ni `seed/_dry_run.json`. Un exemple de commande d'archive est donné en fin de fichier.

## Dépannage

| Symptôme | Cause probable et solution |
|---|---|
| Tableau vide dans Tracing | La période est sur 24 h : passer sur **7 jours** |
| `Clés refusées par Langfuse` | `.env` régénéré après le premier démarrage : relancer avec `-Reset` / `--reset` |
| Coûts à 0 $ | Les prix des modèles ont été déclarés après les traces : relancer avec `-Reset` / `--reset` |
| Traces absentes plus de 2 min après le seed | Ingestion antidatée refusée par une future version de Langfuse : `uv run --project seed seed/seed_langfuse.py --compresser` après un `--reset` (toutes les traces sur les 6 dernières heures) |
| Le Playground ne répond pas | Normal : aucune connexion LLM en mode rejeu |
| Port 3100 occupé | Changer `EXPLORER_PORT` et `LANGFUSE_BASE_URL` dans `.env` |
| `uv` introuvable | Windows : `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |

## Sécurité et conformité du kit

- Les secrets sont générés localement à chaque installation. Aucun secret n'est présent dans l'archive.
- Postgres, ClickHouse et Redis ne sont pas exposés sur l'hôte. MinIO est lié à `127.0.0.1`.
- La télémétrie de Langfuse est désactivée. Le seed ne contacte que `localhost`.
- Les données sont 100 % fictives. Les identifiants utilisateurs sont pseudonymisés, et l'IBAN est l'exemple officiel de la norme.
- Toutes les versions sont figées (images, `uv.lock`, version affichée dans le guide). Une montée de version passe par `verify.py` puis `capture_screens.py`.

```bash
# Archive à distribuer (depuis le dossier parent)
tar --exclude='langfuse-explorer/formateur' --exclude='langfuse-explorer/.env' \
    --exclude='langfuse-explorer/seed/.venv' --exclude='langfuse-explorer/seed/_dry_run.json' \
    -czf langfuse-explorer.tgz langfuse-explorer
```

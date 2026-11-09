# Votre parcours d'autonomie

La formation s'arrête, HelpDesk-Ops non. Tout ce qui a été vu vite, ou pas du tout, est dans ce dépôt : notebooks
exécutés avec leurs sorties, exercices et corrigés, fiches de référence (🔎) dans les annexes des decks, et les
étapes de la construction pas à pas, distribuées pendant la formation.

## Avant de commencer

```bash
cd Orchestration                       # la racine du cours
git pull                               # les dernières corrections
(cd infrastructure && uv run smoke_test.py)   # la stack répond-elle ?
uv run --project infrastructure jupyter lab   # les notebooks
```

Démarrez la stack (Langfuse, Qdrant, le modèle) comme pendant la formation. Tout reste local : aucun appel à un
service extérieur.

## La méthode

1. **Lire** le notebook, cellule par cellule, en exécutant.
2. **Refaire** l'exercice **sans** ouvrir le corrigé (`exercices/enonces/`). Bloqué plus de 20 minutes : la fiche 🔎
   de l'annexe, puis seulement le corrigé (`exercices/corriges/`).
3. **Comparer** avec l'étape de construction correspondante (les fichiers `etape_NN_*.py` distribués pendant la
   formation) : dans VS Code, clic droit sur l'étape 3 › *Select for Compare*, puis sur l'étape 4 › *Compare with
   Selected* montre ce que l'étape 4 ajoute. Les blocs ajoutés sont marqués `# ═══ NOUVEAU`.
4. **Regarder** la trace dans Langfuse à chaque lancement : le dessin, l'exécution et la trace racontent la même histoire.

## 1. Consolider : ce qu'on a vu vite (dans cet ordre)

| # | Sujet | Notebook | Exercice | Construction |
|---|---|---|---|---|
| 1 | StateGraph : état, reducers, arêtes conditionnelles, `ToolNode` | `J1_03_stategraph` | Ex1.1 (corrigé) | étape 3, défi 3 |
| 2 | Persistance : checkpointers, `thread_id`, voyage dans le temps, purge RGPD | `J2_01_persistance` | mini-défi J2-01 | étape 4 |
| 3 | Human-in-the-loop : `interrupt()`, `Command(resume=…)`, idempotence, `HumanInTheLoopMiddleware` | `J2_02_human_in_the_loop` | Ex2.1 (corrigé) | étape 5 |
| 4 | Sortie structurée : Pydantic, `with_structured_output`, `Command(goto=…)` | `J2_03_sortie_structuree` | — | étape 6a |
| 5 | Robustesse : `RetryPolicy`, `TimeoutPolicy`, `error_handler`, sous-graphes | `J2_04_robustesse` | Ex2.2 (corrigé) | étape 6 |
| 6 | RAG hybride sur Qdrant, spécialistes exposés comme outils | `J3_02_rag_et_sous_agents` | Ex3.1 (corrigé) | étape 7 |

Les points 1 à 6 rendent tout le reste lisible : ne les sautez pas.

Pour le point 3, commencez par l'animation `comprendre/interrupt_reprise.html` : elle montre pas à pas la pause, le checkpoint retrouvé par le `thread_id` et la reprise par une autre personne.

## 2. Aller plus loin : ce qu'on n'a pas fait ensemble

| # | Sujet | Notebook | Exercice |
|---|---|---|---|
| 7 | Les 5 patterns multi-agents : routeur, sous-agents, handoff, skills | `J3_01_patterns_multi_agents` | — |
| 8 | MCP : écrire un serveur FastMCP, le consommer, la liste blanche | `J3_04_mcp` | — |
| 9 | Datasets et experiments : comparer des variantes, comparer des modèles | `J4_05_experiments_dashboards` | Ex4.1 (corrigé) |
| 10 | Scores et juge LLM : retours utilisateurs, fidélité, calibration | `J4_04_scores_llm_juge` | — |
| 11 | Middleware, liste blanche et journal d'audit, red-team OWASP mesurée | `J5_01_middleware`, `J5_02_red_team` | Ex5.1 (corrigé) |
| 12 | Au besoin : parallélisme `Send`, Deep Agents | `J3_03_parallelisme_send`, `J3_05_deep_agents_bilan` | — |
| 13 | Au besoin : observabilité par l'API metrics, RGPD des traces, prompts versionnés | `J4_01` à `J4_03` | — |
| 14 | Au besoin : conformité (AI Act, RGPD, NIS2), gate d'évaluation en CI, `langgraph dev` | `J5_03_conformite`, `J5_04_devops_projet_final` | — |

**Examen blanc :** le mini-projet « HelpDesk-Ops parle néerlandais »
(`exercices/enonces/MiniProjet_J4_neerlandais.ipynb`). Il combine garde-fou, graphe, dataset et sécurité ; un corrigé
existe, ne l'ouvrez qu'à la fin.

## 3. Votre propre projet, avec votre assistant IA

[`guide_ia/`](guide_ia/README.md) : définir votre projet (`modele_PROJET.md`), configurer votre assistant IA
(`modele_AGENTS.md`, documentation à jour par MCP), puis les étapes 0 à 8 avec, pour chacune, le choix recommandé
et sa raison.

## Les réflexes à garder

- **Dessiner, exécuter, observer** : `draw_ascii()` avant de lancer, puis la trace Langfuse après.
- **Un format valide n'est pas une décision juste** : une règle explicable en Python garde le dernier mot (plancher d'urgence).
- **Rien qui touche le monde extérieur avant `interrupt()`** : le nœud repart du début à la reprise.
- **Un outil n'est donné qu'à qui en a besoin** ; un serveur MCP passe par une liste blanche côté client.
- **Ce qui n'est pas mesuré n'est qu'une impression** : un changement de prompt, de modèle ou de règle repasse par
  l'experiment, et on compare les runs.

## Ressources

- LangGraph et LangChain : <https://docs.langchain.com>
- Langfuse : <https://langfuse.com/docs>
- OWASP Top 10 for Agentic Applications : <https://genai.owasp.org>
- Model Context Protocol : <https://modelcontextprotocol.io>

# AGENTS.md : règles pour l'assistant IA de ce projet

> Modèle : copiez ce fichier à la racine de votre projet sous le nom `AGENTS.md` et adaptez la section « Projet ».
> Claude Code : ajoutez un `CLAUDE.md` contenant la seule ligne `@AGENTS.md`.
> GitHub Copilot (chat) : `.github/copilot-instructions.md` contenant « Suis les règles de AGENTS.md ».

## Projet

- La description complète, la décision à prendre, les cas de test et l'avancement sont dans `PROJET.md`. Lis-le
  avant chaque tâche ; mets à jour sa section « Avancement » à la fin d'une étape.
- Pile : Python 3.12, `uv`, LangChain 1.x, LangGraph 1.x, Langfuse (SDK v4, auto-hébergé), Pydantic 2.
- Modèle : <Ollama / vLLM, nom du modèle>, obtenu **uniquement** par `get_llm()` dans `<chemin>`.

## Méthode

1. **Plan avant le code.** Pour toute tâche de plus de quelques lignes, propose un plan court (fichiers, nœuds,
   outils, changements de l'état) et attends ma validation.
2. **Une étape à la fois**, dans l'ordre de `PROJET.md`. Ne commence pas l'étape suivante sans qu'on le décide.
3. **Vérifie les API dans la documentation** (serveurs MCP LangChain et Langfuse, ou
   `https://docs.langchain.com/llms.txt` et `https://langfuse.com/llms.txt`) avant de les utiliser. Ta mémoire
   de ces bibliothèques peut être dépassée. Si tu n'as pas pu vérifier, dis-le.
4. **Exécute pour de vrai** : chaque étape se termine par une exécution, `draw_ascii()` du graphe, et l'indication
   de ce qu'il faut regarder dans Langfuse. Ne dis jamais « ça devrait marcher ».
5. Marque le code ajouté à une étape par `# ═══ NOUVEAU (étape N)`.
6. En cas d'erreur : donne une hypothèse et comment la vérifier **avant** de corriger. Pas de correctifs en rafale.

## API à utiliser (versions 1.x) et API interdites

| Besoin | ✅ Utiliser | ❌ Ne pas utiliser (ancien) |
|---|---|---|
| Agent | `from langchain.agents import create_agent` | `initialize_agent`, `AgentExecutor`, `create_react_agent` de `langgraph.prebuilt` |
| Chaîne | LCEL : `prompt \| llm \| parser` | `LLMChain`, `SequentialChain` |
| Graphe | `from langgraph.graph import StateGraph, START, END, MessagesState` | `MessageGraph`, `set_entry_point` si `START` suffit |
| Pause humaine | `from langgraph.types import interrupt, Command` | `interrupt_before` sur un nœud qui a déjà agi |
| Robustesse | `RetryPolicy`, `TimeoutPolicy` (`langgraph.types`) | des `try/except` qui avalent l'erreur |
| Sortie structurée | `llm.with_structured_output(Schema, method="json_schema")` | parser du JSON à la main dans du texte |
| Traces | `from langfuse.langchain import CallbackHandler` ; `from langfuse import observe, propagate_attributes, get_client` | `langfuse.callback`, `langfuse.decorators` (SDK v2) |
| Évaluation | `dataset.run_experiment(...)`, `create_score(...)` | boucles maison sans dataset |

## Règles non négociables

- **Versions** : ne modifie jamais `pyproject.toml` ou `uv.lock` sans me le demander.
- **Secrets** : aucune clé dans le code ni dans les logs ; tout passe par `.env` (ignoré par git).
- **Effets de bord** : aucune action sur le monde réel (écriture, envoi, création) **avant** un `interrupt()` ;
  le nœud est réexécuté depuis le début à la reprise.
- **Consignes au modèle** : dans le **prompt**. Avec `method="json_schema"`, le schéma contraint la forme mais le
  modèle ne lit pas les `Field(description=…)`.
- **Règles métier** explicables (priorité minimale, cas de sécurité) : en Python, appliquées **après** le modèle,
  et appliquées aussi dans l'évaluation.
- **Outils** : le minimum de droits ; les serveurs MCP passent par une liste blanche.
- **Erreurs** : un `error_handler` peut répondre poliment, mais l'erreur doit rester visible (log + trace).
- **Timeouts** : mesurés sur le matériel cible, pas devinés.
- **Mesure** : tout changement de prompt, de modèle ou de règle repasse par l'experiment ; rapporte les chiffres
  avant/après, y compris s'ils ne s'améliorent pas.
- **Données personnelles** : jamais dans les tags, les noms de traces ni le dataset ; prévoir la suppression d'un thread.

## Commandes

```bash
uv sync                                  # installer
uv run python <fichier>.py "<question>"  # lancer une étape
uv run pytest                            # tests (avec un faux modèle : rapides, sans GPU)
```

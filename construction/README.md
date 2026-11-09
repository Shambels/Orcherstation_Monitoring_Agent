# Construction dirigée de HelpDesk-Ops

Le même système de tickets, construit **brique par brique**, en scripts Python. Pas de notebook : un fichier
par étape, qu'on lance dans un terminal et dont on voit l'effet immédiatement (terminal, dessin du graphe, Langfuse).

Chaque fichier `etape_NN_*.py` est **complet et exécutable** : il contient tout le système à la fin de
l'étape. D'une étape à la suivante, seul le code marqué `# ═══ NOUVEAU (étape N)` change. Ces étapes
sont celles de la carte d'architecture projetée au début de chaque partie du cours.

| Étape | Fichier | Ce qu'on ajoute | Partie du cours | À voir |
|---|---|---|---|---|
| 0 | `etape_00_chaine_tri.py` | la chaîne de tri LCEL et deux règles métier, tracées | J1-01 (vu le 04/11) | une trace `tri-ticket`, l'e-mail masqué |
| 1 | `etape_01_sessions_observe.py` | `@observe`, `propagate_attributes` : session, utilisateur, tags | Langfuse, la suite | deux traces dans une session |
| 2 | `etape_02_agent_outils.py` | deux `@tool`, l'agent ReAct (`create_agent`) | Les fondamentaux des agents | les outils appelés, la boucle ReAct dans la trace |
| 3 | `etape_03_stategraph.py` | l'état, les nœuds `trier → agent ⇄ tools`, le dessin du graphe | StateGraph, Ex1.1 | le graphe en ASCII, chaque nœud qui s'exécute |
| 4 | `etape_04_memoire.py` | `SqliteSaver`, `thread_id`, effacement | Persistance | l'agent se souvient d'un lancement à l'autre |
| 5 | `etape_05_validation_ticket.py` | `interrupt()` pour les P1, `creer_ticket` idempotent | Human-in-the-loop, Ex2.1 | ⏸ la pause, la reprise, le ticket dans un fichier JSON |
| 5 › défi | `defi_05_validation_eclairee.py` | correction du défi du 10/11 : la priorité dans la question d'`interrupt()`, la question relue dans le checkpoint par le 2e processus, un motif obligatoire au refus | mardi 10/11 (poussée pendant la séance) | « 📋 À valider » avant « ✍️ Décision » |
| 3 › défi | `defi_03_escalade.py` | correction du mini-défi du 09/11 : un nœud `escalader` et une arête conditionnelle après le tri | lundi 09/11 | le P1 part à l'astreinte sans passer par l'agent |
| 6a | `etape_06a_contrat.py` | l'étape 5 + `TicketDecision` (`with_structured_output`) et le plancher d'urgence, **sans async ni robustesse** : ce que le groupe écrit le 09/11 | Sortie structurée (09/11) | 📐 la décision, le résumé dans le ticket |
| 6 | `etape_06_structure_robustesse.py` | `TicketDecision` (`with_structured_output`), plancher d'urgence, `RetryPolicy`, `TimeoutPolicy`, `error_handler` ; le graphe passe en async | Sortie structurée, robustesse (09/11) | `--panne 2` : réessayé sans que l'utilisateur le voie ; `--panne 5` : le repli |
| 7 | `etape_07_rag_specialistes_mcp.py` | RAG Qdrant, spécialistes Procédures, Diagnostic et Accès exposés comme outils d'un coordinateur, outils MCP filtrés par liste blanche | J3 (09/11) | ⛔ l'outil MCP destructif écarté, l'arbre multi-agents dans Langfuse, les références [KB-xxx] |
| 8 | `etape_08_evaluation_securite.py` | garde-fou avant le tri, scores posés par le code, `--juger` (juge LLM), `--evaluer` (experiment sur le dataset) | J4 (10/11) | 🛡️ une injection refusée sans outil ni ticket, les scores sur la trace, le run du dataset |
| 8 › défis | `defi_08a_ticketing.py`, `defi_08b_mesurer_le_tri.py`, `defi_08c_hameconnage.py` | corrections de l'atelier du 10/11 : un spécialiste Ticketing ; le tri décrit et mesuré (deux runs) ; une règle hameçonnage appliquée dans le graphe ET dans l'experiment | atelier (10/11, poussées pendant la séance) | `demander_ticketing` ; deux runs comparés ; C10 en acces · P2 |

## Lancer une étape

Depuis la racine du cours, la stack démarrée :

```bash
uv run --project infrastructure python construction/etape_03_stategraph.py "srv-erp-02 est-il en ligne ?"
```

Les étapes 4 à 8 se lancent **plusieurs fois** : c'est là qu'on voit la mémoire et la pause humaine.

```bash
uv run --project infrastructure python construction/etape_05_validation_ticket.py --thread t-vpn "Le VPN est bloqué pour tout le service, urgent"
uv run --project infrastructure python construction/etape_05_validation_ticket.py --thread t-vpn --approuver marie
```

## Voir ce qui change

Dans VS Code : clic droit sur un fichier › *Select for Compare*, puis sur l'autre › *Compare with Selected*.
Dans un terminal :

```bash
git diff --no-index construction/etape_02_agent_outils.py construction/etape_03_stategraph.py
```

Le plus rapide reste de chercher `NOUVEAU (étape 3)` dans le fichier : ce sont les seules lignes ajoutées.

## Vous construisez en même temps : `atelier/`

Le formateur écrit à l'écran, vous écrivez dans `construction/atelier/mon_helpdesk.py` (voir `atelier/README.md`).
En retard ou bloqué : copiez l'étape de référence et repartez de là.

## Ce que les étapes écrivent sur le poste

| Fichier | Contenu | Git |
|---|---|---|
| `data/checkpoints/construction.sqlite` | les conversations (étapes 4 et 5), **en clair** | ignoré |
| `data/checkpoints/construction_tickets.json` | les tickets créés (étapes 5 à 8) | ignoré |
| `data/logs/mcp_inventaire.log` | ce que le serveur MCP a fait (étapes 7 et 8) | ignoré |
| `construction/sortie/*.md` | le schéma Mermaid de chaque graphe (s'ouvre dans JupyterLab) | ignoré |

Repartir de zéro : supprimez ces trois fichiers, ou effacez un thread avec `--oublier`.

## Pour le formateur

- **Vérifier que tout tourne** sans GPU ni Langfuse, avec un faux modèle :
  `uv run --project infrastructure pytest construction/test_etapes.py -q`. Le test ne remplace pas une
  exécution réelle sur un poste de la salle, avec le modèle et Langfuse.
- **Animer une étape** (15 à 25 min) :
  1. projeter la carte ;
  2. lancer l'étape précédente ;
  3. écrire les blocs `NOUVEAU` en direct (`comparer.py` sert d'antisèche) ;
  4. lancer, puis lire le terminal, puis la trace Langfuse.
- **Comparer les défis et l'étape 6a** : `comparer.py 3 defi3`, `comparer.py 5 6a`, `comparer.py 8 defi8a` (8b, 8c).
- **Les étapes 3 et 5 remplacent les exercices 1.1 et 2.1** (décision du 04/11) ; l'étape 7 recouvre l'Ex3.1 (spécialiste Accès).
- **Étape 7 la première fois** : `--indexer` remplit Qdrant avec data/kb (modèles d'embeddings préchargés la veille).
- **Étape 8, `--juger`** : sous vLLM, bascule en mode juge puis retour (1 à 3 min dans chaque sens) ; à lancer une fois, en démo.
- Les étapes sont volontairement **autonomes** : chacune redéfinit le tri, les règles et les outils au lieu de les
  importer de `helpdesk_ops`. On voit tout le système dans un seul fichier. Seuls le socle (`creer_llm`,
  `init_observabilite`, les données fictives) et `normaliser` viennent de `src/`.

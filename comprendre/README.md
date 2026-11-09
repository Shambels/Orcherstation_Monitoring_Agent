# Comprendre : les mécanismes en animation

Des pages à ouvrir dans le navigateur (double-clic), sans rien à installer et sans connexion.
Chaque animation se parcourt pas à pas, sur le code du fil rouge HelpDesk-Ops.

Commandes : ← → pour avancer ou reculer, espace pour lancer ou arrêter la lecture, ⟲ pour recommencer.
Chaque page propose au moins un choix qui change la suite : essayez les deux branches.

| Ordre | Animation | Ce qu'elle montre | Où on le voit dans le dépôt |
| --- | --- | --- | --- |
| 1 | `boucle_react.html` | `bind_tools`, l'AIMessage avec `tool_calls`, le ToolMessage, et le LLM qui relit toute la liste à chaque appel (il n'a pas de mémoire). Puis `create_agent`, la même boucle empaquetée. | J1-02, `construction/etape_02_agent_outils.py` |
| 2 | `stategraph_reducers.html` | Un nœud renvoie une mise à jour partielle, fusionnée clé par clé par les reducers (`add_messages`, `operator.add`, ou remplacement). Super-steps, arête conditionnelle, `recursion_limit`. | J1-03, Ex1.1, `construction/etape_03_stategraph.py` |
| 3 | `persistance_time_travel.html` | Un checkpoint après chaque super-step, des threads isolés, `get_state`, `get_state_history`, une reprise depuis une sauvegarde passée (fork), l'effacement RGPD. | J2-01, `construction/etape_04_memoire.py` |
| 4 | `interrupt_reprise.html` | `interrupt()` arrête le graphe et le programme quitte. L'état attend dans le checkpoint, sous le `thread_id`. Une autre personne reprend avec `Command(resume=…)`, et le nœud repart du début. | J2-02, Ex2.1, `construction/etape_05_validation_ticket.py` |
| 5 | `robustesse_retry_timeout.html` | `RetryPolicy` (tentatives, attentes, gigue, `retry_on`), `TimeoutPolicy`, le gestionnaire d'erreur qui prend le relais, sur une frise en secondes. | J2-04, Ex2.2, `construction/etape_06_structure_robustesse.py` |
| 6 | `coordinateur_specialistes.html` | Des spécialistes comme outils : chacun démarre avec sa propre fenêtre de contexte, et seule sa réponse finale remonte. Le coût en appels LLM, et la comparaison avec le handoff. | J3-01, J3-02, Ex3.1, `construction/etape_07_rag_specialistes_mcp.py` |
| 7 | `rag_hybride.html` | Indexation, recherche dense et BM25, fusion des classements, filtre de payload, passages injectés dans le prompt. | J3-02, `construction/etape_07_rag_specialistes_mcp.py` |
| 8 | `send_map_reduce.html` | `Send` lance une branche par élément dans le même super-step, un reducer fusionne, la plus lente fixe la durée. `max_concurrency` borne le parallélisme. | J3-03 |
| 9 | `mcp_client_serveur.html` | Un client et un serveur FastMCP, la poignée de main, `tools/list`, la liste blanche côté client, un appel complet. Un serveur compromis montre pourquoi on filtre. | J3-04, Ex5.1, `construction/etape_07_rag_specialistes_mcp.py` |

Les réponses des modèles affichées dans les animations sont des exemples : un vrai modèle peut formuler autrement.
Les comportements de LangGraph et LangChain, eux, ont été vérifiés sur les versions installées par le cours.

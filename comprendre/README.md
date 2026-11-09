# Comprendre : les mécanismes en animation

Des pages à ouvrir dans le navigateur (double-clic), sans rien à installer et sans connexion.
Chaque animation se parcourt pas à pas, sur le code du fil rouge.

| Animation | Ce qu'elle montre | Où on le voit dans le dépôt |
| --- | --- | --- |
| `interrupt_reprise.html` | `interrupt()` arrête le graphe et le programme quitte. L'état attend dans le checkpointer, rangé sous le `thread_id`. Deux heures plus tard, une autre personne reprend avec `Command(resume=…)`, et le nœud interrompu repart du début. | `J2_02_human_in_the_loop`, Ex2.1, `construction/etape_05_validation_ticket.py` |

Commandes : ← → pour avancer ou reculer, espace pour lancer ou arrêter la lecture.
À l'étape 9, choisissez la décision de Marie (approuver ou refuser) et essayez un `thread_id` mal tapé.

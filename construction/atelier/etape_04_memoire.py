"""Étape 4 : la mémoire des conversations.                        (carte : étape 4 · J2-01)

Ce qu'on ajoute à l'étape 3 :
    - un checkpointer SQLite : un checkpoint à chaque étape du graphe, dans data/checkpoints/construction.sqlite ;
    - un thread_id par conversation : relancer le script avec le même --thread reprend la conversation,
      même après l'arrêt du programme ;
    - --oublier : effacer une conversation (RGPD : un checkpoint contient la conversation en clair).

Lancer, en trois fois :
    uv run --project infrastructure python construction/etape_04_memoire.py --thread conv-42 "Mon VPN ne marche plus"
    uv run --project infrastructure python construction/etape_04_memoire.py --thread conv-42 "Et srv-vpn-01, il est en ligne ?"
    uv run --project infrastructure python construction/etape_04_memoire.py --thread conv-42 --oublier

À observer :
    - au 2e lancement, l'agent sait de quoi on parle : il relit les messages du 1er, sortis du fichier SQLite ;
    - le nombre de messages et de checkpoints du thread augmente à chaque lancement ;
    - Langfuse › Sessions : la session porte le nom du thread, une conversation = une session.
"""
import argparse
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langfuse import propagate_attributes
from langgraph.checkpoint.sqlite import SqliteSaver                    # ═══ NOUVEAU (étape 4)
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from helpdesk_ops.config import creer_llm
from helpdesk_ops.donnees import CATEGORIES, INVENTAIRE, KB
from helpdesk_ops.observabilite import init_observabilite
from helpdesk_ops.texte import mots_cles, normaliser

# ═══ NOUVEAU (étape 4) : la ligne de commande désigne la conversation ═══
parser = argparse.ArgumentParser(description="HelpDesk-Ops, étape 4 : la mémoire")
parser.add_argument("message", nargs="?", default="Mon VPN ne marche plus depuis ce matin")
parser.add_argument("--thread", default="conv-demo", help="identifiant de la conversation")
parser.add_argument("--oublier", action="store_true", help="effacer la conversation (RGPD)")
args = parser.parse_args()

RACINE = next(p for p in Path(__file__).resolve().parents if (p / "infrastructure").is_dir())   # la racine du cours
SORTIE = Path(__file__).resolve().parent / "sortie"
BASE = RACINE / "data" / "checkpoints" / "construction.sqlite"     # ═══ NOUVEAU (étape 4) : ignoré par Git

llm = creer_llm("agent")                         # le modèle désigné par infrastructure/.env
langfuse, handler = init_observabilite()         # client Langfuse avec masque PII + CallbackHandler

# --------------------------------------------------------------------------- la chaîne de tri (LCEL)
PROMPT_TRI = ChatPromptTemplate.from_messages([
    ("system",
     "Tu es l'agent de tri N1 du help-desk de NordTech SA. "
     "Classe le ticket dans UNE catégorie parmi : {categories}. "
     "Réponds uniquement par le nom de la catégorie, en minuscules, sans ponctuation."),
    ("human", "Ticket : {ticket}"),
])
tri = PROMPT_TRI | llm | StrOutputParser()


# --------------------------------------------------------------------------- les règles métier (sans LLM)
def normaliser_categorie(brute: str) -> str:
    """'Réseau.' ou 'catégorie : reseau' -> 'reseau' ; une réponse inattendue -> 'autre' (repli sûr)."""
    nettoyee = normaliser(brute).rstrip(".")
    return next((c for c in CATEGORIES if c in nettoyee), "autre")


def estimer_priorite(ticket: str) -> str:
    """P1 si un mot d'urgence apparaît, sinon P3 : simple, donc explicable."""
    texte = normaliser(ticket)
    return "P1" if any(m in texte for m in ("bloque", "urgent", "production", "tous les utilisateurs")) else "P3"


def file_attente(categorie: str) -> str:
    return "N2-infra" if categorie in ("reseau", "materiel") else "N1"


# --------------------------------------------------------------------------- les outils
@tool(parse_docstring=True)
def statut_serveur(nom: str) -> str:
    """Retourne le statut (en ligne, hors ligne, dégradé) d'un serveur de l'inventaire NordTech.

    Args:
        nom: nom d'hôte exact en minuscules, par exemple 'srv-vpn-01'
    """
    return INVENTAIRE.get(nom.strip().lower(), f"inconnu : '{nom}' n'est pas dans l'inventaire")


@tool(parse_docstring=True)
def chercher_kb(question: str) -> str:
    """Cherche dans la base de connaissances NordTech les procédures utiles pour un problème.

    Args:
        question: description courte du problème, par exemple 'configurer le VPN'
    """
    mots = mots_cles(question)
    trouves = [a for a in KB if mots & mots_cles(a["titre"] + " " + a["contenu"])]
    if not trouves:
        return "Aucun article trouvé. Proposer d'ouvrir un ticket."
    return "\n".join(f"[{a['id']}] {a['titre']} : {a['contenu']}" for a in trouves[:2])


OUTILS = [statut_serveur, chercher_kb]
SYSTEME = SystemMessage(
    "Tu es l'agent support N1 du help-desk de NordTech SA. Réponds en français, en 3 phrases maximum. "
    "Utilise statut_serveur pour l'état d'un serveur et chercher_kb pour une procédure. "
    "N'invente jamais de procédure ni de nom de serveur : si les outils ne donnent rien, propose d'ouvrir un ticket.")
llm_outils = llm.bind_tools(OUTILS)


# --------------------------------------------------------------------------- l'état et les nœuds
class EtatTicket(MessagesState):
    ticket: str
    categorie: str
    priorite: str
    file: str


def trier(state: EtatTicket) -> dict:
    brute = tri.invoke({"ticket": state["ticket"], "categories": ", ".join(CATEGORIES)})
    categorie = normaliser_categorie(brute)
    priorite = estimer_priorite(state["ticket"])
    return {"categorie": categorie, "priorite": priorite, "file": file_attente(categorie),
            "messages": [HumanMessage(f"[{categorie} · {priorite}] {state['ticket']}")]}


def agent(state: EtatTicket) -> dict:
    return {"messages": [llm_outils.invoke([SYSTEME] + state["messages"])]}


builder = StateGraph(EtatTicket)
builder.add_node("trier", trier)
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(OUTILS))
builder.add_edge(START, "trier")
builder.add_edge("trier", "agent")
builder.add_conditional_edges("agent", tools_condition)
builder.add_edge("tools", "agent")


def dessiner(graphe, nom: str) -> None:
    SORTIE.mkdir(exist_ok=True)
    (SORTIE / f"{nom}.md").write_text(f"```mermaid\n{graphe.get_graph().draw_mermaid()}\n```\n", encoding="utf-8")
    try:
        print(graphe.get_graph().draw_ascii())
    except ImportError:
        print("(dessin ASCII indisponible : grandalf absent)")
    print(f"🗺️  Schéma Mermaid : {(SORTIE / (nom + '.md')).relative_to(RACINE)}\n")


# ═══ NOUVEAU (étape 4) : compiler AVEC le checkpointer ; config["configurable"]["thread_id"] = la conversation ═══
BASE.parent.mkdir(parents=True, exist_ok=True)
with SqliteSaver.from_conn_string(str(BASE)) as memoire:
    graphe = builder.compile(checkpointer=memoire)
    config = {"configurable": {"thread_id": args.thread}, "callbacks": [handler],
              "run_name": "helpdesk", "recursion_limit": 10}

    if args.oublier:                                                   # ═══ NOUVEAU (étape 4) : droit à l'effacement
        memoire.delete_thread(args.thread)
        print(f"🧹 Conversation {args.thread} effacée du fichier SQLite.")
        raise SystemExit(0)

    avant = len(graphe.get_state(config).values.get("messages", []))
    if avant == 0:
        dessiner(graphe, "etape_04_graphe")
    print(f"💾 Thread {args.thread} : {avant} message(s) déjà en mémoire")
    print(f"🎫 {args.message}")

    with propagate_attributes(session_id=args.thread, user_id="agent-n1-042",   # ═══ NOUVEAU : session = thread
                              tags=["construction", "etape-4"]):
        etat = graphe.invoke({"ticket": args.message}, config=config)

    for appel in (a for m in etat["messages"][avant:] for a in getattr(m, "tool_calls", [])):
        print(f"   🧰 outil appelé : {appel['name']}({appel['args']})")
    print(f"   🤖 {etat['messages'][-1].content}")

    # ═══ NOUVEAU (étape 4) : lire ce que la mémoire contient ═══
    historique = list(graphe.get_state_history(config))
    print(f"💾 Thread {args.thread} : {len(etat['messages'])} messages, {len(historique)} checkpoints "
          f"dans {BASE.relative_to(RACINE)}")

langfuse.flush()
print(f"🔭 Langfuse › Sessions › {args.thread}")
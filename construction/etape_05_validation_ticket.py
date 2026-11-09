"""Étape 5 : la validation humaine et le ticket.                  (carte : étape 5 · J2-02, Ex2.1)

Ce qu'on ajoute à l'étape 4 :
    - un nœud creer_ticket : la première ACTION À IMPACT du système, écrite dans data/checkpoints/construction_tickets.json ;
    - un nœud valider : pour un P1, le graphe s'arrête (interrupt) et attend la décision d'un humain ;
      le checkpoint garde tout : on peut fermer le terminal et reprendre plus tard ;
    - l'idempotence : la clé du ticket vient du thread et de la demande, rejouer ne crée pas de doublon.

Lancer :
    # un P3 : le ticket est créé directement
    uv run --project infrastructure python construction/etape_05_validation_ticket.py --thread t-imprimante "Mon imprimante affiche une erreur papier"
    # un P1 : le graphe s'arrête et attend…
    uv run --project infrastructure python construction/etape_05_validation_ticket.py --thread t-vpn "Le VPN est bloqué pour tout le service, urgent"
    # … la décision d'un responsable, plus tard, dans un autre terminal
    uv run --project infrastructure python construction/etape_05_validation_ticket.py --thread t-vpn --approuver marie
    uv run --project infrastructure python construction/etape_05_validation_ticket.py --thread t-vpn --refuser marie --motif "doublon de NT-0001"

À observer :
    - le terminal : ⏸ « en attente de validation », puis 🎫 le ticket créé, avec « validé par » ;
    - data/checkpoints/construction_tickets.json : le ticket, sa file, qui l'a validé ;
    - Langfuse › Sessions › t-vpn : DEUX traces (avant et après la pause), une seule conversation.
"""
import argparse
import json
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig                    # ═══ NOUVEAU (étape 5)
from langchain_core.tools import tool
from langfuse import propagate_attributes
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, interrupt                         # ═══ NOUVEAU (étape 5)

from helpdesk_ops.config import creer_llm
from helpdesk_ops.donnees import CATEGORIES, INVENTAIRE, KB
from helpdesk_ops.observabilite import init_observabilite
from helpdesk_ops.texte import mots_cles, normaliser

parser = argparse.ArgumentParser(description="HelpDesk-Ops, étape 5 : validation humaine et ticket")
parser.add_argument("message", nargs="?", default="Le VPN est bloqué pour tout le service, urgent")
parser.add_argument("--thread", default="conv-demo", help="identifiant de la conversation")
parser.add_argument("--oublier", action="store_true", help="effacer la conversation (RGPD)")
parser.add_argument("--approuver", metavar="NOM", help="valider le ticket P1 en attente")      # ═══ NOUVEAU (étape 5)
parser.add_argument("--refuser", metavar="NOM", help="refuser le ticket P1 en attente")       # ═══ NOUVEAU (étape 5)
parser.add_argument("--motif", default="", help="motif du refus")                             # ═══ NOUVEAU (étape 5)
args = parser.parse_args()

RACINE = next(p for p in Path(__file__).resolve().parents if (p / "infrastructure").is_dir())   # la racine du cours
SORTIE = Path(__file__).resolve().parent / "sortie"
BASE = RACINE / "data" / "checkpoints" / "construction.sqlite"
TICKETS = RACINE / "data" / "checkpoints" / "construction_tickets.json"   # ═══ NOUVEAU (étape 5) : ignoré par Git

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


# ═══ NOUVEAU (étape 5) : le ticketing de NordTech (en vrai : l'API de l'outil ITSM), idempotent ═══
def enregistrer_ticket(cle: str, **champs) -> tuple[dict, bool]:
    """Renvoie (ticket, créé). La même clé renvoie le ticket existant : pas de doublon au rejeu."""
    tickets = json.loads(TICKETS.read_text(encoding="utf-8")) if TICKETS.exists() else {}
    if cle in tickets:
        return tickets[cle], False
    ticket = {"id": f"NT-{len(tickets) + 1:04d}", **champs}
    tickets[cle] = ticket
    TICKETS.write_text(json.dumps(tickets, ensure_ascii=False, indent=2), encoding="utf-8")
    return ticket, True


# --------------------------------------------------------------------------- l'état et les nœuds
class EtatTicket(MessagesState):
    ticket: str
    categorie: str
    priorite: str
    file: str
    valide_par: str          # ═══ NOUVEAU (étape 5)


def trier(state: EtatTicket) -> dict:
    brute = tri.invoke({"ticket": state["ticket"], "categories": ", ".join(CATEGORIES)})
    categorie = normaliser_categorie(brute)
    priorite = estimer_priorite(state["ticket"])
    return {"categorie": categorie, "priorite": priorite, "file": file_attente(categorie), "valide_par": "",
            "messages": [HumanMessage(f"[{categorie} · {priorite}] {state['ticket']}")]}


def agent(state: EtatTicket) -> dict:
    return {"messages": [llm_outils.invoke([SYSTEME] + state["messages"])]}


# ═══ NOUVEAU (étape 5) : après l'agent, trois chemins au lieu de deux ═══
def apres_agent(state: EtatTicket) -> Literal["tools", "valider", "creer_ticket"]:
    if tools_condition(state) == "tools":
        return "tools"
    return "valider" if state["priorite"] == "P1" else "creer_ticket"


# ═══ NOUVEAU (étape 5) : la pause humaine. À la reprise, le nœud repart DU DÉBUT : rien avant interrupt() ═══
def valider(state: EtatTicket) -> Command[Literal["creer_ticket", "__end__"]]:
    decision = interrupt({"question": "Créer ce ticket P1 ?", "ticket": state["ticket"],
                          "categorie": state["categorie"], "file": state["file"]})
    if decision["action"] == "approuver":
        return Command(goto="creer_ticket", update={"valide_par": decision["par"]})
    refus = AIMessage(f"Ticket P1 refusé par {decision['par']}" + (f" : {decision['motif']}" if decision["motif"] else "."))
    return Command(goto=END, update={"messages": [refus]})


# ═══ NOUVEAU (étape 5) : l'action à impact, dans son propre nœud ═══
def creer_ticket(state: EtatTicket, config: RunnableConfig) -> dict:
    cle = f"{config['configurable']['thread_id']}:{normaliser(state['ticket'])}"
    ticket, cree = enregistrer_ticket(cle, categorie=state["categorie"], priorite=state["priorite"],
                                      file=state["file"], resume=state["ticket"][:120],
                                      valide_par=state["valide_par"] or None)
    texte = f"Ticket {ticket['id']} {'créé' if cree else 'déjà existant'} dans la file {ticket['file']}"
    return {"messages": [AIMessage(texte + (f", validé par {ticket['valide_par']}." if ticket["valide_par"] else "."))]}


builder = StateGraph(EtatTicket)
builder.add_node("trier", trier)
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(OUTILS))
builder.add_node("valider", valider)                    # ═══ NOUVEAU (étape 5)
builder.add_node("creer_ticket", creer_ticket)          # ═══ NOUVEAU (étape 5)
builder.add_edge(START, "trier")
builder.add_edge("trier", "agent")
builder.add_conditional_edges("agent", apres_agent)     # ═══ NOUVEAU (étape 5) : remplace tools_condition seul
builder.add_edge("tools", "agent")
builder.add_edge("creer_ticket", END)                   # ═══ NOUVEAU (étape 5)


def dessiner(graphe, nom: str) -> None:
    SORTIE.mkdir(exist_ok=True)
    (SORTIE / f"{nom}.md").write_text(f"```mermaid\n{graphe.get_graph().draw_mermaid()}\n```\n", encoding="utf-8")
    try:
        print(graphe.get_graph().draw_ascii())
    except ImportError:
        print("(dessin ASCII indisponible : grandalf absent)")
    print(f"🗺️  Schéma Mermaid : {(SORTIE / (nom + '.md')).relative_to(RACINE)}\n")


# ═══ NOUVEAU (étape 5) : la question posée à l'humain, lue dans le checkpoint ═══
def en_attente(graphe, config) -> list[dict]:
    return [i.value for t in graphe.get_state(config).tasks for i in t.interrupts]


BASE.parent.mkdir(parents=True, exist_ok=True)
with SqliteSaver.from_conn_string(str(BASE)) as memoire:
    graphe = builder.compile(checkpointer=memoire)
    config = {"configurable": {"thread_id": args.thread}, "callbacks": [handler],
              "run_name": "helpdesk", "recursion_limit": 10}

    if args.oublier:
        memoire.delete_thread(args.thread)
        print(f"🧹 Conversation {args.thread} effacée du fichier SQLite (les tickets créés restent : c'est l'ITSM).")
        raise SystemExit(0)

    avant = len(graphe.get_state(config).values.get("messages", []))
    # ═══ NOUVEAU (étape 5) : reprendre une conversation en pause, ou en commencer une ═══
    if args.approuver or args.refuser:
        if not en_attente(graphe, config):
            raise SystemExit(f"Rien à valider sur le thread {args.thread}.")
        entree = Command(resume={"action": "approuver" if args.approuver else "refuser",
                                 "par": args.approuver or args.refuser, "motif": args.motif})
        print(f"✍️  Décision de {args.approuver or args.refuser} : {'approuvé' if args.approuver else 'refusé'}")
    else:
        if en_attente(graphe, config):
            raise SystemExit(f"Le thread {args.thread} attend une validation : --approuver NOM ou --refuser NOM.")
        if avant == 0:
            dessiner(graphe, "etape_05_graphe")
        entree = {"ticket": args.message}
        print(f"🎫 {args.message}")

    with propagate_attributes(session_id=args.thread, user_id="agent-n1-042", tags=["construction", "etape-5"]):
        etat = graphe.invoke(entree, config=config)

    for appel in (a for m in etat["messages"][avant:] for a in getattr(m, "tool_calls", [])):
        print(f"   🧰 outil appelé : {appel['name']}({appel['args']})")
    attente = en_attente(graphe, config)
    if attente:
        q = attente[0]
        print(f"   🏷️  {etat['categorie']} · {etat['priorite']} · file {etat['file']}")
        print(f"   🤖 {etat['messages'][-1].content}")
        print(f"⏸  {q['question']} Le graphe est arrêté, son état est dans le checkpoint. Pour reprendre :")
        print(f"   ... etape_05_validation_ticket.py --thread {args.thread} --approuver <nom>   (ou --refuser <nom> --motif ...)")
    else:
        print(f"   🎫 {etat['messages'][-1].content}")
        print(f"📒 Tickets : {TICKETS.relative_to(RACINE)}")

langfuse.flush()
print(f"🔭 Langfuse › Sessions › {args.thread}")

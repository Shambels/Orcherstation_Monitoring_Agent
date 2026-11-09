"""Étape 6a : le contrat du tri (sortie structurée), sur l'étape 5.     (carte : étape 6 · J2-03 · lundi 09/11)

Ce qu'on ajoute à l'étape 5 :
    - TicketDecision : un modèle Pydantic. Les Literal CONTRAIGNENT la réponse (aucune autre valeur possible) ;
      avec method="json_schema", le modèle ne LIT PAS les descriptions du schéma (vérifié le 07/11 avec granite
      sous Ollama) : les consignes vont dans le prompt ;
    - with_structured_output(..., method="json_schema") : le LLM REMPLIT le contrat, plus besoin de StrOutputParser
      ni de normaliser_categorie ;
    - la règle d'urgence devient un PLANCHER : le modèle propose la priorité, la règle ne laisse jamais un « urgent » sous P1 ;
    - le résumé produit par le modèle part dans le ticket.

Ce qui ne change pas : le graphe (trier → agent ⇄ tools → valider / creer_ticket), la mémoire, la pause humaine.
L'étape 6 complète (etape_06_structure_robustesse.py) y ajoute la robustesse : elle est montrée en démo.

Lancer :
    uv run --project infrastructure python construction/etape_06a_contrat.py --thread t-imp6 "Mon imprimante affiche une erreur papier"
    uv run --project infrastructure python construction/etape_06a_contrat.py --thread t-vpn6 "Le VPN est bloqué pour tout le service, urgent"

À observer :
    - le terminal : 📐 la décision (catégorie · priorité · résumé) ;
    - Langfuse : la génération du tri renvoie un JSON conforme au schéma ;
    - data/checkpoints/construction_tickets.json : le champ resume vient du modèle.
"""
import argparse
import json
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langfuse import propagate_attributes
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field                                  # ═══ NOUVEAU (étape 6a)

from helpdesk_ops.config import creer_llm
from helpdesk_ops.donnees import INVENTAIRE, KB
from helpdesk_ops.observabilite import init_observabilite
from helpdesk_ops.texte import mots_cles, normaliser

parser = argparse.ArgumentParser(description="HelpDesk-Ops, étape 6a : le contrat du tri")
parser.add_argument("message", nargs="?", default="Le VPN est bloqué pour tout le service, urgent")
parser.add_argument("--thread", default="conv-demo", help="identifiant de la conversation")
parser.add_argument("--oublier", action="store_true", help="effacer la conversation (RGPD)")
parser.add_argument("--approuver", metavar="NOM", help="valider le ticket P1 en attente")
parser.add_argument("--refuser", metavar="NOM", help="refuser le ticket P1 en attente")
parser.add_argument("--motif", default="", help="motif du refus")
args = parser.parse_args()

RACINE = next(p for p in Path(__file__).resolve().parents if (p / "infrastructure").is_dir())   # la racine du cours
SORTIE = Path(__file__).resolve().parent / "sortie"
BASE = RACINE / "data" / "checkpoints" / "construction.sqlite"
TICKETS = RACINE / "data" / "checkpoints" / "construction_tickets.json"

llm = creer_llm("agent")                         # le modèle désigné par infrastructure/.env
langfuse, handler = init_observabilite()         # client Langfuse avec masque PII + CallbackHandler

# ═══ NOUVEAU (étape 6a) : le contrat du tri. Il contraint la FORME ; les consignes, le modèle les lit dans le PROMPT ═══
class TicketDecision(BaseModel):
    """Qualification d'une demande de support."""
    categorie: Literal["reseau", "materiel", "logiciel", "acces", "autre"]
    priorite: Literal["P1", "P2", "P3", "P4"] = Field(
        description="P1 : production arrêtée ou nombreux utilisateurs bloqués ; P4 : simple demande")
    resume: str = Field(description="résumé du problème en une phrase, sans donnée personnelle")


# --------------------------------------------------------------------------- la chaîne de tri (LCEL)
PROMPT_TRI = ChatPromptTemplate.from_messages([
    ("system",
     "Tu es l'agent de tri N1 du help-desk de NordTech SA. Qualifie le ticket. "                        # ═══ NOUVEAU (étape 6a)
     "Priorité : P1 = service de production arrêté ou nombreux utilisateurs bloqués ; P2 = un utilisateur bloqué ; "
     "P3 = gêne sans blocage ; P4 = simple demande. Le résumé ne contient aucune donnée personnelle."),
    ("human", "Ticket : {ticket}"),
])
tri = PROMPT_TRI | llm.with_structured_output(TicketDecision, method="json_schema")   # ═══ NOUVEAU (étape 6a) : le contrat remplace le parser


# --------------------------------------------------------------------------- les règles métier (sans LLM)
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
    valide_par: str
    resume: str              # ═══ NOUVEAU (étape 6a)


def trier(state: EtatTicket) -> dict:
    decision = tri.invoke({"ticket": state["ticket"]})                  # ═══ NOUVEAU (étape 6a) : un TicketDecision
    if decision is None:                                                # sortie inexploitable : repli prudent
        decision = TicketDecision(categorie="autre", priorite="P3", resume=state["ticket"][:120])
    # ═══ NOUVEAU (étape 6a) : la règle d'urgence est un plancher. Un format valide n'est pas une décision juste ═══
    priorite = "P1" if estimer_priorite(state["ticket"]) == "P1" else decision.priorite
    categorie = decision.categorie
    print(f"   📐 décision : {categorie} · {priorite} · « {decision.resume} »")
    return {"categorie": categorie, "priorite": priorite, "file": file_attente(categorie), "valide_par": "",
            "resume": decision.resume,
            "messages": [HumanMessage(f"[{categorie} · {priorite}] {state['ticket']}")]}


def agent(state: EtatTicket) -> dict:
    return {"messages": [llm_outils.invoke([SYSTEME] + state["messages"])]}


def apres_agent(state: EtatTicket) -> Literal["tools", "valider", "creer_ticket"]:
    if tools_condition(state) == "tools":
        return "tools"
    return "valider" if state["priorite"] == "P1" else "creer_ticket"


def valider(state: EtatTicket) -> Command[Literal["creer_ticket", "__end__"]]:
    decision = interrupt({"question": "Créer ce ticket P1 ?", "ticket": state["ticket"],
                          "categorie": state["categorie"], "file": state["file"]})
    if decision["action"] == "approuver":
        return Command(goto="creer_ticket", update={"valide_par": decision["par"]})
    refus = AIMessage(f"Ticket P1 refusé par {decision['par']}" + (f" : {decision['motif']}" if decision["motif"] else "."))
    return Command(goto=END, update={"messages": [refus]})


def creer_ticket(state: EtatTicket, config: RunnableConfig) -> dict:
    cle = f"{config['configurable']['thread_id']}:{normaliser(state['ticket'])}"
    ticket, cree = enregistrer_ticket(cle, categorie=state["categorie"], priorite=state["priorite"],
                                      file=state["file"], resume=state["resume"],          # ═══ NOUVEAU (étape 6a) : le résumé du contrat
                                      valide_par=state["valide_par"] or None)
    texte = f"Ticket {ticket['id']} {'créé' if cree else 'déjà existant'} dans la file {ticket['file']}"
    return {"messages": [AIMessage(texte + (f", validé par {ticket['valide_par']}." if ticket["valide_par"] else "."))]}


builder = StateGraph(EtatTicket)
builder.add_node("trier", trier)
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(OUTILS))
builder.add_node("valider", valider)
builder.add_node("creer_ticket", creer_ticket)
builder.add_edge(START, "trier")
builder.add_edge("trier", "agent")
builder.add_conditional_edges("agent", apres_agent)
builder.add_edge("tools", "agent")
builder.add_edge("creer_ticket", END)


def dessiner(graphe, nom: str) -> None:
    SORTIE.mkdir(exist_ok=True)
    (SORTIE / f"{nom}.md").write_text(f"```mermaid\n{graphe.get_graph().draw_mermaid()}\n```\n", encoding="utf-8")
    try:
        print(graphe.get_graph().draw_ascii())
    except ImportError:
        print("(dessin ASCII indisponible : grandalf absent)")
    print(f"🗺️  Schéma Mermaid : {(SORTIE / (nom + '.md')).relative_to(RACINE)}\n")


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
            dessiner(graphe, "etape_06a_graphe")
        entree = {"ticket": args.message}
        print(f"🎫 {args.message}")

    with propagate_attributes(session_id=args.thread, user_id="agent-n1-042", tags=["construction", "etape-6a"]):
        etat = graphe.invoke(entree, config=config)

    for appel in (a for m in etat["messages"][avant:] for a in getattr(m, "tool_calls", [])):
        print(f"   🧰 outil appelé : {appel['name']}({appel['args']})")
    attente = en_attente(graphe, config)
    if attente:
        q = attente[0]
        print(f"   🏷️  {etat['categorie']} · {etat['priorite']} · file {etat['file']}")
        print(f"   🤖 {etat['messages'][-1].content}")
        print(f"⏸  {q['question']} Le graphe est arrêté, son état est dans le checkpoint. Pour reprendre :")
        print(f"   ... etape_06a_contrat.py --thread {args.thread} --approuver <nom>   (ou --refuser <nom> --motif ...)")
    else:
        print(f"   🎫 {etat['messages'][-1].content}")
        print(f"📒 Tickets : {TICKETS.relative_to(RACINE)}")

langfuse.flush()
print(f"🔭 Langfuse › Sessions › {args.thread}")

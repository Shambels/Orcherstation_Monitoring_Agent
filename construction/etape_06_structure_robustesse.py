"""Étape 6 : un contrat pour le tri, et un graphe qui tient bon.  (carte : étape 6 · J2-03, J2-04)

Ce qu'on ajoute à l'étape 5 :
    - la sortie structurée : le tri remplit un contrat Pydantic (TicketDecision : catégorie, priorité, résumé)
      au lieu d'un texte à normaliser ; la règle d'urgence reste un PLANCHER (jamais moins que P1 si urgent) ;
    - un outil réseau instable (ping_serveur, la supervision simulée de helpdesk_ops.reseau) ;
    - la robustesse LangGraph 1.2, nœud par nœud : RetryPolicy (réessayer le transitoire), TimeoutPolicy
      (borner une tentative), error_handler (le repli quand tout a échoué) ;
    - le graphe passe en ASYNCHRONE : les timeouts l'exigent. Le programme principal devient `async def main()`,
      la mémoire devient AsyncSqliteSaver.

Lancer :
    # la supervision répond du premier coup
    uv run --project infrastructure python construction/etape_06_structure_robustesse.py --thread t-ping "srv-erp-02 répond-il au ping ?"
    # 2 pannes avant le succès : RetryPolicy réessaie, l'utilisateur ne voit rien
    uv run --project infrastructure python construction/etape_06_structure_robustesse.py --thread t-ping2 --panne 2 "srv-erp-02 répond-il au ping ?"
    # 5 pannes : les 3 tentatives échouent, l'error_handler répond à la place de l'outil
    uv run --project infrastructure python construction/etape_06_structure_robustesse.py --thread t-ping5 --panne 5 "srv-erp-02 répond-il au ping ?"

À observer :
    - le terminal : la décision structurée (catégorie · priorité · résumé), le nombre d'appels à la supervision ;
    - Langfuse : la génération du tri renvoie un JSON conforme au schéma ; avec --panne 2, le nœud tools
      apparaît plusieurs fois (une par tentative) ; avec --panne 5, l'agent reçoit « outil indisponible ».
"""
import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal

import httpx                                                           # ═══ NOUVEAU (étape 6)
import openai                                                          # ═══ NOUVEAU (étape 6)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langfuse import propagate_attributes
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver           # ═══ NOUVEAU (étape 6) : remplace SqliteSaver
from langgraph.errors import NodeError                                 # ═══ NOUVEAU (étape 6)
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, RetryPolicy, TimeoutPolicy, interrupt   # ═══ NOUVEAU (étape 6) : RetryPolicy, TimeoutPolicy
from pydantic import BaseModel, Field                                  # ═══ NOUVEAU (étape 6)

from helpdesk_ops.config import creer_llm
from helpdesk_ops.donnees import INVENTAIRE, KB
from helpdesk_ops.observabilite import init_observabilite
from helpdesk_ops.reseau import RESEAU, ping_serveur                   # ═══ NOUVEAU (étape 6) : supervision instable
from helpdesk_ops.texte import mots_cles, normaliser

parser = argparse.ArgumentParser(description="HelpDesk-Ops, étape 6 : sortie structurée et robustesse")
parser.add_argument("message", nargs="?", default="Le VPN est bloqué pour tout le service, urgent")
parser.add_argument("--thread", default="conv-demo", help="identifiant de la conversation")
parser.add_argument("--oublier", action="store_true", help="effacer la conversation (RGPD)")
parser.add_argument("--approuver", metavar="NOM", help="valider le ticket P1 en attente")
parser.add_argument("--refuser", metavar="NOM", help="refuser le ticket P1 en attente")
parser.add_argument("--motif", default="", help="motif du refus")
parser.add_argument("--panne", type=int, default=0, help="nombre d'échecs de la supervision avant succès")   # ═══ NOUVEAU (étape 6)
args = parser.parse_args()

RACINE = next(p for p in Path(__file__).resolve().parents if (p / "infrastructure").is_dir())   # la racine du cours
SORTIE = Path(__file__).resolve().parent / "sortie"
BASE = RACINE / "data" / "checkpoints" / "construction.sqlite"
TICKETS = RACINE / "data" / "checkpoints" / "construction_tickets.json"

llm = creer_llm("agent")                         # le modèle désigné par infrastructure/.env
langfuse, handler = init_observabilite()         # client Langfuse avec masque PII + CallbackHandler


# ═══ NOUVEAU (étape 6) : le contrat du tri. Il contraint la FORME ; les consignes, le modèle les lit dans le PROMPT ═══
class TicketDecision(BaseModel):
    """Qualification d'une demande de support."""
    categorie: Literal["reseau", "materiel", "logiciel", "acces", "autre"]
    priorite: Literal["P1", "P2", "P3", "P4"] = Field(
        description="P1 : production arrêtée ou nombreux utilisateurs bloqués ; P4 : simple demande")
    resume: str = Field(description="résumé du problème en une phrase, sans donnée personnelle")


# --------------------------------------------------------------------------- la chaîne de tri (LCEL)
PROMPT_TRI = ChatPromptTemplate.from_messages([
    ("system",
     "Tu es l'agent de tri N1 du help-desk de NordTech SA. Qualifie le ticket. "                        # ═══ NOUVEAU (étape 6)
     "Priorité : P1 = service de production arrêté ou nombreux utilisateurs bloqués ; P2 = un utilisateur bloqué ; "
     "P3 = gêne sans blocage ; P4 = simple demande. Le résumé ne contient aucune donnée personnelle."),
    ("human", "Ticket : {ticket}"),
])
tri = PROMPT_TRI | llm.with_structured_output(TicketDecision, method="json_schema")   # ═══ NOUVEAU (étape 6) : le contrat remplace le parser


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


OUTILS = [statut_serveur, chercher_kb, ping_serveur]                    # ═══ NOUVEAU (étape 6) : + ping_serveur
SYSTEME = SystemMessage(
    "Tu es l'agent support N1 du help-desk de NordTech SA. Réponds en français, en 3 phrases maximum. "
    "Utilise statut_serveur pour l'état d'un serveur, ping_serveur pour tester sa joignabilité réseau "
    "et chercher_kb pour une procédure. "
    "N'invente jamais de procédure ni de nom de serveur : si les outils ne donnent rien, propose d'ouvrir un ticket.")
llm_outils = llm.bind_tools(OUTILS)

# ═══ NOUVEAU (étape 6) : ce qui mérite un nouvel essai. Une erreur de code (KeyError…) ne se réessaie pas ═══
ERREURS_RESEAU_LLM = (ConnectionError, httpx.TransportError, openai.APIConnectionError, openai.InternalServerError)
REPLI = "Je n'ai pas pu traiter votre demande automatiquement : je la transmets à un technicien N1."


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
    resume: str              # ═══ NOUVEAU (étape 6)


async def trier(state: EtatTicket) -> dict:                             # ═══ NOUVEAU (étape 6) : async
    decision = await tri.ainvoke({"ticket": state["ticket"]})
    if decision is None:                                                # sortie inexploitable : repli prudent
        decision = TicketDecision(categorie="autre", priorite="P3", resume=state["ticket"][:120])
    # ═══ NOUVEAU (étape 6) : la règle d'urgence est un plancher. Un format valide n'est pas une décision juste ═══
    priorite = "P1" if estimer_priorite(state["ticket"]) == "P1" else decision.priorite
    categorie = decision.categorie
    return {"categorie": categorie, "priorite": priorite, "file": file_attente(categorie), "valide_par": "",
            "resume": decision.resume,
            "messages": [HumanMessage(f"[{categorie} · {priorite}] {state['ticket']}")]}


async def agent(state: EtatTicket) -> dict:                             # ═══ NOUVEAU (étape 6) : async
    return {"messages": [await llm_outils.ainvoke([SYSTEME] + state["messages"])]}


def apres_agent(state: EtatTicket) -> Literal["tools", "valider", "creer_ticket"]:
    if tools_condition(state) == "tools":
        return "tools"
    return "valider" if state["priorite"] == "P1" else "creer_ticket"


# ═══ NOUVEAU (étape 6) : les replis. Après un error_handler, les arêtes normales ne sont PAS suivies : goto obligatoire ═══
def agent_en_echec(state: EtatTicket, error: NodeError) -> Command[Literal["__end__"]]:
    return Command(update={"messages": [AIMessage(REPLI)]}, goto=END)   # pas de ticket sur une analyse incomplète


def outils_en_echec(state: EtatTicket, error: NodeError) -> Command[Literal["agent"]]:
    # Une réponse par appel d'outil en attente : sans elles, le modèle reçoit un historique invalide
    motif = type(error.error).__name__
    reponses = [ToolMessage(content=f"outil indisponible ({motif}) : ne pas réessayer, le signaler à l'utilisateur",
                            tool_call_id=appel["id"], name=appel["name"], status="error")
                for appel in state["messages"][-1].tool_calls]
    return Command(update={"messages": reponses}, goto="agent")


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
                                      file=state["file"], resume=state["resume"],          # ═══ NOUVEAU : le résumé du contrat
                                      valide_par=state["valide_par"] or None)
    texte = f"Ticket {ticket['id']} {'créé' if cree else 'déjà existant'} dans la file {ticket['file']}"
    return {"messages": [AIMessage(texte + (f", validé par {ticket['valide_par']}." if ticket["valide_par"] else "."))]}


builder = StateGraph(EtatTicket)
# ═══ NOUVEAU (étape 6) : la robustesse se déclare nœud par nœud ═══
builder.add_node("trier", trier, retry_policy=RetryPolicy(max_attempts=3, retry_on=ERREURS_RESEAU_LLM),
                 timeout=TimeoutPolicy(run_timeout=120))
builder.add_node("agent", agent, retry_policy=RetryPolicy(max_attempts=3, retry_on=ERREURS_RESEAU_LLM),
                 timeout=TimeoutPolicy(run_timeout=120), error_handler=agent_en_echec)
builder.add_node("tools", ToolNode(OUTILS),
                 retry_policy=RetryPolicy(max_attempts=3, initial_interval=0.2, retry_on=ConnectionError),
                 timeout=TimeoutPolicy(run_timeout=10), error_handler=outils_en_echec)
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


async def en_attente(graphe, config) -> list[dict]:                    # ═══ NOUVEAU (étape 6) : async
    return [i.value for t in (await graphe.aget_state(config)).tasks for i in t.interrupts]


# ═══ NOUVEAU (étape 6) : le programme principal est asynchrone (ainvoke, aget_state, AsyncSqliteSaver) ═══
async def main() -> None:
    BASE.parent.mkdir(parents=True, exist_ok=True)
    RESEAU.configurer(echecs_avant_succes=args.panne)                  # ═══ NOUVEAU (étape 6) : le scénario de panne
    async with AsyncSqliteSaver.from_conn_string(str(BASE)) as memoire:
        graphe = builder.compile(checkpointer=memoire)
        config = {"configurable": {"thread_id": args.thread}, "callbacks": [handler],
                  "run_name": "helpdesk", "recursion_limit": 10}

        if args.oublier:
            await memoire.adelete_thread(args.thread)
            print(f"🧹 Conversation {args.thread} effacée du fichier SQLite (les tickets créés restent : c'est l'ITSM).")
            return

        avant = len((await graphe.aget_state(config)).values.get("messages", []))
        if args.approuver or args.refuser:
            if not await en_attente(graphe, config):
                raise SystemExit(f"Rien à valider sur le thread {args.thread}.")
            entree = Command(resume={"action": "approuver" if args.approuver else "refuser",
                                     "par": args.approuver or args.refuser, "motif": args.motif})
            print(f"✍️  Décision de {args.approuver or args.refuser} : {'approuvé' if args.approuver else 'refusé'}")
        else:
            if await en_attente(graphe, config):
                raise SystemExit(f"Le thread {args.thread} attend une validation : --approuver NOM ou --refuser NOM.")
            if avant == 0:
                dessiner(graphe, "etape_06_graphe")
            entree = {"ticket": args.message}
            print(f"🎫 {args.message}")

        with propagate_attributes(session_id=args.thread, user_id="agent-n1-042", tags=["construction", "etape-6"]):
            etat = await graphe.ainvoke(entree, config=config)

        print(f"   📐 décision : {etat['categorie']} · {etat['priorite']} · « {etat['resume']} »")   # ═══ NOUVEAU (étape 6)
        for appel in (a for m in etat["messages"][avant:] for a in getattr(m, "tool_calls", [])):
            print(f"   🧰 outil appelé : {appel['name']}({appel['args']})")
        if RESEAU.appels:                                                                       # ═══ NOUVEAU (étape 6)
            print(f"   📡 supervision : {RESEAU.appels} appel(s), dont {min(RESEAU.appels, args.panne)} en échec")
        attente = await en_attente(graphe, config)
        if attente:
            q = attente[0]
            print(f"   🏷️  {etat['categorie']} · {etat['priorite']} · file {etat['file']}")
            print(f"   🤖 {etat['messages'][-1].content}")
            print(f"⏸  {q['question']} Le graphe est arrêté, son état est dans le checkpoint. Pour reprendre :")
            print(f"   ... etape_06_structure_robustesse.py --thread {args.thread} --approuver <nom>   (ou --refuser <nom> --motif ...)")
        else:
            reponse = next((m.content for m in reversed(etat["messages"][avant:-1])          # ═══ NOUVEAU (étape 6)
                            if isinstance(m, AIMessage) and m.content and not m.tool_calls), "")
            if reponse:                                       # la réponse de l'agent, avant l'annonce du ticket
                print(f"   🤖 {reponse}")
            print(f"   🎫 {etat['messages'][-1].content}")
            print(f"📒 Tickets : {TICKETS.relative_to(RACINE)}")


asyncio.run(main())
langfuse.flush()
print(f"🔭 Langfuse › Sessions › {args.thread}")

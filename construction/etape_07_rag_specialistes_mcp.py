"""Étape 7 : le RAG, les spécialistes et MCP.                     (carte : étape 7 · J3-01 à J3-04)

Ce qu'on ajoute à l'étape 6 :
    - le RAG : la recherche par mots communs laisse la place à la KB complète (data/kb) dans Qdrant,
      en recherche hybride (dense + BM25 français), via helpdesk_ops.kb ;
    - trois spécialistes (create_agent), chacun avec SES outils : Procédures (RAG), Diagnostic, Accès ;
    - un coordinateur qui les appelle COMME DES OUTILS : chaque spécialiste ne reçoit que la consigne
      (isolation du contexte) et rend un texte court ;
    - les outils MCP du serveur « inventaire du parc », lancé dans un processus séparé, filtrés par une
      liste blanche CÔTÉ CLIENT : l'outil destructif reinitialiser_poste n'arrive jamais à l'agent ;
    - le tri, la validation humaine des P1 et le ticket ne changent pas : le coordinateur remplace l'agent.

Lancer :
    # la première fois : indexer la KB dans Qdrant (1 à 2 min)
    uv run --project infrastructure python construction/etape_07_rag_specialistes_mcp.py --indexer
    uv run --project infrastructure python construction/etape_07_rag_specialistes_mcp.py --thread t-outlook "Outlook plante dès que j'ouvre une pièce jointe"
    uv run --project infrastructure python construction/etape_07_rag_specialistes_mcp.py --thread t-pc "Le PC-0203 est très lent, quel est son historique ?"

À observer :
    - le terminal : les outils MCP proposés par le serveur et ceux retenus ; les délégations du coordinateur ;
    - la réponse cite ses sources [KB-xxx] ;
    - Langfuse : l'arbre multi-agents. Sous le coordinateur, un outil demander_… par délégation, et DEDANS
      la trace complète du spécialiste (ses générations, ses outils, la recherche Qdrant ou l'appel MCP) ;
    - data/logs/mcp_inventaire.log : ce que le serveur MCP a fait, hors de la console.
"""
import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal

import httpx
import openai
from langchain.agents import create_agent                              # ═══ NOUVEAU (étape 7)
from langchain.mcp import MCPAdapter                                   # ═══ NOUVEAU (étape 7) : client MCP (bêta)
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langfuse import propagate_attributes
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import NodeError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, RetryPolicy, TimeoutPolicy, interrupt
from pydantic import BaseModel, Field

from helpdesk_ops import kb                                            # ═══ NOUVEAU (étape 7) : la KB dans Qdrant
from helpdesk_ops.config import creer_llm
from helpdesk_ops.donnees import INVENTAIRE
from helpdesk_ops.jalons.j3 import CONSIGNE_ACCES, DESCRIPTION_ACCES, etat_compte   # ═══ NOUVEAU (étape 7) : l'annuaire
from helpdesk_ops.mcp_inventaire import filtrer_outils, transport_stdio            # ═══ NOUVEAU (étape 7)
from helpdesk_ops.multi_agents import CONSIGNES, DESCRIPTIONS, PROMPT_COORDINATEUR  # ═══ NOUVEAU (étape 7) : les consignes
from helpdesk_ops.observabilite import init_observabilite
from helpdesk_ops.reseau import RESEAU, ping_serveur
from helpdesk_ops.texte import normaliser

parser = argparse.ArgumentParser(description="HelpDesk-Ops, étape 7 : RAG, spécialistes et MCP")
parser.add_argument("message", nargs="?", default="Outlook plante dès que j'ouvre une pièce jointe")
parser.add_argument("--thread", default="conv-demo", help="identifiant de la conversation")
parser.add_argument("--oublier", action="store_true", help="effacer la conversation (RGPD)")
parser.add_argument("--approuver", metavar="NOM", help="valider le ticket P1 en attente")
parser.add_argument("--refuser", metavar="NOM", help="refuser le ticket P1 en attente")
parser.add_argument("--motif", default="", help="motif du refus")
parser.add_argument("--panne", type=int, default=0, help="nombre d'échecs de la supervision avant succès")
parser.add_argument("--indexer", action="store_true", help="(ré)indexer data/kb dans Qdrant, puis quitter")   # ═══ NOUVEAU (étape 7)
args = parser.parse_args()

RACINE = next(p for p in Path(__file__).resolve().parents if (p / "infrastructure").is_dir())   # la racine du cours
SORTIE = Path(__file__).resolve().parent / "sortie"
BASE = RACINE / "data" / "checkpoints" / "construction.sqlite"
TICKETS = RACINE / "data" / "checkpoints" / "construction_tickets.json"

llm = creer_llm("agent")                         # le modèle désigné par infrastructure/.env
langfuse, handler = init_observabilite()         # client Langfuse avec masque PII + CallbackHandler

if args.indexer:                                                       # ═══ NOUVEAU (étape 7)
    print(f"📚 {kb.indexer(recreer=True)} morceaux indexés dans la collection {kb.nom_collection()}")
    raise SystemExit(0)


class TicketDecision(BaseModel):
    """Qualification d'une demande de support."""
    categorie: Literal["reseau", "materiel", "logiciel", "acces", "autre"]
    priorite: Literal["P1", "P2", "P3", "P4"] = Field(
        description="P1 : production arrêtée ou nombreux utilisateurs bloqués ; P4 : simple demande")
    resume: str = Field(description="résumé du problème en une phrase, sans donnée personnelle")


# --------------------------------------------------------------------------- la chaîne de tri (LCEL)
PROMPT_TRI = ChatPromptTemplate.from_messages([
    ("system",
     "Tu es l'agent de tri N1 du help-desk de NordTech SA. Qualifie le ticket. "
     "Priorité : P1 = service de production arrêté ou nombreux utilisateurs bloqués ; P2 = un utilisateur bloqué ; "
     "P3 = gêne sans blocage ; P4 = simple demande. Le résumé ne contient aucune donnée personnelle."),
    ("human", "Ticket : {ticket}"),
])
tri = PROMPT_TRI | llm.with_structured_output(TicketDecision, method="json_schema")


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


# ═══ NOUVEAU (étape 7) : le RAG remplace chercher_kb. Public seulement, un extrait par article, références [KB-xxx] ═══
@tool(parse_docstring=True)
def rechercher_procedures(question: str) -> str:
    """Cherche les procédures du help-desk NordTech les plus pertinentes pour un problème.

    Args:
        question: le problème décrit en une phrase, par exemple 'le VPN ne se connecte pas depuis l'hôtel'
    """
    return kb.formater(kb.rechercher(question, k=3))


# ═══ NOUVEAU (étape 7) : UNE boucle asyncio pour tout le programme. Le client MCP reste attaché à la boucle qui l'a
# créé : avec asyncio.run ici, puis asyncio.run(main()), l'appel d'un outil MCP échoue (« Event loop is closed »). ═══
BOUCLE = asyncio.new_event_loop()

# ═══ NOUVEAU (étape 7) : les outils MCP, découverts au démarrage, filtrés par NOTRE liste blanche (OWASP ASI04) ═══
OUTILS_MCP_PROPOSES = BOUCLE.run_until_complete(MCPAdapter(transport_stdio()).list_tools())
OUTILS_MCP = filtrer_outils(OUTILS_MCP_PROPOSES, autorises={"lister_postes", "detail_poste", "historique_interventions"})

ERREURS_RESEAU_LLM = (ConnectionError, httpx.TransportError, openai.APIConnectionError, openai.InternalServerError)
REPLI = "Je n'ai pas pu traiter votre demande automatiquement : je la transmets à un technicien N1."

# ═══ NOUVEAU (étape 7) : les spécialistes. Un outil n'est donné qu'à l'agent qui en a besoin (OWASP ASI02) ═══
SPECIALISTES = {
    "kb": create_agent(llm, [rechercher_procedures], system_prompt=CONSIGNES["kb"], name="specialiste_kb"),
    "diagnostic": create_agent(llm, [statut_serveur, ping_serveur, *OUTILS_MCP], system_prompt=CONSIGNES["diagnostic"],
                               name="specialiste_diagnostic"),
    "acces": create_agent(llm, [etat_compte, rechercher_procedures], system_prompt=CONSIGNE_ACCES, name="specialiste_acces"),
}
DESCRIPTIONS_SPECIALISTES = {"kb": DESCRIPTIONS["kb"], "diagnostic": DESCRIPTIONS["diagnostic"], "acces": DESCRIPTION_ACCES}


# ═══ NOUVEAU (étape 7) : un spécialiste exposé comme un outil. Il ne reçoit QUE la consigne ═══
def comme_outil(nom: str, specialiste, description: str) -> BaseTool:
    async def deleguer(consigne: str, config: RunnableConfig) -> str:
        resultat = await specialiste.ainvoke({"messages": [HumanMessage(consigne)]},
                                             config={**config, "run_name": f"specialiste-{nom}"})   # traces imbriquées
        return resultat["messages"][-1].content
    deleguer.__doc__ = f"""{description}

        Args:
            consigne: la tâche précise confiée au spécialiste, avec les identifiants utiles (serveur, poste, compte)
        """
    return tool(f"demander_{nom}", parse_docstring=True)(deleguer)


# ═══ NOUVEAU (étape 7) : le coordinateur. Il ne répond jamais de mémoire : il délègue, puis rédige ═══
coordinateur = create_agent(llm, [comme_outil(n, s, DESCRIPTIONS_SPECIALISTES[n]) for n, s in SPECIALISTES.items()],
                            system_prompt=PROMPT_COORDINATEUR, name="coordinateur")


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
    resume: str


async def trier(state: EtatTicket) -> dict:
    decision = await tri.ainvoke({"ticket": state["ticket"]})
    if decision is None:                                                # sortie inexploitable : repli prudent
        decision = TicketDecision(categorie="autre", priorite="P3", resume=state["ticket"][:120])
    priorite = "P1" if estimer_priorite(state["ticket"]) == "P1" else decision.priorite
    categorie = decision.categorie
    return {"categorie": categorie, "priorite": priorite, "file": file_attente(categorie), "valide_par": "",
            "resume": decision.resume,
            "messages": [HumanMessage(f"[{categorie} · {priorite}] {state['ticket']}")]}


# ═══ NOUVEAU (étape 7) : le coordinateur remplace le couple agent ⇄ tools du graphe ═══
async def coordonner(state: EtatTicket) -> dict:
    resultat = await coordinateur.ainvoke({"messages": state["messages"]})
    return {"messages": resultat["messages"][len(state["messages"]):]}   # seulement les messages nouveaux


def apres_coordinateur(state: EtatTicket) -> Literal["valider", "creer_ticket"]:   # ═══ NOUVEAU (étape 7)
    return "valider" if state["priorite"] == "P1" else "creer_ticket"


def agent_en_echec(state: EtatTicket, error: NodeError) -> Command[Literal["__end__"]]:
    return Command(update={"messages": [AIMessage(REPLI)]}, goto=END)   # pas de ticket sur une analyse incomplète


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
                                      file=state["file"], resume=state["resume"],
                                      valide_par=state["valide_par"] or None)
    texte = f"Ticket {ticket['id']} {'créé' if cree else 'déjà existant'} dans la file {ticket['file']}"
    return {"messages": [AIMessage(texte + (f", validé par {ticket['valide_par']}." if ticket["valide_par"] else "."))]}


builder = StateGraph(EtatTicket)
builder.add_node("trier", trier, retry_policy=RetryPolicy(max_attempts=3, retry_on=ERREURS_RESEAU_LLM),
                 timeout=TimeoutPolicy(run_timeout=120))
builder.add_node("coordinateur", coordonner, retry_policy=RetryPolicy(max_attempts=2, retry_on=ERREURS_RESEAU_LLM),
                 timeout=TimeoutPolicy(run_timeout=600), error_handler=agent_en_echec)   # ═══ NOUVEAU (étape 7) · CPU : jusqu'à 6 min mesurées le 07/11
builder.add_node("valider", valider)
builder.add_node("creer_ticket", creer_ticket)
builder.add_edge(START, "trier")
builder.add_edge("trier", "coordinateur")                              # ═══ NOUVEAU (étape 7)
builder.add_conditional_edges("coordinateur", apres_coordinateur)      # ═══ NOUVEAU (étape 7)
builder.add_edge("creer_ticket", END)


def dessiner(graphe, nom: str) -> None:
    SORTIE.mkdir(exist_ok=True)
    (SORTIE / f"{nom}.md").write_text(f"```mermaid\n{graphe.get_graph().draw_mermaid()}\n```\n", encoding="utf-8")
    try:
        print(graphe.get_graph().draw_ascii())
    except ImportError:
        print("(dessin ASCII indisponible : grandalf absent)")
    print(f"🗺️  Schéma Mermaid : {(SORTIE / (nom + '.md')).relative_to(RACINE)}\n")


async def en_attente(graphe, config) -> list[dict]:
    return [i.value for t in (await graphe.aget_state(config)).tasks for i in t.interrupts]


async def main() -> None:
    BASE.parent.mkdir(parents=True, exist_ok=True)
    RESEAU.configurer(echecs_avant_succes=args.panne)
    async with AsyncSqliteSaver.from_conn_string(str(BASE)) as memoire:
        graphe = builder.compile(checkpointer=memoire)
        config = {"configurable": {"thread_id": args.thread}, "callbacks": [handler],
                  "run_name": "helpdesk", "recursion_limit": 25}           # ═══ NOUVEAU : délégations = plus d'étapes

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
                dessiner(graphe, "etape_07_graphe")
                # ═══ NOUVEAU (étape 7) : ce que le serveur MCP propose, et ce que NOUS retenons ═══
                retenus = {o.name for o in OUTILS_MCP}
                print("🔌 MCP inventaire : " + ", ".join(f"{o.name} {'✅' if o.name in retenus else '⛔'}"
                                                         for o in OUTILS_MCP_PROPOSES))
            entree = {"ticket": args.message}
            print(f"🎫 {args.message}")

        with propagate_attributes(session_id=args.thread, user_id="agent-n1-042", tags=["construction", "etape-7"]):
            etat = await graphe.ainvoke(entree, config=config)

        print(f"   📐 décision : {etat['categorie']} · {etat['priorite']} · « {etat['resume']} »")
        for appel in (a for m in etat["messages"][avant:] for a in getattr(m, "tool_calls", [])):
            print(f"   🧭 délégué à : {appel['name']}({appel['args']})")              # ═══ NOUVEAU (étape 7)
        if RESEAU.appels:
            print(f"   📡 supervision : {RESEAU.appels} appel(s), dont {min(RESEAU.appels, args.panne)} en échec")
        attente = await en_attente(graphe, config)
        if attente:
            q = attente[0]
            print(f"   🏷️  {etat['categorie']} · {etat['priorite']} · file {etat['file']}")
            print(f"   🤖 {etat['messages'][-1].content}")
            print(f"⏸  {q['question']} Le graphe est arrêté, son état est dans le checkpoint. Pour reprendre :")
            print(f"   ... etape_07_rag_specialistes_mcp.py --thread {args.thread} --approuver <nom>   (ou --refuser <nom> --motif ...)")
        else:
            reponse = next((m.content for m in reversed(etat["messages"][avant:-1])
                            if isinstance(m, AIMessage) and m.content and not m.tool_calls), "")
            if reponse:                                       # la réponse de l'agent, avant l'annonce du ticket
                print(f"   🤖 {reponse}")
            print(f"   🎫 {etat['messages'][-1].content}")
            print(f"📒 Tickets : {TICKETS.relative_to(RACINE)}")


BOUCLE.run_until_complete(main())                                      # ═══ NOUVEAU (étape 7) : la même boucle
langfuse.flush()
print(f"🔭 Langfuse › Sessions › {args.thread}")

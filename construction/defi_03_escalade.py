"""Mini-défi de l'étape 3 (correction) : un P1 ne passe pas par l'agent, il part à l'astreinte.   (lundi 09/11)

Le défi, sur une copie de l'étape 3 (construction/atelier/defi_escalade.py) :
    - un nœud `escalader` qui renvoie un message « transmis à l'astreinte N2 » : une mise à jour partielle ;
    - une fonction de routage `apres_tri` : "escalader" si la priorité est P1, sinon "agent" ;
    - l'arête fixe trier → agent devient une ARÊTE CONDITIONNELLE ; escalader → END.

Ce qui ne change pas : l'état, trier, agent, tools, la boucle agent ⇄ tools.

Lancer :
    uv run --project infrastructure python construction/defi_03_escalade.py "srv-erp-02 est-il en ligne ?"
    uv run --project infrastructure python construction/defi_03_escalade.py "Le VPN est bloqué pour tout le service, urgent"

À observer :
    - le dessin : trier a maintenant DEUX sorties en pointillés (agent, escalader) ;
    - le P3 suit trier → agent ⇄ tools ; le P1 suit trier → escalader, sans appeler le modèle une 2e fois ;
    - Langfuse : la trace du P1 n'a que deux enfants (trier, escalader).

Lundi après-midi, à l'étape 5, `escalader` devient `valider` : la même route P1, avec une pause humaine.
"""
import sys
from pathlib import Path
from typing import Literal                                             # ═══ NOUVEAU (défi 3)

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage    # ═══ NOUVEAU (défi 3) : AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langfuse import propagate_attributes
from langgraph.graph import END, START, MessagesState, StateGraph      # ═══ NOUVEAU (étape 3)
from langgraph.prebuilt import ToolNode, tools_condition              # ═══ NOUVEAU (étape 3)

from helpdesk_ops.config import creer_llm
from helpdesk_ops.donnees import CATEGORIES, INVENTAIRE, KB
from helpdesk_ops.observabilite import init_observabilite
from helpdesk_ops.texte import mots_cles, normaliser

TICKET = sys.argv[1] if len(sys.argv) > 1 else "srv-erp-02 est-il en ligne ? L'ERP ne répond plus"
RACINE = next(p for p in Path(__file__).resolve().parents if (p / "infrastructure").is_dir())   # la racine du cours
SORTIE = Path(__file__).resolve().parent / "sortie"

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
llm_outils = llm.bind_tools(OUTILS)                  # ═══ NOUVEAU (étape 3) : le modèle connaît les outils


# ═══ NOUVEAU (étape 3) : l'état partagé. MessagesState apporte `messages` et son reducer add_messages ═══
class EtatTicket(MessagesState):
    ticket: str
    categorie: str
    priorite: str
    file: str


# ═══ NOUVEAU (étape 3) : les nœuds. Chacun lit l'état et renvoie une MISE À JOUR PARTIELLE ═══
def trier(state: EtatTicket) -> dict:
    brute = tri.invoke({"ticket": state["ticket"], "categories": ", ".join(CATEGORIES)})
    categorie = normaliser_categorie(brute)
    priorite = estimer_priorite(state["ticket"])
    return {"categorie": categorie, "priorite": priorite, "file": file_attente(categorie),
            "messages": [HumanMessage(f"[{categorie} · {priorite}] {state['ticket']}")]}


def agent(state: EtatTicket) -> dict:
    return {"messages": [llm_outils.invoke([SYSTEME] + state["messages"])]}


# ═══ NOUVEAU (défi 3) : un nœud de plus, et la fonction qui choisit la route après le tri ═══
def escalader(state: EtatTicket) -> dict:
    return {"messages": [AIMessage(f"Ticket {state['priorite']} transmis à l'astreinte {state['file']} : "
                                   "un technicien vous rappelle dans les 15 minutes.")]}


def apres_tri(state: EtatTicket) -> Literal["escalader", "agent"]:
    return "escalader" if state["priorite"] == "P1" else "agent"


# ═══ NOUVEAU (étape 3) : le câblage ═══
builder = StateGraph(EtatTicket)
builder.add_node("trier", trier)
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(OUTILS))
builder.add_node("escalader", escalader)                  # ═══ NOUVEAU (défi 3)
builder.add_edge(START, "trier")
builder.add_conditional_edges("trier", apres_tri)         # ═══ NOUVEAU (défi 3) : remplace add_edge("trier", "agent")
builder.add_edge("escalader", END)                        # ═══ NOUVEAU (défi 3)
builder.add_conditional_edges("agent", tools_condition)   # un appel d'outil → "tools", sinon → END
builder.add_edge("tools", "agent")                        # après l'outil, on revient à l'agent : la boucle
graphe = builder.compile()


# ═══ NOUVEAU (étape 3) : dessiner le graphe, sans service en ligne ═══
def dessiner(graphe, nom: str) -> None:
    SORTIE.mkdir(exist_ok=True)
    (SORTIE / f"{nom}.md").write_text(f"```mermaid\n{graphe.get_graph().draw_mermaid()}\n```\n", encoding="utf-8")
    try:
        print(graphe.get_graph().draw_ascii())       # grandalf, installé par uv sync
    except ImportError:
        print("(dessin ASCII indisponible : grandalf absent)")
    print(f"🗺️  Schéma Mermaid : {(SORTIE / (nom + '.md')).relative_to(RACINE)}\n")


dessiner(graphe, "defi_03_graphe")

# --------------------------------------------------------------------------- exécuter
with propagate_attributes(session_id="construction-defi-3", user_id="agent-n1-042",
                          tags=["construction", "defi-3"]):
    config = {"callbacks": [handler], "run_name": "helpdesk", "recursion_limit": 10}
    print(f"🎫 {TICKET}")
    for etape in graphe.stream({"ticket": TICKET}, config=config, stream_mode="updates"):
        for noeud, maj in etape.items():                    # ═══ NOUVEAU (étape 3) : voir passer chaque nœud
            if noeud == "trier":
                print(f"   ▶ trier  : {maj['categorie']} · {maj['priorite']} · file {maj['file']}")
            elif noeud == "agent":
                msg = maj["messages"][-1]
                if msg.tool_calls:
                    print(f"   ▶ agent  : demande {', '.join(f'{a['name']}({a['args']})' for a in msg.tool_calls)}")
                else:
                    print(f"   ▶ agent  : répond « {msg.content} »")
            elif noeud == "escalader":                       # ═══ NOUVEAU (défi 3)
                print(f"   ▶ escalader : {maj['messages'][-1].content}")
            else:
                print(f"   ▶ {noeud}  : {maj['messages'][-1].content[:90]}")

langfuse.flush()
print("🔭 Langfuse › Tracing › helpdesk (P1 : trier, escalader · sinon : trier, agent, tools)")

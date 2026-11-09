"""Étape 2 : l'agent et ses outils.                               (carte : étape 2 · J1-02)

Ce qu'on ajoute à l'étape 1 :
    - deux outils @tool : statut_serveur (l'inventaire) et chercher_kb (la base de connaissances) ;
    - un agent ReAct (create_agent) : il DÉCIDE lui-même d'appeler un outil, lit le résultat, répond ;
    - le tri ne disparaît pas : il passe sa décision à l'agent, en tête du message.

Lancer :
    uv run --project infrastructure python construction/etape_02_agent_outils.py "srv-erp-02 est-il en ligne ?"

À observer :
    - le terminal : les outils appelés par l'agent, avec leurs arguments, puis sa réponse ;
    - Langfuse : sous traiter-ticket, la chaîne tri-ticket PUIS l'agent ; dans l'agent, une génération qui
      demande l'outil, l'outil (TOOL), une seconde génération qui répond. C'est la boucle ReAct.
"""
import sys

from langchain.agents import create_agent                     # ═══ NOUVEAU (étape 2)
from langchain_core.messages import HumanMessage              # ═══ NOUVEAU (étape 2)
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool                         # ═══ NOUVEAU (étape 2)
from langfuse import observe, propagate_attributes

from helpdesk_ops.config import creer_llm
from helpdesk_ops.donnees import CATEGORIES, INVENTAIRE, KB   # ═══ NOUVEAU (étape 2) : INVENTAIRE, KB
from helpdesk_ops.observabilite import init_observabilite
from helpdesk_ops.texte import mots_cles, normaliser

TICKETS = sys.argv[1:] or [
    "Bonjour, le VPN est bloqué depuis ce matin, c'est urgent. Jean (jean.dupont@nordtech.example)",
    "srv-erp-02 est-il en ligne ? L'ERP ne répond plus",
]

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


# ═══ NOUVEAU (étape 2) : les outils. La docstring EST la consigne que lit le modèle ═══
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

# ═══ NOUVEAU (étape 2) : l'agent ReAct, prêt à l'emploi ═══
SYSTEME = ("Tu es l'agent support N1 du help-desk de NordTech SA. Réponds en français, en 3 phrases maximum. "
           "Utilise statut_serveur pour l'état d'un serveur et chercher_kb pour une procédure. "
           "N'invente jamais de procédure ni de nom de serveur : si les outils ne donnent rien, propose d'ouvrir un ticket.")
agent = create_agent(model=llm, tools=OUTILS, system_prompt=SYSTEME)


@observe(name="traiter-ticket")
def traiter_ticket(ticket: str) -> dict:
    brute = tri.invoke({"ticket": ticket, "categories": ", ".join(CATEGORIES)},
                       config={"callbacks": [handler], "run_name": "tri-ticket"})
    categorie = normaliser_categorie(brute)
    decision = {"categorie": categorie, "priorite": estimer_priorite(ticket), "file": file_attente(categorie)}
    # ═══ NOUVEAU (étape 2) : le tri passe la main à l'agent ═══
    resultat = agent.invoke(
        {"messages": [HumanMessage(f"[{decision['categorie']} · {decision['priorite']}] {ticket}")]},
        config={"callbacks": [handler], "run_name": "agent-support", "recursion_limit": 10})
    decision["outils"] = [(a["name"], a["args"]) for m in resultat["messages"] for a in getattr(m, "tool_calls", [])]
    decision["reponse"] = resultat["messages"][-1].content
    return decision


# --------------------------------------------------------------------------- exécuter
with propagate_attributes(session_id="construction-etape-2", user_id="agent-n1-042",
                          tags=["construction", "etape-2"]):
    for ticket in TICKETS:
        d = traiter_ticket(ticket)
        print(f"🎫 {ticket}")
        print(f"   🏷️  {d['categorie']} · {d['priorite']} · file {d['file']}")
        for nom, args in d["outils"]:
            print(f"   🧰 outil appelé : {nom}({args})")
        print(f"   🤖 {d['reponse']}\n")

langfuse.flush()
print("🔭 Langfuse › Sessions › construction-etape-2")

"""Étape 1 : regrouper les traces et tracer son propre code.     (carte : étape 1 · J1-01 §6 à 11)

Ce qu'on ajoute à l'étape 0 :
    - @observe : la fonction traiter_ticket devient la RACINE de la trace ; la chaîne s'y range ;
    - propagate_attributes : une session (la journée de l'agent N1), un utilisateur pseudonyme, des tags ;
    - deux tickets traités dans la même session.

Lancer :
    uv run --project infrastructure python construction/etape_01_sessions_observe.py

À observer :
    - Langfuse › Tracing : deux traces « traiter-ticket », chacune avec la chaîne tri-ticket DEDANS ;
    - Langfuse › Sessions : la session « construction-etape-1 » regroupe les deux traces ;
    - Langfuse › Users : agent-n1-042 (un pseudonyme, jamais un nom ni un e-mail).
"""
import sys

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe, propagate_attributes          # ═══ NOUVEAU (étape 1)

from helpdesk_ops.config import creer_llm
from helpdesk_ops.donnees import CATEGORIES
from helpdesk_ops.observabilite import init_observabilite
from helpdesk_ops.texte import normaliser

TICKETS = sys.argv[1:] or [
    "Bonjour, le VPN est bloqué depuis ce matin, c'est urgent. Jean (jean.dupont@nordtech.example)",
    "Mon imprimante du 2e étage affiche une erreur papier",
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


# ═══ NOUVEAU (étape 1) : une fonction métier tracée, qui devient la racine de la trace ═══
@observe(name="traiter-ticket")
def traiter_ticket(ticket: str) -> dict:
    brute = tri.invoke({"ticket": ticket, "categories": ", ".join(CATEGORIES)},
                       config={"callbacks": [handler], "run_name": "tri-ticket"})
    categorie = normaliser_categorie(brute)
    return {"categorie": categorie, "priorite": estimer_priorite(ticket), "file": file_attente(categorie)}


# --------------------------------------------------------------------------- exécuter
# ═══ NOUVEAU (étape 1) : session, utilisateur et tags posés UNE fois, hérités par tout ce qui est dedans ═══
with propagate_attributes(session_id="construction-etape-1", user_id="agent-n1-042",
                          tags=["construction", "etape-1"]):
    for ticket in TICKETS:
        decision = traiter_ticket(ticket)
        print(f"🎫 {ticket}")
        print(f"   → {decision['categorie']} · {decision['priorite']} · file {decision['file']}")

langfuse.flush()
print("🔭 Langfuse › Sessions › construction-etape-1 :", len(TICKETS), "traces")

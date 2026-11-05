"""Outils du fil rouge (jalon J1) : lecture seule, docstrings précises, erreurs explicites.

Règles appliquées (OWASP ASI02) : exposer le minimum, normaliser l'entrée, renvoyer un message
d'erreur exploitable par le modèle plutôt qu'un None.
"""
from langchain_core.tools import tool

from helpdesk_ops.donnees import INVENTAIRE, KB, TICKETS_OUVERTS
from helpdesk_ops.texte import mots_cles, normaliser


@tool(parse_docstring=True)
def statut_serveur(nom: str) -> str:
    """Retourne le statut (en ligne, hors ligne, dégradé) d'un serveur de l'inventaire NordTech.

    Args:
        nom: nom d'hôte exact en minuscules, par exemple 'srv-vpn-01'
    """
    return INVENTAIRE.get(nom.strip().lower(), f"inconnu : '{nom}' n'est pas dans l'inventaire")


@tool(parse_docstring=True)
def tickets_ouverts(file: str) -> str:
    """Retourne le nombre de tickets ouverts dans une file d'attente du help-desk.

    Args:
        file: nom exact de la file : 'N1', 'N2-infra' ou 'N2-applicatif'
    """
    if file not in TICKETS_OUVERTS:
        return f"file inconnue : '{file}'. Files valides : {', '.join(TICKETS_OUVERTS)}"
    return f"{TICKETS_OUVERTS[file]} tickets ouverts dans la file {file}"


def rechercher_kb(question: str, k: int = 2) -> list[dict]:
    """Recherche par mots communs (voir mots_cles), sans accents. Le vrai RAG sur Qdrant arrive en J3."""
    mots = mots_cles(question)
    scores = []
    for article in KB:
        texte = normaliser(article["titre"] + " " + article["contenu"]).replace("-", " ")
        score = sum(mot in texte for mot in mots)
        if score:
            scores.append((score, article))
    return [a for _, a in sorted(scores, key=lambda s: s[0], reverse=True)[:k]]


@tool(parse_docstring=True)
def chercher_kb(question: str) -> str:
    """Cherche dans la base de connaissances du help-desk NordTech les procédures utiles pour un problème.

    Args:
        question: description courte du problème, par exemple 'configurer le VPN' ou 'Outlook plante'
    """
    articles = rechercher_kb(question)
    if not articles:
        return "Aucun article trouvé. Proposer d'ouvrir un ticket."
    return "\n".join(f"[{a['id']}] {a['titre']} : {a['contenu']}" for a in articles)


OUTILS_J1 = [statut_serveur, tickets_ouverts, chercher_kb]

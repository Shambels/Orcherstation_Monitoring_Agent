"""Normalisation de texte français : le piège n°1 des règles par mots-clés."""
import unicodedata


def sans_accents(texte: str) -> str:
    """'Accès Réseau' -> 'Acces Reseau' : décompose les lettres accentuées puis retire les accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", texte) if not unicodedata.combining(c))


def normaliser(texte: str) -> str:
    """Forme de comparaison : sans accents, en minuscules, sans espaces superflus."""
    return " ".join(sans_accents(texte).lower().split())


# Trop courants pour désigner un sujet. Les sigles de 3 lettres (vpn, erp, mfa) et « mot » (de passe) restent.
MOTS_VIDES = {"les", "des", "une", "est", "pas", "sur", "par", "pour", "dans", "avec", "mon", "ton", "son",
              "mes", "ses", "que", "qui", "aux", "plus", "puis", "tout", "tous"}


def mots_cles(texte: str) -> set[str]:
    """Mots de 3 lettres ou plus, hors mots vides : 'Mon VPN ne marche plus' -> {'vpn', 'marche'}."""
    return {m for m in normaliser(texte).replace("-", " ").split() if len(m) >= 3 and m not in MOTS_VIDES}

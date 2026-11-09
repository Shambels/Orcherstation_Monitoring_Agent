"""La console HelpDesk-Ops avec le faux modèle des étapes : le moteur, puis l'interface (AppTest de Streamlit).

    uv run --project infrastructure pytest construction/test_console.py -q

Comme test_etapes.py, ce test ne remplace pas un essai avec le vrai modèle et Langfuse.
"""
import queue

import pytest

import test_etapes
from console_moteur import Boucle, Moteur, debut_programme, fichiers_disponibles
from test_etapes import faux_qdrant_et_mcp, sans_modele_ni_langfuse   # noqa: F401 (fixtures)

pytestmark = pytest.mark.usefixtures("sans_modele_ni_langfuse")


def moteur(nom: str) -> Moteur:
    return Moteur(next(test_etapes.DOSSIER.glob(f"{nom}*.py")), Boucle())


def derouler(m: Moteur, entree, thread=None, **kw) -> list:
    file = queue.Queue()
    m.lancer(entree, thread, file, **kw)
    evenements = []
    while True:
        e = file.get(timeout=60)
        evenements.append(e)
        if e.type == "erreur":
            raise e.valeur
        if e.type == "fin":
            return evenements


def noeuds(evenements) -> list[str]:
    return [e.noeud for e in evenements if e.type in ("noeud", "pause")]


def test_la_console_s_arrete_au_programme_du_terminal():
    for f in fichiers_disponibles(test_etapes.ICI):
        source = f.read_text(encoding="utf-8")
        ligne = source.splitlines()[debut_programme(source) - 1]
        assert ligne.startswith(("brute = tri.invoke", "with propagate_attributes", "dessiner(",
                                 "BASE.parent.mkdir", "async def main")), f"{f.name} : {ligne}"


def test_etape_0_le_tri_seul():
    m = moteur("etape_00")
    assert m.etape == 0 and not m.b["graphe"]
    r = m.traiter_sans_graphe("Mon VPN est bloqué, urgent", "s")
    assert (r["categorie"], r["priorite"], r["file"]) == ("reseau", "P1", "N2-infra")


def test_etape_2_l_agent_et_ses_outils():
    m = moteur("etape_02")
    assert m.etape == 2
    r = m.traiter_sans_graphe("srv-vpn-01 est-il en ligne ?", "s")
    assert r["outils"][0][0] == "statut_serveur" and "en ligne" in r["reponse"]


def test_etape_3_chaque_noeud_arrive_dans_l_ordre():
    m = moteur("etape_03")
    assert m.etape == 3 and not m.b["memoire"]
    assert noeuds(derouler(m, {"ticket": "srv-vpn-01 est-il en ligne ?"})) == ["trier", "agent", "tools", "agent"]


def test_etape_4_la_memoire_et_l_oubli():
    m = moteur("etape_04")
    derouler(m, {"ticket": "Mon VPN ne marche plus"}, "c42")
    derouler(m, {"ticket": "Et srv-vpn-01 ?"}, "c42")
    assert "c42" in m.threads() and len(m.etat("c42")["valeurs"]["messages"]) == 8
    m.oublier("c42")
    assert "c42" not in m.threads()


def test_etape_5_pause_superviseur_puis_ticket():
    from langgraph.types import Command
    m = moteur("etape_05")
    ev = derouler(m, {"ticket": "Le VPN est bloqué pour tout le service, urgent"}, "t-vpn")
    assert noeuds(ev)[-1] == "valider"                                        # ⏸ : le graphe s'arrête
    (thread, q), = m.en_attente()
    assert thread == "t-vpn" and q["question"] == "Créer ce ticket P1 ?"
    ev = derouler(m, Command(resume={"action": "approuver", "par": "marie", "motif": ""}), "t-vpn")
    assert noeuds(ev) == ["valider", "creer_ticket"] and not m.en_attente()
    (ticket,) = m.tickets()
    assert ticket["valide_par"] == "marie" and ticket["thread"] == "t-vpn"


def test_etape_6_les_pannes_simulees():
    m = moteur("etape_06_")
    derouler(m, {"ticket": "srv-erp-02 répond-il au ping ?"}, "t6", pannes=2)
    assert m.m.RESEAU.appels == 3                                             # 2 échecs, puis le succès


def test_etape_8_scores_poses_par_le_code(faux_qdrant_et_mcp):
    m = moteur("etape_08")
    assert m.etape == 8 and ("reinitialiser_poste", False) in m.mcp()
    ev = derouler(m, {"ticket": "Outlook plante à l'ouverture d'une pièce jointe"}, "t8")
    assert noeuds(ev)[0] == "garde_fou" and ev[-1].valeur == "trace-test"
    scores = m.poser_scores("trace-test", "SUPPORT", "Voir [KB-004].", "Outlook plante")
    assert scores == {"garde_fou": "SUPPORT", "sources_citees": True}


@pytest.mark.parametrize("etape", ["0", "5"])
def test_l_interface_s_affiche(monkeypatch, etape):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("HELPDESK_CONSOLE_DOSSIER", str(test_etapes.DOSSIER))
    at = AppTest.from_file(str(test_etapes.ICI / "console.py"), default_timeout=120)
    at.query_params["etape"] = etape
    at.run()
    assert not at.exception, at.exception
    onglets = [t.label for t in at.tabs]
    assert ("🧑‍💼 Superviseur" in onglets) == (etape == "5")


def test_console_copiee_dans_l_atelier(monkeypatch):
    """La console distribuée : console.py et console_moteur.py copiés dans atelier/, à côté de mon_helpdesk.py."""
    import shutil
    from streamlit.testing.v1 import AppTest
    atelier = test_etapes.DOSSIER / "atelier"
    atelier.mkdir()
    for f in ("console.py", "console_moteur.py"):
        shutil.copy(test_etapes.ICI / f, atelier / f)
    shutil.copy(test_etapes.DOSSIER / "etape_05_validation_ticket.py", atelier / "mon_helpdesk.py")
    shutil.copy(test_etapes.DOSSIER / "defi_03_escalade.py", atelier / "mon_defi_3.py")
    assert [p.name for p in fichiers_disponibles(atelier)] == ["mon_helpdesk.py", "mon_defi_3.py"]
    # même processus que les autres tests : console_moteur est déjà importé depuis construction/, on désigne le dossier
    monkeypatch.setenv("HELPDESK_CONSOLE_DOSSIER", str(atelier))
    at = AppTest.from_file(str(atelier / "console.py"), default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    assert "🧑‍💼 Superviseur" in [t.label for t in at.tabs]          # mon_helpdesk.py (étape 5) est branché

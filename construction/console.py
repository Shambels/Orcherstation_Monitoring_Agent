"""La console HelpDesk-Ops : votre graphe devient le moteur d'un outil de support.

    uv run --project infrastructure streamlit run construction/console.py        # depuis la racine du cours
    uv run --project infrastructure streamlit run construction/atelier/console.py   # copiée dans votre atelier
    uv run --project infrastructure python construction/atelier/console.py          # même effet : elle se relance avec streamlit run

Choisissez dans la barre latérale une étape de référence ou votre atelier (construction/atelier/mon_helpdesk.py) :
la console affiche les briques présentes dans le fichier, et seulement celles-là. L'étape 3 allume le graphe,
l'étape 4 ajoute les conversations, l'étape 5 la page du superviseur et les tickets, et ainsi de suite.

Le terminal et la console partagent les mêmes fichiers (data/checkpoints/) : un P1 mis en pause dans le
terminal peut être approuvé dans la console, et inversement.
"""
import queue
import re
import subprocess
import sys
import uuid
from pathlib import Path

import streamlit as st
from streamlit import runtime

if __name__ == "__main__" and not runtime.exists():
    # Lancée avec « python console.py » : on relance la même console avec « streamlit run », qui ouvre le navigateur
    from streamlit.web import cli as stcli
    sys.argv = ["streamlit", "run", str(Path(__file__).resolve()), *sys.argv[1:]]
    sys.exit(stcli.main())

sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_moteur import BRIQUES, Boucle, Moteur, dot, fichiers_disponibles, raconter   # noqa: E402

st.set_page_config(page_title="HelpDesk-Ops · console", page_icon="🎧", layout="wide")


@st.cache_resource
def boucle() -> Boucle:
    return Boucle()


@st.cache_resource(show_spinner="Chargement du système (modèle, Langfuse, outils…)")
def moteur(chemin: str, _mtime: float) -> Moteur:
    """Un moteur par fichier ; modifié sur le disque (mtime), il est rechargé."""
    return Moteur(Path(chemin), boucle())


def libelle(p: Path) -> str:
    if p.parent.name == "atelier":
        return f"🧑‍💻 Mon atelier · {p.name}"
    n = re.match(r"(etape|defi)_(\d+)([a-z]?)_(.*)\.py", p.name)
    if not n:
        return p.name
    titre = n.group(4).replace("_", " ")
    if n.group(1) == "defi":                    # defi_03_escalade → mini-défi ; defi_08a_ticketing → défi A
        nom = f"Défi {n.group(3).upper()}" if n.group(3) else "Mini-défi"
        return f"✅ {nom} (correction, étape {int(n.group(2))}) · {titre}"
    return f"Étape {int(n.group(2))}{n.group(3)} · {titre}"


def avec_sources(texte: str) -> str:
    return re.sub(r"\[(KB-\d+)\]", r"**`\1`**", texte)


# =============================================================================== barre latérale
fichiers = fichiers_disponibles()
demande = st.query_params.get("etape")
defaut = next((i for i, f in enumerate(fichiers) if demande and f.name.startswith(f"etape_{int(demande):02d}")), 0)

with st.sidebar:
    st.markdown("## 🎧 HelpDesk-Ops")
    st.caption("NordTech SA (fictif) · stack 100 % locale")
    choix = st.selectbox("Le système branché", fichiers, index=defaut, format_func=libelle)
    try:
        M = moteur(str(choix), choix.stat().st_mtime)
    except Exception as e:                      # votre atelier ne s'exécute pas : on le dit, avec la ligne
        st.error(f"Ce fichier ne se charge pas : {type(e).__name__} : {e}")
        st.stop()

    st.markdown("**Les briques de ce fichier**")
    for cle, texte in BRIQUES:
        st.markdown(f"{'✅' if M.b[cle] else '◻️'} {texte}")

    thread = None
    if M.b["memoire"]:
        st.divider()
        st.markdown("**💾 Conversations**")
        connues = M.threads()
        if "nouveau_thread" in st.session_state and st.session_state.nouveau_thread not in connues:
            connues = [st.session_state.nouveau_thread] + connues
        thread = st.selectbox("Conversation (thread_id)", connues or ["conv-console"], key="thread")
        with st.form("nouvelle", clear_on_submit=True, border=False):
            nom = st.text_input("Nouvelle conversation", placeholder=f"t-{uuid.uuid4().hex[:4]}")
            if st.form_submit_button("➕ Ouvrir") and nom.strip():
                st.session_state.nouveau_thread = nom.strip()
                st.session_state.thread = nom.strip()
                st.rerun()
        if st.button("🧹 Oublier cette conversation", help="RGPD art. 17 : efface le thread du fichier SQLite "
                                                           "(les tickets créés restent : c'est l'ITSM)"):
            M.oublier(thread)
            st.toast(f"Conversation {thread} effacée.")
            st.rerun()

    pannes = 0
    if M.b["robustesse"]:
        st.divider()
        pannes = st.slider("📡 Pannes de la supervision", 0, 5, 0,
                           help="Nombre d'échecs de ping_serveur avant le succès. 2 : RetryPolicy réessaie ; "
                                "5 : les 3 tentatives échouent, l'error_handler répond.")
    juger = False
    if M.b["securite"]:
        juger = st.checkbox("⚖️ Juger la fidélité (juge LLM)", help="gemma4, juge ≠ modèle évalué. "
                                                                    "Sous vLLM : bascule en mode juge (1 à 3 min).")
    if M.mcp():
        with st.expander("🔌 Outils MCP (inventaire du parc)"):
            for nom_outil, garde in M.mcp():
                st.markdown(f"{'✅' if garde else '⛔'} `{nom_outil}`")
            st.caption("Liste blanche CÔTÉ CLIENT : un outil écarté n'arrive jamais à l'agent.")

    st.divider()
    st.link_button("🔭 Ouvrir Langfuse", M.lien_langfuse(session=thread) or "http://localhost:3000")
    st.caption(f"Étape détectée : {M.etape} · tags Langfuse : {', '.join(M.tags)}")


# =============================================================================== exécuter un graphe, nœud par nœud
def derouler(entree, thread_id, zone_graphe, zone_journal) -> tuple[str | None, list, list[str]]:
    """Lance le graphe et anime le dessin pendant l'exécution. Renvoie (trace_id, pauses, journal)."""
    file = queue.Queue()
    M.lancer(entree, thread_id, file, pannes=pannes)
    faits, journal, pauses = set(), [], []
    zone_graphe.graphviz_chart(dot(M.graphe, courant="__start__"))
    with st.spinner("Le graphe s'exécute…"):
        while True:
            e = file.get()
            if e.type == "noeud":
                faits.add(e.noeud)
                journal.append(raconter(e.noeud, e.valeur))
                zone_graphe.graphviz_chart(dot(M.graphe, faits=faits, courant=e.noeud))
            elif e.type == "pause":
                pauses = e.valeur
                journal.append(f"⏸ **valider** : {pauses[0].get('question', 'en attente')} "
                               "Le graphe est arrêté, son état est dans le checkpoint.")
                zone_graphe.graphviz_chart(dot(M.graphe, faits=faits, courant="valider", pause=True))
            elif e.type == "erreur":
                zone_journal.error(f"{type(e.valeur).__name__} : {e.valeur}")
                return None, [], journal
            else:
                if not pauses:
                    zone_graphe.graphviz_chart(dot(M.graphe, faits=faits | {"__end__"}))
                zone_journal.markdown("\n\n".join(journal))
                return e.valeur, pauses, journal
            zone_journal.markdown("\n\n".join(journal))


def afficher_messages(messages) -> None:
    """La conversation telle que le checkpoint la garde : demandes, appels d'outils, résultats, réponses."""
    noms = {}
    for m in messages:
        if m.type == "human":
            texte = str(m.content)
            etiquette = re.match(r"^\[([^\]]+)\]\s*", texte)
            with st.chat_message("user"):
                st.markdown(texte[etiquette.end():] if etiquette else texte)
                if etiquette:
                    st.caption(f"tri : {etiquette.group(1)}")
        elif m.type == "ai":
            with st.chat_message("assistant", avatar="🎧"):
                for a in getattr(m, "tool_calls", []) or []:
                    noms[a["id"]] = a["name"]
                    icone = "🧭" if a["name"].startswith("demander_") else "🧰"
                    st.markdown(f"{icone} `{a['name']}` · `{a['args']}`")
                if m.content:
                    st.markdown(avec_sources(str(m.content)))
        elif m.type == "tool":
            with st.expander(f"↳ résultat de `{noms.get(m.tool_call_id, m.name or 'outil')}`"):
                st.text(str(m.content)[:2000])


def bandeau_decision(v: dict) -> None:
    cols = [("🛡️ Garde-fou", v.get("decision")), ("🏷️ Catégorie", v.get("categorie")),
            ("🚦 Priorité", v.get("priorite")), ("📥 File", v.get("file"))]
    cols = [(t, x) for t, x in cols if x]
    if cols:
        for c, (t, x) in zip(st.columns(len(cols)), cols):
            c.metric(t, x)
    if v.get("resume"):
        st.caption(f"📐 résumé structuré : « {v['resume']} »")


def apres_execution(trace_id, question: str) -> None:
    """Ce qui suit une exécution : supervision, scores, lien vers la trace."""
    if M.b["robustesse"] and M.m.RESEAU.appels:
        st.info(f"📡 supervision : {M.m.RESEAU.appels} appel(s), dont {min(M.m.RESEAU.appels, pannes)} en échec")
    if M.b["securite"] and trace_id and thread:
        v = M.etat(thread)["valeurs"]
        reponse = next((str(m.content) for m in reversed(v.get("messages", [])[:-1])
                        if m.type == "ai" and m.content and not getattr(m, "tool_calls", None)), "")
        with st.spinner("Scores…"):
            scores = M.poser_scores(trace_id, v.get("decision", "?"), reponse, question, juger=juger)
        st.session_state.scores = scores
    if trace_id:
        st.session_state.derniere_trace = trace_id


# =============================================================================== les onglets
noms_onglets = ["💬 Agent N1"]
if M.b["validation"]:
    noms_onglets.append("🧑‍💼 Superviseur")
if M.b["graphe"]:
    noms_onglets.append("🗺️ Le graphe")
if M.b["validation"]:
    noms_onglets.append("📒 Tickets")
if M.b["securite"]:
    noms_onglets.append("📊 Évaluation")
onglets = dict(zip(noms_onglets, st.tabs(noms_onglets)))

# ----------------------------------------------------------------------------- l'agent N1
with onglets["💬 Agent N1"]:
    if not M.b["graphe"]:
        # Étapes 0 à 2 : des fonctions ; la console garde l'historique, le système, lui, ne se souvient de rien
        cle = f"historique_{M.chemin.name}"
        historique = st.session_state.setdefault(cle, [])
        session = f"console-etape-{M.etape}"
        for h in historique:
            with st.chat_message("user"):
                st.markdown(h["ticket"])
            with st.chat_message("assistant", avatar="🎧"):
                bandeau_decision(h)
                if "brute" in h:
                    st.caption(f"le modèle a répondu {h['brute']!r}, normalisé par la règle")
                for nom_outil, args in h.get("outils", []):
                    st.markdown(f"🧰 `{nom_outil}` · `{args}`")
                if h.get("reponse"):
                    st.markdown(avec_sources(h["reponse"]))
        if M.etape == 0:
            st.caption("Étape 0 : une trace « tri-ticket » par ticket, isolée (les sessions arrivent à l'étape 1).")
        ticket = st.chat_input("Décrivez le problème…")
        if ticket:
            with st.spinner("Tri en cours…"):
                historique.append({"ticket": ticket, **M.traiter_sans_graphe(ticket, session)})
            st.rerun()
    else:
        etat = M.etat(thread) if thread else {"valeurs": {}, "attente": []}
        if not M.b["memoire"]:
            st.caption("Étape 3 : pas encore de mémoire. Chaque ticket repart de zéro (la mémoire arrive à l'étape 4).")
            for h in st.session_state.setdefault(f"historique_{M.chemin.name}", []):
                with st.chat_message("user"):
                    st.markdown(h["ticket"])
                with st.chat_message("assistant", avatar="🎧"):
                    st.markdown("\n\n".join(h["journal"]))
        else:
            bandeau_decision(etat["valeurs"])
            afficher_messages(etat["valeurs"].get("messages", []))
            if st.session_state.get("scores") and M.b["securite"]:
                s = st.session_state.scores
                st.caption("⭐ scores posés par le code : " + " · ".join(f"{k} = {v}" for k, v in s.items()))
        if st.session_state.get("derniere_trace"):
            lien = M.lien_langfuse(trace_id=st.session_state.derniere_trace)
            if lien:
                st.link_button("🔭 La trace de ce tour dans Langfuse", lien)

        if etat["attente"]:
            st.warning(f"⏸ **{etat['attente'][0].get('question', 'En attente')}** Ce thread attend la décision "
                       "d'un superviseur (onglet 🧑‍💼 Superviseur, ou dans un autre navigateur). "
                       "Le graphe est arrêté, son état est dans le checkpoint.")
        ticket = st.chat_input("Décrivez le problème…", disabled=bool(etat["attente"]))
        if ticket:
            with st.chat_message("user"):
                st.markdown(ticket)
            gauche, droite = st.columns([2, 3])
            zone_graphe, zone_journal = gauche.empty(), droite.empty()
            trace_id, _, journal = derouler({"ticket": ticket}, thread, zone_graphe, zone_journal)
            apres_execution(trace_id, ticket)
            if not M.b["memoire"]:
                st.session_state[f"historique_{M.chemin.name}"].append({"ticket": ticket, "journal": journal})
            st.rerun()

# ----------------------------------------------------------------------------- le superviseur
if "🧑‍💼 Superviseur" in onglets:
    with onglets["🧑‍💼 Superviseur"]:
        st.markdown("### Les P1 qui attendent une décision")
        st.caption("Toutes les conversations du fichier SQLite, terminal compris. Approuver ou refuser reprend le graphe "
                   "là où il s'est arrêté : `Command(resume=…)` sur le même thread_id.")
        attentes = M.en_attente()
        if not attentes:
            st.success("Rien à valider.")
        qui = st.text_input("Votre nom", value="marie", key="superviseur")
        for t, q in attentes:
            with st.container(border=True):
                st.markdown(f"**{q.get('ticket', '?')}**")
                st.caption(f"thread `{t}` · {q.get('categorie', '?')} · file {q.get('file', '?')}")
                motif = st.text_input("Motif (en cas de refus)", key=f"motif_{t}")
                a, r, _ = st.columns([1, 1, 2])
                decision = None
                if a.button("✅ Approuver", key=f"ok_{t}"):
                    decision = "approuver"
                if r.button("⛔ Refuser", key=f"non_{t}"):
                    decision = "refuser"
                if decision:
                    from langgraph.types import Command
                    gauche, droite = st.columns([2, 3])
                    derouler(Command(resume={"action": decision, "par": qui, "motif": motif}),
                                           t, gauche.empty(), droite.empty())
                    st.toast(f"Décision de {qui} : {decision} ({t})")
                    st.rerun()

# ----------------------------------------------------------------------------- le graphe
if "🗺️ Le graphe" in onglets:
    with onglets["🗺️ Le graphe"]:
        st.graphviz_chart(dot(M.graphe))
        st.caption("Flèches pleines : arêtes fixes · pointillés : arêtes conditionnelles (le code choisit la suite).")
        lignes = M.m.SOURCE.splitlines()
        fin = (M.m.LIGNE_PROGRAMME or len(lignes) + 1) - 1
        st.markdown(f"La console exécute les **lignes 1 à {fin}** de `{M.chemin.name}` : le système. "
                    f"Le programme du terminal commence ligne {fin + 1} ; la console le remplace.")
        with st.expander("Le schéma Mermaid"):
            st.code(M.graphe.get_graph().draw_mermaid(), language="text")

# ----------------------------------------------------------------------------- les tickets
if "📒 Tickets" in onglets:
    with onglets["📒 Tickets"]:
        tickets = M.tickets()
        if tickets:
            st.dataframe(sorted(tickets, key=lambda t: t["id"], reverse=True), hide_index=True,
                         column_order=["id", "priorite", "categorie", "file", "resume", "valide_par", "thread"],
                         use_container_width=True)
        else:
            st.info("Aucun ticket pour l'instant.")
        st.caption(f"Le « ITSM » de NordTech : `{getattr(M.m, 'TICKETS', '')}`. La clé thread + demande rend "
                   "la création idempotente : rejouer ne crée pas de doublon.")

# ----------------------------------------------------------------------------- l'évaluation
if "📊 Évaluation" in onglets:
    with onglets["📊 Évaluation"]:
        st.markdown("### L'experiment sur le dataset versionné (20 cas)")
        st.caption("Rejoue le garde-fou et le tri de CE fichier sur data/eval/helpdesk_eval.json, puis publie le run "
                   "dans Langfuse › Datasets › helpdesk-ops-eval › Experiments. Long sans GPU.")
        if st.button("▶ Lancer l'experiment"):
            sortie = st.empty()
            lignes = []
            racine = next(p for p in M.chemin.resolve().parents if (p / "infrastructure").is_dir())
            with subprocess.Popen([sys.executable, str(M.chemin), "--evaluer"], cwd=racine, text=True,
                                  encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as p:
                for ligne in p.stdout:
                    lignes.append(ligne.rstrip())
                    sortie.code("\n".join(lignes[-40:]), language="text")
            st.success("Terminé.") if p.returncode == 0 else st.error(f"Code de sortie {p.returncode}")

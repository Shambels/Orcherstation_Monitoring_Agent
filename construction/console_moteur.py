"""Le moteur de la console HelpDesk-Ops : charge une étape (ou votre atelier) et l'exécute pour l'interface.

La console ne modifie pas les étapes. Elle exécute la partie « système » du fichier (les règles, les outils,
les nœuds, le builder…) et s'arrête là où commence la partie « terminal » (le premier `with`, `for`,
`async def main`, `print`, `dessiner(...)`, `.invoke(...)` au niveau du module). Le graphe que vous écrivez
devient ainsi le moteur d'un outil, sans une ligne de plus.

Ce module ne dépend pas de Streamlit : il est testé par test_console.py, avec le faux modèle des étapes.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import queue
import sqlite3
import sys
import threading
import types
from dataclasses import dataclass
from pathlib import Path

from langfuse import observe, propagate_attributes

ICI = Path(__file__).resolve().parent
UTILISATEUR = "agent-n1-042"                     # un pseudonyme, jamais un e-mail (étape 1)

# Les appels au niveau du module qui signalent le début du programme du terminal
APPELS_PROGRAMME = {"print", "dessiner", "invoke", "ainvoke", "stream", "astream", "mkdir", "flush"}


# --------------------------------------------------------------------------- charger un fichier
def debut_programme(source: str) -> int | None:
    """Numéro de la première ligne du programme du terminal, ou None si le fichier n'en a pas."""
    for stmt in ast.parse(source).body:
        if isinstance(stmt, (ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
            return stmt.lineno
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "main":
            return min([stmt.lineno] + [d.lineno for d in stmt.decorator_list])
        if isinstance(stmt, (ast.Expr, ast.Assign)) and isinstance(stmt.value, ast.Call):
            f = stmt.value.func
            if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) in APPELS_PROGRAMME:
                return stmt.lineno
    return None


def charger(chemin: Path) -> types.ModuleType:
    """Exécute la partie « système » du fichier dans un module neuf ; le programme du terminal n'est pas lancé."""
    source = chemin.read_text(encoding="utf-8")
    ligne = debut_programme(source)
    systeme = "\n".join(source.splitlines()[: ligne - 1]) if ligne else source
    nom = f"console_{chemin.stem}"
    module = types.ModuleType(nom)
    module.__file__ = str(chemin)
    sys.modules[nom] = module                    # les annotations (EtatTicket, Command[…]) se résolvent ici
    argv, sys.argv = sys.argv, [str(chemin)]     # les étapes lisent leurs options : la console prend les défauts
    try:
        exec(compile(systeme, str(chemin), "exec"), module.__dict__)
    finally:
        sys.argv = argv
    module.LIGNE_PROGRAMME = ligne
    module.SOURCE = source
    return module


def fichiers_disponibles(dossier: Path | None = None) -> list[Path]:
    """Les fichiers que la console peut brancher, votre atelier d'abord.

    - console dans construction/ : construction/atelier/*.py, puis les étapes, puis les corrections des défis ;
    - console copiée dans construction/atelier/ : tous les .py de ce dossier (mon_helpdesk.py en tête, puis vos
      copies : mon_defi_a.py, une étape distribuée…).
    HELPDESK_CONSOLE_DOSSIER : un autre dossier (tests).
    """
    dossier = dossier or Path(os.environ.get("HELPDESK_CONSOLE_DOSSIER", ICI))

    def travail(d: Path) -> list[Path]:
        if not d.is_dir():
            return []
        f = [p for p in d.glob("*.py") if p.name not in OUTILLAGE and not p.name.startswith(("test_", "_"))]
        return sorted(f, key=lambda p: (p.name != "mon_helpdesk.py", p.name))

    if dossier.name == "atelier":
        return travail(dossier)
    return (travail(dossier / "atelier") + sorted(dossier.glob("etape_*.py")) + sorted(dossier.glob("defi_*.py")))


OUTILLAGE = {"console.py", "console_moteur.py", "comparer.py", "conftest.py"}   # jamais proposés comme « système »


def numero_etape(module: types.ModuleType) -> int:
    """L'étape atteinte, déduite des briques présentes (votre atelier n'a pas de numéro dans son nom)."""
    b = briques(module)
    return max((n for n, (cle, _) in enumerate(BRIQUES) if b[cle]), default=0)


# --------------------------------------------------------------------------- les briques de la carte
BRIQUES = [  # (clé, libellé) dans l'ordre de la carte d'architecture : l'index est l'étape qui l'ajoute
    ("tri", "🏷️ le tri et les règles métier"),
    ("session", "🔭 sessions et @observe"),
    ("outils", "🧰 l'agent et ses outils"),
    ("graphe", "🗺️ le StateGraph"),
    ("memoire", "💾 la mémoire des conversations"),
    ("validation", "✍️ la validation humaine et le ticket"),
    ("robustesse", "📐 sortie structurée et robustesse"),
    ("specialistes", "🧭 RAG, spécialistes et MCP"),
    ("securite", "🛡️ garde-fou et évaluation"),
]


def briques(m: types.ModuleType) -> dict[str, bool]:
    a = lambda *noms: all(hasattr(m, n) for n in noms)
    return {
        "tri": a("tri"),
        "session": a("traiter_ticket") or a("builder"),
        "outils": a("agent") or a("OUTILS") or a("coordinateur"),
        "graphe": a("builder"),
        "memoire": a("builder", "BASE"),
        "validation": a("valider", "creer_ticket"),
        "robustesse": a("TicketDecision", "RESEAU"),
        "specialistes": a("coordinateur"),
        "securite": a("decider"),
    }


# --------------------------------------------------------------------------- une boucle asyncio pour la console
class Boucle:
    """Une seule boucle, dans un fil à part : les clients asynchrones (Ollama, aiosqlite, MCP) y restent attachés."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None):
        self.loop = loop or asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="console-asyncio").start()

    def soumettre(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def executer(self, coro, timeout: float | None = None):
        return self.soumettre(coro).result(timeout)


@dataclass
class Evenement:
    type: str                 # "noeud", "pause", "fin", "erreur"
    noeud: str = ""
    valeur: object = None


# --------------------------------------------------------------------------- le moteur
class Moteur:
    def __init__(self, chemin: Path, boucle: Boucle):
        self.chemin = chemin
        self.m = charger(chemin)
        # Étapes 7 et 8 : le fichier a sa boucle (BOUCLE), à laquelle le client MCP est attaché : on la reprend
        self.boucle = Boucle(self.m.BOUCLE) if isinstance(getattr(self.m, "BOUCLE", None), asyncio.AbstractEventLoop) else boucle
        self.b = briques(self.m)
        self.etape = numero_etape(self.m)
        self.memoire = None
        self.graphe = None
        if self.b["graphe"]:
            if self.b["memoire"]:
                self.boucle.executer(self._ouvrir_memoire())
            else:
                self.graphe = self.m.builder.compile()

    async def _ouvrir_memoire(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver   # même fichier, mêmes tables que SqliteSaver
        self.m.BASE.parent.mkdir(parents=True, exist_ok=True)
        self._cm = AsyncSqliteSaver.from_conn_string(str(self.m.BASE))
        self.memoire = await self._cm.__aenter__()
        self.graphe = self.m.builder.compile(checkpointer=self.memoire)

    # ----- ce qu'on montre
    @property
    def tags(self) -> list[str]:
        return ["console", f"etape-{self.etape}"]

    def config(self, thread: str | None = None) -> dict:
        c = {"callbacks": [self.m.handler], "run_name": "helpdesk", "recursion_limit": 25}
        if thread:
            c["configurable"] = {"thread_id": thread}
        return c

    def threads(self) -> list[str]:
        """Les conversations du fichier SQLite, la plus récente d'abord (le terminal et la console partagent le fichier)."""
        if not self.b["memoire"] or not self.m.BASE.exists():
            return []
        with sqlite3.connect(f"file:{self.m.BASE}?mode=ro", uri=True) as cx:
            try:
                lignes = cx.execute("SELECT thread_id, MAX(checkpoint_id) FROM checkpoints "
                                    "GROUP BY thread_id ORDER BY 2 DESC").fetchall()
            except sqlite3.OperationalError:          # fichier neuf : pas encore de table
                return []
        return [t for t, _ in lignes]

    def etat(self, thread: str) -> dict:
        snap = self.boucle.executer(self.graphe.aget_state(self.config(thread)))
        attente = [i.value for t in snap.tasks for i in t.interrupts]
        return {"valeurs": snap.values or {}, "attente": attente}

    def en_attente(self) -> list[tuple[str, dict]]:
        """Les P1 en pause, toutes conversations confondues : la boîte du superviseur."""
        if not self.b["validation"]:
            return []
        return [(t, a) for t in self.threads() for a in self.etat(t)["attente"]]

    def oublier(self, thread: str) -> None:
        self.boucle.executer(self.memoire.adelete_thread(thread))

    def tickets(self) -> list[dict]:
        chemin = getattr(self.m, "TICKETS", None)
        if not isinstance(chemin, Path) or not chemin.exists():
            return []
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        return [{"thread": cle.split(":", 1)[0], **t} for cle, t in donnees.items()]

    def mcp(self) -> list[tuple[str, bool]]:
        if not hasattr(self.m, "OUTILS_MCP_PROPOSES"):
            return []
        retenus = {o.name for o in self.m.OUTILS_MCP}
        return [(o.name, o.name in retenus) for o in self.m.OUTILS_MCP_PROPOSES]

    def lien_langfuse(self, *, trace_id: str | None = None, session: str | None = None) -> str | None:
        base = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000").rstrip("/")
        projet = os.environ.get("LANGFUSE_INIT_PROJECT_ID")
        if trace_id:
            try:
                return self.m.langfuse.get_trace_url(trace_id=trace_id)
            except Exception:
                return f"{base}/project/{projet}/traces/{trace_id}" if projet else None
        if session and projet:
            return f"{base}/project/{projet}/sessions/{session}"
        return f"{base}/project/{projet}/traces" if projet else base

    # ----- étapes 0 à 2 : des fonctions, pas encore de graphe
    def traiter_sans_graphe(self, ticket: str, session: str) -> dict:
        m = self.m
        if hasattr(m, "traiter_ticket"):
            with propagate_attributes(session_id=session, user_id=UTILISATEUR, tags=self.tags):
                resultat = m.traiter_ticket(ticket)
        else:                                   # étape 0 : la chaîne seule, tracée par le CallbackHandler
            brute = m.tri.invoke({"ticket": ticket, "categories": ", ".join(m.CATEGORIES)},
                                 config={"callbacks": [m.handler], "run_name": "tri-ticket"})
            categorie = m.normaliser_categorie(brute)
            resultat = {"categorie": categorie, "priorite": m.estimer_priorite(ticket),
                        "file": m.file_attente(categorie), "brute": brute}
        m.langfuse.flush()
        return resultat

    # ----- étapes 3 à 8 : le graphe, nœud par nœud
    def lancer(self, entree, thread: str | None, file: queue.Queue, *, pannes: int = 0):
        """Démarre le graphe dans la boucle ; chaque nœud terminé arrive dans `file` (Evenement)."""
        if self.b["robustesse"]:
            self.m.RESEAU.configurer(echecs_avant_succes=pannes)
        return self.boucle.soumettre(self._derouler(entree, thread, file))

    async def _derouler(self, entree, thread, file: queue.Queue):
        try:
            with propagate_attributes(session_id=thread or f"console-etape-{self.etape}", user_id=UTILISATEUR,
                                      tags=self.tags):
                trace_id = await self._trace(entree, thread, file)
            file.put(Evenement("fin", valeur=trace_id))
        except BaseException as e:                 # l'interface affiche l'erreur au lieu de se figer
            file.put(Evenement("erreur", valeur=e))
        finally:
            self.m.langfuse.flush()

    @observe(name="helpdesk-console", capture_input=False, capture_output=False)
    async def _trace(self, entree, thread, file: queue.Queue) -> str | None:
        async for maj in self.graphe.astream(entree, config=self.config(thread), stream_mode="updates"):
            for noeud, valeur in maj.items():
                if noeud == "__interrupt__":
                    file.put(Evenement("pause", noeud="valider", valeur=[i.value for i in valeur]))
                else:
                    file.put(Evenement("noeud", noeud=noeud, valeur=valeur))
        return self.m.langfuse.get_current_trace_id()

    # ----- étape 8 : les scores posés par le code (et le juge, sur demande)
    def poser_scores(self, trace_id: str, decision: str, reponse: str, question: str, juger: bool = False) -> dict:
        m, scores = self.m, {}
        m.langfuse.create_score(trace_id=trace_id, name="garde_fou", value=decision, data_type="CATEGORICAL")
        scores["garde_fou"] = decision
        if decision == "SUPPORT" and reponse:
            cite = "[KB-" in reponse
            m.langfuse.create_score(trace_id=trace_id, name="sources_citees", value=float(cite), data_type="BOOLEAN")
            scores["sources_citees"] = cite
            if juger:
                m.basculer_vllm("juge")
                try:
                    j = m.creer_juge(m.creer_llm("judge", reasoning=False))(reponse, m.kb.formater(m.kb.rechercher(question, k=3)))
                finally:
                    m.basculer_vllm("agents")
                if j is not None:
                    note = m.noter(j)
                    m.langfuse.create_score(trace_id=trace_id, name="fidelite", value=note, data_type="NUMERIC",
                                            comment=f"sans source : {j.affirmations_sans_source[:3]}")
                    scores["fidelite"] = note
                    scores["sans_source"] = j.affirmations_sans_source[:3]
        m.langfuse.flush()
        return scores


# --------------------------------------------------------------------------- dessiner le graphe (Graphviz, sans service en ligne)
def dot(graphe, *, faits: set[str] = frozenset(), courant: str | None = None, pause: bool = False) -> str:
    g = graphe.get_graph()
    lignes = ['digraph G {', 'rankdir=TB; bgcolor="transparent"; pad=0.2;',
              'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=12, '
              'fillcolor="#e2e8f0", color="#64748b", fontcolor="#0f172a"];',
              'edge [color="#64748b", arrowsize=0.7];']
    for nid in g.nodes:
        if nid in ("__start__", "__end__"):
            lignes.append(f'"{nid}" [label="{"début" if nid == "__start__" else "fin"}", shape=ellipse, '
                          f'fillcolor="#cbd5e1"];')
            continue
        if nid == courant:
            couleur = "#fbbf24" if not pause else "#f87171"
        elif nid in faits:
            couleur = "#86efac"
        else:
            couleur = "#e2e8f0"
        etiquette = f"⏸ {nid}" if pause and nid == courant else nid
        lignes.append(f'"{nid}" [label="{etiquette}", fillcolor="{couleur}"];')
    for e in g.edges:
        style = ' [style=dashed]' if e.conditional else ''
        lignes.append(f'"{e.source}" -> "{e.target}"{style};')
    lignes.append("}")
    return "\n".join(lignes)


# --------------------------------------------------------------------------- raconter un nœud en une ligne
def raconter(noeud: str, valeur) -> str:
    v = valeur if isinstance(valeur, dict) else {}
    msgs = v.get("messages") or []
    dernier = msgs[-1] if msgs else None
    if noeud == "garde_fou":
        return f"🛡️ **garde-fou** : {v.get('decision', '?')}"
    if noeud == "trier":
        res = f" · « {v['resume']} »" if v.get("resume") else ""
        return f"🏷️ **trier** : {v.get('categorie', '?')} · {v.get('priorite', '?')} · file {v.get('file', '?')}{res}"
    if dernier is not None and getattr(dernier, "tool_calls", None):
        verbe = "délègue à" if noeud == "coordonner" else "demande"
        appels = ", ".join(f"`{a['name']}({a['args']})`" for a in dernier.tool_calls)
        return f"🤖 **{noeud}** : {verbe} {appels}"
    if noeud == "tools":
        return f"🔧 **tools** : {str(dernier.content)[:120] if dernier else ''}"
    if noeud == "creer_ticket":
        return f"🎫 **creer_ticket** : {dernier.content if dernier else ''}"
    if noeud == "refus_poli":
        return f"🙅 **refus_poli** : {dernier.content if dernier else ''}"
    if noeud == "valider":
        return "✍️ **valider** : décision du superviseur appliquée"
    if dernier is not None:
        return f"🤖 **{noeud}** : répond « {str(dernier.content)[:160]} »"
    return f"▶ **{noeud}**"

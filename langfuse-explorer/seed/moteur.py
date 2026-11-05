"""
moteur.py : construit des traces HelpDesk-Ops synthétiques et les convertit en spans OpenTelemetry.

Pourquoi ne pas simplement appeler le SDK Langfuse ?
  Le SDK horodate chaque observation à l'instant présent. Pour remplir les dashboards
  et les filtres de période, le seed doit placer les traces sur les 7 derniers jours.
  On construit donc les spans nous-mêmes (ReadableSpan) avec :
    - des horodatages explicites (début, fin, premier token) ;
    - des identifiants DÉTERMINISTES (trace_id dérivé d'une clé stable) : chaque poste
      obtient les mêmes traces, et le guide peut pointer directement vers une trace ;
    - EXACTEMENT les attributs et la portée d'instrumentation que produit le SDK 4.15.6
      (fonctions d'attributs du SDK réutilisées), pour un rendu identique dans l'UI.

Aucun appel réseau ici : ce module ne fait que du calcul (testable hors ligne).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langfuse._client.attributes import (  # version figée (4.15.6) : voir pyproject.toml
    LangfuseOtelSpanAttributes as A,
    _flatten_and_serialize_metadata,
    create_generation_attributes,
    create_span_attributes,
)

DATA = Path(__file__).parent / "data"
SDK_VERSION = "4.15.6"

# --------------------------------------------------------------------------------------
# Identifiants déterministes
# --------------------------------------------------------------------------------------


def trace_id_de(cle: str) -> str:
    """32 caractères hexadécimaux minuscules : format attendu par Langfuse et OTel."""
    return hashlib.sha256(f"lfx-trace:{cle}".encode()).hexdigest()[:32]


def span_id_de(cle: str, chemin: str) -> str:
    return hashlib.sha256(f"lfx-span:{cle}:{chemin}".encode()).hexdigest()[:16]


def ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def tokens(texte: Any) -> int:
    """Estimation grossière mais stable : ~3,6 caractères par token en français."""
    if not isinstance(texte, str):
        texte = json.dumps(texte, ensure_ascii=False)
    return max(1, round(len(texte) / 3.6))


# --------------------------------------------------------------------------------------
# Modèle d'observation
# --------------------------------------------------------------------------------------


@dataclass
class Obs:
    nom: str
    type: str = "span"                      # span, generation, agent, tool, chain, retriever,
    ms: float = 5.0                         # embedding, guardrail, evaluator, event
    entree: Any = None
    sortie: Any = None
    metadata: dict | None = None
    niveau: str | None = None               # DEBUG, DEFAULT, WARNING, ERROR
    message: str | None = None              # status_message
    modele: str | None = None
    usage: dict | None = None
    params: dict | None = None
    prompt: tuple[str, int] | None = None   # (nom, version)
    ttft_ms: float | None = None            # délai avant le premier token (générations)
    enfants: list["Obs"] = field(default_factory=list)
    pause_avant_ms: float = 2.0
    # calculés par placer()
    debut: datetime | None = None
    fin: datetime | None = None
    span_id: str | None = None

    def ajouter(self, *obs: "Obs") -> "Obs":
        self.enfants.extend(obs)
        return self


@dataclass
class Trace:
    cle: str
    racine: Obs
    debut: datetime
    user: str | None = None
    session: str | None = None
    environnement: str = "production"
    release: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    scores: list[dict] = field(default_factory=list)      # {nom, valeur, commentaire, cible}
    commentaire: str | None = None
    file_annotation: bool = False
    faits: dict = field(default_factory=dict)             # pour le corrigé et verify.py

    @property
    def trace_id(self) -> str:
        return trace_id_de(self.cle)

    @property
    def nom(self) -> str:
        return self.racine.nom


def placer(trace: Trace) -> None:
    """Calcule début/fin de chaque observation : enfants séquentiels dans le parent."""

    def visiter(o: Obs, debut: datetime, chemin: str) -> datetime:
        o.debut = debut
        o.span_id = span_id_de(trace.cle, chemin)
        curseur = debut + timedelta(milliseconds=1.5)
        for i, enfant in enumerate(o.enfants):
            curseur += timedelta(milliseconds=enfant.pause_avant_ms)
            curseur = visiter(enfant, curseur, f"{chemin}/{i}")
        fin_propre = debut + timedelta(milliseconds=o.ms)
        o.fin = max(fin_propre, curseur + timedelta(milliseconds=1.5)) if o.enfants else fin_propre
        if o.type == "event":
            o.fin = o.debut
        return o.fin

    visiter(trace.racine, trace.debut, "0")


def parcourir(o: Obs):
    yield o
    for e in o.enfants:
        yield from parcourir(e)


# --------------------------------------------------------------------------------------
# Conversion en spans OpenTelemetry (format identique au SDK Langfuse 4.15.6)
# --------------------------------------------------------------------------------------

GENERATION_LIKE = {"generation", "embedding"}


def attributs(trace: Trace, o: Obs, est_racine: bool) -> dict:
    if o.type in GENERATION_LIKE:
        attrs = create_generation_attributes(
            completion_start_time=(o.debut + timedelta(milliseconds=o.ttft_ms)) if o.ttft_ms else None,
            metadata=o.metadata, level=o.niveau, status_message=o.message,
            model=o.modele, model_parameters=o.params, input=o.entree, output=o.sortie,
            usage_details=o.usage, observation_type=o.type,
        )
        if o.prompt:
            attrs[A.OBSERVATION_PROMPT_NAME] = o.prompt[0]
            attrs[A.OBSERVATION_PROMPT_VERSION] = o.prompt[1]
    else:
        attrs = create_span_attributes(
            metadata=o.metadata, input=o.entree, output=o.sortie,
            level=o.niveau, status_message=o.message, observation_type=o.type,
        )
    # Attributs « de trace » propagés sur chaque observation (comme propagate_attributes)
    attrs[A.ENVIRONMENT] = trace.environnement
    if trace.release:
        attrs[A.RELEASE] = trace.release
    attrs[A.TRACE_NAME] = trace.nom
    if trace.user:
        attrs[A.TRACE_USER_ID] = trace.user
    if trace.session:
        attrs[A.TRACE_SESSION_ID] = trace.session
    if trace.tags:
        attrs[A.TRACE_TAGS] = list(trace.tags)
    if trace.metadata:
        attrs.update(_flatten_and_serialize_metadata(trace.metadata, "trace"))
    if est_racine:
        attrs[A.IS_APP_ROOT] = True
    return attrs


def vers_spans(trace: Trace, public_key: str) -> list:
    """Retourne des ReadableSpan prêts pour OTLPSpanExporter.export()."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import SpanContext, SpanKind, TraceFlags

    placer(trace)
    tid = int(trace.trace_id, 16)
    scope = InstrumentationScope("langfuse-sdk", SDK_VERSION, None, {"public_key": public_key})
    resource = Resource.create({
        "service.name": "helpdesk-ops",
        "langfuse.environment": trace.environnement,
        **({"langfuse.release": trace.release} if trace.release else {}),
    })
    spans = []

    def visiter(o: Obs, parent: Obs | None):
        ctx = SpanContext(tid, int(o.span_id, 16), is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED))
        pctx = (SpanContext(tid, int(parent.span_id, 16), is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED))
                if parent else None)
        spans.append(ReadableSpan(
            name=o.nom, context=ctx, parent=pctx, resource=resource,
            attributes=attributs(trace, o, parent is None), kind=SpanKind.INTERNAL,
            start_time=ns(o.debut), end_time=ns(o.fin), instrumentation_scope=scope,
        ))
        for e in o.enfants:
            visiter(e, o)

    visiter(trace.racine, None)
    return spans


# --------------------------------------------------------------------------------------
# Données métier (fictives) : articles KB et jeu d'évaluation du cours
# --------------------------------------------------------------------------------------


def charger_kb() -> dict[str, dict]:
    kb = {}
    for f in sorted((DATA / "kb").glob("KB-*.md")):
        texte = f.read_text(encoding="utf-8")
        titre = re.search(r"^titre:\s*(.+)$", texte, re.M).group(1).strip()
        etapes = re.findall(r"^\d+\.\s+(.+)$", texte, re.M)
        kb[f.stem] = {"id": f.stem, "titre": titre, "etapes": etapes[:4]}
    return kb


KB = charger_kb()
CAS = {c["id"]: c for c in json.loads((DATA / "helpdesk_eval.json").read_text(encoding="utf-8"))["cas"]}
REDTEAM = {c["id"]: c for c in json.loads((DATA / "redteam.json").read_text(encoding="utf-8"))["cas"]}

# Débits simulés (tokens/s) : (lecture du prompt, génération). Ordres de grandeur réalistes.
DEBITS = {
    ("vllm-gpu", "granite4.1:8b"): (4500, 75),
    ("vllm-gpu", "granite4.1:3b"): (9000, 160),
    ("vllm-gpu", "gemma4:12b"): (2800, 45),
    ("ollama-cpu", "granite4.1:8b"): (55, 6.5),
    ("ollama-cpu", "granite4.1:3b"): (140, 16),
    ("ollama-cpu", "gemma4:12b"): (30, 3.5),
}


def generation(nom: str, modele: str, backend: str, messages: Any, sortie: Any, *,
               prompt: tuple[str, int] | None = None, temperature: float = 0,
               tokens_entree: int | None = None, metadata: dict | None = None,
               niveau: str | None = None, message: str | None = None, rng: random.Random | None = None) -> Obs:
    t_in = tokens_entree or tokens(messages) + 40
    t_out = tokens(sortie) + 2
    lecture, debit = DEBITS[(backend, modele)]
    bruit = (rng.uniform(0.85, 1.2) if rng else 1.0)
    ttft = (t_in / lecture * 1000 + (35 if backend == "vllm-gpu" else 400)) * bruit
    total = ttft + t_out / debit * 1000 * bruit
    return Obs(nom, "generation", ms=total, entree=messages, sortie=sortie, modele=modele,
               usage={"input": t_in, "output": t_out}, params={"temperature": temperature},
               prompt=prompt, ttft_ms=ttft, metadata=metadata, niveau=niveau, message=message)


def compiler(template: str, **variables) -> str:
    for k, v in variables.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


# --------------------------------------------------------------------------------------
# Gabarit principal : le graphe HelpDesk-Ops du cours
#   garde-fou -> qualification -> superviseur (routage) -> spécialiste (outil, KB) -> rédaction
# --------------------------------------------------------------------------------------

OUTIL_PAR_SPECIALISTE = {"kb": "rechercher_procedures", "diagnostic": "statut_serveur",
                         "ticketing": "tickets_ouverts", "acces": "etat_compte"}


def reponse_kb(kb_ids: list[str], langue: str = "fr") -> str:
    if not kb_ids:
        return "Je n'ai pas trouvé de procédure correspondante. Je transmets votre demande à l'équipe N1, qui vous recontactera."
    art = KB[kb_ids[0]]
    etapes = "\n".join(f"{i}. {e}" for i, e in enumerate(art["etapes"], 1)) or "1. Suivre la procédure de l'article."
    sources = ", ".join(kb_ids)
    if langue == "nl":
        return f"Volg deze stappen ({art['titre']}):\n{etapes}\nBron: {sources}"
    return f"Voici la procédure « {art['titre']} » :\n{etapes}\nSource : {sources}"


def gabarit_support(p: dict, prompts: dict, rng: random.Random) -> tuple[Obs, dict]:
    """
    p : paramètres d'un scénario (voir 10_signatures.yaml). Retourne (racine, faits).
    prompts : {nom: {version: texte_ou_messages}} pour compiler les entrées des générations.
    """
    q = p["question"]
    q_trace = p.get("question_masquee", q)        # ce qui est réellement envoyé si masquage PII
    backend = p.get("backend", "vllm-gpu")
    m_agent = p.get("modele_agent", "granite4.1:8b")
    m_qualif = p.get("modele_qualification", "granite4.1:3b")
    v_sup = p.get("prompt_superviseur", 3)
    v_red = p.get("prompt_redacteur", 2)
    spe = p.get("specialiste", "kb")
    langue = p.get("langue", "fr")
    faits: dict[str, Any] = {"specialiste": spe, "backend": backend}

    racine = Obs("traiter-demande", "agent", ms=10, entree={"question": q_trace, "canal": p.get("canal", "teams")},
                 metadata={"backend": backend, "langue": langue, "graphe": "helpdesk-ops", "recursion_limit": 25})

    # --- 0. Masquage PII éventuel (avant tout appel LLM) ---------------------------------
    if p.get("pii") and p["pii"].get("masque"):
        ent = p["pii"]["entites"]
        racine.ajouter(Obs("masquage-pii", "guardrail", ms=rng.uniform(4, 9),
                           entree={"texte": "[non journalisé : texte brut]"},
                           sortie={"texte": q_trace, "entites_masquees": ent},
                           metadata={"moteur": "regex + presidio", "politique": "RGPD-art5-minimisation"}))
        faits["entites_masquees"] = sum(ent.values())

    # --- 1. Garde-fou d'entrée -----------------------------------------------------------
    verdict = p.get("verdict_garde_fou", "SUPPORT")
    gf = Obs("garde-fou-entree", "guardrail", ms=rng.uniform(25, 60), entree={"question": q_trace},
             sortie={"verdict": verdict, "regles_declenchees": p.get("regles_garde_fou", [])},
             metadata={"version_regles": "2026-10-22"})
    if verdict != "SUPPORT":
        gf.niveau = "WARNING"
        gf.message = f"Demande refusée : {verdict}"
    racine.ajouter(gf)
    if verdict != "SUPPORT":
        refus = ("Je suis l'assistant du support informatique de NordTech : je ne peux pas traiter cette demande."
                 if verdict == "HORS_PERIMETRE" else
                 "Cette demande ne peut pas être traitée. Le help-desk ne communique jamais de mot de passe ni de droits d'administration.")
        racine.sortie = {"reponse": refus, "refus": verdict}
        faits.update(refus=verdict, reponse=refus)
        return racine, faits

    # --- 2. Qualification (catégorie, priorité) -----------------------------------------
    qualif = {"categorie": p.get("categorie") or "autre", "priorite": p.get("priorite") or "P3"}
    racine.ajouter(generation(
        "qualification", m_qualif, backend,
        [{"role": "user", "content": compiler(prompts["qualification"][1], question=q_trace)}],
        json.dumps(qualif, ensure_ascii=False), prompt=("qualification", 1), rng=rng))

    # --- 3. Superviseur : routage (boucle éventuelle) -----------------------------------
    sup = Obs("superviseur", "agent", ms=3)
    racine.ajouter(sup)
    tpl_sup = prompts["superviseur"][v_sup]
    boucle = p.get("boucle_routage", 0)
    if boucle:
        # Le superviseur alterne kb <-> redaction sans jamais répondre FIN : GraphRecursionError
        for i in range(boucle):
            cible = "kb" if i % 2 == 0 else "redacteur"
            sup.ajouter(generation(f"routage", m_agent, backend,
                                   [{"role": "user", "content": compiler(tpl_sup, question=q_trace, historique=f"{i} étapes")}],
                                   cible, prompt=("superviseur", v_sup), rng=rng,
                                   metadata={"etape": i + 1}))
            if cible == "kb":
                sup.ajouter(Obs("rechercher_procedures", "tool", ms=rng.uniform(40, 90),
                                entree={"requete": q_trace[:60]}, sortie={"resultats": ["KB-011"]}))
        err = "GraphRecursionError: Recursion limit of 25 reached without hitting a stop condition. You can increase the limit by setting the `recursion_limit` config key."
        racine.niveau, racine.message = "ERROR", err
        racine.sortie = {"erreur": "GraphRecursionError"}
        faits.update(erreur="GraphRecursionError", nb_routages=boucle, recursion_limit=25)
        return racine, faits

    sup.ajouter(generation("routage", m_agent, backend,
                           [{"role": "user", "content": compiler(tpl_sup, question=q_trace, historique="(vide)")}],
                           spe, prompt=("superviseur", v_sup), rng=rng))

    # --- 4. Spécialiste ------------------------------------------------------------------
    agent = Obs(f"specialiste-{spe}", "agent", ms=3)
    racine.ajouter(agent)
    outil = p.get("outil") or OUTIL_PAR_SPECIALISTE[spe]
    kb_ids = p.get("kb", [])
    agent.ajouter(generation("choix-outil", m_agent, backend,
                             [{"role": "system", "content": f"Spécialiste {spe}. Outils : {outil}."},
                              {"role": "user", "content": q_trace}],
                             {"tool_calls": [{"name": outil, "arguments": p.get("arguments_outil", {"requete": q_trace[:80]})}]},
                             rng=rng))

    # Tentatives réseau (ping instable)
    for t in range(p.get("echecs_reseau", 0)):
        agent.ajouter(Obs(outil, "tool", ms=5000 + rng.uniform(0, 40), entree=p.get("arguments_outil", {}),
                          sortie={"erreur": "TimeoutError", "detail": "pas de réponse après 5 s"},
                          niveau="WARNING", message=f"Timeout après 5 s (tentative {t + 1}/3), nouvel essai dans {2 ** t} s",
                          metadata={"tentative": t + 1, "retry_policy": "max_attempts=3, backoff=2"},
                          pause_avant_ms=1000 * 2 ** t if t else 0))
    if p.get("echecs_reseau"):
        faits["tentatives"] = p["echecs_reseau"] + 1

    erreur_outil = p.get("erreur_outil")
    if erreur_outil:
        agent.ajouter(Obs(outil, "tool", ms=rng.uniform(120, 300), entree=p.get("arguments_outil", {}),
                          sortie={"erreur": erreur_outil["erreur"]}, niveau="ERROR", message=erreur_outil["message"]))
        outil_repli = erreur_outil.get("repli", "rechercher_procedures")
        faits["outil_en_erreur"] = outil
        outil = outil_repli

    if outil == "rechercher_procedures":
        tool = Obs("rechercher_procedures", "tool", ms=3, entree={"requete": q_trace[:80]})
        emb = Obs("embedding-question", "embedding", ms=rng.uniform(18, 45), entree=q_trace,
                  modele="paraphrase-multilingual-MiniLM-L12-v2", usage={"input": tokens(q_trace)},
                  metadata={"dimension": 384, "device": "cpu"})
        docs = [{"id": k, "titre": KB[k]["titre"], "score": round(0.86 - 0.07 * i + rng.uniform(-0.02, 0.02), 3)}
                for i, k in enumerate(kb_ids)] or [{"id": "KB-017", "titre": KB["KB-017"]["titre"], "score": 0.31}]
        ret = Obs("qdrant-kb_nordtech", "retriever", ms=rng.uniform(8, 25),
                  entree={"collection": "kb_nordtech", "k": 3, "filtre": {"visibilite": "public"}},
                  sortie=docs, metadata={"moteur": "qdrant v1.19.1", "hybride": "dense + bm25"})
        tool.ajouter(emb, ret)
        tool.sortie = {"articles": [d["id"] for d in docs]}
        agent.ajouter(tool)
    else:
        agent.ajouter(Obs(outil, "tool", ms=p.get("ms_outil", rng.uniform(60, 220)),
                          entree=p.get("arguments_outil", {}), sortie=p.get("resultat_outil", {"ok": True})))
    faits["outil"] = outil

    # Interruption humaine (human-in-the-loop) : on s'arrête ici
    if p.get("interruption"):
        evt = Obs("interrupt", "event", entree={"raison": p["interruption"]},
                  sortie={"en_attente": "validation du responsable N2"},
                  metadata={"checkpoint": "AsyncPostgresSaver", "thread_id": p.get("session")})
        racine.ajouter(evt)
        racine.sortie = {"reponse": p["reponse"], "statut": "en_attente_validation"}
        faits.update(statut="en_attente_validation", reponse=p["reponse"])
        return racine, faits

    # --- 5. Rédaction --------------------------------------------------------------------
    reponse = p.get("reponse") or reponse_kb(kb_ids, langue)
    articles = "\n".join(f"[{k}] {KB[k]['titre']} : " + " ".join(KB[k]["etapes"]) for k in kb_ids) or "(aucun)"
    msgs = [{"role": m["role"], "content": compiler(m["content"], question=q_trace, articles_kb=articles)}
            for m in prompts["redacteur"][v_red]]
    m_red = p.get("modele_redaction", m_agent)
    red = generation("redaction-reponse", m_red, backend, msgs, reponse, prompt=("redacteur", v_red),
                     temperature=0.2, tokens_entree=p.get("tokens_contexte"), rng=rng)
    agent.ajouter(red)

    # --- 6. Filtre de sortie + événements ------------------------------------------------
    racine.ajouter(Obs("filtre-sortie", "guardrail", ms=rng.uniform(3, 8), entree={"longueur": len(reponse)},
                       sortie={"pii_detectees": p.get("pii_en_sortie", 0), "action": "aucune"}))
    sortie = {"reponse": reponse, "specialiste": spe, **qualif}
    if p.get("ticket"):
        racine.ajouter(Obs("ticket-cree", "event", entree={"file": p["ticket"]["file"]},
                           sortie={"numero": p["ticket"]["numero"], "priorite": qualif["priorite"]}))
        sortie["ticket"] = p["ticket"]["numero"]
        faits["ticket"] = p["ticket"]["numero"]
    racine.sortie = sortie
    faits.update(reponse=reponse, **qualif)
    return racine, faits


def gabarit_reprise(p: dict, rng: random.Random) -> tuple[Obs, dict]:
    """Reprise après interrupt : le responsable N2 a validé, on crée le ticket P1."""
    racine = Obs("reprendre-apres-validation", "agent", ms=10,
                 entree={"decision": p["decision"], "valide_par": p["valide_par"], "commentaire": p.get("commentaire_validation")},
                 metadata={"checkpoint": "AsyncPostgresSaver", "thread_id": p.get("session"), "reprise": "Command(resume=...)"})
    racine.ajouter(Obs("creer_ticket", "tool", ms=rng.uniform(150, 260),
                       entree={"file": p["ticket"]["file"], "priorite": "P1", "titre": p["titre_ticket"]},
                       sortie={"numero": p["ticket"]["numero"], "astreinte": "déclenchée"}))
    racine.ajouter(Obs("ticket-cree", "event", entree={"file": p["ticket"]["file"]},
                       sortie={"numero": p["ticket"]["numero"], "priorite": "P1"}))
    racine.sortie = {"reponse": p["reponse"], "ticket": p["ticket"]["numero"]}
    return racine, {"ticket": p["ticket"]["numero"], "valide_par": p["valide_par"]}

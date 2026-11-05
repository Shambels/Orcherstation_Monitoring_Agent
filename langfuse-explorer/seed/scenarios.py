"""
scenarios.py : lit les fichiers YAML de scenarios/ et produit la liste complète des traces,
plus les « faits » attendus (réponses des missions, contrôlées par tools/verify.py).

Entièrement déterministe : même graine, mêmes traces, mêmes identifiants, mêmes réponses.
Seuls les horodatages absolus dépendent de l'instant où le seed est lancé.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

import yaml

from moteur import (CAS, KB, OUTIL_PAR_SPECIALISTE, Obs, Trace, gabarit_reprise,
                    gabarit_support, parcourir, placer, reponse_kb)

DOSSIER = Path(__file__).parent / "scenarios"


def charger(nom: str) -> dict:
    return yaml.safe_load((DOSSIER / nom).read_text(encoding="utf-8"))


REF = charger("00_referentiel.yaml")
SIG = charger("10_signatures.yaml")
TRAFIC = charger("20_trafic.yaml")
EXP = charger("30_experiences.yaml")


def textes_prompts() -> dict:
    """{nom: {version: texte | messages}} pour compiler les entrées des générations."""
    out: dict = {}
    for p in REF["prompts"]:
        out[p["nom"]] = {i: (v.get("texte") or v.get("messages")) for i, v in enumerate(p["versions"], 1)}
    return out


PROMPTS = textes_prompts()
PRIX = {m["nom"]: (m["prix_entree_par_million"] / 1e6, m["prix_sortie_par_million"] / 1e6) for m in REF["modeles"]}


def horodatage(maintenant: datetime, jour: int, minutes: float, compresser: bool) -> datetime:
    if compresser:  # repli : tout dans les 6 dernières heures (jour 6 = il y a ~6 h)
        return maintenant - timedelta(minutes=jour * 50 + minutes / 12 + 2)
    return maintenant - timedelta(days=jour, minutes=minutes)


def construire_signature(s: dict, maintenant: datetime, compresser: bool, rng: random.Random) -> Trace:
    p = dict(s)
    if p.get("gabarit") == "reprise":
        racine, faits = gabarit_reprise(p, rng)
    else:
        racine, faits = gabarit_support(p, PROMPTS, rng)
    t = Trace(cle=s["cle"], racine=racine, debut=horodatage(maintenant, s["jour"], s["minutes"], compresser),
              user=s.get("user"), session=s.get("session"), environnement=s.get("environnement", "production"),
              release=s.get("release"), tags=s.get("tags", []), commentaire=s.get("commentaire"),
              file_annotation=s.get("file_annotation", False),
              metadata={"scenario": s["cle"], "prompt_superviseur": f"v{s.get('prompt_superviseur', 3)}"})
    t.scores = [dict(sc) for sc in s.get("scores", [])]
    if s.get("score_session"):
        t.scores.append({**s["score_session"], "cible": "session"})
    t.faits = faits
    return t


def construire_conversation(maintenant: datetime, compresser: bool, rng: random.Random) -> list[Trace]:
    c = SIG["conversation_teams"]
    traces = []
    for i, tour in enumerate(c["tours"], 1):
        p = {"question": tour["question"], "specialiste": "kb", "kb": tour["kb"], "categorie": tour["categorie"],
             "priorite": tour["priorite"], "reponse": tour.get("reponse"), "session": c["session"]}
        racine, faits = gabarit_support(p, PROMPTS, rng)
        t = Trace(cle=f"teams-tour-{i}", racine=racine,
                  debut=horodatage(maintenant, c["jour"], tour["minutes"], compresser),
                  user=c["user"], session=c["session"], release=c["release"],
                  tags=["specialiste:kb", "conversation"], metadata={"scenario": "conversation-teams", "tour": i,
                                                                     "prompt_superviseur": "v3"})
        t.scores.append({"nom": "pertinence", "valeur": tour["pertinence"], "commentaire": "Juge LLM"})
        if tour.get("feedback") is not None:
            t.scores.append({"nom": "feedback-utilisateur", "valeur": tour["feedback"],
                             "commentaire": tour.get("feedback_commentaire")})
        t.faits = faits
        traces.append(t)
    traces[-1].scores.append({**c["score_session"], "cible": "session"})
    return traces


# --------------------------------------------------------------------------------------
# Trafic de fond
# --------------------------------------------------------------------------------------

def parametres_cas(cas: dict, choisi: str, rng: random.Random, n: int) -> dict:
    """Paramètres du gabarit pour un cas du jeu d'évaluation, routé vers `choisi`."""
    attendu = cas["specialiste"]
    p = {"specialiste": choisi, "categorie": cas["categorie"], "priorite": cas["priorite"]}
    if choisi != attendu:  # erreur de routage : mauvais outil, réponse à côté
        if choisi == "kb":
            p.update(outil="rechercher_procedures", kb=["KB-018"] if attendu == "diagnostic" else [],
                     reponse=None if attendu == "diagnostic" else None)
        elif choisi == "diagnostic":
            p.update(outil="statut_serveur", arguments_outil={"serveur": "srv-app-01"},
                     resultat_outil={"serveur": "srv-app-01", "statut": "UP"},
                     reponse="Aucun serveur en défaut n'a été détecté. Si le problème persiste, ouvrez un ticket.")
        elif choisi == "acces":
            p.update(outil="etat_compte", arguments_outil={"compte": "inconnu"},
                     resultat_outil={"erreur": "compte non précisé"},
                     reponse="Pouvez-vous m'indiquer l'identifiant du compte concerné ?")
        p["_erreur_routage"] = True
        return p
    outil = cas["outil"]
    if attendu == "kb" or outil == "rechercher_procedures":
        p.update(outil="rechercher_procedures", kb=cas["kb"])
    elif attendu == "diagnostic":
        if outil == "ping_serveur":
            p.update(outil="ping_serveur", arguments_outil={"hote": "srv-erp-02", "paquets": 4},
                     resultat_outil={"hote": "srv-erp-02", "recus": 4, "perdus": 0, "rtt_moyen_ms": round(rng.uniform(2, 5), 1)},
                     reponse="srv-erp-02 répond au ping : 4 paquets reçus sur 4.")
        else:  # poste lent (C18)
            p.update(outil="statut_serveur", arguments_outil={"poste": "PC-0203"},
                     resultat_outil={"poste": "PC-0203", "cpu_moyen": "92 %", "disque_libre": "4 %"},
                     kb=cas["kb"], reponse="Le poste PC-0203 a moins de 5 % d'espace disque libre et un processeur saturé. " + reponse_kb(cas["kb"]))
    elif attendu == "ticketing":
        if outil == "tickets_ouverts":
            nb = 8 + n % 7
            p.update(outil="tickets_ouverts", arguments_outil={"file": "N2-infra"},
                     resultat_outil={"file": "N2-infra", "en_attente": nb},
                     reponse=f"{nb} tickets attendent actuellement dans la file N2-infra.")
        else:
            num = f"DEM-{5100 + n}"
            p.update(outil="creer_ticket", arguments_outil={"type": "demande", "objet": cas["question"]},
                     resultat_outil={"numero": num}, ticket={"file": "N1-materiel", "numero": num},
                     reponse=f"Votre demande est enregistrée sous le numéro {num}. Le matériel vous sera remis sous 5 jours ouvrés.")
    elif attendu == "acces":
        if outil == "etat_compte":
            compte = cas["question"].split("collab-")[1][:6] if "collab-" in cas["question"] else "inconnu"
            p.update(outil="etat_compte", arguments_outil={"compte": f"collab-{compte}"},
                     resultat_outil={"compte": f"collab-{compte}", "statut": "verrouillé", "raison": "3 échecs de connexion",
                                     "deverrouillage_auto": "15 min"},
                     kb=["KB-002"], reponse="Votre compte est verrouillé après 3 échecs : il se débloque seul dans 15 minutes. " + reponse_kb(["KB-002"]))
        else:
            p.update(outil="rechercher_procedures", kb=cas["kb"])
    return p


PREFIXES = ["", "", "", "Bonjour, ", "Urgent : ", "Re : "]


def construire_trafic(maintenant: datetime, compresser: bool) -> list[Trace]:
    rng = random.Random(TRAFIC["graine"])
    traces: list[Trace] = []
    n = 0
    for j in TRAFIC["jours"]:
        minutes = sorted(rng.uniform(15, 590) for _ in range(j["traces"]))
        for m in minutes:
            n += 1
            cas = CAS[rng.choice(TRAFIC["cas"])]
            attendu = cas["specialiste"]
            erreur = rng.random() < j["taux_erreur_routage"]
            choisi = TRAFIC["confusions"][attendu] if erreur else attendu
            p = parametres_cas(cas, choisi, rng, n)
            prefixe = rng.choice(PREFIXES)
            p["question"] = prefixe + (cas["question"][0].lower() + cas["question"][1:] if prefixe else cas["question"])
            p["prompt_superviseur"] = j["prompt_superviseur"]
            racine, faits = gabarit_support(p, PROMPTS, rng)
            user = rng.choice(TRAFIC["utilisateurs"])
            t = Trace(cle=f"trafic-{n:03d}", racine=racine, debut=horodatage(maintenant, j["jour"], m, compresser),
                      user=user, session=f"sess-{user[-6:]}-{n:03d}", release=j["release"],
                      tags=[f"specialiste:{choisi}"] + ([f"categorie:{cas['categorie']}"] if cas["categorie"] else []),
                      metadata={"scenario": "trafic", "cas": cas["id"], "prompt_superviseur": f"v{j['prompt_superviseur']}"})
            pert = round(rng.uniform(0.12, 0.42), 2) if erreur else round(rng.uniform(0.74, 0.97), 2)
            t.scores.append({"nom": "pertinence", "valeur": pert,
                             "commentaire": ("Mauvais spécialiste : la réponse ne traite pas la demande."
                                             if erreur else "Réponse fidèle aux articles.")})
            if rng.random() < TRAFIC["part_feedback"]:
                fb = 1 if pert >= 0.6 else 0
                if rng.random() < 0.1:
                    fb = 1 - fb
                t.scores.append({"nom": "feedback-utilisateur", "valeur": fb})
            if rng.random() < TRAFIC["part_ton"]:
                t.scores.append({"nom": "ton", "valeur": rng.choice(["professionnel", "professionnel", "neutre"])})
            faits.update(cas=cas["id"], erreur_routage=erreur, pertinence=pert)
            t.faits = faits
            traces.append(t)

    f = TRAFIC["formation"]
    for i in range(f["traces"]):
        cas = CAS[rng.choice(TRAFIC["cas"])]
        p = parametres_cas(cas, cas["specialiste"], rng, 900 + i)
        p.update(question=cas["question"], backend=f["backend"], modele_agent=f["modele_agent"],
                 modele_qualification=f["modele_agent"], prompt_superviseur=3)
        racine, faits = gabarit_support(p, PROMPTS, rng)
        user = f["utilisateurs"][i % len(f["utilisateurs"])]
        t = Trace(cle=f"formation-{i + 1:02d}", racine=racine,
                  debut=horodatage(maintenant, f["jour"], 30 + i * 37, compresser),
                  user=user, session=f"sess-{user}-lab", environnement="formation", release=f["release"],
                  tags=list(f["tags"]) + [f"specialiste:{cas['specialiste']}"],
                  metadata={"scenario": "formation", "cas": cas["id"], "prompt_superviseur": "v3"})
        t.faits = faits
        traces.append(t)
    return traces


# --------------------------------------------------------------------------------------
# Assemblage + faits attendus
# --------------------------------------------------------------------------------------

def toutes_les_traces(maintenant: datetime | None = None, compresser: bool = False) -> list[Trace]:
    maintenant = maintenant or datetime.now(timezone.utc)
    rng = random.Random(7)
    traces = [construire_signature(s, maintenant, compresser, rng) for s in SIG["signatures"]]
    traces += construire_conversation(maintenant, compresser, rng)
    traces += construire_trafic(maintenant, compresser)
    for t in traces:
        placer(t)
    traces.sort(key=lambda t: t.debut)
    return traces


def cout(o: Obs) -> float:
    if o.type != "generation" or o.modele not in PRIX:
        return 0.0
    pin, pout = PRIX[o.modele]
    return o.usage["input"] * pin + o.usage["output"] * pout


def faits_globaux(traces: list[Trace]) -> dict:
    """Réponses attendues des missions (indépendantes de l'heure du seed)."""
    prod = [t for t in traces if t.environnement == "production"]
    par_cle = {t.cle: t for t in traces}

    def duree(t: Trace) -> float:
        return (t.racine.fin - t.racine.debut).total_seconds()

    couts = {t.cle: sum(cout(o) for o in parcourir(t.racine)) for t in traces}
    plus_chere = max(couts, key=couts.get)
    modele_plus_cher = max((o for o in parcourir(par_cle[plus_chere].racine) if o.type == "generation"),
                           key=cout).modele
    plus_lente = max(traces, key=duree)
    erreurs_racine = [t.cle for t in traces if t.racine.niveau == "ERROR"]
    avec_obs_erreur = [t.cle for t in traces if any(o.niveau == "ERROR" for o in parcourir(t.racine))]
    avec_warning = [t.cle for t in traces if any(o.niveau == "WARNING" for o in parcourir(t.racine))]
    sessions = Counter(t.session for t in traces if t.session)
    users = Counter(t.user for t in traces if t.user)
    pert_par_release = defaultdict(list)
    for t in prod:
        for s in t.scores:
            if s["nom"] == "pertinence":
                pert_par_release[t.release].append(s["valeur"])
    obs_types = Counter(o.type for t in traces for o in parcourir(t.racine))
    cout_modele = defaultdict(float)
    for t in traces:
        for o in parcourir(t.racine):
            if o.type == "generation":
                cout_modele[o.modele] += cout(o)
    nb_scores = Counter(s["nom"] for t in traces for s in t.scores)

    return {
        "nb_traces": len(traces),
        "nb_traces_par_environnement": dict(Counter(t.environnement for t in traces)),
        "nb_traces_par_release": dict(Counter(t.release for t in traces)),
        "nb_observations": sum(1 for t in traces for _ in parcourir(t.racine)),
        "types_observations": dict(obs_types),
        "traces_racine_erreur": erreurs_racine,
        "traces_avec_observation_erreur": avec_obs_erreur,
        "traces_avec_warning": avec_warning,
        "trace_plus_chere": {"cle": plus_chere, "trace_id": par_cle[plus_chere].trace_id,
                             "cout_usd": round(couts[plus_chere], 6), "modele": modele_plus_cher},
        "cout_total_usd": round(sum(couts.values()), 6),
        "cout_par_modele_usd": {k: round(v, 6) for k, v in sorted(cout_modele.items(), key=lambda x: -x[1])},
        "trace_plus_lente": {"cle": plus_lente.cle, "trace_id": plus_lente.trace_id,
                             "duree_s": round(duree(plus_lente), 1), "backend": plus_lente.faits.get("backend")},
        "session_plus_longue": {"id": sessions.most_common(1)[0][0], "nb_traces": sessions.most_common(1)[0][1]},
        "nb_sessions": len(sessions),
        "nb_utilisateurs": len(users),
        "utilisateur_plus_actif": {"id": users.most_common(1)[0][0], "nb_traces": users.most_common(1)[0][1]},
        "pertinence_moyenne_par_release": {k: round(mean(v), 3) for k, v in sorted(pert_par_release.items())},
        "nb_scores_par_nom": dict(nb_scores),
        "signatures": {t.cle: {"trace_id": t.trace_id, "nom": t.nom, "debut_relatif": None, **t.faits}
                       for t in traces if not t.cle.startswith(("trafic-", "formation-"))},
    }


if __name__ == "__main__":
    import json
    tr = toutes_les_traces()
    print(json.dumps(faits_globaux(tr), ensure_ascii=False, indent=1, default=str)[:6000])

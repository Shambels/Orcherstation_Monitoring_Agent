"""
build_guide.py : génère guide/index.html (participants) et formateur/solutions.html
à partir de guide/source/{ecrans,missions}.yaml et des données du seed.

    uv run --project seed tools/build_guide.py

Garde-fou : chaque mission qui déclare `faits:` est confrontée aux données réelles du seed.
Si on modifie un scénario et qu'une réponse ne correspond plus, la génération échoue.
Les réponses sont stockées sous forme d'empreintes SHA-256 (pas de corrigé en clair
dans le guide distribué). C'est une protection pédagogique, pas cryptographique.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "seed"))

from moteur import SDK_VERSION, trace_id_de  # noqa: E402
from scenarios import faits_globaux, toutes_les_traces  # noqa: E402

PROJET = "explorer"
VERSION_LANGFUSE = "4.46.0"


def normaliser(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]", "", s)


def empreinte(s: str) -> str:
    return hashlib.sha256(("lfx|" + normaliser(s)).encode()).hexdigest()


def route(lien: str | None) -> str | None:
    if not lien:
        return None
    if ":" not in lien:
        return lien
    genre, val = lien.split(":", 1)
    return {"trace": f"traces/{trace_id_de(val)}", "session": f"sessions/{val}",
            "user": f"users/{val}", "prompt": f"prompts/{val}"}[genre]


def valeur(faits: dict, chemin: str):
    v = faits
    for k in chemin.split("."):
        v = v[k]
    return v


def main() -> None:
    src = RACINE / "guide" / "source"
    ecr = yaml.safe_load((src / "ecrans.yaml").read_text(encoding="utf-8"))
    mis = yaml.safe_load((src / "missions.yaml").read_text(encoding="utf-8"))
    faits = faits_globaux(toutes_les_traces())

    erreurs = []
    ids = set()
    for m in mis["missions"]:
        if m["id"] in ids:
            erreurs.append(f"{m['id']} : identifiant en double")
        ids.add(m["id"])
        if m["ecran"] not in ecr["ecrans"]:
            erreurs.append(f"{m['id']} : écran inconnu {m['ecran']}")
        if m["type"] == "reponse" and not m.get("reponses"):
            erreurs.append(f"{m['id']} : aucune réponse")
        if m.get("faits"):
            attendu = normaliser(valeur(faits, m["faits"]))
            if normaliser(m["reponses"][0]) not in attendu:
                erreurs.append(f"{m['id']} : « {m['reponses'][0]} » ne correspond plus à {m['faits']} = {valeur(faits, m['faits'])!r}")
    if erreurs:
        sys.exit("❌ Missions incohérentes avec le seed :\n  " + "\n  ".join(erreurs))

    ecrans = {}
    for k, e in ecr["ecrans"].items():
        e = dict(e)
        if e.get("exemple"):
            e["route_exemple"] = e["route"].replace("{trace_id}", trace_id_de(e["exemple"]))
        ecrans[k] = e

    missions_publiques = []
    for m in mis["missions"]:
        pub = {k: m.get(k) for k in ("id", "niveau", "ecran", "titre", "type", "consigne", "indice", "pourquoi", "enjeux")}
        pub["route"] = route(m.get("lien"))
        pub["hashes"] = sorted({empreinte(r) for r in m.get("reponses", [])})
        missions_publiques.append(pub)

    data = {"projet": PROJET, "version": VERSION_LANGFUSE, "groupes": ecr["groupes"], "ecrans": ecrans,
            "niveaux": mis["niveaux"], "missions": missions_publiques}
    gabarit = (src / "template.html").read_text(encoding="utf-8")
    page = (gabarit.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__LANGFUSE_VERSION__", VERSION_LANGFUSE).replace("__SDK_VERSION__", SDK_VERSION)
            .replace("__DATE__", date.today().strftime("%d/%m/%Y")))
    (RACINE / "guide" / "index.html").write_text(page, encoding="utf-8")

    # ---------------- Corrigé formateur ----------------
    e = html.escape
    lignes = []
    for niv in mis["niveaux"]:
        lignes.append(f"<h2>{e(niv['nom'])}</h2>")
        for m in [x for x in mis["missions"] if x["niveau"] == niv["id"]]:
            r = route(m.get("lien"))
            lien = f'<a href="http://localhost:3100/project/{PROJET}/{r}">{e(r)}</a>' if r else ""
            rep = " | ".join(e(x) for x in m.get("reponses", [])) or "<i>action (auto-déclarée)</i>"
            lignes.append(f"""<article><h3>{m['id']} · {e(m['titre'])} <small>({e(ecrans[m['ecran']]['libelle'])})</small></h3>
<p>{e(m['consigne'])}</p><p class="rep"><b>Réponse(s) acceptée(s) :</b> {rep}</p>
<p><b>Chemin :</b> {e(m['indice'])} {lien}</p><p class="pq"><b>Message à faire passer :</b> {e(m['pourquoi'])}</p></article>""")
    resume = f"""<table><tr><th>Indicateur</th><th>Valeur attendue</th></tr>
<tr><td>Traces (production + formation)</td><td>{faits['nb_traces']} {faits['nb_traces_par_environnement']}</td></tr>
<tr><td>Observations</td><td>{faits['nb_observations']}</td></tr>
<tr><td>Traces par release</td><td>{faits['nb_traces_par_release']}</td></tr>
<tr><td>Pertinence moyenne par release</td><td>{faits['pertinence_moyenne_par_release']}</td></tr>
<tr><td>Trace la plus chère</td><td>{faits['trace_plus_chere']}</td></tr>
<tr><td>Trace la plus lente</td><td>{faits['trace_plus_lente']}</td></tr>
<tr><td>Coût par modèle (USD)</td><td>{faits['cout_par_modele_usd']}</td></tr>
<tr><td>Scores</td><td>{faits['nb_scores_par_nom']}</td></tr></table>"""
    sol = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Corrigé formateur</title><style>
body{{font:15px/1.5 Arial,sans-serif;max-width:900px;margin:30px auto;padding:0 16px;color:#1D1838;background:#fff}}
h1{{color:#3B2A80}}h2{{color:#3B2A80;border-bottom:2px solid #23AC67;padding-bottom:4px;margin-top:36px}}
article{{border:1px solid #D9D5EA;border-radius:8px;padding:10px 16px;margin:12px 0}}h3{{margin:4px 0}}small{{color:#4A4566;font-weight:400}}
.rep{{background:#E2F6EA;padding:6px 10px;border-radius:6px}}.pq{{color:#4A4566}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #D9D5EA;padding:6px 10px;text-align:left;vertical-align:top;font-size:13.5px}}th{{background:#ECE9F7}}
@media print{{article{{break-inside:avoid}}}}</style></head><body>
<h1>Langfuse Explorer : corrigé formateur</h1>
<p><b>Ne pas distribuer.</b> Langfuse {VERSION_LANGFUSE}, données du seed déterministe. Généré le {date.today().strftime('%d/%m/%Y')}.</p>
<h2>Repères chiffrés</h2>{resume}{''.join(lignes)}</body></html>"""
    (RACINE / "formateur").mkdir(exist_ok=True)
    (RACINE / "formateur" / "solutions.html").write_text(sol, encoding="utf-8")
    print(f"✅ guide/index.html : {len(ecrans)} écrans, {len(missions_publiques)} missions")
    print("✅ formateur/solutions.html")


if __name__ == "__main__":
    main()

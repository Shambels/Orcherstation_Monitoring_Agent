"""
verify.py : contrôle que l'instance Langfuse du kit contient bien ce que les missions attendent.

    uv run --project seed tools/verify.py

À lancer après le seed (attendre ~1 min) et après TOUTE mise à jour de version de Langfuse :
si un contrôle échoue, une mission du guide n'a plus de réponse valable. C'est le test de
non-régression du kit.

Lectures via les API v4 uniquement (Observations v2, Scores v3) : en mode events_only,
les anciennes routes /traces, /sessions, /observations répondent 404.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "seed"))

from seed_langfuse import lire_env  # noqa: E402

OK, KO = "✅", "❌"
resultats: list[tuple[bool, str]] = []


def controle(cond: bool, texte: str) -> None:
    resultats.append((cond, texte))
    print(f"{OK if cond else KO} {texte}")


def main() -> None:
    env = lire_env(RACINE / ".env")
    from langfuse import Langfuse
    lf = Langfuse(public_key=env["LANGFUSE_PUBLIC_KEY"], secret_key=env["LANGFUSE_SECRET_KEY"],
                  base_url=env["LANGFUSE_BASE_URL"], tracing_enabled=False)
    api = lf.api
    faits_path = RACINE / "formateur" / "faits.json"
    if not faits_path.exists():
        sys.exit("❌ formateur/faits.json absent : lancer le seed d'abord.")
    faits = json.loads(faits_path.read_text(encoding="utf-8"))
    depuis = datetime.now(timezone.utc) - timedelta(days=8)

    def observations(**filtres) -> list:
        out, curseur = [], None
        while True:
            res = api.observations.get_many(limit=1000, cursor=curseur, from_start_time=depuis, **filtres)
            out.extend(res.data)
            curseur = res.meta.cursor if res.meta else None
            if not curseur or not res.data:
                return out

    # 1. Traces (= observations racines)
    racines = observations(is_root_observation=True, fields="core,basic")
    hors_exp = [o for o in racines if o.environment in ("production", "formation")]
    controle(len(hors_exp) == faits["nb_traces"],
             f"traces production + formation : {len(hors_exp)} (attendu {faits['nb_traces']})")
    par_env = {}
    for o in hors_exp:
        par_env[o.environment] = par_env.get(o.environment, 0) + 1
    controle(par_env == faits["nb_traces_par_environnement"], f"répartition par environnement : {par_env}")

    # 2. Traces signatures (identifiants déterministes)
    ids = {o.trace_id for o in racines}
    manquantes = [k for k, v in faits["signatures"].items() if v["trace_id"] not in ids]
    controle(not manquantes, f"{len(faits['signatures'])} traces signatures présentes" +
             (f" (manquantes : {manquantes})" if manquantes else ""))

    # 3. Niveaux
    erreurs = observations(level="ERROR", fields="core,basic")
    traces_err = {o.trace_id for o in erreurs}
    attendu_err = {faits["signatures"][k]["trace_id"] for k in faits["traces_avec_observation_erreur"]}
    controle(traces_err == attendu_err, f"traces contenant une observation ERROR : {len(traces_err)} (attendu {len(attendu_err)})")

    # 4. Coûts calculés par Langfuse à partir des prix déclarés
    chere = faits["trace_plus_chere"]
    gens = observations(trace_id=chere["trace_id"], type="GENERATION", fields="core,basic,model,usage")
    cout = sum((g.total_cost or 0) for g in gens)
    controle(cout > 0, f"coût calculé sur la trace la plus chère : {cout:.6f} $ (attendu ≈ {chere['cout_usd']} $)")
    if cout == 0:
        print("   → les prix des modèles n'ont pas été appliqués : vérifier Settings > Models, puis relancer avec --reset.")

    # 5. Prompts liés aux générations
    lies = [g for g in observations(trace_id=faits["signatures"]["boucle-routage"]["trace_id"],
                                    type="GENERATION", fields="core,basic,prompt") if g.prompt_name]
    controle(bool(lies) and all(g.prompt_version == 2 for g in lies if g.prompt_name == "superviseur"),
             "générations de la boucle liées au prompt superviseur v2")
    for nom, nb in {"superviseur": 3, "redacteur": 2, "qualification": 1, "juge-pertinence": 1}.items():
        p = lf.get_prompt(nom, label="production", cache_ttl_seconds=0)
        controle(p.version == nb, f"prompt {nom} : version production = v{p.version} (attendu v{nb})")

    # 6. Scores (API v3)
    scores, curseur = [], None
    while True:
        res = api.scores_v3.get_many_v3(limit=100, cursor=curseur, from_timestamp=depuis)
        scores.extend(res.data)
        curseur = getattr(res.meta, "cursor", None) if res.meta else None
        if not curseur or not res.data:
            break
    par_nom = {}
    for s in scores:
        par_nom[s.name] = par_nom.get(s.name, 0) + 1
    for nom, nb in faits["nb_scores_par_nom"].items():
        controle(par_nom.get(nom, 0) >= nb, f"scores « {nom} » : {par_nom.get(nom, 0)} (attendu ≥ {nb})")
    controle(par_nom.get("routage_correct", 0) >= 60, f"scores d'expérience routage_correct : {par_nom.get('routage_correct', 0)}")

    # 7. Dataset
    try:
        ds = lf.get_dataset("helpdesk-eval")
        controle(len(ds.items) == 20, f"dataset helpdesk-eval : {len(ds.items)} items")
    except Exception as e:  # noqa: BLE001
        controle(False, f"dataset helpdesk-eval introuvable ({e})")

    lf.shutdown()
    ko = [t for ok, t in resultats if not ok]
    print(f"\n{len(resultats) - len(ko)}/{len(resultats)} contrôles réussis.")
    if ko:
        print("Si les données viennent d'être injectées, attendre 1 minute et relancer.")
        sys.exit(1)


if __name__ == "__main__":
    main()

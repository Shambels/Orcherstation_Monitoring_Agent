"""
seed_langfuse.py : injecte dans le Langfuse du kit un jeu de données de démonstration
DÉTERMINISTE (mêmes traces, mêmes identifiants, mêmes réponses aux missions sur tous les postes).

    uv run --project seed seed/seed_langfuse.py               # injection complète
    uv run --project seed seed/seed_langfuse.py --dry-run     # n'envoie rien : écrit les spans en JSON
    uv run --project seed seed/seed_langfuse.py --compresser  # repli : tout sur les 6 dernières heures
    uv run --project seed seed/seed_langfuse.py --force       # réinjecte même si déjà fait (doublons !)

Ordre d'injection (l'ordre compte) :
  1. prix des modèles   -> AVANT les traces, sinon Langfuse ne calcule pas les coûts à l'ingestion
  2. configs de scores  -> les scores s'y rattachent (bornes, catégories)
  3. prompts versionnés -> les générations pointent vers « superviseur v2 », etc.
  4. traces (OpenTelemetry, horodatées sur 7 jours)
  5. scores, commentaires, file d'annotation
  6. dataset + 3 expériences (SDK run_experiment)

Rien ne sort du poste : seul http://localhost:3100 est contacté.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("OTEL_SDK_DISABLED", "false")


def lire_env(chemin: Path) -> dict:
    if not chemin.exists():
        sys.exit(f"❌ {chemin} introuvable : lancer d'abord start.ps1 / start.sh (ou python generate_env.py).")
    env = {}
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if "=" in ligne and not ligne.lstrip().startswith("#"):
            k, v = ligne.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def etape(n: int, titre: str) -> None:
    print(f"\n[{n}/6] {titre}")


# --------------------------------------------------------------------------------------
# 1. Modèles, 2. configs de scores, 3. prompts (idempotents : rien n'est créé deux fois)
# --------------------------------------------------------------------------------------

def creer_modeles(api, ref: dict) -> None:
    existants = set()
    page = 1
    while True:
        res = api.models.list(page=page, limit=100)
        existants |= {m.model_name for m in res.data if not m.is_langfuse_managed}
        if page >= res.meta.total_pages:
            break
        page += 1
    for m in ref["modeles"]:
        if m["nom"] in existants:
            print(f"   = modèle {m['nom']} déjà déclaré")
            continue
        api.models.create(model_name=m["nom"], match_pattern=m["motif"], unit="TOKENS",
                          input_price=m["prix_entree_par_million"] / 1e6,
                          output_price=m["prix_sortie_par_million"] / 1e6,
                          start_date=datetime(2026, 1, 1, tzinfo=timezone.utc))
        print(f"   + modèle {m['nom']} : {m['prix_entree_par_million']} $ / {m['prix_sortie_par_million']} $ par million de tokens")


def creer_configs_scores(api, ref: dict) -> dict[str, tuple[str, str]]:
    """Retourne {nom: (config_id, data_type)}."""
    existantes = {}
    page = 1
    while True:
        res = api.score_configs.get(page=page, limit=100)
        existantes |= {c.name: c for c in res.data}
        if page >= res.meta.total_pages:
            break
        page += 1
    out = {}
    for c in ref["configs_scores"]:
        if c["nom"] in existantes:
            cfg = existantes[c["nom"]]
            print(f"   = config {c['nom']} déjà présente")
        else:
            kwargs = {"name": c["nom"], "data_type": c["type"], "description": c["description"]}
            if c["type"] == "NUMERIC":
                kwargs.update(min_value=c["min"], max_value=c["max"])
            if c["type"] == "CATEGORICAL":
                kwargs["categories"] = [{"label": lab, "value": i} for i, lab in enumerate(c["categories"])]
            cfg = api.score_configs.create(**kwargs)
            print(f"   + config {c['nom']} ({c['type']})")
        out[c["nom"]] = (cfg.id, c["type"])
    return out


def creer_prompts(lf, api, ref: dict) -> None:
    for p in ref["prompts"]:
        try:
            meta = api.prompts.list(name=p["nom"], limit=50)
            nb = max((len(x.versions) for x in meta.data if x.name == p["nom"]), default=0)
        except Exception:
            nb = 0
        if nb >= len(p["versions"]):
            print(f"   = prompt {p['nom']} : {nb} version(s) déjà présentes")
            continue
        for i, v in enumerate(p["versions"][nb:], nb + 1):
            lf.create_prompt(name=p["nom"], type=p["type"],
                             prompt=v["texte"] if p["type"] == "text" else v["messages"],
                             labels=v["labels"], config=v.get("config"), commit_message=v["message"],
                             tags=["helpdesk-ops"])
            print(f"   + prompt {p['nom']} v{i}" + (f"  [{', '.join(v['labels'])}]" if v["labels"] else ""))


# --------------------------------------------------------------------------------------
# 4. Traces
# --------------------------------------------------------------------------------------

def deja_injecte(api, trace_id: str) -> bool:
    try:
        res = api.observations.get_many(trace_id=trace_id, limit=1, fields="core")
        return len(res.data) > 0
    except Exception:
        return False


def exporter_traces(traces, base_url: str, pk: str, sk: str) -> int:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import SpanExportResult

    from moteur import vers_spans

    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{base_url}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}", "x-langfuse-sdk-name": "python",
                 "x-langfuse-sdk-version": "4.15.6", "x-langfuse-public-key": pk,
                 # Traitement temps réel sur le modèle de données v4 (sinon ~15 min de délai)
                 "x-langfuse-ingestion-version": "4"},
        timeout=30,
    )
    lot, total = [], 0
    for t in traces:
        lot.extend(vers_spans(t, pk))
        if len(lot) >= 300:
            if exporter.export(lot) != SpanExportResult.SUCCESS:
                sys.exit("❌ Export OTLP refusé : vérifier les clés et l'URL dans .env (docker compose logs langfuse-web).")
            total += len(lot)
            lot = []
    if lot:
        if exporter.export(lot) != SpanExportResult.SUCCESS:
            sys.exit("❌ Export OTLP refusé.")
        total += len(lot)
    exporter.shutdown()
    return total


# --------------------------------------------------------------------------------------
# 5. Scores, commentaires, file d'annotation
# --------------------------------------------------------------------------------------

def creer_scores(lf, traces, configs: dict) -> int:
    n = 0
    for t in traces:
        for i, s in enumerate(t.scores):
            cfg_id, dtype = configs[s["nom"]]
            valeur = s["valeur"] if dtype == "CATEGORICAL" else float(s["valeur"])
            commun = dict(name=s["nom"], value=valeur, data_type=dtype, config_id=cfg_id,
                          comment=s.get("commentaire"), environment=t.environnement,
                          timestamp=t.racine.fin + timedelta(minutes=2 + i),
                          score_id=f"lfx-{t.cle}-{s['nom']}"[:100])
            if s.get("cible") == "session":
                lf.create_score(session_id=t.session, **commun)
            else:
                lf.create_score(trace_id=t.trace_id, **commun)
            n += 1
    return n


def creer_commentaires(api, traces) -> int:
    try:
        projet = api.projects.get().data[0].id
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️ commentaires ignorés ({e})")
        return 0
    n = 0
    for t in traces:
        if t.commentaire:
            try:
                api.comments.create(project_id=projet, object_type="TRACE", object_id=t.trace_id, content=t.commentaire)
                n += 1
            except Exception as e:  # noqa: BLE001
                print(f"   ⚠️ commentaire sur {t.cle} refusé : {e}")
    return n


def creer_file_annotation(api, ref: dict, traces, configs: dict) -> int:
    fa = ref["file_annotation"]
    try:
        existantes = {q.name: q for q in api.annotation_queues.list_queues(limit=100).data}
        file = existantes.get(fa["nom"]) or api.annotation_queues.create_queue(
            name=fa["nom"], description=fa["description"], score_config_ids=[configs[s][0] for s in fa["scores"]])
        n = 0
        for t in traces:
            if t.file_annotation:
                api.annotation_queues.create_queue_item(queue_id=file.id, object_id=t.trace_id, object_type="TRACE")
                n += 1
        return n
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️ file d'annotation non créée ({e})")
        return 0


# --------------------------------------------------------------------------------------
# 6. Dataset + expériences
# --------------------------------------------------------------------------------------

def creer_dataset_et_experiences(lf, exp: dict) -> None:
    from langfuse import Evaluation

    from moteur import CAS, REDTEAM  # noqa: F401

    ds = exp["dataset"]
    try:
        lf.get_dataset(ds["nom"])
        print(f"   = dataset {ds['nom']} déjà présent")
    except Exception:
        lf.create_dataset(name=ds["nom"], description=ds["description"],
                          metadata={"source": "data/helpdesk_eval.json", "version": "2026-10-21"})
        for c in CAS.values():
            lf.create_dataset_item(
                dataset_name=ds["nom"], id=f"lfx-helpdesk-{c['id']}", input={"question": c["question"]},
                expected_output={"specialiste": c["specialiste"], "refus": c["refus"], "categorie": c["categorie"]},
                metadata={"cas": c["id"], "outil_attendu": c["outil"], "kb": c["kb"]})
        print(f"   + dataset {ds['nom']} : {len(CAS)} items")

    dataset = lf.get_dataset(ds["nom"])

    def routage_correct(*, output, expected_output, **_):
        ok = output["specialiste"] == expected_output["specialiste"] and output["refus"] == expected_output["refus"]
        return Evaluation(name="routage_correct", value=1.0 if ok else 0.0,
                          comment=None if ok else f"attendu {expected_output['specialiste'] or expected_output['refus']}, obtenu {output['specialiste'] or output['refus']}")

    def categorie_correcte(*, output, expected_output, **_):
        if expected_output["categorie"] is None:
            return []
        ok = output["categorie"] == expected_output["categorie"]
        return Evaluation(name="categorie_correcte", value=1.0 if ok else 0.0)

    def taux_routage(*, item_results, **_):
        vals = [e.value for r in item_results for e in r.evaluations if e.name == "routage_correct"]
        return Evaluation(name="taux_routage", value=round(sum(vals) / len(vals), 3) if vals else 0.0)

    for run in exp["runs"]:
        rng = random.Random(run["nom"])

        def tache(*, item, run=run, rng=rng, **_):
            cas = item.metadata["cas"]
            attendu = item.expected_output
            spe = run["erreurs_routage"].get(cas, attendu["specialiste"])
            refus = attendu["refus"]
            if cas in run["refus_manques"]:
                spe, refus = "kb", None
            sortie = {"specialiste": spe if refus is None else None, "refus": refus,
                      "categorie": run["erreurs_categorie"].get(cas, attendu["categorie"])}
            ms = rng.uniform(*run["latence_ms"])
            with lf.start_as_current_observation(
                    name="routage", as_type="generation", model=run["modele"],
                    input=[{"role": "user", "content": item.input["question"]}],
                    usage_details={"input": 260 + len(item.input["question"]) // 3, "output": 12},
                    metadata={"prompt_superviseur": f"v{run['prompt_superviseur']}"}) as g:
                time.sleep(ms / 1000)
                g.update(output=json.dumps(sortie, ensure_ascii=False))
            return sortie

        res = lf.run_experiment(name=run["nom"], run_name=run["nom"], description=run["description"],
                                data=dataset.items, task=tache,
                                evaluators=[routage_correct, categorie_correcte], run_evaluators=[taux_routage],
                                max_concurrency=10,
                                metadata={"modele": run["modele"], "prompt_superviseur": f"v{run['prompt_superviseur']}"})
        taux = next((e.value for e in res.run_evaluations if e.name == "taux_routage"), None)
        print(f"   + expérience {run['nom']} : taux de routage correct = {taux}")


# --------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Seed déterministe du kit Langfuse Explorer")
    ap.add_argument("--env", default=str(RACINE / ".env"))
    ap.add_argument("--dry-run", action="store_true", help="n'envoie rien, écrit seed/_dry_run.json")
    ap.add_argument("--compresser", action="store_true", help="place toutes les traces sur les 6 dernières heures")
    ap.add_argument("--force", action="store_true", help="réinjecte les traces même si elles existent déjà")
    args = ap.parse_args()

    from scenarios import EXP, REF, faits_globaux, toutes_les_traces

    maintenant = datetime.now(timezone.utc)
    traces = toutes_les_traces(maintenant, compresser=args.compresser)
    faits = faits_globaux(traces)
    (RACINE / "formateur").mkdir(exist_ok=True)
    (RACINE / "formateur" / "faits.json").write_text(json.dumps(faits, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.dry_run:
        from moteur import vers_spans
        spans = [json.loads(s.to_json()) for t in traces for s in vers_spans(t, "pk-lf-dry-run")]
        sortie = Path(__file__).parent / "_dry_run.json"
        sortie.write_text(json.dumps(spans, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✅ dry-run : {len(traces)} traces, {len(spans)} spans -> {sortie}")
        return

    env = lire_env(Path(args.env))
    base_url, pk, sk = env["LANGFUSE_BASE_URL"], env["LANGFUSE_PUBLIC_KEY"], env["LANGFUSE_SECRET_KEY"]
    os.environ.update(LANGFUSE_BASE_URL=base_url, LANGFUSE_PUBLIC_KEY=pk, LANGFUSE_SECRET_KEY=sk,
                      LANGFUSE_TRACING_ENVIRONMENT="production")

    from langfuse import Langfuse
    lf = Langfuse(public_key=pk, secret_key=sk, base_url=base_url, environment="production",
                  additional_headers={"x-langfuse-ingestion-version": "4"})
    api = lf.api
    if not lf.auth_check():
        sys.exit("❌ Clés refusées par Langfuse. Après un `docker compose down -v`, relancer start avec -Reset / --reset.")

    etape(1, "Prix des modèles (FinOps)")
    creer_modeles(api, REF)
    etape(2, "Configurations de scores")
    configs = creer_configs_scores(api, REF)
    etape(3, "Prompts versionnés")
    creer_prompts(lf, api, REF)

    etape(4, "Traces (7 jours d'activité du helpdesk)")
    if deja_injecte(api, traces[0].trace_id) and not args.force:
        print("   = traces déjà présentes : rien à faire (repartir de zéro : start -Reset / --reset)")
        lf.shutdown()
        return
    nb_spans = exporter_traces(traces, base_url, pk, sk)
    print(f"   + {len(traces)} traces, {nb_spans} observations envoyées")
    print("   … attente de l'ingestion par le worker", end="", flush=True)
    for _ in range(45):  # scores et commentaires s'attachent à des traces existantes
        if deja_injecte(api, traces[0].trace_id) and deja_injecte(api, traces[-1].trace_id):
            print(" ok")
            break
        print(".", end="", flush=True)
        time.sleep(2)
    else:
        print("\n   ⚠️ traces pas encore visibles après 90 s : on continue (elles apparaîtront plus tard).")

    etape(5, "Scores, commentaires, file d'annotation")
    print(f"   + {creer_scores(lf, traces, configs)} scores")
    lf.flush()
    print(f"   + {creer_commentaires(api, traces)} commentaires")
    print(f"   + {creer_file_annotation(api, REF, traces, configs)} éléments dans la file « {REF['file_annotation']['nom']} »")

    etape(6, "Dataset et expériences")
    creer_dataset_et_experiences(lf, EXP)
    lf.flush()
    lf.shutdown()

    print("\n⏳ Langfuse traite les données en arrière-plan : comptez 30 à 60 s avant de tout voir dans l'UI.")
    print("   Vérification : uv run --project seed tools/verify.py")


if __name__ == "__main__":
    main()

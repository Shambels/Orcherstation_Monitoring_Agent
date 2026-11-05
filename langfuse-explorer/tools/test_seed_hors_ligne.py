"""
test_seed_hors_ligne.py : teste le seed SANS Langfuse (maintenance du kit).

  - un faux collecteur OTLP local reçoit les spans et les décode (protobuf) ;
  - l'API Langfuse est remplacée par des doublures « autospec » : tout appel avec un
    argument inexistant ou mal nommé dans le SDK 4.15.6 fait échouer le test ;
  - run_experiment tourne pour de vrai (SDK), avec un exporteur en mémoire.

    uv run --project seed tools/test_seed_hors_ligne.py
"""

from __future__ import annotations

import gzip
import http.server
import json
import sys
import threading
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SEED = Path(__file__).resolve().parent.parent / "seed"
sys.path.insert(0, str(SEED))

RECU: list = []


class Collecteur(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        corps = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if not self.path.endswith("/otel/v1/traces"):  # scores des évaluateurs, etc. : acquittés
            self.send_response(207)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"successes": [], "errors": []}')
            return
        if self.headers.get("Content-Encoding") == "gzip":
            corps = gzip.decompress(corps)
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
        req = ExportTraceServiceRequest()
        req.ParseFromString(corps)
        RECU.append((dict(self.headers), req))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.end_headers()

    def log_message(self, *a):
        pass


def main() -> None:
    import seed_langfuse as S
    from scenarios import EXP, REF, faits_globaux, toutes_les_traces

    srv = http.server.HTTPServer(("127.0.0.1", 0), Collecteur)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    traces = toutes_les_traces()
    faits = faits_globaux(traces)

    # 1. Export OTLP réel vers le faux collecteur
    n = S.exporter_traces(traces, base, "pk-lf-test", "sk-lf-test")
    spans = [s for _, req in RECU for rs in req.resource_spans for ss in rs.scope_spans for s in ss.spans]
    entetes = {k.lower(): v for k, v in RECU[0][0].items()}
    assert n == len(spans) == faits["nb_observations"], (n, len(spans))
    assert entetes.get("x-langfuse-ingestion-version") == "4", entetes
    assert entetes["authorization"].startswith("Basic ")
    scope = RECU[0][1].resource_spans[0].scope_spans[0].scope
    assert scope.name == "langfuse-sdk" and scope.version == "4.15.6"
    ids = Counter((s.trace_id.hex(), s.span_id.hex()) for s in spans)
    assert max(ids.values()) == 1, "span_id en double"
    racines = [s for s in spans if not s.parent_span_id]
    assert len(racines) == faits["nb_traces"]
    for s in spans:
        assert s.end_time_unix_nano >= s.start_time_unix_nano
    import time
    age_max_h = (time.time_ns() - min(s.start_time_unix_nano for s in spans)) / 3.6e12
    futur = max(s.end_time_unix_nano for s in spans) > time.time_ns()
    assert age_max_h < 168 and not futur, (age_max_h, futur)
    print(f"✅ OTLP : {len(spans)} spans, {len(racines)} traces, plus ancienne il y a {age_max_h:.0f} h, aucune dans le futur")

    # 2. Appels API : doublures autospec (vérifient les noms d'arguments du SDK)
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exp_mem = InMemorySpanExporter()
    lf = Langfuse(public_key="pk-lf-test", secret_key="sk-lf-test", base_url=base, span_exporter=exp_mem,
                  environment="production")
    vide = SimpleNamespace(data=[], meta=SimpleNamespace(total_pages=1))
    api = mock.create_autospec(lf.api, instance=True)
    api.models.list.return_value = vide
    api.score_configs.get.return_value = vide
    api.score_configs.create.side_effect = lambda **k: SimpleNamespace(id=f"cfg-{k['name']}")
    api.prompts.list.return_value = vide
    api.projects.get.return_value = SimpleNamespace(data=[SimpleNamespace(id="explorer")])
    api.annotation_queues.list_queues.return_value = vide
    api.annotation_queues.create_queue.return_value = SimpleNamespace(id="q1")

    with mock.patch.object(Langfuse, "create_prompt", autospec=True) as cp, \
         mock.patch.object(Langfuse, "create_score", autospec=True) as cs:
        S.creer_modeles(api, REF)
        configs = S.creer_configs_scores(api, REF)
        S.creer_prompts(lf, api, REF)
        nb_scores = S.creer_scores(lf, traces, configs)
        nb_com = S.creer_commentaires(api, traces)
        nb_file = S.creer_file_annotation(api, REF, traces, configs)
    assert api.models.create.call_count == len(REF["modeles"])
    assert cp.call_count == sum(len(p["versions"]) for p in REF["prompts"])
    assert cs.call_count == nb_scores == sum(faits["nb_scores_par_nom"].values())
    sess = [c for c in cs.call_args_list if c.kwargs.get("session_id")]
    print(f"✅ API : {api.models.create.call_count} modèles, {len(configs)} configs, {cp.call_count} versions de prompts, "
          f"{nb_scores} scores (dont {len(sess)} de session), {nb_com} commentaires, {nb_file} éléments en file")

    # 3. Dataset + expériences : run_experiment réel, API dataset doublée
    from langfuse.api import DatasetItem
    from moteur import CAS

    items = [DatasetItem.model_construct(id=f"lfx-helpdesk-{c['id']}", dataset_id="ds1", dataset_name="helpdesk-eval",
                                         input={"question": c["question"]},
                                         expected_output={"specialiste": c["specialiste"], "refus": c["refus"],
                                                          "categorie": c["categorie"]},
                                         metadata={"cas": c["id"]}, status="ACTIVE")
             for c in CAS.values()]
    ds = SimpleNamespace(items=items)
    lf.api = api
    api.dataset_run_items.create.side_effect = lambda **k: SimpleNamespace(dataset_run_id=f"run-{k['run_name']}")
    with mock.patch.object(Langfuse, "get_dataset", autospec=True, return_value=ds), \
         mock.patch.object(Langfuse, "create_dataset", autospec=True), \
         mock.patch.object(Langfuse, "create_dataset_item", autospec=True), \
         mock.patch("time.sleep"):
        S.creer_dataset_et_experiences(lf, EXP)
    lf.flush()
    gens = [s for s in exp_mem.get_finished_spans() if s.name == "routage"]
    assert len(gens) == len(CAS) * len(EXP["runs"]), len(gens)
    print(f"✅ Expériences : {len(EXP['runs'])} runs × {len(CAS)} items")
    srv.shutdown()


if __name__ == "__main__":
    main()

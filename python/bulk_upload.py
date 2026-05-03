"""Upload poe.watch market snapshots to Elasticsearch.

Reads data/raw/manifest.json (produced by download_data.py) and indexes each
dataset into poe-data-{league_id}. After each dataset finishes, writes one
record into the poe-bench index so we can compare Python vs C++ in Kibana.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from elasticsearch import Elasticsearch, helpers

LANGUAGE = "python"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
BENCH_INDEX = "poe-bench"
CHUNK_SIZE = 1000
MAX_CHUNK_BYTES = 10 * 1024 * 1024


def docs_for(dataset: dict, snapshot_at: str):
    """Yield one bulk-action dict per item in `dataset["file"]`.

    Stamps every doc with the fields the index template requires:
      @timestamp, league, hardcore, ingest_metadata.language.
    Uses a deterministic _id ({league_id}_{item.id}) so re-running the
    uploader overwrites instead of duplicating.
    """
    league_id = dataset["league_id"]
    is_hc = dataset["hardcore"]
    file_path = PROJECT_ROOT / dataset["file"]
    index = f"poe-data-{league_id}"

    with open(file_path, "r") as f:
        payload = json.load(f)

    for item in payload["items"]:
        item["@timestamp"] = snapshot_at
        item["league"] = league_id
        item["hardcore"] = is_hc
        item["ingest_metadata"] = {"language": LANGUAGE}
        yield {
            "_op_type": "index",
            "_index": index,
            "_id": f"{league_id}_{item['id']}",
            "_source": item,
        }


def upload_one(es: Elasticsearch, dataset: dict, snapshot_at: str):
    """Index one dataset. Returns (ok, failed, duration_ms)."""
    start = time.perf_counter()
    ok = 0
    failed = 0

    # streaming_bulk yields one (success, info) per doc; count failures without aborting
    for success, info in helpers.streaming_bulk(
        es,
        docs_for(dataset, snapshot_at),
        chunk_size=CHUNK_SIZE,
        max_chunk_bytes=MAX_CHUNK_BYTES,
        raise_on_error=False,
        raise_on_exception=False,
    ):
        if success:
            ok += 1
        else:
            failed += 1
            if failed <= 3:
                print(f"  [WARN] indexing failed: {info}", file=sys.stderr)

    duration_ms = (time.perf_counter() - start) * 1000
    return ok, failed, duration_ms


def write_bench_record(es: Elasticsearch, league_id: str, ok: int,
                       failed: int, duration_ms: float) -> None:
    """One row per (tool, league, run) — that's what Kibana will visualise."""
    es.index(index=BENCH_INDEX, document={
        "@timestamp": datetime.now(tz=timezone.utc)
                              .isoformat(timespec="seconds")
                              .replace("+00:00", "Z"),
        "tool": LANGUAGE,
        "league_id": league_id,
        "doc_count": ok,
        "duration_ms": duration_ms,
        "failures": failed,
    })


def main() -> int:
    es_url = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    es = Elasticsearch(
        es_url,
        request_timeout=60,
        max_retries=3,
        retry_on_timeout=True,
    )

    print(f"--- Bulk uploader ({LANGUAGE}) ---")
    print(f"[INFO] reading manifest from {MANIFEST_PATH}")
    manifest = json.loads(MANIFEST_PATH.read_text())
    snapshot_at = manifest["snapshot_at"]
    print(f"[INFO] snapshot_at = {snapshot_at}, datasets = {len(manifest['datasets'])}")

    total_failed = 0
    for ds in manifest["datasets"]:
        index = f"poe-data-{ds['league_id']}"
        print(f"[UPLOAD] {ds['league_id']:14s} -> {index}")
        ok, failed, duration_ms = upload_one(es, ds, snapshot_at)
        rate = ok / (duration_ms / 1000) if duration_ms > 0 else 0
        print(f"  ok={ok} failed={failed} duration_ms={duration_ms:.1f} ({rate:.0f} docs/s)")
        write_bench_record(es, ds["league_id"], ok, failed, duration_ms)
        total_failed += failed

    print(f"--- Done (total failures: {total_failed}) ---")
    return 1 if total_failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
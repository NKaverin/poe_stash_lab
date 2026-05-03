import json
import os
import sys
from elasticsearch import Elasticsearch


TEMPLATES = {
    "poe_data_template":  "poe_index_v1.json",
    "poe_bench_template": "poe_bench_v1.json",
}
MAPPINGS_DIR = os.path.join(os.path.dirname(__file__), "mappings")


def apply_template(es: Elasticsearch, name: str, filename: str) -> bool:
    """Apply one template. Returns True on success, False on failure."""
    path = os.path.join(MAPPINGS_DIR, filename)
    try:
        with open(path, "r") as f:
            mapping_data = json.load(f)
        es.indices.put_index_template(name=name, body=mapping_data)
        print(
            f"[SUCCESS]: Template '{name}' applied "
            f"(patterns={mapping_data.get('index_patterns')})."
        )
        return True
    except FileNotFoundError:
        print(f"[ERROR]: Schema file not found at {path}")
    except json.JSONDecodeError as e:
        print(f"[ERROR]: {filename} is not valid JSON: {e}")
    except Exception as e:
        print(f"[ERROR]: Failed to apply template '{name}': {e}")
    return False


def run_setup() -> int:
    es_url = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    es = Elasticsearch(es_url)
    print("--- Running Elasticsearch Initialisation ---")
    try:
        health = es.cluster.health()
        print(
            f"[STATUS]: Cluster is {health['status'].upper()} "
            f"with {health['number_of_nodes']} node(s)."
        )
    except Exception as e:
        print(f"[ERROR]: Could not connect to Elasticsearch: {e}")
        return 1
    failed = 0
    for name, filename in TEMPLATES.items():
        if not apply_template(es, name, filename):
            failed += 1
    print("--- Setup Complete ---" if failed == 0 else f"--- Setup Failed ({failed}) ---")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_setup())
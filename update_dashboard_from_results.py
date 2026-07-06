#!/usr/bin/env python3
"""Update the embedded latest-AI data block in index.html from categorizer outputs."""
from __future__ import annotations
import argparse
import csv
import json
import re
from datetime import date, datetime
from pathlib import Path

CATEGORY_MAP = {
    "likert": "mean",
    "survival": "survival",
    "existential": "existential",
    "extinction": "extinction",
    "economic": "economic",
    "instability": "instability",
    "surveillance": "surveillance",
    "delegated": "delegated_agency_loss_of_control",
    "information": "information_disorder",
    "infrastructure": "infrastructure_anxiety",
    "none": "none",
}


def read_first_csv_row(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"No rows found in {path}")
    return rows[0]


def maybe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def count_csv_rows(path: Path) -> int | None:
    if not path or not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return max(0, sum(1 for _ in f) - 1)


def scrape_ok_count(path: Path) -> int | None:
    if not path or not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return sum(1 for r in rows if (r.get("status") or "").lower() == "ok")


def load_embedded_data(index_text: str) -> dict:
    match = re.search(r'<script id="dashboard-data" type="application/json">\s*(.*?)\s*</script>', index_text, flags=re.S)
    if not match:
        raise ValueError("Could not find <script id=\"dashboard-data\" type=\"application/json\"> block in index.html")
    return json.loads(match.group(1))


def replace_embedded_data(index_text: str, data: dict) -> str:
    new_json = json.dumps(data, indent=2, ensure_ascii=False)
    return re.sub(
        r'(<script id="dashboard-data" type="application/json">\s*)(.*?)(\s*</script>)',
        lambda m: m.group(1) + "\n" + new_json + "\n  " + m.group(3).strip(),
        index_text,
        flags=re.S,
        count=1,
    )


def make_period_label(label_arg: str | None) -> str:
    if label_arg:
        return label_arg
    today = date.today()
    return f"Latest AI monthly update ({today.strftime('%B %Y')})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Update index.html from monthly AI categorization CSV outputs.")
    parser.add_argument("--index", type=Path, default=Path("index.html"), help="Path to index.html to update.")
    parser.add_argument("--overall", type=Path, required=True, help="Path to TableOverallAIStats_Expanded.csv from the categorizer.")
    parser.add_argument("--scrape-metadata", type=Path, default=None, help="Optional scraper metadata.csv.")
    parser.add_argument("--sources", type=Path, default=None, help="Optional sources_used.csv or discovered_sources.csv.")
    parser.add_argument("--period-label", default=None, help="Optional label for the latest AI column.")
    parser.add_argument("--coding-runs", default=None, help="Optional note for runs per article, e.g. 5 or 25.")
    parser.add_argument("--output", type=Path, default=None, help="Where to write the updated HTML. Defaults to --index.")
    args = parser.parse_args()

    html = args.index.read_text(encoding="utf-8")
    data = load_embedded_data(html)
    row = read_first_csv_row(args.overall)

    latest_values = {key: maybe_float(row.get(col)) for key, col in CATEGORY_MAP.items()}
    data["datasets"]["latest"]["values"] = latest_values
    data["datasets"]["latest"]["score"] = latest_values["likert"]
    data["datasets"]["latest"]["label"] = make_period_label(args.period_label)
    data["datasets"]["latest"]["shortLabel"] = "Latest AI"

    # Update tags using the strongest non-none categories.
    label_by_key = {m["key"]: m["label"] for m in data.get("measures", [])}
    strongest = sorted(
        [(k, v) for k, v in latest_values.items() if k not in {"likert", "none"}],
        key=lambda item: item[1],
        reverse=True,
    )[:3]
    data["datasets"]["latest"]["tags"] = [label_by_key.get(k, k).replace(" / loss of human control", "") for k, _ in strongest]
    data["datasets"]["latest"]["summary"] = (
        "This column was updated by the monthly workflow using the newest scraped AI articles. "
        "The strongest categories in this run were " + ", ".join(data["datasets"]["latest"]["tags"]) + "."
    )

    data["lastUpdated"] = date.today().isoformat()
    data["latestPeriod"] = data["datasets"]["latest"]["label"]
    data["methodNote"] = "Updated automatically from monthly scraped AI articles and LLM fear-category coding."
    data.setdefault("automation", {})
    data["automation"].update({
        "status": "Completed",
        "documents": int(maybe_float(row.get("documents"), 0)),
        "codedRows": int(maybe_float(row.get("rows"), 0)),
        "scrapedOk": scrape_ok_count(args.scrape_metadata) if args.scrape_metadata else None,
        "sourcesFound": count_csv_rows(args.sources) if args.sources else None,
        "codingRunsPerArticle": args.coding_runs or "see workflow",
    })

    updated = replace_embedded_data(html, data)
    out = args.output or args.index
    out.write_text(updated, encoding="utf-8")
    print(f"Updated {out} with latest AI values from {args.overall}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

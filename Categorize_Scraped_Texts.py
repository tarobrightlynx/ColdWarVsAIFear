#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
By: Eleanor A. Chen
Run the scraped AI articles through the fear-category coding setup.

This is the cleaned-up version of the older CallOpenAPI notebook/script I was using:
- pulls the scraped .txt files from ai_article_scraper_bigquery_package/ai_recent_texts/texts
- asks GPT to code each article more than once, so the results are not based on one pass
- keeps the raw GPT responses in a pickle file
- turns that pickle file into a CSV with one column per fear category
- includes the three AI-specific categories I added later:
    delegated_agency_loss_of_control
    information_disorder
    infrastructure_anxiety

Packages I used for this version:
    pip install openai==0.28 pandas tqdm

API key setup:
    Windows PowerShell:
        $env:OPENAI_API_KEY="your_key_here"
    Mac/Linux:
        export OPENAI_API_KEY="your_key_here"
    You can also put the key in a local text file and use --api-key-file openai_key.txt

Example:
    python categorize_scraped_texts_expanded.py --texts-dir "ai_article_scraper_bigquery_package\ai_recent_texts\texts" --out "ai_categorized_expanded" --runs 25
"""

import argparse
import json
import os
import pickle
import getpass
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from tqdm import tqdm

# Keeping the old OpenAI 0.28 style here because the original project was written that way.
# With a newer OpenAI package, this script may need: pip install openai==0.28
import openai
try:
    from openai.error import RateLimitError, APIError, Timeout, APIConnectionError
except Exception:  # fallback in case this OpenAI install exposes errors differently
    RateLimitError = Exception
    APIError = Exception
    Timeout = Exception
    APIConnectionError = Exception


DEFAULT_TEXTS_DIR = r"ai_article_scraper_bigquery_package\ai_recent_texts\texts"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_RUNS = 25
MAX_CHARS = 25000  # keeps huge scraped pages from getting too long for the model


PROMPT = """
Rate the level of fear in the text on a scale of 1-10 using these psychological fear appraisal methods.

1. Appraisal Theory of Emotion:
Evaluate fear based on relevance, unexpectedness, threat or harm potential, coping potential, and norm compatibility.

2. Intensity Scale, Likert-based 1-10:
Assign fear intensity based on language markers, urgency or finality, tone and framing, and whether coping strategies or reassurances are present.
1 = little or no fear expressed.
10 = extreme fear, catastrophic framing, or urgent existential danger.

3. Categorize the fear using any category/categories that apply. This is multi-label, so more than one category may be selected.

Categories:
- Survival: Fear involving physical life, bodily safety, or immediate danger.
- Existential dread: Fear involving purposelessness, meaninglessness, identity loss, human irrelevance, or loss of human uniqueness.
- Global extinction: Fear involving planetary destruction, species-level risk, or the end of civilization.
- Economic crisis: Fear involving job loss, financial insecurity, labor disruption, business disruption, or economic instability.
- Geopolitical instability: Fear involving war, arms races, national security, state conflict, political chaos, or global power competition.
- Surveillance: Fear involving privacy loss, monitoring, autonomy, data control, or tracking.
- Delegated agency/loss of human control: Fear that humans are transferring judgment, decision-making, creativity, labor, military authority, governance, or social authority to AI systems.
- Information disorder: Fear involving misinformation, deepfakes, synthetic media, political manipulation, AI-generated deception, or the collapse of trust in public information.
- Infrastructure anxiety: Fear involving the material systems behind AI, including data centers, electricity demand, water use, land use, environmental strain, energy grids, chips, or local community impacts.
- None: Use only when no significant fear is detected.

Return only a valid JSON object. Do not include markdown, code fences, emojis, or bold text.
Use exactly this structure:
{
  "Likert_scale": {
    "scale": 1,
    "reason": "brief reason for the fear score"
  },
  "Category": {
    "category": ["category name 1", "category name 2"],
    "reason": "brief reason for the chosen category/categories"
  }
}
""".strip()


def read_text_file(path: Path) -> str:
    """Read one scraped file, trying a few encodings because web text can be messy."""
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=enc, errors="replace")
        except Exception:
            continue
    return path.read_text(errors="replace")


def strip_scraper_header(text: str) -> str:
    """Drop the scraper's header block if this file has one."""
    marker = "=" * 80
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text.strip()


def extract_url_from_header(text: str, fallback: str) -> str:
    """Use the saved URL from the header, or fall back to the filename."""
    match = re.search(r"^URL:\s*(.+)$", text, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    return fallback


def trim_text(text: str, max_chars: int = MAX_CHARS) -> str:
    """Shorten long articles but keep both the opening and ending sections."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n[...TEXT TRUNCATED FOR LENGTH...]\n\n" + text[-half:]


def clean_json_string(raw: str) -> str:
    """Clean up the model response enough to pull out the JSON object."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    return raw.strip()


def safe_json_loads(raw: str) -> Dict[str, Any]:
    """Parse the model's JSON, and show the raw answer if it breaks."""
    cleaned = clean_json_string(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse model output as JSON. Output was:\n{raw}\n\nError: {exc}")


def safe_chat_completion(model: str, messages: List[Dict[str, str]], max_tokens: int = 650, temperature: float = 0.2,
                         max_retries: int = 8, backoff_start: int = 2) -> str:
    """Call OpenAI, backing off when the API is busy or rate-limited."""
    backoff = backoff_start
    for attempt in range(1, max_retries + 1):
        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response["choices"][0]["message"]["content"]
        except (RateLimitError, APIError, Timeout, APIConnectionError) as exc:
            if attempt == max_retries:
                raise
            print(f"OpenAI temporary error/rate limit: {exc}. Sleeping {backoff}s before retry {attempt}/{max_retries}...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 90)
        except Exception:
            raise
    raise RuntimeError("OpenAI request failed after retries.")


def ask_gpt_about_text(text: str, model: str) -> Dict[str, Any]:
    """Send one article to the model and return the parsed coding result."""
    text = trim_text(text)
    raw_response = safe_chat_completion(
        model=model,
        messages=[
            {"role": "system", "content": "You are a careful research assistant doing consistent content analysis coding."},
            {"role": "user", "content": PROMPT + "\n\nTEXT TO CODE:\n" + text},
        ],
        max_tokens=650,
        temperature=0.2,
    )
    return safe_json_loads(raw_response)


def category_to_text(category_value: Any) -> str:
    """Make the category value easier to search, whether GPT returns a list or a string."""
    if isinstance(category_value, list):
        return " | ".join(str(x) for x in category_value).lower()
    return str(category_value).lower()


def has_any(text: str, patterns: List[str]) -> int:
    return 1 if any(p in text for p in patterns) else 0


def flatten_pickle_to_dataframe(data: Dict[str, List[Dict[str, Any]]]) -> pd.DataFrame:
    """Turn the raw GPT response dictionary into the table format used for analysis."""
    rows = []

    for key, value in data.items():
        link = key.strip().split("/")[-1].split("\\")[-1]
        source = key

        for iteration, item in enumerate(value):
            try:
                likert_raw = item.get("Likert_scale", {}).get("scale", None)
                # Allows for 7, "7", or "7/10" in case the model formats it differently.
                match = re.search(r"\d+", str(likert_raw))
                likert_score = int(match.group(0)) if match else None
                if likert_score is not None:
                    likert_score = max(1, min(10, likert_score))

                likert_reason = str(item.get("Likert_scale", {}).get("reason", ""))
                category_value = item.get("Category", {}).get("category", "")
                category_type = category_to_text(category_value)
                category_reason = str(item.get("Category", {}).get("reason", ""))

                cat_survival = has_any(category_type, ["survival", "physical life", "bodily safety", "immediate danger"])
                cat_existential = has_any(category_type, ["existential dread", "identity loss", "human irrelevance", "meaninglessness", "loss of meaning"])
                cat_global = has_any(category_type, ["global extinction", "planetary", "species-level", "end of civilization", "civilizational"])
                cat_economic = has_any(category_type, ["economic crisis", "job loss", "labor", "employment", "financial", "workforce"])
                cat_geopolitical = has_any(category_type, ["geopolitical", "instability", "arms race", "national security", "war", "state conflict"])
                cat_surveillance = has_any(category_type, ["surveillance", "privacy", "monitoring", "data control", "tracking", "autonomy"])

                # Extra categories added for the newer AI-era articles.
                cat_delegated = has_any(category_type, [
                    "delegated agency", "loss of human control", "loss of control", "human control",
                    "decision-making", "decision making", "transferring judgment", "authority to ai", "autonomous systems"
                ])
                cat_information = has_any(category_type, [
                    "information disorder", "misinformation", "disinformation", "deepfake", "deepfakes",
                    "synthetic media", "political manipulation", "ai-generated deception", "public information", "collapse of trust"
                ])
                cat_infrastructure = has_any(category_type, [
                    "infrastructure anxiety", "data center", "data centers", "electricity", "water use",
                    "land use", "environmental strain", "energy grid", "energy grids", "chips", "local community"
                ])

                # Count "none" only when it is the only label selected.
                cat_none_raw = has_any(category_type, ["none", "no significant fear"])
                category_sum = (
                    cat_survival + cat_existential + cat_global + cat_economic + cat_geopolitical +
                    cat_surveillance + cat_delegated + cat_information + cat_infrastructure
                )
                cat_none = 1 if cat_none_raw and category_sum == 0 else 0

                rows.append({
                    "link": link,
                    "source": source,
                    "iteration": iteration,
                    "likert_score": likert_score,
                    "likert_reason": likert_reason,
                    "survival": cat_survival,
                    "existential": cat_existential,
                    "extinction": cat_global,
                    "economic": cat_economic,
                    "instability": cat_geopolitical,
                    "surveillance": cat_surveillance,
                    "delegated_agency_loss_of_control": cat_delegated,
                    "information_disorder": cat_information,
                    "infrastructure_anxiety": cat_infrastructure,
                    "none": cat_none,
                    "category_raw": str(category_value),
                    "category_reason": category_reason,
                })
            except Exception as exc:
                rows.append({
                    "link": link,
                    "source": source,
                    "iteration": iteration,
                    "likert_score": None,
                    "likert_reason": "",
                    "survival": 0,
                    "existential": 0,
                    "extinction": 0,
                    "economic": 0,
                    "instability": 0,
                    "surveillance": 0,
                    "delegated_agency_loss_of_control": 0,
                    "information_disorder": 0,
                    "infrastructure_anxiety": 0,
                    "none": 0,
                    "category_raw": "",
                    "category_reason": f"Flattening error: {exc}",
                })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates().reset_index(drop=True)
    return df


def make_summary_tables(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Make the per-article summary table and the overall summary table."""
    category_cols = [
        "survival", "existential", "extinction", "economic", "instability", "surveillance",
        "delegated_agency_loss_of_control", "information_disorder", "infrastructure_anxiety", "none"
    ]

    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    by_link = df.groupby("link").agg(
        mean=("likert_score", "mean"),
        std=("likert_score", "std"),
        runs=("iteration", "count"),
        **{col: (col, "mean") for col in category_cols}
    ).reset_index()

    overall_values = {
        "mean": df.groupby("link")["likert_score"].mean().mean(),
        "std": df.groupby("link")["likert_score"].mean().std(),
        "documents": df["link"].nunique(),
        "rows": len(df),
    }
    for col in category_cols:
        overall_values[col] = df.groupby("link")[col].mean().mean()

    overall = pd.DataFrame([overall_values])
    return by_link, overall


def collect_text_files(texts_dir: Path) -> List[Path]:
    return sorted([p for p in texts_dir.rglob("*.txt") if p.is_file()])



def resolve_api_key(args) -> str:
    """Find the OpenAI API key from the command line, a file, the environment, or a prompt."""
    if getattr(args, "api_key", None):
        return args.api_key.strip()

    if getattr(args, "api_key_file", None):
        key_path = Path(args.api_key_file)
        if not key_path.exists():
            raise FileNotFoundError(f"API key file not found: {key_path}")
        key = key_path.read_text(encoding="utf-8").strip()
        if key:
            return key

    key = os.getenv("OPENAI_API_KEY", "ENTER_KEY").strip()
    if key:
        return key

    if getattr(args, "no_key_prompt", False):
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Either set it, pass --api-key-file, or pass --api-key."
        )

    print("OPENAI_API_KEY is not set.")
    print("Paste your OpenAI API key below. It will only be used for this run and will not be saved by this script.")
    key = getpass.getpass("OpenAI API key: ").strip()
    if not key:
        raise RuntimeError("No API key provided.")
    return key

def main() -> int:
    parser = argparse.ArgumentParser(description="Categorize scraped AI article text files using GPT and expanded fear categories.")
    parser.add_argument("--texts-dir", type=Path, default=Path(DEFAULT_TEXTS_DIR), help="Folder containing scraped .txt files.")
    parser.add_argument("--out", type=Path, default=Path("ai_categorized_expanded"), help="Output folder.")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Number of GPT runs per text file.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model name.")
    parser.add_argument("--max-files", type=int, default=0, help="Only process first N text files; 0 means all.")
    parser.add_argument("--resume", action="store_true", help="Resume from existing raw pickle if available.")
    parser.add_argument("--api-key", default=None, help="OpenAI API key for this run. Safer option: use OPENAI_API_KEY or --api-key-file.")
    parser.add_argument("--api-key-file", type=Path, default=None, help="Path to a text file containing your OpenAI API key.")
    parser.add_argument("--no-key-prompt", action="store_true", help="Do not prompt for an API key if none is found; fail instead.")
    args = parser.parse_args()

    api_key = resolve_api_key(args)
    openai.api_key = api_key

    text_files = collect_text_files(args.texts_dir)
    if args.max_files and args.max_files > 0:
        text_files = text_files[:args.max_files]

    if not text_files:
        raise FileNotFoundError(f"No .txt files found in {args.texts_dir}")

    args.out.mkdir(parents=True, exist_ok=True)
    raw_pkl_path = args.out / "AIFearDic_Expanded.pkl"
    merged_csv_path = args.out / "TableAIFinalData_Expanded.csv"
    stats_csv_path = args.out / "TableAIStats_Expanded.csv"
    overall_csv_path = args.out / "TableOverallAIStats_Expanded.csv"
    errors_csv_path = args.out / "errors.csv"

    AIdic: Dict[str, List[Dict[str, Any]]] = {}
    errors = []

    if args.resume and raw_pkl_path.exists():
        with raw_pkl_path.open("rb") as f:
            AIdic = pickle.load(f)
        print(f"Resumed existing pickle with {len(AIdic)} already-coded sources.")

    for path in tqdm(text_files, desc="Coding text files"):
        raw_text = read_text_file(path)
        source_key = extract_url_from_header(raw_text, fallback=str(path))
        article_text = strip_scraper_header(raw_text)

        if not article_text or len(article_text) < 200:
            errors.append({"file": str(path), "source": source_key, "error": "Text too short or empty."})
            continue

        existing_runs = len(AIdic.get(source_key, []))
        if existing_runs >= args.runs:
            continue

        temp = AIdic.get(source_key, [])
        for run_index in range(existing_runs, args.runs):
            try:
                print(f"\nProcessing: {path.name} | run {run_index + 1}/{args.runs}")
                data = ask_gpt_about_text(article_text, model=args.model)
                print(json.dumps(data, ensure_ascii=False)[:500])
                temp.append(data)

                # Save after each successful run so a long coding job can be resumed later.
                AIdic[source_key] = temp
                with raw_pkl_path.open("wb") as f:
                    pickle.dump(AIdic, f)
            except Exception as exc:
                errors.append({
                    "file": str(path),
                    "source": source_key,
                    "run": run_index,
                    "error": repr(exc),
                })
                print(f"Error on {path.name}, run {run_index}: {exc}")
                # Move on instead of letting one bad article stop the whole batch.
                break

    # Turn the saved raw responses into the final CSV outputs.
    mergedf = flatten_pickle_to_dataframe(AIdic)
    mergedf.to_csv(merged_csv_path, index=False)

    stats_df, overall_df = make_summary_tables(mergedf)
    stats_df.to_csv(stats_csv_path, index=False)
    overall_df.to_csv(overall_csv_path, index=False)

    if errors:
        pd.DataFrame(errors).to_csv(errors_csv_path, index=False)

    print("\nDone.")
    print(f"Raw pickle saved to: {raw_pkl_path}")
    print(f"Merged data saved to: {merged_csv_path}")
    print(f"Per-article stats saved to: {stats_csv_path}")
    print(f"Overall stats saved to: {overall_csv_path}")
    if errors:
        print(f"Errors saved to: {errors_csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
By: Eleanor A. Chen
Small scraper for collecting AI-related articles for the fear-of-technology project.

General flow:
- start with the URL list in this file or a URL file passed in from the command line
- optionally look for newer AI articles from public discovery sources
- download each page with a regular browser-style User-Agent
- pull article text from HTML with trafilatura first, then BeautifulSoup if needed
- pull PDF text with pypdf when it is installed
- save each source as a .txt file and keep a metadata CSV with the scrape status

Notes for using it:
- This does not get around paywalls, logins, robots rules, or publisher blocks.
- Subscription sites like NYTimes, Washington Post, FT, and Forbes may give little text.
- Discovery is only a starting point; it can miss articles or bring back duplicates.
- GDELT may rate-limit requests. Try --discovery-delay, --gdelt-retries,
  --discover-provider google_news, or --discover-provider bigquery if that happens.
- For blocked sources, use proper access or swap in an open-access source.

Install packages I usually use:
    pip install requests beautifulsoup4 trafilatura pypdf pandas tqdm google-cloud-bigquery

Examples:
    # Scrape only the built-in list
    python scrape_ai_articles.py --out ai_article_texts --delay 2

    # Find recent AI articles from the last 4 weeks, then scrape those too
    python scrape_ai_articles.py --weeks 4 --out ai_recent_texts --max-discovered-per-query 10 --discovery-delay 8

    # Use only recent discovery, without the built-in URLs
    python scrape_ai_articles.py --weeks 2 --no-builtins --out ai_recent_only

    # Add my own search topics
    python scrape_ai_articles.py --weeks 6 --query "AI job displacement layoffs" --query "AI data centers water electricity"

    # Use GDELT through BigQuery instead of the public GDELT API
    python scrape_ai_articles.py --weeks 1 --discover-provider bigquery --bq-project YOUR_PROJECT_ID --out ai_recent_texts

    # Scrape URLs from my own file
    python scrape_ai_articles.py --urls-file ai_sources_urls.txt --out ai_article_texts --no-builtins
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote_plus, urlparse, urlunparse, parse_qsl, urlencode
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura  # type: ignore
except Exception:  # pragma: no cover
    trafilatura = None

try:
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover
    PdfReader = None


@dataclass(frozen=True)
class Source:
    url: str
    theme: str = "uncategorized"
    note: str = ""


DEFAULT_DISCOVERY_QUERIES: dict[str, list[str]] = {
    "labor_economic": [
        "artificial intelligence job loss layoffs automation white collar jobs",
        "AI labor displacement worker retraining employment projections",
    ],
    "information_surveillance": [
        "AI deepfakes misinformation election public trust",
        "AI surveillance privacy identity theft cybersecurity",
    ],
    "existential_delegated_agency": [
        "AI safety existential risk superintelligence loss of control",
        "AI autonomous decision making human control agency",
    ],
    "infrastructure_anxiety": [
        "AI data centers electricity water power demand community opposition",
        "AI infrastructure data center energy water local impact",
    ],
    "policy_geopolitics": [
        "AI regulation national security arms race government policy",
        "AI geopolitics US China competition national security",
    ],
    "public_opinion": [
        "Americans views artificial intelligence concerned excited poll",
        "public opinion AI fear concern trust survey",
    ],
}

"""
BUILTIN_SOURCES: list[Source] = [
    # Public opinion / current AI concern
    Source("https://www.pewresearch.org/short-reads/2026/03/12/key-findings-about-how-americans-view-artificial-intelligence/", "public_opinion", "Added: 2026 Pew summary of U.S. AI attitudes"),
    Source("https://www.pewresearch.org/internet/2025/04/03/how-the-us-public-and-ai-experts-view-artificial-intelligence/", "public_opinion", "Pew public vs expert AI views"),
    Source("https://hai.stanford.edu/ai-index/2026-ai-index-report/public-opinion", "public_opinion", "Added: Stanford HAI 2026 AI Index public opinion chapter"),
    Source("https://hai.stanford.edu/ai-index/2026-ai-index-report", "public_opinion", "Added: Stanford HAI 2026 AI Index full report page"),

    # Infrastructure anxiety / data centers / material impacts
    Source("https://www.reuters.com/world/us/americans-wary-ai-driven-data-center-boom-reutersipsos-poll-shows-2026-06-11/", "infrastructure_anxiety", "Added: Reuters/Ipsos data-center public concern poll"),
    Source("https://www.reuters.com/business/energy/fast-tracked-power-plants-fuel-ai-boom-with-little-public-scrutiny-2026-06-16/", "infrastructure_anxiety", "Added: Reuters investigation on AI power plants"),
    Source("https://news.gallup.com/poll/709772/americans-oppose-data-centers-area.aspx", "infrastructure_anxiety", "Added: Gallup 2026 AI data center local opposition"),
    Source("https://www.axios.com/2026/06/18/arizona-ai-data-center-water-power", "infrastructure_anxiety", "Added: Axios on AI data centers, energy, and water"),

    # Policy / geopolitics / governance
    Source("https://www.ai.gov/action-plan", "policy_geopolitics", "Added: official AI Action Plan"),
    Source("https://bidenwhitehouse.archives.gov/briefing-room/statements-releases/2025/01/13/fact-sheet-ensuring-u-s-security-and-economic-strength-in-the-age-of-artificial-intelligence/", "policy_geopolitics", "White House archive AI security/economic strength fact sheet"),
    Source("https://www.axios.com/2025/06/25/ai-united-states-government-plan", "policy_geopolitics", "Axios AI Marshall Plan article"),
    Source("https://www.rand.org/pubs/commentary/2025/03/seeking-stability-in-the-competition-for-ai-advantage.html", "policy_geopolitics", "RAND commentary on AI competition stability"),
    Source("https://www.rand.org/content/dam/rand/pubs/perspectives/PEA3600/PEA3691-4/RAND_PEA3691-4.pdf", "policy_geopolitics", "RAND PDF perspective"),
    Source("https://www.dhs.gov/sites/default/files/2024-10/24_0930_ia_24-320-ia-publication-2025-hta-final-30sep24-508.pdf", "policy_geopolitics", "DHS 2025 Homeland Threat Assessment PDF"),
    Source("https://www.gov.ca.gov/2025/06/17/as-trump-moves-to-decimate-state-ai-laws-governor-newsom-taps-the-nations-top-experts-for-groundbreaking-ai-report/", "policy_geopolitics", "California AI policy/report announcement"),
    Source("https://www.ft.com/content/9751cbe5-e560-4f1a-82ea-9a5899c135a6", "policy_geopolitics", "FT military AI article; may be paywalled"),
    Source("https://www.axios.com/2025/06/25/tech-pentagon-defense-history-detachment-201", "policy_geopolitics", "Axios tech and Pentagon article"),
    Source("https://www.washingtonpost.com/technology/2025/05/08/altman-congress-openai-regulation/", "policy_geopolitics", "Washington Post AI regulation article; may be paywalled"),
    Source("https://www.washingtonpost.com/opinions/2024/10/07/sam-altman-ai-power-danger/", "policy_geopolitics", "Washington Post opinion on AI power; may be paywalled"),

    # Labor / economic anxiety
    Source("https://www.bls.gov/opub/mlr/2025/article/incorporating-ai-impacts-in-bls-employment-projections.htm", "labor_economic", "BLS official article on AI in employment projections"),
    Source("https://www.brookings.edu/articles/ai-labor-displacement-and-the-limits-of-worker-retraining/", "labor_economic", "Brookings AI labor displacement/retraining"),
    Source("https://budgetlab.yale.edu/research/evaluating-impact-ai-labor-market-current-state-affairs", "labor_economic", "Added: Yale Budget Lab labor-market evidence"),
    Source("https://www.bcg.com/publications/2026/ai-will-reshape-more-jobs-than-it-replaces", "labor_economic", "Added: BCG 2026 jobs reshaped by AI"),
    Source("https://www.businessinsider.com/pwc-global-jobs-barometer-ai-advanced-skills-entry-level-jobs-2026-6", "labor_economic", "Added: BI coverage of PwC 2026 AI Jobs Barometer; may be paywalled"),
    Source("https://www.businessinsider.com/openai-response-to-dario-amodei-white-collar-jobs-ai-prediction-2025-6", "labor_economic", "Business Insider OpenAI response to Amodei; may be paywalled"),
    Source("https://www.marketingaiinstitute.com/blog/dario-amodei-ai-entry-level-jobs", "labor_economic", "Marketing AI Institute on Amodei entry-level job warning"),
    Source("https://dig.watch/updates/anthropic-ceo-warns-of-mass-job-losses-from-ai", "labor_economic", "Digital Watch summary of Anthropic CEO job-loss warning"),
    Source("https://www.island.io/new-tab/ai-automation-impact-on-white-collar-jobs", "labor_economic", "Island article on automation/white-collar jobs"),
    Source("https://www.shrm.org/executive-network/insights/research/measuring-automation-displacement-risk-march-2025-en", "labor_economic", "SHRM automation displacement risk"),
    Source("https://www.sciencedirect.com/science/article/pii/S2773032824000154", "labor_economic", "ScienceDirect article; may require institution access"),
    Source("https://www.hr-brew.com/stories/2025/02/28/despite-fears-of-displacement-policy-pros-are-confident-ai-will-impact-tasks-more-profoundly-than-jobs", "labor_economic", "HR Brew article on task vs job impacts"),
    Source("https://time.com/7290751/ai-future-of-work-essay/", "labor_economic", "Time essay on AI and future of work"),
    Source("https://arxiv.org/abs/2605.23159", "labor_economic", "Added: 2026 arXiv labor demand reorganization paper"),
    Source("https://arxiv.org/abs/2507.07935", "labor_economic", "Added: Microsoft/Bing Copilot occupation applicability paper"),
    Source("https://arxiv.org/abs/2507.08244", "labor_economic", "Added: AI capabilities and labor outcomes paper"),

    # Existential risk / delegated agency / loss of control
    Source("https://time.com/7265056/nuclear-level-risk-of-superintelligent-ai/", "existential_delegated_agency", "Time on nuclear-level risk of superintelligent AI"),
    Source("https://www.pnas.org/doi/10.1073/pnas.2419055122", "existential_delegated_agency", "PNAS immediate harms vs existential-risk narratives"),
    Source("https://www.news.uzh.ch/en/articles/media/2025/fear-of-ki-risks.html", "existential_delegated_agency", "University of Zurich release on PNAS study"),
    Source("https://safe.ai/work/statement-on-ai-risk", "existential_delegated_agency", "Center for AI Safety statement"),
    Source("https://safe.ai/ai-risk", "existential_delegated_agency", "Center for AI Safety AI risk explainer"),
    Source("https://newsletter.safe.ai/p/ai-safety-newsletter-49-superintelligence", "existential_delegated_agency", "CAIS newsletter on superintelligence"),
    Source("https://www.lesswrong.com/posts/XsYQyBgm8eKjd3Sqw/on-the-rationality-of-deterring-asi", "existential_delegated_agency", "LessWrong post on deterring ASI"),
    Source("https://thezvi.wordpress.com/2025/03/14/on-maim-and-superintelligence-strategy/", "existential_delegated_agency", "Zvi/WordPress commentary on superintelligence strategy"),
    Source("https://time.com/7297582/ai-safety-risks-paul-tudor-jones-essay/", "existential_delegated_agency", "Time essay on AI safety risks"),
    Source("https://www.axios.com/newsletters/axios-am-167e2440-d545-11ef-86f8-718f1121da12", "existential_delegated_agency", "Axios AM catastrophic warning newsletter"),
    Source("https://www.theguardian.com/technology/2025/may/10/ai-firms-urged-to-calculate-existential-threat-amid-fears-it-could-escape-human-control", "existential_delegated_agency", "Guardian on AI firms/existential threat"),
    Source("https://www.scientificamerican.com/article/heres-why-ai-may-be-extremely-dangerous-whether-its-conscious-or-not/", "existential_delegated_agency", "Scientific American on dangerous AI"),
    Source("https://www.vox.com/future-perfect/414324/ai-consciousness-welfare-suffering-chatgpt-claude", "existential_delegated_agency", "Vox on AI consciousness/welfare"),
    Source("https://ipwatchdog.com/2025/05/13/existential-threat-ai-consciousness/id%3D188819/", "existential_delegated_agency", "IPWatchdog on AI consciousness/existential threat"),
    Source("https://www.linkedin.com/pulse/dario-amodeis-ai-warnings-call-awareness-may-2025-dev-mhaiskar--linec/", "existential_delegated_agency", "LinkedIn commentary; scrape may fail/login"),
    Source("https://archive.is/c8J9u", "existential_delegated_agency", "Archive link; scrape may fail"),

    # Information disorder / cyber / surveillance / trust
    Source("https://www.forbes.com/sites/chuckbrooks/2025/07/06/criminal-hackers-are-employing-ai-to-facilitate-identity-theft/", "information_surveillance", "Forbes on AI-enabled identity theft; may be paywalled/blocked"),
    Source("https://www.route-fifty.com/artificial-intelligence/2025/06/declining-public-trust-ai-national-security-problem/406329/", "information_surveillance", "Route Fifty on AI trust and national security"),
    Source("https://www.axios.com/newsletters/axios-ai-plus-4b28126b-fc74-48d2-b891-fa5855bd888b", "information_surveillance", "Axios AI+ newsletter"),
    Source("https://www.washingtonpost.com/opinions/2025/01/03/technology-internet-ai-future/", "information_surveillance", "Washington Post opinion on tech/AI future; may be paywalled"),

    # Spending / arms-race framing
    Source("https://www.nytimes.com/2025/06/27/technology/ai-spending-openai-amazon-meta.html", "arms_race_spending", "NYTimes AI spending article; likely paywalled"),
]
"""


TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ocid"
}


def normalize_url(url: str) -> str:
    """Tidy a URL just enough so duplicate links match up."""
    url = url.strip().strip('"').strip("'")
    if not url:
        return ""
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in TRACKING_QUERY_KEYS]
    path = parsed.path.rstrip("/") if parsed.path != "/" else parsed.path
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), path, "", urlencode(query), ""))


def slugify_url(url: str, max_len: int = 90) -> str:
    parsed = urlparse(url)
    host = re.sub(r"^www\.", "", parsed.netloc)
    path = parsed.path.strip("/") or "index"
    raw = f"{host}_{path}"
    raw = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_").lower()
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{raw[:max_len].rstrip('_')}_{digest}"


def read_sources_file(path: Path) -> list[Source]:
    """Load URLs from a plain text file or a small CSV export."""
    if not path.exists():
        raise FileNotFoundError(path)
    sources: list[Source] = []
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "url" not in [h.lower() for h in reader.fieldnames]:
                raise ValueError("CSV must include a 'url' column.")
            fieldmap = {name.lower(): name for name in reader.fieldnames}
            for row in reader:
                url = row.get(fieldmap["url"], "").strip()
                if url and not url.startswith("#"):
                    theme = row.get(fieldmap.get("theme", ""), "") if "theme" in fieldmap else "uncategorized"
                    note = row.get(fieldmap.get("note", ""), "") if "note" in fieldmap else ""
                    sources.append(Source(url=url, theme=theme or "uncategorized", note=note))
    else:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    sources.append(Source(line))
    return sources


def dedupe_sources(sources: Iterable[Source]) -> list[Source]:
    seen: set[str] = set()
    out: list[Source] = []
    for s in sources:
        norm = normalize_url(s.url)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(Source(norm, s.theme, s.note))
    return out


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36; academic-research-text-extraction/1.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def cutoff_from_weeks(weeks: int) -> datetime:
    if weeks <= 0:
        raise ValueError("--weeks must be a positive integer when using recent-article discovery.")
    return datetime.now(timezone.utc) - timedelta(weeks=weeks)


def _theme_queries_from_args(raw_queries: list[str]) -> dict[str, list[str]]:
    if raw_queries:
        return {"discovered_custom": raw_queries}
    return DEFAULT_DISCOVERY_QUERIES


def _gdelt_json_with_retries(
    session: requests.Session,
    api_url: str,
    query: str,
    timeout: int,
    retries: int,
    backoff: float,
) -> Optional[dict]:
    """Request GDELT results, with a few retries for the errors it commonly throws."""
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(api_url, timeout=timeout)
            status = resp.status_code

            # GDELT rate-limits pretty easily, so use Retry-After when it gives one.
            if status == 429 or status in {500, 502, 503, 504}:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep_for = float(retry_after)
                else:
                    sleep_for = backoff * (2 ** (attempt - 1)) + random.uniform(0, 1.5)
                last_error = f"HTTP {status}; sleeping {sleep_for:.1f}s before retry {attempt}/{retries}"
                print(f"[discover:gdelt] {last_error} for query={query!r}", file=sys.stderr)
                time.sleep(sleep_for)
                continue

            resp.raise_for_status()

            # A 200 from GDELT is not always useful; sometimes the body is empty or HTML.
            text = resp.text.strip()
            if not text:
                sleep_for = backoff * (2 ** (attempt - 1)) + random.uniform(0, 1.5)
                last_error = f"empty response; sleeping {sleep_for:.1f}s before retry {attempt}/{retries}"
                print(f"[discover:gdelt] {last_error} for query={query!r}", file=sys.stderr)
                time.sleep(sleep_for)
                continue

            try:
                return resp.json()
            except json.JSONDecodeError:
                preview = text[:160].replace("\n", " ")
                sleep_for = backoff * (2 ** (attempt - 1)) + random.uniform(0, 1.5)
                last_error = f"non-JSON response preview={preview!r}; sleeping {sleep_for:.1f}s before retry {attempt}/{retries}"
                print(f"[discover:gdelt] {last_error} for query={query!r}", file=sys.stderr)
                time.sleep(sleep_for)
                continue

        except requests.RequestException as e:
            sleep_for = backoff * (2 ** (attempt - 1)) + random.uniform(0, 1.5)
            last_error = f"request error {e!r}; sleeping {sleep_for:.1f}s before retry {attempt}/{retries}"
            print(f"[discover:gdelt] {last_error} for query={query!r}", file=sys.stderr)
            time.sleep(sleep_for)

    print(f"[discover:gdelt] giving up for query={query!r}. Last error: {last_error}", file=sys.stderr)
    return None


def discover_with_gdelt(
    session: requests.Session,
    queries_by_theme: dict[str, list[str]],
    weeks: int,
    max_per_query: int,
    timeout: int = 30,
    discovery_delay: float = 5.0,
    retries: int = 5,
    backoff: float = 5.0,
) -> list[Source]:
    """Find recent article URLs with GDELT's public DOC API.

    GDELT is useful, but it can be touchy about quick repeat requests, so this
    keeps the loop slow and retries the usual rate-limit or bad-response cases.
    When it still does not cooperate, Google News RSS is the backup option.
    """
    cutoff = cutoff_from_weeks(weeks)
    start = cutoff.strftime("%Y%m%d%H%M%S")
    end = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    discovered: list[Source] = []

    # Larger requests work, but smaller batches have caused fewer 429s for me.
    maxrecords = max(1, min(max_per_query, 50))

    for theme, queries in queries_by_theme.items():
        for q in queries:
            api_url = (
                "https://api.gdeltproject.org/api/v2/doc/doc?"
                f"query={quote_plus(q)}&mode=ArtList&format=json"
                f"&maxrecords={maxrecords}&startdatetime={start}&enddatetime={end}&sort=datedesc"
            )
            data = _gdelt_json_with_retries(
                session=session,
                api_url=api_url,
                query=q,
                timeout=timeout,
                retries=retries,
                backoff=backoff,
            )
            if not data:
                # Skip this query rather than killing the whole run.
                continue

            for item in data.get("articles", []):
                url = item.get("url") or ""
                if not url:
                    continue
                seen_date = item.get("seendate") or ""
                # GDELT seendate has shown up in both plain digits and T/Z formats.
                is_recent = True
                digits = re.sub(r"\D", "", seen_date)
                if len(digits) >= 8:
                    try:
                        if len(digits) >= 14:
                            dt = datetime.strptime(digits[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                        else:
                            dt = datetime.strptime(digits[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
                        is_recent = dt >= cutoff
                    except Exception:
                        is_recent = True
                if is_recent:
                    title = item.get("title", "")
                    domain = item.get("domain", "")
                    discovered.append(Source(
                        url=url,
                        theme=theme,
                        note=f"Discovered by GDELT within last {weeks} weeks. Query: {q}. Title: {title}. Domain: {domain}",
                    ))

            if discovery_delay > 0:
                time.sleep(discovery_delay + random.uniform(0, 1.0))
    return dedupe_sources(discovered)

def discover_with_google_news_rss(
    session: requests.Session,
    queries_by_theme: dict[str, list[str]],
    weeks: int,
    max_per_query: int,
    timeout: int = 30,
) -> list[Source]:
    """Get recent links from Google News RSS.

    Some of these links are Google redirect URLs instead of the publisher's direct
    link. The request should still follow redirects, but GDELT usually gives cleaner URLs.
    """
    cutoff = cutoff_from_weeks(weeks)
    discovered: list[Source] = []
    for theme, queries in queries_by_theme.items():
        for q in queries:
            rss_query = f"{q} when:{weeks}w"
            rss_url = (
                "https://news.google.com/rss/search?"
                f"q={quote_plus(rss_query)}&hl=en-US&gl=US&ceid=US:en"
            )
            try:
                resp = session.get(rss_url, timeout=timeout)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)
                count = 0
                for item in root.findall(".//item"):
                    if count >= max_per_query:
                        break
                    link_el = item.find("link")
                    title_el = item.find("title")
                    pub_el = item.find("pubDate")
                    link = link_el.text.strip() if link_el is not None and link_el.text else ""
                    title = title_el.text.strip() if title_el is not None and title_el.text else ""
                    pub_date = pub_el.text.strip() if pub_el is not None and pub_el.text else ""
                    if not link:
                        continue
                    is_recent = True
                    if pub_date:
                        try:
                            dt = parsedate_to_datetime(pub_date)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            is_recent = dt.astimezone(timezone.utc) >= cutoff
                        except Exception:
                            is_recent = True
                    if is_recent:
                        discovered.append(Source(
                            url=link,
                            theme=theme,
                            note=f"Discovered by Google News RSS within last {weeks} weeks. Query: {q}. Title: {title}. Published: {pub_date}",
                        ))
                        count += 1
            except Exception as e:
                print(f"[discover:google_news] failed for query={q!r}: {e}", file=sys.stderr)
            time.sleep(1.0)
    return dedupe_sources(discovered)



def _make_topic_regex(query: str) -> str:
    """Turn a search phrase into a small topic regex for matching GDELT metadata."""
    tokens = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", query)]
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "are", "into", "about",
        "artificial", "intelligence", "generative", "chatgpt", "openai", "anthropic",
    }
    useful = []
    for t in tokens:
        if t not in stop and t not in useful:
            useful.append(t)
    # Keep the pattern short so it does not turn into a giant catch-all regex.
    useful = useful[:8]
    if not useful:
        useful = ["risk", "jobs", "policy", "safety", "automation"]
    return r"(" + "|".join(re.escape(t) for t in useful) + r")"


def discover_with_bigquery(
    queries_by_theme: dict[str, list[str]],
    weeks: int,
    max_per_query: int,
    project_id: Optional[str],
    location: str = "US",
    table: str = "gdelt-bq.gdeltv2.gkg_partitioned",
    max_bytes_billed: Optional[int] = None,
    dry_run: bool = False,
) -> list[Source]:
    """Use GDELT's BigQuery table to find recent article URLs.

    This avoids the public GDELT API, which is helpful when the API starts returning
    429 errors. It does need Google Cloud credentials and a billing-enabled project.

    Setup:
        pip install google-cloud-bigquery
        gcloud auth application-default login
        python scrape_ai_articles.py --weeks 1 --discover-provider bigquery --bq-project YOUR_PROJECT_ID

    A couple of reminders:
    - BigQuery has article metadata, not the full text, so the scraper still has to fetch each URL.
    - The public dataset is available, but the Google Cloud project can still be charged for queries.
    """
    try:
        from google.cloud import bigquery  # type: ignore
        from google.api_core.exceptions import GoogleAPIError  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "BigQuery discovery requires the google-cloud-bigquery package. Install it with: "
            "pip install google-cloud-bigquery"
        ) from e

    if not project_id:
        raise ValueError(
            "--discover-provider bigquery requires --bq-project YOUR_GOOGLE_CLOUD_PROJECT_ID. "
            "You also need to authenticate with: gcloud auth application-default login"
        )

    cutoff = cutoff_from_weeks(weeks)
    now = datetime.now(timezone.utc)
    start_partition = cutoff.strftime("%Y-%m-%d")
    end_partition = now.strftime("%Y-%m-%d")
    start_int = int(cutoff.strftime("%Y%m%d%H%M%S"))
    end_int = int(now.strftime("%Y%m%d%H%M%S"))
    maxrecords = max(1, min(max_per_query, 100))

    client = bigquery.Client(project=project_id, location=location)
    discovered: list[Source] = []

    # Search the metadata fields that seem most useful for discovery.
    # BigQuery does not have the full article body, so URLs still get scraped later.
    blob_sql = """
        LOWER(CONCAT(
            IFNULL(DocumentIdentifier, ''), ' ',
            IFNULL(SourceCommonName, ''), ' ',
            IFNULL(V2Themes, ''), ' ',
            IFNULL(V2Organizations, ''), ' ',
            IFNULL(V2Persons, ''), ' ',
            IFNULL(AllNames, '')
        ))
    """

    ai_regex = r"(artificial intelligence|generative ai|\bai\b|openai|chatgpt|anthropic|deepfake|superintelligence|large language model|llm)"

    for theme, queries in queries_by_theme.items():
        for q in queries:
            topic_regex = _make_topic_regex(q)
            sql = f"""
                SELECT
                  ANY_VALUE(DocumentIdentifier) AS url,
                  ANY_VALUE(SourceCommonName) AS domain,
                  MAX(DATE) AS gdelt_date,
                  ANY_VALUE(V2Themes) AS themes,
                  ANY_VALUE(V2Tone) AS tone
                FROM `{table}`
                WHERE
                  _PARTITIONTIME >= TIMESTAMP(@start_partition)
                  AND _PARTITIONTIME <= TIMESTAMP(@end_partition)
                  AND DATE >= @start_int
                  AND DATE <= @end_int
                  AND DocumentIdentifier IS NOT NULL
                  AND STARTS_WITH(DocumentIdentifier, 'http')
                  AND REGEXP_CONTAINS({blob_sql}, @ai_regex)
                  AND REGEXP_CONTAINS({blob_sql}, @topic_regex)
                GROUP BY DocumentIdentifier
                ORDER BY gdelt_date DESC
                LIMIT @maxrecords
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("start_partition", "STRING", start_partition),
                    bigquery.ScalarQueryParameter("end_partition", "STRING", end_partition),
                    bigquery.ScalarQueryParameter("start_int", "INT64", start_int),
                    bigquery.ScalarQueryParameter("end_int", "INT64", end_int),
                    bigquery.ScalarQueryParameter("ai_regex", "STRING", ai_regex),
                    bigquery.ScalarQueryParameter("topic_regex", "STRING", topic_regex),
                    bigquery.ScalarQueryParameter("maxrecords", "INT64", maxrecords),
                ],
                use_legacy_sql=False,
                dry_run=dry_run,
            )
            if max_bytes_billed and max_bytes_billed > 0:
                job_config.maximum_bytes_billed = max_bytes_billed

            try:
                query_job = client.query(sql, job_config=job_config, location=location)
                if dry_run:
                    print(
                        f"[discover:bigquery] dry run for query={q!r}: "
                        f"would process {query_job.total_bytes_processed:,} bytes",
                        file=sys.stderr,
                    )
                    continue
                rows = list(query_job.result())
            except GoogleAPIError as e:
                print(f"[discover:bigquery] failed for query={q!r}: {e}", file=sys.stderr)
                continue
            except Exception as e:
                print(f"[discover:bigquery] failed for query={q!r}: {e!r}", file=sys.stderr)
                continue

            for row in rows:
                url = str(row.url or "").strip()
                if not url:
                    continue
                discovered.append(Source(
                    url=url,
                    theme=theme,
                    note=(
                        f"Discovered by GDELT BigQuery within last {weeks} weeks. "
                        f"Query: {q}. Domain: {row.domain}. GDELT date: {row.gdelt_date}."
                    ),
                ))
            print(f"[discover:bigquery] {len(rows)} URLs for query={q!r}")

    return dedupe_sources(discovered)

def discover_recent_articles(
    session: requests.Session,
    weeks: int,
    provider: str,
    raw_queries: list[str],
    max_per_query: int,
    timeout: int,
    discovery_delay: float,
    gdelt_retries: int,
    gdelt_backoff: float,
    bq_project: Optional[str] = None,
    bq_location: str = "US",
    bq_table: str = "gdelt-bq.gdeltv2.gkg_partitioned",
    bq_max_bytes_billed: Optional[int] = None,
    bq_dry_run: bool = False,
) -> list[Source]:
    """Pick the discovery method and return a deduped list of recent sources."""
    queries_by_theme = _theme_queries_from_args(raw_queries)
    discovered: list[Source] = []
    gdelt_count = 0
    if provider in {"bigquery", "both"}:
        print(f"Discovering recent articles with GDELT BigQuery from the last {weeks} weeks...")
        bq_sources = discover_with_bigquery(
            queries_by_theme=queries_by_theme,
            weeks=weeks,
            max_per_query=max_per_query,
            project_id=bq_project,
            location=bq_location,
            table=bq_table,
            max_bytes_billed=bq_max_bytes_billed,
            dry_run=bq_dry_run,
        )
        discovered.extend(bq_sources)

    if provider in {"gdelt", "both"}:
        print(f"Discovering recent articles with GDELT from the last {weeks} weeks...")
        gdelt_sources = discover_with_gdelt(
            session,
            queries_by_theme,
            weeks,
            max_per_query,
            timeout=timeout,
            discovery_delay=discovery_delay,
            retries=gdelt_retries,
            backoff=gdelt_backoff,
        )
        gdelt_count = len(gdelt_sources)
        discovered.extend(gdelt_sources)

    # If GDELT comes back empty, make one more attempt through Google News RSS.
    use_google = provider in {"google_news", "both"} or (provider == "gdelt" and gdelt_count == 0)
    if use_google:
        if provider == "gdelt" and gdelt_count == 0:
            print("GDELT returned no usable sources; falling back to Google News RSS...")
        else:
            print(f"Discovering recent articles with Google News RSS from the last {weeks} weeks...")
        discovered.extend(discover_with_google_news_rss(session, queries_by_theme, weeks, max_per_query, timeout=timeout))
    return dedupe_sources(discovered)


def extract_pdf_text(content: bytes) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed. Run: pip install pypdf")
    reader = PdfReader(BytesIO(content))
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            text = f"\n[Page {i+1} extraction error: {e}]\n"
        if text.strip():
            parts.append(f"\n\n--- Page {i+1} ---\n{text}")
    return "\n".join(parts).strip()


def extract_html_text(html: str, url: str) -> tuple[str, str]:
    """Pull out the page title and the best article text I can get."""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Try trafilatura first; it usually does a cleaner job on article pages.
    if trafilatura is not None:
        try:
            extracted = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
                favor_recall=True,
            )
            if extracted and len(extracted.strip()) > 500:
                return title, clean_text(extracted)
        except Exception:
            pass

    # Backup plan: strip page clutter and keep the visible paragraphs/headings.
    for tag in soup(["script", "style", "noscript", "svg", "form", "nav", "footer", "header", "aside"]):
        tag.decompose()

    candidates = soup.find_all(["article", "main"])
    if not candidates:
        candidates = [soup.body] if soup.body else [soup]

    best_text = ""
    for cand in candidates:
        pieces = []
        for el in cand.find_all(["h1", "h2", "h3", "p", "li", "blockquote"]):
            t = el.get_text(" ", strip=True)
            if t:
                pieces.append(t)
        candidate_text = "\n\n".join(pieces)
        if len(candidate_text) > len(best_text):
            best_text = candidate_text

    return title, clean_text(best_text)


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_and_extract(session: requests.Session, source: Source, timeout: int = 30) -> dict:
    url = source.url
    result = {
        "url": url,
        "theme": source.theme,
        "note": source.note,
        "status": "",
        "http_status": "",
        "content_type": "",
        "title": "",
        "chars": 0,
        "text": "",
        "error": "",
    }
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        result["url"] = resp.url or url
        result["http_status"] = str(resp.status_code)
        result["content_type"] = resp.headers.get("content-type", "")[:120]
        resp.raise_for_status()

        content_type = result["content_type"].lower()
        is_pdf = "application/pdf" in content_type or urlparse(resp.url).path.lower().endswith(".pdf")
        if is_pdf:
            text = extract_pdf_text(resp.content)
            title = Path(urlparse(resp.url).path).name or "PDF document"
        else:
            if not resp.encoding:
                resp.encoding = resp.apparent_encoding
            title, text = extract_html_text(resp.text, resp.url)

        result["title"] = title
        result["text"] = text
        result["chars"] = len(text)
        if len(text) < 500:
            result["status"] = "blocked_or_empty"
            result["error"] = "Extracted text under 500 characters. Page may be paywalled, blocked, script-rendered, or not an article."
        else:
            result["status"] = "ok"
    except Exception as e:
        result["status"] = "error"
        result["error"] = repr(e)
    return result


def write_outputs(results: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    text_dir = out_dir / "texts"
    text_dir.mkdir(exist_ok=True)

    metadata_path = out_dir / "metadata.csv"
    fields = ["id", "url", "theme", "note", "status", "http_status", "content_type", "title", "chars", "file", "error"]

    with metadata_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i, r in enumerate(results, start=1):
            slug = slugify_url(r["url"])
            filename = f"{i:03d}_{slug}.txt"
            file_path = text_dir / filename
            header = (
                f"URL: {r['url']}\n"
                f"Theme: {r['theme']}\n"
                f"Title: {r['title']}\n"
                f"Status: {r['status']}\n"
                f"HTTP status: {r['http_status']}\n"
                f"Content type: {r['content_type']}\n"
                f"Note: {r['note']}\n"
                f"Error: {r['error']}\n"
                f"\n{'='*80}\n\n"
            )
            file_path.write_text(header + (r.get("text") or ""), encoding="utf-8")
            writer.writerow({
                "id": f"{i:03d}",
                "url": r["url"],
                "theme": r["theme"],
                "note": r["note"],
                "status": r["status"],
                "http_status": r["http_status"],
                "content_type": r["content_type"],
                "title": r["title"],
                "chars": r["chars"],
                "file": str(file_path),
                "error": r["error"],
            })


def export_sources_csv(sources: list[Source], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["url", "theme", "note"])
        writer.writeheader()
        for s in sources:
            writer.writerow({"url": s.url, "theme": s.theme, "note": s.note})


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape AI article/report text for a research dataset.")
    parser.add_argument("--urls-file", type=Path, help="Optional .txt or .csv file of URLs. CSV may include url,theme,note columns.")
    parser.add_argument("--out", type=Path, default=Path("ai_article_texts"), help="Output directory.")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds to wait between article scrape requests.")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds.")
    parser.add_argument("--max", type=int, default=0, help="Only process first N sources after combining/deduping; 0 means all.")
    parser.add_argument("--no-builtins", action="store_true", help="Use only --urls-file and/or discovered sources, not built-in sources.")
    parser.add_argument("--export-sources", action="store_true", help="Export the final deduped source list as sources_used.csv before scraping.")

    # Options for finding newer articles.
    parser.add_argument("--weeks", type=int, default=0, help="Discover recent articles from the last N weeks before scraping. 0 disables discovery.")
    parser.add_argument("--query", action="append", default=[], help="Custom discovery query. Can be used multiple times. If omitted, default category queries are used.")
    parser.add_argument("--discover-provider", choices=["gdelt", "google_news", "bigquery", "both"], default="gdelt", help="Provider for recent article discovery. BigQuery avoids GDELT API 429s but requires Google Cloud auth/project.")
    parser.add_argument("--max-discovered-per-query", type=int, default=10, help="Maximum recent articles to discover per query.")
    parser.add_argument("--discovery-delay", type=float, default=6.0, help="Seconds to wait between discovery API queries. Increase if you see GDELT 429 errors.")
    parser.add_argument("--gdelt-retries", type=int, default=5, help="Number of retries for each GDELT query when rate-limited or given non-JSON responses.")
    parser.add_argument("--gdelt-backoff", type=float, default=6.0, help="Initial exponential-backoff seconds for GDELT retries.")
    parser.add_argument("--export-discovered", action="store_true", help="Export discovered sources as discovered_sources.csv even if --export-sources is not set.")
    parser.add_argument("--discover-only", action="store_true", help="Discover recent sources and export CSV files, but do not scrape article text.")

    # Options used only for BigQuery discovery.
    parser.add_argument("--bq-project", help="Google Cloud project ID to bill BigQuery discovery queries to. Required for --discover-provider bigquery.")
    parser.add_argument("--bq-location", default="US", help="BigQuery location for the GDELT public dataset; default: US.")
    parser.add_argument("--bq-table", default="gdelt-bq.gdeltv2.gkg_partitioned", help="GDELT BigQuery table to query; default: gdelt-bq.gdeltv2.gkg_partitioned.")
    parser.add_argument("--bq-max-bytes-billed", type=int, default=0, help="Optional BigQuery safety cap in bytes, e.g., 5000000000 for 5GB. 0 means no explicit cap.")
    parser.add_argument("--bq-dry-run", action="store_true", help="Estimate BigQuery bytes processed and export no discovered URLs.")
    args = parser.parse_args(argv)

    if args.weeks < 0:
        print("--weeks must be 0 or a positive integer.", file=sys.stderr)
        return 2
    if args.max_discovered_per_query < 1:
        print("--max-discovered-per-query must be at least 1.", file=sys.stderr)
        return 2
    if args.discovery_delay < 0:
        print("--discovery-delay must be 0 or positive.", file=sys.stderr)
        return 2
    if args.gdelt_retries < 1:
        print("--gdelt-retries must be at least 1.", file=sys.stderr)
        return 2
    if args.gdelt_backoff < 0:
        print("--gdelt-backoff must be 0 or positive.", file=sys.stderr)
        return 2
    if args.discover_provider == "bigquery" and args.weeks > 0 and not args.bq_project:
        print("--discover-provider bigquery requires --bq-project YOUR_GOOGLE_CLOUD_PROJECT_ID.", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    session = make_session()

    sources: list[Source] = []
    if not args.no_builtins:
        sources.extend(BUILTIN_SOURCES)
    if args.urls_file:
        sources.extend(read_sources_file(args.urls_file))

    discovered: list[Source] = []
    if args.weeks > 0:
        discovered = discover_recent_articles(
            session=session,
            weeks=args.weeks,
            provider=args.discover_provider,
            raw_queries=args.query,
            max_per_query=args.max_discovered_per_query,
            timeout=args.timeout,
            discovery_delay=args.discovery_delay,
            gdelt_retries=args.gdelt_retries,
            gdelt_backoff=args.gdelt_backoff,
            bq_project=args.bq_project,
            bq_location=args.bq_location,
            bq_table=args.bq_table,
            bq_max_bytes_billed=(args.bq_max_bytes_billed or None),
            bq_dry_run=args.bq_dry_run,
        )
        print(f"Discovered {len(discovered)} unique recent source URLs.")
        export_sources_csv(discovered, args.out / "discovered_sources.csv")
        sources.extend(discovered)

    sources = dedupe_sources(sources)
    if args.max and args.max > 0:
        sources = sources[: args.max]

    if args.export_sources or args.weeks > 0:
        export_sources_csv(sources, args.out / "sources_used.csv")

    if args.discover_only:
        print(f"Discovery complete. Discovered sources saved as: {args.out / 'discovered_sources.csv'}")
        print(f"Final source list saved as: {args.out / 'sources_used.csv'}")
        return 0

    if not sources:
        print("No sources to process. Use built-ins, pass --urls-file, or use --weeks for discovery.", file=sys.stderr)
        return 2

    print(f"Processing {len(sources)} sources...")
    results: list[dict] = []

    try:
        from tqdm import tqdm  # type: ignore
        iterator = tqdm(sources)
    except Exception:
        iterator = sources

    for idx, source in enumerate(iterator, start=1):
        print(f"[{idx}/{len(sources)}] {source.url}")
        result = fetch_and_extract(session, source, timeout=args.timeout)
        print(f"  -> {result['status']} | chars={result['chars']} | http={result['http_status']}")
        results.append(result)
        if idx < len(sources) and args.delay > 0:
            time.sleep(args.delay)

    write_outputs(results, args.out)
    print(f"\nDone. Text files saved in: {args.out / 'texts'}")
    print(f"Metadata CSV saved as: {args.out / 'metadata.csv'}")
    if args.export_sources or args.weeks > 0:
        print(f"Source list saved as: {args.out / 'sources_used.csv'}")
    if args.weeks > 0:
        print(f"Discovered source list saved as: {args.out / 'discovered_sources.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
fetch_official_aims_scope.py - fetch official journal aims/scope text.

This script prioritizes publisher-provided pages such as:
  - Aims & Scope
  - About the journal
  - What we publish
  - Journal overview

It writes successful records to data/aims_scope.json and failures to
data/aims_scope_failures.json. It is intentionally conservative: existing
records with usable aims_scope text are skipped unless --overwrite is passed.

Usage:
    python scripts/fetch_official_aims_scope.py --limit 20
    python scripts/fetch_official_aims_scope.py --publisher "Wiley" --limit 50
    python scripts/fetch_official_aims_scope.py --issn-l 1474-7472 --overwrite
"""

import argparse
import html
import json
import os
import re
import time
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from tqdm import tqdm


DATA_DIR = Path(__file__).parent.parent / "data"
JOURNALS_PATH = DATA_DIR / "journals_ssci.json"
AIMS_PATH = DATA_DIR / "aims_scope.json"
FAILURES_PATH = DATA_DIR / "aims_scope_failures.json"

_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        if line.startswith("OPENALEX_MAILTO="):
            os.environ.setdefault("OPENALEX_MAILTO", line.split("=", 1)[1].strip())

MAILTO = os.environ.get("OPENALEX_MAILTO", "")
CONTACT = f" (mailto:{MAILTO})" if MAILTO else ""
USER_AGENT = f"JournalFinder/1.0 official-scope-fetcher{CONTACT}"

SCOPE_TERMS = [
    "aims and scope",
    "aims & scope",
    "aim and scope",
    "about the journal",
    "what we publish",
    "journal overview",
    "scope",
    "overview",
]

POSITIVE_TEXT_SIGNALS = [
    "publishes",
    "journal publishes",
    "welcomes",
    "covers",
    "aims",
    "scope",
    "peer-reviewed",
    "manuscripts",
    "submissions",
    "articles",
    "research",
]

NOISE_SIGNALS = [
    "cookie",
    "privacy policy",
    "terms and conditions",
    "advertisement",
    "subscribe",
    "sign in",
    "institutional login",
    "shopping cart",
    "accept all",
]

STOP_HEADINGS = [
    "journal navigation",
    "editorial board",
    "abstracting and indexing",
    "submit",
    "submission",
    "author guidelines",
    "instructions for authors",
    "open access",
    "fees and funding",
    "metrics",
    "latest articles",
    "most read",
    "contact",
]

NOISE_LINES = {
    "editorial board",
    "editorial policies",
    "ethics and disclosures",
    "rights and permissions",
    "contact the journal",
    "articles",
    "collections",
    "volumes and issues",
    "online first articles",
    "sign up for alerts",
    "for authors",
    "pre submission checklist",
    "submission guidelines",
    "how to publish with us",
    "fees and funding",
    "calls for papers",
    "language editing",
    "submit your manuscript",
    "about this journal",
    "journal navigation",
}


class TextAndLinkParser(HTMLParser):
    """Small HTML parser that extracts visible text and anchor links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_chunks = []
        self.links = []
        self._skip_depth = 0
        self._current_href = None
        self._current_link_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
            return
        if tag == "a":
            self._current_href = attrs.get("href")
            self._current_link_text = []
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "br"}:
            self.text_chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "canvas"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "a" and self._current_href:
            text = clean_space(" ".join(self._current_link_text))
            self.links.append((text, self._current_href))
            self._current_href = None
            self._current_link_text = []
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4"}:
            self.text_chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        self.text_chunks.append(text)
        if self._current_href:
            self._current_link_text.append(text)


def clean_space(text):
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s+\n", "\n", text)
    return text.strip()


def normalize_for_match(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return clean_space(text)


def word_count(text):
    return len(re.findall(r"[A-Za-z][A-Za-z'-]+", text or ""))


def load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36 "
            f"JournalFinder/1.0{CONTACT}"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    })
    return session


def fetch_html(session, url, timeout=25):
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200:
            return None, f"http_{resp.status_code}"
        content_type = resp.headers.get("content-type", "").lower()
        if "html" not in content_type and "text/" not in content_type:
            return None, f"not_html:{content_type[:40]}"
        return resp.text, None
    except requests.RequestException as exc:
        return None, exc.__class__.__name__


def parse_html(html_text):
    parser = TextAndLinkParser()
    parser.feed(html_text)
    raw_text = "\n".join(parser.text_chunks)
    lines = [clean_space(line) for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    return lines, parser.links


def has_existing_scope(record, min_words):
    if not isinstance(record, dict):
        return False
    text = record.get("aims_scope") or ""
    return word_count(text) >= min_words


def publisher_key(publisher):
    p = normalize_for_match(publisher)
    if "wiley" in p:
        return "wiley"
    if "springer" in p or "palgrave" in p:
        return "springer"
    if "sage" in p:
        return "sage"
    if "taylor" in p or "routledge" in p:
        return "taylor_francis"
    if "elsevier" in p:
        return "elsevier"
    if "cambridge" in p:
        return "cambridge"
    if "oxford" in p:
        return "oxford"
    return "generic"


def append_path(base_url, suffix):
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    return f"{root}{path}{suffix}"


def candidate_urls(journal):
    """Generate likely official scope URLs from homepage and publisher patterns."""
    homepage = journal.get("homepage_url")
    publisher = journal.get("publisher")
    key = publisher_key(publisher)
    candidates = []

    if homepage:
        candidates.append(homepage)

    if homepage and key == "springer":
        parsed = urlparse(homepage)
        match = re.search(r"/journal/([^/?#]+)", parsed.path)
        if match:
            root = f"{parsed.scheme}://{parsed.netloc}"
            candidates.insert(0, f"{root}/journal/{match.group(1)}/aims-and-scope")

    if homepage and key == "sage":
        parsed = urlparse(homepage)
        code = parsed.path.rstrip("/").split("/")[-1]
        if code:
            candidates.insert(0, f"{parsed.scheme}://{parsed.netloc}/aims-scope/{code}")

    if homepage and key == "taylor_francis":
        candidates.extend([
            append_path(homepage, "/about-this-journal"),
            append_path(homepage, "/journal-information"),
        ])

    if homepage and key == "elsevier":
        candidates.extend([
            append_path(homepage, "/about/aims-and-scope"),
            append_path(homepage, "/about"),
        ])

    if homepage and key in {"wiley", "cambridge", "oxford"}:
        candidates.extend([
            append_path(homepage, "/aims-and-scope"),
            append_path(homepage, "/about"),
        ])

    clean = []
    seen = set()
    for url in candidates:
        if not url:
            continue
        url = url.replace(" ", "").rstrip("/")
        if url and url not in seen:
            clean.append(url)
            seen.add(url)
    return clean


def discover_scope_links(base_url, links, max_links=8):
    """Find likely Aims/Scope/About links from a journal homepage."""
    scored = []
    for link_text, href in links:
        text = normalize_for_match(link_text)
        href_norm = normalize_for_match(href)
        haystack = f"{text} {href_norm}"
        score = 0
        for term in SCOPE_TERMS:
            if normalize_for_match(term) in haystack:
                score += 3
        if "aim" in haystack and "scope" in haystack:
            score += 5
        if "about" in haystack and "journal" in haystack:
            score += 3
        if not score:
            continue
        if any(bad in haystack for bad in ["advert", "login", "privacy", "cookie"]):
            score -= 3
        if score <= 0:
            continue
        scored.append((score, urljoin(base_url, href)))

    scored.sort(key=lambda item: -item[0])
    urls = []
    seen = set()
    for _, url in scored:
        if url not in seen:
            urls.append(url)
            seen.add(url)
        if len(urls) >= max_links:
            break
    return urls


def extract_scope_text(lines, page_url, journal=None):
    """Extract the most likely official scope passage from visible page text."""
    if not lines:
        return ""

    lower_lines = [line.lower() for line in lines]
    start_indices = []
    for i, line in enumerate(lower_lines):
        if any(term in line for term in SCOPE_TERMS):
            start_indices.append(i)

    blocks = []
    for start in start_indices[:8]:
        block = []
        for line in lines[start:start + 90]:
            norm = normalize_for_match(line)
            if norm in NOISE_LINES:
                continue
            if block and any(stop in norm for stop in STOP_HEADINGS) and word_count(" ".join(block)) >= 80:
                break
            if len(line) > 2:
                block.append(line)
        text = normalize_text("\n".join(block))
        if text:
            blocks.append(text)

    url_hint = normalize_for_match(page_url)
    if not blocks and ("aim" in url_hint or "scope" in url_hint or "about" in url_hint):
        fallback_lines = [
            line for line in lines[:120]
            if normalize_for_match(line) not in NOISE_LINES
        ]
        blocks.append(normalize_text("\n".join(fallback_lines)))

    if not blocks:
        return ""

    def block_score(text):
        norm = normalize_for_match(text)
        score = min(word_count(text), 600) / 60
        score += sum(1.0 for signal in POSITIVE_TEXT_SIGNALS if signal in norm)
        score -= sum(1.5 for noise in NOISE_SIGNALS if noise in norm)
        return score

    best = max(blocks, key=block_score)
    return trim_scope_text(best, journal=journal)


def strip_leading_boilerplate(text, journal=None):
    """Remove publisher header text that often precedes the real scope paragraph."""
    cleaned = normalize_text(text)
    lowered = cleaned.lower()
    journal_name = (journal or {}).get("name") if isinstance(journal, dict) else None

    starts = []
    if journal_name:
        name = re.escape(journal_name)
        for pattern in [
            rf"{name}\s+is\b",
            rf"{name}\s+publishes\b",
            rf"{name}\s+aims\s+to\b",
        ]:
            match = re.search(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                starts.append(match.start())

    for phrase in [
        "this journal aims",
        "this journal is",
        "the journal aims",
        "the journal publishes",
        "the journal provides",
        "the journal is",
        "journal publishes",
        "publishes original",
        "publishes high-quality",
        "welcomes submissions",
        "is a peer-reviewed",
        "is an international",
        "is dedicated to",
        "focuses on",
    ]:
        idx = lowered.find(phrase)
        if idx >= 0:
            starts.append(idx)

    if not starts:
        return cleaned

    start = min(starts)
    prefix = normalize_for_match(cleaned[:start])
    noisy_prefix = any(
        signal in prefix
        for signal in [
            "skip to main content", "log in", "find a journal", "publish with us",
            "saved research", "cart home", "publishing model", "journal menu",
            "view saved research", "open access funding", "select institution",
            "journal updates",
        ]
    )
    if start > 0 and noisy_prefix:
        return cleaned[start:].lstrip(" |:-\n")
    return cleaned


def trim_scope_text(text, journal=None, max_words=700):
    """Keep extracted text compact enough for embeddings and review."""
    lines = []
    seen = set()
    stripped = strip_leading_boilerplate(text, journal)
    for line in normalize_text(stripped).splitlines():
        norm = normalize_for_match(line)
        if not norm or norm in NOISE_LINES:
            continue
        if norm in seen and word_count(line) < 8:
            continue
        seen.add(norm)
        lines.append(line)

    cleaned = normalize_text("\n".join(lines))
    words = re.findall(r"\S+", cleaned)
    if len(words) <= max_words:
        return cleaned
    return normalize_text(" ".join(words[:max_words]))


def quality_check(text, journal, min_words):
    """Return confidence and rejection reason for extracted text."""
    wc = word_count(text)
    if wc < min_words:
        return None, f"too_short:{wc}"

    norm = normalize_for_match(text)
    signal_count = sum(1 for signal in POSITIVE_TEXT_SIGNALS if signal in norm)
    noise_count = sum(1 for noise in NOISE_SIGNALS if noise in norm)
    if signal_count < 2:
        return None, "weak_scope_signals"
    if noise_count >= 4:
        return None, "too_much_page_noise"

    name = normalize_for_match(journal.get("name"))
    name_words = [w for w in name.split() if len(w) > 3]
    name_hits = sum(1 for w in name_words[:6] if w in norm)

    if wc >= 120 and signal_count >= 4 and name_hits >= 1:
        return "high", None
    if wc >= 100 and signal_count >= 3:
        return "medium", None
    return "low", None


def fetch_scope_for_journal(session, journal, min_words):
    """Try generated and discovered URLs until official scope text is found."""
    tried = []
    queue = candidate_urls(journal)
    seen = set(queue)

    for idx, url in enumerate(list(queue)):
        html_text, error = fetch_html(session, url)
        tried.append({"url": url, "error": error})
        if error:
            continue

        lines, links = parse_html(html_text)
        if idx == 0:
            for discovered in discover_scope_links(url, links):
                if discovered not in seen:
                    queue.append(discovered)
                    seen.add(discovered)

        text = extract_scope_text(lines, url, journal=journal)
        confidence, reason = quality_check(text, journal, min_words)
        if confidence:
            return {
                "aims_scope": text,
                "source": f"publisher_web:{publisher_key(journal.get('publisher'))}",
                "source_url": url,
                "confidence": confidence,
                "last_checked": date.today().isoformat(),
                "tried_urls": [item["url"] for item in tried],
            }, None
        tried[-1]["error"] = reason or "no_scope_text"

        # Process discovered links after the homepage without mutating the active iterator.
        if idx + 1 == len(queue):
            break

    # Continue over links appended during homepage discovery.
    for url in queue[len(tried):]:
        html_text, error = fetch_html(session, url)
        tried.append({"url": url, "error": error})
        if error:
            continue
        lines, _ = parse_html(html_text)
        text = extract_scope_text(lines, url, journal=journal)
        confidence, reason = quality_check(text, journal, min_words)
        if confidence:
            return {
                "aims_scope": text,
                "source": f"publisher_web:{publisher_key(journal.get('publisher'))}",
                "source_url": url,
                "confidence": confidence,
                "last_checked": date.today().isoformat(),
                "tried_urls": [item["url"] for item in tried],
            }, None
        tried[-1]["error"] = reason or "no_scope_text"

    return None, {
        "issn_l": journal.get("issn_l"),
        "name": journal.get("name"),
        "publisher": journal.get("publisher"),
        "homepage_url": journal.get("homepage_url"),
        "last_checked": date.today().isoformat(),
        "tried": tried,
    }


def should_process(journal, aims_data, args):
    if args.issn_l and journal.get("issn_l") != args.issn_l:
        return False
    if getattr(args, "scope", None):
        if journal.get("_meta", {}).get("source_scope") != args.scope:
            return False
    if args.publisher:
        pub = normalize_for_match(journal.get("publisher"))
        if normalize_for_match(args.publisher) not in pub:
            return False
    if not journal.get("homepage_url"):
        return False
    if args.overwrite:
        return True
    return not has_existing_scope(aims_data.get(journal.get("issn_l")), args.min_words)


def merge_record(existing, journal, fetched):
    record = existing.copy() if isinstance(existing, dict) else {}
    record.setdefault("name", journal.get("name"))
    record.setdefault("abstract_summary", None)
    record.setdefault("recent_titles", record.get("recent_titles"))
    record.update(fetched)
    return record


def coverage_report(journals, aims_data, min_words):
    total = len(journals)
    valid = sum(
        1 for journal in journals
        if has_existing_scope(aims_data.get(journal.get("issn_l")), min_words)
    )
    return valid, total, valid / total * 100 if total else 0


def merge_progress(path, touched_keys, updated_records):
    """Reload on-disk JSON and only overwrite keys touched in this run."""
    current = load_json(path, {})
    if not isinstance(current, dict):
        current = {}
    for key in touched_keys:
        if key in updated_records:
            current[key] = updated_records[key]
        else:
            current.pop(key, None)
    save_json(path, current)
    return current


def main():
    parser = argparse.ArgumentParser(description="Fetch official journal aims/scope pages")
    parser.add_argument("--limit", type=int, default=None, help="Maximum journals to process")
    parser.add_argument("--publisher", type=str, default=None, help="Only process a publisher substring")
    parser.add_argument("--issn-l", type=str, default=None, help="Only process one ISSN-L")
    parser.add_argument("--scope", type=str, default=None,
                        help="Only process journals with this _meta.source_scope (e.g. scie_env_health)")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing usable aims_scope text")
    parser.add_argument("--min-words", type=int, default=80, help="Minimum extracted scope length")
    parser.add_argument("--sleep", type=float, default=0.8, help="Delay between journals")
    parser.add_argument("--save-every", type=int, default=10, help="Save progress every N attempts")
    parser.add_argument("--dry-run", action="store_true", help="Show target journals without fetching")
    args = parser.parse_args()

    journals = load_json(JOURNALS_PATH, [])
    aims_data = load_json(AIMS_PATH, {})
    failures = load_json(FAILURES_PATH, {})
    if not isinstance(aims_data, dict):
        aims_data = {}
    if not isinstance(failures, dict):
        failures = {}

    before_valid, total, before_pct = coverage_report(journals, aims_data, args.min_words)

    targets = [j for j in journals if should_process(j, aims_data, args)]
    if args.limit is not None:
        targets = targets[:args.limit]

    print(f"Official aims/scope coverage before: {before_valid}/{total} ({before_pct:.1f}%)")
    print(f"Targets to process: {len(targets)}")
    if args.dry_run:
        for journal in targets[:50]:
            print(f"{journal.get('issn_l')} | {journal.get('publisher')} | {journal.get('name')} | {journal.get('homepage_url')}")
        return

    session = create_session()
    success = 0
    failed = 0
    touched_aims = set()
    touched_failures = set()

    for idx, journal in enumerate(tqdm(targets, desc="Fetching official scope"), start=1):
        issn_l = journal.get("issn_l")
        fetched, failure = fetch_scope_for_journal(session, journal, args.min_words)
        if fetched:
            aims_data[issn_l] = merge_record(aims_data.get(issn_l), journal, fetched)
            failures.pop(issn_l, None)
            touched_aims.add(issn_l)
            touched_failures.add(issn_l)
            success += 1
        else:
            failures[issn_l] = failure
            touched_failures.add(issn_l)
            failed += 1

        if idx % args.save_every == 0:
            aims_data = merge_progress(AIMS_PATH, touched_aims, aims_data)
            failures = merge_progress(FAILURES_PATH, touched_failures, failures)

        if args.sleep:
            time.sleep(args.sleep)

    aims_data = merge_progress(AIMS_PATH, touched_aims, aims_data)
    failures = merge_progress(FAILURES_PATH, touched_failures, failures)

    after_valid, _, after_pct = coverage_report(journals, aims_data, args.min_words)
    print("\nDone")
    print(f"  Success: {success}")
    print(f"  Failed: {failed}")
    print(f"  Official aims/scope coverage after: {after_valid}/{total} ({after_pct:.1f}%)")
    print(f"  Added usable records: {after_valid - before_valid}")
    print(f"  Data: {AIMS_PATH}")
    print(f"  Failures: {FAILURES_PATH}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""
Harvest official journal Aims & Scope text and APC values for CODEX_TASK.md.

Only writes data/codex_harvest.json. It is deliberately incremental so it can
be stopped and resumed while skipping already harvested ISSN-L records.
"""

import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WORKLIST_PATH = DATA_DIR / "codex_worklist.json"
OUTPUT_PATH = DATA_DIR / "codex_harvest.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SCOPE_HEADINGS = (
    "aims and scope",
    "aim and scope",
    "scope",
    "journal description",
    "about this journal",
    "about the journal",
    "overview",
)

STOP_HEADINGS = (
    "journal metrics",
    "metrics",
    "impact factor",
    "cite score",
    "citescore",
    "downloads",
    "editorial board",
    "instructions for authors",
    "submission guidelines",
    "submit your article",
    "abstracting and indexing",
    "indexing",
    "fees and funding",
    "publication charges",
    "article publishing charge",
    "open access",
    "advertise",
    "contact",
)

NOISE_RE = re.compile(
    r"(impact factor|citescore|downloads|submission to acceptance|acceptance to publication|"
    r"journal metrics|altmetric|editorial board)",
    re.I,
)

APC_CONTEXT_RE = re.compile(
    r"(article publishing charge|article publication charge|apc|publication fee|"
    r"publishing fee|open access fee|publication charge|author publication charge)",
    re.I,
)

MONEY_RE = re.compile(
    r"(?:(?:US|U\.S\.)?\s?\$|USD\s?\$?|US dollars?\s?)\s?([1-9]\d{2,4}(?:,\d{3})?)",
    re.I,
)


class Harvester:
    def __init__(self, delay=1.1, timeout=20, respect_robots=True):
        self.delay = delay
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.last_request_at = 0
        self.robots = {}

    def wait(self):
        elapsed = time.time() - self.last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def allowed(self, url):
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        base = f"{parsed.scheme}://{parsed.netloc}"
        rp = self.robots.get(base)
        if rp is None:
            rp = RobotFileParser()
            rp.set_url(urljoin(base, "/robots.txt"))
            try:
                self.wait()
                rp.read()
                self.last_request_at = time.time()
            except Exception:
                return True
            self.robots[base] = rp
        return rp.can_fetch(UA, url)

    def fetch(self, url):
        if not url or not self.allowed(url):
            return None
        try:
            self.wait()
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            self.last_request_at = time.time()
        except requests.RequestException:
            return None
        if resp.status_code >= 400 or "text/html" not in resp.headers.get("content-type", ""):
            return None
        return resp.url, resp.text


def load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def slug_from_name(name):
    text = re.sub(r"&", " and ", name.lower())
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def candidate_urls(item):
    homepage = item.get("homepage_url") or ""
    publisher = (item.get("publisher") or "").lower()
    name = item.get("name") or ""
    urls = []

    def add(url):
        if url and url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)

    add(homepage)
    parsed = urlparse(homepage)
    path = parsed.path.strip("/")

    if "elsevier" in publisher or "journals.elsevier.com" in homepage:
        slug = path.split("/")[0] if parsed.netloc.endswith("journals.elsevier.com") and path else slug_from_name(name)
        add(f"https://www.sciencedirect.com/journal/{slug}/about/aims-and-scope")
        add(f"https://www.sciencedirect.com/journal/{slug}/publish/open-access")
        add(f"https://www.journals.elsevier.com/{slug}/aims-and-scope")
        add(f"https://www.journals.elsevier.com/{slug}/publish/open-access")

    if "taylor" in publisher or "routledge" in publisher or "tandfonline.com" in homepage:
        code = None
        m = re.search(r"/toc/([^/]+)/", homepage)
        if m:
            code = m.group(1)
        m = re.search(r"journalCode=([^&]+)", homepage)
        if m:
            code = m.group(1)
        if code:
            add(f"https://www.tandfonline.com/action/journalInformation?journalCode={code}")
            add(f"https://www.tandfonline.com/action/authorSubmission?journalCode={code}&show=instructions")

    if "springer" in publisher or "biomed central" in publisher or "springer.com" in homepage or "link.springer.com" in homepage:
        m = re.search(r"/journal/([^/?#]+)", homepage)
        if m:
            code = m.group(1)
            add(f"https://link.springer.com/journal/{code}")
            add(f"https://www.springer.com/journal/{code}")
            add(f"https://www.springer.com/journal/{code}/how-to-publish-with-us")

    if "wiley" in publisher or "onlinelibrary.wiley.com" in homepage:
        add(homepage.rstrip("/") + "/aims-and-scope")
        add(homepage.rstrip("/") + "/author-guidelines")

    if "sage" in publisher or "journals.sagepub.com" in homepage:
        m = re.search(r"/home/([^/?#]+)", homepage)
        if not m:
            m = re.search(r"https?://([a-z0-9]+)\.sagepub\.com", homepage)
        if m:
            code = m.group(1)
            add(f"https://journals.sagepub.com/home/{code}")
            add(f"https://journals.sagepub.com/description/{code}")
            add(f"https://journals.sagepub.com/author-instructions/{code}")

    if "oup" in publisher or "oxford" in publisher or "academic.oup.com" in homepage:
        if "oxfordjournals.org" in homepage:
            slug = urlparse(homepage).netloc.split(".")[0]
            add(f"https://academic.oup.com/{slug}")
        add(homepage.rstrip("/") + "/pages/About")
        add(homepage.rstrip("/") + "/pages/General_Instructions")

    if "frontiers" in publisher or "frontiersin.org" in homepage:
        path = urlparse(homepage).path.strip("/")
        if path and not path.startswith("journals/"):
            add(f"https://www.frontiersin.org/journals/{path}")
        add(homepage.rstrip("/") + "/about")

    if "mdpi" in publisher or "mdpi.com" in homepage:
        add(homepage.rstrip("/") + "/about")
        add(homepage.rstrip("/") + "/apc")

    if "bmj" in publisher or "bmj" in homepage:
        add(homepage.rstrip("/") + "/pages/about")
        add(homepage.rstrip("/") + "/pages/authors")

    if "liebert" in publisher or "liebertpub.com" in homepage:
        add(homepage.rstrip("/") + "/aims-and-scope")
        add(homepage.rstrip("/") + "/for-authors")

    if "plos" in publisher or "public library of science" in publisher:
        slug = slug_from_name(name).replace("plos-", "")
        add(f"https://journals.plos.org/{slug}/s/journal-information")
        add(f"https://journals.plos.org/{slug}/s/publication-fees")

    if not homepage and ("american chemical society" in publisher or "acs" in publisher):
        slug = slug_from_name(name)
        add(f"https://pubs.acs.org/journal/{slug}")

    return urls


def clean_text(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"^(Aims and Scope|Aim and Scope|Scope|Overview|About this journal)\s*", "", text, flags=re.I)
    return text.strip(" -:\n\t")


def node_text(node):
    return clean_text(node.get_text(" ", strip=True))


def heading_key(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def extract_scope_from_html(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "form", "nav", "footer", "header"]):
        tag.decompose()

    headings = soup.find_all(re.compile("^h[1-6]$"))
    best = ""
    for h in headings:
        htext = heading_key(h.get_text(" ", strip=True))
        if not any(key in htext for key in SCOPE_HEADINGS):
            continue

        parts = []
        for sib in h.find_all_next():
            if sib is h:
                continue
            if re.match("^h[1-6]$", sib.name or ""):
                next_heading = heading_key(sib.get_text(" ", strip=True))
                if any(stop in next_heading for stop in STOP_HEADINGS):
                    break
                if parts and any(key in next_heading for key in SCOPE_HEADINGS):
                    continue
                if parts:
                    break
            if sib.name in ("p", "li"):
                text = node_text(sib)
                if not text or len(text) < 25 or NOISE_RE.search(text):
                    continue
                parts.append(text)
            if len(" ".join(parts)) > 3500:
                break
        candidate = clean_text(" ".join(parts))
        if len(candidate) > len(best):
            best = candidate

    if len(best) >= 90:
        return best[:6000]

    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        text = clean_text(meta["content"])
        if len(text) >= 90 and not NOISE_RE.search(text):
            return text

    paragraphs = []
    for p in soup.find_all("p"):
        text = node_text(p)
        if len(text) >= 80 and not NOISE_RE.search(text):
            paragraphs.append(text)
    fallback = clean_text(" ".join(paragraphs[:8]))
    return fallback[:6000] if len(fallback) >= 180 else ""


def extract_apc_from_html(html):
    soup = BeautifulSoup(html, "lxml")
    text = clean_text(soup.get_text(" ", strip=True))
    candidates = []
    for m in APC_CONTEXT_RE.finditer(text):
        window = text[max(0, m.start() - 220): m.end() + 320]
        for money in MONEY_RE.finditer(window):
            value = int(money.group(1).replace(",", ""))
            if 100 <= value <= 15000:
                candidates.append(value)
    if candidates:
        return candidates[0]
    return None


def discover_relevant_links(base_url, html):
    soup = BeautifulSoup(html, "lxml")
    links = []
    patterns = ("aim", "scope", "about", "overview", "open access", "publication charge", "author guideline", "fee")
    for a in soup.find_all("a", href=True):
        label = clean_text(a.get_text(" ", strip=True)).lower()
        href = a["href"]
        joined = urljoin(base_url, href)
        if any(pat in label or pat.replace(" ", "-") in href.lower() for pat in patterns):
            if joined.startswith(("http://", "https://")) and joined not in links:
                links.append(joined)
        if len(links) >= 8:
            break
    return links


def confidence_for(scope, source_url, apc):
    score = 0
    if len(scope or "") >= 400:
        score += 2
    elif len(scope or "") >= 180:
        score += 1
    if source_url and any(x in source_url.lower() for x in ("aim", "scope", "about", "description", "journalinformation")):
        score += 1
    if apc:
        score += 1
    if score >= 3:
        return "high"
    if score >= 1:
        return "medium"
    return "low"


def harvest_one(item, harvester, max_pages=6):
    urls = candidate_urls(item)
    best_scope = ""
    best_scope_url = ""
    apc = None
    visited = set()

    for url in list(urls):
        if len(visited) >= max_pages:
            break
        if url in visited:
            continue
        visited.add(url)
        fetched = harvester.fetch(url)
        if not fetched:
            continue
        final_url, html = fetched
        scope = extract_scope_from_html(html) if item.get("need_scope") else ""
        if len(scope) > len(best_scope):
            best_scope = scope
            best_scope_url = final_url
        if item.get("need_apc") and apc is None:
            apc = extract_apc_from_html(html)
        if item.get("need_scope") and not best_scope:
            for link in discover_relevant_links(final_url, html):
                if link not in urls:
                    urls.append(link)
        if (not item.get("need_scope") or len(best_scope) >= 300) and (not item.get("need_apc") or apc):
            break

    if not best_scope and apc is None:
        return None

    record = {
        "aims_scope": best_scope or "",
        "source_url": best_scope_url or (next(iter(visited), "") if visited else ""),
        "confidence": confidence_for(best_scope, best_scope_url, apc),
        "last_checked": date.today().isoformat(),
    }
    if apc is not None:
        record["apc_usd"] = apc
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-scope", default="scie_env_health")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--save-every", type=int, default=50)
    parser.add_argument("--delay", type=float, default=1.1)
    parser.add_argument("--no-robots", action="store_true")
    args = parser.parse_args()

    worklist = load_json(WORKLIST_PATH, [])
    harvest = load_json(OUTPUT_PATH, {})
    targets = [x for x in worklist if x.get("source_scope") == args.source_scope][: args.limit]
    harvester = Harvester(delay=args.delay, respect_robots=not args.no_robots)

    processed = 0
    added = 0
    skipped = 0
    for idx, item in enumerate(targets, 1):
        issn = item.get("issn_l")
        if not issn or issn in harvest:
            skipped += 1
            continue
        print(f"[{idx}/{len(targets)}] {issn} {item.get('name')} ({item.get('publisher')})", flush=True)
        record = harvest_one(item, harvester)
        processed += 1
        if record:
            harvest[issn] = record
            added += 1
            print(
                f"  ok scope={len(record.get('aims_scope',''))} apc={record.get('apc_usd')} "
                f"confidence={record.get('confidence')}",
                flush=True,
            )
        else:
            print("  no usable official data", flush=True)
        if processed % args.save_every == 0:
            save_json(OUTPUT_PATH, harvest)
            print(f"  saved {len(harvest)} records", flush=True)

    save_json(OUTPUT_PATH, harvest)
    print(
        json.dumps(
            {
                "targets": len(targets),
                "processed": processed,
                "added": added,
                "skipped_existing": skipped,
                "total_harvest": len(harvest),
                "output": str(OUTPUT_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
query_db.py - 期刊数据库查询脚本

从本地 JSON 数据库中筛选和排序期刊，返回 top N 候选。
供 Claude Code Skill 通过 Bash 调用。

匹配策略：
  - 概念覆盖词法分（0.45）+ 校准后的语义分（0.55）混合召回
  - 主题合格后再按声望、速度、费用等偏好重排
  - 若 FAISS 索引不存在，自动降级为纯关键词匹配

Usage:
    python query_db.py --discipline economics --keywords "labor,wage,employment"
    python query_db.py --discipline both --keywords "aging,pension,labor market" --sort speed
    python query_db.py --discipline demography --keywords "fertility,family" --oa-only --max-apc 3000
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_paths import DataDirectoryError, resolve_data_dir


DATA_DIR = None
DB_DIR = None
RECALL_POOL_SIZE = 300
MIN_TOPIC_FIT = 0.35
SEMANTIC_SCORE_FLOOR = 0.72
SEMANTIC_SCORE_SPAN = 0.16
MIN_UNCORROBORATED_SEMANTIC_SCORE = 0.84
TOKEN_ALIASES = {
    "ageing": "aging",
    "behaviour": "behavior",
    "behaviours": "behavior",
    "educational": "education",
    "epidemiological": "epidemiology",
    "labour": "labor",
    "political": "politics",
}
MATCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "among",
    "between",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "using",
    "with",
    "within",
}
METHOD_CONTEXT_TOKENS = {
    "cohort",
    "difference",
    "experiment",
    "interview",
    "panel",
    "qualitative",
    "randomized",
    "regression",
    "survey",
    "trial",
}
GEOGRAPHY_CONTEXT_TOKENS = {
    "africa",
    "america",
    "asia",
    "australia",
    "britain",
    "china",
    "europe",
    "european",
    "india",
    "japan",
    "korea",
    "oecd",
    "uk",
}
POPULATION_CONTEXT_TOKENS = {
    "adolescent",
    "adult",
    "elderly",
    "older",
    "youth",
}
GENERIC_PARTIAL_TOKENS = {
    "analysis",
    "climate",
    "data",
    "development",
    "economic",
    "environment",
    "health",
    "international",
    "policy",
    "population",
    "research",
    "science",
    "social",
    "study",
}


def set_data_dir(path=None):
    """Configure one validated data directory for lexical and semantic search."""
    global DATA_DIR, DB_DIR
    DATA_DIR = resolve_data_dir(__file__, explicit=path)
    DB_DIR = DATA_DIR
    return DATA_DIR


def get_data_dir():
    return DATA_DIR or set_data_dir()


def load_database(discipline):
    """Load journal database for specified discipline(s).

    Supports: 'all' (unified SSCI), 'economics', 'demography', 'both' (econ+demo legacy).
    """
    journals = []
    db_dir = get_data_dir()

    # Try unified SSCI database first for 'all'
    if discipline == "all":
        path = db_dir / "journals_ssci.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                journals = json.load(f)
            return journals
        # Fallback to loading all available files
        discipline = "both"

    if discipline in ("economics", "both"):
        path = db_dir / "journals_economics.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                journals.extend(json.load(f))

    if discipline in ("demography", "both"):
        path = db_dir / "journals_demography.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                existing_issns = {j["issn_l"] for j in journals}
                for j in data:
                    if j["issn_l"] not in existing_issns:
                        journals.append(j)

    return journals


def normalize_match_token(token):
    """Normalize lightweight spelling/morphology variants for topic matching."""
    token = TOKEN_ALIASES.get(token, token)
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize_for_match(text):
    tokens = []
    seen = set()
    for token in re.findall(r"[a-z0-9]+", (text or "").lower()):
        normalized = normalize_match_token(token)
        if normalized in MATCH_STOPWORDS or normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return tokens


def keyword_phrase_score(keyword, evidence_text):
    """Score one concept against one evidence string without generic one-word leakage."""
    query_tokens = tokenize_for_match(keyword)
    evidence_tokens = set(tokenize_for_match(evidence_text))
    if not query_tokens or not evidence_tokens:
        return 0.0

    matched_tokens = [
        token
        for token in query_tokens
        if token in evidence_tokens
    ]
    if len(query_tokens) == 1:
        return float(bool(matched_tokens))
    if len(matched_tokens) >= 2:
        return len(matched_tokens) / len(query_tokens)
    if (
        len(matched_tokens) == 1
        and len(matched_tokens[0]) >= 4
        and matched_tokens[0] not in GENERIC_PARTIAL_TOKENS
    ):
        return 0.35
    return 0.0


def journal_keyword_evidence(journal):
    """Return de-duplicated journal name/topic evidence with position weights."""
    evidence = []
    seen = set()

    def add(text, weight):
        normalized = " ".join(tokenize_for_match(text))
        if normalized and normalized not in seen:
            seen.add(normalized)
            evidence.append((text, weight))

    add(journal.get("name", ""), 1.0)
    for index, topic in enumerate(journal.get("topics", [])[:15]):
        text = topic.get("name", "") if isinstance(topic, dict) else str(topic)
        add(text, max(0.65, 1.0 - index * 0.025))
    for scope_keyword in journal.get("scope_keywords", []):
        add(scope_keyword, 0.70)

    return evidence


def keyword_importance(keyword):
    """Downweight study context so it cannot overpower the journal's core scope."""
    tokens = set(tokenize_for_match(keyword))
    if tokens & METHOD_CONTEXT_TOKENS:
        return 0.25
    if tokens & GEOGRAPHY_CONTEXT_TOKENS:
        return 0.35
    if tokens & POPULATION_CONTEXT_TOKENS:
        return 0.50
    return 1.0


def build_semantic_query(keywords):
    """Use scope-defining concepts for SPECTER2; keep context in lexical matching."""
    core_keywords = [
        keyword
        for keyword in keywords
        if keyword_importance(keyword) >= 0.75
    ]
    return " ".join(core_keywords or keywords)


def compute_keyword_score(journal, keywords, core_only=False):
    """Score concept coverage, taking only the best evidence for each keyword."""
    if not keywords:
        return 1.0

    evidence = journal_keyword_evidence(journal)
    selected_keywords = [
        keyword
        for keyword in keywords
        if not core_only or keyword_importance(keyword) >= 0.75
    ]
    if not selected_keywords:
        selected_keywords = keywords
    weighted_scores = []
    total_weight = 0.0
    for keyword in selected_keywords:
        importance = keyword_importance(keyword)
        score = max(
            (
                keyword_phrase_score(keyword, text) * weight
                for text, weight in evidence
            ),
            default=0.0,
        )
        weighted_scores.append(score * importance)
        total_weight += importance
    return sum(weighted_scores) / total_weight if total_weight else 0.0


def is_review_only_journal(journal):
    """Identify clearly review/commissioned outlets without broad title guessing."""
    name = (journal.get("name") or "").strip().lower()
    notes = (journal.get("notes") or "").lower()
    name_match = (
        name.startswith("annual review of ")
        or name.startswith("wiley interdisciplinary reviews")
        or name.startswith("current opinion in ")
        or (name.startswith("current ") and name.endswith(" reports"))
    )
    note_markers = (
        "commissioned",
        "reviews only",
        "review-only",
        "约稿为主",
        "综述类期刊",
    )
    return name_match or any(marker in notes for marker in note_markers)


def apply_filters(journals, args):
    """Apply hard filters to journal list."""
    filtered = journals

    if args.oa_only:
        filtered = [j for j in filtered if j.get("is_oa")]

    if args.max_apc is not None:
        filtered = [
            j for j in filtered
            if j.get("apc_usd") is not None and j["apc_usd"] <= args.max_apc
        ]

    if args.min_quartile:
        q_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
        max_q = q_map.get(args.min_quartile, 4)
        filtered = [
            j for j in filtered
            if j.get("jcr_quartile") in q_map
            and q_map[j["jcr_quartile"]] <= max_q
        ]

    if getattr(args, "max_review_days", None) is not None:
        filtered = [
            j for j in filtered
            if j.get("review_median_days") is None
            or j["review_median_days"] <= args.max_review_days
        ]

    if getattr(args, "require_review_data", False):
        filtered = [j for j in filtered if j.get("review_median_days") is not None]

    if not getattr(args, "include_review_only", False):
        filtered = [j for j in filtered if not is_review_only_journal(j)]

    # Exclude non-submittable publications (working papers, reports, OECD surveys, etc.)
    exclude_patterns = [
        "working paper", "discussion note", "staff paper",
        "oecd economic surveys", "oecd journal", "oecd social",
        "oecd employment", "oecd pensions",
        "imf staff", "briefing",
    ]
    filtered = [
        j for j in filtered
        if not any(pat in j.get("name", "").lower() for pat in exclude_patterns)
    ]

    return filtered


def get_normalized_impact(journal):
    """Get a normalized impact score (0-10 scale) for sorting.
    Manual IF takes priority; falls back to citedness_2yr with a cap."""
    if journal.get("impact_factor"):
        return min(journal["impact_factor"], 20)  # Cap at 20
    citedness = journal.get("citedness_2yr") or 0
    # Cap citedness at 15 to prevent outliers from dominating
    return min(citedness, 15)


def clamp(value, low=0.0, high=1.0):
    """Clamp a numeric score to a stable range."""
    return max(low, min(high, value))


def calibrate_semantic_score(score):
    """Map clustered SPECTER2 cosine scores onto a useful 0-1 relevance scale."""
    return clamp((float(score) - SEMANTIC_SCORE_FLOOR) / SEMANTIC_SCORE_SPAN)


def normalize_review_confidence(journal):
    """Label review-time reliability based on sample size and coverage."""
    samples = journal.get("review_samples") or 0
    coverage = journal.get("review_coverage") or 0
    review_days = journal.get("review_median_days")

    if review_days is None:
        return "missing"
    if samples >= 10 and coverage >= 0.3:
        return "credible"
    if samples >= 3:
        return "limited"
    return "very_limited"


def review_confidence_details(journal):
    """Return a compact evidence bundle for review-time interpretation."""
    confidence = normalize_review_confidence(journal)
    labels = {
        "credible": "credible: enough samples and date coverage",
        "limited": "limited: small sample or sparse date coverage",
        "very_limited": "very_limited: very few dated articles",
        "missing": "missing: no reliable received-to-accepted data",
    }
    return {
        "level": confidence,
        "label": labels[confidence],
        "median_days": journal.get("review_median_days"),
        "samples": journal.get("review_samples") or 0,
        "coverage": journal.get("review_coverage") or 0,
        "accept_to_online_days": journal.get("accept_to_online_days"),
    }


def compute_topic_fit(journal, max_keyword_score):
    """Combine keyword, semantic, and pre-merged scores into a topic-fit score."""
    combined = journal.get("_combined_score")
    if combined is not None:
        return clamp(float(combined))

    keyword = journal.get("_keyword_score") or 0
    keyword_norm = keyword / max_keyword_score if max_keyword_score else 0
    semantic = journal.get("_semantic_score") or 0
    if semantic:
        return clamp(
            keyword_norm * 0.45
            + calibrate_semantic_score(semantic) * 0.55
        )
    return clamp(keyword_norm)


def compute_prestige_score(journal):
    """Score journal prestige using JCR quartile first, then impact proxies."""
    q_scores = {"Q1": 1.0, "Q2": 0.78, "Q3": 0.55, "Q4": 0.32}
    quartile = journal.get("jcr_quartile")
    if quartile in q_scores:
        quartile_score = q_scores[quartile]
    else:
        quartile_score = 0.45

    impact = journal.get("impact_factor")
    if impact is not None:
        impact_score = clamp(float(impact) / 10)
    else:
        impact_score = clamp((journal.get("citedness_2yr") or 0) / 15)

    cas = journal.get("cas_zone")
    cas_scores = {1: 1.0, 2: 0.78, 3: 0.55, 4: 0.32}
    cas_score = cas_scores.get(cas, 0.45)

    return clamp(quartile_score * 0.45 + impact_score * 0.35 + cas_score * 0.20)


def compute_speed_score(journal):
    """Score review speed while giving missing data a neutral-low value."""
    days = journal.get("review_median_days")
    if days is None:
        return 0.35
    if days <= 90:
        return 1.0
    if days <= 150:
        return 0.82
    if days <= 240:
        return 0.62
    if days <= 365:
        return 0.42
    return 0.18


def compute_cost_score(journal, max_apc=None):
    """Score APC affordability and OA type."""
    oa_type = journal.get("oa_type")
    apc = journal.get("apc_usd")

    if oa_type in ("diamond", "subscription") and apc is None:
        return 1.0
    if apc is None:
        return 0.62
    if max_apc is not None:
        return clamp(1 - (apc / max(max_apc, 1)) * 0.35)
    if apc <= 1000:
        return 0.92
    if apc <= 2500:
        return 0.72
    if apc <= 4000:
        return 0.48
    return 0.24


def compute_cn_score(journal):
    """Score CN author presence, capped to avoid over-rewarding outliers."""
    ratio = journal.get("cn_author_ratio")
    if ratio is None:
        return 0.35
    return clamp(float(ratio) / 0.15)


def compute_volume_score(journal):
    """Score annual volume, preferring journals with a steady article flow."""
    volume = journal.get("annual_volume_2024")
    if volume is None:
        return 0.35
    if 25 <= volume <= 250:
        return 1.0
    if 10 <= volume < 25:
        return 0.72
    if 250 < volume <= 500:
        return 0.68
    if volume > 500:
        return 0.45
    return 0.35


def compute_data_completeness_score(journal):
    """Reward journals with enough metadata for a more trustworthy decision."""
    fields = [
        "jcr_quartile", "cas_zone", "impact_factor", "cn_author_ratio",
        "annual_volume_2024", "review_median_days", "apc_usd", "review_type",
    ]
    present = sum(1 for field in fields if journal.get(field) is not None)
    return present / len(fields)


def build_risk_flags(journal):
    """Generate compact risk labels for downstream explanation."""
    flags = []
    confidence = normalize_review_confidence(journal)
    if confidence == "missing":
        flags.append("review_time_missing")
    elif confidence in ("limited", "very_limited"):
        flags.append("review_time_low_confidence")

    if journal.get("jcr_quartile") is None and journal.get("cas_zone") is None:
        flags.append("ranking_data_missing")
    if journal.get("apc_usd") and journal["apc_usd"] > 4000:
        flags.append("high_apc")
    if not journal.get("topics"):
        flags.append("topic_metadata_sparse")
    if journal.get("annual_volume_2024") is not None and journal["annual_volume_2024"] < 10:
        flags.append("low_annual_volume")
    return flags


def build_recommendation_notes(journal, scores):
    """Create short machine-readable reasons for recommending the journal."""
    notes = []
    if scores["topic_fit"] >= 0.75:
        notes.append("strong_topic_fit")
    elif scores["topic_fit"] >= 0.5:
        notes.append("moderate_topic_fit")

    if journal.get("jcr_quartile"):
        notes.append(f"jcr_{journal['jcr_quartile'].lower()}")
    if journal.get("cas_zone"):
        notes.append(f"cas_zone_{journal['cas_zone']}")
    if (
        scores["speed_fit"] >= 0.8
        and normalize_review_confidence(journal) == "credible"
    ):
        notes.append("fast_review_signal")
    if scores["cost_fit"] >= 0.85:
        notes.append("low_cost_or_subscription")
    if scores["cn_fit"] >= 0.7:
        notes.append("cn_author_presence")
    return notes[:5]


def parse_priorities(priority_text):
    """Parse comma-separated user priorities into normalized internal names."""
    if not priority_text:
        return []
    aliases = {
        "fit": "topic_fit",
        "topic": "topic_fit",
        "match": "topic_fit",
        "prestige": "prestige_fit",
        "rank": "prestige_fit",
        "if": "prestige_fit",
        "speed": "speed_fit",
        "fast": "speed_fit",
        "budget": "cost_fit",
        "cost": "cost_fit",
        "apc": "cost_fit",
        "cn": "cn_fit",
        "china": "cn_fit",
        "friendly": "cn_fit",
        "volume": "volume_fit",
        "capacity": "volume_fit",
        "data": "data_completeness",
    }
    priorities = []
    for item in priority_text.split(","):
        key = item.strip().lower().replace("-", "_")
        mapped = aliases.get(key)
        if mapped and mapped not in priorities:
            priorities.append(mapped)
    return priorities


def normalize_weights(weights):
    total = sum(weights.values()) or 1
    return {key: value / total for key, value in weights.items()}


def scoring_weights(args):
    """Return multi-objective weights adjusted by user preference."""
    weights = {
        "topic_fit": 0.42,
        "prestige_fit": 0.16,
        "speed_fit": 0.10,
        "cost_fit": 0.08,
        "cn_fit": 0.08,
        "volume_fit": 0.06,
        "data_completeness": 0.10,
    }
    sort_mode = args.sort
    if sort_mode == "prestige":
        weights.update({"topic_fit": 0.34, "prestige_fit": 0.34, "speed_fit": 0.06})
    elif sort_mode == "speed":
        weights.update({"topic_fit": 0.34, "prestige_fit": 0.12, "speed_fit": 0.28})
    elif sort_mode == "cn_friendly":
        weights.update({"topic_fit": 0.34, "prestige_fit": 0.12, "cn_fit": 0.24})

    for priority in parse_priorities(getattr(args, "priorities", "")):
        weights[priority] = weights.get(priority, 0) + 0.14
        if priority != "topic_fit":
            weights["topic_fit"] = max(0.28, weights["topic_fit"] - 0.03)

    return normalize_weights(weights)


def score_candidate(journal, args, max_keyword_score):
    """Attach v1 multi-objective scores to one journal record."""
    scores = {
        "topic_fit": compute_topic_fit(journal, max_keyword_score),
        "prestige_fit": compute_prestige_score(journal),
        "speed_fit": compute_speed_score(journal),
        "cost_fit": compute_cost_score(journal, args.max_apc),
        "cn_fit": compute_cn_score(journal),
        "volume_fit": compute_volume_score(journal),
        "data_completeness": compute_data_completeness_score(journal),
    }
    weights = scoring_weights(args)
    other_weights = {
        key: value
        for key, value in weights.items()
        if key != "topic_fit"
    }
    other_total = sum(other_weights.values()) or 1.0
    other_score = sum(
        scores[key] * weight
        for key, weight in other_weights.items()
    ) / other_total
    topic_share = (
        0.75
        if "topic_fit" in parse_priorities(getattr(args, "priorities", ""))
        else 0.70
    )
    weighted = scores["topic_fit"] * topic_share + other_score * (1 - topic_share)
    risk_flags = build_risk_flags(journal)
    risk_penalty = min(0.10, len(risk_flags) * 0.025)
    final_score = clamp(weighted - risk_penalty)

    journal["_fit_scores"] = {key: round(value, 3) for key, value in scores.items()}
    journal["_final_score"] = round(final_score, 4)
    journal["_review_confidence"] = normalize_review_confidence(journal)
    journal["_review_evidence"] = review_confidence_details(journal)
    journal["_risk_flags"] = risk_flags
    journal["_recommendation_notes"] = build_recommendation_notes(journal, scores)
    return journal


def rank_candidates(candidates, args, top_n):
    """Rank candidates with explainable multi-objective scoring."""
    max_keyword_score = max(
        (j.get("_keyword_score", 0) for j in candidates),
        default=1,
    ) or 1
    scored = [score_candidate(j, args, max_keyword_score) for j in candidates]
    scored = [
        journal
        for journal in scored
        if journal.get("_fit_scores", {}).get("topic_fit", 0) >= MIN_TOPIC_FIT
        and (
            journal.get("_keyword_score", 0) > 0
            or bool(journal.get("topics"))
        )
        and (
            journal.get("_core_keyword_score", 0) > 0
            or journal.get("_semantic_score", 0)
            >= MIN_UNCORROBORATED_SEMANTIC_SCORE
        )
    ]
    scored.sort(
        key=lambda j: (
            -j.get("_final_score", 0),
            -j.get("_fit_scores", {}).get("topic_fit", 0),
            -(j.get("impact_factor") or j.get("citedness_2yr") or 0),
        )
    )
    return scored[:top_n]


def sort_journals(journals, sort_mode, keywords):
    """Build a topic-only lexical recall list; preferences apply during final ranking."""
    for j in journals:
        j["_keyword_score"] = compute_keyword_score(j, keywords)
        j["_core_keyword_score"] = compute_keyword_score(
            j,
            keywords,
            core_only=True,
        )

    # Minimum keyword relevance threshold
    min_score = 0.05 if keywords else 0
    journals = [j for j in journals if j["_keyword_score"] >= min_score]
    journals.sort(
        key=lambda journal: (
            -journal["_keyword_score"],
            -get_normalized_impact(journal),
            journal.get("issn_l", ""),
        )
    )
    return journals


def format_output(journals, top_n=15):
    """Format top N journals for output."""
    results = []
    for j in journals[:top_n]:
        # Only include journals with some keyword relevance
        if j.get("_keyword_score", 0) <= 0:
            continue

        record = {
            "name": j["name"],
            "abbreviation": j.get("abbreviation"),
            "issn_l": j["issn_l"],
            "publisher": j.get("publisher"),
            "jcr_quartile": j.get("jcr_quartile"),
            "cas_zone": j.get("cas_zone"),
            "impact_factor": j.get("impact_factor"),
            "citedness_2yr": j.get("citedness_2yr"),
            "is_oa": j.get("is_oa"),
            "oa_type": j.get("oa_type"),
            "apc_usd": j.get("apc_usd"),
            "apc_waiver": j.get("apc_waiver"),
            "cn_author_ratio": j.get("cn_author_ratio"),
            "annual_volume_2024": j.get("annual_volume_2024"),
            "review_median_days": j.get("review_median_days"),
            "review_samples": j.get("review_samples"),
            "accept_to_online_days": j.get("accept_to_online_days"),
            "review_coverage": j.get("review_coverage"),
            "word_limit_max": j.get("word_limit_max"),
            "review_type": j.get("review_type"),
            "warning_tags": j.get("warning_tags", []),
            "notes": j.get("notes", ""),
            "topics": [t["name"] for t in j.get("topics", [])[:5]],
            "_keyword_score": round(j.get("_keyword_score", 0), 2),
            "_core_keyword_score": round(j.get("_core_keyword_score", 0), 2),
        }
        results.append(record)

    return results


def run_semantic_search(
    query_text,
    top_k,
    filter_quartile=None,
    max_apc=None,
    oa_only=False,
    max_review_days=None,
    require_review_data=False,
):
    """
    调用 semantic_search.py 获取语义搜索结果。

    返回 ({issn_l: _semantic_score}, error)。
    若索引不存在或调用失败，返回空字典和可展示的降级原因。
    """
    data_dir = get_data_dir()
    faiss_index = data_dir / "journal_index.faiss"
    if not faiss_index.exists():
        return {}, (
            f"Semantic index not found at {faiss_index}. "
            "Run python scripts/build_embeddings.py."
        )

    script_path = Path(__file__).parent / "semantic_search.py"
    cmd = [
        sys.executable, str(script_path),
        "--query", query_text,
        "--top", str(top_k),
        "--data-dir", str(data_dir),
    ]
    if filter_quartile:
        cmd += ["--filter-quartile", filter_quartile]
    if max_apc is not None:
        cmd += ["--max-apc", str(max_apc)]
    if oa_only:
        cmd.append("--oa-only")
    if max_review_days is not None:
        cmd += ["--max-review-days", str(max_review_days)]
    if require_review_data:
        cmd.append("--require-review-data")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            return {}, f"Semantic search failed: {detail or 'unknown error'}"
        data = json.loads(result.stdout)
        scores = {
            r["issn_l"]: r["_semantic_score"]
            for r in data.get("results", [])
        }
        return scores, None
    except subprocess.TimeoutExpired:
        return {}, "Semantic search timed out after 120 seconds."
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"Semantic search failed: {exc}"


def merge_results(keyword_journals, semantic_scores, top_n, all_journals=None):
    """
    合并关键词匹配结果和语义搜索结果。

    策略：
      - 关键词概念覆盖分已经位于 [0, 1]，权重 0.45
      - SPECTER2 余弦分先校准到 [0, 1]，权重 0.55
      - 对两个来源的 issn_l 取并集，按综合分排序
      - 返回 top_n 条结果
    """
    # Keyword scores are already bounded concept-coverage scores in [0, 1].
    kw_scores = {j["issn_l"]: j.get("_keyword_score", 0) for j in keyword_journals}

    # 收集所有候选期刊的 issn_l（两个来源的并集）
    all_issns = set(kw_scores) | set(semantic_scores)

    # 构建综合分数字典
    combined = {}
    for issn in all_issns:
        kw = kw_scores.get(issn, 0.0)
        sem = calibrate_semantic_score(semantic_scores.get(issn, 0.0))

        if semantic_scores:
            # Require corroboration where available; strong semantic-only bridge
            # journals can still pass the downstream relevance floor.
            combined[issn] = kw * 0.45 + sem * 0.55
        else:
            # 语义搜索不可用时，降级为纯关键词分
            combined[issn] = kw

    # Stable secondary key makes equal-score results reproducible.
    sorted_issns = sorted(
        combined.items(),
        key=lambda item: (-item[1], item[0]),
    )[:top_n]

    # 重建期刊记录列表（优先从关键词结果取，补充 _semantic_score）
    journal_map = {j["issn_l"]: j for j in keyword_journals}
    all_journal_map = None
    results = []
    for issn, score in sorted_issns:
        if issn in journal_map:
            j = journal_map[issn].copy()
        else:
            # 仅语义搜索命中、关键词未命中的期刊：从数据库加载基础信息
            if all_journal_map is None:
                if all_journals is None:
                    all_journals = load_database("all")
                all_journal_map = {x["issn_l"]: x for x in all_journals}
            j_match = all_journal_map.get(issn)
            if not j_match:
                continue
            j = {
                "name": j_match["name"],
                "abbreviation": j_match.get("abbreviation"),
                "issn_l": issn,
                "publisher": j_match.get("publisher"),
                "jcr_quartile": j_match.get("jcr_quartile"),
                "cas_zone": j_match.get("cas_zone"),
                "impact_factor": j_match.get("impact_factor"),
                "citedness_2yr": j_match.get("citedness_2yr"),
                "is_oa": j_match.get("is_oa"),
                "oa_type": j_match.get("oa_type"),
                "apc_usd": j_match.get("apc_usd"),
                "apc_waiver": j_match.get("apc_waiver"),
                "cn_author_ratio": j_match.get("cn_author_ratio"),
                "annual_volume_2024": j_match.get("annual_volume_2024"),
                "review_median_days": j_match.get("review_median_days"),
                "review_samples": j_match.get("review_samples"),
                "accept_to_online_days": j_match.get("accept_to_online_days"),
                "review_coverage": j_match.get("review_coverage"),
                "word_limit_max": j_match.get("word_limit_max"),
                "review_type": j_match.get("review_type"),
                "warning_tags": j_match.get("warning_tags", []),
                "notes": j_match.get("notes", ""),
                "topics": [t.get("name", t) if isinstance(t, dict) else t
                           for t in j_match.get("topics", [])[:5]],
                "_keyword_score": j_match.get("_keyword_score", 0.0),
                "_core_keyword_score": j_match.get(
                    "_core_keyword_score",
                    0.0,
                ),
            }

        j["_semantic_score"] = round(semantic_scores.get(issn, 0.0), 4)
        j["_semantic_fit_score"] = round(
            calibrate_semantic_score(semantic_scores.get(issn, 0.0)),
            4,
        )
        j["_combined_score"] = round(score, 4)
        results.append(j)

    return results


def positive_int(value):
    """Parse a strictly positive integer for CLI arguments."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def recall_pool_size(top_n):
    """Keep rankings prefix-stable when only the requested output size changes."""
    return max(RECALL_POOL_SIZE, top_n)


def main():
    parser = argparse.ArgumentParser(description="Query journal database")
    parser.add_argument(
        "--discipline", choices=["economics", "demography", "both", "all"],
        default="all", help="Which discipline database to search (all = full SSCI/AHCI)"
    )
    parser.add_argument(
        "--keywords", type=str, default="",
        help="Comma-separated keywords to match (e.g., 'labor,wage,employment')"
    )
    parser.add_argument(
        "--max-apc", type=int, default=None,
        help="Maximum APC in USD"
    )
    parser.add_argument(
        "--oa-only", action="store_true",
        help="Only show OA journals"
    )
    parser.add_argument(
        "--min-quartile", type=str, default=None,
        choices=["Q1", "Q2", "Q3", "Q4"],
        help="Minimum JCR quartile (e.g., Q2 means Q1 and Q2)"
    )
    parser.add_argument(
        "--sort", type=str, default="balanced",
        choices=["speed", "prestige", "cn_friendly", "balanced"],
        help="Sorting strategy"
    )
    parser.add_argument(
        "--priorities", type=str, default="",
        help=(
            "Comma-separated soft priorities: fit,prestige,speed,budget,cn,volume,data. "
            "Example: --priorities prestige,speed"
        )
    )
    parser.add_argument(
        "--max-review-days", type=int, default=None,
        help="Soft hard-filter: keep journals with missing review data or median review days under this limit"
    )
    parser.add_argument(
        "--require-review-data", action="store_true",
        help="Only show journals with review-time data"
    )
    parser.add_argument(
        "--include-review-only",
        action="store_true",
        help="Include clearly identified review/commissioned outlets",
    )
    parser.add_argument(
        "--top", type=positive_int, default=15,
        help="Number of results to return"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory containing journals_ssci.json (overrides auto-discovery)",
    )

    args = parser.parse_args()
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    # --- 关键词匹配部分 ---
    try:
        set_data_dir(args.data_dir)
        all_journals = load_database(args.discipline)
    except DataDirectoryError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
    if not all_journals:
        print(json.dumps({
            "error": (
                f"No journals found in {get_data_dir()}. "
                "Run build_database.py first."
            )
        }))
        sys.exit(1)

    journals = apply_filters(all_journals, args)
    # 扩大候选池再交给合并逻辑，避免语义命中的期刊被提前截断
    candidate_pool = recall_pool_size(args.top)
    kw_journals = sort_journals(journals, args.sort, keywords)
    kw_results_raw = format_output(kw_journals, candidate_pool)

    # --- 语义搜索部分 ---
    query_text = build_semantic_query(keywords) if keywords else ""
    semantic_scores = {}
    semantic_error = None
    if query_text:
        semantic_scores, semantic_error = run_semantic_search(
            query_text,
            top_k=candidate_pool,
            filter_quartile=args.min_quartile,
            max_apc=args.max_apc,
            oa_only=args.oa_only,
            max_review_days=args.max_review_days,
            require_review_data=args.require_review_data,
        )

    # --- 合并 ---
    if semantic_scores:
        # 有语义结果：先合并成候选池，再做多目标重排
        candidate_results = merge_results(
            kw_results_raw,
            semantic_scores,
            top_n=candidate_pool,
            all_journals=all_journals,
        )
    else:
        # 无语义结果（索引未建或无关键词）：使用关键词候选池
        candidate_results = format_output(kw_journals, candidate_pool)

    # Semantic-only candidates must obey the same hard filters as keyword hits.
    candidate_results = apply_filters(candidate_results, args)
    results = rank_candidates(candidate_results, args, args.top)
    if not results:
        quality_status = "no_good_match"
    elif len(results) < args.top:
        quality_status = "limited_matches"
    else:
        quality_status = "ok"

    output = {
        "query": {
            "discipline": args.discipline,
            "keywords": keywords,
            "sort": args.sort,
            "priorities": parse_priorities(args.priorities),
            "semantic_query_text": query_text,
            "semantic_search": bool(semantic_scores),
            "semantic_status": (
                "enabled"
                if semantic_scores
                else "fallback"
                if query_text
                else "disabled_no_query"
            ),
            "semantic_error": semantic_error,
            "data_dir": str(get_data_dir()),
            "filters": {
                "oa_only": args.oa_only,
                "max_apc": args.max_apc,
                "min_quartile": args.min_quartile,
                "max_review_days": args.max_review_days,
                "require_review_data": args.require_review_data,
                "include_review_only": args.include_review_only,
            },
        },
        "total_in_database": len(all_journals),
        "results_count": len(results),
        "quality": {
            "status": quality_status,
            "topic_fit_floor": MIN_TOPIC_FIT,
            "requested_results": args.top,
        },
        "results": results,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

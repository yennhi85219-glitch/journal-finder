import copy
import importlib.util
from argparse import Namespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "quality_query_db",
    ROOT / "skill" / "scripts" / "query_db.py",
)
query_db = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(query_db)
SEMANTIC_SPEC = importlib.util.spec_from_file_location(
    "quality_semantic_search",
    ROOT / "skill" / "scripts" / "semantic_search.py",
)
semantic_search = importlib.util.module_from_spec(SEMANTIC_SPEC)
SEMANTIC_SPEC.loader.exec_module(semantic_search)


def journal(
    issn,
    name,
    topics,
    *,
    quartile="Q2",
    impact_factor=4.0,
    review_days=120,
    review_samples=20,
    review_coverage=0.8,
    apc_usd=2000,
    cn_ratio=0.08,
):
    return {
        "issn_l": issn,
        "name": name,
        "abbreviation": None,
        "publisher": "Test Publisher",
        "topics": [{"name": topic} for topic in topics],
        "scope_keywords": [topic.lower() for topic in topics],
        "jcr_quartile": quartile,
        "cas_zone": 2,
        "impact_factor": impact_factor,
        "citedness_2yr": impact_factor,
        "is_oa": True,
        "oa_type": "gold",
        "apc_usd": apc_usd,
        "apc_waiver": None,
        "cn_author_ratio": cn_ratio,
        "annual_volume_2024": 100,
        "review_median_days": review_days,
        "review_samples": review_samples,
        "review_coverage": review_coverage,
        "accept_to_online_days": 20,
        "word_limit_max": None,
        "review_type": "double_blind",
        "warning_tags": [],
        "notes": "",
    }


def args(**overrides):
    defaults = {
        "oa_only": False,
        "max_apc": None,
        "min_quartile": None,
        "max_review_days": None,
        "require_review_data": False,
        "include_review_only": False,
        "sort": "balanced",
        "priorities": "fit",
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def rank_keyword_only(journals, keywords, top_n):
    options = args()
    candidates = query_db.sort_journals(
        copy.deepcopy(journals),
        options.sort,
        keywords,
    )
    formatted = query_db.format_output(candidates, top_n=100)
    return query_db.rank_candidates(formatted, options, top_n)


def test_phrase_matching_requires_concept_coverage_and_dedupes_topics():
    relevant = journal(
        "1111-1111",
        "Environmental Epidemiology",
        [
            "Climate Change and Health Impacts",
            "Environmental Epidemiology",
        ],
    )
    generic_health = journal(
        "2222-2222",
        "Journal of Eating Disorders",
        [
            "Obesity and Health Practices",
            "Digital Mental Health Interventions",
            "Global Health Studies",
        ],
    )
    duplicated = copy.deepcopy(relevant)
    duplicated["issn_l"] = "3333-3333"
    duplicated["topics"] *= 3
    duplicated["scope_keywords"] *= 3
    keywords = ["climate health", "environmental epidemiology"]

    relevant_score = query_db.compute_keyword_score(relevant, keywords)
    generic_score = query_db.compute_keyword_score(generic_health, keywords)
    duplicated_score = query_db.compute_keyword_score(duplicated, keywords)

    assert relevant_score >= 0.8
    assert generic_score == 0
    assert duplicated_score == pytest.approx(relevant_score)


def test_keyword_matching_ignores_grammatical_stopwords():
    unrelated = journal(
        "4444-4444",
        "Antimicrobial Resistance",
        [
            "Resistance in Clinical Settings",
            "Antibiotics in Hospitals",
        ],
    )

    score = query_db.compute_keyword_score(
        unrelated,
        ["difference in differences", "labor market"],
    )

    assert score == 0


def test_distinctive_partial_terms_can_support_recall():
    journal_of_human_resources = journal(
        "4444-5555",
        "Journal of Human Resources",
        [
            "Wage Inequality",
            "Employment and Welfare Studies",
        ],
    )

    score = query_db.compute_keyword_score(
        journal_of_human_resources,
        ["minimum wage", "youth employment", "labor economics"],
        core_only=True,
    )

    assert score > 0


def test_semantic_query_uses_scope_concepts_not_study_context():
    keywords = [
        "minimum wage",
        "difference in differences",
        "China",
        "labor economics",
    ]

    semantic_query = query_db.build_semantic_query(keywords)

    assert semantic_query == "minimum wage labor economics"


def test_keyword_fallback_keeps_relevance_ahead_of_prestige():
    journals = [
        journal(
            "1000-0001",
            "Journal of Labor Economics",
            ["Labor Market Dynamics and Wage Inequality", "Employment Studies"],
            quartile="Q1",
            impact_factor=8.0,
        ),
        journal(
            "1000-0002",
            "Labour Economics",
            ["Minimum Wage and Employment", "Labor Market Policy"],
        ),
        journal(
            "1000-0003",
            "Industrial Relations",
            ["Employment Relations", "Wage Bargaining and Labor Markets"],
        ),
        journal(
            "2000-0001",
            "Nature Climate Change",
            ["Climate Models", "Environmental Policy"],
            quartile="Q1",
            impact_factor=27.0,
        ),
        journal(
            "2000-0002",
            "Medical Research",
            ["Clinical Medicine", "Public Health"],
            quartile="Q1",
            impact_factor=20.0,
        ),
    ]

    ranked = rank_keyword_only(
        journals,
        ["minimum wage", "employment", "labor market"],
        top_n=3,
    )
    names = [item["name"] for item in ranked]

    assert names[0] == "Labour Economics"
    assert set(names) == {
        "Journal of Labor Economics",
        "Labour Economics",
        "Industrial Relations",
    }


def test_hybrid_recall_keeps_semantic_bridge_and_drops_weak_distractors():
    journals = [
        journal(
            "3000-0001",
            "Environment International",
            ["Climate Change and Health Impacts", "Environmental Epidemiology"],
            quartile="Q1",
            impact_factor=10.0,
        ),
        journal(
            "3000-0002",
            "Environmental Epidemiology",
            ["Exposure Science", "Population Health"],
        ),
        journal(
            "3000-0003",
            "GeoHealth",
            ["Earth Systems", "Human Health"],
        ),
        journal(
            "4000-0001",
            "Climate Policy",
            ["Climate Governance", "Carbon Policy"],
            quartile="Q1",
            impact_factor=15.0,
        ),
        journal(
            "4000-0002",
            "Toxicology",
            ["Chemical Toxicity", "Laboratory Exposure"],
            quartile="Q1",
            impact_factor=18.0,
        ),
    ]
    keywords = ["temperature mortality", "climate health", "environmental epidemiology"]
    options = args()
    keyword_candidates = query_db.sort_journals(
        copy.deepcopy(journals),
        options.sort,
        keywords,
    )
    keyword_output = query_db.format_output(keyword_candidates, 100)
    semantic_scores = {
        "3000-0001": 0.86,
        "3000-0002": 0.87,
        "3000-0003": 0.86,
        "4000-0001": 0.78,
        "4000-0002": 0.77,
    }

    merged = query_db.merge_results(
        keyword_output,
        semantic_scores,
        top_n=100,
        all_journals=journals,
    )
    ranked = query_db.rank_candidates(merged, options, top_n=3)
    names = [item["name"] for item in ranked]

    assert "Environmental Epidemiology" in names[:2]
    assert set(names) == {
        "Environment International",
        "Environmental Epidemiology",
        "GeoHealth",
    }


def test_merge_ties_are_deterministic():
    journals = [
        journal("5000-0002", "Second", ["Labor Markets"]),
        journal("5000-0001", "First", ["Labor Markets"]),
    ]

    merged = query_db.merge_results(
        [],
        {"5000-0002": 0.8, "5000-0001": 0.8},
        top_n=2,
        all_journals=journals,
    )

    assert [item["issn_l"] for item in merged] == ["5000-0001", "5000-0002"]


def test_output_size_does_not_change_internal_recall_pool():
    assert query_db.recall_pool_size(5) == query_db.recall_pool_size(15)
    assert query_db.recall_pool_size(15) == query_db.recall_pool_size(100)


def test_semantic_prefilter_applies_all_hard_constraints():
    journals = {
        "7000-0001": journal(
            "7000-0001",
            "Eligible",
            ["Climate and Health"],
            quartile="Q1",
            review_days=90,
            apc_usd=1500,
        ),
        "7000-0002": journal(
            "7000-0002",
            "Missing Review",
            ["Climate and Health"],
            quartile="Q1",
            review_days=None,
            apc_usd=1500,
        ),
        "7000-0003": journal(
            "7000-0003",
            "Too Expensive",
            ["Climate and Health"],
            quartile="Q1",
            review_days=90,
            apc_usd=3000,
        ),
    }
    candidates = [
        ("7000-0001", 0.85),
        ("7000-0002", 0.84),
        ("7000-0003", 0.83),
    ]

    filtered = semantic_search.apply_filters(
        candidates,
        journals,
        filter_quartile="Q1",
        max_apc=2000,
        oa_only=True,
        max_review_days=120,
        require_review_data=True,
    )

    assert filtered == [("7000-0001", 0.85)]


def test_review_only_outlets_are_excluded_unless_requested():
    original = journal(
        "7000-1001",
        "Environmental Epidemiology",
        ["Environmental Epidemiology"],
    )
    review_only = journal(
        "7000-1002",
        "Current Environmental Health Reports",
        ["Environmental Epidemiology"],
    )

    default_results = query_db.apply_filters(
        [original, review_only],
        args(),
    )
    review_results = query_db.apply_filters(
        [original, review_only],
        args(include_review_only=True),
    )

    assert [item["name"] for item in default_results] == [
        "Environmental Epidemiology"
    ]
    assert [item["name"] for item in review_results] == [
        "Environmental Epidemiology",
        "Current Environmental Health Reports",
    ]


def test_fast_signal_requires_credible_review_evidence():
    weak = journal(
        "6000-0001",
        "Fast but Uncertain",
        ["Labor Markets"],
        review_days=30,
        review_samples=1,
        review_coverage=0.02,
    )
    options = args(priorities="speed")

    scored = query_db.score_candidate(weak, options, max_keyword_score=1)

    assert scored["_review_confidence"] == "very_limited"
    assert "fast_review_signal" not in scored["_recommendation_notes"]


@pytest.mark.parametrize(
    ("sort_mode", "priority", "expected"),
    [
        ("prestige", "prestige", "Prestige"),
        ("speed", "speed", "Speed"),
        ("balanced", "budget", "Budget"),
        ("cn_friendly", "cn", "CN"),
    ],
)
def test_preferences_choose_the_intended_winner(sort_mode, priority, expected):
    candidates = [
        journal(
            "8000-0001",
            "Prestige",
            ["Labor Markets"],
            quartile="Q1",
            impact_factor=12,
            review_days=180,
            apc_usd=3000,
            cn_ratio=0.02,
        ),
        journal(
            "8000-0002",
            "Speed",
            ["Labor Markets"],
            quartile="Q3",
            impact_factor=2,
            review_days=45,
            apc_usd=3000,
            cn_ratio=0.02,
        ),
        journal(
            "8000-0003",
            "Budget",
            ["Labor Markets"],
            quartile="Q3",
            impact_factor=2,
            review_days=180,
            apc_usd=500,
            cn_ratio=0.02,
        ),
        journal(
            "8000-0004",
            "CN",
            ["Labor Markets"],
            quartile="Q3",
            impact_factor=2,
            review_days=180,
            apc_usd=3000,
            cn_ratio=0.30,
        ),
    ]
    for candidate in candidates:
        candidate["_combined_score"] = 0.75
        candidate["_keyword_score"] = 0.75
        candidate["_core_keyword_score"] = 0.75
    options = args(sort=sort_mode, priorities=priority)

    ranked = query_db.rank_candidates(candidates, options, top_n=4)

    assert ranked[0]["name"] == expected

import importlib.util
import io
import json
import sys
import unittest
from argparse import ArgumentTypeError, Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "skill" / "scripts" / "query_db.py"
SPEC = importlib.util.spec_from_file_location("query_db", MODULE_PATH)
query_db = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(query_db)


def journal(
    issn,
    name,
    *,
    is_oa=True,
    quartile="Q1",
    review_days=90,
):
    return {
        "issn_l": issn,
        "name": name,
        "is_oa": is_oa,
        "jcr_quartile": quartile,
        "review_median_days": review_days,
        "topics": [],
        "scope_keywords": [],
        "warning_tags": [],
        "notes": "",
    }


class QueryDatabaseTests(unittest.TestCase):
    def test_explicit_quartile_filter_excludes_unknown_values(self):
        args = Namespace(
            oa_only=False,
            max_apc=None,
            min_quartile="Q2",
            max_review_days=None,
            require_review_data=False,
        )
        journals = [
            journal("0000-0001", "Known Q1", quartile="Q1"),
            journal("0000-0002", "Known Q2", quartile="Q2"),
            journal("0000-0003", "Known Q3", quartile="Q3"),
            journal("0000-0004", "Unknown", quartile=None),
            journal("0000-0005", "Not Applicable", quartile="N/A"),
        ]

        result = query_db.apply_filters(journals, args)

        self.assertEqual(
            [item["name"] for item in result],
            ["Known Q1", "Known Q2"],
        )

    def test_legitimate_titles_are_not_removed_by_broad_patterns(self):
        args = Namespace(
            oa_only=False,
            max_apc=None,
            min_quartile=None,
            max_review_days=None,
            require_review_data=False,
        )
        journals = [
            journal("0000-0010", "Review of Economic Dynamics"),
            journal("0000-0011", "One Health Outlook"),
            journal("0000-0012", "The World Bank Economic Review"),
            journal("0000-0013", "IMF Working Paper"),
        ]

        result = query_db.apply_filters(journals, args)

        self.assertEqual(
            [item["name"] for item in result],
            [
                "Review of Economic Dynamics",
                "One Health Outlook",
                "The World Bank Economic Review",
            ],
        )

    def test_explicit_apc_cap_excludes_unknown_values(self):
        args = Namespace(
            oa_only=False,
            max_apc=2000,
            min_quartile=None,
            max_review_days=None,
            require_review_data=False,
        )
        known_low = journal("0000-0014", "Known Low APC")
        known_low["apc_usd"] = 1500
        known_high = journal("0000-0015", "Known High APC")
        known_high["apc_usd"] = 3000
        unknown = journal("0000-0016", "Unknown APC")
        unknown["apc_usd"] = None

        result = query_db.apply_filters(
            [known_low, known_high, unknown],
            args,
        )

        self.assertEqual([item["name"] for item in result], ["Known Low APC"])

    def test_semantic_only_completion_loads_database_once(self):
        all_journals = [
            journal("0000-0020", "First"),
            journal("0000-0021", "Second"),
        ]
        with patch.object(
            query_db,
            "load_database",
            return_value=all_journals,
        ) as load_database:
            result = query_db.merge_results(
                [],
                {"0000-0020": 0.9, "0000-0021": 0.8},
                top_n=2,
            )

        self.assertEqual(len(result), 2)
        load_database.assert_called_once_with("all")

    def test_semantic_union_reapplies_all_hard_filters(self):
        all_journals = [
            journal("0000-0100", "Allowed Journal"),
            journal("0000-0101", "Closed Journal", is_oa=False),
            journal("0000-0102", "Missing Review", review_days=None),
            journal("0000-0103", "Slow Review", review_days=240),
            journal("0000-0104", "Unknown Quartile", quartile=None),
            journal("0000-0105", "IMF Working Paper"),
        ]
        all_journals[0]["topics"] = [{"name": "Climate and Health"}]
        semantic_scores = {
            item["issn_l"]: 0.9 - index * 0.01
            for index, item in enumerate(all_journals)
        }
        argv = [
            "query_db.py",
            "--keywords",
            "climate",
            "--oa-only",
            "--min-quartile",
            "Q2",
            "--max-review-days",
            "120",
            "--require-review-data",
            "--top",
            "10",
        ]

        stdout = io.StringIO()
        with (
            patch.object(query_db, "load_database", return_value=all_journals),
            patch.object(
                query_db,
                "run_semantic_search",
                return_value=(semantic_scores, None),
            ),
            patch.object(sys, "argv", argv),
            redirect_stdout(stdout),
        ):
            query_db.main()

        output = json.loads(stdout.getvalue())
        self.assertEqual(
            [item["name"] for item in output["results"]],
            ["Allowed Journal"],
        )

    def test_top_must_be_positive(self):
        with self.assertRaises(ArgumentTypeError):
            query_db.positive_int("0")
        self.assertEqual(query_db.positive_int("3"), 3)


if __name__ == "__main__":
    unittest.main()

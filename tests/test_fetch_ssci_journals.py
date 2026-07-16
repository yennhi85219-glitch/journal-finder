import unittest

from scripts.fetch_ssci_journals import reconcile_existing_records


class ReconcileExistingRecordsTests(unittest.TestCase):
    def test_refreshes_matches_and_prunes_records_outside_current_whitelist(self):
        existing = [
            {
                "issn_l": "1111-1111",
                "issn": ["1111-1111", "2222-2222"],
                "name": "Print Match",
                "topics": [{"name": "Keep OpenAlex data"}],
                "_jcr_subject": "OLD SUBJECT",
                "_jcr_detail": "OLD DETAIL",
                "_source_scope": None,
            },
            {
                "issn_l": "4444-4444",
                "issn": ["4444-4444"],
                "name": "Electronic Match",
                "_jcr_subject": "OLD SUBJECT",
                "_jcr_detail": "OLD DETAIL",
                "_source_scope": "ssci_ahci",
            },
            {
                "issn_l": "9999-9999",
                "issn": ["9999-9999"],
                "name": "No Longer Whitelisted",
                "_source_scope": "scie_env_health",
            },
        ]
        jcr = [
            {
                "issn": "11111111",
                "eissn": "2222-2222",
                "subject": "ECONOMICS",
                "jcr_detail": "ECONOMICS(SSCI):Q1",
                "scope": "ssci_ahci",
            },
            {
                "issn": "3333-3333",
                "eissn": "44444444",
                "subject": "ENVIRONMENTAL SCIENCES",
                "jcr_detail": "ENVIRONMENTAL SCIENCES(SCIE):Q1",
                "scope": "scie_env_health",
            },
            {
                "issn": "5555-5555",
                "eissn": "6666-6666",
                "subject": "SOCIOLOGY",
                "jcr_detail": "SOCIOLOGY(SSCI):Q2",
                "scope": "ssci_ahci",
            },
        ]

        retained, missing, stats = reconcile_existing_records(existing, jcr)

        self.assertEqual([j["issn_l"] for j in retained], ["1111-1111", "4444-4444"])
        self.assertEqual(missing, [jcr[2]])
        self.assertEqual(
            stats,
            {"retained": 2, "refreshed": 2, "pruned": 1, "missing": 1},
        )
        self.assertEqual(retained[0]["topics"], [{"name": "Keep OpenAlex data"}])
        self.assertEqual(retained[0]["_jcr_subject"], "ECONOMICS")
        self.assertEqual(retained[0]["_jcr_detail"], "ECONOMICS(SSCI):Q1")
        self.assertEqual(retained[0]["_source_scope"], "ssci_ahci")
        self.assertEqual(retained[1]["_source_scope"], "scie_env_health")
        self.assertEqual(existing[0]["_jcr_subject"], "OLD SUBJECT")

    def test_unchanged_jcr_metadata_is_retained_without_refresh(self):
        existing = [
            {
                "issn_l": "1111-1111",
                "issn": ["1111-1111"],
                "_jcr_subject": "ECONOMICS",
                "_jcr_detail": "ECONOMICS(SSCI):Q1",
                "_source_scope": "ssci_ahci",
            }
        ]
        jcr = [
            {
                "issn": "1111-1111",
                "eissn": None,
                "subject": "ECONOMICS",
                "jcr_detail": "ECONOMICS(SSCI):Q1",
                "scope": "ssci_ahci",
            }
        ]

        retained, missing, stats = reconcile_existing_records(existing, jcr)

        self.assertEqual(len(retained), 1)
        self.assertEqual(missing, [])
        self.assertEqual(
            stats,
            {"retained": 1, "refreshed": 0, "pruned": 0, "missing": 0},
        )


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path

from src.context import load_context, validate_context

FIXTURE = Path("fixtures/context.json")


def fixture_data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ContextTest(unittest.TestCase):
    def test_fixture_matches_domain(self):
        data = load_context(FIXTURE)
        self.assertEqual(data["domain"], "canal-heritage-monitoring")
        self.assertGreaterEqual(len(data["facts"]), 1)

    def test_fixture_lists_sites_and_observations(self):
        data = load_context(FIXTURE)
        self.assertGreaterEqual(len(data["heritage_sites"]), 4)
        self.assertGreaterEqual(len(data["observations"]), 1)


class DomainRuleTest(unittest.TestCase):
    def setUp(self):
        self.data = fixture_data()

    def test_each_site_type_needs_own_threshold(self):
        self.data["threshold_profiles"] = [
            p for p in self.data["threshold_profiles"] if p["site_type"] != "会馆"
        ]
        self.data["observations"] = [
            o for o in self.data["observations"] if o["site_id"] != "site-02"
        ]
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_observation_cannot_use_other_type_threshold(self):
        self.data["observations"][0]["profile_id"] = "th-shuigong-crack"
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_archive_must_cover_five_dimensions(self):
        del self.data["heritage_sites"][0]["archive"][0]
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_original_state_must_be_immutable(self):
        self.data["heritage_sites"][0]["archive"][0]["original_state"]["immutable"] = False
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_chain_stages_must_follow_pipeline_order(self):
        stages = self.data["evidence_chains"][0]["stages"]
        stages[0], stages[1] = stages[1], stages[0]
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_revised_record_must_name_superseded_version(self):
        self.data["evidence_chains"][0]["stages"][0]["supersedes"] = None
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_chain_stages_must_link_to_upstream_record(self):
        self.data["evidence_chains"][0]["stages"][2]["based_on"] = "mon-site01"
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_original_state_never_overwritten(self):
        self.data["interventions"][0]["original_state_overwritten"] = True
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_emergency_intervention_must_enter_followup_review(self):
        self.data["interventions"][0]["review_status"] = "已批准"
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_non_emergency_cannot_execute_before_review(self):
        self.data["interventions"][1]["executed_before_review"] = True
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_decision_must_include_all_factors(self):
        del self.data["decisions"][0]["factors"]["resident_privacy"]
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_guide_must_cite_latest_approved_version(self):
        self.data["guide_citations"][0]["version"] = 2
        with self.assertRaises(ValueError):
            validate_context(self.data)

    def test_guide_cannot_cite_unapproved_interpretation(self):
        self.data["guide_citations"].append(
            {"citation_id": "cite-03", "interpretation_id": "exp-site04", "version": 2}
        )
        with self.assertRaises(ValueError):
            validate_context(self.data)


if __name__ == "__main__":
    unittest.main()

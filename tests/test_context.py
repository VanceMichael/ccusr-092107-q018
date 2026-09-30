import json
import unittest
from pathlib import Path

from src.context import (
    DECISION_FACTORS,
    STAGE_ORDER,
    classify_reading,
    current_model,
    latest_approved_version,
    load_context,
    validate_context,
)

FIXTURE = Path("fixtures/context.json")


def raw_context() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def find_archive(data: dict, archive_id: str) -> dict:
    return next(a for a in data["archives"] if a["id"] == archive_id)


def find_version(archive: dict, number: int) -> dict:
    return next(v for v in archive["versions"] if v["version"] == number)


class FixtureTest(unittest.TestCase):
    def test_fixture_matches_domain(self):
        data = load_context(FIXTURE)
        self.assertEqual(data["domain"], "canal-heritage-monitoring")
        self.assertGreaterEqual(len(data["facts"]), 6)

    def test_four_point_types_carry_distinct_profiles(self):
        data = load_context(FIXTURE)
        types = {p["point_type"] for p in data["heritage_points"]}
        self.assertEqual(types, {"钞关", "会馆", "水工遗址", "古镇民居"})
        # 同一读数 2.1mm 裂缝在四类阈值下分级不同，证明不能共用一套阈值
        value = 2.1
        results = {
            "hp-customs-01": classify_reading(data, "hp-customs-01", "裂缝宽度", value),
            "hp-guild-01": classify_reading(data, "hp-guild-01", "裂缝宽度", value),
            "hp-sluice-01": classify_reading(data, "hp-sluice-01", "裂缝宽度", value),
            "hp-town-01": classify_reading(data, "hp-town-01", "裂缝宽度", value),
        }
        self.assertEqual(
            results,
            {
                "hp-customs-01": "预警",
                "hp-guild-01": "处置",
                "hp-sluice-01": "正常",
                "hp-town-01": "预警",
            },
        )

    def test_hydraulic_only_point_uses_water_level_indicator(self):
        data = load_context(FIXTURE)
        with self.assertRaises(ValueError):
            classify_reading(data, "hp-guild-01", "水位变幅", 1.5)
        self.assertEqual(
            classify_reading(data, "hp-sluice-01", "水位变幅", 2.5), "处置"
        )

    def test_only_dwellings_may_be_inhabited(self):
        data = raw_context()
        data["heritage_points"][0]["inhabited"] = True
        self.assertTrue(validate_context(data))


class ArchiveChainTest(unittest.TestCase):
    def test_customs_archive_covers_all_six_stages_in_order(self):
        data = load_context(FIXTURE)
        archive = find_archive(data, "ar-customs-northwall")
        self.assertEqual([v["stage"] for v in archive["versions"][:6]], STAGE_ORDER)

    def test_original_state_record_can_never_be_voided_or_overwrite(self):
        data = raw_context()
        archive = find_archive(data, "ar-customs-northwall")
        find_version(archive, 1)["status"] = "作废"
        errors = validate_context(data)
        self.assertTrue(any("原状资料" in e for e in errors))

    def test_stage_regression_rejected(self):
        data = raw_context()
        archive = find_archive(data, "ar-customs-northwall")
        find_version(archive, 5)["stage"] = "监测"
        errors = validate_context(data)
        self.assertTrue(any("阶段回退" in e for e in errors))

    def test_emergency_may_act_first_but_must_be_ratified_later(self):
        data = load_context(FIXTURE)
        sluice = find_archive(data, "ar-sluice-pier")
        emergency_version = find_version(sluice, 4)
        self.assertEqual(emergency_version["status"], "先处置待补审")
        self.assertTrue(emergency_version["emergency"]["action_first"])
        # 补审版本与紧急版本必须相互指向
        self.assertEqual(
            find_version(sluice, 5).get("ratifies_version"), 4
        )
        self.assertEqual(
            emergency_version["emergency"]["ratified_by_version"], 5
        )

    def test_emergency_status_without_emergency_flag_rejected(self):
        data = raw_context()
        sluice = find_archive(data, "ar-sluice-pier")
        find_version(sluice, 4)["emergency"] = None
        self.assertTrue(any("紧急处置" in e for e in validate_context(data)))

    def test_ratification_pair_must_point_back(self):
        data = raw_context()
        sluice = find_archive(data, "ar-sluice-pier")
        find_version(sluice, 5)["ratifies_version"] = 3
        errors = validate_context(data)
        self.assertTrue(any("未相互指向" in e for e in errors))

    def test_pending_ratification_is_allowed(self):
        # 古镇民居紧急支顶补审尚在办理（ratified_by_version=null），资料仍有效
        data = load_context(FIXTURE)
        town = find_archive(data, "ar-town-dwelling")
        self.assertIsNone(
            find_version(town, 3)["emergency"]["ratified_by_version"]
        )


class VersionAndGuideTest(unittest.TestCase):
    def test_latest_approved_version(self):
        data = load_context(FIXTURE)
        latest = latest_approved_version(data, "ar-customs-northwall")
        self.assertEqual(latest["version"], 6)
        self.assertEqual(latest["stage"], "开放条件")

    def test_published_guide_must_cite_latest_approved(self):
        data = load_context(FIXTURE)
        guide = next(g for g in data["public_guides"] if g["id"] == "gd-001")
        self.assertEqual(guide["interpretation_ref"], "ar-customs-northwall#6")

    def test_published_guide_citing_stale_approval_rejected(self):
        data = raw_context()
        next(g for g in data["public_guides"] if g["id"] == "gd-001")[
            "interpretation_ref"
        ] = "ar-customs-northwall#5"
        errors = validate_context(data)
        self.assertTrue(any("最新批准版本" in e for e in errors))

    def test_published_guide_citing_unapproved_rejected(self):
        data = raw_context()
        guide = next(g for g in data["public_guides"] if g["id"] == "gd-001")
        guide["interpretation_ref"] = "ar-sluice-pier#4"
        errors = validate_context(data)
        self.assertTrue(any("未批准" in e for e in errors))

    def test_draft_guide_may_wait_for_approval(self):
        data = load_context(FIXTURE)
        draft = next(g for g in data["public_guides"] if g["id"] == "gd-002")
        self.assertEqual(draft["status"], "草稿")


class InterventionDecisionTest(unittest.TestCase):
    def _intervention(self, data: dict, intervention_id: str) -> dict:
        return next(
            i for i in data["interventions"] if i["id"] == intervention_id
        )

    def test_all_six_contention_factors_present(self):
        data = load_context(FIXTURE)
        for intervention in data["interventions"]:
            factors = {f["factor"] for f in intervention["decision"]["factors"]}
            self.assertEqual(factors, set(DECISION_FACTORS))

    def test_dropping_one_factor_rejected(self):
        data = raw_context()
        intervention = self._intervention(data, "iv-customs-tie")
        intervention["decision"]["factors"] = [
            f
            for f in intervention["decision"]["factors"]
            if f["factor"] != "居民隐私"
        ]
        errors = validate_context(data)
        self.assertTrue(any("决策因素" in e for e in errors))

    def test_intervention_must_rest_on_approved_diagnosis(self):
        data = raw_context()
        intervention = self._intervention(data, "iv-customs-tie")
        intervention["based_on_refs"] = ["ar-customs-northwall#2"]  # 巡查证据
        errors = validate_context(data)
        self.assertTrue(any("病害判断" in e for e in errors))

    def test_upstream_downstream_must_follow_chain_position(self):
        data = raw_context()
        intervention = self._intervention(data, "iv-sluice-brace")
        # 钞关在闸址上游，错记成下游必须被拦截
        intervention["water_environment"]["upstream_point_ids"] = []
        intervention["water_environment"]["downstream_point_ids"] = [
            "hp-customs-01",
            "hp-town-01",
        ]
        errors = validate_context(data)
        self.assertTrue(any("记为下游" in e for e in errors))

    def test_historical_model_cannot_support_decision(self):
        data = raw_context()
        self._intervention(data, "iv-customs-tie")["model_id"] = "m3d-customs-01"
        errors = validate_context(data)
        self.assertTrue(any("历史模型" in e for e in errors))

    def test_current_model_tracks_version_swap(self):
        data = load_context(FIXTURE)
        self.assertEqual(current_model(data, "hp-customs-01")["id"], "m3d-customs-02")

    def test_model_supersession_chain(self):
        data = raw_context()
        old = next(m for m in data["models_3d"] if m["id"] == "m3d-customs-01")
        old["status"] = "现行"  # 出现两个现行版本
        errors = validate_context(data)
        self.assertTrue(any("两个现行" in e for e in errors))


class ObservationTest(unittest.TestCase):
    def test_reading_unit_must_match_profile(self):
        data = raw_context()
        data["observation_samples"][0]["readings"][0]["unit"] = "cm"
        errors = validate_context(data)
        self.assertTrue(any("单位" in e for e in errors))

    def test_indicator_outside_profile_rejected(self):
        data = raw_context()
        data["observation_samples"][1]["readings"][0]["indicator"] = "水位变幅"
        errors = validate_context(data)
        self.assertTrue(any("阈值内" in e for e in errors))

    def test_threshold_ordering_enforced(self):
        data = raw_context()
        data["threshold_profiles"][0]["disease_thresholds"][0]["warning_max"] = 0.1
        errors = validate_context(data)
        self.assertTrue(any("正常≤预警≤处置" in e for e in errors))


if __name__ == "__main__":
    unittest.main()

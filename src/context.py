"""读取并检查项目领域资料。"""

import json
from pathlib import Path

REQUIRED_FIELDS = {
    "domain",
    "version",
    "facts",
    "sample_id",
    "threshold_profiles",
    "heritage_sites",
    "observations",
    "evidence_chains",
    "interventions",
    "decisions",
    "interpretations",
    "guide_citations",
}

ARCHIVE_DIMENSIONS = ("建筑", "构件", "碑刻", "材质", "周边环境")

PIPELINE_STAGES = ("监测", "巡查证据", "病害判断", "干预方案", "施工与开放条件")

DECISION_FACTORS = (
    "expert_contention",
    "budget_contention",
    "upstream_downstream_impact",
    "model_version_change",
    "resident_privacy",
    "visitor_volume_change",
)

EMERGENCY_REVIEW_STATUSES = ("待补审", "已补审")


def load_context(path: Path) -> dict:
    """返回字段完整且通过领域规则校验的资料。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_context(data)
    return data


def validate_context(data: dict) -> None:
    """校验领域资料的结构与跨记录约束，发现问题时抛出 ValueError。"""
    if not REQUIRED_FIELDS.issubset(data):
        raise ValueError("领域资料缺少必要字段")
    site_ids = {site["site_id"] for site in data["heritage_sites"]}
    _check_thresholds(data, site_ids)
    _check_archives(data)
    _check_observations(data, site_ids)
    _check_chains(data, site_ids)
    _check_interventions(data, site_ids)
    _check_decisions(data, site_ids)
    _check_citations(data)


def _check_thresholds(data: dict, site_ids: set) -> None:
    profiles = data["threshold_profiles"]
    profile_ids = [profile["profile_id"] for profile in profiles]
    if len(profile_ids) != len(set(profile_ids)):
        raise ValueError("病害阈值编号重复")
    for profile in profiles:
        if not profile["warning"] < profile["critical"]:
            raise ValueError("预警阈值必须低于危急阈值")
    covered_types = {profile["site_type"] for profile in profiles}
    for site in data["heritage_sites"]:
        if site["site_type"] not in covered_types:
            raise ValueError(
                f"遗产点{site['site_id']}缺少{site['site_type']}的专属病害阈值"
            )


def _check_archives(data: dict) -> None:
    for site in data["heritage_sites"]:
        dimensions = [entry["dimension"] for entry in site["archive"]]
        if sorted(dimensions) != sorted(ARCHIVE_DIMENSIONS):
            raise ValueError(f"遗产点{site['site_id']}的档案必须覆盖五个维度")
        for entry in site["archive"]:
            original = entry["original_state"]
            if original["version"] != 1 or original["immutable"] is not True:
                raise ValueError("原状资料必须保留首版且标记为不可覆盖")
            if entry["latest_version"] < original["version"]:
                raise ValueError("档案最新版本不能早于原状首版")


def _check_observations(data: dict, site_ids: set) -> None:
    profiles = {p["profile_id"]: p for p in data["threshold_profiles"]}
    sites = {s["site_id"]: s for s in data["heritage_sites"]}
    for obs in data["observations"]:
        if obs["site_id"] not in site_ids or obs["profile_id"] not in profiles:
            raise ValueError("观察样例引用了不存在的遗产点或病害阈值")
        if profiles[obs["profile_id"]]["site_type"] != sites[obs["site_id"]]["site_type"]:
            raise ValueError(f"观察样例{obs['observation_id']}套用了其他类型遗产的阈值")


def _check_chains(data: dict, site_ids: set) -> None:
    for chain in data["evidence_chains"]:
        if chain["site_id"] not in site_ids:
            raise ValueError("证据链引用了不存在的遗产点")
        stages = chain["stages"]
        if tuple(stage["stage"] for stage in stages) != PIPELINE_STAGES:
            raise ValueError("证据链阶段必须按监测到施工与开放条件的顺序衔接")
        for index, stage in enumerate(stages):
            has_previous = stage["supersedes"] is not None
            if has_previous != (stage["version"] > 1):
                raise ValueError("只有换版记录才需要指明被接替的版本")
            upstream = stages[index - 1]["record_id"] if index else None
            if stage["based_on"] != upstream:
                raise ValueError("证据链相邻阶段之间未逐版衔接")


def _check_interventions(data: dict, site_ids: set) -> None:
    original_refs = {
        entry["original_state"]["record_id"]
        for site in data["heritage_sites"]
        for entry in site["archive"]
    }
    for plan in data["interventions"]:
        if plan["site_id"] not in site_ids:
            raise ValueError("干预方案引用了不存在的遗产点")
        if plan["original_state_overwritten"]:
            raise ValueError("原状资料永远不能被覆盖")
        if plan["original_state_ref"] not in original_refs:
            raise ValueError("干预方案必须锚定保留中的原状资料")
        if plan["executed_before_review"]:
            if not plan["emergency"]:
                raise ValueError("只有紧急加固允许先处置后补审")
            if plan["review_status"] not in EMERGENCY_REVIEW_STATUSES:
                raise ValueError("紧急处置后必须进入补审流程")


def _check_decisions(data: dict, site_ids: set) -> None:
    for decision in data["decisions"]:
        if decision["site_id"] not in site_ids:
            raise ValueError("决策引用了不存在的遗产点")
        missing = [name for name in DECISION_FACTORS if name not in decision["factors"]]
        if missing:
            raise ValueError(
                f"决策{decision['decision_id']}缺少因素：{'、'.join(missing)}"
            )


def _check_citations(data: dict) -> None:
    latest_approved = {}
    for item in data["interpretations"]:
        if item["status"] == "已批准":
            latest = latest_approved.get(item["interpretation_id"], 0)
            latest_approved[item["interpretation_id"]] = max(latest, item["version"])
    for citation in data["guide_citations"]:
        approved = latest_approved.get(citation["interpretation_id"])
        if approved is None:
            raise ValueError("导览引用的解释缺少已批准版本")
        if citation["version"] != approved:
            raise ValueError("公众导览必须引用最新批准版本的解释")

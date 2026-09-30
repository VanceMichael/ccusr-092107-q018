"""读取并校验运河古建联防监测领域资料。

只依赖标准库。``load_context`` 在结构或领域不变量被破坏时抛出
``ValueError``；查询助手供后续业务服务沿用同一套领域标识与版本约定。
"""

import json
from pathlib import Path

# 六阶段顺序：档案版本只允许沿此顺序向后衔接，不得回退
STAGE_ORDER = ["监测", "巡查证据", "病害判断", "干预方案", "施工", "开放条件"]
# 省级档案必须覆盖的五类对象
ARCHIVE_CATEGORIES = ["建筑", "构件", "碑刻", "材质", "周边环境"]
# 干预决策必须逐项纳入的六类争用因素
DECISION_FACTORS = [
    "专家争用",
    "预算争用",
    "上下游影响",
    "三维模型换版",
    "居民隐私",
    "游客量变化",
]

_REQUIRED_TOP_LEVEL = {
    "domain",
    "version",
    "facts",
    "sample_id",
    "heritage_points",
    "threshold_profiles",
    "observation_samples",
    "archives",
    "interventions",
    "models_3d",
    "public_guides",
}


def load_context(path: Path) -> dict:
    """返回通过全部结构与领域不变量校验的领域资料。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = _REQUIRED_TOP_LEVEL - data.keys()
    if missing:
        raise ValueError(f"领域资料缺少必要字段: {sorted(missing)}")
    errors = validate_context(data)
    if errors:
        raise ValueError("领域资料校验失败: " + "；".join(errors))
    return data


def validate_context(data: dict) -> list[str]:
    """返回全部违例说明；为空表示资料可用。"""
    errors: list[str] = []

    points = {p["id"]: p for p in data["heritage_points"]}
    if len(points) != len(data["heritage_points"]):
        errors.append("遗产点编号重复")

    profiles = {t["id"]: t for t in data["threshold_profiles"]}
    if len(profiles) != len(data["threshold_profiles"]):
        errors.append("阈值档案编号重复")

    _validate_points(points, profiles, errors)
    _validate_profiles(profiles, errors)
    _validate_samples(data["observation_samples"], points, profiles, errors)
    sample_ids = {s["id"] for s in data["observation_samples"]}
    archives = _validate_archives(data["archives"], points, sample_ids, errors)
    _validate_models(data["models_3d"], points, errors)
    _validate_interventions(data["interventions"], points, archives, errors)
    _validate_intervention_models(data, errors)
    _validate_guides(data["public_guides"], points, archives, errors)

    return errors


# ---------------------------------------------------------------- 阈值分级

def threshold_profile_for(data: dict, point_id: str) -> dict:
    """返回遗产点当前适用的差异化阈值档案。"""
    point = next(p for p in data["heritage_points"] if p["id"] == point_id)
    return next(
        t for t in data["threshold_profiles"] if t["id"] == point["threshold_profile_id"]
    )


def classify_reading(data: dict, point_id: str, indicator: str, value: float) -> str:
    """按该点所属类型的阈值判定 正常/预警/处置。

    钞关、会馆、水工遗址、古镇民居阈值不同，绝不跨类型套用。
    """
    profile = threshold_profile_for(data, point_id)
    rule = next(
        (t for t in profile["disease_thresholds"] if t["indicator"] == indicator), None
    )
    if rule is None:
        raise ValueError(f"{point_id} 的阈值未覆盖指标 {indicator}")
    if value <= rule["normal_max"]:
        return "正常"
    if value <= rule["warning_max"]:
        return "预警"
    return "处置"


# ---------------------------------------------------------------- 版本查询

def parse_ref(ref: str) -> tuple[str, int | None]:
    """解析 ar-x#3 / os-001 形式的引用，返回 (档案或样例编号, 版本号)。"""
    if "#" in ref:
        archive_id, _, version_text = ref.partition("#")
        return archive_id, int(version_text)
    return ref, None


def get_version(data: dict, ref: str) -> dict:
    """按 ar-x#版本号 取指定档案版本。"""
    archive_id, number = parse_ref(ref)
    if number is None:
        raise ValueError(f"档案引用必须带版本号: {ref}")
    archive = next(
        (a for a in data["archives"] if a["id"] == archive_id), None
    )
    if archive is None:
        raise ValueError(f"档案不存在: {archive_id}")
    version = next((v for v in archive["versions"] if v["version"] == number), None)
    if version is None:
        raise ValueError(f"档案 {archive_id} 不存在版本 {number}")
    return version


def latest_approved_version(data: dict, archive_id: str) -> dict:
    """返回档案最新的有效批准版本——公众导览只允许引用它。"""
    archive = next((a for a in data["archives"] if a["id"] == archive_id), None)
    if archive is None:
        raise ValueError(f"档案不存在: {archive_id}")
    approved = [v for v in archive["versions"] if v["status"] == "已批准"]
    if not approved:
        raise ValueError(f"档案 {archive_id} 尚无已批准版本")
    return max(approved, key=lambda v: v["version"])


def current_model(data: dict, point_id: str) -> dict | None:
    """返回该遗产点现行三维模型；旧版模型仅供留存，不得用于决策。"""
    candidates = [
        m
        for m in data["models_3d"]
        if m["heritage_point_id"] == point_id and m["status"] == "现行"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda m: m["model_version"])


# ---------------------------------------------------------------- 逐项校验

def _validate_points(points: dict, profiles: dict, errors: list[str]) -> None:
    types_seen = {p["point_type"] for p in points.values()}
    missing_types = {"钞关", "会馆", "水工遗址", "古镇民居"} - types_seen
    if missing_types:
        errors.append(f"遗产点类型不全: {sorted(missing_types)}")
    for point in points.values():
        profile = profiles.get(point["threshold_profile_id"])
        if profile is None:
            errors.append(f"{point['id']} 引用了不存在的阈值档案 {point['threshold_profile_id']}")
        elif profile["applies_to_type"] != point["point_type"]:
            errors.append(
                f"{point['id']} 是{point['point_type']}却套用{profile['applies_to_type']}阈值"
            )
        if point["inhabited"] and point["point_type"] != "古镇民居":
            errors.append(f"{point['id']} 非古镇民居不允许标记为有人居住")


def _validate_profiles(profiles: dict, errors: list[str]) -> None:
    profile_types = list(profiles.values())
    covered = {t["applies_to_type"] for t in profile_types}
    missing = {"钞关", "会馆", "水工遗址", "古镇民居"} - covered
    if missing:
        errors.append(f"阈值档案未覆盖类型: {sorted(missing)}")
    for profile in profile_types:
        for rule in profile["disease_thresholds"]:
            if not (
                rule["normal_max"] <= rule["warning_max"] <= rule["action_max"]
            ):
                errors.append(
                    f"阈值档案 {profile['id']} 指标 {rule['indicator']} 阈值不满足 正常≤预警≤处置"
                )


def _validate_samples(
    samples: list[dict], points: dict, profiles: dict, errors: list[str]
) -> None:
    sample_ids = [s["id"] for s in samples]
    if len(sample_ids) != len(set(sample_ids)):
        errors.append("观察样例编号重复")
    for sample in samples:
        point = points.get(sample["heritage_point_id"])
        if point is None:
            errors.append(f"{sample['id']} 引用了不存在的遗产点 {sample['heritage_point_id']}")
            continue
        profile = profiles[point["threshold_profile_id"]]
        indicators = {t["indicator"]: t["unit"] for t in profile["disease_thresholds"]}
        for reading in sample["readings"]:
            if reading["indicator"] not in indicators:
                errors.append(
                    f"{sample['id']} 指标 {reading['indicator']} 不在{point['point_type']}阈值内"
                )
            elif reading["unit"] != indicators[reading["indicator"]]:
                errors.append(
                    f"{sample['id']} 指标 {reading['indicator']} 单位与阈值档案不一致"
                )


def _validate_archives(
    archives: list[dict], points: dict, sample_ids: set[str], errors: list[str]
) -> dict:
    archive_map: dict[str, dict] = {}
    valid_refs: set[str] = set()

    if len({a["id"] for a in archives}) != len(archives):
        errors.append("档案编号重复")

    categories_seen: set[str] = set()
    stages_seen: set[str] = set()

    for archive in archives:
        point = points.get(archive["heritage_point_id"])
        if point is None:
            errors.append(f"{archive['id']} 引用了不存在的遗产点 {archive['heritage_point_id']}")
        categories_seen.add(archive["category"])
        numbers = [v["version"] for v in archive["versions"]]
        if numbers != list(range(1, len(numbers) + 1)):
            errors.append(f"{archive['id']} 版本号必须从1连续递增且不得插队覆盖")
        for version in archive["versions"]:
            ref = f"{archive['id']}#{version['version']}"
            valid_refs.add(ref)
            stages_seen.add(version["stage"])

        prev_stage_index = -1
        for version in archive["versions"]:
            stage_index = STAGE_ORDER.index(version["stage"])
            if stage_index < prev_stage_index:
                errors.append(f"{ref} 阶段回退，六阶段只能逐版向后衔接")
            prev_stage_index = stage_index

            # 原状资料：只准追加，永远不能被覆盖或作废
            if version["is_original_state_record"]:
                if version["status"] != "已批准":
                    errors.append(f"{ref} 是原状资料，不允许处于 {version['status']} 状态")
                if version["supersedes_version"] is not None:
                    errors.append(f"{ref} 是原状资料，不允许声明覆盖任何旧版本")

            if version["supersedes_version"] is not None:
                if version["supersedes_version"] not in numbers:
                    errors.append(f"{ref} 声明覆盖的版本不存在")
                elif version["supersedes_version"] >= version["version"]:
                    errors.append(f"{ref} 只能覆盖更早版本")

            emergency = version.get("emergency")
            if emergency and emergency["action_first"]:
                if version["status"] != "先处置待补审":
                    errors.append(f"{ref} 先处置版本状态必须为 先处置待补审")
            elif version["status"] == "先处置待补审":
                errors.append(f"{ref} 缺少紧急处置说明")

            if version.get("ratifies_version") is not None:
                if version["status"] != "已批准":
                    errors.append(f"{ref} 补审追认版本自身必须已批准")
                if version["ratifies_version"] not in numbers:
                    errors.append(f"{ref} 追认的紧急版本不存在")

        # 双向核对：紧急版本指定的追认版本必须真的追认回来
        versions_by_number = {v["version"]: v for v in archive["versions"]}
        for number, version in versions_by_number.items():
            emergency = version.get("emergency")
            if emergency and emergency["action_first"]:
                ratified_by = emergency["ratified_by_version"]
                if ratified_by is not None:
                    ratifier = versions_by_number.get(ratified_by)
                    if ratifier is None:
                        errors.append(f"{archive['id']}#{number} 指定的补审版本不存在")
                    elif ratifier.get("ratifies_version") != number:
                        errors.append(
                            f"{archive['id']}#{number} 与补审版本 {ratified_by} 未相互指向"
                        )

        archive_map[archive["id"]] = archive

    missing_categories = set(ARCHIVE_CATEGORIES) - categories_seen
    if missing_categories:
        errors.append(f"省级档案缺少分类: {sorted(missing_categories)}")
    missing_stages = set(STAGE_ORDER) - stages_seen
    if missing_stages:
        errors.append(f"档案版本未覆盖全部阶段: {sorted(missing_stages)}")

    # 证据引用完整性（在全部档案编号建立后核对）
    for archive in archives:
        for version in archive["versions"]:
            for ref in version["evidence_refs"]:
                target_id, number = parse_ref(ref)
                if target_id.startswith("os-"):
                    if ref not in sample_ids:
                        errors.append(
                            f"{archive['id']}#{version['version']} 引用了不存在的观察样例 {ref}"
                        )
                elif ref not in valid_refs:
                    errors.append(f"{archive['id']}#{version['version']} 引用了不存在的 {ref}")

    return archive_map


def _validate_models(models: list[dict], points: dict, errors: list[str]) -> None:
    if len({m["id"] for m in models}) != len(models):
        errors.append("三维模型编号重复")
    by_id = {m["id"]: m for m in models}
    for model in models:
        if model["heritage_point_id"] not in points:
            errors.append(f"{model['id']} 引用了不存在的遗产点")
        if model["supersedes_id"] is not None:
            older = by_id.get(model["supersedes_id"])
            if older is None:
                errors.append(f"{model['id']} 声明替换的模型不存在")
            else:
                if older["heritage_point_id"] != model["heritage_point_id"]:
                    errors.append(f"{model['id']} 跨遗产点替换模型")
                if older["model_version"] != model["model_version"] - 1:
                    errors.append(f"{model['id']} 只能替换紧邻上一版模型")
                if older["status"] != "历史":
                    errors.append(f"被替换的 {older['id']} 必须转为历史版本")
    # 每个遗产点至多一个现行模型
    current: dict[str, str] = {}
    for model in models:
        if model["status"] == "现行":
            if model["heritage_point_id"] in current:
                errors.append(
                    f"{model['heritage_point_id']} 同时存在两个现行三维模型"
                )
            current[model["heritage_point_id"]] = model["id"]


def _validate_interventions(
    interventions: list[dict],
    points: dict,
    archives: dict[str, dict],
    errors: list[str],
) -> None:
    if len({i["id"] for i in interventions}) != len(interventions):
        errors.append("干预方案编号重复")

    for intervention in interventions:
        point_id = intervention["heritage_point_id"]
        point = points.get(point_id)
        if point is None:
            errors.append(f"{intervention['id']} 引用了不存在的遗产点")
            continue

        # 干预必须建立在已批准的病害判断版本之上
        for ref in intervention["based_on_refs"]:
            archive_id, number = parse_ref(ref)
            archive = archives.get(archive_id)
            if archive is None:
                errors.append(f"{intervention['id']} 依据 {ref} 不存在")
                continue
            version = next(
                (v for v in archive["versions"] if v["version"] == number), None
            )
            if version is None:
                errors.append(f"{intervention['id']} 依据 {ref} 不存在")
            elif version["status"] != "已批准" or version["stage"] != "病害判断":
                errors.append(
                    f"{intervention['id']} 只能依据已批准的病害判断版本，{ref} 不符合"
                )

        model_id = intervention.get("model_id")
        # 模型归属与现行状态的跨表核对在 _validate_intervention_models 中统一完成

        # 上下游必须与桩号一致，且影响评估要双向覆盖
        water = intervention["water_environment"]
        for upstream_id in water["upstream_point_ids"]:
            other = points.get(upstream_id)
            if other is None:
                errors.append(f"{intervention['id']} 上游点 {upstream_id} 不存在")
            elif other["chain_position_km"] >= point["chain_position_km"]:
                errors.append(f"{intervention['id']} 误把下游点 {upstream_id} 记为上游")
        for downstream_id in water["downstream_point_ids"]:
            other = points.get(downstream_id)
            if other is None:
                errors.append(f"{intervention['id']} 下游点 {downstream_id} 不存在")
            elif other["chain_position_km"] <= point["chain_position_km"]:
                errors.append(f"{intervention['id']} 误把上游点 {downstream_id} 记为下游")

        for affected_id in intervention["opening_arrangement"]["affected_point_ids"]:
            if affected_id not in points:
                errors.append(f"{intervention['id']} 开放安排涉及不存在的点 {affected_id}")

        # 六项争用因素逐项进入决策，缺项、多项、重复都不允许
        factor_names = [f["factor"] for f in intervention["decision"]["factors"]]
        if sorted(factor_names) != sorted(DECISION_FACTORS):
            errors.append(
                f"{intervention['id']} 决策因素必须恰为六项: {DECISION_FACTORS}"
            )


def _validate_intervention_models(
    data: dict, errors: list[str]
) -> None:
    """干预引用的三维模型必须属于本点且为现行版本。"""
    models_by_id = {m["id"]: m for m in data["models_3d"]}
    for intervention in data["interventions"]:
        model_id = intervention.get("model_id")
        if model_id is None:
            continue
        model = models_by_id.get(model_id)
        if model is None:
            errors.append(f"{intervention['id']} 引用了不存在的模型 {model_id}")
        elif model["heritage_point_id"] != intervention["heritage_point_id"]:
            errors.append(f"{intervention['id']} 引用了其他遗产点的模型 {model_id}")
        elif model["status"] != "现行":
            errors.append(f"{intervention['id']} 不允许依据历史模型 {model_id} 决策")


def _validate_guides(
    guides: list[dict], points: dict, archives: dict[str, dict], errors: list[str]
) -> None:
    if len({g["id"] for g in guides}) != len(guides):
        errors.append("公众导览编号重复")
    for guide in guides:
        point = points.get(guide["heritage_point_id"])
        if point is None:
            errors.append(f"{guide['id']} 引用了不存在的遗产点")
        archive_id, number = parse_ref(guide["interpretation_ref"])
        archive = archives.get(archive_id)
        if archive is None:
            errors.append(f"{guide['id']} 引用的解释来源 {archive_id} 不存在")
            continue
        if point is not None and archive["heritage_point_id"] != guide["heritage_point_id"]:
            errors.append(f"{guide['id']} 引用了其他遗产点档案的解释")
        version = next((v for v in archive["versions"] if v["version"] == number), None)
        if version is None:
            errors.append(f"{guide['id']} 引用的 {guide['interpretation_ref']} 版本不存在")
            continue
        # 草稿可暂挂未定版本；一旦发布，解释必须来自最新有效批准版本
        if guide["status"] == "已发布":
            if version["status"] != "已批准":
                errors.append(f"{guide['id']} 已发布却引用未批准版本 {guide['interpretation_ref']}")
            latest = max(
                v["version"] for v in archive["versions"] if v["status"] == "已批准"
            )
            if number != latest:
                errors.append(
                    f"{guide['id']} 引用的解释不是最新批准版本（应为 {archive_id}#{latest}）"
                )

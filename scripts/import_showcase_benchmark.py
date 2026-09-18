"""Import the versioned real-job benchmark from the archived showcase report."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "showcase" / "report-run-843296ac.md"
DATASET_DIR = ROOT / "evaluations" / "real-job-benchmark-v1"

RUN_ID = "run-843296ac"
COLLECTED_AT = "2026-09-09T08:36:43Z"
ANNOTATED_AT = "2026-09-18T00:00:00Z"
ANNOTATED_BY = "codex-manual-review-2026-09-18"

RELEVANT_CASES = {
    1,
    10,
    27,
    28,
    31,
    34,
}
TOP_K = {
    1: 1,
    31: 2,
    28: 3,
    10: 4,
    34: 5,
    27: 6,
}

COMPANY_SLUGS = {
    "腾讯": "tencent",
    "小米": "xiaomi",
    "网易": "netease",
    "小红书": "xiaohongshu",
    "米哈游": "mihoyo",
    "小鹏汽车": "xiaopeng",
    "智元机器人": "agibot",
    "银河通用": "galbot",
    "星动纪元": "robotera",
    "傅利叶智能": "fourier",
    "京东": "jd",
    "华为": "huawei",
    "蔚来": "nio",
    "理想汽车": "lixiang",
    "优必选": "ubtech",
    "众擎机器人": "engineai",
    "星海图": "galaxea",
    "它石智航": "tars",
    "自变量机器人": "x-square",
    "逐际动力": "limxdynamics",
    "智平方": "ai2robotics",
    "星尘智能": "astribot",
    "原力灵机": "dexmal",
    "加速进化": "booster",
    "智谱AI": "zhipu",
    "MiniMax": "minimax",
    "阶跃星辰": "step",
    "百川智能": "baichuan",
    "面壁智能": "modelbest",
    "生数科技": "shengshu",
}

FAILED_CASES = [
    {
        "case_id": "failed-038-state-grid-home",
        "company": "国家电网",
        "ats": "sgcc",
        "role_family": "unknown",
        "url": "https://zhaopin.sgcc.com.cn/sgcchr/static/home.html",
        "expected_signal": "source unavailable during the 2026-09-09 run",
        "source_id": "sgcc",
        "failure_category": "source_unavailable",
    },
    {
        "case_id": "failed-039-byd-position",
        "company": "比亚迪",
        "ats": "byd",
        "role_family": "unknown",
        "url": (
            "https://job.byd.com/portal/pc/#/skiller/skillerPositionDetails?id=1803338074273374210"
        ),
        "expected_signal": "source unavailable during the 2026-09-09 run",
        "source_id": "byd",
        "failure_category": "source_unavailable",
    },
    {
        "case_id": "failed-040-state-grid-detail",
        "company": "国家电网",
        "ats": "sgcc",
        "role_family": "unknown",
        "url": (
            "https://zhaopin.sgcc.com.cn/sgcchr/static/"
            "unitPart.html?bullet_id=f423534d65af47dfa96f24a60eec26f9&"
            "particulars=true"
        ),
        "expected_signal": "source unavailable during the 2026-09-09 run",
        "source_id": "sgcc",
        "failure_category": "source_unavailable",
    },
]


def main() -> None:
    entries = _parse_report(REPORT_PATH)[:37]
    catalog_cases = []
    labels = []
    snapshots = []

    for index, entry in enumerate(entries, start=1):
        case_id = f"case-{index:03d}-{_company_slug(entry['company'])}"
        evidence = "\n".join(
            [
                entry["title"],
                entry["company"],
                entry["location"],
                "岗位要求",
                entry["requirements"],
                "工作内容",
                entry["responsibilities"],
            ]
        )
        catalog_cases.append(
            {
                "case_id": case_id,
                "company": entry["company"],
                "ats": _ats_for_url(entry["url"]),
                "role_family": _role_family(entry["title"]),
                "url": entry["url"],
                "collected_at": COLLECTED_AT,
                "content_sha256": _sha256(evidence),
                "expected_signal": f"{entry['company']} {entry['title']}",
                "source_id": _source_id(entry["url"]),
            }
        )
        labels.append(
            {
                "case_id": case_id,
                "title": entry["title"],
                "title_aliases": [],
                "company": entry["company"],
                "company_aliases": [],
                "location": (
                    None if entry["location"] == "Unknown Location" else entry["location"]
                ),
                "location_aliases": [],
                "role_family": _role_family(entry["title"]),
                "required_skills": None,
                "hard_constraints": _hard_constraints(entry["requirements"]),
                "is_relevant": index in RELEVANT_CASES,
                "recommended_top_k": TOP_K.get(index),
                "annotated_by": ANNOTATED_BY,
                "annotated_at": ANNOTATED_AT,
            }
        )
        snapshots.append(
            {
                "case_id": case_id,
                "url": entry["url"],
                "title": entry["title"],
                "content": evidence,
            }
        )

    for failed in FAILED_CASES:
        case_id = failed["case_id"]
        catalog_cases.append(
            {
                "case_id": case_id,
                "company": failed["company"],
                "ats": failed["ats"],
                "role_family": failed["role_family"],
                "url": failed["url"],
                "collected_at": COLLECTED_AT,
                "content_sha256": _sha256(failed["url"]),
                "expected_signal": failed["expected_signal"],
                "source_id": failed["source_id"],
            }
        )
        labels.append(
            {
                "case_id": case_id,
                "title": None,
                "title_aliases": [],
                "company": failed["company"],
                "company_aliases": [],
                "location": None,
                "location_aliases": [],
                "role_family": None,
                "required_skills": None,
                "hard_constraints": None,
                "is_relevant": None,
                "recommended_top_k": None,
                "annotated_by": ANNOTATED_BY,
                "annotated_at": ANNOTATED_AT,
            }
        )

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(
        DATASET_DIR / "catalog.json",
        {
            "dataset_version": "real-job-benchmark-v1",
            "source_run_id": RUN_ID,
            "collected_at": COLLECTED_AT,
            "cases": catalog_cases,
        },
    )
    _write_json(
        DATASET_DIR / "ground-truth.json",
        {
            "dataset_version": "real-job-benchmark-v1",
            "source_run_id": RUN_ID,
            "labels": labels,
        },
    )
    _write_jsonl(DATASET_DIR / "snapshots.jsonl", snapshots)


def _parse_report(path: Path) -> list[dict[str, str]]:
    content = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^### \d+\. (.+)$", content, flags=re.MULTILINE))
    entries = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        section = content[match.end() : next_start]
        entries.append(
            {
                "title": match.group(1).strip(),
                "company": _field(section, "公司"),
                "location": _field(section, "地点"),
                "url": _field(section, "链接"),
                "requirements": _block(section, "岗位要求", "工作内容"),
                "responsibilities": _block(section, "工作内容", "匹配分析"),
            }
        )
    return entries


def _field(section: str, name: str) -> str:
    match = re.search(rf"^- {re.escape(name)}: (.+)$", section, flags=re.MULTILINE)
    if not match:
        raise ValueError(f"missing report field: {name}")
    return match.group(1).strip()


def _block(section: str, heading: str, next_heading: str) -> str:
    match = re.search(
        rf"^\*\*{re.escape(heading)}\*\*\s*\n(.*?)(?=^\*\*{re.escape(next_heading)}\*\*|^## )",
        section,
        flags=re.MULTILINE | re.DOTALL,
    )
    return " ".join(match.group(1).split()) if match else ""


def _company_slug(company: str) -> str:
    return COMPANY_SLUGS.get(company, _slug(company))


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "unknown"


def _ats_for_url(url: str) -> str:
    domain = url.split("/", 3)[2]
    if "feishu.cn" in domain:
        return "feishu"
    if "mokahr.com" in domain:
        return "moka"
    if "zhiye.com" in domain:
        return "zhiye"
    if "join.qq.com" in domain:
        return "tencent"
    if "xiaomi" in domain:
        return "xiaomi"
    if "hr.163.com" in domain:
        return "netease"
    if "xiaohongshu" in domain:
        return "xiaohongshu"
    if "mihoyo" in domain:
        return "mihoyo"
    if "jd.com" in domain:
        return "jd"
    if "huawei" in domain:
        return "huawei"
    if "lixiang" in domain:
        return "lixiang"
    return domain.split(".")[-2]


def _source_id(url: str) -> str:
    return f"showcase-{_ats_for_url(url)}"


def _role_family(title: str) -> str:
    if any(term in title for term in ("设计师", "视觉")):
        return "design"
    if any(term in title for term in ("运营", "招聘", "Marketing")):
        return "operations"
    if any(term in title for term in ("产品经理", "客户经理")):
        return "product-business"
    if any(
        term in title
        for term in ("全栈", "Infra", "系统开发", "Agent", "Product Engineer", "数据分析")
    ):
        return "engineering"
    return "algorithm"


def _hard_constraints(requirements: str) -> list[str] | None:
    constraints: list[str] = []
    if "博士" in requirements:
        constraints.append("博士")
    elif "硕士" in requirements:
        constraints.append("硕士及以上")
    if "2027届" in requirements:
        constraints.append("2027届")
    return constraints or None


def _sha256(value: str) -> str:
    normalized = "\n".join(line.strip() for line in value.splitlines() if line.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

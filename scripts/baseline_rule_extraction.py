"""阶段 0 规则抽取中文基线测量：现有 PageExtractor（纯规则、无 LLM）在真实中文 JD 上的逐字段成功率。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/baseline_rule_extraction.py

只读测量，不改生产代码。判定标准（诚实口径）：
- title/company/location：不等于 Unknown 前缀且非空
- requirements/responsibilities：非空且长度 ≥ 30（避免只截到一行杂讯）
- skills 质量字段：规则切分结果与人工预期的差距单独展示（切分条数、每条前 40 字符），
  用于阶段 2 评估 "skills 结构化字段" 的必要性
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from web_task_agent.browser import HttpPageLoader  # noqa: E402
from web_task_agent.extractor import PageExtractor  # noqa: E402

# 与 smoke_chinese_jd.py 同一批 6 个牛客真实岗位（阶段 0 已验证 6/6 可抓）
NOWCODER_JOBS = [
    ("拼多多-大模型算法工程师", "https://www.nowcoder.com/jobs/detail/447182"),
    ("快手-大模型算法实习", "https://www.nowcoder.com/jobs/detail/401709"),
    ("阿里巴巴-视觉&多模态算法实习", "https://www.nowcoder.com/jobs/detail/164845"),
    ("元戎启行-AI Infra 实习生", "https://www.nowcoder.com/jobs/detail/452650"),
    ("元戎启行-感知算法实习生", "https://www.nowcoder.com/jobs/detail/451583"),
    ("华为-AI工程师秋招实习", "https://www.nowcoder.com/jobs/detail/389094"),
]

MIN_SECTION_LEN = 30


def _field_ok(field: str, value: str) -> bool:
    if field in {"title", "company", "location"}:
        return bool(value.strip()) and not value.startswith("Unknown")
    return len(value.strip()) >= MIN_SECTION_LEN


async def main() -> int:
    loader = HttpPageLoader(timeout_seconds=30)
    extractor = PageExtractor()
    rows: list[dict[str, object]] = []

    for label, url in NOWCODER_JOBS:
        try:
            page = await loader(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch-fail] {label}: {type(exc).__name__}: {str(exc)[:120]}")
            rows.append({"label": label, "error": type(exc).__name__})
            continue

        job = extractor.extract(page)
        fields = {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "requirements": job.requirements,
            "responsibilities": job.responsibilities,
        }
        ok = {name: _field_ok(name, value) for name, value in fields.items()}
        rows.append({
            "label": label,
            "ok": ok,
            "confidence": job.confidence,
            "skills_n": len(job.skills),
            "skills_preview": [s[:40] for s in job.skills[:5]],
            "req_preview": job.requirements[:120],
            "title_actual": job.title[:60],
        })
        await asyncio.sleep(2)  # 低频礼貌间隔

    # 汇总矩阵
    field_names = ["title", "company", "location", "requirements", "responsibilities"]
    print("\n=== 规则抽取字段成功率矩阵（6 个牛客真实中文 JD） ===")
    header = f"{'岗位':<26}" + "".join(f"{f[:12]:>14}" for f in field_names) + f"{'confidence':>12}"
    print(header)
    print("-" * len(header))
    counted = [r for r in rows if "ok" in r]
    for row in rows:
        if "ok" not in row:
            print(f"{row['label']:<26} fetch 失败: {row['error']}")
            continue
        ok: dict[str, bool] = row["ok"]  # type: ignore[assignment]
        marks = "".join(f"{'OK':>12}" if ok[f] else f"{'FAIL':>12}" for f in field_names)
        print(f"{row['label']:<26}{marks}{row['confidence']:>12.2f}")

    if counted:
        print("-" * len(header))
        for f in field_names:
            hits = sum(1 for r in counted if r["ok"][f])  # type: ignore[index]
            print(f"  {f:<20} {hits}/{len(counted)}")

    # skills 切分质量单独展示（这是计划里指出的核心短板）
    print("\n=== skills 切分质量抽查（逗号切分在中文整段上的表现） ===")
    for row in rows:
        if "skills_preview" not in row:
            continue
        print(f"\n### {row['label']} — 切出 {row['skills_n']} 条")
        for s in row["skills_preview"]:  # type: ignore[index]
            print(f"    | {s}")
        print(f"    requirements 预览: {row['req_preview']}")  # type: ignore[index]

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""阶段 0 LLM 抽取冒烟：真实组件链（HttpPageLoader + qwen LLM 抽取）在中文 JD 上的表现。

用法（项目根目录，需 DASHSCOPE_API_KEY）：
    DASHSCOPE_API_KEY=... .venv/Scripts/python.exe scripts/smoke_llm_extraction.py

每个 URL 一次 LLM 调用（qwen-plus）。直接组装组件而非走 CLI 评测分支，
因为 CLI 的评测路径都绑定英文关键词过滤器（verifier 默认 AI/LLM/Agent），
中文 JD 会被 verification_filtered 误杀——这正是要测量的问题之一。
输出：规则 vs LLM 逐字段对比 + verifier 过滤原因。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from web_task_agent.browser import HttpPageLoader  # noqa: E402
from web_task_agent.extractor import PageExtractor  # noqa: E402
from web_task_agent.llm_extractor import (  # noqa: E402
    build_configured_llm_field_extractor,
)
from web_task_agent.verifier import JobVerifier  # noqa: E402

# Windows 控制台 GBK 编码兜底（✓ 等字符会崩）
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

NOWCODER_JOBS = [
    ("拼多多-大模型算法工程师", "https://www.nowcoder.com/jobs/detail/447182"),
    ("快手-大模型算法实习", "https://www.nowcoder.com/jobs/detail/401709"),
    ("阿里巴巴-视觉&多模态算法实习", "https://www.nowcoder.com/jobs/detail/164845"),
    ("元戎启行-AI Infra 实习生", "https://www.nowcoder.com/jobs/detail/452650"),
    ("元戎启行-感知算法实习生", "https://www.nowcoder.com/jobs/detail/451583"),
    ("华为-AI工程师秋招实习", "https://www.nowcoder.com/jobs/detail/389094"),
]


def _short(value: str, limit: int = 60) -> str:
    return " ".join(value.split())[:limit]


def _has_content(job) -> dict[str, bool]:
    return {
        "title": bool(job.title.strip()) and not job.title.startswith("Unknown"),
        "company": bool(job.company.strip()) and not job.company.startswith("Unknown"),
        "location": bool(job.location.strip()) and not job.location.startswith("Unknown"),
        "requirements": len(job.requirements.strip()) >= 30,
        "responsibilities": len(job.responsibilities.strip()) >= 30,
    }


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    # OS 环境变量可能有旧 key 且 load_dotenv 默认不覆盖——强制以 .env 为准
    env_key = ""
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DASHSCOPE_API_KEY="):
            env_key = line.split("=", 1)[1].strip()
            break
    if env_key:
        os.environ["DASHSCOPE_API_KEY"] = env_key

    llm_extractor = build_configured_llm_field_extractor(provider="qwen", model=None)
    rule_only = PageExtractor()
    rule_with_llm = PageExtractor(llm_field_extractor=llm_extractor)
    loader = HttpPageLoader(timeout_seconds=30)
    # 诊断模式：关键词放空，只看 confidence/内容过滤，不因英文关键词误杀
    verifier = JobVerifier(required_keywords=[])

    rows: list[dict] = []
    for label, url in NOWCODER_JOBS:
        try:
            page = await loader(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[fetch-fail] {label}: {type(exc).__name__}")
            continue

        rule_job = rule_only.extract(page)
        llm_job = rule_with_llm.extract(page)  # 规则 confidence<0.6 时触发 LLM
        used_llm = llm_job is not rule_job
        verdict = verifier.verify(llm_job)
        rows.append({"label": label, "rule": rule_job, "llm": llm_job, "used_llm": used_llm, "verdict": verdict})
        await asyncio.sleep(2)  # 低频礼貌间隔

    field_names = ["title", "company", "location", "requirements", "responsibilities"]
    print("\n=== 规则 vs LLM 字段成功率（6 个牛客中文 JD） ===")
    print(f"{'岗位':<26}" + "".join(f"{f[:10]:>12}" for f in field_names) + "   LLM触发")
    print("-" * 100)
    for row in rows:
        rule_ok = _has_content(row["rule"])
        llm_ok = _has_content(row["llm"])
        marks = "".join(
            f"{('Y' if rule_ok[f] else '-') + '>' + ('Y' if llm_ok[f] else '-'):>12}"
            for f in field_names
        )
        print(f"{row['label']:<26}{marks}   {'是' if row['used_llm'] else '否'}")

    print("\n=== LLM 抽取内容抽查 ===")
    for row in rows:
        job = row["llm"]
        print(f"\n### {row['label']} (confidence={job.confidence:.2f}, verifier={'通过' if row['verdict'].is_valid else '拦截: ' + '; '.join(row['verdict'].reasons)})")
        print(f"    title    : {_short(job.title, 70)}")
        print(f"    company  : {_short(job.company, 50)} | location: {_short(job.location, 30)}")
        print(f"    skills   : {json.dumps(job.skills[:8], ensure_ascii=False)}")
        print(f"    职责预览 : {_short(job.responsibilities or job.requirements, 100)}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

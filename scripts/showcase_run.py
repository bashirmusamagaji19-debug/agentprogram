"""阶段 2 showcase:全源真实运行一次完整管线,产出演示产物与统计。

用法(项目根目录):
    ./.venv/Scripts/python.exe -X utf8 scripts/showcase_run.py [--count 80] [--no-llm]

- 数据:官方列表 API 实时发现(公平采样,42 源)
- 简历:合成简历(与评测口径一致;真实简历轮尚待用户提供,不在本脚本做真实效果声明)
- LLM:qwen(DASHSCOPE_API_KEY 从 .env 读取;--no-llm 跳过)
- 产物:streamlit-runs/showcase-<runid>/ 下的 JSON/Markdown/Dashboard/行动计划
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SYNTHETIC_RESUME = """教育背景:某高校计算机科学与技术专业,本科。
技能:Python、FastAPI、LangGraph、RAG 检索增强、PyTorch 基础、MySQL、Docker。
经历:
1. 基于 LangGraph 的多 Agent 协作系统,负责任务规划与工具调用链路;
2. RAG 知识库问答系统,覆盖文档解析、向量检索与重排;
3. 参与开源项目,熟悉 Git 协作与单元测试。
求职意向:AI Agent / 大模型应用相关实习(2027 届)。
"""


def load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=80)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    load_env()
    from web_task_agent.streamlit_runner import UiRunRequest, run_ui_request

    use_llm = not args.no_llm and bool(os.environ.get("DASHSCOPE_API_KEY", "").strip())
    request = UiRunRequest(
        data_mode="official",
        official_specs=None,  # 全源
        target_count=args.count,
        skills=["Python", "LLM", "RAG", "LangGraph", "Agent"],
        resume_text=SYNTHETIC_RESUME,
        llm_match_provider="qwen" if use_llm else None,
        llm_extractor_provider=None,  # 规则抽取已覆盖官方 API 的标准标签正文
    )
    print(f"开始 showcase 运行:target={args.count}, llm_match={'qwen' if use_llm else 'off'}")
    result = await run_ui_request(request, output_root="streamlit-runs")

    m = result.metrics
    print(
        f"\nrun_id={result.run_id} valid={m.valid_jobs} "
        f"visited={m.pages_visited} failed={m.failed_pages}"
    )

    # ── 统计摘要 ──
    tier_counter = Counter(j.tier or "未标注" for j in result.jobs)
    cat_counter = Counter(j.category or "未标注" for j in result.jobs)
    src_counter = Counter(j.source for j in result.jobs)
    matched = [mm for mm in result.matches if mm.score >= 0.5]

    summary = {
        "run_id": result.run_id,
        "metrics": m.model_dump(mode="json"),
        "tier_distribution": dict(tier_counter),
        "category_distribution": dict(cat_counter),
        "source_distribution": dict(src_counter),
        "llm_match_enabled": use_llm,
        "matches_ge_0.5": len(matched),
    }
    run_dir = Path("streamlit-runs") / result.run_id
    (run_dir / "showcase-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n── 梯队分布 ──")
    for tier, n in tier_counter.most_common():
        print(f"  {tier or '未标注'}: {n}")
    print("── 岗位类别分布 ──")
    for cat, n in cat_counter.most_common():
        print(f"  {cat or '未标注'}: {n}")
    print(f"── 有效发现源:{len(src_counter)} ──")
    print(f"── LLM 匹配(≥0.5):{len(matched)} 条 ──")
    for mm in sorted(result.matches, key=lambda x: -x.score)[:5]:
        job = next((j for j in result.jobs if j.url == mm.job_id), None)
        if job:
            print(f"  [{mm.score:.2f}] {job.tier or '?'} | {job.title[:30]} | {job.company[:12]}")
    print(f"\n产物目录:{run_dir.resolve()}")
    print("摘要:showcase-summary.json")


if __name__ == "__main__":
    asyncio.run(main())

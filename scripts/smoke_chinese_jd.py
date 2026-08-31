"""阶段 0 冒烟脚本：测量 HttpPageLoader 在真实中文 JD 页面上的可访问性。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/smoke_chinese_jd.py

只读测量，不写任何生产代码路径。输出按站点记录：
- 成功：title、正文长度、正文预览
- 失败：异常类型（对应 http_timeout / http_error / empty_page / browser_error 分类）
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from web_task_agent.browser import (  # noqa: E402
    BrowserConfigurationError,
    HttpPageLoader,
)

# 2026-08-31 从牛客实习列表页（www.nowcoder.com/jobs/intern/center）采集的
# AI/算法/大模型相关真实岗位详情页，覆盖不同公司类型。
NOWCODER_JOBS = [
    ("拼多多-大模型算法工程师", "https://www.nowcoder.com/jobs/detail/447182"),
    ("快手-大模型算法实习", "https://www.nowcoder.com/jobs/detail/401709"),
    ("阿里巴巴-视觉&多模态算法实习", "https://www.nowcoder.com/jobs/detail/164845"),
    ("元戎启行-AI Infra 实习生", "https://www.nowcoder.com/jobs/detail/452650"),
    ("元戎启行-感知算法实习生", "https://www.nowcoder.com/jobs/detail/451583"),
    ("华为-AI工程师秋招实习", "https://www.nowcoder.com/jobs/detail/389094"),
]


async def main() -> int:
    loader = HttpPageLoader(timeout_seconds=30)
    results: list[tuple[str, str, str, int, str]] = []

    for label, url in NOWCODER_JOBS:
        try:
            page = await loader(url)
            preview = " ".join(page.content.split())[:160]
            results.append((label, "OK", page.title, len(page.content), preview))
        except BrowserConfigurationError as exc:
            results.append((label, type(exc).__name__, "", 0, str(exc)[:160]))
        except Exception as exc:  # noqa: BLE001
            results.append((label, type(exc).__name__, "", 0, str(exc)[:160]))
        await asyncio.sleep(2)  # 低频礼貌间隔

    print(f"{'岗位':<28} {'结果':<12} {'标题':<24} 正文长度")
    print("-" * 100)
    for label, status, title, length, preview in results:
        title_short = (title or "")[:22]
        print(f"{label:<28} {status:<12} {title_short:<24} {length}")
    print("-" * 100)
    for label, status, title, length, preview in results:
        print(f"\n### {label} [{status}] len={length}")
        print(f"    {preview}")

    ok = sum(1 for r in results if r[1] == "OK")
    print(f"\n合计: {ok}/{len(results)} 页面可通过 HttpPageLoader 获取正文")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

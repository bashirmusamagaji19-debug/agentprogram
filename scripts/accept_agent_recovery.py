"""Execute actual Hybrid runtime recovery loops against controlled adapters."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_task_agent.agent_cli import build_hybrid_runtime, hybrid_state_payload  # noqa: E402
from web_task_agent.browser import FakeBrowserClient, PageTimeoutError  # noqa: E402
from web_task_agent.demo_pages import DEMO_JOB_PAGES  # noqa: E402
from web_task_agent.extractor import PageExtractor  # noqa: E402
from web_task_agent.matcher import JobMatcher  # noqa: E402
from web_task_agent.models import UserProfile  # noqa: E402
from web_task_agent.reporter import MarkdownReporter  # noqa: E402
from web_task_agent.storage import JobRepository  # noqa: E402
from web_task_agent.verifier import JobVerifier  # noqa: E402
from web_task_agent.visual_extractor import DemoVisualJobExtractor  # noqa: E402
from web_task_agent.workflow import WebTaskWorkflow  # noqa: E402


class FailingBrowser(FakeBrowserClient):
    async def open_url(self, url: str):
        if url.endswith("/broken"):
            raise PageTimeoutError("controlled timeout")
        return await super().open_url(url)


async def main() -> None:
    root = ROOT / "portfolio-artifacts/acceptance-2026-10-05/recovery" / uuid4().hex[:8]
    output = ROOT / "docs/results/portfolio-acceptance-2026-10-05"
    root.mkdir(parents=True)
    cases = []
    for name in ("next-url", "text-to-visual"):
        repository = JobRepository(root / f"{name}.sqlite")
        repository.initialize()
        workflow = WebTaskWorkflow(
            browser=FailingBrowser(DEMO_JOB_PAGES), extractor=PageExtractor(), verifier=JobVerifier(),
            matcher=JobMatcher(), repository=repository, reporter=MarkdownReporter(root / "reports"),
            visual_extractor=DemoVisualJobExtractor() if name == "text-to-visual" else None,
        )
        urls = (
            ["https://example.com/jobs/broken", "https://example.com/jobs/ai-engineering-intern"]
            if name == "next-url" else ["https://example.com/jobs/unstructured-ai-agent-intern"]
        )
        state = await workflow.run_with_hybrid_agent(
            UserProfile(keyword="AI intern", target_count=1, skills=["Python", "LangGraph"], seed_urls=urls),
            runtime=build_hybrid_runtime(workflow), max_steps=12,
        )
        actions = [decision.action.value for decision in state.decision_history]
        (root / f"{name}.json").write_text(
            json.dumps(hybrid_state_payload(state), ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"{name}: reason={state.terminal_reason}, actions={actions}", flush=True)
        assert state.terminal_reason == "target_reached" and len(repository.list_jobs()) == 1
        if name == "next-url":
            assert actions.count("open_page") == 3
        else:
            assert actions.index("extract_text") < actions.index("extract_visual")
        cases.append({"case_id": name, "controlled_adapters": True, **hybrid_state_payload(state)})
        print(f"{name}: {' -> '.join(actions)}", flush=True)
    (output / "agent-recovery.json").write_text(
        json.dumps({"actual_runtime_execution": True, "external_vlm_called": False, "cases": cases},
                   ensure_ascii=False, indent=2), encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())

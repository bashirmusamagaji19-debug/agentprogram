from __future__ import annotations

from pathlib import Path
from typing import Protocol


class LangGraphWorkflow(Protocol):
    def build_langgraph(self): ...


class LangGraphExporter:
    def __init__(self, output_dir: str | Path = "docs") -> None:
        self.output_dir = Path(output_dir)

    def render_markdown(self, workflow: LangGraphWorkflow) -> str:
        mermaid = workflow.build_langgraph().get_graph().draw_mermaid()
        return "\n".join(
            [
                "# LangGraph Agent 工作流",
                "",
                "这个图展示 Web Task Agent 的 LangGraph 编排路径。",
                "",
                "```mermaid",
                mermaid,
                "```",
                "",
            ]
        )

    def write_markdown(self, workflow: LangGraphWorkflow) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / "agent-workflow-graph.md"
        path.write_text(self.render_markdown(workflow), encoding="utf-8")
        return path

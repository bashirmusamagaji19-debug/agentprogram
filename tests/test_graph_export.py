from tests.fixtures.job_pages import FAKE_JOB_PAGES
from web_task_agent.browser import FakeBrowserClient
from web_task_agent.extractor import PageExtractor
from web_task_agent.graph_export import LangGraphExporter
from web_task_agent.matcher import JobMatcher
from web_task_agent.reporter import MarkdownReporter
from web_task_agent.storage import JobRepository
from web_task_agent.verifier import JobVerifier
from web_task_agent.workflow import WebTaskWorkflow


def make_workflow(tmp_path):
    repo = JobRepository(tmp_path / "agent.db")
    repo.initialize()
    return WebTaskWorkflow(
        browser=FakeBrowserClient(FAKE_JOB_PAGES),
        extractor=PageExtractor(),
        matcher=JobMatcher(),
        verifier=JobVerifier(),
        repository=repo,
        reporter=MarkdownReporter(tmp_path / "reports"),
    )


def test_langgraph_exporter_renders_mermaid_markdown(tmp_path):
    workflow = make_workflow(tmp_path)

    markdown = LangGraphExporter().render_markdown(workflow)

    assert "# LangGraph Agent 工作流" in markdown
    assert "```mermaid" in markdown
    assert "planner" in markdown
    assert "browser" in markdown
    assert "extractor" in markdown
    assert "verifier" in markdown
    assert "matcher" in markdown
    assert "reporter" in markdown


def test_langgraph_exporter_writes_markdown_file(tmp_path):
    workflow = make_workflow(tmp_path)

    path = LangGraphExporter(output_dir=tmp_path).write_markdown(workflow)

    assert path.name == "agent-workflow-graph.md"
    assert path.exists()
    assert "```mermaid" in path.read_text(encoding="utf-8")

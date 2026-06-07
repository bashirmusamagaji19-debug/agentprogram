# Web 自动任务 Agent

这是一个面向 AI 工程 / AI 应用实习的 Agent 项目。第一版目标是基于 `browser-use` 和 `LangGraph` 构建 Web 自动任务工作流，自动发现 AI 实习岗位，抽取结构化 JD，验证结果并生成 Markdown 报告。

## MVP 能力

- 通过浏览器客户端读取网页内容。
- 用工作流拆分规划、浏览、抽取、验证、保存和报告生成。
- 用 SQLite 保存岗位记录和运行指标。
- 用测试中的 fake browser 保证端到端流程可复现。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
web-task-agent --keyword "AI engineering intern" --location "Remote" --target-count 3
```

# Web 自动任务 Agent

这是一个面向 AI 工程 / AI 应用实习的 Agent 项目。第一版目标是基于 `browser-use` 和 `LangGraph` 构建 Web 自动任务工作流，自动发现 AI 实习岗位，抽取结构化 JD，验证结果并生成 Markdown 报告。

## MVP 能力

- 通过浏览器客户端读取网页内容。
- 用工作流拆分规划、浏览、抽取、验证、匹配、保存和报告生成。
- 用 SQLite 保存岗位记录和运行指标。
- 根据技能标签和简历文本生成岗位匹配分数、缺失技能和建议动作。
- 用测试中的 fake browser 保证端到端流程可复现。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo
```

如果 Windows PowerShell 显示中文乱码，请使用 UTF-8 终端或执行 chcp 65001 后再查看。

## 已验证的 MVP 命令

```powershell
.\.venv\Scripts\web-task-agent.exe --keyword "AI intern" --location "Remote" --target-count 2 --skill Python --skill LangGraph --demo
```

该命令使用内置 demo 页面运行，不依赖真实招聘网站，适合快速展示工作流闭环。生成的 Markdown 报告会包含岗位列表、运行指标和匹配分析。当前真实 `browser-use` 网页搜索仍保留在 adapter 边界之后，后续阶段再接入。

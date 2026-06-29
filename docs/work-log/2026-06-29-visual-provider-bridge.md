# 本轮工作：Real Visual Provider Bridge

## 目标

把 `visual-web-agent` 的真实 Qwen-VL 截图理解链路桥接到 `Agent`，让 seed URL workflow 和 comparison 进入真实 visual provider 阶段。

## 背景

上一轮（`2026-06-29-visual-extractor-integration.md`）在 Agent 内建了 `DemoVisualJobExtractor`（确定性 fixture），验证了 Protocol → Workflow → CLI → Comparison 全链路。本轮加入真实 Qwen-VL provider，同时解决了双重获取（double-fetch）的架构问题。

## 实施范围

### visual-web-agent 侧
- **新增** `factory.py`：`build_visual_job_extractor()` 可复用工厂，接受 browser/VLM 注入。
- **修改** `cli.py`：单 URL 和批量路径都通过 factory 构建 extractor。

### Agent 侧
- **新增** `visual_provider.py`：`QwenVisualExtractorAdapter`（实现了 `AsyncVisualJobExtractor` 协议）、动态 import `visual_web_agent.factory`、`build_configured_visual_extractor()` 工厂。
- **修改** `visual_extractor.py`：`DemoVisualJobExtractor.uses_own_browser = False`。
- **修改** `workflow.py`：`_browser_node` 检测 `uses_own_browser` → 跳过 workflow browser，创建占位 `BrowserPage`。
- **修改** `cli.py`：`--visual-extractor-provider qwen-vl` / `--visual-extractor-model`、provider 异常捕获、comparison 输出 provider 行。
- **修改** `evaluation.py`：上一轮已加 `visual_extractor_factory`，本轮复用。

## 核心设计：双重获取消除

### 问题

```
真实 provider 模式下：
  workflow browser.open_url()  → 获取页面文本（浪费）
  visual provider.extract()    → Playwright 截图 + VLM（真正需要）

两次 HTTP 请求同一 URL，浪费带宽和时间。
```

### 方案 A：uses_own_browser 信号

1. `DemoVisualJobExtractor.uses_own_browser = False` — 依赖 workflow browser 提供页面。
2. `QwenVisualExtractorAdapter.uses_own_browser = True` — 自带 Playwright，自行获取。
3. `_browser_node`：如果 `uses_own_browser is True`，直接从 `candidate_urls` 创建空占位 `BrowserPage`，不调用 workflow browser。
4. `_extractor_node`：visual extractor 用 `page.url` 自行截图抽取。

```python
# workflow.py _browser_node
if (
    state.candidate_urls
    and self.visual_extractor is not None
    and getattr(self.visual_extractor, "uses_own_browser", False)
):
    for url in state.candidate_urls:
        state.pages.append(BrowserPage(url=url, title="", content="", source="visual-provider"))
    return state
```

测试 `test_workflow_skips_browser_when_visual_extractor_uses_own_browser` 验证 browser 确实没有被调用。

## 文件变更

| 仓库 | 文件 | 操作 | 说明 |
|------|------|------|------|
| visual-web-agent | `src/visual_web_agent/factory.py` | 新建 | 可复用 extractor 工厂 |
| visual-web-agent | `src/visual_web_agent/cli.py` | 修改 | 使用 factory 构建 |
| visual-web-agent | `tests/test_factory.py` | 新建 | 3 个 factory 测试 |
| Agent | `src/web_task_agent/visual_provider.py` | 新建 | Qwen-VL 桥接 + 动态 import |
| Agent | `src/web_task_agent/visual_extractor.py` | 修改 | 加 `uses_own_browser = False` |
| Agent | `src/web_task_agent/workflow.py` | 修改 | `_browser_node` 跳过逻辑 + `BrowserPage` import |
| Agent | `src/web_task_agent/cli.py` | 修改 | provider 参数 + 异常捕获 + comparison |
| Agent | `tests/test_visual_provider.py` | 新建 | 4 个桥接测试 |
| Agent | `tests/test_workflow.py` | 修改 | +1 个 skip-browser 测试 |
| Agent | `tests/test_scaffold.py` | 修改 | +2 个 CLI provider 测试 |
| Agent | `README.md` | 修改 | Real visual provider 章节 |
| visual-web-agent | `README.md` | 修改 | Reusable factory 章节 |
| Agent | `docs/work-log/2026-06-29-visual-provider-bridge.md` | 新建 | 本日志 |

## 验证命令

```powershell
# 全量测试
.\.venv\Scripts\python.exe -m pytest

# visual-web-agent 测试
pushd ..\visual-web-agent
.\.venv\Scripts\python.exe -m pytest tests/test_factory.py tests/test_parser.py tests/test_extractor.py -v
popd

# Agent visual 专项测试
.\.venv\Scripts\python.exe -m pytest tests/test_visual_extractor.py tests/test_visual_provider.py tests/test_workflow.py -v

# CLI 集成
.\.venv\Scripts\python.exe -m pytest tests/test_scaffold.py -k "visual" -v

# 手动：安装 sibling package
.\.venv\Scripts\python.exe -m pip install -e "..\visual-web-agent"

# 手动：provider demo（用 demo 页面测试 adapter，不含真实 API key）
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider.json
```

预期：如果 `visual-web-agent` 未安装 → exit code 2 + 安装提示。如果已安装但无 `DASHSCOPE_API_KEY` → VLM 初始化报错（预期行为）。

## 当前边界

- 真实 provider 仍然通过 `visual-web-agent` 负责截图和 VLM 调用。
- Agent 只负责桥接、验证、匹配、报告和评测。
- 如果 sibling package 没装，CLI 返回清晰的配置错误（exit code 2），不会静默降级。
- `--visual-extractor-provider` 只能用于 seed URL 模式。搜索模式会打印 warning。
- `--demo --visual-extractor-provider qwen-vl` 可以同时使用，但 demo 优先。demo URL 对应 `FakeBrowserClient` 的页面，provider 的 Playwright 无法访问这些假 URL（会超时）。

## 面试讲述要点

- "我在两个独立项目之间做了窄桥接——`visual-web-agent` 负责视觉理解，`Agent` 通过 Protocol adapter 接入。"
- "解决了一个架构问题：真实 provider 自带 Playwright browser，如果 workflow 也做一次 browser 获取就会双重请求。我通过 `uses_own_browser` 信号让 workflow 跳过自己的 browser 步骤。"
- "动态 import 保证 Agent 不依赖 `visual-web-agent` 的重依赖（Playwright）。没装就报清晰的配置错误。"
- "测试用 monkeypatch 覆盖 `build_configured_visual_extractor`，不需要真实 API key 就能验证全链路。"
- "`--compare-llm-extractor` 现在可以同时对比 baseline、llm-demo、visual-demo、qwen-vl 四条路径。"

# 本轮工作：Visual Extractor 最小接入计划

## 目标

把 `visual-web-agent` 的截图/VLM 抽取思路以最小实验路径接入 `Agent`，先支持 seed URL 和评测对比，不直接合并两个项目。

## 背景

`Agent` 项目（Web Task Agent）用文本抽取（规则 + LLM）从招聘页面提取结构化岗位信息。`visual-web-agent` 项目用 Playwright 截图 + Qwen-VL 从视觉角度理解页面。本轮在 `Agent` 内部加一个窄 adapter，让两条路径可以在同一批 seed URL 上对比，同时保持两个项目独立。

## 实施范围

- **新增** `web_task_agent.visual_extractor` 适配层（`VisualJobFields`、`AsyncVisualJobExtractor` Protocol、`DemoVisualJobExtractor`、`job_from_visual_fields()`）。
- **修改** `WebTaskWorkflow`：`__init__` 接受可选 async visual extractor、`_extractor_node` 升级为 async、视觉优先 + 文本回退。
- **修改** CLI：新增 `--visual-extractor-demo` 参数、`build_cli_visual_extractor()` 工厂函数、无 `--seed-url` 时的 warning。
- **修改** `EvaluationRunner`：接受可选 `visual_extractor_factory`。
- **修改** `--compare-llm-extractor`：额外输出 `visual_demo` 对比行。
- **新增** `demo_pages.py` 添加 `visual-ai-intern` 页面。
- 保持默认文本抽取路径不变。

## 架构决策

### 为什么在 Agent 内复制 VisualJobFields 而不是 import visual-web-agent？

`visual-web-agent` 依赖 Playwright，是重依赖。在 Agent 侧定义独立的 Pydantic model 可以保持 Agent 的轻量安装（`pip install -e ".[dev]"`），避免 CI 和测试环境需要浏览器。等接口稳定后可以选择：
1. 让 Agent 声明 optional dependency 依赖 `visual-web-agent` 的 models 子包。
2. 把共享 schema 抽取为第三个包。
3. 保持复制（如果模型差异变大，各自演进更安全）。

### 为什么只做 deterministic demo，不接真实 Qwen-VL？

第一里程碑的核心问题是"接口对不对"（Protocol 定义 → Workflow 接入 → CLI → 评测对比），不是"效果好不好"。确定性 demo 可以用纯 pytest 验证全链路，不依赖 API key 和网络。真实 Qwen-VL 接入只需要实现 `AsyncVisualJobExtractor` 协议即可插入。

### 视觉优先 + 文本回退策略

`_extractor_node` 对每个 page：先跑 visual extractor（如果配置了），成功就跳过文本抽取；失败就回退到文本抽取。这样：
- visual 路径不会破坏现有文本路径的结果。
- 评测时可以同时看到 visual 成功率和文本 baseline。

## 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/web_task_agent/visual_extractor.py` | 新建 | 视觉抽取适配层 |
| `tests/test_visual_extractor.py` | 新建 | 4 个单元测试 |
| `src/web_task_agent/workflow.py` | 修改 | 可选 visual_extractor + async extractor_node |
| `tests/test_workflow.py` | 修改 | +3 个测试（visual、fallback、langgraph） |
| `src/web_task_agent/cli.py` | 修改 | --visual-extractor-demo + comparison 支持 |
| `src/web_task_agent/evaluation.py` | 修改 | 可选 visual_extractor_factory |
| `src/web_task_agent/demo_pages.py` | 修改 | 新增 visual-ai-intern 页面 |
| `tests/test_scaffold.py` | 修改 | +2 个 CLI 测试 |
| `README.md` | 修改 | Visual extractor demo 章节 |
| `docs/work-log/2026-06-29-visual-extractor-integration.md` | 新建 | 本日志 |

## 验证命令

```powershell
# 全量测试
.\.venv\Scripts\python.exe -m pytest

# 视觉抽取专项测试
.\.venv\Scripts\python.exe -m pytest tests/test_visual_extractor.py tests/test_workflow.py -v

# CLI 集成测试
.\.venv\Scripts\python.exe -m pytest tests/test_scaffold.py::test_cli_seed_url_can_use_visual_extractor_demo tests/test_scaffold.py::test_cli_compare_extractor_can_include_visual_demo -v

# 手动验收
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-demo --json-output outputs\visual-demo.json
.\.venv\Scripts\web-task-agent.exe --compare-llm-extractor --seed-url "https://example.com/jobs/visual-ai-intern" --visual-extractor-demo --json-output evaluations\visual-comparison.json
```

预期输出：

```text
Visual extractor demo: enabled
Report written to: reports\run-xxxxx.md
Valid jobs: 1
JSON output written to: outputs\visual-demo.json
```

```text
LLM extractor comparison
baseline: 1/1
llm-demo: 1/1
visual-demo: 1/1
Comparison report written to: evaluations\llm-extractor-comparison.md
```

JSON metadata 应包含：

```json
{
  "extractor_mode": "visual-demo",
  "visual_extraction": {
    "successes": 1,
    "failures": 0,
    "errors": []
  }
}
```

## 当前边界

- 本轮只接 deterministic visual demo，不直接调用真实 Qwen-VL。
- 真实截图链路继续保留在 `visual-web-agent` 项目中，等 Agent 侧接口稳定后再决定迁移或作为依赖调用。
- 视觉抽取失败时回退到文本抽取，避免破坏现有 demo 和评测闭环。
- `--visual-extractor-demo` 建议配合 `--seed-url` 使用；搜索模式下 demo extractor 没有对应 fixture，会静默回退到文本抽取。

## 下一步

1. 在 `visual-web-agent` 实现 `AsyncVisualJobExtractor` 协议的 `QwenVlJobExtractor`（Playwright + Qwen-VL）。
2. 在 Agent CLI 增加 `--visual-extractor-provider qwen-vl`，从 Agent 直接调用 visual-web-agent 的实现（或作为 optional dependency）。
3. 在当前 8 样本真实 benchmark 上做文本 vs 视觉的对比评测。
4. 决定 `VisualJobFields` 的去重策略（共享包 vs 各自演进）。

## 面试讲述要点

- "我在 Agent 项目里加了一个视觉抽取的实验路径，用 Protocol 抽象保证接口可替换。"
- "先做确定性 demo 验证全链路（单元测试 → workflow → CLI → 评测对比），再接真实 VLM。"
- "视觉失败时回退到文本抽取，不会破坏已有的评测闭环。"
- "架构上保持了两个项目的独立——visual-web-agent 继续做视觉探索，Agent 通过窄 adapter 接入。"
- "`--compare-llm-extractor` 可以同时对比 baseline、llm-demo、visual-demo 三条路径，是面试现场可以展示的差异化能力。"

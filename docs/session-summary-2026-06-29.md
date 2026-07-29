# 工作汇总：2026-06-29 — 2026-06-30

两天 21 个 commit，完成四个计划的实施 + 多轮 bug fix。

---

## 一、Visual Extractor 接入（计划 1）

**目标**：把 `visual-web-agent` 的截图/VLM 抽取思路以最小实验路径接入 `Agent`。

| 文件 | 说明 |
|------|------|
| `src/web_task_agent/visual_extractor.py` | `VisualJobFields`、`AsyncVisualJobExtractor` Protocol、`DemoVisualJobExtractor`、`job_from_visual_fields()` |
| `src/web_task_agent/workflow.py` | `_extractor_node` 改为 async、视觉优先 + 文本回退 |
| `src/web_task_agent/cli.py` | `--visual-extractor-demo` |
| `src/web_task_agent/evaluation.py` | `EvaluationRunner` 支持 `visual_extractor_factory` |
| `src/web_task_agent/demo_pages.py` | 新增 `visual-ai-intern` 页面 |
| `tests/test_visual_extractor.py` | 5 个单元测试 |
| `docs/work-log/2026-06-29-visual-extractor-integration.md` | 工作日志 |

**关键设计**：确定性 demo 先验证全链路（Protocol → Workflow → CLI → Comparison），再接真实 VLM。

---

## 二、Real Visual Provider Bridge（计划 2）

**目标**：把真实 Qwen-VL（Playwright 截图 + DashScope API）桥接到 Agent。

### Agent 侧

| 文件 | 说明 |
|------|------|
| `src/web_task_agent/visual_provider.py` | `QwenVisualExtractorAdapter`（`uses_own_browser=True`）、`VisualProviderConfigurationError`、`import_visual_web_agent()` 动态 import、API key 前置检查、`_visual_fields_are_meaningful()` 质量门、`close()` |
| `src/web_task_agent/visual_extractor.py` | `DemoVisualJobExtractor.uses_own_browser = False` |
| `src/web_task_agent/workflow.py` | `_browser_node` 检测 `uses_own_browser` → 跳过 workflow browser，消除双重获取 |
| `src/web_task_agent/cli.py` | `--visual-extractor-provider qwen-vl` / `--visual-extractor-model`、异常捕获、comparison 输出 provider 行、`_visual_provider_run_failed()` helper、exit code 2 语义 |
| `src/web_task_agent/evaluation.py` | `VisualExtractorFactory` 类型 |
| `tests/test_visual_provider.py` | 8 个测试（adapter 转换、缺包错误、API key 检查、空字段质量门、title-only 拒绝、close() 验证、exit code 2） |
| `docs/work-log/2026-06-29-visual-provider-bridge.md` | 工作日志（含三轮修复记录） |

### visual-web-agent 侧

| 文件 | 说明 |
|------|------|
| `src/visual_web_agent/factory.py` | `build_visual_job_extractor()` 可复用工厂 |
| `src/visual_web_agent/extractor.py` | `VisualJobExtractor.close()` |
| `src/visual_web_agent/browser.py` | `PlaywrightBrowserClient.close()` 幂等化（`_playwright = None` 初始化） |
| `tests/test_factory.py` | 3 个测试 |

### 修复轮次

1. **第一轮**：demo + provider 互斥报错、Playwright 清理链（`VisualJobExtractor.close()` → adapter → CLI finally）、API key 前置检查
2. **第二轮**：comparison 路径 `try/finally close()`、`PlaywrightBrowserClient.close()` 幂等、demo script 用真实 URL、provider 失败诊断
3. **第三轮**：区分"VLM 调用成功"和"有效字段抽取"（质量门拒绝空字段/Unknown Title/title-only）、exit code 2 + `"produced no valid jobs"`、comparison 保持 exit 0

---

## 三、Real Site Benchmark V2（计划 3）

**目标**：把一次性 `--compare-llm-extractor` 升级为结构化 provider matrix。

| 文件 | 说明 |
|------|------|
| `src/web_task_agent/benchmark.py` | `BenchmarkCase`（含 company/ATS/role_family/expected_signal 元数据）、`BenchmarkProviderResult`、`BenchmarkMatrixResult`、`run_benchmark_matrix()` 可注入 runner、Markdown/JSON artifact 渲染 |
| `src/web_task_agent/cli.py` | `--benchmark-v2` / `--benchmark-providers` / `--benchmark-limit` / `--benchmark-dashboard`、`run_cli_benchmark_v2()`（5 个 provider 分支） |
| `src/web_task_agent/dashboard.py` | `render_benchmark_summary()` |
| `tests/test_benchmark.py` | 8 个测试 |
| `docs/work-log/2026-06-29-real-site-benchmark-v2.md` | 工作日志 |

**产出物**：`evaluations/benchmark-v2.json` + `benchmark-v2.md` + `dashboards/benchmark-v2.html`

---

## 四、Benchmark 解释层（计划 4）

**目标**：把 raw benchmark 矩阵翻译成面试可讲的中文叙事，不调 LLM。

| 文件 | 说明 |
|------|------|
| `src/web_task_agent/benchmark_explainer.py` | `BenchmarkInsight`（一句话结论、provider 解读、失败原因、visual provider 价值、60s/3min 面试讲法）、7 个 failure category 全覆盖 |
| `src/web_task_agent/cli.py` | `--benchmark-explain`、`run_cli_benchmark_v2(explain=)` |
| `src/web_task_agent/dashboard.py` | `render_benchmark_summary(result, *, insight=None)` |
| `tests/test_benchmark_explainer.py` | 5 个测试（含 failure category 完整性检查） |
| `docs/work-log/2026-06-29-benchmark-explanation-layer.md` | 工作日志 |

**产出物**：`evaluations/benchmark-v2-explained.md`（面试用叙事层）

---

## 关键架构决策

1. **视觉抽取 Protocol 抽象**：`AsyncVisualJobExtractor` Protocol 保证 demo/real provider 可替换。
2. **双重获取消除**：`uses_own_browser` 信号 → workflow browser 跳过 → provider 自行 Playwright 截图。
3. **动态 import**：Agent 不硬依赖 `visual-web-agent`（Playwright 重依赖），缺包时 exit 2 + 安装提示。
4. **质量门**：VLM 调用成功 ≠ 有效字段抽取。空字段 / Unknown Title / title-only 均计为 failure。
5. **Benchmark 分层**：`json`（数据源）→ `md`（原始矩阵）→ `explained.md`（叙事层）→ `html`（dashboard）。递增的抽象层次。
6. **解释层不调 LLM**：确定性 heuristic，可测试、可复跑、可审查。

---

## 验收清单

```powershell
# 确定性 visual demo
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --demo --target-count 1 --visual-extractor-demo --json-output outputs\visual-demo.json
# → Valid jobs: 1, exit 0

# 真实 Qwen-VL provider（需 DASHSCOPE_API_KEY）
.\.venv\Scripts\web-task-agent.exe --seed-url "https://job-boards.greenhouse.io/anthropic/jobs/5116927008" --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\visual-provider.json
# → Valid jobs: 1, exit 0, 无 unclosed transport

# 假 URL + provider → 质量门拒绝
.\.venv\Scripts\web-task-agent.exe --seed-url "https://example.com/jobs/visual-ai-intern" --target-count 1 --visual-extractor-provider qwen-vl --json-output outputs\empty.json
# → visual successes: 0, exit 2, 诊断信息

# Benchmark v2 matrix
.\.venv\Scripts\web-task-agent.exe --benchmark-v2 --benchmark-providers baseline,llm-demo,deepseek --benchmark-limit 8 --benchmark-dashboard --benchmark-explain
# → 四个 artifact 全部生成

# 全量测试
.\.venv\Scripts\python.exe -m pytest
# → 全部通过
```

---

## 面试讲述主线

> "我把一个 Web 自动任务 Agent 从能跑通的 demo 做成了可验证、可解释的工程系统。核心做了四件事：第一，用 Protocol 抽象把视觉抽取路径接入工作流，demo 和真实 Qwen-VL 可替换；第二，解决了双重获取问题——真实 provider 自带浏览器时 workflow 自动跳过；第三，把真实站点评测升级成 provider matrix，8 个样本 × 5 个 provider 在同一张矩阵里比较；第四，加了确定性中文解释层，把 benchmark 结果翻译成一句话结论、失败分析和面试讲法——不调 LLM，纯 heuristic，可复跑。"

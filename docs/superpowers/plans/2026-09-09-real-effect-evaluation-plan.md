# 真实效果评测基线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一个版本化、可重复、多轮运行的真实岗位评测基线，分别衡量来源可达性、岗位字段正确性、验证通过率和推荐质量。

**Architecture:** 保留 `src/web_task_agent/benchmark.py` 负责真实 URL/provider matrix，新增一个评测层负责 ground truth、逐条指标和多轮汇总。原始运行结果继续写 JSON/Markdown，人工标注和汇总报告使用独立版本目录，避免把一次在线运行覆盖成“最新真相”。默认不保存完整网页正文；样本以 URL、元数据和内容哈希为主，必要的脱敏快照单独存储。

**Tech Stack:** Python 3.11+, Pydantic, pytest, existing `EvaluationRunner`, JSON/Markdown/HTML artifacts, optional CSV export for annotation.

---

## 文件结构

- Create: `evaluations/real-job-benchmark-v1/catalog.json` - 固定样本目录和采集元数据。
- Create: `evaluations/real-job-benchmark-v1/ground-truth.json` - 人工标注，不放简历或密钥。
- Create: `evaluations/real-job-benchmark-v1/README.md` - 标注规则、版本边界和更新方式。
- Create: `src/web_task_agent/real_job_evaluation.py` - ground truth 模型、指标计算、多轮汇总和报告渲染。
- Modify: `src/web_task_agent/benchmark.py` - 复用现有 case/provider 运行结果，补充稳定的 case_id 映射和运行元数据。
- Create: `scripts/run_real_job_benchmark.py` - 受控入口，固定数据集版本、provider、轮次和输出目录。
- Create: `tests/test_real_job_evaluation.py` - 指标、缺失标签、跨轮汇总和脱敏测试。
- Modify: `docs/showcase/README.md` - 增加数据集版本、轮次表、指标定义和证据边界。
- Modify: `README.md` - 增加评测命令和“可证明/不可证明”说明。

## Task 1: 固定样本目录和标注协议

**Files:**
- Create: `evaluations/real-job-benchmark-v1/catalog.json`
- Create: `evaluations/real-job-benchmark-v1/ground-truth.json`
- Create: `evaluations/real-job-benchmark-v1/README.md`

- [ ] **Step 1: 写目录格式和校验规则**

目录每条至少包含 `case_id`、`company`、`ats`、`role_family`、`url`、`collected_at`、`content_sha256`、`expected_signal`；禁止写入 API key、Authorization、简历正文和模型原始响应。

- [ ] **Step 2: 选取首批 40 条样本**

覆盖至少 4 类来源、8 个以上公司/ATS、4 个岗位族；每条 URL 做一次人工打开检查，记录无法访问、重定向或页面漂移，不删除失败样本。

- [ ] **Step 3: 写人工标注协议**

每条标注 `title`、`company`、`location`、`role_family`、`required_skills`、`hard_constraints`、`is_relevant` 和 `recommended_top_k`；双人标注时保留 `annotator_a`、`annotator_b` 和 `adjudicated`，单人首版也必须记录 `annotated_by` 与 `annotated_at`。

- [ ] **Step 4: 写目录完整性测试**

运行 `pytest tests/test_real_job_evaluation.py -k catalog -v`，预期所有 case_id 唯一、URL 为 HTTPS、哈希格式正确、ground truth 能与目录一一对应。

- [ ] **Step 5: 提交独立变更**

提交信息：`test: establish versioned real job benchmark catalog`。

## Task 2: 实现字段和推荐指标

**Files:**
- Create: `src/web_task_agent/real_job_evaluation.py`
- Create: `tests/test_real_job_evaluation.py`

- [ ] **Step 1: 先写失败测试**

覆盖：标准化文本后的 title/company/location exact-or-alias accuracy；required skills precision/recall/F1；`is_relevant` 分类准确率；推荐 `precision_at_k`、`recall_at_k`、`ndcg_at_k`；缺失 ground truth 时不计入分母并在报告中列出。

- [ ] **Step 2: 定义纯数据模型**

定义 `GroundTruthLabel`、`CaseEvaluation`、`RunEvaluationSummary` 和 `MultiRunSummary`。指标函数只接收结构化预测与标注，不调用网络或 LLM，确保可重复。

- [ ] **Step 3: 实现指标计算**

所有分数保留原始计数与分母，例如 `title_correct/title_labeled`，避免只输出百分比。对没有人工标签的字段返回 `null`，不能填 0。

- [ ] **Step 4: 实现失败分类映射**

沿用现有 `browser_error`、`no_pages`、`no_extracted_jobs`、`verification_filtered` 等类别，并单独记录 `annotation_missing`、`page_drift` 和 `source_unavailable`，避免把页面不可达混成抽取错误。

- [ ] **Step 5: 运行定向测试**

运行 `\.venv\Scripts\python.exe -m pytest tests/test_real_job_evaluation.py -q`，预期所有纯函数和边界用例通过。

- [ ] **Step 6: 提交指标层**

提交信息：`feat: add ground-truth real job evaluation metrics`。

## Task 3: 实现多轮 benchmark 入口和报告

**Files:**
- Create: `scripts/run_real_job_benchmark.py`
- Modify: `src/web_task_agent/benchmark.py`
- Modify: `tests/test_benchmark.py`

- [ ] **Step 1: 写入口契约测试**

入口接受 `--dataset-version`、`--providers`、`--runs`、`--output-dir`、`--offline`；测试中注入 fake runner，确认同一 case 顺序和同一 provider 集合被每轮复用。

- [ ] **Step 2: 扩展运行元数据**

每轮写 `run_id`、`dataset_version`、`started_at`、`finished_at`、provider、样本数、来源尝试/成功数、页面访问数、有效岗位数、失败类别、耗时和估算成本；不要写密钥或原始响应。

- [ ] **Step 3: 汇总三类结果**

报告分别展示来源可达率、验证通过率、字段准确率、推荐 P@K/R@K/NDCG、失败分布、P50/P95 耗时和跨轮均值/最小值/最大值；明确“来源产出率不等于全网覆盖率”。

- [ ] **Step 4: 生成 JSON、Markdown、HTML**

写入 `evaluations/real-job-benchmark-v1/runs/<run-id>/`，并生成版本根目录的 `summary.json`、`summary.md`、`summary.html`。报告链接只指向已生成的白名单产物。

- [ ] **Step 5: 执行三轮在线评测**

首轮使用 `baseline` 和现有规则/LLM 匹配路径；第二、三轮在不同时间窗口执行。网络失败必须保留为失败记录，不用 Demo 数据替换。

- [ ] **Step 6: 提交评测入口和样例报告**

提交信息：`feat: add repeatable real job benchmark runs`。

## Task 4: 更新 showcase 和发布边界

**Files:**
- Modify: `docs/showcase/README.md`
- Modify: `README.md`

- [ ] **Step 1: 写“已证明/未证明”表格**

已证明：真实链路运行、失败可分类、结果可审计。未证明：全网覆盖率、真实简历推荐准确率、长期稳定性。每个数字都附 dataset version、run_id 和生成时间。

- [ ] **Step 2: 加入历史轮次对比**

展示每轮的 source reachability、valid jobs、field accuracy、P@K、失败类别和耗时，不只展示最好一次运行。

- [ ] **Step 3: 做敏感字段扫描和完整验证**

运行 `\.venv\Scripts\python.exe -m pytest -q`、`\.venv\Scripts\python.exe -m web_task_agent.agent_release_check`、`git diff --check`；扫描报告不得出现 `DASHSCOPE_API_KEY` 的值、Authorization、简历正文或模型原始响应。

- [ ] **Step 4: 提交文档**

提交信息：`docs: publish real job evidence boundaries`。

## 完成门槛

- 固定样本不少于 40 条，至少 3 轮运行结果可复核。
- 至少一项字段指标和一项推荐指标有人工 ground truth；缺失标签不填 0。
- 失败类别可逐条追溯到 case_id 和 URL。
- showcase 不再把单次 `39/47` 描述成覆盖率或推荐准确率。

# Web 自动任务 Agent 项目设计

## 项目定位

本项目面向 AI 工程 / AI 应用实习，目标是构建一个可演示、可评测、可扩展的 Web 自动任务 Agent。第一版聚焦一个强场景：自动寻找 AI 算法 / AI 工程实习岗位，提取岗位 JD，结合用户简历能力做匹配分析，并生成结构化投递报告。

项目不是简单调用大模型总结网页，而是用 LangGraph 明确表达 Agent 工作流，用 browser-use 执行真实网页操作，并加入状态追踪、失败恢复、信息验证和任务评测，让它具备简历项目需要的工程深度。

## 第一版目标

第一版 MVP 的验收目标是：用户输入岗位关键词、城市偏好、个人技能标签或简历文本后，系统自动完成至少 10 个岗位的信息搜集，并输出一份包含岗位列表、匹配分数、能力差距、投递优先级和学习建议的报告。

MVP 不追求自动投递，也不处理需要登录、验证码、复杂反爬的网站。第一版优先选择公开网页、公司招聘页、实习聚合页、GitHub / 开源社区招聘信息等低摩擦来源。

## 核心能力

1. Web 搜索与浏览：通过 browser-use 驱动浏览器搜索岗位、打开页面、滚动、点击、返回和读取页面内容。
2. 任务规划：通过 LangGraph 将任务拆成规划、执行、抽取、验证、记忆更新和报告生成几个稳定节点。
3. 结构化抽取：从网页中抽取岗位名称、公司、地点、职责、技能要求、投递链接、发布时间和来源 URL。
4. 简历匹配：根据用户技能、项目经历和岗位 JD 生成匹配分数，指出强匹配点和短板。
5. 失败恢复：识别页面加载失败、内容缺失、重复岗位、无关页面等情况，并触发重试或跳过。
6. 评测记录：记录每次任务的成功率、访问页面数、有效岗位数、平均执行步骤、失败原因和 token 成本。

## 工作流架构

LangGraph 工作流包含以下节点：

- `InputNormalizer`：规范化用户输入，包括岗位关键词、地点、偏好行业、技能标签和简历文本。
- `Planner`：生成搜索策略，例如查询词、目标站点、需要搜集的岗位数量和停止条件。
- `BrowserExecutor`：调用 browser-use 执行网页搜索、页面打开、点击、滚动和内容读取。
- `PageExtractor`：将网页内容转换为结构化岗位记录。
- `Verifier`：判断记录是否完整、是否重复、是否与 AI 实习方向相关。
- `MemoryUpdater`：保存岗位记录、访问历史、失败页面和用户偏好。
- `Matcher`：根据岗位 JD 与用户背景计算匹配结果。
- `Reporter`：生成 Markdown / HTML 报告，并提供可视化统计数据。

节点之间通过共享状态对象传递数据。状态对象至少包含：用户画像、搜索计划、浏览轨迹、候选 URL、岗位记录、失败记录、匹配结果和评测指标。

## 数据模型

岗位记录 `JobPosting` 包含：

- `title`
- `company`
- `location`
- `source`
- `url`
- `requirements`
- `responsibilities`
- `skills`
- `posted_at`
- `confidence`

匹配记录 `MatchResult` 包含：

- `job_id`
- `score`
- `matched_skills`
- `missing_skills`
- `reason`
- `priority`
- `suggested_actions`

任务运行记录 `RunMetrics` 包含：

- `run_id`
- `started_at`
- `finished_at`
- `pages_visited`
- `jobs_found`
- `valid_jobs`
- `duplicate_jobs`
- `failed_pages`
- `avg_steps_per_job`
- `estimated_token_cost`

第一版使用 SQLite 保存结构化数据。后续如果需要做长期偏好记忆或语义检索，再引入 Chroma / FAISS。

## 技术栈

- Python：主开发语言。
- browser-use：浏览器操作和网页任务执行。
- LangGraph：Agent 工作流编排。
- FastAPI：提供任务启动、状态查询、报告读取接口。
- SQLite：保存岗位、运行记录和失败日志。
- Pydantic：定义状态和结构化数据模型。
- Streamlit：第一版 Dashboard，降低前端开发成本。
- OpenAI / DeepSeek / Qwen：LLM 抽取、规划和总结，可通过配置切换。

## 用户界面

第一版采用 Streamlit Dashboard，包括：

- 输入区：岗位关键词、地点、目标数量、技能标签、简历文本。
- 运行状态区：当前节点、访问页面数、已找到岗位数、失败次数。
- 岗位表格：标题、公司、地点、匹配分、投递链接、来源。
- 岗位详情：JD 摘要、匹配理由、缺失技能、投递建议。
- 评测面板：成功率、有效岗位占比、失败原因分布、平均步骤数。
- 报告导出：Markdown 报告下载。

## 错误处理

系统必须处理以下错误：

- 页面打不开：记录 URL 和错误信息，换下一个候选页面。
- 页面内容为空：重试一次滚动 / 等待，仍失败则跳过。
- 抽取字段缺失：降低置信度，并交给 Verifier 判断是否保留。
- 重复岗位：根据公司、标题和 URL 去重。
- LLM 输出格式错误：要求模型按 schema 重试，连续失败后保留原文摘要。
- 搜索结果质量差：Planner 调整查询词或切换目标站点。

## 评测方案

第一版构建一个小型评测集，包含 20 个任务配置，例如不同关键词、城市和技能组合。每个任务记录：

- 是否完成目标岗位数量。
- 有效岗位比例。
- 平均每个岗位访问页面数。
- 字段抽取完整率。
- 匹配分析是否可用。
- 失败原因分类。

这部分是项目的算法和工程亮点。面试时可以展示 Agent 不是“看起来能跑”，而是有可量化指标。

## 里程碑

### Milestone 1：最小闭环

- 创建项目结构。
- 接入 browser-use，完成单个网页搜索和内容读取。
- 接入 LangGraph，跑通 Planner -> BrowserExecutor -> Reporter。
- 输出第一份 Markdown 报告。

### Milestone 2：结构化抽取

- 定义 Pydantic 数据模型。
- 实现 PageExtractor 和 Verifier。
- 保存岗位记录到 SQLite。
- 支持去重和字段完整性检查。

### Milestone 3：简历匹配

- 支持输入简历文本或技能标签。
- 实现 Matcher。
- 输出匹配分数、强匹配点、缺失技能和投递优先级。

### Milestone 4：Dashboard 与评测

- 实现 Streamlit Dashboard。
- 增加运行状态展示。
- 设计 20 个评测任务。
- 输出评测统计报告。

## 简历表达

推荐简历描述：

基于 LangGraph 与 browser-use 构建 Web 自动任务 Agent，实现 AI 实习岗位检索、网页操作、结构化信息抽取、简历匹配和报告生成。设计 Planner、Browser Executor、Extractor、Verifier、Matcher、Reporter 多节点工作流，并实现状态追踪、失败恢复、重复检测和任务评测，统计任务成功率、有效岗位比例、平均执行步数与失败原因。

## 非目标

第一版不做自动投递，不处理登录态和验证码，不承诺覆盖所有招聘网站，不构建复杂 React 前端，不训练专用模型。项目重点是清晰、稳定、可演示的 Agent 工作流。

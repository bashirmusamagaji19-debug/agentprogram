# 开放互联网岗位搜索 Agent 设计

## 1. 目标与边界

本项目面向 AI 应用 / Agent 工程实习求职。用户通过 Web 运行台输入自然语言岗位需求，系统使用搜索 API 发现开放互联网候选 URL，再通过浏览器打开并验证招聘详情页，最终返回带官方链接、页面证据和匹配理由的真实岗位。

第一版支持范围：

- AI 应用、Agent、RAG、多模态应用和 Python AI 后端岗位；
- 实习岗位为主要目标，也允许用户输入其他职位类型作为查询条件；
- 中国大陆城市和远程为主要地点范围；
- 搜索 API + 浏览器验证为在线路径；
- Fixture provider + 本地页面为离线演示路径。

第一版不承诺全行业、全站点或每次都有结果。真实可用性的定义是：在支持范围内完成真实搜索和详情验证；没有可信结果时返回可解释的零结果或失败状态，不编造岗位。

不在本轮范围内：自动投递、登录态管理、验证码绕过、复杂多租户系统、多 Agent 协作、RAG 知识库、更多搜索供应商、复杂运维后台和大规模模型训练。

## 2. 用户流程

```text
自然语言岗位需求
  -> 需求结构化
  -> 用户确认约束
  -> 生成多组搜索查询
  -> 搜索 API 发现候选 URL
  -> 来源和页面类型验证
  -> 浏览器获取岗位详情
  -> 字段抽取与证据绑定
  -> 硬约束过滤
  -> 相关性匹配与去重
  -> 持久化结果和审计轨迹
  -> Web 展示岗位、证据、轨迹和失败原因
```

用户输入示例：

```text
找北京或远程的 Agent 开发实习，要求 Python、LangGraph 或 RAG，排除纯模型训练和产品经理岗位。
```

系统应解析出岗位类别、地点、雇佣类型、必需技能、偏好技能和排除条件。硬约束由代码执行，LLM 只能提供结构化解析和软匹配建议。

## 3. 系统架构

```text
Web UI
  -> JobSearch API
  -> QueryParser
  -> SearchPlanner
  -> SearchProvider
  -> Candidate URL Queue
  -> SourceVerifier
  -> BrowserDetailLoader
  -> JobExtractor
  -> EvidenceVerifier
  -> ConstraintFilter
  -> JobMatcher
  -> Deduplicator
  -> SQLite / Audit Artifacts
  -> Web UI
```

### 3.1 QueryParser

将自然语言输入转换为版本化的 `SearchIntent`，至少包含：

- `role_keywords`；
- `locations`；
- `employment_types`；
- `required_skills`；
- `preferred_skills`；
- `excluded_roles`；
- `target_count`。

解析结果在前端展示，用户可确认后再执行。解析失败或结构化输出非法时使用确定性关键词提取，并记录 fallback 原因。

### 3.2 SearchPlanner

根据 `SearchIntent` 生成最多 4 组查询，覆盖岗位同义词、地点和技能组合。每个任务最多验证 20 个候选页面。规划器不得直接决定岗位有效性或绕过预算。

### 3.3 SearchProvider

定义可替换接口：

- `TavilySearchProvider`：在线搜索 API，读取环境变量中的 API key；
- `FixtureSearchProvider`：离线演示，返回固定候选 URL。

搜索标题、摘要和 URL 只作为发现证据，不能直接作为最终岗位字段证据。搜索 API 失败必须生成结构化的 `search_api_error`，不能伪装成“无匹配岗位”。

### 3.4 SourceVerifier

验证候选 URL 是否属于公司官方招聘域名或可确认归属的公开 ATS，并拒绝搜索结果页、新闻页、培训广告、聚合转载页和无法确认归属的页面。来源验证结果包含来源类型、归属理由和失败代码。

### 3.5 BrowserDetailLoader

使用现有浏览器抽象打开详情页，支持静态内容、动态渲染和明确的超时/HTTP 错误分类。单 URL 最多重试一次。页面正文、最终 URL、获取时间和内容哈希写入审计 artifact；页面内容只保留实现所需的脱敏证据，不保存 API key 或用户简历正文。

### 3.6 JobExtractor 与 EvidenceVerifier

抽取 `title`、`company`、`location`、`employment_type`、`responsibilities`、`requirements`、`source_url`。每个字段必须带页面证据片段或 DOM 定位信息；缺失字段保持为空，不由模型补写。EvidenceVerifier 拒绝无证据或字段互相矛盾的结果。

### 3.7 ConstraintFilter、JobMatcher 与 Deduplicator

- `ConstraintFilter` 强制地点、职位类型和排除岗位等硬约束，硬约束违反率必须为 0；
- `JobMatcher` 对必需技能、偏好技能和职责相关性给出可解释分数，理由必须引用抽取字段或证据；
- `Deduplicator` 基于规范化 URL、公司/标题/地点组合和官方职位 ID 合并重复结果，保留来源和合并轨迹。

### 3.8 Audit Store

每次运行至少保存：

```text
run-summary.json
search-queries.jsonl
candidate-pages.jsonl
jobs.jsonl
execution-trace.jsonl
failures.jsonl
evaluation-summary.md
```

运行记录包含 provider、模型（如有）、代码版本、开始/结束时间、预算、状态和终止原因。失败结果不能被静默丢弃。

## 4. Agent 决策边界

Agent 运行循环沿用现有 Hybrid Decision Agent 设计：LLM 只能从类型化白名单工具中提出结构化决策，确定性 policy 拥有最终授权权。

允许的工具类别：

```text
parse_query
search_web
verify_source
open_page
extract_job
verify_evidence
match_job
save_result
finish
```

policy 强制控制：查询数量、候选页面上限、URL 重试次数、页面超时、硬约束、终止原因和持久化幂等。规划器非法 JSON、未知动作、超时或 provider 不可用时回退到 deterministic policy。

## 5. Web 前端设计

采用“Agent 运行台”而非静态结果 Dashboard，包含四个视图：

### 5.1 运行任务

自然语言输入框、在线/离线模式、目标数量和搜索状态。提交后展示结构化 `SearchIntent`，允许用户确认或取消。

### 5.2 执行轨迹

实时或轮询展示 action、decision source、target、observation、成功/失败、延迟、fallback reason 和剩余预算。失败恢复必须可见，例如 `open_page -> rendered fallback -> extract_job`。

### 5.3 岗位结果

展示岗位名称、公司、地点、雇佣类型、匹配分数、匹配理由、缺失技能、官方原始链接、证据字段和页面获取时间。结果详情只链接到原始招聘页，不提供自动投递。

### 5.4 评测证据

分别展示自动化测试、冻结真实页面评测和在线运行结果。页面必须区分真实搜索 API、浏览器验证和离线 fixture，不能把 fixture 数字标成真实泛化准确率。

前端必须实现空状态、运行中、成功、部分成功、无可信结果和失败状态；刷新后可查看已完成运行记录。

## 6. 错误处理

错误必须分类并保持语义区分：

- `search_api_error`：搜索服务不可用或 key 无效；
- `source_untrusted`：无法确认官方或 ATS 归属；
- `page_unavailable`：404、403、超时或网络错误；
- `not_job_detail`：页面不是单个岗位详情；
- `extraction_incomplete`：关键字段缺失或没有证据；
- `constraint_rejected`：不满足用户硬约束；
- `duplicate_job`：与已有结果重复；
- `no_trusted_match`：搜索和验证完成但没有可信匹配。

任务必须在候选耗尽、预算耗尽、目标数量达到或不可恢复错误时进入终态，并保存完整 artifact。

## 7. 评测与验收

准备 20 条不同表达的真实用户需求，覆盖 Agent 开发、LLM 应用、RAG、Python AI 后端、多模态、城市组合、远程、排除条件和无结果条件。每条需求检查 Top 5 结果，形成最多 100 条人工复核记录。

验收指标：

```text
需求结构化正确率 >= 90%
最终链接官方/可信 ATS 比例 = 100%
抽样链接实时可访问率 >= 90%
硬约束违反率 = 0%
匹配理由证据支持率 >= 90%
Top 5 人工相关率 >= 80%
重复岗位率 <= 5%
所有失败均有结构化分类
```

固定 4 个异构官方来源和 40 个冻结真实岗位页面作为回归评测基线，不与开放搜索的在线指标混合。在线模式执行 3 轮独立运行，记录来源尝试/成功数、岗位数、证据完整率、失败分类和结果差异。三轮不要求全部成功，但每轮必须正常终止并生成完整 artifact。

## 8. 工程验收

- 保留现有全量测试与覆盖率门禁；
- 新增约 10–15 个开放搜索、来源验证、证据绑定、错误分类、离线 replay 和前端 API 边界测试；
- 离线演示无需 API key、公网或 GPU；
- 在线 key 只从环境变量读取，不能写入日志或 artifact；
- checkpoint replay 不产生重复持久化；
- API 对非法输入、超时和任务失败返回结构化错误；
- 前后端测试、构建、启动和演示命令可在全新环境复现；
- 不提交个人简历、网页敏感数据和运行缓存。

## 9. 项目完成停止线

```text
[ ] 开放搜索 API provider 与离线 fixture provider 均可运行
[ ] 浏览器验证候选详情页并保存字段证据
[ ] Web Agent 运行台可输入自然语言并复盘轨迹
[ ] 真实结果只使用详情页证据和官方/可信 ATS URL
[ ] 20 条用户需求评测完成
[ ] 4 个来源、40 个冻结页面回归基线完成
[ ] 3 轮在线运行生成完整审计产物
[ ] 搜索失败、页面失败、无可信结果明确区分
[ ] 自动化测试与发布检查通过
[ ] README、演示脚本、架构图和面试材料完成
[ ] 所有指标注明数据集、日期、provider 和口径
```

满足停止线后不再增加功能，转入按执行链学习源码、准备 60 秒/3 分钟/10 分钟讲述和模拟面试。

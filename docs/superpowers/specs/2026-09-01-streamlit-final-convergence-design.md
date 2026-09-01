# Streamlit 最终收敛设计

> 日期：2026-09-01  
> 分支：`feature/chinese-job-pipeline`

## 目标

为 Web Task Agent 增加一个本地和单实例云端均可运行的 Streamlit 单页应用，让用户能够填写求职条件、提供简历、运行现有岗位 Agent、检查结果与失败原因，并下载现有 JSON、Markdown、HTML Dashboard 和行动计划产物。

本轮是项目最终收敛，不扩展新的招聘站点、模型供应商、自动投递或多用户后台系统。

## 成功标准

1. 无 API key 时，用户能够在 Streamlit 页面完成内置 Demo 全流程。
2. 页面支持粘贴简历文本或上传 `.md`、`.txt` 文件，并填写关键词、地点、目标岗位数和技能标签。
3. 页面提供三种互斥数据模式：内置 Demo、聚合岗位 JSON、指定岗位 URL。
4. 页面可选择规则模式，以及可选的 LLM 抽取和 LLM 匹配；未配置所需凭据时在运行前给出明确错误。
5. 结果页展示岗位、匹配分、优先级、匹配技能、缺失技能、来源链接、失败分类和 Agent 执行轨迹。
6. 用户能够下载 JSON、Markdown 报告、HTML Dashboard 和行动计划；没有生成的产物不显示下载按钮。
7. Streamlit 层不复制抽取、验证、匹配、持久化或报告业务规则。
8. 原有测试与覆盖率门禁不回归，新增的界面适配逻辑具有确定性单元测试。
9. README 提供本地启动和 Streamlit Community Cloud/Render 风格部署说明，并明确单实例、临时文件和 SQLite 限制。

## 非目标

- 不实现用户注册、登录、权限或多租户隔离。
- 不实现任务队列、分布式 worker、定时任务或后台常驻运行。
- 不实现简历 PDF/DOCX 解析；本轮只接受文本、Markdown 和 TXT。
- 不自动投递岗位，不在用户确认前产生外部业务副作用。
- 不把本轮合成简历结果描述成真实求职效果。
- 不解决网易和 `wecruit.hotjob.cn` 的站点适配问题。

## 架构

Streamlit 是现有应用之上的薄适配层：

```text
Streamlit form
  -> UiRunRequest validation
  -> existing WebTaskWorkflow / aggregator loader
  -> existing verifier + matcher + repository
  -> existing reporter + dashboard + action plan
  -> UiRunResult
  -> tables, diagnostics and downloads
```

新增 `streamlit_app.py` 负责布局与 Session State。新增 `streamlit_runner.py` 负责把页面输入转换成现有业务对象并调用现有工作流。这样单元测试无需启动浏览器，也不需要通过 Streamlit widget API 验证核心编排。

CLI 和 Streamlit 共用现有领域模型及业务模块。若实现过程中发现 CLI 中存在一段必须复用但无法调用的组装逻辑，只抽取该段为小型公共函数，不进行无关的 CLI 重构。

## 组件

### `streamlit_runner.py`

定义：

- `UiDataMode`：`demo`、`aggregator`、`seed_urls`。
- `UiRunRequest`：关键词、地点、目标数量、技能、简历文本、数据模式、聚合文件或 URL、LLM provider 选项和输出目录。
- `UiRunResult`：岗位、匹配结果、指标、失败记录、执行轨迹和可下载产物路径。
- `validate_ui_request()`：执行与 CLI 等价但面向表单的前置校验。
- `run_ui_request()`：异步运行一次任务并生成产物。
- `read_download_artifact()`：限制下载目标为本次运行返回的产物，按文本或字节读取。

所有 provider key 继续从环境变量读取，不进入 `UiRunRequest`、Session State、日志、JSON 或报告。

### `streamlit_app.py`

页面使用安静、工作型布局：

- 侧栏：数据模式、模型选项和高级设置。
- 主区表单：关键词、地点、目标数量、技能、简历输入。
- 运行状态：spinner、成功摘要或结构化错误。
- 结果 Tabs：`岗位结果`、`失败与诊断`、`执行轨迹`、`下载`。

岗位表格保持固定列：岗位、公司、地点、匹配分、优先级、匹配技能、缺失技能和链接。不会用大量装饰卡片包装页面区块。

### 部署文件

- 在项目依赖中加入受约束的 Streamlit 版本。
- 增加根目录 `streamlit_app.py` 作为稳定部署入口，内部只调用包内 `main()`。
- 增加 `.streamlit/config.toml`，设置 headless、端口从平台参数接管、关闭使用统计收集。
- README 记录本地命令和云平台启动命令。

不提交 API key、`.env`、运行数据库或用户简历。

## 数据模式

### 内置 Demo

默认选择。使用确定性 fixture，不调用公网和模型，确保本地首次启动与云端公开演示可以稳定完成。

### 聚合岗位 JSON

接受页面上传的 JSON 文件。文件仅在当前运行中解析；使用现有 `AggregatorRepoSource` 和内容解析链。无有效岗位时展示实际失败分类，不回退到伪造 Demo 结果。

### 指定岗位 URL

接受每行一个 `http` 或 `https` URL。复用现有 URL 安全校验、页面加载、官方 API、抽取和 verifier。非法 URL 在运行前显示；站点失败在诊断 Tab 中逐条展示。

## LLM 选项

默认关闭 LLM，保证零凭据演示。

用户可以分别开启 LLM 抽取和 LLM 匹配，并从现有 provider 集中选择。运行前检查对应环境变量是否存在，只报告变量名缺失，不显示或保存变量值。真实 provider 异常作为可读错误或 URL 级失败返回，不用 Demo 结果掩盖。

第一版 Web UI 不开放任意 base URL、模型 ID或 API key 输入，减少错误配置与密钥泄漏面。

## 状态与并发

一次点击对应一个同步等待的运行。运行期间禁用重复提交；结果保存在当前 Streamlit Session State 中，页面 rerun 后仍可查看本次结果。

每次运行使用独立的输出子目录和 run ID，避免同一实例中不同会话覆盖产物。SQLite 仍是单实例本地存储，不宣称跨实例一致性。云平台重启后文件和历史记录可能丢失。

## 错误处理

错误分三层显示：

1. 表单错误：缺少简历、无 URL、文件格式错误、目标数越界、缺少 provider 环境变量。
2. 任务错误：聚合格式错误、所有页面失败、provider 请求失败。
3. 页面级失败：超时、HTTP 错误、空页面、内容过短、验证拒绝和重复岗位。

用户看到短消息和建议动作；诊断 Tab 保留错误类别及目标 URL。页面不显示堆栈、密钥、完整环境变量或不必要的原始响应。

## 测试策略

### 单元测试

- 三种数据模式的请求校验。
- 简历上传解码和技能解析。
- provider 环境变量存在性检查，不读取或返回密钥值。
- Demo 请求通过 fake/现有 fixture 完成，并返回岗位和全部预期产物。
- 聚合文件与 URL 模式正确调用既有边界。
- 无结果和失败结果保留诊断，不伪造岗位。
- 下载函数只能读取本次 `UiRunResult` 中登记的文件。

### 集成验证

- 使用项目 `.venv` 运行全量 pytest 与 coverage 门禁。
- 运行项目规定的聚焦 Ruff/release check，而不是用历史全仓 Ruff 债务阻塞本轮。
- 启动 Streamlit 本地服务，检查健康端点。
- 使用浏览器分别验证桌面和移动视口：表单可填写、Demo 可运行、表格不溢出、Tabs 和下载按钮可操作、无元素重叠。

真实 provider 和真实招聘站点属于可选验收，只有获得有效凭据并真实运行成功后才记录为通过。

## 本地与云端交付

本地启动：

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

云端启动命令：

```text
streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port $PORT
```

云端文档必须声明：这是单实例演示部署；本地文件和 SQLite 不保证持久；多个实例不会共享任务状态；真实网站访问可能受云出口、反爬和页面变化影响。

## 最终验收清单

- Demo 模式从表单提交到结果和下载完成。
- 指定 URL 与聚合文件两种入口的输入校验和失败呈现完成。
- 无 API key 的默认体验可用。
- API key 不进入页面状态、日志或产物。
- 本地 Streamlit 健康检查通过。
- 桌面和移动浏览器视觉检查通过。
- 全量测试与覆盖率门禁通过。
- 聚焦 Ruff、构建和 Git diff 检查通过。
- README、最终 work-log、项目叙述和已知限制一致。
- 真实简历验证若未执行，明确列为唯一业务证据缺口，不阻塞工程交付完成。

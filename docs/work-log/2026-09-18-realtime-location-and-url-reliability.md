# Work Log - 2026-09-18 实时地点与岗位链接可靠性修复

## 背景

真实用户体验暴露了两个会直接破坏项目可用性的问题：

1. 岗位详情页明确标注了工作地点，但系统抽取结果大量显示 `Unknown Location`。
2. 部分岗位有 URL，但点开后是“页面不存在”或“职位已下线”。

这不是展示层的小问题：地点是推荐和用户判断的核心字段，无效链接则会让“实时岗位”退化成无法验证的 demo。

## 复现与证据

- 修复前实时运行 `run-d11b120a` 返回 9 个岗位；除腾讯外，其余 8 个地点均为 `Unknown Location`。
- 构造 `DiscoveredJob(location="上海")` 并进入页面抽取链路时，已发现地点在 `BrowserPage.metadata` 边界被丢弃，最终稳定复现为 `Unknown Location`。
- 星动纪元的原 Moka 链接打开后显示“您访问的页面不存在”；官网实际使用 hash 路由 `#/job/{id}`。
- 小鹏的原飞书链接缺少 `/detail`，打开后显示页面不存在；补齐路由后可显示职位正文。
- 智元招聘 API 未带 `website-path: internrecruitment` 时，返回了错误门户下的过期/混合职位；这些 URL 在前端显示“该职位已下线”。

## 根因

### 1. 地点在发现层到抽取层之间丢失

`DiscoveredJob` 已经有结构化 `location`，但 `build_discovered_page` 和 `AggregatorPageLoader._to_page` 没有将它传入 `BrowserPage.metadata`。`PageExtractor` 又只依赖正文标签和启发式推断，因此当详情正文未含明确字段时，已有的地点被降级成 `Unknown Location`。

此外，LLM 返回空地点时会覆盖先前已获得的地点，形成第二次信息丢失。

### 2. 详情页路由模板已与招聘站当前前端不一致

- Moka 的星动纪元站点使用 hash 路由，而适配器生成了普通 path 路由。
- 小鹏飞书招聘详情页需要 `/detail` 后缀，适配器模板未包含该段。

### 3. 智元的列表 API 依赖门户上下文

智元实习招聘与其他门户共用飞书招聘域名。只提交 `portal_type`/`portal_entrance` 不足以限定正确站点，还需要 `website-path: internrecruitment` 请求头。缺少该上下文时，列表“返回了数据”，但数据并不属于当前实习门户。

### 4. 来源字段存在 schema 变体

Moka 部分岗位使用 `locations[].provinceName/cityName`，而不是 `locationNames`。飞书来源的主城市为空时，直接拼接会生成以“、”开头的地点。

## 解决方案

- 在发现页和聚合页的 metadata 中保留 `discovered_location`。
- 抽取地点的优先级调整为：正文显式字段 > 发现层结构化地点 > 正文推断 > `Unknown Location`。
- LLM 返回空地点时保留已抽取的地点，不再用空值覆盖。
- 修正星动纪元和小鹏的 URL 模板。
- 为 JSON transport 增加自定义请求头支持；智元适配器带上 `website-path`，并只保留实习类型。
- 增加 Moka 地点 schema 兼容，并过滤飞书地点拼接中的空值。

主要修改文件：

- `src/web_task_agent/extractor.py`
- `src/web_task_agent/job_sources.py`
- `src/web_task_agent/official_list_families.py`
- `src/web_task_agent/official_list_source.py`

## 测试与验证

按“先失败、后修复”增加了回归测试，覆盖：

- 发现层地点进入页面 metadata。
- 抽取器的地点回退顺序和 LLM 空值保护。
- 星动纪元的 hash URL 与 Moka `locations[]` 地点。
- 智元 `website-path` 请求头、实习类型过滤和地点。
- 小鹏详情页 `/detail` URL。

本次修复后的本地验证结果：

- `pytest`: `525 passed in 27.56s`
- `ruff check`: 通过
- `git diff --check`: 通过（仅有 Windows 换行符提示）

修复后实时运行 `run-3682fdae` 对小米、米哈游、星动纪元、智元、小鹏进行了复核，关键样本如下：

| 公司 | 岗位样本 | 抽取地点 | 详情页复核 |
|---|---|---|---|
| 小米 | 大模型算法实习生 | 深圳 | 列表来源有效 |
| 米哈游 | 视觉设计实习生 | 上海 | 列表来源有效 |
| 星动纪元 | VLA 相关岗位 | 北京市 | 修正后 hash URL 可打开 |
| 智元 | 端到端双足导航算法工程师 VLN | 上海 | 当前实习门户 URL 可打开 |
| 小鹏 | AI Infra 相关岗位 | 北京、上海、广州 | 补齐 `/detail` 后可打开 |

## 仍然存在的边界

- 当前多数来源的 `url_verification` 是 `list-attested`：它能证明 URL 来自当次官方列表响应，不等于每个详情页都已被独立浏览器验证。
- 岗位可能在列表抓取后被企业下线，因此任何实时系统都无法保证 URL 永久有效。
- 如果官方列表和详情正文都没有地点，系统仍会返回 `Unknown Location`，而不是编造地点。
- 本次修复了已复现的适配器问题，不代表任意招聘站都已获得相同的实时可靠性。

后续如果继续提升实时可靠性，应优先增加“按来源的在线健康检查 + 详情页失效分类”，而不是将 `list-attested` 包装成全链路验证。

## 二次复盘：修复后界面仍显示 Unknown

用户再次体验时，截图显示除腾讯外的岗位仍大量为 `Unknown Location`。这次不是前述修复逻辑失效，而是运行中的 Streamlit 长驻进程没有重新加载已修改的 Python 模块。

### 证据

- Streamlit 服务于 09:26 启动，早于地点和 URL 修复。
- 截图对应的新任务 `run-134cfae5` 虽在 10:13 生成，但其 `pages[].metadata` 仍没有 `discovered_location`，且星动纪元、小鹏仍是旧 URL 模板。
- 同一代码库的新 Python 进程 `run-3682fdae` 已能正确输出深圳、上海、北京等地点。

这组对照证明：界面不是在展示文件缓存，而是 Streamlit 进程内的已导入模块仍为旧版。停止原启动会话并重启服务后，全来源 15 条复跑 `run-15e317e0` 从有效结果中仅剩 2 条 Unknown。

### 网易 schema 漂移

重启后仍未知的网易岗位，根因是官方 API 当前返回：

```json
{
  "workPlaceList": [1],
  "workPlaceNameList": ["北京市"]
}
```

旧适配器只读取 `workLocationName` / `addressName`。先增加回归测试并确认失败，再让适配器优先解析 `workPlaceNameList`，并保留旧字段回退。修复后真实 API 冒烟返回：

```text
('大模型算法实习生', '北京市', 'https://hr.163.com/job-detail.html?id=65260')
```

### 银河通用岗位本身未公开地点

银河通用“算法招聘实习生”的列表 API 和详情 API 均返回 `locations: []`。浏览器实际页面中，“职位信息”只有职位名称、职位性质和职能类型，没有工作地点。因此该条继续显示 `Unknown Location`，不使用企业总部地点猜测。

### 最终在线验证

网易适配器修复后再次重启 Streamlit，然后用全默认来源、目标 15 条执行 `run-6f624870`：

- 有效岗位：10
- 有地点：9
- `Unknown Location`：1（仅银河通用，官方页/API 未提供）
- 网易“大模型算法实习生”：北京市
- 小米：深圳；小红书：北京市、上海市；米哈游：上海；星动纪元：北京市

完整 `pytest -q` 与 `ruff check .` 均以退出码 0 完成。

### 排查工具问题

附件图像首先尝试通过配置的外部视觉接口识别，但接口返回 HTTP 401 `invalid_api_key`。本次改用本地原图检查与运行产物数据交叉验证；该 401 是本机视觉技能配置问题，与岗位 Agent 的地点抽取无关。

## 三次复盘：搜索地点未约束结果

用户在 Streamlit 中输入“地点：深圳”后，`run-b9adb0a8` 仍返回纯北京、上海和未知地点岗位。运行产物确认请求中已有 `location="深圳"`，因此不是界面传值失败。

### 根因

- 官方列表模式的 `_build_browser` 只对来源调用 `discover(limit=...)`，没有将用户地点作为发现条件。
- 工作流 verifier 只校验岗位质量和 AI 相关性，没有对 `UserProfile.location` 做结果过滤。
- matcher 只计算技能匹配，不读取用户地点。

结论：地点原本只是任务记录/查询文本的一部分，不是官方 API 模式的结果约束。

### 动态解决方案

- 新增无状态 `location_matches(requested_location, actual_location)`，每次调用都显式传入当次请求地点。
- 工作流在岗位质量校验后、技能匹配和持久化前，使用本次 `state.user.location` 严格过滤。
- 代码中没有固定的“深圳”条件；输入北京、上海或远程时使用当次值。
- 支持行政区后缀、多城市字符串和 `Remote`/“远程”别名。腾讯“深圳总部、北京、上海…”可同时匹配深圳或北京。
- `全国` / `不限` 为通配输入，保留原有不限地点行为。
- 具体地点请求下，异地和 `Unknown Location` 岗位被排除，并在 `filtered_jobs` 记录 `requested` 与 `got`，不用异地岗位凑满目标数。

### TDD 与自动验证

1. 纯函数测试先因 `web_task_agent.location_filter` 不存在而失败，实现后 15 个参数化地点用例全部通过。
2. 工作流测试先证明深圳和北京两次运行都错误保留全部岗位，接入动态过滤后，同一工作流实例可根据每次输入切换结果。
3. 严格过滤改变了旧 Demo 测试中“Remote 也返回非远程岗位”的隐含假设；非地点目标的产物测试改用“全国”。
4. 完整 `pytest -q` 退出码为 0，`ruff check .` 输出 `All checks passed!`。

### 真实来源复核

| Run ID | 当次输入地点 | 有效结果 | 结果地点 | 不匹配结果 |
|---|---:|---:|---|---:|
| `run-63753149` | 深圳 | 2 | 腾讯（含深圳的多城市）、小米（深圳） | 0 |
| `run-8ce1e281` | 北京 | 5 | 腾讯、网易、小红书、星动纪元、京东均含北京 | 0 |
| `run-327ae4a0` | 全国 | 8 | 深圳、北京、上海、多城市与官方未标注地点 | 0（通配） |

深圳运行的诊断明确排除了网易北京、小红书北京/上海、米哈游上海、星动纪元北京、京东北京和银河通用未知地点。北京运行则排除小米深圳、米哈游上海和银河通用未知地点，证明结果由当次输入驱动，而不是硬编码深圳。

本轮未重新定义“岗位关键词”的硬过滤语义；它仍属于相关性发现/匹配问题，应与地点条件分开评估。

## 四次复盘：来源健康度与永久失效链接门禁

官方列表模式记录的是本轮最终汇总状态，而不是逐次尝试的完整历史：每个配置来源记录最终 status、attempts、jobs_discovered，失败时才带脱敏摘要；同一来源如果后续补足轮成功，最终会归为 healthy/empty，不保留此前失败摘要。单来源失败仍不会中断其他来源，且 empty 与 failed 不混淆。

详情加载链新增稳定失败分类：404/410、E1005 或明确短下线页属于 `removed`，禁止用列表 JD 兜底；401/403/429 属于 `blocked`，5xx/超时属于 `transient`，短空壳属于 `empty_shell`，这些非永久信号仍允许诚实降级为 `list-attested`。分类结果会进入 JSON metadata 和 Streamlit“失败与诊断”页。

验证边界保持不变：`list-attested` 只证明岗位来自当次列表响应，不代表详情页已由真实浏览器完成渲染验证；临时网络故障也不等于岗位已经下线。

### 本轮自动验证

- 聚焦可靠性测试：退出码 0，`148 passed`、`0 skipped`。
- 完整 `pytest -q`：退出码 0，`636 passed`、`1 skipped`；本次收集到的测试无失败。
- `ruff check .` 与 `git diff --check`：退出码均为 0。
- 离线 Streamlit runner smoke：退出码 0，实际输出 `run-b0212b33 2 ['action_plan', 'dashboard', 'json', 'report']`，生成 2 个岗位和 JSON、Markdown、HTML Dashboard、行动计划四类产物。默认 `UiRunRequest(data_mode="demo", target_count=2)` 会有一个候选页面被 verifier 过滤，原因包括 `confidence below 0.5`、`missing requirements and responsibilities`、`not relevant to AI job direction`；因此 smoke 使用与现有 runner test 相同的确定性参数：`data_mode="demo"`、`keyword="AI intern"`、`location="全国"`、`target_count=2`、`skills=["Python","LangGraph"]`、`resume_text="Built a LangGraph job agent with Python."`。

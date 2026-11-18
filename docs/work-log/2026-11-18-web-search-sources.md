# 网页搜索、会话额度与大厂来源核验（2026-11-18）

## 用户需求与实际行为

数据模式增加「网页搜索」，由现有 DeepSeek Search 公开搜索 API 提供线索；Agent 从空 seed 列表开始执行 SearchJobsTool。搜索摘要只提供候选标题和 URL，不作为岗位正文或仍在招聘的证据。详情读取经过官方 API、HTTP 与必要的浏览器渲染，交付前仍需本轮浏览器回执：岗位标题、JD 正文及可见启用的申请入口均匹配。

同一会话模型运行上限从 3 调为 30，沿用原计数器；搜索也计入用量。配置校验在扣减之前完成。搜索最多三组查询、十个受检候选，并服从目标岗位数量对应的整轮截止时间。保存前确认恢复不会重新搜索或重复消耗模型调用，恢复进度、来源归档与最终交付证据也有回归覆盖。

用户进一步要求保留大厂：本机初筛保留 8 家；随后增加云端复核，理想在 Streamlit 与 GitHub 云端均持续超时，移到受限说明，最终可选来源为 7 家。智元等小厂从当前列表移除，旧适配器与历史结果仍可读取。未核验通过的大厂列出具体限制，并可通过网页搜索或指定真实岗位 URL 再找线索。

## 真实来源验收

使用生产 `run_ui_request` 入口、Hybrid policy、全国、目标 2、210 秒，无个人简历、模型关闭。每条交付都经过新的真实浏览器检查，不自动投递简历。公开明细、Run ID、URL 和回执见 [windows-summary.json](../results/source-validation-20261118/windows-summary.json)。

| 公司 | 抽查关键词 | 交付/目标 | 结论 |
|---|---|---|---|
| 小米 | AI Agent 实习 | 2/2 | 样本通过 |
| 小红书 | AI Agent 实习 | 2/2 | 样本通过 |
| 小鹏 | AI Agent 实习 | 2/2 | 样本通过 |
| 美团 | AI 实习 | 2/2 | 样本通过 |
| 网易 | AI 实习 | 2/2 | 样本通过 |
| 米哈游 | AI 实习 | 2/2 | 样本通过，另有 3 个待核验未交付 |
| 理想汽车 | 实习 | 2/2 | 本机样本通过，但云端发现接口持续超时，已移到受限来源 |
| 蔚来 | AI 实习 | 1/2 | 部分可用；其余详情未通过，未进入交付 |

累计 15 条，不能解释为每个岗位、城市或未来请求均成功。来源请求成功、查询无匹配候选、详情不通过分别统计；HTTP 壳页也不直接等同于最终浏览器失败。

其他大厂本轮结果：腾讯校招接口空列表；百度接口拒绝访问；华为现有适配器只请求社招、不能证明实习覆盖，申请入口检查也不稳定；京东和携程有候选但招聘类型或详情检查未通过；比亚迪正文不足，拼多多缺少完整岗位字段/条件证据。没有将这些公司标成全站没有岗位，也没有绕过鉴权限制。

公司与招聘域名以官方入口核对：[小米](https://hr.xiaomi.com/)、[小红书](https://job.xiaohongshu.com/)、[小鹏](https://www.xiaopeng.com/)、[美团](https://zhaopin.meituan.com/)、[网易](https://hr.163.com/)、[米哈游](https://jobs.mihoyo.com/)、[理想](https://www.lixiang.com/employ.html)、[蔚来招聘](https://nio.jobs.feishu.cn/)。域名归属不是运行可用性的替代证明。

## 本轮找到并修复的原因

- 美团使用过时 `/jobdetail` 详情地址，改为官网现行 `/web/position/detail?jobUnionId=...`。发现接口已返回完整职责/要求，直接复用官方列表正文，避免再次在未过滤列表前十页中找不到同一岗位并耗尽时间；保存时仍独立检查网页。
- 网易完整岗位页的导航「登录/注册」被误当作登录拦截；仅保留明确登录后可见、验证码等阻断信号。申请按钮「申 请」的字符间空格现在能识别，隐藏/禁用按钮仍不通过。
- 米哈游 URL 的 `#/campus/position/<id>` 被去重逻辑删除，多个不同岗位合并成一条。现在保留 SPA 路由身份，普通文档锚点仍忽略。
- 蔚来详情路由修正，并增加已注册官方详情 API 读取；不再由普通 HTTP 空壳/404 单独决定所有候选结果。列表与详情状态不一致时仍过滤，不伪造在线证据。
- 多来源发现改为最多四路并发，独立传输层和发现截止时间，轮转合并成功结果；慢源不会抹掉快源的岗位，发现只占部分整轮预算，保留详情核验时间。
- 来源指标使用过滤后的 candidate_count，接口返回几十条但没有满足用户条件时展示「无匹配候选」。搜索失败尝试、详情策略、HITL 恢复后的交付证据均进入归档。

## 网页搜索实测

真实 DeepSeek 搜索与文本抽取开启，未传简历，目标 1：`run-e2690a3c` 成功。发现 10 条、选择 3 条、检查 2 条，交付深圳医学科学院「AI Agent工程师实习生（临床研究方向）」1 条；第一条因实习条件证据不足被过滤。独立浏览器确认真实正文与可用申请入口，公开轨迹见 [web-search-summary.json](../results/source-validation-20261118/web-search-summary.json)。

使用现有配置，没有更换密钥，没有把密钥写入源码、日志或公开验收产物。Search API 依据 [DeepSeek 官方文档](https://api-docs.deepseek.com/guides/search/)接入。

## 验证与发布

实现前基线 1197 passed、1 skipped；新增搜索、额度、SPA 身份、来源隔离、导航登录及空格按钮、恢复归档等回归。最终全量测试、CI、云端来源抽查与线上页面验收结果见下文。云端手动审计入口为 `.github/workflows/live-source-audit.yml`，使用合成空简历、真实公开招聘数据，不需要模型密钥。

### 云端复核及后续修复

首次 Linux 真源检查 [44253261530](https://github.com/bashirmusamagaji19-debug/agentprogram/actions/runs/44253261530) 对八家公司均已输出结果：小米、小红书、小鹏、美团、米哈游各 2 条，蔚来 1 条，网易与理想 0 条。检查完成后 Python 背景清理未结束，手动取消工作流以收尾，公开产物已成功上传；不能把此工作流算成全部通过。原始汇总见 [cloud-summary.json](../results/source-validation-20261118/cloud-summary.json)。

网易在 Streamlit 的 `run-1071077d` 同样为 0 条，证据显示浏览器落到英文首页 `Home | NetEase Careers`。使用相同公开详情 `id=65260` 做单变量对照：`en-US,en` 自动重定向到 `/index.html`（1001 字正文）；`zh-CN,zh` 保留岗位 URL、返回「职位详情 | 网易社会招聘」（915 字正文）。因此统一给真实浏览器设置 `--lang=zh-CN`、`--accept-lang=zh-CN,zh`。真实浏览器冒烟同时读取 `navigator.language`，在 Linux CI 中要求 `zh-CN`，不只检查配置字面量。

理想的 Streamlit `run-c35acda3` 在来源发现阶段耗尽 90 秒，0 候选；Linux 210 秒抽查也超时。没有将本机可用当成部署环境可用，移出当前可选名单，在「其他大厂来源的限制」明确说明。

线上网页搜索实际运行 `run-99675ae1` 已完成：10 个发现、3 个选定、3 个检查、1 个交付；轨迹可见 `search_jobs`、`verify_job` 与 `save_results`。本次交付美国亚利桑那州立大学职业网站收录的 koderAI 实习岗位，独立浏览器检查通过；无个人简历，技能仅 Python。额度从 30 变为 29。公开 UI 观察记录见 [cloud-ui-search.json](../results/source-validation-20261118/cloud-ui-search.json)。「全国/不限」现有逻辑不限制工作地点，网页搜索可以返回海外岗位；需要严格地域条件时填写具体城市，不能把这次结果称作中国本土岗位验收。

八源并行发现实测 16.03 秒返回 20 个轮转合并候选；理想在「AI 实习」条件下筛选后为 0，但其他七源的候选保留。见 [multi-source-discovery.json](../results/source-validation-20261118/multi-source-discovery.json)。

CI 全量检查发现聚合模式的注入工厂被官方来源 `isinstance` 判断误伤，已修为仅官方模式按并发协议调用；原失败测试先复现、修复后通过。另一处搜索筛选会把 `#/...` SPA 岗位当首页，已补 6 个详情/列表回归并修复。额度显示改为运行后立即刷新。

发布验证：`2fc0d57` 的 [master CI 44253940482](https://github.com/bashirmusamagaji19-debug/agentprogram/actions/runs/44253940482) 全部通过：1224 passed、1 skipped，覆盖率 88.22%，真实 Linux 浏览器冒烟通过。`5fb9868` 的 [CI 44254455900](https://github.com/bashirmusamagaji19-debug/agentprogram/actions/runs/44254455900) 也成功。本机相关定向测试通过；本机覆盖率全量在旧数据集逐页重复校验上耗时很长，已停止，完整覆盖率结果使用上述 Linux CI，未宣称本机全量通过。

语言修复后的 Linux 复测 [44256549557](https://github.com/bashirmusamagaji19-debug/agentprogram/actions/runs/44256549557) 已完成，网易 `run-876b2711` 检查 2 条、交付 2 条，标题、正文和启用的投递入口均由新浏览器回执确认；理想 `run-e2ad2612` 再次在发现阶段耗尽 210 秒，0 候选。因此该审计整体为 failure，不能称作两源全部通过。公开结果见 [cloud-recheck-summary.json](../results/source-validation-20261118/cloud-recheck-summary.json)。结合首轮云端证据，当前保留七家均至少有一条云端新鲜核验交付，共 13 条；蔚来仍为 1/2 部分成功。

最终代码 `9334faa` 的 [master CI 44258731936](https://github.com/bashirmusamagaji19-debug/agentprogram/actions/runs/44258731936) 全部通过：1224 passed、1 skipped，覆盖率 88.22%，Ruff 与真实 Linux 浏览器检查均通过；[功能分支 CI 44258731899](https://github.com/bashirmusamagaji19-debug/agentprogram/actions/runs/44258731899) 也全部通过。同一提交的 [部署分支 CI 44258731739](https://github.com/bashirmusamagaji19-debug/agentprogram/actions/runs/44258731739) 初次出现两项旧时间测试失败：`test_direct_hitl_resume_is_bounded_and_timeout_persists_terminal_state[recover_hitl]` 在 0.3 秒预算内未到暂停点；`test_human_wait_does_not_spend_hitl_active_deadline[resume_hitl]` 在同样预算内未完成存盘。此前 `057439f` 的 master CI 也有后一项参数 `recover_hitl` 波动，而该提交另两分支成功。没有修改生产时限或删掉断言以掩盖，部署分支失败任务已发起重跑；不宣称三分支每次 CI 都通过。

所有改动在隔离工作树完成，部署分支为 `master` 与 `feature/chinese-job-pipeline`；没有覆盖原始工作区的未提交修改。网页搜索、30 次额度及此前的八源列表已在真实 Streamlit 页面操作验收；随后浏览器语言修复与七源名单已推送部署分支，并用 Linux 真源复测覆盖。最终版本推送后未再人工操作线上页面，不将先前页面截图或观察当作最终七源界面的复验。

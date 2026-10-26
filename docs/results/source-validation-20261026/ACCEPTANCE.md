# 当前问题修复及全量测试验收（2026-10-26）

验收工作区：`Agent/.worktrees/agent-improvements`。原 checkout 与既有未提交改动保持原状；未提交或合并本工作区的混合改动。

## 结论

本轮遗留的“导航先加载导致正文等待提前结束”和“MiniMax、生数详情链接缺少 /detail”已完成修复和真实链路验证。全量测试 **1145 passed, 1 skipped**，耗时 **438.17 秒**，覆盖率 **88.03%**（阈值70%）。42个注册来源均有检查记录：**41个实际执行、网易1个因已有访问限制未执行**；**9个来源共交付20条岗位**，另有9条待核验候选未计入交付。这不表示42个来源均已可用。

## 修复内容

- 页面核验等待预期岗位的标题和JD正文就绪，长导航/页脚不再单凭字符数触发结束；最多等待8秒，并服从本次总时限。通用页面读取保留原有行为。
- 对照官网列表的真实链接，将 MiniMax、生数生成地址改为 `/index/position/<id>/detail`；指定旧地址的API读取也返回正确的 canonical URL。保留完整岗位ID，并继续验证在线状态、标题、正文和可见启用的投递入口。
- 继承并复核上次已完成的实习证据过滤、美团公开请求格式、百度拒绝访问诊断和停止重复扫描修复。未将“实习经历优先”当作实习岗位证明。
- 新增8个详情路由回归用例；修复前观察到6个失败（其余2个正确路由已有支持），修复后定向111项通过。SPA等待相关6项回归也包含在完整测试中。

## 发布检查

| 检查 | 结果 | 日志 |
|---|---|---|
| 完整pytest及覆盖率 | 1145通过、1跳过；88.03% | [完整日志](../../../portfolio-artifacts/source-validation-20261026/final/full-regression.log) |
| Ruff src/tests | 通过 | [日志](../../../portfolio-artifacts/source-validation-20261026/final/ruff.log) |
| Wheel构建 | web_task_agent-0.1.0-py3-none-any.whl成功 | [日志](../../../portfolio-artifacts/source-validation-20261026/final/wheel.log) |
| Doctor | 虚拟环境、核心依赖与输出位置通过 | [日志](../../../portfolio-artifacts/source-validation-20261026/final/doctor.log) |
| 严格HITL | pause_rate=1.00，rejected_effects=0，duplicate_effects=0 | [日志](../../../portfolio-artifacts/source-validation-20261026/final/strict-hitl.log) |
| git diff --check | 退出码0 | [日志](../../../portfolio-artifacts/source-validation-20261026/final/diff.log) |

唯一跳过项为 `tests/test_run_store.py:155` 的 PostgreSQL 集成：未提供 `TEST_DATABASE_URL` 与 psycopg；未将其算作通过。SQLite路径正常验证。

## 真实来源测试

每来源独立走现有 `run_ui_request` 生产入口，关键词 `AI Agent 实习`、全国、目标3、本轮候选上限10、总时限90秒；4个来源并发。模型关闭、不读取个人简历、不自动提交求职申请。零结果只表示本轮条件、适配器与预算下没有交付。

| 来源 | 候选 | 交付 | 待核验 | 秒 | 状态与原因 |
|---|---:|---:|---:|---:|---|
| tencent-campus | 0 | 0 | 0 | 7.078 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| meituan | 20 | 0 | 6 | 90.14 | 达到90秒总时限，未交付 |
| unitree | 0 | 0 | 0 | 0.625 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| xiaomi | 20 | 3 | 0 | 25.5 | 已达到目标并入库 |
| netease | 0 | 0 | 0 | 0 | 沿用既有浏览器访问限制，未执行 |
| xiaohongshu | 13 | 3 | 0 | 20.937 | 已达到目标并入库 |
| mihoyo | 6 | 1 | 0 | 43.781 | 已核验并入库；不足目标时保留 partial |
| baidu | 0 | 0 | 0 | 0.5 | upstream denied access (status=no-auth, message=illegal-visit) |
| bing-serp | 1 | 0 | 0 | 1.922 | empty_shell |
| xpeng | 8 | 3 | 1 | 42.546 | 已达到目标并入库 |
| agibot | 12 | 3 | 0 | 28.172 | 已达到目标并入库 |
| galaxea | 0 | 0 | 0 | 3.657 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| robotera | 0 | 0 | 0 | 17.437 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| fourier | 1 | 1 | 0 | 13.281 | 已核验并入库；不足目标时保留 partial |
| jd | 4 | 0 | 0 | 5.265 | page_empty_shell: page has insufficient content |
| pinduoduo | 0 | 0 | 0 | 1.359 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| ctrip | 2 | 0 | 0 | 24.906 | page_empty_shell: PageEmptyError |
| huawei | 0 | 0 | 0 | 3.672 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| nio | 20 | 0 | 0 | 26.64 | page_removed: HTTP 404 |
| liauto | 10 | 3 | 0 | 67.734 | 已达到目标并入库 |
| byd | 16 | 0 | 0 | 90.438 | 来源请求已达到本次总时限，请稍后重试。 |
| geely | 5 | 0 | 1 | 70.297 | no visible enabled apply control proves the posting is still open |
| ubtech | 1 | 0 | 0 | 2.609 | page_empty_shell: page has insufficient content |
| zhongqi | 0 | 0 | 0 | 11.219 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| xinghai | 1 | 0 | 1 | 10.688 | the page is a login or anti-bot challenge rather than a job detail |
| tarsrobot | 0 | 0 | 0 | 14.109 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| x2robot | 0 | 0 | 0 | 3.688 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| limx | 0 | 0 | 0 | 2.422 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| ai2robotics | 0 | 0 | 0 | 10.593 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| astribot | 0 | 0 | 0 | 4.329 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| dexmal | 0 | 0 | 0 | 13.344 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| booster | 0 | 0 | 0 | 2.875 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| deeprobotics | 0 | 0 | 0 | 0.563 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| moonshot | 0 | 0 | 0 | 19.047 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |
| zhipu | 8 | 0 | 0 | 7.375 | page_removed: HTTP 404 |
| minimax | 3 | 1 | 0 | 13.547 | 已核验并入库；不足目标时保留 partial |
| stepfun | 1 | 0 | 0 | 69.015 | empty_shell |
| deepseek | 9 | 0 | 0 | 19.89 | page_unknown: HTTP 302 |
| baichuan | 1 | 0 | 0 | 4.687 | page_removed: HTTP 404 |
| modelbest | 1 | 0 | 0 | 5.859 | page_removed: HTTP 404 |
| shengshu | 2 | 2 | 0 | 35.593 | 已核验并入库；不足目标时保留 partial |
| enflame | 0 | 0 | 0 | 9.906 | 本轮未发现满足查询条件的候选；不代表全站没有岗位 |

20条结果均独立复核了本次运行的浏览器核验记录、公开网页证据SHA-256和实际SQLite行数，也重新执行生产关键词/地点/实习条件检查。小红书有一条标题无“实习”，但JD明确要求“可实习时长4个月及以上、每周不少于4天”，符合现有严格证据规则；未仅凭标题关键词否定或通过。

- [全部来源机器摘要](source-results.json)
- [20条岗位、链接及证据哈希](verified-jobs.json)
- [完整来源运行摘要](../../../portfolio-artifacts/source-validation-20261026/final/all-sources-summary.json)
- [真实网络日志](../../../portfolio-artifacts/source-validation-20261026/final/live-all-sources.log)
- [公开网页与来源响应证据包](../../../portfolio-artifacts/source-validation-20261026/final/live-public-evidence.zip)
- 原始SQLite及运行目录：`C:/Temp/wta-sources-20261026-final/`，公开证据已归档到上述ZIP。

## 页面表单与服务

以Streamlit AppTest操作真实“指定岗位URL”表单，输入旧MiniMax地址，使用合成技能简历、目标1；未替换生产运行函数或网页内容。运行 `run-656e59b8` 在 **9.12 秒**内成功，页面无异常，返回 `AI Agent 产品运营实习生`，地址转换为正确的 `/detail`，独立浏览器核验并成功保存。此为程序化页面验收，不替代用户个人PDF的人工验收。

[页面验收记录](../../../portfolio-artifacts/source-validation-20261026/final/ui-live-smoke.json)。本地服务已从当前工作区源码完整重启；[http://127.0.0.1:8510](http://127.0.0.1:8510) 健康检查为200，证据见[health](../../../portfolio-artifacts/source-validation-20261026/final/website-health.json)。关闭后在当前工作区运行 `./scripts/start_website.ps1`。

## 剩余限制

百度本轮上游返回 no-auth/illegal-visit；网易未越过已有访问限制测试。美团能发现候选，但页面身份核验/总时限未通过；比亚迪达到来源总时限；吉利缺少可证明有效的投递入口，星海图遇登录/挑战，其余零交付来源的具体原因见逐项表。仍需继续适配这些来源，不能将它们计入成功来源，也未放宽核验标准填充结果。

本次未运行付费模型实验、未重新执行720单元模型实验；模型开关关闭的全量回归和真实来源运行不构成模型API当前可用性证明。个人PDF人工验收与远程PostgreSQL集成仍未覆盖。

# 2026-10-25 审核修复收尾

本次在 `Agent/.worktrees/agent-improvements` 完成修复；保留原始 checkout、旧冻结集和失败证据，没有提交或推送。

## 修复范围

- 模型预算增加发请求前的持久化记账；恢复对账，未知在途请求保守计费；完成后释放 Token 预留。
- 单任务超时不再封锁后续模型任务；失败任务已经产生的交付继续进入质量统计；Reader 页面率与任务率分开。
- Reader 的停止原因只依赖真实执行证据，不读取 gold 停止理由；错误优先，已知移除页不触发模型。
- v3 修正标签冲突与技术岗位误拒，并按场景分层：60 任务、30 组、dev20/test40，test 覆盖全部 14 类场景；没有跨 split 的 URL、job key 或正文精确哈希重复。
- 来源统计按实际尝试计分，纯缓存读取不算当前详情请求；canonical URL 可查证据；历史缓存保留原来源和本次 cache 取用方式，当前可用性不冒充已验证。
- 同 URL 的多个 native 身份分别保存正文/尝试；无法分段确认的共享页保守 review，视觉抽取不能绕过。
- 发现、归档初始化与 HTTP/API 使用同一个剩余时限；过期审批的终态持久化有独立 1–5 秒有界 I/O 收尾，不延长业务执行。
- 新运行的 TTL 清理覆盖本地正文，恢复时同步续期；未标记的历史目录保守保留。CI/release Ruff 覆盖整个 src/tests；必需 fixture 已从 Git 忽略中放行。
- README/面试入口指标一致；纠正旧采集“全部 removed”的错误归因，保留历史原始数值。

## 实际运行证据

见 [acceptance.json](acceptance.json)。离线目标 3 交付/保存 3 条；attempts 断网回放同样为 3 条；批准保存 3 条，重复恢复仍为 3 条，拒绝保存 0 条。这些是合成 fixture 的控制流验收。

本轮真实腾讯/小米来源运行：9/10 个尝试候选取到详情正文，8 次验证全部未通过，交付 0 条，终态 budget_exhausted；主要缺少实习关键词依据。另一次美团/百度窄条件查询无候选。不降低过滤标准，也不把这些运行当作真实链路正向验收成功。

小鹏 URL 的旧规格与现代码不一致，现有证据不足以断言哪个当前路由有效；因此没有凭旧规格盲改模板，也不把旧 404 当成独立确认的岗位下线。

## 冻结与人工交接

最终使用 `evaluations/model-value-v3`。初始构建保留在 `portfolio-artifacts/model-value-v3-initial-20261025`；v1/v2 原数据不变。源码哈希仅对声明的文件作字节版本核对，dataset_valid 与 code_version_match 分开；运行时另记录实际源码哈希，不声称这能证明整个外部环境可复现。

人工说明见 [HUMAN_REVIEW.md](../../HUMAN_REVIEW.md)。14 条建议样本覆盖 dev6/test8、正常与边界类别。模板 status=pending、reviewed_case_ids 为空；没有创建有效人工回执。

人工复核后，`scripts/finish_model_value_evaluation.ps1` 自动执行正式 Planner/Reader 配对及 10 条预声明真实任务采集。正式 test 和真实交付人工核对仍未完成；当前 rules 默认是保守选型，不宣称已证明模型收益。

## 最终门禁

完整 release-check 六项通过：全目录 Ruff、全量 pytest/coverage、wheel-build、doctor、strict-hitl、git-diff-check。覆盖率 87.99%。Windows 子进程日志编码随后修复，并以 17 项定向测试、真实中文子进程输出及 Ruff/diff 检查验证；未为这项日志修复重复运行整套测试。日志保留在本次聊天 outputs/fix-20261025-release-check.txt，旧编码异常也保留可查。


## 人工复核后续修订

用户已复核14条并确认g03相近岗位保守review、g06访问错误与明确移除分开。v4是新冻结版本，旧v3原数据和异议回执不变。原文及合格gold keys不变，g03两变体推荐候选改review；14条回执覆盖通过正式前置校验。4项定向回归及Ruff/diff检查通过，未重复全量测试。正式模型实验仍未运行：部署未显式配置PLANNER_MODEL。详见human-review-resolution-20261025.md。

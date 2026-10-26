# 实习项目最终验收

用户同意按真实搜索、抽取匹配、Agent/HITL 复核、文档与发布检查的顺序收尾。完整证据和三分钟演示入口：[本轮验收](../results/portfolio-acceptance-2026-10-05/README.md)。本轮未修改生产业务逻辑。

## 验收结果

- 腾讯校招与小米两个官方来源，大模型 + 北京、上海、北京/上海三轮各返回 2 个合格岗位，零条件违规。失效候选 404 保留，不把来源 healthy 包装成详情全可用。
- 三个受控抽取任务：规则完成 2/3，真实 Qwen 增强路径 3/3，视觉 Demo adapter 3/3；完成率不作为字段准确率。
- 16 条既有合成标注匹配案例：规则 15/16、纯 Qwen 13/16、混合 15/16，无调用失败。语义边界收益与乐观偏差均保留，未为追求满分继续调参数。
- 冻结历史正文的两个 deterministic provider 各回放 3 轮，结果相同；37 正文成功、3 来源失败保留。Title 37/37、Company 37/40、相关性 30/37、P@6/R@6 4/6、NDCG@6 0.7409。地点仅有两条标注，技能无标注，不宣传泛化准确率。
- 实际 Hybrid runtime 受控恢复：失败 URL 重试后换 URL、低置信度文本转视觉，两条都完成匹配保存。视觉为 Demo adapter，未调用外部 VLM。
- 真实 Qwen planner 的一个 Demo case：5 次规划调用、1 次 invalid/fallback、目标完成。单个案例的 20% fallback 不作为线上错误率。
- 独立 CLI 进程恢复 HITL：批准保存 1、拒绝保存 0、重复恢复退出 2 且无新增写入。portfolio benchmark 三条均暂停，拒绝与重复副作用均为 0。
- 创建与恢复 UI 运行的 PID 不同，2 个岗位恢复，JSON/报告/Dashboard/行动计划四项 sha256 一致，其他 owner 不可读。

## 收尾修正

- README 与面试文档统一 632 passed / 1 skipped / 87.35%，Showcase 加历史日期和旧口径提示。明确网页固定工作流、Hybrid/HITL 独立 CLI、云端未验收。
- release-check 首轮失败来自文档测试硬编码 520/84.89，观察失败后改成两个招聘入口的指标一致性契约；聚焦测试通过，最终完整 release-check 六阶段全部 PASS。
- 只读复核发现搜索脚本可让零结果通过；改为同时校验零违规和目标数量，并在证据记录 positive_acceptance_passed。最终真实三轮复跑各 2 条/零违规，复核确认解决。
- 验收脚本调试时曾直接构造 runtime state，漏传 visual_available；改用产品既有 run_with_hybrid_agent 入口，视觉恢复完成。这是脚本设置问题，未改生产 policy。

## 交付检查与边界

- 本地 release-check：focused Ruff、pytest coverage、wheel build、doctor、strict msgpack HITL、git diff --check 全部 PASS。
- 全仓 Ruff 通过，覆盖率读取最终 .coverage 为 87.35%。本地 Python 3.12.7，远端 Python 3.11 CI 未触发。
- 本轮 14 个证据文件扫描环境凭据原值与 PRIVATE RESUME 标记，零命中；本轮证据与面试文档的 Markdown 本地链接无断链。
- 云端 PostgreSQL/S3 部署继续暂缓；未配置 TEST_DATABASE_URL 的集成测试跳过。网页没有 Hybrid/HITL 审批集成。
- 分支 fix/source-reliability-finish，基线 c69096a5826ac6aa3829086ff504fba57c30b04d。原有混合工作区改动保留，本轮未自动提交/推送/合并。

此后以修复可复现的问题和演示准备为主，冻结新功能范围。

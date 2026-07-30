# Resume Portfolio Finish Design

## 目标

把当前 Web Task Agent 从“功能完整的开发分支”收口为“招聘方可快速理解、面试现场可稳定复现、所有简历数字可追溯”的 AI Agent 实习简历项目。本轮不再增加 Agent 能力，不需要云服务器、GPU、训练或真实 provider API。

## 停止标准

满足以下条件后停止继续开发：

1. 仓库首页在一分钟内说明问题、Agent 架构、HITL 安全边界、验证指标和一条稳定演示命令。
2. 无 API key 的本地命令能够生成 Hybrid Agent 决策证据和 HITL approve/reject/replay 证据。
3. 本地 release-check 命令与 GitHub Actions 的实际 lint、pytest、coverage 门禁一致并全部通过。
4. 简历三条、60 秒讲法和 3 分钟追问材料引用版本化 artifact，不混淆任务完成、循环终止、工具成功和字段准确率。
5. 代码、文档、测试和工作日志形成小而可回滚的提交，最终工作树干净。

## 方案选择

### 不采用：继续增加功能

Web 审批后台、多 Agent 协作和云端 checkpoint 会扩大演示面，但不能显著提高当前项目的实习简历可信度，还会引入鉴权、部署和分布式一致性等新范围。

### 不采用：只重写文档

只整理 README 可以改善第一印象，但招聘方无法用一条命令验证 Hybrid Agent 与 HITL，也无法确认本地检查与 CI 是否一致。

### 采用：证据驱动的 portfolio 收尾

保留现有架构和公开 benchmark，只增加最小的 portfolio 演示入口、验证入口与招聘材料。任何实现改动都必须直接服务停止标准。

## 产物设计

### 1. 招聘入口

重构 README 顶部信息顺序，但保留详细参考内容：

- 一句话定位：基于 LangGraph、browser-use、Pydantic 和 SQLite 的 Hybrid Web Task Agent。
- 核心工程点：typed tools、bounded recovery、deterministic safety policy、durable HITL、idempotent side effects、versioned evaluation。
- 已验证数字：301 tests、90.74% coverage、10/10 loop termination、HITL 3/3 pause、reject/duplicate effects 0。
- 一条无密钥演示命令和证据文件链接。
- 明确 fixture/真实站点/provider 历史数据的边界。

更新 `docs/interview-benchmark-story.md`，加入 HITL 与幂等故事，并移除已经过时的 Windows ACL/旧测试状态。

### 2. 离线 portfolio 演示

新增一个 CLI 模式 `--portfolio-demo`，只编排已有能力，不引入新业务逻辑。它依次执行：

1. 环境 doctor；
2. deterministic Hybrid Agent demo，生成 JSON/Markdown/HTML 或现有可用的可视证据；
3. HITL approve、reject、replay benchmark，生成版本化 JSON/Markdown；
4. 输出关键指标和所有产物的相对路径。

演示必须无 API key、无公网依赖、可重复运行。若任一步失败，返回非零退出码，不输出“完成”结论。CLI help 和 `--print-demo-script` 必须包含该入口。

### 3. 验证入口

新增 PowerShell 友好的仓库验证脚本或 CLI 命令，严格复用 CI 的实际范围：

- Ruff：`agent_*.py`、`search_discovery.py` 和对应测试；
- 全量 deterministic pytest；
- `web_task_agent` coverage 至少 70%；
- wheel build；
- doctor；
- strict msgpack HITL benchmark；
- `git diff --check`。

全仓 Ruff 的 105 项历史风格债务继续透明记录，但不把未在 CI 中启用的规则范围描述为绿色门禁，也不在本轮无关格式化旧代码。

## 数据流

```text
portfolio-demo
  -> doctor
  -> existing Hybrid Agent runtime -> execution trace artifacts
  -> existing HITL benchmark -> approve/reject/replay artifacts
  -> concise evidence summary + artifact paths

release-check
  -> CI-equivalent Ruff
  -> pytest + coverage
  -> build + doctor
  -> strict HITL benchmark
  -> Git hygiene
```

## 错误处理

- portfolio demo 的每一阶段都必须明确打印阶段名；异常转换为清晰的 CLI 错误并返回非零状态。
- 不捕获后继续伪造汇总指标；汇总只从实际生成的 artifact 读取。
- 生成目录由调用方指定或使用稳定默认值，重复运行覆盖同名演示证据但不写入敏感信息。
- 公开 artifact 继续执行现有脱敏约束，不包含 API key、Authorization、简历正文、页面正文或模型原始 response。

## 测试与验收

采用 TDD：

- 先增加 CLI parser/help/portfolio-demo 编排失败测试；
- 再实现最小入口并验证生成 artifact；
- 增加 README 和面试文档契约测试，确保指标和 fixture 边界不会被后续改丢；
- 最后从干净进程运行 CI 等价检查、coverage、wheel、doctor 和 strict benchmark。

简历项目完成后不自动 push、创建 PR、合并或删除 worktree；这些 Git 外部步骤仍等待用户明确选择。

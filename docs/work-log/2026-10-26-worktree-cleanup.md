# 工作树收尾与复盘记录（2026-10-26）

## 用户确认与处理范围

用户核对主分支归档后授权：“保留工作日志用于复盘，其他的代码和测试你自行决定去留”。本轮保留已验收master的全部生产源码、测试、固定评测集和公开证据，仅清理冗余工作树的实体副本及其中缓存。未删除任何Git分支或历史快照。

## 最终项目入口

主目录：`C:/Users/13993/Desktop/大模型学习/Agent`，当前分支 `master`。网站已迁回主目录，入口 [http://127.0.0.1:8510](http://127.0.0.1:8510)；使用 `./scripts/start_website.ps1` 可重新启动。共享 `.venv` 和同级 `visual-web-agent` 均保留。

归档验收版本为 `342657d5109d9785eec16555773a6f4fe0366f52`；本轮生产代码和测试内容与该版本一致，仅补充收尾文档及保留独立工作日志。完整回归仍以归档阶段的1145通过、1跳过、覆盖率88.03%为证据，本轮没有重复执行相同的全量回归。

## 保留的复盘与恢复材料

归档目录：`C:/Users/13993/Desktop/大模型学习/Agent-archives/cleanup-20261026-212003`。

- `work-logs/`：按原目录分类保存 **3321** 份Markdown、日志、文本材料，原始字节保留。数量包含原件的副本、评测说明与供应商日志，不表示有3321次独立实验。
- `worktrees/*.zip`：主目录和10个已登记目录共11份文件备份，包含未提交代码、测试、忽略目录中的数据库、历史运行和网页证据；可重建的虚拟环境、依赖缓存及Git管理文件不打入这些ZIP。
- `manifests/*.json`、`backups.json`：每个备份文件的SHA-256、文件数和ZIP校验信息。备份完成后，对本轮移除的9个目标共7796个文件又进行了原件哈希复核。
- `all-branches-before-cleanup.bundle`：删除工作树前的全部Git引用，已通过bundle verify。收尾后另有包含stash与本次日志提交的 `all-branches-after-cleanup.bundle`。
- `runtime-migration.json`、`migration-verification-final.json`：迁移和逐产物核对记录。
- `preserved-primary-20261026/`：已在当前Git中保留主目录独立模型迁移的原始工作日志；对应代码继续保存在 `archive/primary-20261026-202156` 和 `archive/primary-before-cleanup-20261026-212003`，以及未丢弃的stash中。这些独立模型改动尚未合入当前master。

本地备份包含原始运行数据与配置，未提交这些原始数据到Git，也没有推送远程。

## 已移除的本仓库工作树

- `agent-improvements`：已备份并移除。
- `hybrid-decision-agent`：已备份并移除。
- `master-archive-review`：已备份并移除。
- `multimodal-agent`：已备份并移除。
- `open-web-job-agent`：已备份并移除。
- `planner-benchmark`：已备份并移除。
- `real-job-foundation`：已备份并移除。
- `source-health-reliability`：已备份并移除。
- `ui-overhaul`：已备份并移除。

## 保留的其他仓库与旧登记

- `Agent/.worktrees/multimodal-visual` 和 `Agent/.worktrees/visual-source-recovery` 实际归属同级 `visual-web-agent` 仓库，本轮保持原状，另复制了其中日志供复盘。
- `Agent/.worktrees/web-task-agent-mvp` 与 `D:/Agent/.worktrees/web-task-agent-mvp` 实际引用 `D:/Agent/.git`，本轮未删除其实体目录。
- 当前仓库中还有一条指向D盘旧工作树的历史登记，与D盘实体的实际Git归属不一致。删除这条登记的操作被自动审批审查拒绝，工具仅返回 `blocked by policy`，未提供更具体原因。没有通过其他方式重试删除；登记继续保留，所以 `git worktree list` 仍可能列出D盘记录。

## 网站历史与验证

- 网站的 `streamlit-persistence`、`streamlit-runs` 及相关产物已经复制到主目录；迁移2996个文件、2个已有相同文件，无冲突覆盖。
- 迁移前后9条运行记录一致，6条已有可读结果及39份可用产物逐字节和SHA-256一致。其余3条是迁移前就没有结果JSON的失败记录，保持原状，没有伪造或补齐。
- 817个固定数据/证据/夹具文件的原始哈希一致。
- 页面程序化启动（AppTest）无异常；网站健康检查200。
- 迁移相关页面、存储、产物与历史回归：**91通过、1跳过**。跳过项仍是未配置PostgreSQL集成；日志 `migration-regression.log`。
- Ruff和Doctor通过。此前的完整源码与测试未删减，因此保留原有全部回归覆盖能力。

## 后续复盘入口

先看本日志与 `work-logs/primary/docs/work-log/`、`work-logs/agent-improvements/docs/work-log/`，再根据运行ID查对应ZIP和产物。需要恢复旧代码时，从Git快照创建独立分支或解压到新目录；避免覆盖当前master。归档后旧报告里出现的工作树路径属于当时的历史位置，当前运行入口以本日志为准。

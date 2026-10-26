# 实习项目最终验收计划

> 按用户已同意的顺序在当前分支执行；使用 executing-plans 和 verification-before-completion，已有混合工作区改动保留。

**目标：** 将现有搜索、抽取匹配、Hybrid、HITL 和本地持久化整理成可复核的实习项目，不增加网页审批或云部署。

**架构：** 使用现有公开官方来源、冻结 JD 样本、CLI 演示和文件存储 adapter 验收；原始产物放入忽略的 portfolio-artifacts，摘要和复现命令放入 docs/results。

**技术栈：** Python、LangGraph、Pydantic、SQLite、Streamlit；可用凭据仅用于公开岗位和合成简历的有限模型调用。

- [x] 真实搜索：腾讯校招与小米两个来源，关键词大模型，目标 2；依次输入北京、上海、北京/上海。核对返回岗位全部满足关键词和实际工作地，保留来源状态、排除原因与详情解析策略；零结果如实记录。
- [x] 抽取与匹配：冻结样本和合成简历，对比规则和可用 LLM。记录字段协议、变化和限制；无凭据用明确标注的 deterministic demo，不能宣称真实模型收益。
- [x] Agent/HITL：运行 --portfolio-demo；复核实际运行时恢复测试；用独立进程启动、审批恢复 HITL，验证拒绝和 replay。
- [x] 持久化：独立进程恢复本地运行与四项下载产物，核对 sha256、状态和 owner 隔离。
- [x] 交付：统一 README / 面试讲述中的当前指标与能力边界，增加三分钟演示步骤与证据入口；历史评测不改写。
- [x] 执行 --release-check、全仓 Ruff、git diff --check；记录六阶段结果与未验收的云端 PostgreSQL/S3。检查新增证据不含凭据或个人简历正文；保留本地分支，不自动混合提交/推送。

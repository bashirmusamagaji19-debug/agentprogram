# 真实效果证据与云端持久化总路线图

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement each plan task-by-task.

**Goal:** 把当前“单次真实 showcase + 单实例 Streamlit Demo”推进为可重复评测、可恢复运行、可逐步扩展到多用户的产品基础。

**Architecture:** 先建立独立、版本化的真实岗位评测基线，明确来源可达性、字段正确性和推荐质量的指标边界；再把运行记录与产物从实例本地磁盘迁移到共享数据库和对象存储。第一阶段不改变现有岗位发现、验证和匹配逻辑，第二阶段先提供持久化存储适配层，只有在需要长任务和并发时才拆出 API、队列和 Worker。

**Tech Stack:** Python 3.11+, Pydantic, pytest, 现有 `benchmark.py`/`evaluation.py`/`WebTaskWorkflow`, Streamlit, PostgreSQL-compatible database, S3-compatible object storage, optional FastAPI and Redis queue.

---

## 子计划

1. [真实效果评测基线](2026-09-09-real-effect-evaluation-plan.md)
   - 目标：从一次 showcase 变成固定样本、人工标注、多轮运行和可比较指标。
   - 首个门槛：40 条版本化样本、至少 3 轮运行、字段与推荐指标均有 ground truth。

2. [云端持久化与多用户演进](2026-09-09-cloud-persistence-plan.md)
   - 目标：先解决重启丢失，再按并发需求演进到 API + 队列 + Worker。
   - 首个门槛：应用重启后历史 run 和下载产物仍可访问，用户之间不能越权读取。

## 推荐顺序

1. 先执行评测基线计划。它不依赖云端架构，能直接回答“系统效果如何”。
2. 再执行持久化计划的阶段 A。它解决目前最明确的云端风险，改动小于完整重构。
3. 当单次运行时间、并发或用户数成为实际瓶颈时，再执行持久化计划的阶段 B。

## 统一约束

- 不把来源产出率称为全网覆盖率；不把任务完成率称为字段准确率或推荐准确率。
- 不保存 API key、Authorization、原始模型响应或未经用户同意的简历正文。
- 现有 `WebTaskWorkflow`、verifier、matcher 和下载白名单继续作为业务边界。
- 所有公开报告同时记录数据集版本、运行时间、失败分类和证据限制。
- 每个阶段都以测试、实际运行和文档更新为完成条件，不以代码合并代替验收。

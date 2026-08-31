# Work Log — 2026-08-31 中文 JD 冒烟测试（阶段 0）

> 分支：`feature/chinese-job-pipeline` · 计划：`docs/superpowers/plans/2026-08-31-chinese-job-pipeline.md`
> 本阶段纯测量，不改生产代码。脚本：`scripts/smoke_chinese_jd.py`、`scripts/baseline_rule_extraction.py`、`scripts/smoke_llm_extraction.py`

## 一、站点可访问性矩阵（牛客 6 个真实岗位详情页）

| 岗位 | URL | HttpPageLoader | 正文 | JD 结构 |
|---|---|---|---|---|
| 拼多多-大模型算法工程师 | nowcoder.com/jobs/detail/447182 | ✅ | 1757+ 字符 | 中文"岗位职责/任职要求"完整 |
| 快手-大模型算法实习 | /401709 | ✅ | 完整 | 同上 |
| 阿里巴巴-视觉&多模态算法实习 | /164845 | ✅ | 完整 | 同上 |
| 元戎启行-AI Infra 实习生 | /452650 | ✅ | 完整 | 同上 |
| 元戎启行-感知算法实习生 | /451583 | ✅ | 完整 | 同上 |
| 华为-AI工程师秋招实习 | /389094 | ✅ | 完整 | 同上 |

**结论**：牛客岗位详情页是服务端渲染、无需登录，6/6 可直接抓取。计划里的阶段 4（浏览器路径）对牛客可以降级为可选；实习僧等 SPA 站点待测。

## 二、规则抽取中文基线（`scripts/baseline_rule_extraction.py`）

6/6 页面逐字段统计（判定：title/company/location 非 Unknown 且非空；sections ≥30 字符）：

| 字段 | 成功率 | 失败原因 |
|---|---|---|
| title | 6/6 | 页面 title 兜底 |
| company | 6/6 | 头部行推断命中 |
| location | 0/6 | `_LABELS` 全英文 |
| requirements | 0/6 | section marker 全英文（`requirements`/`qualifications`） |
| responsibilities | 0/6 | 同上 |
| skills | 0 条 | requirements 为空；逗号切分对整段中文无效 |

规则 confidence 全部 0.40 < 0.6 阈值 → 现有管线中 LLM 兜底必然触发。

## 三、LLM 抽取冒烟（`scripts/smoke_llm_extraction.py`，qwen-plus × 6 次调用）

规则 vs LLM（`rule→llm`，Y=有内容 / -=缺失）：

| 岗位 | title | company | location | requirements | responsibilities | LLM 触发 | confidence |
|---|---|---|---|---|---|---|---|
| 拼多多 | Y>Y | Y>Y | ->Y | ->Y | ->Y | 是 | 1.00 |
| 快手 | Y>Y | Y>Y | ->Y | ->Y | ->Y | 是 | 1.00 |
| 阿里巴巴 | Y>Y | Y>Y | ->Y | ->Y | ->Y | 是 | 1.00 |
| 元戎启行-Infra | Y>Y | Y>Y | ->Y | ->Y | ->Y | 是 | 1.00 |
| 元戎启行-感知 | Y>Y | Y>Y | ->Y | ->Y | ->Y | 是 | 1.00 |
| 华为 | Y>Y | Y>Y | ->Y | ->Y | ->Y | 是 | 1.00 |

- LLM 抽取 title/company/location/职责/要求**全部正确**（拼多多、快手、阿里、元戎启行、华为的公司名与地点均与页面一致）
- **verifier 误杀实锤**：元戎启行-感知算法实习生（内容完全正确）被默认关键词 `["AI","LLM","Agent"]` 拦截，reason=`not relevant to AI internship direction`——正文是纯中文时英文关键词匹配不到 → 阶段 2 必须加中文关键词
- **skills 字段缺失实锤**：现有 schema 无 skills 字段，规则切分产物是"1、硕士及以上学历"这类编号碎片——阶段 2 的 `skills: string[]` 结构化输出必要

## 四、阶段 0 期间发现并修复的环境问题

1. **venv editable 安装指向过期 worktree**：`.pth` 指向已删除的 `.worktrees/hitl-checkpoint/src`，`web-task-agent.exe` 全挂 → `pip install -e .` 重装修复
2. **OS 环境变量旧 DASHSCOPE key 覆盖 .env**：Windows 用户环境变量里残留 41 字符旧 key，`load_dotenv()` 默认不覆盖已存在变量 → CLI 全部 401。`.env` 里的新 key（36 字符）实测有效。修复方式：运行时显式内联 `DASHSCOPE_API_KEY=<.env 值>`。**教训：`load_dotenv()` 的 override 语义在"改了 .env 但不生效"时是第一嫌疑**
3. **`/models` 端点不校验 key 的假阳性**：用旧 key 请求 dashscope `/v1/models` 返回 200，误判 key 有效；`/chat/completions` 才是真实校验。**教训：验证 API key 必须用实际要调用的端点**

## 五、对后续阶段的影响

- 阶段 1（聚合源）：牛客可作直接抓取源；聚合仓库仍按计划调研
- 阶段 2（抽取中文化）：方向确认——`_LABELS`/section marker 加中文、LLM schema 加 `skills`、verifier 加中文关键词；LLM prompt 已基本可用（6/6 字段正确），主要是 schema 扩展
- 阶段 4（浏览器路径）：对牛客可降级为可选；实习僧仍未知

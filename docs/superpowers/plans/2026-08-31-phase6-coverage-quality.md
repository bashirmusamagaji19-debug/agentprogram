# 阶段 6 计划：覆盖修复 + 质量收口（2026-08-31）

> 分支：`feature/chinese-job-pipeline` · 上游：阶段 5 前半（合成简历 E2E，stories #21-#22）

## 背景：为什么是这个顺序

合成简历 E2E 后的已知缺口，按对"真实可用"的伤害排序：

1. **覆盖坍塌（最大）**：237 条实习+AI 岗里 216 条（91%）是腾讯——其中 210 条是校招库
   （`source_id=cn-tencent-campus`，青云计划等），ByPostId 全部 E1005 → 上轮 12 岗只跑出 5 valid。
   **已破案**：校招库在 `join.qq.com`，公开无鉴权接口
   `GET https://join.qq.com/api/v1/jobDetails/getJobDetailsByPostId?postId=…`
   返回 `topicDetail`（课题背景/挑战/具体工作）+ `topicRequirement`（学历/技术要求）——
   完整 JD 内容，实测 5/5 命中（探索记录见本计划末尾附录）。
2. **Top-5 有幻觉风险（次大）**：LLM 匹配器乐观偏差真实复现（商业分析岗 0.68），
   分歧数据已有（#8/#12/#14），混合仲裁条件成熟。
3. **网易页抽取质量差（小）**：hr.163.com 页面可达但 company 抽成句子——新站点适配，低频。

阶段 6 只做 1 和 2（质量收口），站点适配类（3/wecruit）延后。

## 任务 6A：腾讯校招 API 适配（预计 2h）

**改动**：
- `official_api.py` 新增 `_fetch_tencent_campus(url)`：
  - 路由：`careers.tencent.com` 的 ByPostId 抛 `OfficialApiUnavailableError`（E1005）时，
    **fallback 到 join.qq.com** `getJobDetailsByPostId`（同 postId 命名空间，实测互通）
  - 组装：`公司：腾讯` + `工作地点：{recruitCityList}` + `岗位职责：\n{topicDetail}` +
    `任职要求：\n{topicRequirement}`（标准标签行，规则抽取直接命中——Fix #19 的格式约定）
  - `recruitCity` 是数字 ID → 用 `recruitCityList[].name`（响应里有；实测确认字段名）
- 顺序设计：**先 join.qq.com 后 careers ByPostId**？不——careers 是权威源（社招岗），
  campus 岗在 careers 必然 E1005。直接按序：careers ByPostId → E1005 → join.qq.com 详情。
  两跳都是轻量 JSON 调用。

**验收**：
- fake transport 单测：E1005 → campus fallback 链路、cityList 组装、两跳全挂 → `OfficialApiUnavailableError`
- 真实冒烟（声明后跑）：取聚合库 3 个青云计划 postId，断言 requirements/responsibilities 非空
- 重跑合成简历主简历 E2E：12 岗 valid 数 5 → 预期 ≥9（腾讯 5 条从失败变覆盖）

## 任务 6B：混合仲裁匹配（预计 1.5h）

**动机**：标注集分歧数据（#8/#12/#14 规则对 LLM 错）+ 真实复现（商业分析岗 LLM 0.68）。
LLM 乐观偏差的可靠信号是"规则 low（<0.4）但 LLM medium+（≥0.5）"。

**改动**（`matcher.py` `match()` 的 LLM 兜底分支）：
- 规则 < llm_min_rule_score 调 LLM 后，**仲裁规则**：
  - LLM score < 0.5 → 采 LLM（双方都不看好，LLM 更细）
  - 规则 low 且 LLM ≥ 0.5 → **加权仲裁**：`final = 0.5 * rule + 0.5 * llm`？
    不——先做最保守的：**折半惩罚** `final = llm * 0.7`，reason 标注
    "LLM 高分但规则未命中关键词，已折减"。数字 0.7 的依据：#12（0.65×0.7=0.45 medium
    边界）、#14（0.85×0.7=0.6 medium）——都不该值得投……重算：目标是让 no_match 降到
    0.4 以下，0.7 折减做不到 #14（0.85→0.595）。
  - **改为规则否决**：规则 < 0.2（几乎零命中）且 LLM ≥ 0.5 → score 封顶 0.39（low），
    reason 写明"关键词无交集，LLM 判断仅作参考"。规则 [0.2, 0.6) → 采 LLM 但也折减到
    min(llm, 0.55)（不超过 medium）。
  - **先跑数字再定**：用标注集 16 条跑 3 个候选策略（不折减/封顶 0.39/折半），
    取 no_match 拒绝率与 match 召回的帕累托最优。**策略选择依据写进 work-log**。
- LLM < llm_min_rule_score 时若规则 ≥ 0.6：现状不调 LLM，保持。

**验收**：
- 标注集评测：仲裁后混合准确率 ≥ 0.94（不伤规则强项），LLM 的 3 个乐观错误
  （#8/#12/#14）至少修 2 个
- `--evaluate-matcher` 增加 `hybrid` 行（规则 + LLM + 仲裁三列对比）
- 合成简历重跑：商业分析岗对 LLM 应用简历分数应降到 medium 以下

## 任务 6C：收尾（预计 1h）

- work-log `2026-08-31-phase6-coverage-quality.md`（含 API 探索附录：join.qq.com 发现过程）
- story #23：腾讯双库结构（社招 careers vs 校招 join.qq.com，postId 命名空间互通但接口不同）
- README 增补"中文岗位真实使用"章节（README 是阶段 5 验收项，提前铺）
- memory 更新

## 非目标（明确不做）

- wecruit/网易新站点适配（覆盖率低，单独一轮做）
- 浏览器路径重启（join.qq.com 修复后剩余盲区 < 10%）
- 简历 PDF 解析（当前 markdown 文本够用）

## API 探索附录（2026-08-31 实测）

- 聚合库腾讯岗：社招（`source_id=cn-tencent`）postId `2084…` ByPostId OK；
  校招（`cn-tencent-campus`，800 条）postId `1231…/1284…` ByPostId 全 E1005
- `careers.tencent.com` Query 接口（pageIndex/pageSize 参数）只覆盖社招库
- campus.html 是 404 壳；join.qq.com 是校招独立门户（job-radar 的 `tencent_campus.py`
  adapter 用 `POST /api/v1/position/searchPosition` 列表——**但列表不带 JD 正文**，
  jd_text 只有标题行，这就是 46 字符中位数的来源）
- 从 join.qq.com 前端 JS bundle（`p_zh-cn_index.build.js`, 751KB）挖出完整 API 清单，
  其中 `GET /api/v1/jobDetails/getJobDetailsByPostId?postId=…` 返回
  `topicDetail` + `topicRequirement` 完整课题 JD——青云计划岗 5/5 命中
- 方法论教训（story #23 素材）：**API 逆向要从消费端 JS 找线索**——服务端 HTML 是壳
  不含路由信息，真正定义接口清单的是前端 bundle 的字符串

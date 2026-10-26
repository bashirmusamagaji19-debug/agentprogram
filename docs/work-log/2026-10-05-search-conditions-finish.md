# 运行台搜索条件收尾

用户确认：地点指实际岗位 base，不要求 URL 出现城市；每个岗位关键词参与搜索。

## 实现

- 关键词按空格、逗号、顿号分词，AND 验证；显式支持 AI/人工智能、Agent/智能体、intern/实习别名和英文词边界。
- 工作地多城市 OR，异地/未知地点排除，全国/不限不限制。支持北京、上海、深圳、广州的中英文城市名；Moka 保留省、市字段。
- 官方、聚合、Demo 发现阶段筛选已知信息，详情阶段严格验证。飞书/Moka 在适配器限额前筛选，原生 keyword 查询无结果时有界宽取，最终条件不放宽。
- 严格工作流在筛选后去重、截断；指定 URL 不再只打开目标数的前几条，最多 100 条。候选预算与平台翻页上限保留，不保证穷尽招聘库。
- JSON 保存 search_conditions，条件排除的 search_filter 不计页面访问失败。技能/简历保持软评分。严格模式在运行台开启，历史 CLI/benchmark 默认不变。

## 验证

- 观察关键词联合条件、发现前限额、详情校验、英文城市名、Demo 名额、Moka 省市和飞书/Moka 响应中第 46 条合格岗位的失败回归，再修复并验证通过。
- 全量命令：`.venv/Scripts/python.exe -m pytest --cov=web_task_agent --cov-report=term --cov-fail-under=70`，632 passed / 1 skipped，覆盖率 87.35%。唯一跳过为未配置 TEST_DATABASE_URL 的 PostgreSQL 集成测试。
- `.venv/Scripts/python.exe -m ruff check .` 通过。
- Streamlit AppTest 实际提交：AI intern + 上海 + 目标 1，返回 ML Platform Intern / Shanghai；改为北京，返回零结果并展示条件无匹配说明，无页面异常。此为受控 Demo 验证，不是全站实时效果评测。
- 只读代码复核提出两项问题（适配器限额前过滤、Moka 城市覆盖），修复后复核确认解决。

本地预览使用 8513 端口，云资源部署仍按用户决定暂缓。本次不提交或推送已有混合工作区改动。

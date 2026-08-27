# 公开详情页真实 Smoke

- 获取时间：`2026-08-26T14:37:42.9430269Z`
- 范围：不经过搜索 API，直接验证公开详情页加载、来源判断、字段抽取和失败分类
- 正文策略：不保存网页正文，只记录 SHA-256、字节数和脱敏字段

| 请求来源 | 结果 | 最终页面 | 抽取/失败 | 证据 |
|---|---|---|---|---|
| Anthropic Greenhouse | 可信岗位 | 原岗位详情 URL | `greenhouse_open_graph` | 4 个字段，正文 112,249 bytes，SHA-256 `34cfe5700c8e4ee8717c4febf67bc0c72d1b4211e55365069f96cfec05b67a06` |
| Reddit Greenhouse 历史 URL | 拒绝 | 招聘板 `?error=true` | `not_job_detail` | 未抽取、未保存错误页正文 |

Anthropic 页面抽取结果：`Applied AI Technical Evangelist, Startup Ecosystem`，公司 `Anthropic`，地点 `San Francisco, CA`。

该结果证明当前详情页证据链能处理成功页面和已失效页面，不证明 Tavily 搜索召回率，也不计入设计要求的 3 轮独立在线搜索。

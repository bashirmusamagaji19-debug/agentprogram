# Benchmark Story

## 一句话

这是一个可验证的 Web Task Agent，不是一次性 prompt demo。

## 现在已经能讲清楚的点

- 工作流是完整的：浏览、抽取、验证、匹配、报告、评测都已经拆开并能单独复盘。
- 先用 fake browser 和 fixture 稳住闭环，再把真实站点样本切到 `HttpPageLoader`，避免真实浏览器环境把评测口径弄脏。
- 4 样本真实 benchmark 已经跑通，baseline 和 deterministic demo 是 `3/4`，DeepSeek 是 `4/4`。
- 失败样本的过滤原因会回填到 JSON 和 Markdown，能直接看到是 `missing requirements`、`confidence` 还是 `relevance` 导致的过滤。
- 这意味着项目不仅有结果，还有边界、归因和回归测试。

## 面试时可以怎么说

“我把一个招聘搜索 Agent 拆成了可验证的工作流，先用本地 fake browser 跑通闭环，再接真实站点样本做 benchmark。现在我能对比规则抽取、deterministic demo 和 DeepSeek provider 的结果，而且失败项会把 verifier 过滤原因写回 JSON 和报告，所以不是单纯跑通，而是能解释为什么差一条。”

## 还能继续堆的空间

- 继续扩真实样本数，做回归基线。
- 把 `HttpPageLoader` 改成非阻塞，减少评测时的事件循环阻塞。
- 扩 Qwen provider 对比，形成更完整的 provider matrix。
- 把 verifier 过滤原因拆得更细，继续提高解释性。

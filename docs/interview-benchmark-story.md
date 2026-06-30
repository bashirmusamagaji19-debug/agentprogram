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
- 把真实站点评测从一次性 comparison 升级成 provider matrix benchmark，每个样本有公司、ATS、岗位族元数据，所有 provider 跑同一批样本输出矩阵。

## Real Site Benchmark V2 讲法

这一阶段我把真实站点评测从一次性的 comparison 升级成 provider matrix。每个样本不只有 URL，还有公司、ATS 类型、岗位族、期望信号和技能标签；每个 provider 都跑同一批样本，输出完成率、有效岗位数、失败分类和耗时。

面试时重点不是说某个 provider 永远最好，而是说明我如何设计可复现评测：固定样本目录、统一 workflow、统一 verifier、统一失败分类，再把 rule、LLM、visual provider 放在同一张矩阵里比较。真实页面可能变化，所以系统把 HTTP、空页面、抽取失败、verifier 过滤都记录下来。这比只展示一次成功 demo 更能体现工程判断。

新增的 `benchmark-v2-explained.md` 是讲述层：它把矩阵翻译成一句话结论、provider 对比、失败原因说明、visual provider 价值，以及 60 秒和 3 分钟面试讲法。
- 把 verifier 过滤原因拆得更细，继续提高解释性。

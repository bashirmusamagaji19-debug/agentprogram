# CLI 离线演示回归修复

## 问题

详情页证据加固后，`--open-search-demo` 的 fixture 仍只有搜索摘要，没有详情页正文，导致离线演示返回 `Verified jobs: 0`，与演示目标不符。

## 修复

- CLI demo fixture 增加最小 `JobPosting` JSON-LD 详情页内容。
- 新增 CLI 集成测试，断言命令返回成功、summary 的 `verified_count=1` 且生成 `jobs.jsonl`。

## 验证

- 修复前测试实际失败：`verified_count` 为 `0`。
- 修复后开放搜索测试全部通过。
- 全量测试：`402 passed`，覆盖率 `90.75%`。
- Ruff 与 Python 编译检查通过。

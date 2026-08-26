# Online Acceptance Automation Design

**Goal:** 用一条命令生成真实 Tavily 在线岗位搜索的可审计报告，并由 GitHub Actions 持续验证 Docker 部署产物可构建。

## Scope

- 新增独立的 online smoke CLI，直接复用 `DemoQueryParser`、`TavilySearchProvider` 和 `OpenSearchPipeline`。
- 默认执行 3 条中英文岗位查询，也允许通过重复的 `--query` 参数覆盖。
- 每条查询使用独立 artifact 目录，最终汇总为 JSON 和 Markdown；报告记录岗位数量、失败分类、provider 质量信息和原始 artifact 路径。
- 缺少 `TAVILY_API_KEY` 时立即失败，且报告和终端都不输出密钥。
- GitHub Actions 增加独立 Docker build job，只构建镜像，不需要任何 secret，也不执行真实在线搜索。

## Non-goals

- 不修改搜索、验证、匹配或抽取策略。
- 不把 fixture 指标包装成真实在线指标。
- 不在 CI 中调用付费搜索 API。
- 不声称 Docker 构建成功等同于公网部署成功。

## Acceptance

- 报告生成器可在测试注入的 provider 下稳定验证成功与失败汇总。
- 缺 key 的 CLI 返回非零退出码和清晰错误。
- JSON 与 Markdown 都明确标记 `mode=online` 和 provider。
- CI 配置包含无 secret 的 Docker build job。
- README 给出 PowerShell 一条命令和证据边界。

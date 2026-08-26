# 真实在线验收自动化与 Docker CI

## 本轮目标

将“配置搜索 key 后人工点页面测试”收敛为一条可重复命令，并补上本机 Docker Desktop 未启动时缺失的云端镜像构建证据。

## 实现

- 新增 `python -m web_task_agent.open_search.online_smoke`。
- 默认执行 3 条中英文查询，每条查询使用独立 artifact 目录。
- 汇总输出 `online-smoke-report.json` 和 `online-smoke-report.md`。
- 报告记录 provider、可信岗位总数、单查询终止原因、失败分类和 provider 质量 metadata。
- 缺少 `TAVILY_API_KEY` 时返回退出码 `2`，不创建报告，也不输出密钥。
- GitHub Actions 新增独立 `docker-build` job，无 secret 执行镜像构建。

## TDD 证据

1. `test_online_smoke.py` 首次运行因模块不存在而 collection error。
2. 最小实现后，测试暴露默认查询数量句式未命中既有 parser 契约。
3. 查询调整为已有冻结测试覆盖的“1 个岗位 / top 1 jobs”格式后，4 条测试通过。
4. Docker 契约测试首次因 `docker-build` job 不存在而失败；补入 job 后 2 条 CI 契约测试通过。

## 本地验证

- 开放搜索测试：`71 passed`。
- 新增聚焦测试：`6 passed`。
- 全量测试：`379 passed`，`7 warnings`。
- 总覆盖率：`90.65%`，门槛为 `70%`。
- Ruff：通过。
- Python 编译检查：通过。
- 清空 Tavily key 后运行 CLI：退出码 `2`，未创建输出目录。

## 证据边界

本轮环境没有用于该入口的 Tavily key，因此没有执行真实互联网搜索，不能声称已获得新的在线岗位样本。新增工具解决的是“key 就绪后如何一条命令形成可审计证据”；Docker CI 解决的是“部署镜像能否在 Linux runner 构建”，两者都不等同于公网应用已经上线。

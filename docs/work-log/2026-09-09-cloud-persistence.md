# Work Log - 2026-09-09 Cloud Persistence

> 后续状态更新: 2026-09-21 完成阶段 A 代码、测试和文件 adapter 重启验收。

## 目标

让 Streamlit 运行台在当前进程或会话结束后，仍能按 owner 查询历史 run、恢复结果并下载白名单产物；同时保留未来切换 PostgreSQL 和 S3-compatible storage 的边界，不把云凭据或任意路径写入业务记录。

## 已完成

- 新增 `RunStore` 契约和 `FileRunStore`、`PostgresRunStore`。
- run 状态固定为 `queued/running/succeeded/partial/failed`，终止态不允许回退。
- 创建接口支持 `owner_id + idempotency_key` 幂等重放；查询和更新始终要求 owner。
- 新增 `ArtifactStore` 契约、`FileArtifactStore` 和懒加载依赖的 `S3ArtifactStore`。
- 产物元数据记录 `object_key`、`mime`、字节数、sha256、owner/run 和 retention deadline。
- 下载只接受运行时注册过的 artifact key；不会读取用户提交的任意文件路径。
- Streamlit 侧栏展示当前 owner 最近 10 次 run；页面 rerun 后从 store 恢复，不再只依赖 `st.session_state`。
- 默认 retention 为 7 天，启动时清理过期 run 和 artifact。
- `APP_PASSCODE` 启用访问门禁，历史 owner 固定为 `APP_OWNER_ID`（默认 `demo`）。
- JSON 持久化副本排除了本地 artifact 路径和简历正文；请求摘要只记录结构化条件、数量与 provider 开关。

## 未执行

- 未创建真实 PostgreSQL 数据库、数据库用户或私有 S3 bucket，因为没有目标平台凭据。
- 未对线上实例执行冷启动/重启验收。
- 未验证 PostgreSQL adapter 的远程连接、迁移权限和 S3 生命周期规则。
- 阶段 B 的 FastAPI、Redis 和常驻 Worker 继续保持推迟；当前长任务仍同步阻塞 Streamlit 请求。

## 本地重启验收

命令：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
$root = Join-Path $env:TEMP "web-task-agent-persistence-acceptance-20260921"
.\.venv\Scripts\python.exe scripts\verify_persistence_restart.py create `
  --root $root --owner owner-a
.\.venv\Scripts\python.exe scripts\verify_persistence_restart.py verify `
  --root $root --owner owner-a --run-id run-ffa41d11
```

创建进程输出：

```json
{"artifact_sha256":{"action_plan":"f25afe33f6c8229ec7eb80ed8a7b847cabc2f3a2e162e71faaba433e406d4a0b","dashboard":"a733d76617dc231b0faf4b28f65d4f5be167885506a08c901adae31eb66e9bda","json":"2491d7184b8112ea295e512298f49905daf5d8b61e161d4d5b21710cfc1c225e","report":"87ae6a2db0a80ca20d60468c6683b92f95f17b6b370f13c5dcab7a469a48c042"},"owner_id":"owner-a","run_id":"run-ffa41d11","status":"succeeded"}
```

独立验证进程输出：

```json
{"artifact_count":4,"hash_matches":{"action_plan":true,"dashboard":true,"json":true,"report":true},"owner_id":"owner-a","run_id":"run-ffa41d11","status":"succeeded","valid_jobs":2}
```

这说明相同存储目录在两个独立 Python 进程之间可恢复 run、结果和 4 个 artifact；它不证明云端持久卷或托管数据库行为。

## 自动化边界证据

- `tests/test_run_store.py` 覆盖状态机、幂等创建、owner 隔离、过期清理和可选 PostgreSQL 集成跳过。
- `tests/test_artifact_store.py` 覆盖文件 adapter、缺失文件拒绝、路径安全、过期清理以及 fake S3 client 的私有 object key。
- `tests/test_persistent_streamlit.py` 覆盖重启恢复、7 天 retention、口令门禁、云端配置完整性和敏感内容扫描。
- 未设置 `TEST_DATABASE_URL` 时 PostgreSQL 集成测试明确 skip，不会伪装通过。

## 云端切换检查单

1. 创建最小权限数据库用户，只授予 schema 所需 DDL/DML 权限，不要把管理员 URL 写入仓库。
2. 创建私有 bucket，并禁止匿名读取；对象只通过应用下载白名单或未来签名 URL 访问。
3. 在 Streamlit Secrets 中配置五项存储变量和 `APP_PASSCODE`。
4. 部署后创建一个 run，记录 run_id 和四个 artifact sha256。
5. 重启/重新部署应用，用同一 owner 恢复 run，确认哈希一致。
6. 用另一个测试 owner 查询同一 run/artifact，预期均为“未找到”。
7. 扫描应用日志、数据库记录和对象内容，确认无 API key、Authorization、完整简历正文和服务器绝对路径。

在完成以上步骤前，只能声明“代码具备云 adapter 和本地重启证据”，不能声明“云端持久化已完成生产验收”。

## 最终本地验证

2026-09-21 在 `feature/cloud-persistence` worktree 运行：

```text
568 passed, 1 skipped in 8.11s
All checks passed!
```

覆盖率复跑为 `84.92%`。其中 skip 是未配置 `TEST_DATABASE_URL` 的 PostgreSQL 集成测试；File adapter、S3 adapter fake client、Streamlit AppTest 和端到端恢复测试均已执行。

Streamlit AppTest 验证了口令未输入时不渲染历史台，输入正确口令后进入主界面且无 exception。本地服务 `http://127.0.0.1:8512/_stcore/health` 返回 `200 ok`。

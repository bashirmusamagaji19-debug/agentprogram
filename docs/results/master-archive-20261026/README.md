# master 本地归档核对单（2026-10-26）

## 归档范围

本次将 `agent-improvements` 中已验收的真实岗位发现、实习证据过滤、执行预算、独立浏览器链接核验、页面正文等待及 MiniMax/生数详情路由修复归入本地主分支 `master`。包含相关源码、测试、固定评测数据与公开验收材料。

主目录 `Agent` 的独立模型迁移修改仍原样保留在 `fix/source-reliability-finish` 的未提交状态，并另存 `archive/primary-20261026-202156`（`e31060878d498a7986258739354316e0b1c42a12`）快照；这些独立改动没有与当前已验收版本混合。对应工作日志为原主目录的 `docs/work-log/2026-10-26-unified-deepseek-flash.md`，后续整合时需要单独验证。

已验收工作区的归档快照为 `archive/validated-20261026-202156`（`250812115ba73303ffc03dfc49812ad35220bb5b`）。归档前 master 为 `c69096a5826ac6aa3829086ff504fba57c30b04d`；另保留 `archive/master-before-20261026-202156` 指向该提交。

## 归档副本验证

- 在独立目录 `Agent/.worktrees/master-archive-review` 从归档提交检出后重新执行完整 pytest：**1145 passed, 1 skipped in 553.46s**，覆盖率 **88.03%**。
- 唯一跳过项为 PostgreSQL 集成测试：未配置 `TEST_DATABASE_URL` 与 psycopg；SQLite 用例正常验证。
- Ruff、Wheel、Doctor 和 strict HITL 均通过。HITL 的 pause_rate=1.00、rejected_effects=0、duplicate_effects=0。
- 固定评测数据、公开证据与测试夹具817个文件的 SHA-256 与原件一致；`.gitattributes` 为这些文件保留原始字节及 Markdown 空白，不因 Git 换行转换破坏校验。
- 71个Python源文件与已验收工作区内容一致（仅忽略系统换行差异）。归档副本另整理了一个测试文件的末尾空行，并针对该文件重新执行16项测试，全部通过。
- 两份原目录共281/1199个已备份文件内容和原始Git状态均已复核一致。

本次归档没有重跑真实网络来源审计；源码与已验收版本一致。最近一次真实审计结论仍为：42个来源有记录，其中41个执行、网易1个受限未执行；9个来源交付20条经过浏览器核验且入库的岗位。9条待核验候选未计入交付。详见[来源验收报告](../source-validation-20261026/ACCEPTANCE.md)。

## 备份与恢复入口

本地归档目录：`C:/Users/13993/Desktop/大模型学习/Agent-archives/master-20261026-202156/`。

- `archive-manifest.json`：原提交、快照分支、文件数及最终主分支记录。
- `primary-files.zip`、`validated-files.zip`：Git跟踪与可见新增文件的原始字节备份，以及存在时的本地.env/Streamlit配置；运行状态文件仅存本机备份包，不加入master。
- `primary-file-manifest.json`、`validated-file-manifest.json`：文件哈希清单。
- `primary-changes.patch`、`validated-changes.patch`：原有修改的二进制补丁。
- `primary-original.index`、`validated-original.index`：原索引副本，原工作树索引保持不动。
- `verified-test-evidence/`：上一轮完整回归、来源审计和公开网页证据包。
- `master-full-regression.log`、`master-coverage.xml`、`master-*-*.log`：本次归档副本验证日志。
- `repository-snapshots.bundle`、`master-source.zip`：归档完成后的Git引用包与主分支源码包，附文件哈希清单。

ZIP不是忽略目录的完整磁盘镜像。SQLite、历史streamlit-runs、portfolio-artifacts和缓存等原目录仍原样保留；以后删除任何目录前，还需按用户指定范围核对这些运行数据。

## 等待用户核对

- master检出目录为 `Agent/.worktrees/master-archive-review`，便于直接查看与运行。
- 原主目录仍位于 `fix/source-reliability-finish`，原 `agent-improvements` 仍位于 `codex/agent-improvements`，保留原有未提交状态。
- 所有历史分支与工作树均保留；本次没有删除文件、目录、工作树、分支或运行数据，也没有push到远程。
- 网站继续由原 `agent-improvements` 提供服务，入口为 http://127.0.0.1:8510 。
- 用户核对成果、备份和运行情况后，再明确指定允许删除的对象；本次到归档和核对材料为止。

# B：岗位来源证据与离线回放实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 复用现有可靠性与阅读回放实现，不覆盖历史实验目录。

**Goal:** 每条推荐都能回答从哪里发现、用什么内容验证、何时采集以及为何失败；相同归档能够断网回放。

**Architecture:** 在既有发现对象和 loader 外增加类型化 provenance 与追加式 archive。在线取正文、缓存/列表降级、冻结回放分层记账；所有产物引用同一 manifest，正文与标签分别版本化。

**Tech Stack:** Python、Pydantic、SHA-256、JSON/JSONL、现有 AggregatorPageLoader、ArtifactStore、pytest。

---

## 当前基础与扩展边界

根目录：`C:\Users\13993\Desktop\大模型学习\Agent`。

已有 `DiscoveredJob`、`BrowserPage.metadata`、`content_origin`、canonical URL、缓存 TTL、`resolution_log`、`source_diagnostics`。已有 `reading_replay.py` 对相同 URL/标题/正文复用一次模型输入，并按城市分别判定；这些继续保留。新 archive 解决发现输入与采集过程的完整复现，旧回放解决阅读对照，两者不要混成同一个成功率。

## 文件责任

| 文件 | 动作与责任 |
|---|---|
| `src/web_task_agent/source_provenance.py` | 新增：候选、正文、尝试、失败、归档 manifest 的类型和 ID。 |
| `src/web_task_agent/source_archive.py` | 新增：原子保存、哈希验证、schema 版本、路径边界。 |
| `src/web_task_agent/source_replay.py` | 新增：只读归档 browser/source adapter，不含公网回退。 |
| `src/web_task_agent/job_sources.py` | 修改：DiscoveredJob 增加可选来源字段；loader 各分支记录尝试和最终引用。 |
| `src/web_task_agent/official_list_source.py`、`official_list_families.py` | 修改：来源 spec 和原始岗位 ID 透传，不改已有 API 协议。 |
| `src/web_task_agent/browser.py`、`models.py`、`storage.py` | 修改：metadata/缓存保留 archive refs、请求 URL 与 canonical URL。 |
| `src/web_task_agent/reading_replay.py` | 修改：同时支持旧 pages.json 和新 manifest；旧输出语义兼容。 |
| `src/web_task_agent/agent_components.py`、`agent_service.py` | A/B 交接：online/replay adapter 选择，产物白名单。 |
| `scripts/accept_source_replay.py` | 新增：留档、断网回放、校验和失败汇总。 |
| `tests/test_source_provenance.py`、`test_source_archive.py`、`test_source_replay.py` | 新增：数据、持久化、回放和反例测试。 |

## B1. 定义完整来源契约（约 3h）

- [x] 先写 ID 稳定性和来源区分测试：相同 canonical 岗位 ID 在不同入口发现可关联；同一 URL 的新正文必须是新 snapshot；同一页面多个岗位不能合并。
- [x] 在 `source_provenance.py` 增加以下模型骨架的全部字段；这不是对远端协议的猜测，只保存当前 adapter 已知信息，未知字段为 null：

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class CandidateEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = 1
    candidate_id: str
    source_spec: str
    source_job_id: str | None = None
    discovery_url: str | None = None
    requested_url: str
    canonical_url: str | None = None
    keyword: str
    location: str
    discovered_at: datetime
    title: str = ""
    company: str = ""
    location_hint: str = ""
    query_context: dict[str, str | int] = Field(default_factory=dict)

class SnapshotEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    snapshot_id: str
    candidate_id: str
    captured_at: datetime
    content_origin: Literal["official-api", "http", "list", "cache"]
    requested_url: str
    canonical_url: str | None = None
    title: str
    body_relative_path: str
    body_sha256: str
    input_sha256: str
    evidence_level: Literal["detail", "list", "historical"]
    availability: Literal["observed", "removed", "unknown"]
    observed_at: datetime | None = None
    cache_age_seconds: float | None = None
```

- [x] 增加时区、哈希格式、相对路径、长度和来源枚举校验。`captured_at` 是保存时间，`observed_at` 是外部内容观察时间；读旧缓存不能把旧 observed_at 更新成现在。
- [x] `candidate_id` 来自来源 spec+来源岗位 ID；没有原始 ID 时用 canonical/requested URL 的 hash。`snapshot_id` 为规范化 JSON（标题、正文、来源、原观察时间）的 hash；`body_sha256` 对实际保存的 UTF-8 正文字节计算，不把规范化哈希冒充原文哈希。
- [x] 定义 `ResolutionAttempt`：candidate_id、sequence、strategy、started_at、elapsed_ms、status、error_category、http_status、snapshot_id。定义 `ArchiveManifest`：schema_version、run_id、conditions、candidate_ids、snapshot_ids、attempts_path、created_at、code_revision、dirty_manifest_sha256。
- [x] 测试未知字段和无时区日期被拒绝；同一快照跨城市复用但 candidate/request 条件保持独立。对 URL 和 query_context 使用白名单，仅保存岗位 ID、分页、关键词、城市等，不保存 headers、Cookie、签名参数和完整请求包。

## B2. 接入既有发现与正文链，追加式归档（约 5–7h）

- [x] 新 `SourceArchive(root)` 提供 `write_candidate(evidence)`、`write_page(candidate_id, page, observation)`、`write_failure(candidate_id, failure)`、`read_page(snapshot_id)`、`write_manifest(manifest)`。前两个方法分别返回 candidate_id、snapshot_id 字符串，`read_page` 返回带 provenance metadata 的 BrowserPage。`observation` 接受下方 fixture 所示字段映射，由 SnapshotEvidence 校验；失败使用当前 PageFailureCategory。其他写方法返回写入文件的相对路径。
- [x] 目录固定为 `source-archive/<run_id>/manifest.json`、`candidates.jsonl`、`attempts.jsonl`、`snapshots/<snapshot_id>.json`、`bodies/<body_sha256>.txt`。文件临时写入后原子 replace；run_id 已存在时拒绝以新采集覆盖，resume 只能追加新的 attempt 和 manifest revision。
- [x] 每次 adapter 调用前后各记录稳定序号与结果；缓存、官方 API、HTTP、列表正文都记账。最终成功不能抹掉前面 404、限流、超时或不支持 API 的记录。`resolution_log` 继续兼容展示，新证据是规范来源。
- [x] `DiscoveredJob` 增加默认空的 `source_job_id`、`discovery_url`、`query_context`、`discovered_at`；先覆盖实际验收的腾讯、小米及一种 family adapter，再给其余来源记录已知 spec/URL，不伪造未知 ID。
- [x] `BrowserPage.metadata` 透传 candidate_id/snapshot_id/canonical_url/observed_at，缓存序列化保留这些引用。旧 cache 读出没有 provenance 时标记 legacy_unknown，新采集不能假称具备完整历史证据。
- [x] 存储测试逐字验证正文；传入坏 hash、`../` 路径、未知 schema、残缺写入时失败；同正文去重不丢失不同来源的引用。只对公开岗位正文留档，不包含候选人简历、登录页面或模型密钥。
- [x] 来源移除判定保持现有策略：当前详情 404/410/明确移除优先于旧列表正文，不向下继续兜底；403/429/超时/5xx 可在预算内降级，但最终标明 stale/list 证据，不能写成“详情在线验证成功”。缓存命中而未当次探测时 availability=unknown。

## B3. 断网回放和旧格式兼容（约 4–6h）

- [x] `ArchiveBrowser` 实现既有 `BrowserClient.search/open_url`，只从 manifest 读取候选/快照；未记录 URL 产生 `missing_snapshot`，不启动浏览器、不请求 HTTP、不调用搜索。
- [x] 定义两种回放：`content` 直接读取最后一次成功正文，用于固定阅读/推荐比较；`attempts` 按原尝试顺序注入 TimeoutError/PageRemovedError 等安全类别，用于验证恢复。二者都不把原始异常响应正文重新抛出。
- [x] 每次读取验证 body hash 和 snapshot input hash；manifest 条件与请求不同须显式选择重新判定，不能静默套用旧过滤结论。快照存在不代表当下岗位仍开放。
- [x] 对模型也分层：`rules` 使用确定性阅读；`recorded` 只使用模型缓存中与输入 hash+prompt hash+provider+model+参数一致的记录；缺失时返回 `missing_model_record`。任何回放模式不得悄悄调用真实模型补齐。
- [x] 下面的测试模式放进 `tests/test_source_replay.py`，测试 fixture 使用真实 B2 writer 创建小归档：

```python
from datetime import UTC, datetime
import pytest
from web_task_agent.models import BrowserPage
from web_task_agent.source_archive import SourceArchive
from web_task_agent.source_provenance import CandidateEvidence

@pytest.fixture
def archived_page(tmp_path):
    now = datetime(2026, 10, 23, tzinfo=UTC)
    url = "https://example.com/jobs/1"
    page = BrowserPage(url=url, title="AI Agent Intern", source="http",
        content="Title: AI Agent Intern\nCompany: Example\nLocation: Remote\n"
                "Requirements: Python LangGraph\nResponsibilities: Build AI agents")
    archive_root = tmp_path / "archive"
    archive = SourceArchive(archive_root)
    candidate_id = archive.write_candidate(CandidateEvidence(
        candidate_id="fixture-1", source_spec="fixture", source_job_id="1",
        discovery_url="https://example.com/jobs", requested_url=url,
        keyword="AI intern", location="Remote", discovered_at=now,
    ))
    snapshot_id = archive.write_page(candidate_id, page, {
        "captured_at": now, "observed_at": now, "content_origin": "http",
        "canonical_url": url, "evidence_level": "detail", "availability": "observed",
    })
    return archive_root, snapshot_id, page

def fail_network(*args, **kwargs):
    raise AssertionError("network must not be used in replay")

def test_replay_integrity(archived_page, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    archive_root, snapshot_id, original_page = archived_page
    loaded = SourceArchive(archive_root).read_page(snapshot_id)
    assert loaded.content == original_page.content
    assert loaded.title == original_page.title
    assert loaded.metadata["snapshot_id"] == snapshot_id
```

`archived_page` fixture 返回 archive root、snapshot_id 和原 BrowserPage。必须另加异步完整 service 回放测试，同时替换所有 provider transport 和来源 adapter 为 fail_network，单测 socket 并不足以证明整个服务未联网。B3 的 adapter 测试可先独立完成，完整 service 回放测试在 A4 交接后执行，并作为 A7 的关闭条件。

- [x] `reading_replay.py` 增加新 manifest reader；旧 `comparison.json + pages.json` 仍可读，转换结果标 `legacy_partial_provenance`。保持原正文不变、逐城市判定、所有 error/review 留在分母的现有测试。
- [x] 断网下跑 A 的目标 3、目标不足、超时恢复、移除禁止降级场景。比较规范化岗位集合、确定性 trace、终态；时间、随机 ID 单独排除。

## B4. 来源指标与报告挂接（约 3–4h）

- [x] 在 A service 的 result.json、Markdown、Dashboard 保存 manifest 引用、采集日期和模式；每条岗位显示 `detail/list/historical` 的人类可读解释，点击来源不伪装成当次详情验证。
- [x] 报告按下面口径分别保留原始分子/分母；不得合并成一个“网页成功率”：

| 指标 | 分子/分母 |
|---|---|
| 来源可调用率 | 返回有效协议结构的来源 / 实际调用来源；empty 与 error 分开 |
| 有候选来源率 | 产出至少一个候选的来源 / 实际调用来源 |
| 详情正文获取率 | 从当次详情 API/HTTP 获得有效正文的候选 / 尝试详情获取候选 |
| 列表/缓存交付数 | 按最终 content_origin 分组的实际交付数量，不计入详情成功 |
| 条件通过率 | 满足固定条件的唯一岗位 / 进入验证的唯一岗位 |
| 快照完整率 | 所有必要文件存在且 hash 正确的记录 / 计划归档记录 |
| 回放一致率 | 规范化字段、终态与确定性轨迹一致记录 / 有完整快照的计划记录 |

- [x] 移除、限流、超时、空正文、列表页、字段冲突、条件排除分别展示。候选数、原网页数、岗位记录数三种单位分别计数，多岗位公告不能充当多个成功页面。
- [x] 更新 `ArtifactStore` 白名单：公开下载安全 manifest/摘要；大正文归档单独导出可选包，下载前验证文件只在本 run archive 根内。run-context、checkpoint 和简历不进入公开归档包。
- [x] 若文件存储 TTL 清理 run，归档索引和正文按引用一起清理，不删仍被评测版本引用的冻结快照；冻结数据集使用独立目录和 retention 策略。

## B5. 实际来源观察、版本化导出与验收（约 3–6h）

- [x] 新建 `scripts/accept_source_replay.py`，参数为 `--mode capture|replay|verify`、`--input-dir`、`--output-dir`、`--specs`、`--keyword`、`--location`、`--target-count`、`--replay-mode content|attempts`、`--reader rules|recorded`。capture 调 A service；replay 只读 archive；verify 只检完整性。
- [x] 首次 online 固定 `tencent-campus,xiaomi` 加一种实际可用 family 来源，关键词 `AI Agent 实习`，北京/上海分别观察。扩大来源前保留本批全部失败，不能换来源凑满。
- [x] 分三次运行记录真实客户端时间、来源集合和预算，建议跨至少两个时段。无候选可如实为零，记录原因；三轮不证明长期稳定性。
- [x] 只有确认可用的原始发现上下文才能用于新来源重抓；旧 40 条 benchmark 保持不变。对旧数据无法补回的 source_id/endpoint 标 unknown，不能根据域名猜出不存在的证据。
- [x] 所有正文与模型记录按 manifest hash 冻结，交 C 创建新版本；10-17 的标签更正保留 old/new 和原因，不回写旧审计结果。

## 验收命令

```powershell
Set-Location -LiteralPath 'C:\Users\13993\Desktop\大模型学习\Agent'
.\.venv\Scripts\python.exe -m pytest tests/test_source_provenance.py tests/test_source_archive.py tests/test_source_replay.py tests/test_aggregator_loader.py tests/test_page_cache.py tests/test_page_reliability.py tests/test_source_reliability_runner.py tests/test_reading_replay.py -v
.\.venv\Scripts\python.exe scripts/accept_source_replay.py --mode capture --specs tencent-campus,xiaomi --keyword 'AI Agent 实习' --location '北京、上海' --target-count 3 --output-dir portfolio-artifacts/source-archive-v1-live
.\.venv\Scripts\python.exe scripts/accept_source_replay.py --mode verify --input-dir portfolio-artifacts/source-archive-v1-live
.\.venv\Scripts\python.exe scripts/accept_source_replay.py --mode replay --input-dir portfolio-artifacts/source-archive-v1-live --output-dir portfolio-artifacts/source-archive-v1-replay --replay-mode content --reader rules
```

以上新命令为实施后验收入口。期望：fixture 的快照完整与确定性回放一致均通过；完整 service 断网测试零外部调用；归档被篡改必须拒绝；实际来源失败保留，历史内容标清时间。B 关闭不要求每条真实 URL 成功，而要求每个实际结果和失败均有可验证的证据链，且不再用 URL 列表冒充完整重现输入。

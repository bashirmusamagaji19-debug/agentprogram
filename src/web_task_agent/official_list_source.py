"""官方列表 API 直连发现(阶段 3A:实时岗位)。

背景(2026-09-08,见 docs/superpowers/plans/2026-09-08-phase3-realtime-discovery.md):
- job-radar 快照有时效问题(快照到运行之间岗位可能下线,实习岗表现 404)
- 大厂招聘官网有公开无鉴权的列表 JSON 接口(story #23 方法论:从 JS bundle
  挖接口),源头直连 = 零时效问题 + 发布时间真实
- 发现的 postId URL 走既有详情链(OfficialApiContentFetcher 按 host 路由,
  校招岗 E1005 自动落 join.qq.com 兜底,story #33 canonical 修复保证链接可打开)

与 AggregatorRepoSource 同协议(JobSource:discover(limit) → DiscoveredJob),
AggregatorPageLoader 直接复用 —— 官方 API 取正文的分支按 URL 命中。
"""

from __future__ import annotations

import json
from urllib import parse as url_parse
from urllib import request as url_request

from web_task_agent.job_sources import AI_TITLE_KEYWORDS, DiscoveredJob, is_intern_ai_job

_TENCENT_SEARCH_POSITION_API = "https://join.qq.com/api/v1/position/searchPosition"
_MEITUAN_LIST_API = "https://zhaopin.meituan.com/api/official/job/getJobList"

# 美团列表接口按 keywords 全文检索,配置与详情适配器(_fetch_meituan)一致的
# 关键词集合;jobTypeList 含实习类型。
MEITUAN_LIST_KEYWORDS = ["大模型", "AI", "Agent", "LLM", "算法"]

TransportOpenJson = callable  # (method, url, *, body) -> dict 的协议别名(鸭子类型)


def _default_transport() -> DefaultTransport:
    return DefaultTransport()


class DefaultTransport:
    """urllib 传输层,测试用 FakeTransport 替换。"""

    def open_json(self, method: str, url: str, *, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = url_request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
        with url_request.urlopen(req, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))


# 腾讯校招库:标题过滤口径与 job-radar 模式(is_intern_ai_job)一致 ——
# 校招标题不一定带"实习"(如"AI全栈工程师",项目=应届毕业生),
# 所以"实习/校招"由 projectName/recruitLabelName 判定,AI 方向由标题判定。
def _is_campus_intern_ai(item: dict) -> bool:
    title = str(item.get("positionTitle") or "").strip()
    label = str(item.get("recruitLabelName") or "")
    project = str(item.get("projectName") or "")
    # 校招属性由项目/标签判定(校招标题不一定带"实习",如"AI全栈工程师")
    if not any(keyword in label + project + title for keyword in ("实习", "校招", "应届", "青云")):
        return False
    return any(keyword in title for keyword in AI_TITLE_KEYWORDS)


async def _tencent_campus_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    jobs: list[DiscoveredJob] = []
    page_no = 1
    while len(jobs) < limit and page_no <= 20:  # 20 页封顶,防失控
        payload = transport.open_json(
            "POST",
            _TENCENT_SEARCH_POSITION_API,
            body={"pageNo": page_no, "pageSize": 50},
        )
        items = (payload.get("data") or {}).get("positionList") or []
        if not items:
            break
        for item in items:
            if not _is_campus_intern_ai(item):
                continue
            post_id = str(item.get("postId") or "").strip()
            title = str(item.get("positionTitle") or "").strip()
            if not post_id or not title:
                continue
            jobs.append(
                DiscoveredJob(
                    url=(
                        "https://careers.tencent.com/jobdesc.html?postId="
                        f"{url_parse.quote(post_id)}"
                    ),
                    title=title,
                    company="腾讯",
                    location=str(item.get("workCities") or "").strip(),
                    jd_text="",  # 列表不带 JD;正文由详情链官方 API 直取
                    source="tencent-campus",
                    tags=[str(item.get("recruitLabelName") or "")],
                )
            )
            if len(jobs) >= limit:
                break
        page_no += 1
    return jobs


async def _meituan_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    jobs: list[DiscoveredJob] = []
    for keyword in MEITUAN_LIST_KEYWORDS:
        if len(jobs) >= limit:
            break
        page_no = 1
        while len(jobs) < limit and page_no <= 5:
            payload = transport.open_json(
                "POST",
                _MEITUAN_LIST_API,
                body={
                    "keywords": [keyword],
                    "jobTypeList": [3],  # 实习类型(与详情适配器一致)
                    "page": {"pageNo": page_no, "pageSize": 50},
                },
            )
            data = payload.get("data") or {}
            items = data.get("jobList") or []
            if not items:
                break
            for item in items:
                name = str(item.get("name") or "").strip()
                if not name or not is_intern_ai_job(name):
                    continue
                union_id = str(item.get("jobUnionId") or "").strip()
                if not union_id:
                    continue
                cities = "、".join(
                    str(c.get("name") or "").strip()
                    for c in (item.get("cityList") or [])
                    if isinstance(c, dict) and c.get("name")
                )
                jobs.append(
                    DiscoveredJob(
                        url=f"https://zhaopin.meituan.com/jobdetail?jobUnionId={union_id}",
                        title=name,
                        company="美团",
                        location=cities,
                        # 列表自带 jobDuty/jobRequirement,拼接为 jd_text 兜底
                        jd_text=(
                            f"岗位职责：\n{item.get('jobDuty', '')}\n"
                            f"任职要求：\n{item.get('jobRequirement', '')}"
                        ).strip(),
                        source="meituan",
                    )
                )
                if len(jobs) >= limit:
                    break
            page_no += 1
    return jobs


_SPECS = {
    "tencent-campus": _tencent_campus_lister,
    "meituan": _meituan_lister,
}


class OfficialListSource:
    """官方列表 API 直连发现源(实时岗位)。

    Args:
        specs: 站点适配器名列表(默认全部),发现顺序 = 传入顺序。
        transport: 传输层(DefaultTransport),测试注入 fake。
    """

    def __init__(
        self,
        specs: list[str] | None = None,
        *,
        transport: object | None = None,
    ) -> None:
        self.specs = specs or list(_SPECS)
        for spec in self.specs:
            if spec not in _SPECS:
                raise ValueError(
                    f"unknown spec: {spec} (available: {sorted(_SPECS)})"
                )
        self.transport = transport if transport is not None else DefaultTransport()

    async def discover(self, limit: int) -> list[DiscoveredJob]:
        if limit <= 0:
            return []
        jobs: list[DiscoveredJob] = []
        seen: set[str] = set()
        for spec in self.specs:
            if len(jobs) >= limit:
                break
            for job in await _SPECS[spec](self.transport, limit - len(jobs)):
                key = job.url.rstrip("/").lower()
                if key in seen:
                    continue
                seen.add(key)
                jobs.append(job)
        return jobs

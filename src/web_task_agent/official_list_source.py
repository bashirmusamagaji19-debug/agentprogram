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
from web_task_agent.official_list_families import (
    make_feishu_hire_lister,
    make_moka_lister,
    make_zhiye_lister,
)

# 具身智能/机器人是目标求职域(用户新增车企与具身智能厂商),与 AI 词并列为命中口径
_DOMAIN_TITLE_KEYWORDS = (*AI_TITLE_KEYWORDS, "具身", "机器人", "自动驾驶", "智驾")


def _title_in_domain(title: str) -> bool:
    return any(keyword in title for keyword in _DOMAIN_TITLE_KEYWORDS)

_TENCENT_SEARCH_POSITION_API = "https://join.qq.com/api/v1/position/searchPosition"
_MEITUAN_LIST_API = "https://zhaopin.meituan.com/api/official/job/getJobList"

# 美团列表接口按 keywords 全文检索,配置与详情适配器(_fetch_meituan)一致的
# 关键词集合;jobTypeList 含实习类型。
MEITUAN_LIST_KEYWORDS = ["大模型", "AI", "Agent", "LLM", "算法"]

TransportOpenJson = callable  # (method, url, *, body) -> dict 的协议别名(鸭子类型)


def _default_transport() -> DefaultTransport:
    return DefaultTransport()


class DefaultTransport:
    """HTTP 传输层,测试用 FakeTransport 替换。

    双通道:优先 curl_cffi(浏览器级 TLS 指纹 — 飞书招聘系网关按 JA3 过滤
    原生 TLS 栈,实测 405;复核记录见 docs/results/official-api-specs JSON),
    不可用时回退 urllib。响应按 Content-Encoding 自动解压 gzip/deflate。
    """

    def __init__(self) -> None:
        try:
            from curl_cffi import requests as _creq  # noqa: F401

            self._cffi = _creq
        except ImportError:
            self._cffi = None

    def open_json(self, method: str, url: str, *, body: dict | None = None) -> dict:
        last_error: Exception | None = None
        for _ in range(2):  # 网络抖动(瞬时 SSL EOF)重试一次,复核实录
            if self._cffi is not None:
                try:
                    return self._open_cffi(method, url, body)
                except Exception as exc:  # noqa: BLE001 — cffi 失败回退 urllib
                    last_error = exc
            try:
                return self._open_urllib(method, url, body)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise last_error  # type: ignore[misc]

    def _open_cffi(self, method: str, url: str, body: dict | None) -> dict:
        # 指纹网关(飞书招聘系)按 JA3 过滤原生 TLS 栈,必须 impersonate;
        # 压缩显式限 gzip(br/zstd 解压在部分环境不可用,复核实录)
        response = self._cffi.request(
            method,
            url,
            json=body if body is not None else None,
            headers={"Accept-Encoding": "gzip, deflate"},
            impersonate="chrome124",
            timeout=30,
        )
        return _loads_maybe_compressed(response.content)

    def _open_urllib(self, method: str, url: str, body: dict | None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = url_request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
        with url_request.urlopen(req, timeout=30) as response:  # noqa: S310
            return _loads_maybe_compressed(response.read())


def _loads_maybe_compressed(raw: bytes) -> dict:
    """按魔数解压 gzip/deflate 后解析 JSON(部分站点无视 Accept-Encoding 强制压缩)。"""
    import gzip
    import zlib

    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:1] == b"\x78":
        raw = zlib.decompress(raw)
    return json.loads(raw.decode("utf-8"))


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


# ── 简单 bespoke 适配器(规格:docs/results/official-api-specs-2026-09-08.json)──


async def _unitree_lister(transport, limit: int) -> list[DiscoveredJob]:
    """宇树科技:GET /website/job/list,items 自带 duty/ability 全文(26 条小全量)。"""
    payload = transport.open_json("GET", "https://api.unitree.com/website/job/list")
    items = (payload.get("data") or {}).get("items") or []
    jobs = []
    for item in items:
        title = str(item.get("title") or "").strip()
        if not _title_in_domain(title):  # 实习/校招属性由站内 type 参数保证
            continue
        plain = f"岗位职责：\n{item.get('duty', '')}\n任职要求：\n{item.get('ability', '')}".strip()
        if len(plain) < 80:
            continue
        job_id = str(item.get("id") or "").strip()
        if not job_id:
            continue
        city = item.get("cityInfo") or item.get("city") or ""
        jobs.append(
            DiscoveredJob(
                url=f"https://www.unitree.com/cn/position/{job_id}",
                title=title,
                company="宇树科技",
                location=str(city if isinstance(city, str) else "") or "杭州",
                jd_text=plain,
                source="unitree",
            )
        )
        if len(jobs) >= limit:
            break
    return jobs


_XIAOMI_KEYWORDS = ["大模型", "AI", "算法", "机器人"]


async def _xiaomi_lister(transport, limit: int) -> list[DiscoveredJob]:
    """小米(含汽车):GET searchJobPage keyword 循环,type=2 校招 / 3 实习,自带 JD 与 url。"""
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for job_type in (3, 2):  # 实习优先
        for keyword in _XIAOMI_KEYWORDS:
            if len(jobs) >= limit:
                break
            page_num = 1
            while len(jobs) < limit and page_num <= 5:
                url = (
                    "https://hr.xiaomi.com/website/api/agent/searchJobPage"
                    f"?keyword={url_parse.quote(keyword)}&pageSize=50&pageNum={page_num}&type={job_type}"
                )
                payload = transport.open_json("GET", url)
                items = (payload.get("data") or {}).get("list") or []
                if not items:
                    break
                for item in items:
                    title = str(item.get("title") or "").strip()
                    job_url = str(item.get("url") or "").strip()
                    if not title or not job_url or job_url in seen:
                        continue
                    # 实习/校招属性由站内 type/recruitType 参数保证
                    if not any(k in title for k in AI_TITLE_KEYWORDS):
                        continue
                    plain = (
                        f"岗位职责：\n{item.get('description', '')}\n"
                        f"任职要求：\n{item.get('requirement', '')}"
                    ).strip()
                    if len(plain) < 80:
                        continue
                    seen.add(job_url)
                    jobs.append(
                        DiscoveredJob(
                            url=job_url,
                            title=title,
                            company="小米",
                            location="、".join(item.get("cityZhNames") or []),
                            jd_text=plain,
                            source="xiaomi",
                        )
                    )
                    if len(jobs) >= limit:
                        break
                page_num += 1
    return jobs


_NETEASE_KEYWORDS = ["大模型", "算法", "AI", "机器人"]


async def _netease_lister(transport, limit: int) -> list[DiscoveredJob]:
    """网易:POST queryPage,workType=1 实习,keyword 循环,列表自带 description/requirement。"""
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for keyword in _NETEASE_KEYWORDS:
        if len(jobs) >= limit:
            break
        current = 1
        while len(jobs) < limit and current <= 5:
            payload = transport.open_json(
                "POST",
                "https://hr.163.com/api/hr163/position/queryPage",
                body={"currentPage": current, "pageSize": 50, "keyword": keyword, "workType": "1"},
            )
            items = (payload.get("data") or {}).get("list") or []
            if not items:
                break
            for item in items:
                title = str(item.get("name") or "").strip()
                job_id = str(item.get("id") or "").strip()
                url = f"https://hr.163.com/job-detail.html?id={job_id}" if job_id else ""
                if not title or not url or url in seen:
                    continue
                if not _title_in_domain(title):  # 实习/校招属性由站内 type 参数保证
                    continue
                plain = (
                    f"岗位职责：\n{item.get('description', '')}\n"
                    f"任职要求：\n{item.get('requirement', '')}"
                ).strip()
                if len(plain) < 80:
                    continue
                seen.add(url)
                jobs.append(
                    DiscoveredJob(
                        url=url,
                        title=title,
                        company="网易",
                        location=str(
                            item.get("workLocationName") or item.get("addressName") or ""
                        ).strip(),
                        jd_text=plain,
                        source="netease",
                    )
                )
                if len(jobs) >= limit:
                    break
            current += 1
    return jobs


async def _xiaohongshu_lister(transport, limit: int) -> list[DiscoveredJob]:
    """小红书:POST pageQueryPosition,campus/intern 两轮,列表自带 duty/qualification。"""
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for recruit_type in ("intern", "campus"):
        if len(jobs) >= limit:
            break
        page_no = 1
        while len(jobs) < limit and page_no <= 10:
            payload = transport.open_json(
                "POST",
                "https://job.xiaohongshu.com/websiterecruit/position/pageQueryPosition",
                body={"recruitType": recruit_type, "pageNo": page_no, "pageSize": 50},
            )
            items = (payload.get("data") or {}).get("list") or []
            if not items:
                break
            for item in items:
                title = str(item.get("positionName") or "").strip()
                position_id = str(item.get("positionId") or "").strip()
                url = (
                    f"https://job.xiaohongshu.com/campus/position/{position_id}"
                    if position_id
                    else ""
                )
                if not title or not url or url in seen:
                    continue
                if not _title_in_domain(title):  # 实习/校招属性由站内 type 参数保证
                    continue
                plain = (
                    f"岗位职责：\n{item.get('duty', '')}\n"
                    f"任职要求：\n{item.get('qualification', '')}"
                ).strip()
                if len(plain) < 80:
                    continue
                seen.add(url)
                jobs.append(
                    DiscoveredJob(
                        url=url,
                        title=title,
                        company="小红书",
                        location=str(item.get("workplace") or "").strip(),
                        jd_text=plain,
                        source="xiaohongshu",
                    )
                )
                if len(jobs) >= limit:
                    break
            page_no += 1
    return jobs


_MIHOYO_DETAIL_API = "https://ats.openout.mihoyo.com/ats-portal/v1/job/info"


async def _mihoyo_lister(transport, limit: int) -> list[DiscoveredJob]:
    """米哈游:POST job/list(hireType=1 校招含实习)+ 逐条 job/info 补 JD。"""
    payload = transport.open_json(
        "POST",
        "https://ats.openout.mihoyo.com/ats-portal/v1/job/list",
        body={"pageNo": 1, "pageSize": 100, "channelDetailIds": [1], "hireType": 1},
    )
    items = (payload.get("data") or {}).get("list") or []
    jobs: list[DiscoveredJob] = []
    for item in items:
        if len(jobs) >= limit:
            break
        title = str(item.get("title") or "").strip()
        job_id = str(item.get("id") or "").strip()
        if not title or not job_id:
            continue
        if not any(k in title for k in AI_TITLE_KEYWORDS):  # 实习/校招属性由站内 type 参数保证
            continue
        try:
            detail = transport.open_json(
                "POST",
                _MIHOYO_DETAIL_API,
                body={"channelDetailIds": [1], "hireType": 1, "id": job_id},
            )
        except Exception:  # noqa: BLE001 — 详情失败按无正文处理
            continue
        data = detail.get("data") or {}
        plain = (
            f"岗位职责：\n{data.get('description', '')}\n"
            f"任职要求：\n{data.get('jobRequire', '')}"
        ).strip()
        if len(plain) < 80:
            continue
        jobs.append(
            DiscoveredJob(
                url=f"https://jobs.mihoyo.com/#/campus/position/{job_id}",
                title=title,
                company="米哈游",
                location="、".join(
                    str(a.get("addressDetail") or "")
                    for a in (data.get("addressDetailList") or item.get("addressDetailList") or [])
                    if isinstance(a, dict)
                )
                or str(item.get("cityName") or ""),
                jd_text=plain,
                source="mihoyo",
            )
        )
    return jobs


def _family_specs() -> dict[str, object]:
    """SaaS 家族实例(配置来自逆向规格 JSON,复核记录见同目录 verify_failed)。"""
    return {
        "xpeng": make_feishu_hire_lister(
            api_url="https://xiaopeng.jobs.feishu.cn/api/v1/search/job/posts",
            url_tmpl="https://xiaopeng.jobs.feishu.cn/campus/position/{id}",
            extra_body={"site_id": "7280103511501048076", "portal_entrance": 2},
        ),
        "agibot": make_feishu_hire_lister(
            api_url="https://agirobot.jobs.feishu.cn/api/v1/search/job/posts",
            url_tmpl="https://agirobot.jobs.feishu.cn/internrecruitment/position/{id}/detail",
            extra_body={"portal_type": 6, "portal_entrance": 1},
        ),
        "galaxea": make_moka_lister(
            api_url="https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2",
            org_id="yinhetongyong",
            site_id=165930,
            url_tmpl="https://app.mokahr.com/campus-recruitment/yinhetongyong/165930#/job/{id}",
            company="银河通用",
            detail_fetch=True,
        ),
        "robotera": make_moka_lister(
            api_url="https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2",
            org_id="robotera",
            site_id=163878,
            url_tmpl="https://app.mokahr.com/campus-recruitment/robotera/163878/{id}",
            company="星动纪元",
            paging="page",
        ),
        "fourier": make_moka_lister(
            api_url="https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2",
            org_id="fftai",
            site_id=147078,
            url_tmpl="https://app.mokahr.com/campus-recruitment/fftai/147078#/job/{id}",
            company="傅利叶智能",
        ),
        "ubtech": make_zhiye_lister(
            api_url="https://ubtrobot.zhiye.com/api/Jobad/GetJobAdPageList?portalId=4f4d24cb-5973-40d0-9fde-27c6a0dd8570",
            portal_id="4f4d24cb-5973-40d0-9fde-27c6a0dd8570",
            url_tmpl="https://ubtrobot.zhiye.com/intern/detail/{id}",
            company="优必选",
        ),
    }


_SPECS: dict[str, object] = {
    "tencent-campus": _tencent_campus_lister,
    "meituan": _meituan_lister,
    "unitree": _unitree_lister,
    "xiaomi": _xiaomi_lister,
    "netease": _netease_lister,
    "xiaohongshu": _xiaohongshu_lister,
    "mihoyo": _mihoyo_lister,
    **_family_specs(),
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

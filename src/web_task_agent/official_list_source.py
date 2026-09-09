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
    _strip_html,
    decrypt_moka_envelope,
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

    def open_json(
        self,
        method: str,
        url: str,
        *,
        body: dict | None = None,
        form: bool = False,
    ) -> dict:
        last_error: Exception | None = None
        for _ in range(2):  # 网络抖动(瞬时 SSL EOF)重试一次,复核实录
            if self._cffi is not None:
                try:
                    return self._open_cffi(method, url, body, form)
                except Exception as exc:  # noqa: BLE001 — cffi 失败回退 urllib
                    last_error = exc
            try:
                return self._open_urllib(method, url, body, form)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise last_error  # type: ignore[misc]

    def _open_cffi(self, method: str, url: str, body: dict | None, form: bool) -> dict:
        # 指纹网关(飞书招聘系)按 JA3 过滤原生 TLS 栈,必须 impersonate;
        # 压缩显式限 gzip(br/zstd 解压在部分环境不可用,复核实录)
        if body is None:
            data = None
            json_payload = None
        elif form:
            data = url_parse.urlencode(body)
            json_payload = None
        else:
            data = None
            json_payload = body
        response = self._cffi.request(
            method,
            url,
            data=data,
            json=json_payload,
            headers={"Accept-Encoding": "gzip, deflate"},
            impersonate="chrome124",
            timeout=30,
        )
        return _loads_maybe_compressed(response.content)

    def _open_urllib(self, method: str, url: str, body: dict | None, form: bool) -> dict:
        if body is None:
            data = None
        elif form:
            data = url_parse.urlencode(body).encode("utf-8")
        else:
            data = json.dumps(body).encode("utf-8")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
        req = url_request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
        with url_request.urlopen(req, timeout=30) as response:  # noqa: S310
            return _loads_maybe_compressed(response.read())

    def fetch_html(self, url: str) -> str:
        """抓取 HTML 文本(SERP 解析用),双通道与重试语义同 open_json。"""
        last_error: Exception | None = None
        for _ in range(2):
            if self._cffi is not None:
                try:
                    response = self._cffi.request(
                        "GET",
                        url,
                        headers={"Accept-Encoding": "gzip, deflate"},
                        impersonate="chrome124",
                        timeout=30,
                    )
                    return _text_maybe_compressed(response.content)
                except Exception:  # noqa: BLE001 — 回退 urllib
                    pass
            try:
                req = url_request.Request(  # noqa: S310
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0"
                        )
                    },
                    method="GET",
                )
                with url_request.urlopen(req, timeout=30) as response:
                    return _text_maybe_compressed(response.read())
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise last_error  # type: ignore[misc]


def _loads_maybe_compressed(raw: bytes) -> dict:
    """按魔数解压 gzip/deflate 后解析 JSON(部分站点无视 Accept-Encoding 强制压缩)。"""
    import gzip
    import zlib

    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:1] == b"\x78":
        raw = zlib.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def _text_maybe_compressed(raw: bytes) -> str:
    """同 _loads_maybe_compressed 的解压逻辑,但返回文本(HTML 抓取用)。"""
    import gzip
    import zlib

    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:1] == b"\x78":
        raw = zlib.decompress(raw)
    return raw.decode("utf-8", errors="replace")


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


_BAIDU_LIST_API = "https://talent.baidu.com/httservice/getPostListNew"
# 百度限流为冷却型(连续请求触发 no-auth illegal-visit,冷却后恢复,复核实录),
# 故 keyWord 集合克制且类目间串行。
_BAIDU_RECRUIT_TYPES = ("INTERN", "GRADUATE", "SOCIAL")
_BAIDU_KEYWORDS = ["大模型", "AI", "算法", "机器人"]


async def _baidu_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    """百度:POST getPostListNew(form 表单),列表自带 workContent/serviceCondition 全文。

    job URL https://talent.baidu.com/jobs/detail/{recruitType}/{postId}。
    遇到限流(status=no-auth)时停止翻页,返回已获取结果(诚实部分产出)。
    """
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for recruit_type in _BAIDU_RECRUIT_TYPES:
        if len(jobs) >= limit:
            break
        for keyword in _BAIDU_KEYWORDS:
            if len(jobs) >= limit:
                break
            page_no = 1
            while len(jobs) < limit and page_no <= 3:
                payload = transport.open_json(
                    "POST",
                    _BAIDU_LIST_API,
                    body={
                        "recruitType": recruit_type,
                        "pageSize": 50,
                        "curPage": page_no,
                        "keyWord": keyword,
                        "postType": "",
                        "workPlace": "",
                        "projectType": "",
                    },
                    form=True,
                )
                if str(payload.get("status")) != "ok":
                    return jobs  # 限流/异常:诚实返回已获取部分
                items = (payload.get("data") or {}).get("list") or []
                if not items:
                    break
                for item in items:
                    title = str(item.get("name") or "").strip()
                    post_id = str(item.get("postId") or "").strip()
                    if not title or not post_id or not _title_in_domain(title):
                        continue
                    url = (
                        f"https://talent.baidu.com/jobs/detail/"
                        f"{recruit_type}/{url_parse.quote(post_id)}"
                    )
                    if url in seen:
                        continue
                    plain = (
                        f"岗位职责：\n{item.get('workContent', '')}\n"
                        f"任职要求：\n{item.get('serviceCondition', '')}"
                    ).strip()
                    if len(plain) < 80:
                        continue
                    seen.add(url)
                    jobs.append(
                        DiscoveredJob(
                            url=url,
                            title=title,
                            company="百度",
                            location=str(
                                item.get("workPlace") or item.get("cityName") or ""
                            ).strip(),
                            jd_text=plain,
                            source="baidu",
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
    "jd": _jd_lister,
    "pinduoduo": _pinduoduo_lister,
    "ctrip": _ctrip_lister,
    "huawei": _huawei_lister,
    "nio": _nio_lister,
    "liauto": _liauto_lister,
    "byd": _byd_lister,
    "geely": _geely_lister,

    # ── 第二批(agent 并行编写,实测冒烟通过)──
    "ubtech": make_zhiye_lister(
            api_url="https://ubtrobot.zhiye.com/api/Jobad/GetJobAdPageList?portalId=4f4d24cb-5973-40d0-9fde-27c6a0dd8570",
            portal_id="4f4d24cb-5973-40d0-9fde-27c6a0dd8570",
            url_tmpl="https://ubtrobot.zhiye.com/intern/detail/{id}",
            company="优必选",
        ),
    }


# ── 京东(阶段 3A-2 第二批,agent 实测冒烟通过)──
_JD_LIST_API = "https://campus.jd.com/api/wx/position/recommendPage"


async def _jd_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    """京东:POST recommendPage,pageSize=1000 一次拉全量(分页参数实测无效),列表自带 JD。

    响应为 {success, body: [{deptCode, deptName, channelPositionVoList: [...]}]},
    岗位对象关键字段:publishId/positionName/workContent/qualification;
    顶层 workCity 恒为 null,城市在 requirementVoList[].workCity(省-市格式)。
    """
    from web_task_agent.official_list_families import _strip_html  # 复用去标签兜底

    payload = transport.open_json(
        "POST",
        _JD_LIST_API,
        body={
            "pageSize": 1000,  # 分页参数实测无效,pageSize>=350 一次拿全量
            "pageIndex": 0,
            "parameter": {
                "planIdList": None,
                "recommendChannelPositionVoList": [{"deptCode": ""}],  # 空串 = 全部条线
            },
        },
    )
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for section in payload.get("body") or []:
        if len(jobs) >= limit:
            break
        for item in section.get("channelPositionVoList") or []:
            if len(jobs) >= limit:
                break
            title = str(item.get("positionName") or "").strip()
            publish_id = str(item.get("publishId") or "").strip()
            if not title or not publish_id:
                continue
            # 口径只按标题域词(AI/大模型/机器人/具身/自动驾驶/智驾),实习/校招/社招都保留
            if not _title_in_domain(title):
                continue
            url = f"https://campus.jd.com/#/details?id={url_parse.quote(publish_id)}"
            if url in seen:
                continue
            plain = (
                f"岗位职责：\n{_strip_html(str(item.get('workContent') or ''))}\n"
                f"任职要求：\n{_strip_html(str(item.get('qualification') or ''))}"
            ).strip()
            if len(plain) < 80:  # 诚实原则:无可验证正文不产出
                continue
            seen.add(url)
            cities: list[str] = []
            for req in item.get("requirementVoList") or []:
                city = str((req or {}).get("workCity") or "").strip()
                if city and city not in cities:
                    cities.append(city)
            jobs.append(
                DiscoveredJob(
                    url=url,
                    title=title,
                    company="京东",
                    location="、".join(cities),
                    jd_text=plain,
                    source="jd",
                )
            )
    return jobs


# ── 拼多多(阶段 3A-2 第二批,agent 实测冒烟通过)──
# ── 拼多多(careers.pddglobalhr.com)──
# 规格:docs/results/official-api-specs-2026-09-08.json #pinduoduo(2026-09-08 实测复核):
# 列表 POST search/list,body {"page":N,"pageSize":M}(字段名是 page 不是 pageNo;
# pageSize 实测上限 15,传 20 也只回 15,全量 31 岗约 3 页);列表自带 jobDuty
# (岗位职责全文),任职要求 serveRequirement 列表不含,需逐条 POST position/detail 补全;
# 详情 result.shareUrl 即详情页链接,格式固定 /campus/grad/detail?positionId=<id>。

_PDD_LIST_API = "https://careers.pddglobalhr.com/api/careers/applets/api/recruit/position/search/list"
_PDD_DETAIL_API = "https://careers.pddglobalhr.com/api/careers/applets/api/recruit/position/detail"


async def _pinduoduo_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    """拼多多:POST search/list 分页(recruitType 不限,社招/校招/实习全量)+ 逐条 detail 补任职要求。

    只按 _title_in_domain 过滤标题;JD = 详情 jobDuty+serveRequirement(失败回退列表
    jobDuty),正文 <80 字符的岗位跳过(诚实原则:不产出没有可验证正文的岗位)。
    """
    from web_task_agent.official_list_families import _strip_html

    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    page_no = 1
    while len(jobs) < limit and page_no <= 15:  # 全量仅 31 岗(约 3 页),15 页封顶防失控
        payload = transport.open_json(
            "POST", _PDD_LIST_API, body={"page": page_no, "pageSize": 20}
        )
        result = payload.get("result") or {}
        items = result.get("list") if isinstance(result, dict) else None
        items = items or []
        if not items:
            break
        for item in items:
            title = str(item.get("name") or "").strip()
            job_id = str(item.get("id") or "").strip()
            if not title or not job_id or not _title_in_domain(title):
                continue
            list_duty = _strip_html(str(item.get("jobDuty") or ""))
            share_url = ""
            serve = ""
            duty = list_duty
            try:  # 详情补任职要求;失败回退列表 jobDuty,无正文在下方按 <80 跳过
                detail = transport.open_json("POST", _PDD_DETAIL_API, body={"id": job_id})
                data = detail.get("result") or {}
                if isinstance(data, dict):
                    share_url = str(data.get("shareUrl") or "").strip()
                    serve = _strip_html(str(data.get("serveRequirement") or ""))
                    duty = _strip_html(str(data.get("jobDuty") or "")) or list_duty
            except Exception:  # noqa: BLE001
                pass
            plain = f"岗位职责：\n{duty}\n任职要求：\n{serve}".strip()
            if len(plain) < 80:
                continue
            url = share_url or (
                "https://careers.pddglobalhr.com/campus/grad/detail?positionId="
                f"{url_parse.quote(job_id)}"
            )
            if url in seen:
                continue
            seen.add(url)
            jobs.append(
                DiscoveredJob(
                    url=url,
                    title=title,
                    company="拼多多",
                    location=str(
                        item.get("workLocationName") or item.get("workLocation") or ""
                    ).strip(),
                    jd_text=plain,
                    source="pinduoduo",
                )
            )
            if len(jobs) >= limit:
                break
        page_no += 1
    return jobs


# ── 携程(阶段 3A-2 第二批,agent 实测冒烟通过)──
_CTRIP_LIST_API = "https://careers.ctrip.com/api/hrrecruit/getJobAd"
_CTRIP_KEYWORDS = ["大模型", "LLM", "Agent", "机器人", "AI", "算法"]
# category=2 校招 / category=1 社招(含 kind=3 实习通道),两类都要 → 路由前缀不同
_CTRIP_CATEGORY_ROUTES = ((2, "campus"), (1, "experienced"))
_CTRIP_CITY_NAMES = {
    "Shanghai": "上海", "Beijing": "北京", "Shenzhen": "深圳", "Hangzhou": "杭州",
    "Guangzhou": "广州", "Nanjing": "南京", "Suzhou": "苏州", "Chengdu": "成都",
    "Wuhan": "武汉", "Xiamen": "厦门", "Tianjin": "天津", "Jinan": "济南",
    "Guilin": "桂林", "Taiyuan": "太原", "Zhengzhou": "郑州", "Nantong": "南通",
}


def _ctrip_jd_text(item: dict) -> str:
    """列表自带 requirements(HTML 全文)与 duty(多为 null),剥标签后拼接。"""
    duty = _strip_html(str(item.get("duty") or "")).strip()
    requirements = _strip_html(str(item.get("requirements") or "")).strip()
    if duty and requirements:
        return f"岗位职责：\n{duty}\n任职要求：\n{requirements}"
    return requirements


async def _ctrip_lister(transport, limit: int) -> list[DiscoveredJob]:
    """携程招聘官网:POST getJobAd,列表自带 JD 全文,category 2 校招 / 1 社招。

    路由参数是 fromId(MJ 开头)而非数字 id(规格复核实录);URL 前缀按
    category 取 /campus/ 或 /experienced/。关键词循环 + 翻页预算 15 页封顶。
    """
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    page_budget = 15  # 关键词 x 类别共享的翻页总预算,防失控
    for category, route in _CTRIP_CATEGORY_ROUTES:
        if len(jobs) >= limit:
            break
        for keyword in _CTRIP_KEYWORDS:
            if len(jobs) >= limit:
                break
            page = 1
            while page <= 5 and page_budget > 0:
                page_budget -= 1
                payload = transport.open_json(
                    "POST",
                    _CTRIP_LIST_API,
                    body={
                        "condition": {
                            "fromId": [], "keyword": keyword, "kind": [], "country": [],
                            "city": [], "bucode": [], "jobFamilyCode": [],
                            "jobFamilyGroupCode": [], "category": category,
                        },
                        "pager": {"index": page, "size": 30},
                    },
                )
                ret = payload.get("retValue") or {}
                items = ret.get("recruitJobAdList") or []
                if not items:
                    break
                for item in items:
                    if len(jobs) >= limit:
                        break
                    title = str(item.get("jobTitle") or "").strip()
                    from_id = str(item.get("fromId") or "").strip()
                    if not title or not from_id or from_id in seen:
                        continue
                    # 实习/校招不过滤(社招也要),只按标题域词(AI/大模型/机器人/具身/自动驾驶/智驾)
                    if not _title_in_domain(title):
                        continue
                    jd_text = _ctrip_jd_text(item)
                    if len(jd_text) < 80:  # 无可验证正文 → 诚实跳过
                        continue
                    seen.add(from_id)
                    city = str(item.get("cityName") or "").strip()
                    jobs.append(
                        DiscoveredJob(
                            url=(
                                "https://careers.ctrip.com/"
                                f"{route}/job-detail/{url_parse.quote(from_id)}"
                            ),
                            title=title,
                            company="携程",
                            location=_CTRIP_CITY_NAMES.get(city, city),
                            jd_text=jd_text,
                            source="ctrip",
                        )
                    )
                total = int(ret.get("total") or 0)
                if page * 30 >= total:
                    break
                page += 1
    return jobs


# ── 华为(阶段 3A-2 第二批,agent 实测冒烟通过)──
_HUAWEI_LIST_API = (
    "https://apigw-dgg-b0.huawei.com/api/apig/channelhw/recruitmentPosition"
    "/pub/getJobPage?X-HW-ID=app_000000035886"
)

# 华为 jalor 网关强制校验自定义头:缺 x-jalor-tenantAlias 返回 TenantContextError、
# 缺 x-Referer 返回空结果(实测 2026-09-08),无法仅靠 URL/body 携带
_HUAWEI_HEADERS = {
    "Content-Type": "application/json",
    "x-jalor-tenantAlias": "hcm",
    "x-language": "zh_CN",
    "x-Referer": "https://career.huawei.com/cn",
    "x-alb-gray": "prod",
    "Referer": "https://career.huawei.com/cn/campus-recruitment-job-list",
}


def _huawei_open_json(transport, url: str, body: dict) -> dict:  # noqa: ANN001
    """带头请求华为网关:先协商 transport 的 headers 形参,失败按传输层能力兜底。

    1. transport.open_json(..., headers=...) — 支持自定义头的传输层(推荐路径);
    2. curl_cffi 直连(chrome 指纹,与 DefaultTransport 同参)— 覆盖未扩展 headers
       形参的 DefaultTransport(实测网关不做 JA3 过滤,urllib 也可,但 cffi 更稳);
    3. 无自定义头回退 — 测试 FakeTransport 路径(真实网关会拒绝,诚实降级为空)。
    """
    try:
        return transport.open_json("POST", url, body=body, headers=_HUAWEI_HEADERS)
    except TypeError:
        pass
    cffi = getattr(transport, "_cffi", None)
    if cffi is not None:
        response = cffi.request(
            "POST",
            url,
            json=body,
            headers={"Accept-Encoding": "gzip, deflate", **_HUAWEI_HEADERS},
            impersonate="chrome124",
            timeout=30,
        )
        return _loads_maybe_compressed(response.content)
    return transport.open_json("POST", url, body=body)


async def _huawei_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    """华为招聘官网:POST getJobPage 全量拉社招(jobType=SR),列表自带 JD 全文。

    口径(2026-09-08 实测):
    - SR(社招)327 岗,pageSize=100 分 4 页拉全,标题域过滤后 29 条有效;
    - CR(校招/实习)列表与详情接口均为 14 字占位文案"请您详见岗位意向中的…"
      (真实 JD 仅在登录后的岗位意向流程可见),按诚实原则(<80 字符不产出)跳过
      —— 不按实习/校招类型过滤,但无正文的岗位不产出;
    - 网关强制自定义头,经 _huawei_open_json 协商携带。
    """
    from web_task_agent.official_list_families import _strip_html

    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    page_no = 1
    while len(jobs) < limit and page_no <= 15:  # 分页封顶 15 页;SR 全量仅 4 页
        payload = _huawei_open_json(
            transport,
            _HUAWEI_LIST_API,
            {"curPage": page_no, "pageSize": 100, "jobType": "SR"},
        )
        items = (payload.get("data") or {}).get("result") or []
        if not items:
            break
        for item in items:
            title = str(item.get("jobName") or "").strip()
            ad_id = str(item.get("advertisementId") or "").strip()
            if not title or not ad_id or not _title_in_domain(title):
                continue
            job_url = f"https://career.huawei.com/cn/job-details?advertisementId={ad_id}"
            if job_url in seen:
                continue
            plain = _strip_html(
                f"岗位职责：\n{item.get('mainBusiness') or ''}\n"
                f"任职要求：\n{item.get('jobRequire') or ''}"
            ).strip()
            # 占位文案("请您详见岗位意向中的…")与 <80 字符正文一律跳过(诚实原则)
            if len(plain) < 80 or "请您详见岗位意向" in plain:
                continue
            seen.add(job_url)
            jobs.append(
                DiscoveredJob(
                    url=job_url,
                    title=title,
                    company="华为",
                    location=str(item.get("workPlace") or "").strip(),
                    jd_text=plain,
                    source="huawei",
                )
            )
            if len(jobs) >= limit:
                break
        page_no += 1
    return jobs


# ── 蔚来(阶段 3A-2 第二批,agent 实测冒烟通过)──
_NIO_SEARCH_API = "https://nio.jobs.feishu.cn/api/v1/search/job/posts"
_NIO_KEYWORDS = ["大模型", "AI", "Agent", "LLM", "算法", "机器人", "自动驾驶", "智驾", "具身"]


async def _nio_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    """蔚来:POST 飞书招聘 search/job/posts(关键词循环),自带 description/requirement。

    规格快照(nio.cn/careers/jobs HTML 内嵌 __NEXT_DATA__,列表不带 JD)实测过期:
    nio.jobs.feishu.cn 走标准飞书招聘搜索接口,job_post_list 自带 JD 全文,故按
    关键词检索直取;详情 URL 用实测可打开的 /index/position/detail/{id}。
    社招/实习均收(不做招聘类型过滤),岗位域只按标题判定。
    """
    from web_task_agent.official_list_families import _strip_html  # 复用 HTML 剥离

    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    pages = 0  # 全局翻页预算(≤15),防失控
    for keyword in _NIO_KEYWORDS:
        if len(jobs) >= limit:
            break
        offset = 0
        while len(jobs) < limit and pages < 15:
            payload = transport.open_json(
                "POST",
                _NIO_SEARCH_API,
                body={"keyword": keyword, "limit": 100, "offset": offset},
            )
            posts = (payload.get("data") or {}).get("job_post_list") or []
            if not posts:
                break
            pages += 1
            for item in posts:
                title = str(item.get("title") or "").strip()
                job_id = str(item.get("id") or "").strip()
                if not title or not job_id:
                    continue
                url = f"https://nio.jobs.feishu.cn/index/position/detail/{job_id}"
                if url in seen:  # 同一岗位会命中多个关键词,按 URL 去重
                    continue
                if not _title_in_domain(title):
                    continue
                plain = _strip_html(
                    f"岗位职责：\n{str(item.get('description') or '').strip()}\n"
                    f"任职要求：\n{str(item.get('requirement') or '').strip()}".strip()
                ).strip()
                if len(plain) < 80:  # 无可验证正文的岗位不进管线
                    continue
                seen.add(url)
                city = str(((item.get("city_info") or {}) or {}).get("name") or "")
                cities = [
                    str(c.get("name") or "")
                    for c in (item.get("city_list") or [])
                    if isinstance(c, dict) and c.get("name")
                ]
                jobs.append(
                    DiscoveredJob(
                        url=url,
                        title=title,
                        company="蔚来",
                        location="、".join(dict.fromkeys(x for x in [city, *cities] if x)),
                        jd_text=plain,
                        source="nio",
                    )
                )
                if len(jobs) >= limit:
                    break
            offset += 100
    return jobs


# ── 理想汽车(阶段 3A-2 第二批,agent 实测冒烟通过)──
# 理想汽车:GET /osd-hr-recruitment-website/v1/recruit/{social|school}/job-page
# search 关键词检索;列表只带岗位元数据(无 JD),正文逐条调
# /v1/recruit/job/detail?job_id= 补 description+requirements(HTML,需剥离标签)。
# 口径:社招也要(social 通道),不过滤实习/校招 —— social(全职)在前,
# school(校招/实习)在后,只按 _title_in_domain 过滤岗位域。
_LIAUTO_CHANNELS = ("social", "school")
_LIAUTO_KEYWORDS = ["大模型", "AI", "机器人"]


async def _liauto_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    """理想汽车:search 关键词 + social/school 双通道,详情接口逐条补 JD。"""
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    page_budget = 15  # 列表翻页总预算,防失控(page_size=50 时 1 页即全量)
    for channel in _LIAUTO_CHANNELS:
        for keyword in _LIAUTO_KEYWORDS:
            if len(jobs) >= limit:
                break
            page = 1
            while len(jobs) < limit and page <= 5 and page_budget > 0:
                page_budget -= 1
                payload = transport.open_json(
                    "GET",
                    (
                        "https://api-web.lixiang.com/osd-hr-recruitment-website"
                        f"/v1/recruit/{channel}/job-page"
                        f"?page={page}&page_size=50&search={url_parse.quote(keyword)}"
                    ),
                )
                items = (payload.get("data") or {}).get("items") or []
                if not items:
                    break
                for item in items:
                    if len(jobs) >= limit:
                        break
                    title = str(item.get("title") or "").strip()
                    job_id = str(item.get("id") or "").strip()
                    if not title or not job_id or job_id in seen:
                        continue
                    if not _title_in_domain(title):  # 实习/校招不过滤,只按岗位域
                        continue
                    try:
                        detail = transport.open_json(
                            "GET",
                            "https://api-web.lixiang.com/osd-hr-recruitment-website"
                            f"/v1/recruit/job/detail?job_id={url_parse.quote(job_id)}",
                        )
                    except Exception:  # noqa: BLE001 — 详情失败按无正文跳过
                        continue
                    data = detail.get("data") or {}
                    plain = (
                        f"岗位职责：\n{_strip_html(str(data.get('description') or ''))}\n"
                        f"任职要求：\n{_strip_html(str(data.get('requirements') or ''))}"
                    ).strip()
                    if len(plain) < 80:  # 诚实原则:无可验证正文的岗位不产出
                        continue
                    seen.add(job_id)
                    jobs.append(
                        DiscoveredJob(
                            url=f"https://www.lixiang.com/employ/detail/{url_parse.quote(job_id)}.html",
                            title=title,
                            company="理想汽车",
                            location=str(item.get("location_title") or "").strip(),
                            jd_text=plain,
                            source="liauto",
                        )
                    )
                page += 1
    return jobs


# ── 比亚迪(阶段 3A-2 第二批,agent 实测冒烟通过)──
_BYD_LIST_API = "https://job.byd.com/portal/api/portal-api/position/queryList"
_BYD_DETAIL_API = "https://job.byd.com/portal/api/portal-api/position/queryDetail"
_BYD_KEYWORDS = ["算法", "机器人", "AI"]


async def _byd_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    """比亚迪:POST queryList vagueCondition 逐词检索 + 逐条 queryDetail 补 JD 正文。

    列表接口不带 JD(实测 detail 全 null),正文在详情 tagDetailList[]
    (name='工作职责'|'任职要求');实测 vagueCondition=算法/AI/机器人均有命中,
    大模型/自动驾驶直接命中为 0(比亚迪用词偏「算法」),故逐词检索。
    """
    from web_task_agent.official_list_families import _strip_html

    def _jd_text(tags: list) -> str:
        duty: list[str] = []
        require: list[str] = []
        other: list[str] = []
        for tag in tags or []:
            name = _strip_html(str(tag.get("name") or "")).strip()
            detail = _strip_html(str(tag.get("detail") or "")).replace("\r\n", "\n").strip()
            if not detail:
                continue
            if "职责" in name:
                duty.append(detail)
            elif "要求" in name:
                require.append(detail)
            else:
                other.append(f"{name}：\n{detail}")
        parts = []
        if duty:
            parts.append("岗位职责：\n" + "\n".join(duty))
        if require:
            parts.append("任职要求：\n" + "\n".join(require))
        parts.extend(other)
        return "\n".join(parts).strip()

    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    seen_ids: set[str] = set()
    for keyword in _BYD_KEYWORDS:
        if len(jobs) >= limit:
            break
        page_num = 0  # 实测服务端忽略 pageSize 一次性返回全量,pageNum 偏移兜底翻页
        while len(jobs) < limit and page_num < 15:
            payload = transport.open_json(
                "POST",
                _BYD_LIST_API,
                body={
                    "positionTypeArr": [],
                    "positionProvinceArr": [],
                    "positionCityArr": [],
                    "positionOrgArr": [],
                    "vagueCondition": keyword,
                    "searchType": 2,
                    "zpType": "00254",
                    "pageNum": page_num,
                    "pageSize": 100,
                },
            )
            items = (payload.get("data") or {}).get("data") or []
            if not items:
                break
            new_ids = 0
            for item in items:
                job_id = str(item.get("id") or "").strip()
                title = _strip_html(str(item.get("positionName") or "")).strip()
                if not job_id or not title or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                new_ids += 1
                if not _title_in_domain(title):  # 不按实习/校招过滤(社招也要)
                    continue
                try:
                    detail = transport.open_json("POST", _BYD_DETAIL_API, body={"id": job_id})
                except Exception:  # noqa: BLE001 — 详情失败按无正文处理
                    continue
                data = detail.get("data") or {}
                plain = _jd_text(data.get("tagDetailList"))
                if len(plain) < 80:  # 无可验证正文 → 不产出
                    continue
                url = (
                    "https://job.byd.com/portal/pc/#/skiller/"
                    f"skillerPositionDetails?id={url_parse.quote(job_id)}"
                )
                if url in seen:
                    continue
                seen.add(url)
                city = str(item.get("city") or data.get("city") or "").strip()
                jobs.append(
                    DiscoveredJob(
                        url=url,
                        title=title,
                        company="比亚迪",
                        location=city,
                        jd_text=plain,
                        source="byd",
                    )
                )
                if len(jobs) >= limit:
                    break
            if new_ids == 0:
                break  # 服务端忽略 pageSize 全量返回时避免重复翻页
            page_num += 1
    return jobs


# ── 吉利(阶段 3A-2 第二批,agent 实测冒烟通过)──
_GEELY_LIST_API = "https://campus.geely.com/api/outer/ats-apply/website/jobs/v2"
_GEELY_DETAIL_API = "https://campus.geely.com/api/outer/ats-apply/website/job"
_GEELY_SITE_ID = "78436"
_GEELY_JOB_URL_TMPL = "https://campus.geely.com/campus-recruitment/geely/78436?locale=zh-CN#/job/{id}"


def _geely_open_envelope(transport, url: str, body: dict) -> dict | None:
    """POST 并解 Moka AES 信封(密钥 necromancer 随响应自带,IV 为前端公开常量)。

    响应不是信封(被网关改写/接口变更)时返回 None,由调用方按"无数据"处理。
    """
    payload = transport.open_json("POST", url, body=body)
    envelope = payload if "necromancer" in payload else (payload.get("data") or {})
    if "necromancer" not in envelope:
        return None
    return decrypt_moka_envelope(envelope["data"], envelope["necromancer"])


def _geely_job_description(transport, job_id: str) -> str:
    """逐条调详情接口取 jobDescription(HTML → 纯文本),失败按无正文处理。

    解密后 job 字段直接在 data 下(campus.geely.com 实测),兼容 Moka 通用
    的 data.job 包一层形态。
    """
    try:
        decrypted = _geely_open_envelope(
            transport,
            _GEELY_DETAIL_API,
            body={
                "orgId": "geely",
                "siteId": _GEELY_SITE_ID,
                "jobId": job_id,
                "isInviteResume": True,
                "locale": "zh-CN",
            },
        )
    except Exception:  # noqa: BLE001 — 详情失败按"无正文"处理,单岗失败不拖垮整站
        return ""
    if not decrypted:
        return ""
    data = decrypted.get("data") or {}
    job = data.get("job") if isinstance(data.get("job"), dict) else data
    return _strip_html(str(job.get("jobDescription") or ""))


def _geely_location(item: dict) -> str:
    """locations[].cityName 优先,缺城市回退省份/国家(国家=中国视为无信息)。"""
    parts: list[str] = []
    for loc in item.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        part = str(
            loc.get("cityName") or loc.get("provinceName")
            or ("" if loc.get("country") == "中国" else loc.get("country") or "")
        ).strip()
        if part:
            parts.append(part)
    return "、".join(dict.fromkeys(parts))


async def _geely_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    """吉利(Moka SaaS 校招站 campus.geely.com,org=geely/site=78436)。

    列表接口只给 id/title/locations 等(无 JD 正文),逐条调详情接口补
    jobDescription;解密失败或正文 <80 字符的岗位跳过(诚实原则)。社招/
    实习均收,只按 _title_in_domain 过滤标题域词。
    """
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    offset = 0
    while len(jobs) < limit and (offset // 50) < 15:  # 15 页封顶,防失控
        decrypted = _geely_open_envelope(
            transport,
            _GEELY_LIST_API,
            body={
                "orgId": "geely",
                "siteId": _GEELY_SITE_ID,
                "limit": 50,
                "offset": offset,
                "site": "recruitment_web",
            },
        )
        items = ((decrypted or {}).get("data") or {}).get("jobs") or []
        if not items:
            break
        for item in items:
            title = str(item.get("title") or "").strip()
            job_id = str(item.get("id") or "").strip()
            if not title or not job_id or not _title_in_domain(title):
                continue
            url = _GEELY_JOB_URL_TMPL.format(id=job_id)
            if url in seen:
                continue
            seen.add(url)
            plain = _geely_job_description(transport, job_id)
            if len(plain) < 80:  # 无可验证正文的岗位不进管线
                continue
            jobs.append(
                DiscoveredJob(
                    url=url,
                    title=title,
                    company="吉利",
                    location=_geely_location(item),
                    jd_text=plain,
                    source="geely",
                )
            )
            if len(jobs) >= limit:
                break
        offset += 50
    return jobs


_BING_SERP_URL = "https://cn.bing.com/search?q={query}&mkt=zh-CN&count=30"


async def _bing_serp_lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
    """阶段 3B(实验性):Bing SERP 岗位链接发现。

    定位:发现官方列表源之外的新站点/新链接;时效受搜索引擎收录限制,
    无正文来源的域在内容链会被诚实过滤。DiscoveredJob.title 留空
    (标题由后续抽取/官方 API 提供),jd_text 为空走兜底链。
    """
    from urllib.parse import quote_plus

    from web_task_agent.search_discovery import DEFAULT_SERP_QUERIES, discover_job_links

    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for query in DEFAULT_SERP_QUERIES:
        if len(jobs) >= limit:
            break
        try:
            html = transport.fetch_html(_BING_SERP_URL.format(query=quote_plus(query)))
        except Exception:  # noqa: BLE001 — 单查询失败不影响其余
            continue
        for url in discover_job_links(html, base_url=_BING_SERP_URL):
            if url in seen:
                continue
            seen.add(url)
            jobs.append(
                DiscoveredJob(
                    url=url,
                    title="",
                    company="",
                    location="",
                    jd_text="",
                    source="bing-serp",
                )
            )
            if len(jobs) >= limit:
                break
    return jobs


_SPECS: dict[str, object] = {
    "tencent-campus": _tencent_campus_lister,
    "meituan": _meituan_lister,
    "unitree": _unitree_lister,
    "xiaomi": _xiaomi_lister,
    "netease": _netease_lister,
    "xiaohongshu": _xiaohongshu_lister,
    "mihoyo": _mihoyo_lister,
    "baidu": _baidu_lister,
    "bing-serp": _bing_serp_lister,
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

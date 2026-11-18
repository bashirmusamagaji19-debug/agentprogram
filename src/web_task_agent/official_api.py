"""官方招聘 API 直取 JD 正文（阶段 1 兜底层）。

背景（2026-08-31 实测，见 docs/work-log/2026-08-31-chinese-jd-smoke.md 续篇）：
- 聚合仓库（job-radar）给出的官方详情页（zhaopin.meituan.com、careers.tencent.com
  等）全部是 JS 渲染 SPA，HttpPageLoader 返回空正文（5/5 empty_page）
- 但大厂官方招聘 API 是公开 JSON 接口，无鉴权，返回完整 JD 字段：
  - 腾讯社招: GET /tencentcareer/api/post/ByPostId?postId=…&language=zh-cn
      （PostId 从详情页 URL ?postId= 提取；已下线岗位返回 Code=500/E1005，
       表现为 HTTPError 500——诚实走失败分类，不伪装）
  - 腾讯校招: GET https://join.qq.com/api/v1/jobDetails/getJobDetailsByPostId?postId=…
      （青云计划等校招/实习岗在 careers 社招库必 E1005，但 postId 命名空间互通——
       阶段 6 发现，覆盖聚合源 91% 的腾讯岗位池）
  - 美团: POST /api/official/job/getJobList（body 指定 keywords/jobTypeList），
      列表响应自带 jobDuty/jobRequirement——按 jobUnionId 过滤出目标岗位

OfficialApiContentFetcher.fetch(url) 返回 (content, title, company)；
不支持的域名抛 UnsupportedOfficialApiError，调用方回退到 aggregator jd_text。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from time import monotonic
from urllib import parse as url_parse
from urllib import request as url_request
from urllib.error import URLError

from web_task_agent.baidu_recruitment import (
    BAIDU_DETAIL_PATH,
    BAIDU_DETAIL_TYPE_SOURCE,
    BAIDU_HOST,
    baidu_job_text,
    parse_baidu_page,
)
from web_task_agent.browser import PageEmptyError, PageHttpError, PageTimeoutError

# 腾讯校招库详情接口（join.qq.com，公开无鉴权）
_TENCENT_CAMPUS_DETAIL_API = "https://join.qq.com/api/v1/jobDetails/getJobDetailsByPostId"

_HUAWEI_POSITION_API = (
    "https://apigw-dgg-b0.huawei.com/api/apig/channelhw/recruitmentPosition/pub/"
)
_HUAWEI_APP_PARAM = "?X-HW-ID=app_000000035886"
_HUAWEI_INTENT_API = _HUAWEI_POSITION_API + "getPositionIntentionList" + _HUAWEI_APP_PARAM
_HUAWEI_DETAIL_API = _HUAWEI_POSITION_API + "getRecruitmentPositionDetail" + _HUAWEI_APP_PARAM
_HUAWEI_INTERNSHIP_LIST_URL = (
    "https://career.huawei.com/cn/campus-recruitment-job-list?recruitmentType=INTERN"
)
_HUAWEI_DETAIL_URL = "https://career.huawei.com/cn/job-details"
_HUAWEI_HEADERS = {
    "Content-Type": "application/json",
    "x-jalor-tenantAlias": "hcm",
    "x-language": "zh_CN",
    "x-Referer": "https://career.huawei.com/cn",
    "x-alb-gray": "prod",
    "Referer": "https://career.huawei.com/cn/campus-recruitment-job-list",
}


def _huawei_direction_jd(direction: dict) -> str:
    from web_task_agent.official_list_families import _strip_html

    duties = _strip_html(str(direction.get("jobResponsibilities") or ""))
    requirements = _strip_html(str(direction.get("jobDemand") or ""))
    if not duties or not requirements or "请您详见岗位意向" in duties + requirements:
        return ""
    name = str(direction.get("positionIntention") or "").strip()
    direction_label = f"岗位意向：{name}\n" if name else ""
    return f"岗位职责：\n{direction_label}{duties}\n任职要求：\n{requirements}"


@dataclass(frozen=True)
class _FeishuDetailPortal:
    company: str
    website_path: str


# Exact hosts/routes registered in official_list_source.py. The four sites are
# backed by native-detail-probes captured on 2026-10-25; this deliberately does
# not infer companies or API support for other Feishu hosts.
_FEISHU_DETAIL_PORTALS = {
    "xiaopeng.jobs.feishu.cn": _FeishuDetailPortal("小鹏汽车", "campus"),
    "agirobot.jobs.feishu.cn": _FeishuDetailPortal("智元机器人", "internrecruitment"),
    "vrfi1sk8a0.jobs.feishu.cn": _FeishuDetailPortal("MiniMax", "index"),
    "shengshu.jobs.feishu.cn": _FeishuDetailPortal("生数科技", "index"),
    "nio.jobs.feishu.cn": _FeishuDetailPortal("蔚来", "index"),
}

_FEISHU_CHANNEL_DETAIL_PATH = re.compile(
    r"^/(?P<website_path>[^/]+)/position/(?P<post_id>\d+)/detail/?$"
)
_FEISHU_INDEX_DETAIL_PATH = re.compile(
    r"^/index/position/(?:detail/)?(?P<post_id>\d+)/?$"
)


class UnsupportedOfficialApiError(RuntimeError):
    """URL 域名没有对应的官方 API 适配器。"""


class OfficialApiUnavailableError(RuntimeError):
    """官方 API 存在但拿不到该岗位（已下线 / 风控 / 响应格式变化）。"""


@dataclass(frozen=True)
class OfficialApiContent:
    content: str
    title: str = ""
    company: str = ""
    # 内容实际来源的可浏览详情页 URL。腾讯实习岗（青云计划）postId 在社招站
    # 页面 404（用户实录），但内容来自 join.qq.com 校招库——此时必须把展示
    # 链接改写为校招详情页，否则"内容验证通过、链接打不开"。
    canonical_url: str = ""
    internship_evidence_source: str = ""
    internship_evidence_value: str = ""
    content_origin: str = "official-api"


class OfficialApiContentFetcher:
    """按 URL 域名路由到官方招聘 API，直取 JD 正文。

    独立 HTTP 客户端（urllib），与 HttpPageLoader 一致的超时/异常语义。
    """

    def __init__(self, timeout_seconds: int = 20) -> None:
        self.timeout_seconds = timeout_seconds
        self.deadline: float | None = None

    async def fetch(self, url: str) -> OfficialApiContent:
        return await asyncio.to_thread(self._fetch_sync, url)

    def _fetch_sync(self, url: str) -> OfficialApiContent:
        parsed_url = url_parse.urlsplit(url)
        host = (parsed_url.hostname or "").lower()
        if host == "career.huawei.com" and parsed_url.path == "/cn/job-details":
            if "positionIntentionId" in url_parse.parse_qs(
                parsed_url.query, keep_blank_values=True
            ):
                return self._fetch_huawei_direction(url)
        if host == "join.qq.com":
            post_ids = [value for key, value in url_parse.parse_qsl(parsed_url.query)
                        if key.casefold() == "postid"]
            if not post_ids or not post_ids[0].strip():
                raise OfficialApiUnavailableError("tencent campus URL without postId")
            return self._fetch_tencent_campus(post_ids[0].strip())
        if host == "xiaomi.jobs.f.mioffice.cn":
            return self._fetch_xiaomi(url)
        if host in _FEISHU_DETAIL_PORTALS:
            return self._fetch_registered_feishu(url)
        if host == "careers.tencent.com" or host.endswith(".careers.tencent.com"):
            return self._fetch_tencent(url)
        if host == "zhaopin.meituan.com" or host.endswith(".zhaopin.meituan.com"):
            return self._fetch_meituan(url)
        if host == BAIDU_HOST:
            match = BAIDU_DETAIL_PATH.fullmatch(url_parse.urlsplit(url).path)
            if match:
                return self._fetch_baidu_detail(*match.groups())
        raise UnsupportedOfficialApiError(f"no official API adapter for host: {host}")

    def _fetch_huawei_direction(self, url: str) -> OfficialApiContent:
        query = url_parse.parse_qs(url_parse.urlsplit(url).query, keep_blank_values=True)
        ad_ids = query.get("advertisementId", [])
        direction_ids = query.get("positionIntentionId", [])
        if any(len(ids) != 1 or not re.fullmatch(r"[0-9]+", ids[0]) for ids in (ad_ids, direction_ids)):
            raise OfficialApiUnavailableError("Huawei detail lacks a valid ad or direction ID")
        ad_id, direction_id = ad_ids[0], direction_ids[0]
        canonical_url = f"{_HUAWEI_DETAIL_URL}?advertisementId={ad_id}&positionIntentionId={direction_id}"

        def post(api_url: str, body: dict) -> dict:
            return self._open_json(url_request.Request(
                api_url,
                data=json.dumps(body).encode("utf-8"),
                headers={**_HUAWEI_HEADERS, "Referer": canonical_url, "User-Agent": "Mozilla/5.0"},
            ))

        payload = post(_HUAWEI_DETAIL_API, {"advertisementId": ad_id})
        data = payload.get("data")
        if (payload.get("status") != "SUCCESS" or not isinstance(data, dict)
                or str(data.get("advertisementId")) != ad_id):
            raise OfficialApiUnavailableError("Huawei detail unavailable or ad ID mismatch")
        title = str(data.get("jobname") or "").strip()
        job_id = data.get("jobId")
        if not title or not job_id or data.get("recruitType") != "CR" or data.get("scenarioName") != "实习生":
            raise OfficialApiUnavailableError("Huawei detail lacks confirmed internship identity")
        payload = post(_HUAWEI_INTENT_API, {"jobId": job_id})
        directions = payload.get("data")
        if payload.get("status") != "SUCCESS" or not isinstance(directions, list):
            raise OfficialApiUnavailableError("Huawei internship directions unavailable")
        direction = next((
            item for item in directions
            if isinstance(item, dict) and str(item.get("jobId")) == str(job_id)
            and str(item.get("positionIntentionId")) == direction_id
        ), None)
        if direction is None:
            raise OfficialApiUnavailableError("Huawei detail direction ID mismatch")
        jd_text = _huawei_direction_jd(direction)
        if len(jd_text) < 80:
            raise OfficialApiUnavailableError("Huawei internship direction has no sufficient JD")
        location = str(direction.get("jobPlaceName") or "").strip()
        return OfficialApiContent(
            content=f"公司：华为\n工作地点：{location}\n{jd_text}",
            title=title,
            company="华为",
            canonical_url=canonical_url,
            internship_evidence_source="huawei:detail:scenarioName",
            internship_evidence_value=data["scenarioName"],
        )

    def _fetch_xiaomi(self, url: str) -> OfficialApiContent:
        # The existing Xiaomi list adapter returns these native Feishu detail URLs.
        path = url_parse.urlsplit(url).path
        match = re.fullmatch(r"/(internship|campus|social|topintern)/position/(\d+)/detail/?", path)
        if match is None:
            raise OfficialApiUnavailableError("xiaomi URL without a supported job post ID")
        site, post_id = match.groups()
        req = url_request.Request(
            f"https://xiaomi.jobs.f.mioffice.cn/api/v1/job/posts/{post_id}",
            headers={"website-path": site, "Referer": url, "User-Agent": "Mozilla/5.0"},
        )
        payload = self._open_json(req)
        data = (payload.get("data") or {}).get("job_post_detail") or {}
        if payload.get("code") != 0 or str(data.get("id")) != post_id:
            raise OfficialApiUnavailableError("xiaomi detail unavailable or identity mismatch")
        if data.get("channel_online_status") == 0:
            from web_task_agent.page_reliability import PageRemovedError
            raise PageRemovedError("xiaomi position is no longer online")
        title = str(data.get("title") or "").strip()
        duties = str(data.get("description") or "").strip()
        requirements = str(data.get("requirement") or "").strip()
        if not title or not (duties or requirements):
            raise OfficialApiUnavailableError("xiaomi detail has no title or JD")
        cities = [str(city.get("name") or "").strip() for city in data.get("city_list", [])]
        city = "、".join(dict.fromkeys(filter(None, cities))) or str(
            (data.get("city_info") or {}).get("name") or "")
        return OfficialApiContent(
            title=title, company="小米", canonical_url=url_parse.urlunsplit(
                ("https", "xiaomi.jobs.f.mioffice.cn", path.rstrip("/"), "", "")),
            content=f"公司：小米\n工作地点：{city}\n岗位职责：\n{duties}\n任职要求：\n{requirements}",
            internship_evidence_source="xiaomi:detail:recruit_type.id",
            internship_evidence_value=str((data.get("recruit_type") or {}).get("id") or ""),
        )

    def _fetch_registered_feishu(self, url: str) -> OfficialApiContent:
        """Read an exact registered Feishu portal through its native detail API.

        The public detail URL is a client-rendered route and can return 404 when
        fetched directly. The same portal's API returns the authoritative post
        shape used by Xiaomi; ``website-path`` identifies the route namespace.
        """
        parsed = url_parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        portal = _FEISHU_DETAIL_PORTALS.get(host)
        if portal is None:
            raise UnsupportedOfficialApiError(f"no official API adapter for host: {host}")

        match = _FEISHU_CHANNEL_DETAIL_PATH.fullmatch(parsed.path)
        if match is not None and match.group("website_path") == portal.website_path:
            post_id = match.group("post_id")
        else:
            match = _FEISHU_INDEX_DETAIL_PATH.fullmatch(parsed.path)
            if match is None or portal.website_path != "index":
                raise OfficialApiUnavailableError("feishu URL without a registered detail route")
            post_id = match.group("post_id")

        req = url_request.Request(
            f"https://{host}/api/v1/job/posts/{post_id}",
            headers={
                "website-path": portal.website_path,
                "Referer": url,
                "User-Agent": "Mozilla/5.0",
            },
        )
        payload = self._open_json(req)
        raw_data = (payload.get("data") or {}).get("job_post_detail") or {}
        data = raw_data if isinstance(raw_data, dict) else {}
        if payload.get("code") != 0 or str(data.get("id") or "") != post_id:
            raise OfficialApiUnavailableError("feishu detail unavailable or identity mismatch")

        online_status = data.get("channel_online_status")
        if online_status in (0, "0"):
            from web_task_agent.page_reliability import PageRemovedError

            raise PageRemovedError("feishu position is no longer online")
        if online_status not in (1, "1"):
            raise OfficialApiUnavailableError("feishu detail online status missing or unrecognized")

        title = str(data.get("title") or "").strip()
        duties = str(data.get("description") or "").strip()
        requirements = str(data.get("requirement") or "").strip()
        if not title or not (duties or requirements):
            raise OfficialApiUnavailableError("feishu detail has no title or JD")
        cities = [
            str(city.get("name") or "").strip()
            for city in (data.get("city_list") or [])
            if isinstance(city, dict)
        ]
        city = "、".join(dict.fromkeys(filter(None, cities))) or str(
            (data.get("city_info") or {}).get("name") or ""
        )
        recruit_type = data.get("recruit_type") or {}
        recruit_type_name = str(recruit_type.get("name") or "").strip() if isinstance(recruit_type, dict) else ""
        canonical_url = url_parse.urlunsplit(
            (
                parsed.scheme or "https",
                parsed.netloc,
                f"/{portal.website_path}/position/{post_id}/detail",
                "",
                "",
            )
        )
        return OfficialApiContent(
            title=title,
            company=portal.company,
            canonical_url=canonical_url,
            content=(
                f"公司：{portal.company}\n工作地点：{city}\n"
                f"岗位职责：\n{duties}\n任职要求：\n{requirements}"
            ),
            internship_evidence_source="feishu:detail:recruit_type.name",
            internship_evidence_value=recruit_type_name,
        )

    def _fetch_baidu_detail(self, recruit_type: str, post_id: str) -> OfficialApiContent:
        from web_task_agent.page_reliability import PageRemovedError

        url = f"https://{BAIDU_HOST}/jobs/detail/{recruit_type}/{post_id}"
        timeout = self.timeout_seconds if self.deadline is None else min(
            self.timeout_seconds, self.deadline - monotonic()
        )
        if timeout <= 0:
            raise PageTimeoutError("baidu detail request deadline exceeded")
        request = url_request.Request(url)  # noqa: S310
        with url_request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            html = response.read().decode("utf-8")
        try:
            detail = parse_baidu_page(html).get("detailData")
        except ValueError as exc:
            raise PageEmptyError("baidu detail page has no job data") from exc
        if not isinstance(detail, dict) or detail.get("recruitType") != recruit_type:
            raise PageEmptyError("baidu detail has no matching recruitment type")
        item = detail.get("postInfo")
        if not isinstance(item, dict) or item.get("postId") != post_id:
            raise PageEmptyError("baidu detail has no matching job ID")
        if detail.get("isValid") is False:
            raise PageRemovedError("baidu position is no longer online")
        title = str(item.get("name") or "").strip()
        content = baidu_job_text(item)
        if detail.get("isValid") is not True or not title or not content:
            raise PageEmptyError("baidu detail has no verified title or job description")
        return OfficialApiContent(
            content=content,
            title=title,
            company="百度",
            canonical_url=url,
            internship_evidence_source=BAIDU_DETAIL_TYPE_SOURCE,
            internship_evidence_value=recruit_type,
            content_origin="http",
        )

    # ── 腾讯：详情页 URL ?postId=… → ByPostId 接口 ──────────────────

    def _fetch_tencent(self, url: str) -> OfficialApiContent:
        query = url_parse.parse_qs(url_parse.urlsplit(url).query)
        post_ids = query.get("postId") or []
        if not post_ids or not post_ids[0].strip():
            raise OfficialApiUnavailableError(f"tencent URL without postId: {url}")
        post_id = post_ids[0].strip()

        api_url = (
            "https://careers.tencent.com/tencentcareer/api/post/ByPostId"
            f"?postId={url_parse.quote(post_id)}&language=zh-cn"
        )
        try:
            payload = self._get_json(api_url, referer="https://careers.tencent.com/")
        except OfficialApiUnavailableError:
            # 社招库查不到（E1005）→ 大概率是校招/实习岗（青云计划等），
            # 落到 join.qq.com 校招库（postId 命名空间互通）
            return self._fetch_tencent_campus(post_id)
        code = payload.get("Code")
        data = payload.get("Data")
        if code != 200 or not isinstance(data, dict):
            # 已下线岗位返回 Code=500/E1005（HTTP 500 已被 _get_json 转 PageHttpError；
            # 这里兜住"HTTP 200 但 Code≠200"的变体）
            raise OfficialApiUnavailableError(
                f"tencent ByPostId returned code={code} for postId={post_id}"
            )

        title = str(data.get("RecruitPostName") or "").strip()
        company = str(data.get("ComName") or "腾讯").strip() or "腾讯"
        location = str(data.get("Location") or "").strip()
        # 输出标准标签行（"岗位职责："/"任职要求：" 分节），
        # 复合标签 "岗位职责/任职要求：" 规则抽取认不出 → 每岗强制 LLM 抽取（复现实录 #19）
        labeled = [f"公司：{company}"]
        if location:
            labeled.append(f"工作地点：{location}")
        for label, key in (("岗位职责", "Responsibility"), ("任职要求", "Requirement")):
            section = str(data.get(key) or "").strip()
            if section:
                labeled.append(f"{label}：\n{section}")
        content = "\n".join(labeled)
        if len(labeled) <= 1:
            raise OfficialApiUnavailableError(
                f"tencent ByPostId returned empty JD for postId={post_id}"
            )
        return OfficialApiContent(
            content=content,
            title=title,
            company=company,
        )

    # ── 腾讯校招库：join.qq.com（青云计划等 careers 查不到的岗）─────────

    def _fetch_tencent_campus(self, post_id: str) -> OfficialApiContent:
        """join.qq.com 校招库详情：topicDetail（课题）+ topicRequirement（要求）。

        careers ByPostId E1005 的岗大多在这里——两库 postId 互通。
        """
        api_url = f"{_TENCENT_CAMPUS_DETAIL_API}?postId={url_parse.quote(post_id)}"
        payload = self._get_json(api_url, referer="https://join.qq.com/")
        data = payload.get("data")
        if not isinstance(data, dict) or not data:
            raise OfficialApiUnavailableError(f"tencent campus detail empty for postId={post_id}")
        if str(data.get("postId") or post_id) != post_id:
            raise OfficialApiUnavailableError(f"tencent campus detail mismatched for postId={post_id}")

        title = str(data.get("title") or data.get("positionTitle") or "").strip()
        # 标准标签行（与社招/美团一致的格式约定，规则抽取直接命中——见 #19）
        labeled = ["公司：腾讯"]
        work_cities = "、".join(
            str(c).strip() for c in (data.get("workCityList") or []) if str(c).strip()
        )
        if work_cities:
            labeled.append(f"工作地点：{work_cities}")
        # 字段布局兼容:青云课题帖用 topicDetail/topicRequirement,
        # 2027 校招常规帖用 desc/request(实时列表发现时的实录)。
        # 优先 topic 字段,空则落 desc/request。
        for label, keys in (
            ("岗位职责", ("topicDetail", "desc")),
            ("任职要求", ("topicRequirement", "request")),
        ):
            section = next(
                (
                    str(data.get(key) or "").strip()
                    for key in keys
                    if str(data.get(key) or "").strip()
                ),
                "",
            )
            if section:
                labeled.append(f"{label}：\n{section}")
        # 守卫要求至少一个正文 section 非空：只有"公司+城市"的壳正文
        # （字段全 null/空，约 15 字符）不得进入管线（#27）
        if len(labeled) <= 2:
            raise OfficialApiUnavailableError(
                f"tencent campus detail has no JD content for postId={post_id}"
            )
        return OfficialApiContent(
            content="\n".join(labeled),
            title=title,
            company="腾讯",
            internship_evidence_source="tencent:detail:recruitType",
            internship_evidence_value=str(data.get("recruitType") or ""),
            canonical_url=(
                f"https://join.qq.com/post_detail.html?postId={url_parse.quote(post_id)}"
            ),
        )

    # ── 美团：列表接口按 jobUnionId 过滤（响应自带 jobDuty/jobRequirement）──

    def _fetch_meituan(self, url: str) -> OfficialApiContent:
        query = url_parse.parse_qs(url_parse.urlsplit(url).query)
        ids = query.get("jobUnionId") or []
        if not ids or not ids[0].strip():
            raise OfficialApiUnavailableError(f"meituan URL without jobUnionId: {url}")
        job_union_id = ids[0].strip()

        api_url = "https://zhaopin.meituan.com/api/official/job/getJobList"
        for page_no in range(1, 11):  # 实测实习岗集中在第 4-7 页，10 页封顶
            body = json.dumps(
                {
                    "page": {"pageNo": page_no, "pageSize": 20},
                    "jobShareType": "1",
                    "keywords": "",
                    "cityIdList": [],
                    "jobTypeList": [],
                }
            ).encode("utf-8")
            req = url_request.Request(
                api_url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Referer": "https://campus.meituan.com/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                },
            )
            payload = self._open_json(req)
            items = ((payload.get("data") or {}).get("list")) or []
            if not items:
                break
            for item in items:
                if str(item.get("jobUnionId") or "") != job_union_id:
                    continue
                return self._meituan_item_to_content(item)
        raise OfficialApiUnavailableError(
            f"meituan jobUnionId={job_union_id} not found in first 10 list pages"
        )

    def _meituan_item_to_content(self, item: dict) -> OfficialApiContent:
        title = str(item.get("name") or "").strip()
        cities = "、".join(
            str(c.get("name") or "").strip()
            for c in (item.get("cityList") or [])
            if isinstance(c, dict) and c.get("name")
        )
        company = "美团"
        # 标准标签行（与腾讯一致），保证规则抽取可命中（复现实录 #19）
        labeled = [f"公司：{company}"]
        if cities:
            labeled.append(f"工作地点：{cities}")
        for label, key in (("岗位职责", "jobDuty"), ("任职要求", "jobRequirement")):
            section = str(item.get(key) or "").strip()
            if section:
                labeled.append(f"{label}：\n{section}")
        content = "\n".join(labeled)
        if len(labeled) <= 1:
            raise OfficialApiUnavailableError(f"meituan item {title!r} has empty JD")
        return OfficialApiContent(content=content, title=title, company=company)

    # ── HTTP 基础设施：与 HttpPageLoader 一致的异常语义 ──────────────

    def _get_json(self, url: str, *, referer: str) -> dict:
        req = url_request.Request(
            url,
            headers={
                "Referer": referer,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            },
        )
        return self._open_json(req)

    def _open_json(self, req: url_request.Request) -> dict:
        try:
            timeout = self.timeout_seconds if self.deadline is None else min(
                self.timeout_seconds, self.deadline - monotonic())
            if timeout <= 0:
                raise PageTimeoutError("official API request deadline exceeded")
            with url_request.urlopen(req, timeout=timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, OSError)) or isinstance(exc, TimeoutError):
                raise PageTimeoutError(f"official API timeout: {req.full_url}") from exc
            status = getattr(exc, "code", None)
            if status is not None:
                # 腾讯已下线岗位返回 HTTP 500 + E1005 —— 归类为岗位不可用而非服务器错误
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:200]
                except Exception:  # noqa: BLE001
                    pass
                # 只认 E1005 业务错误码：Code:500 是腾讯通用错误壳，
                # 瞬时 500 也带它——用 Code 判定会把可重试错误误判为岗位下线
                # 并错误触发 join.qq.com 第二跳（#27）
                if "E1005" in body:
                    raise OfficialApiUnavailableError(
                        f"official API says post unavailable (E1005): {req.full_url}"
                    ) from exc
                raise PageHttpError(
                    f"official API HTTP error: {req.full_url}", status_code=status
                ) from exc
            raise PageTimeoutError(f"official API unreachable: {req.full_url}") from exc

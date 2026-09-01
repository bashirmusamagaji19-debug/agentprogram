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

import json
from dataclasses import dataclass
from urllib import parse as url_parse
from urllib import request as url_request
from urllib.error import URLError

from web_task_agent.browser import PageHttpError, PageTimeoutError

# 腾讯校招库详情接口（join.qq.com，公开无鉴权）
_TENCENT_CAMPUS_DETAIL_API = "https://join.qq.com/api/v1/jobDetails/getJobDetailsByPostId"


class UnsupportedOfficialApiError(RuntimeError):
    """URL 域名没有对应的官方 API 适配器。"""


class OfficialApiUnavailableError(RuntimeError):
    """官方 API 存在但拿不到该岗位（已下线 / 风控 / 响应格式变化）。"""


@dataclass(frozen=True)
class OfficialApiContent:
    content: str
    title: str = ""
    company: str = ""


class OfficialApiContentFetcher:
    """按 URL 域名路由到官方招聘 API，直取 JD 正文。

    独立 HTTP 客户端（urllib），与 HttpPageLoader 一致的超时/异常语义。
    """

    def __init__(self, timeout_seconds: int = 20) -> None:
        self.timeout_seconds = timeout_seconds

    async def fetch(self, url: str) -> OfficialApiContent:
        host = url_parse.urlsplit(url).netloc.lower()
        if host == "careers.tencent.com" or host.endswith(".careers.tencent.com"):
            return self._fetch_tencent(url)
        if host == "zhaopin.meituan.com" or host.endswith(".zhaopin.meituan.com"):
            return self._fetch_meituan(url)
        raise UnsupportedOfficialApiError(f"no official API adapter for host: {host}")

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
        api_url = (
            f"{_TENCENT_CAMPUS_DETAIL_API}?"
            f"postId={url_parse.quote(post_id)}"
        )
        payload = self._get_json(api_url, referer="https://join.qq.com/")
        data = payload.get("data")
        if not isinstance(data, dict) or not data:
            raise OfficialApiUnavailableError(
                f"tencent campus detail empty for postId={post_id}"
            )

        title = str(data.get("title") or "").strip()
        # 标准标签行（与社招/美团一致的格式约定，规则抽取直接命中——见 #19）
        labeled = ["公司：腾讯"]
        work_cities = "、".join(
            str(c).strip() for c in (data.get("workCityList") or []) if str(c).strip()
        )
        if work_cities:
            labeled.append(f"工作地点：{work_cities}")
        for label, key in (("岗位职责", "topicDetail"), ("任职要求", "topicRequirement")):
            section = str(data.get(key) or "").strip()
            if section:
                labeled.append(f"{label}：\n{section}")
        # 守卫要求至少一个 topic section 非空：只有"公司+城市"的壳正文
        # （topic 字段全 null，约 15 字符）不得进入管线（#27）
        if len(labeled) <= 2:
            raise OfficialApiUnavailableError(
                f"tencent campus detail has no JD content for postId={post_id}"
            )
        return OfficialApiContent(
            content="\n".join(labeled),
            title=title,
            company="腾讯",
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
            with url_request.urlopen(req, timeout=self.timeout_seconds) as response:  # noqa: S310
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

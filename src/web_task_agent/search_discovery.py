from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "ref",
    "source",
    "trk",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() != "a":
            return
        for name, value in attrs:
            if name.casefold() == "href" and value:
                self.links.append(str(value))


def discover_job_links(
    html: str,
    *,
    base_url: str,
    raw_links: list[str] | None = None,
) -> list[str]:
    parser = _LinkExtractor()
    parser.feed(html)
    candidates = [*parser.links, *(raw_links or [])]
    discovered: list[str] = []
    for candidate in candidates:
        normalized = _normalize_candidate(candidate, base_url=base_url)
        if normalized and _looks_like_job_url(normalized) and normalized not in discovered:
            discovered.append(normalized)
    return discovered


def _normalize_candidate(candidate: str, *, base_url: str) -> str | None:
    value = candidate.strip()
    if not value:
        return None
    absolute = urljoin(base_url, value)
    parsed = urlparse(absolute)

    if (
        parsed.hostname
        and parsed.hostname.casefold().endswith("google.com")
        and parsed.path == "/url"
    ):
        redirected = parse_qs(parsed.query).get("q", [])
        if not redirected:
            return None
        parsed = urlparse(redirected[0])

    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return None

    clean_query = [
        (key, value)
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if key.casefold() not in _TRACKING_KEYS and not key.casefold().startswith("utm_")
        for value in values
    ]
    return urlunparse(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path.rstrip("/") or "/",
            "",
            urlencode(clean_query),
            "",
        )
    )


def _looks_like_job_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    if "google." in host or "bing.com" in host:
        return False
    known_hosts = (
        # 美国站点(项目早期口径)
        "greenhouse.io",
        "lever.co",
        "ashbyhq.com",
        "myworkdayjobs.com",
        "smartrecruiters.com",
        # 国内站点(阶段 3B:逆向已覆盖的官方招聘域,见
        # docs/results/official-api-specs-2026-09-08.json)
        "tencent.com",
        "meituan.com",
        "hr.163.com",
        "campus.jd.com",
        "talent.baidu.com",
        "xiaohongshu.com",
        "mihoyo.com",
        "openout.mihoyo.com",
        "career.huawei.com",
        "pddglobalhr.com",
        "careers.ctrip.com",
        "nio.cn",
        "lixiang.com",
        "xiaopeng.jobs.feishu.cn",
        "jobs.feishu.cn",
        "f.mioffice.cn",
        "job.byd.com",
        "campus.geely.com",
        "hr.xiaomi.com",
        "api.unitree.com",
        "www.unitree.com",
        "agirobot.jobs.feishu.cn",
        "mokahr.com",
        "zhiye.com",
        "nowcoder.com",
        "shixiseng.com",
    )
    if any(host.endswith(known) for known in known_hosts):
        return True
    if host.startswith(("jobs.", "careers.", "hr.", "campus.", "talent.", "zhaopin.")):
        return True
    return any(
        marker in path
        for marker in (
            "/jobs/",
            "/job/",
            "/careers/",
            "/positions/",
            "/position",
            "/jobdetail",
            "/job-detail",
            "/jobdesc",
            "/internship",
            "/campus/",
        )
    )


# 阶段 3B 默认查询(实验性):SERP 时效受搜索引擎收录限制,
# 价值在发现新站点与官方列表源之外的补充,不保证实时。
DEFAULT_SERP_QUERIES = (
    "site:careers.tencent.com 大模型 实习",
    "site:campus.kuaishou.com 大模型",
    "大模型 算法 实习 招聘",
    "具身智能 机器人 招聘",
)

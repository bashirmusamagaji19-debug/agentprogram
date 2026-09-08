"""SaaS 招聘平台家族适配器(阶段 3A-2,接口规格见 docs/results/official-api-specs-2026-09-08.json)。

三家 SaaS 承载了大量厂商的招聘站,按平台写一个适配器 = 收录一批厂商:
- 飞书招聘(feishu.cn / f.mioffice.cn):小鹏、智元(与字节社招接口同构)
- Moka(mokahr.com):银河通用、星动纪元、傅利叶 — 响应是 AES-CBC 信封,
  密钥(necromancer)随响应自带、IV 为前端公开常量,解密逻辑来自站点前端,
  属公开协议而非破解
- 北森 zhiye:优必选

所有适配器输出与腾讯/美团一致的 DiscoveredJob;JD 列表自带的直接落 jd_text
(内容门槛在 AggregatorPageLoader 统一执行),无正文的岗位跳过 —— 诚实原则:
不产出没有可验证正文的岗位。
"""

from __future__ import annotations

import base64
import json
import re
from urllib import parse as url_parse

from web_task_agent.job_sources import AI_TITLE_KEYWORDS, DiscoveredJob

_PAGE_CAP = 15  # 每家翻页上限,防失控


def _title_hits_ai(title: str) -> bool:
    return any(keyword in title for keyword in AI_TITLE_KEYWORDS)


# ── 飞书招聘家族 ────────────────────────────────────────────────────


def make_feishu_hire_lister(
    *,
    api_url: str,
    url_tmpl: str,
    extra_body: dict | None = None,
    require_intern_type: bool = False,
):
    """飞书招聘 POST /api/v1/search/job/posts 适配器工厂。

    响应 code=0,data.job_post_list[] 自带 description/requirement 全文。
    require_intern_type: recruit_type.name 为"实习"才收(小米飞书站区分
    校招/实习/社招;小鹏校招站/智元整站收)。
    """

    async def lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
        jobs: list[DiscoveredJob] = []
        offset = 0
        while len(jobs) < limit and (offset // 100) < _PAGE_CAP:
            body = {"keyword": "", "limit": 100, "offset": offset}
            body.update(extra_body or {})
            payload = transport.open_json("POST", api_url, body=body)
            posts = (payload.get("data") or {}).get("job_post_list") or []
            if not posts:
                break
            for item in posts:
                title = str(item.get("title") or "").strip()
                recruit_type = str(((item.get("recruit_type") or {}) or {}).get("name") or "")
                if require_intern_type and "实习" not in recruit_type:
                    continue
                if not _title_hits_ai(title):
                    continue
                description = str(item.get("description") or "").strip()
                requirement = str(item.get("requirement") or "").strip()
                if not (description or requirement):
                    continue
                job_id = str(item.get("id") or "").strip()
                if not job_id:
                    continue
                city = str(((item.get("city_info") or {}) or {}).get("name") or "")
                city_list = [
                    str(c.get("name") or "")
                    for c in (item.get("city_list") or [])
                    if isinstance(c, dict) and c.get("name")
                ]
                location = "、".join(dict.fromkeys([city, *city_list])) or str(
                    item.get("sub_title") or ""
                ).strip()
                jobs.append(
                    DiscoveredJob(
                        url=url_tmpl.format(id=job_id),
                        title=title,
                        company=str(item.get("company") or "").strip() or _company_of(url_tmpl),
                        location=location,
                        jd_text=(
                            f"岗位职责：\n{description}\n任职要求：\n{requirement}".strip()
                        ),
                        source="feishu-hire",
                    )
                )
                if len(jobs) >= limit:
                    break
            offset += 100
        return jobs

    return lister


def _company_of(url_tmpl: str) -> str:
    host = url_parse.urlsplit(url_tmpl).netloc
    return host.split(".")[0]


# ── Moka 家族(AES-CBC 信封) ───────────────────────────────────────

# IV 为 Moka 前端公开常量(各站一致,来自站点 JS,密钥本身随响应自带)
# Moka AES-CBC IV:站点前端公开配置(mokahr 页面内嵌 TurboApply.data.aesIv),
# 解密函数 we() 与密钥(necromancer,响应自带)一起构成公开协议,非破解。
_MOKA_IV_TEXT = "de7c21ed8d6f50fe"  # 16 字符 UTF-8(TurboApply.data.aesIv,公开配置)


def _moka_iv_bytes() -> bytes:
    text = _MOKA_IV_TEXT
    if len(text) == 32:
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    return text.encode("utf-8")


def decrypt_moka_envelope(data: str, necromancer: str) -> dict:
    """解 Moka AES-128-CBC 信封:{"data": base64, "necromancer": 16字符key}。"""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    cipher = AES.new(necromancer.encode("utf-8"), AES.MODE_CBC, _moka_iv_bytes())
    plaintext = unpad(cipher.decrypt(base64.b64decode(data)), AES.block_size)
    return json.loads(plaintext.decode("utf-8"))


def make_moka_lister(
    *,
    api_url: str,
    org_id: str,
    site_id: int,
    url_tmpl: str,
    company: str,
    paging: str = "offset",  # moka 站点分页风格:offset(limit/offset) 或 page(pageNo/pageSize)
    detail_fetch: bool = False,  # 列表不带 JD 时逐条调详情接口补正文
    require_intern_words: bool = True,
):
    """Moka POST /api/outer/ats-apply/website/jobs/v2 适配器工厂。

    响应为 AES 信封(密钥随响应自带)。列表解密后 jobs[].jobDescription(HTML)
    校招站通常自带;空 JD 且 detail_fetch=True 时调
    /api/outer/ats-apply/website/job 补正文,仍为空则跳过 —— 无可验证正文
    的岗位不进管线。HTML 标签剥离后落 jd_text。
    """

    async def lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
        jobs: list[DiscoveredJob] = []
        page = 1
        offset = 0
        for _ in range(_PAGE_CAP):
            if paging == "page":
                body = {
                    "pageNo": page, "pageSize": 10, "orgId": org_id,
                    "siteId": site_id, "keyword": "", "locale": "zh-CN",
                }
                page += 1
            else:
                body = {
                    "orgId": org_id, "siteId": site_id, "limit": 50,
                    "offset": offset, "needStat": True, "locale": "zh-CN",
                }
                offset += 50
            payload = transport.open_json("POST", api_url, body=body)
            envelope = payload if "necromancer" in payload else (payload.get("data") or {})
            if "necromancer" not in envelope:
                break
            decrypted = decrypt_moka_envelope(envelope["data"], envelope["necromancer"])
            items = (decrypted.get("data") or {}).get("jobs") or []
            if not items:
                break
            for item in items:
                title = str(item.get("title") or item.get("jobName") or "").strip()
                if not title or not _title_hits_ai(title):
                    continue
                if require_intern_words and not any(
                    w in title for w in ("实习", "校招", "应届")
                ):
                    continue
                plain = _strip_html(str(item.get("jobDescription") or ""))
                job_id = str(item.get("id") or "").strip()
                if len(plain) < 80 and detail_fetch and job_id:
                    try:
                        detail = transport.open_json(
                            "POST",
                            _moka_detail_url(api_url),
                            body={
                                "siteId": site_id, "orgId": org_id,
                                "jobId": job_id, "locale": "zh-CN",
                            },
                        )
                        detail_envelope = (
                            detail if "necromancer" in detail else (detail.get("data") or {})
                        )
                        if "necromancer" in detail_envelope:
                            detail_data = decrypt_moka_envelope(
                                detail_envelope["data"], detail_envelope["necromancer"]
                            )
                            job = (detail_data.get("data") or {}).get("job") or {}
                            plain = _strip_html(str(job.get("jobDescription") or ""))
                    except Exception:  # noqa: BLE001 — 详情失败按"无正文"处理,不让单岗失败拖垮整站
                        plain = ""
                if len(plain) < 80:  # 无可验证正文的岗位不进管线
                    continue
                jobs.append(
                    DiscoveredJob(
                        url=url_tmpl.format(id=job_id),
                        title=title,
                        company=company,
                        location=str(
                            item.get("locationNames") or item.get("cityName") or ""
                        ).strip(),
                        jd_text=plain,
                        source="moka",
                    )
                )
                if len(jobs) >= limit:
                    break
            if len(jobs) >= limit:
                break
        return jobs

    return lister


def _moka_detail_url(list_api_url: str) -> str:
    return list_api_url.rsplit("/jobs/", 1)[0] + "/job"


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


# ── 北森 zhiye 家族 ────────────────────────────────────────────────


def make_zhiye_lister(
    *,
    api_url: str,
    portal_id: str,
    url_tmpl: str,
    company: str,
):
    """北森 POST /api/Jobad/GetJobAdPageList:Code=200,Data[] 自带 Duty/Require。"""

    async def lister(transport, limit: int) -> list[DiscoveredJob]:  # noqa: ANN001
        jobs: list[DiscoveredJob] = []
        page_index = 0
        while len(jobs) < limit and page_index < _PAGE_CAP:
            payload = transport.open_json(
                "POST",
                api_url,
                body={
                    "PageIndex": page_index,
                    "PageSize": 100,
                    "KeyWords": "",
                    "SpecialType": 0,
                    "PortalId": portal_id,
                },
            )
            items = payload.get("Data") or []
            if not items:
                break
            for item in items:
                title = str(item.get("JobAdName") or "").strip()
                if not title or not _title_hits_ai(title):
                    continue
                duty = str(item.get("Duty") or "").strip()
                require = str(item.get("Require") or "").strip()
                plain = _strip_html(f"岗位职责：\n{duty}\n任职要求：\n{require}".strip())
                if len(plain) < 80:
                    continue
                job_ad_id = str(item.get("JobAdId") or "").strip()
                jobs.append(
                    DiscoveredJob(
                        url=url_tmpl.format(id=job_ad_id),
                        title=title,
                        company=company,
                        location=str(item.get("WorkLocation") or "").strip(),
                        jd_text=plain,
                        source="zhiye",
                    )
                )
                if len(jobs) >= limit:
                    break
            page_index += 1
        return jobs

    return lister

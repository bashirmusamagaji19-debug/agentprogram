from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from web_task_agent.browser import MAX_JOB_RENDER_WAIT_SECONDS, BrowserUseClient, HttpPageLoader

RECEIPT_KEYS = frozenset(
    {
        "status",
        "checked_at",
        "run_id",
        "requested_url",
        "final_url",
        "method",
        "reason",
        "evidence_sha256",
        "title_matched",
        "jd_matched",
        "active",
    }
)

_STATUS_OFFLINE_PATTERNS = (
    re.compile(
        r"(?:该|此|本)\s*(?:职位|岗位)\s*(?:已|已经|目前)?\s*"
        r"(?:下线|关闭|停止招聘|停止接收(?:申请|简历)|暂停招聘|不再招聘|招满|失效)"
        r"(?!\s*(?:后|之后|时|前))",
        re.I,
    ),
    re.compile(
        r"(?:招聘|职位|岗位)\s*(?:已|已经)\s*(?:结束|关闭|过期)"
        r"(?!\s*(?:后|之后|时|前))",
        re.I,
    ),
    re.compile(r"(?:该|此)?\s*(?:职位|岗位)\s*不存在", re.I),
    re.compile(
        r"\bthis\s+(?:job|position|role|vacancy)\s+(?:is|has\s+been)\s+"
        r"(?:closed|filled|expired|removed|no\s+longer\s+available)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:job|position|role|vacancy)\s+(?:is\s+)?no\s+longer\s+"
        r"(?:available|accepting\s+applications)\b",
        re.I,
    ),
    re.compile(
        r"\bapplications?\s+(?:for\s+(?:this|the)\s+(?:job|position|role))?\s+"
        r"(?:are|is|have\s+been)\s+(?:closed|ended|no\s+longer\s+accepted)\b",
        re.I,
    ),
    re.compile(r"\b(?:job|position)\s+posting\s+(?:has\s+)?expired\b", re.I),
    re.compile(r"\b(?:job|position)\s+not\s+found\b", re.I),
    re.compile(r"(?m)^\s*(?:job|position)\s+(?:closed|filled|expired)\s*$", re.I),
)

_LOGIN_OR_CHALLENGE_PATTERNS = (
    re.compile(r"\b(?:sign\s+in|log\s+in)\s+to\s+(?:continue|view|apply)\b", re.I),
    re.compile(r"\bplease\s+(?:sign\s+in|log\s+in)\b", re.I),
    re.compile(r"\bverify\s+you\s+are\s+human\b", re.I),
    re.compile(r"\b(?:captcha|security\s+check|access\s+denied)\b", re.I),
    re.compile(r"请(?:先)?登录|登录后(?:查看|继续|申请)|登录\s*/\s*注册", re.I),
    re.compile(r"人机验证|安全验证|访问受限", re.I),
)

_SHELL_PATTERNS = (
    re.compile(r"^\s*(?:loading|please\s+wait|加载中|\.\.\.)[.!。\s]*$", re.I),
    re.compile(r"\benable\s+javascript\b", re.I),
    re.compile(r"请启用\s*javascript", re.I),
)

_LIST_PAGE_PATTERNS = (
    re.compile(r"\b(?:search\s+results|all\s+(?:jobs|positions)|job\s+results)\b", re.I),
    re.compile(r"全部职位|搜索结果|职位列表|岗位列表", re.I),
)

_APPLY_TEXT_PATTERN = re.compile(
    r"(?:apply(?:\s+now|\s+for\s+(?:this\s+)?(?:job|position|role))?"
    r"|submit\s+(?:an\s+)?application|投递(?:简历)?|立即申请|申请(?:职位|岗位)?|应聘)",
    re.I,
)

_NATIVE_ID_KEYS = re.compile(
    r"^(?:id|job[_-]?id|job[_-]?union[_-]?id|job[_-]?post[_-]?id|"
    r"position[_-]?id|position[_-]?code|posting[_-]?id|requisition[_-]?id|"
    r"requisition[_-]?code|vacancy[_-]?id|gh[_-]?jid|post[_-]?id|"
    r"post[_-]?code|post[_-]?no|job[_-]?no|job[_-]?code|job[_-]?seq(?:[_-]?no)?|"
    r"external[_-]?id|recruit[_-]?id|opportunity[_-]?id|code)$",
    re.I,
)
_STOP_PATH_PARTS = {
    "about", "apply", "career", "careers", "detail", "en", "home", "index", "job",
    "jobs", "position", "positions", "role", "roles", "search", "view", "zh", "cn",
}
_TRACKING_QUERY_KEYS = {
    "dclid", "fbclid", "gclid", "mc_cid", "mc_eid", "msclkid", "ref", "referrer",
    "source", "yclid",
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class JobLinkVerifier:
    """Verify a displayed job URL with fresh page evidence from this run."""

    def __init__(
        self,
        http_loader: Any = None,
        browser_loader: Any = None,
        timeout_seconds: float = 20,
        deadline: float | None = None,
        *,
        enable_browser_fallback: bool = True,
        evidence_dir: Path | None = None,
    ) -> None:
        self.http_loader = http_loader if http_loader is not None else HttpPageLoader(30)
        self.enable_browser_fallback = enable_browser_fallback is True
        if not self.enable_browser_fallback:
            self.browser_loader = None
        elif browser_loader is None:
            self.browser_loader = BrowserUseClient(
                default_timeout_seconds=max(1, int(timeout_seconds)),
                deadline=deadline,
                render_wait_seconds=min(MAX_JOB_RENDER_WAIT_SECONDS, max(0.0, float(timeout_seconds))),
            )
        else:
            self.browser_loader = browser_loader
        self.timeout_seconds = float(timeout_seconds)
        self.deadline = deadline
        self.evidence_dir = Path(evidence_dir) if evidence_dir is not None else None

    async def verify(self, job: Any, *, run_id: str) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")

        requested_url = str(_field(job, "url", "")).strip()
        operation_deadline = self._operation_deadline()
        if operation_deadline is not None and operation_deadline <= monotonic():
            return self._receipt(
                job,
                run_id=run_id,
                requested_url=requested_url,
                final_url=requested_url,
                method="http",
                reason="verification deadline exceeded before HTTP request",
                status="pending",
            )
        if not _is_safe_http_url(requested_url):
            return self._receipt(
                job,
                run_id=run_id,
                requested_url=requested_url,
                final_url=requested_url,
                method="http",
                reason="job URL is not a safe HTTP(S) detail URL",
                status="pending",
            )
        self._apply_deadline(self.http_loader, operation_deadline)
        self._apply_deadline(self.browser_loader, operation_deadline)

        http_error = ""
        http_page: Any = None
        http_gone_status: int | None = None
        http_gone_final_url = requested_url
        http_gone_evidence: Any = None
        http_probe_evidence: Any = None
        try:
            http_page = await self._load_with_deadline(
                self.http_loader, requested_url, operation_deadline
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return self._receipt(
                job,
                run_id=run_id,
                requested_url=requested_url,
                final_url=requested_url,
                method="http",
                reason="verification deadline exceeded during fresh HTTP visit",
                status="pending",
            )
        except Exception as exc:
            http_error = str(exc)
            http_status = _exception_status(exc)
            if http_status in {404, 410}:
                http_gone_status = http_status
                http_gone_final_url = _exception_url(exc, requested_url)
                http_gone_evidence = {
                    "requested_url": requested_url,
                    "final_url": http_gone_final_url,
                    "method": "http",
                    "status_code": http_status,
                    "error": http_error,
                }
                http_probe_evidence = http_gone_evidence
                if self.browser_loader is None:
                    return self._receipt(
                        job,
                        run_id=run_id,
                        requested_url=requested_url,
                        final_url=http_gone_final_url,
                        method="http",
                        reason=f"HTTP {http_status}: job detail is gone",
                        status="rejected",
                        evidence=http_gone_evidence,
                    )

        if http_page is not None:
            http_result = self._evaluate_page(job, http_page, method="http")
            if http_result["status"] == "rejected":
                page_status = _evidence_status(http_result.get("evidence"))
                if page_status in {404, 410} and self.browser_loader is not None:
                    http_gone_status = page_status
                    http_gone_final_url = http_result["final_url"]
                    http_gone_evidence = http_result.get("evidence")
                    http_probe_evidence = http_gone_evidence
                else:
                    return self._receipt(
                        job, run_id=run_id, requested_url=requested_url, **http_result
                    )
            if http_result["status"] == "verified":
                return self._receipt(
                    job, run_id=run_id, requested_url=requested_url, **http_result
                )
            http_probe_evidence = http_result.get("evidence")

        if self.browser_loader is None:
            reason = http_result["reason"] if http_page is not None else (
                f"fresh HTTP visit was inconclusive: {http_error}"
            )
            return self._receipt(
                job,
                run_id=run_id,
                requested_url=requested_url,
                final_url=http_result["final_url"] if http_page is not None else requested_url,
                method="http",
                reason=f"{reason}; browser fallback is unavailable",
                status="pending",
                title_matched=http_result["title_matched"] if http_page is not None else False,
                jd_matched=http_result["jd_matched"] if http_page is not None else False,
                active=http_result["active"] if http_page is not None else False,
                evidence=http_result.get("evidence") if http_page is not None else {"error": http_error},
            )

        previous_content_ready = self._install_job_content_ready(job)
        try:
            browser_page = await self._load_with_deadline(
                self.browser_loader, requested_url, operation_deadline
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            gone_prefix = (
                f"HTTP {http_gone_status} from the static probe; "
                if http_gone_status is not None else ""
            )
            return self._receipt(
                job,
                run_id=run_id,
                requested_url=requested_url,
                final_url=requested_url,
                method="browser",
                reason=f"{gone_prefix}verification deadline exceeded during SPA browser visit",
                status="pending",
                evidence={"http_probe": http_gone_evidence},
            )
        except Exception as exc:
            gone_prefix = (
                f"HTTP {http_gone_status} from the static probe did not establish removal; "
                if http_gone_status is not None else ""
            )
            return self._receipt(
                job,
                run_id=run_id,
                requested_url=requested_url,
                final_url=requested_url,
                method="browser",
                reason=f"{gone_prefix}browser fallback failed: {exc}",
                status="pending",
                evidence={
                    "http_probe": http_probe_evidence,
                    "browser_error": str(exc),
                },
            )
        finally:
            self._restore_content_ready(previous_content_ready)

        browser_result = self._evaluate_page(job, browser_page, method="browser")
        evidence = _combined_page_evidence(http_probe_evidence, browser_result.get("evidence"))
        browser_result["evidence"] = evidence
        if http_gone_status is not None and browser_result["status"] != "verified":
            browser_result["reason"] = (
                f"static HTTP {http_gone_status} did not establish removal; "
                f"{browser_result['reason']}"
            )
        return self._receipt(
            job, run_id=run_id, requested_url=requested_url, **browser_result
        )

    def _operation_deadline(self) -> float | None:
        now = monotonic()
        timeout = max(0.0, self.timeout_seconds)
        own_deadline = now + timeout
        if self.deadline is None:
            return own_deadline
        return min(float(self.deadline), own_deadline)

    def _apply_deadline(self, loader: Any, deadline: float | None) -> None:
        if loader is not None and hasattr(loader, "deadline"):
            try:
                loader.deadline = deadline
            except Exception:
                pass

    def _install_job_content_ready(
        self, job: Any
    ) -> tuple[BrowserUseClient, Any] | None:
        loader = self.browser_loader
        if not isinstance(loader, BrowserUseClient):
            return None
        previous = loader.content_ready

        def content_ready(content: str, title: str) -> bool:
            return _title_matches(job, title, content) and _jd_matches(job, content)

        loader.content_ready = content_ready
        return loader, previous

    def _restore_content_ready(
        self, previous: tuple[BrowserUseClient, Any] | None
    ) -> None:
        if previous is not None:
            loader, callback = previous
            loader.content_ready = callback

    async def _load_with_deadline(self, loader: Any, url: str, deadline: float | None) -> Any:
        remaining = None if deadline is None else deadline - monotonic()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("verification deadline exceeded")
        operation = self._invoke_loader(loader, url)
        if remaining is None:
            return await operation
        return await asyncio.wait_for(operation, timeout=remaining)

    async def _invoke_loader(self, loader: Any, url: str) -> Any:
        open_url = getattr(loader, "open_url", None)
        result = open_url(url) if callable(open_url) else loader(url)
        if inspect.isawaitable(result):
            return await result
        return result

    def _evaluate_page(self, job: Any, page: Any, *, method: str) -> dict[str, Any]:
        requested_url = str(_field(job, "url", "")).strip()
        final_url = _page_final_url(page, requested_url)
        final_url_observed = _page_final_url_observed(page)
        status_code = _page_status(page)
        title = str(_field(page, "title", "") or "")
        content = str(_field(page, "content", "") or "")
        controls = _page_apply_controls(page)
        evidence = {
            "requested_url": requested_url,
            "final_url": final_url,
            "final_url_observed": final_url_observed,
            "method": method,
            "status_code": status_code,
            "title": title,
            "content": content,
            "apply_controls": controls,
        }

        if method == "http" and status_code is None:
            return _page_result(
                final_url,
                method,
                status="pending",
                reason=(
                    "the HTTP response status was not observed; rendered browser "
                    "confirmation is required"
                ),
                evidence=evidence,
            )
        if method == "http" and status_code in {404, 410}:
            return _page_result(
                final_url,
                method,
                status="rejected",
                reason=f"HTTP {status_code}: job detail is gone",
                evidence=evidence,
            )
        if method == "http" and status_code is not None and status_code >= 400:
            return _page_result(
                final_url,
                method,
                status="pending",
                reason=f"HTTP {status_code}: page requires login, challenge handling, or review",
                evidence=evidence,
            )
        if _contains_offline_status(content):
            return _page_result(
                final_url,
                method,
                status="rejected",
                reason="the page explicitly states that the job is no longer open",
                evidence=evidence,
            )
        if _contains_login_or_challenge(content):
            return _page_result(
                final_url,
                method,
                status="pending",
                reason="the page is a login or anti-bot challenge rather than a job detail",
                evidence=evidence,
            )
        if _is_shell_page(content):
            return _page_result(
                final_url,
                method,
                status="pending",
                reason="the page body is an empty SPA shell or loading placeholder",
                evidence=evidence,
            )
        if not final_url_observed:
            return _page_result(
                final_url,
                method,
                status="pending",
                reason="the actual final page URL was not observed",
                evidence=evidence,
            )
        if not _is_safe_http_url(final_url):
            return _page_result(
                final_url,
                method,
                status="pending",
                reason="the final page URL is invalid or unsafe",
                evidence=evidence,
            )
        if not _redirect_identity_ok(requested_url, final_url):
            return _page_result(
                final_url,
                method,
                status="pending",
                reason="redirect host or native job id does not preserve the requested job identity",
                evidence=evidence,
            )
        eligible_controls = [control for control in controls if _is_eligible_apply_control(control)]
        if len(eligible_controls) >= 4:
            return _page_result(
                final_url,
                method,
                status="pending",
                reason="the page appears to be a job list rather than a job detail",
                evidence=evidence,
            )

        title_matched = _title_matches(job, title, content)
        jd_matched = _jd_matches(job, content)
        # Static HTML cannot establish computed visibility or enabled state. A
        # plain control is retained as candidate evidence for the browser path.
        active = method == "browser" and bool(eligible_controls)

        if _contains_list_page(content) and not title_matched and not jd_matched:
            return _page_result(
                final_url,
                method,
                status="pending",
                reason="the page appears to be a job list rather than a job detail",
                evidence=evidence,
            )

        if method == "http":
            if not title_matched:
                reason = "job title was not found in the fresh HTTP response"
            elif not jd_matched:
                reason = "a matching job-description snippet was not found in the fresh HTTP response"
            else:
                reason = (
                    "the HTTP response matches the candidate, but static HTML cannot "
                    "prove a visible enabled apply control; rendered browser confirmation is required"
                )
            return _page_result(
                final_url,
                method,
                status="pending",
                reason=reason,
                evidence=evidence,
                title_matched=title_matched,
                jd_matched=jd_matched,
                active=False,
            )

        if not title_matched:
            reason = "job title was not found in the fresh detail page"
        elif not jd_matched:
            reason = "a matching job-description snippet was not found in the fresh detail page"
        elif not active:
            reason = "no visible enabled apply control proves the posting is still open"
        else:
            reason = "detail page, job title, JD snippet, and visible enabled apply control matched"

        status = "verified" if title_matched and jd_matched and active else "pending"
        return _page_result(
            final_url,
            method,
            status=status,
            reason=reason,
            evidence=evidence,
            title_matched=title_matched,
            jd_matched=jd_matched,
            active=active,
        )

    def _receipt(
        self,
        job: Any,
        *,
        run_id: str,
        requested_url: str,
        final_url: str,
        method: str,
        reason: str,
        status: str,
        title_matched: bool = False,
        jd_matched: bool = False,
        active: bool = False,
        evidence: Any = None,
    ) -> dict[str, Any]:
        payload = evidence if evidence is not None else {
            "requested_url": requested_url,
            "final_url": final_url,
            "method": method,
            "status": status,
            "reason": reason,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if self.evidence_dir is not None:
            try:
                self.evidence_dir.mkdir(parents=True, exist_ok=True)
                (self.evidence_dir / f"{digest}.json").write_bytes(encoded.encode("utf-8"))
            except OSError as exc:
                status = "pending"
                reason = f"{reason}; page evidence could not be persisted: {type(exc).__name__}"
                payload = {
                    "verification_evidence": payload,
                    "evidence_persistence_error": type(exc).__name__,
                }
                encoded = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return {
            "status": status,
            "checked_at": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "requested_url": requested_url,
            "final_url": final_url,
            "method": method,
            "reason": reason,
            "evidence_sha256": digest,
            "title_matched": title_matched is True,
            "jd_matched": jd_matched is True,
            "active": active is True,
        }


def is_deliverable(job: Any, run_id: str, max_age_seconds: float = 900) -> bool:
    """Return true only for fresh, run-bound, rendered browser evidence."""

    receipt = _field(job, "link_verification", None)
    if not isinstance(receipt, Mapping) or set(receipt) != RECEIPT_KEYS:
        return False
    if not isinstance(run_id, str) or not run_id or receipt.get("run_id") != run_id:
        return False
    if receipt.get("status") != "verified":
        return False
    # Static HTTP responses remain useful candidate evidence, but cannot prove
    # computed visibility or enabled state for the apply control.
    if receipt.get("method") != "browser":
        return False
    if not isinstance(receipt.get("reason"), str) or not receipt["reason"].strip():
        return False
    if receipt.get("title_matched") is not True:
        return False
    if receipt.get("jd_matched") is not True:
        return False
    if receipt.get("active") is not True:
        return False

    digest = receipt.get("evidence_sha256")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        return False
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)):
        return False
    if not math.isfinite(float(max_age_seconds)) or max_age_seconds < 0:
        return False

    checked_at_value = receipt.get("checked_at")
    if not isinstance(checked_at_value, str):
        return False
    try:
        checked_at = datetime.fromisoformat(checked_at_value)
    except ValueError:
        return False
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        return False
    now = datetime.now(UTC)
    checked_at = checked_at.astimezone(UTC)
    age = (now - checked_at).total_seconds()
    if age < 0 or age > float(max_age_seconds):
        return False

    job_url = _field(job, "url", None)
    requested_url = receipt.get("requested_url")
    final_url = receipt.get("final_url")
    if not isinstance(job_url, str) or not job_url.strip() or requested_url != job_url.strip():
        return False
    if not isinstance(final_url, str) or not _is_safe_http_url(final_url):
        return False
    return _redirect_identity_ok(requested_url, final_url)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _page_final_url(page: Any, fallback: str) -> str:
    metadata = _field(page, "metadata", {})
    if isinstance(metadata, Mapping):
        final_url = metadata.get("final_url")
        if isinstance(final_url, str) and final_url.strip():
            return final_url.strip()
    page_url = _field(page, "url", fallback)
    return page_url.strip() if isinstance(page_url, str) and page_url.strip() else fallback


def _page_final_url_observed(page: Any) -> bool:
    metadata = _field(page, "metadata", {})
    return isinstance(metadata, Mapping) and metadata.get("final_url_observed") is True


def _page_status(page: Any) -> int | None:
    metadata = _field(page, "metadata", {})
    value = metadata.get("status_code") if isinstance(metadata, Mapping) else None
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _evidence_status(evidence: Any) -> int | None:
    if not isinstance(evidence, Mapping):
        return None
    value = evidence.get("status_code")
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _combined_page_evidence(http_evidence: Any, browser_evidence: Any) -> Any:
    if http_evidence is None:
        return browser_evidence
    return {
        "http_probe": http_evidence,
        "browser": browser_evidence,
    }


def _page_apply_controls(page: Any) -> list[dict[str, Any]]:
    metadata = _field(page, "metadata", {})
    raw = metadata.get("apply_controls", []) if isinstance(metadata, Mapping) else []
    if not isinstance(raw, list):
        return []
    controls: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        control = dict(item)
        control["text"] = re.sub(r"\s+", " ", str(control.get("text", ""))).strip()
        if control["text"]:
            controls.append(control)
    return controls


def _is_eligible_apply_control(control: Mapping[str, Any]) -> bool:
    return (
        control.get("visible") is True
        and control.get("enabled") is True
        and _APPLY_TEXT_PATTERN.search(str(control.get("text", ""))) is not None
    )


def _page_result(
    final_url: str,
    method: str,
    *,
    status: str,
    reason: str,
    evidence: Any,
    title_matched: bool = False,
    jd_matched: bool = False,
    active: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "final_url": final_url,
        "method": method,
        "reason": reason,
        "title_matched": title_matched,
        "jd_matched": jd_matched,
        "active": active,
        "evidence": evidence,
    }


def _exception_status(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _exception_url(exc: Exception, fallback: str) -> str:
    value = getattr(exc, "url", None)
    return str(value) if isinstance(value, str) and value.strip() else fallback


def _contains_offline_status(content: str) -> bool:
    return any(pattern.search(content) for pattern in _STATUS_OFFLINE_PATTERNS)


def _contains_login_or_challenge(content: str) -> bool:
    return any(pattern.search(content) for pattern in _LOGIN_OR_CHALLENGE_PATTERNS)


def _contains_list_page(content: str) -> bool:
    return any(pattern.search(content) for pattern in _LIST_PAGE_PATTERNS)


def _is_shell_page(content: str) -> bool:
    visible = re.sub(r"\s+", " ", content).strip()
    if len(visible) < 40:
        return True
    return any(pattern.search(visible) for pattern in _SHELL_PATTERNS)


def _normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[a-z0-9+#]+|[\u3400-\u9fff]", value))


def _title_matches(job: Any, title: str, content: str) -> bool:
    job_title = _normalized_text(str(_field(job, "title", "") or ""))
    if not job_title:
        return False
    return job_title in _normalized_text(title) or job_title in _normalized_text(content)


def _jd_matches(job: Any, content: str) -> bool:
    sources = [
        str(_field(job, "requirements", "") or ""),
        str(_field(job, "responsibilities", "") or ""),
    ]
    page_tokens = _normalized_text(content).split()
    if not page_tokens:
        return False
    page_sequence = " ".join(page_tokens)
    for source in sources:
        for chunk in re.split(r"[\n;；。.!?！？]+", source):
            tokens = _normalized_text(chunk).split()
            cjk_units = sum(1 for token in tokens if len(token) == 1 and "\u3400" <= token <= "\u9fff")
            minimum = 12 if cjk_units >= max(1, len(tokens) // 2) else 5
            if len(tokens) < minimum:
                continue
            window = min(12 if minimum == 5 else 20, len(tokens))
            starts = {0, max(0, len(tokens) - window), max(0, (len(tokens) - window) // 2)}
            for start in starts:
                phrase = " ".join(tokens[start : start + window])
                if phrase and phrase in page_sequence:
                    return True
    return False


def _is_safe_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    try:
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _native_ids(value: str) -> set[str]:
    try:
        parsed = urlparse(value)
    except ValueError:
        return set()
    ids: set[str] = set()

    def add_candidate(candidate: str, *, trusted_id_key: bool = False) -> None:
        cleaned = unquote(candidate).strip().strip("/").casefold()
        if not cleaned or cleaned in _STOP_PATH_PARTS:
            return
        if trusted_id_key:
            ids.add(cleaned)
            uuid_match = re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", cleaned)
            if uuid_match:
                ids.add(uuid_match.group(0))
            ids.update(part for part in re.findall(r"\d{3,}", cleaned) if part != cleaned)
            return
        # A campaign year is route context, not a job identity. Counting it as
        # an ID lets two different postings pass by sharing only "2027".
        if re.fullmatch(r"(?:19|20|21)\d{2}", cleaned):
            return
        if len(cleaned) >= 3 and (
            re.search(r"\d", cleaned)
            or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", cleaned)
        ):
            ids.add(cleaned)
            uuid_match = re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", cleaned)
            if uuid_match:
                ids.add(uuid_match.group(0))
            ids.update(part for part in re.findall(r"\d{3,}", cleaned) if part != cleaned)

    for key, candidate in parse_qsl(parsed.query, keep_blank_values=True):
        if _NATIVE_ID_KEYS.fullmatch(key):
            add_candidate(candidate, trusted_id_key=True)

    fragment = unquote(parsed.fragment)
    if fragment:
        fragment_path, _, fragment_query = fragment.partition("?")
        for part in re.split(r"[/=&]", fragment_path):
            add_candidate(part)
        for key, candidate in parse_qsl(fragment_query, keep_blank_values=True):
            if _NATIVE_ID_KEYS.fullmatch(key):
                add_candidate(candidate, trusted_id_key=True)

    for part in parsed.path.split("/"):
        add_candidate(part)
    return ids


def _normalize_fragment_path(value: str) -> str:
    normalized = unquote(value).strip()
    if not normalized:
        return ""
    normalized = re.sub(r"/{2,}", "/", normalized)
    return normalized.rstrip("/") or "/"


def _tracking_query_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized.startswith("utm_") or normalized in _TRACKING_QUERY_KEYS


def _normalized_query(query: str) -> tuple[tuple[str, str], ...]:
    pairs = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        normalized_key = unquote(key).strip().casefold()
        if _tracking_query_key(normalized_key):
            continue
        pairs.append((normalized_key, unquote(value).strip()))
    return tuple(sorted(pairs))


def _normalized_fragment(fragment: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    decoded = unquote(fragment).strip()
    if not decoded:
        return "", ()
    fragment_path, separator, fragment_query = decoded.partition("?")
    return (
        _normalize_fragment_path(fragment_path),
        _normalized_query(fragment_query) if separator else (),
    )


def _normalized_url_identity(value: str) -> tuple[Any, ...]:
    parsed = urlparse(value)
    return (
        _normalize_fragment_path(parsed.path or "/"),
        _normalized_query(parsed.query),
        _normalized_fragment(parsed.fragment),
    )


def _redirect_identity_ok(requested_url: str, final_url: str) -> bool:
    if not _is_safe_http_url(requested_url) or not _is_safe_http_url(final_url):
        return False
    requested = urlparse(requested_url)
    final = urlparse(final_url)
    requested_host = (requested.hostname or "").casefold().rstrip(".")
    final_host = (final.hostname or "").casefold().rstrip(".")
    if requested_host != final_host:
        return False
    if requested.scheme.casefold() == "https" and final.scheme.casefold() != "https":
        return False
    if _effective_port(requested) != _effective_port(final):
        return False
    requested_ids = _native_ids(requested_url)
    final_ids = _native_ids(final_url)
    if requested_ids:
        # All identifiable job tokens must survive the redirect. Partial overlap
        # is not enough because campaign years and route codes are shared.
        return requested_ids.issubset(final_ids)
    # Without a native identifier, a same-host redirect still has to preserve
    # the normalized path, non-tracking query, and fragment identity.
    if _normalized_url_identity(requested_url) != _normalized_url_identity(final_url):
        return False
    return True


def _effective_port(parsed: Any) -> int | None:
    try:
        if parsed.port is not None:
            return parsed.port
    except ValueError:
        return None
    return 443 if parsed.scheme.casefold() == "https" else 80

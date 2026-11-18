from __future__ import annotations

import asyncio
import inspect
import json
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from time import monotonic
from typing import Any, Protocol
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

from web_task_agent.models import BrowserPage
from web_task_agent.search_discovery import discover_job_links, next_search_url

PageLoader = Callable[[str], Awaitable[BrowserPage]]
SessionFactory = Callable[[], Any]
ContentReady = Callable[[str, str], bool | Awaitable[bool]]


@dataclass
class _SearchCursor:
    next_url: str
    seen: set[str] = field(default_factory=set)
    pages_read: int = 0
    stagnant_pages: int = 0
    exhausted: bool = False
    buffered: list[str] = field(default_factory=list)
    buffered_page: BrowserPage | None = None
    stop_reason: str = ""

# 低于该长度的正文视为"无有效内容"（美团/腾讯 SPA 返回 4~11 字符的壳；
# 实测 job-radar 的 jd_text 中位数仅 46 字符——基本全是"标题+部门+城市"，
# 50 门槛会把这类无信息内容和第三方站的浏览器兼容提示（98 字符）放进管线，
# 诱发 LLM 幻觉 JD，复现实录 #21）。
# 缓存命中路径（CachedPageLoader / AggregatorPageLoader）同样要过这道门槛，
# 否则门槛收紧前写入的旧壳页会在 TTL 内继续生效（#25）。
MIN_USEFUL_CONTENT_CHARS = 120
# 云端冷启动时动态招聘页可能十余秒才出现正文；仍由单页和整轮截止时间兜底。
MAX_JOB_RENDER_WAIT_SECONDS = 20.0


class BrowserConfigurationError(RuntimeError):
    """Raised when the real browser adapter cannot run a requested action."""


class PageTimeoutError(BrowserConfigurationError):
    """DNS resolution failure, connection timeout, or response timeout."""


class PageHttpError(BrowserConfigurationError):
    """HTTP error status returned (4xx, 5xx)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PageEmptyError(BrowserConfigurationError):
    """Page was fetched successfully but returned no useful content (JS shell or empty body)."""


class BrowserClient(Protocol):
    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        """Search for pages matching a query."""

    async def open_url(self, url: str) -> BrowserPage:
        """Open a page by exact URL."""


class FakeBrowserClient:
    def __init__(self, pages: list[BrowserPage]) -> None:
        self._pages = list(pages)

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        if target_count <= 0:
            return []

        terms = query.casefold().split()
        scored_pages: list[tuple[int, int, BrowserPage]] = []
        for index, page in enumerate(self._pages):
            searchable_text = f"{page.title} {page.content}".casefold()
            score = sum(searchable_text.count(term) for term in terms)
            if score > 0:
                scored_pages.append((score, index, page))

        scored_pages.sort(key=lambda item: (-item[0], item[1]))
        return [page for _, _, page in scored_pages[:target_count]]

    async def open_url(self, url: str) -> BrowserPage:
        for page in self._pages:
            if page.url == url:
                return page
        raise ValueError(f"Browser page not found for URL: {url}")


class BrowserUseClient:
    # 浏览器适配器提供搜索和打开页面两种能力。输出统一为 BrowserPage，
    # 这样工具层无需关心正文来自实际浏览器会话还是来源专用的页面加载器。
    def __init__(
        self,
        default_timeout_seconds: int = 60,
        page_loader: PageLoader | None = None,
        session_factory: SessionFactory | None = None,
        discovery_only: bool = False,
        deadline: float | None = None,
        render_wait_seconds: float = 8.0,
        content_ready: ContentReady | None = None,
        max_search_pages: int = 10,
    ) -> None:
        self.default_timeout_seconds = default_timeout_seconds
        self._page_loader = page_loader
        self._session_factory = session_factory
        self.discovery_only = discovery_only
        self.deadline = deadline
        self.render_wait_seconds = max(0.0, render_wait_seconds)
        # Job verification uses this callback to distinguish a long navigation
        # shell from the detail content that is expected for a specific job.
        self.content_ready = content_ready
        if max_search_pages < 1:
            raise ValueError("max_search_pages must be positive")
        self.max_search_pages = max_search_pages
        self._search_cursors: dict[str, _SearchCursor] = {}
        self.search_diagnostics: dict[str, Any] = {}
        self.last_read_diagnostics: dict[str, Any] = {}

    @property
    def source_diagnostics(self) -> list[dict[str, object]]:
        return getattr(self._page_loader, "source_diagnostics", [])

    @property
    def resolution_log(self) -> list[dict[str, str]]:
        return getattr(self._page_loader, "resolution_log", [])

    async def search(self, query: str, target_count: int) -> list[BrowserPage]:
        # discovery_only 表示候选已由来源层发现，只用这个适配器加载详情。
        if target_count <= 0 or self.discovery_only:
            return []

        query = query.strip()
        base_url = f"https://www.google.com/search?q={quote_plus(query)}"
        cursor = self._search_cursors.setdefault(query, _SearchCursor(next_url=base_url))
        pages: list[BrowserPage] = []
        count = 0
        reason = cursor.stop_reason or "no_more_results"
        while count < target_count:
            remaining = self._remaining_seconds()
            if remaining is not None and remaining <= 0:
                if not pages:
                    raise PageTimeoutError("search deadline exceeded")
                reason = "deadline_exceeded"
                break
            if cursor.buffered:
                page = cursor.buffered_page
                candidates = cursor.buffered
                cursor.buffered = []
            else:
                if cursor.exhausted:
                    break
                if cursor.pages_read >= self.max_search_pages:
                    reason = "page_limit"
                    cursor.exhausted = True
                    break
                search_url = cursor.next_url
                try:
                    page = await asyncio.wait_for(self.open_url(search_url), timeout=remaining)
                except Exception as exc:
                    if not pages:
                        raise
                    from web_task_agent.page_reliability import PageFailureCategory, classify_page_exception

                    timed_out = self.deadline is not None and (
                        isinstance(exc, TimeoutError) or monotonic() >= self.deadline
                    )
                    reason = "deadline_exceeded" if timed_out else "page_error"
                    failure = classify_page_exception(exc)
                    self.search_diagnostics = {
                        "error_type": type(exc).__name__, "error_category": failure.category.value,
                        "recoverable": failure.category in {PageFailureCategory.TRANSIENT, PageFailureCategory.BLOCKED},
                    }
                    break
                cursor.pages_read += 1
                raw_links = page.metadata.get("raw_links", [])
                raw_links = [link for link in raw_links if isinstance(link, str)] if isinstance(raw_links, list) else []
                discovered = discover_job_links(page.content, base_url=search_url, raw_links=raw_links)
                candidates = [url for url in discovered if url not in cursor.seen]
                cursor.seen.update(candidates)
                cursor.stagnant_pages = 0 if candidates else cursor.stagnant_pages + 1
                next_url = next_search_url(page.content, base_url=search_url, raw_links=raw_links, query=query)
                has_next_link = next_url is not None
                # Without explicit controls, Google uses ten-result offsets.
                if next_url is None:
                    offset = int(parse_qs(urlsplit(search_url).query).get("start", [0])[0])
                    next_url = f"{base_url}&start={offset + 10}"
                cursor.next_url = next_url
                if not discovered and not has_next_link:
                    reason, cursor.exhausted = "no_more_results", True
                elif cursor.stagnant_pages >= 2:
                    reason, cursor.exhausted = "no_progress", True
            selected, overflow = candidates[:target_count - count], candidates[target_count - count:]
            if overflow:
                cursor.buffered, cursor.buffered_page = overflow, page
            pages.append(page.model_copy(update={"metadata": {**page.metadata, "candidate_urls": selected}}))
            count += len(selected)
            if count >= target_count:
                reason = "target_reached"
                break
        if cursor.exhausted and reason != "target_reached":
            cursor.stop_reason = reason
        self.search_diagnostics = {
            **(self.search_diagnostics if reason == "page_error" else {}),
            "query": query, "pages_read": cursor.pages_read, "candidate_count": count,
            "requested_count": target_count, "shortfall": max(0, target_count - count),
            "stop_reason": reason, "has_more": bool(cursor.buffered) or not cursor.exhausted,
        }
        return [page.model_copy(update={"metadata": {**page.metadata, "search_diagnostics": self.search_diagnostics}})
                for page in pages]

    async def open_url(self, url: str) -> BrowserPage:
        try:
            # 优先使用配置的加载器，例如来源专用适配器。否则由 browser-use 创建会话加载页面。
            if self._page_loader is not None:
                return await self._page_loader(url)
            return await self._load_with_browser_use(url)
        except BrowserConfigurationError:
            raise  # pass through specific subtypes (PageTimeoutError, etc.)
        except Exception as exc:
            # 统一包装未知的底层异常，同时保留原异常链，供工具层分类和排查。
            raise BrowserConfigurationError(
                f"browser-use failed while opening {url}: {exc}"
            ) from exc

    async def _load_with_browser_use(self, url: str) -> BrowserPage:
        session = self._create_session()
        try:
            # 导航和读取正文共用本次运行的剩余时间，避免每次开页都重新获得完整的等待额度。
            remaining = self._remaining_seconds()
            if remaining is not None:
                if remaining <= 0:
                    raise PageTimeoutError("browser deadline exceeded")
                return await asyncio.wait_for(self._read_browser_page(session, url), timeout=remaining)
            return await self._read_browser_page(session, url)
        except TimeoutError as exc:
            raise PageTimeoutError(f"browser deadline exceeded while opening {url}") from exc
        except asyncio.CancelledError:
            raise
        except BrowserConfigurationError:
            raise
        except Exception as exc:
            raise BrowserConfigurationError(
                f"browser-use failed while opening {url}: {exc}"
            ) from exc
        finally:
            # 超时、取消或读取失败时也释放浏览器会话。
            await self._close_session(session)

    async def _read_browser_page(self, session: Any, url: str) -> BrowserPage:
        # 在同一页面上读正文、标题、最终地址和申请控件，后面的提取与核验可共用这些数据。
        started = monotonic()
        self.last_read_diagnostics = {"stage": "starting_browser"}
        await self._call(session.start)
        self.last_read_diagnostics.update(
            stage="opening_page", session_started_after=round(monotonic() - started, 3),
        )
        page = await self._call(session.new_page, url)
        self.last_read_diagnostics.update(
            stage="rendering", page_created_after=round(monotonic() - started, 3),
        )
        # 动态站点初次渲染可能只有加载提示。正文读取可在限时内等待内容就绪。
        try:
            content = await self._read_body_until_useful(page)
        finally:
            self.last_read_diagnostics["body_wait_finished_after"] = round(monotonic() - started, 3)
            await self._capture_render_diagnostics(page)
        title = await self._read_title(page)
        final_url, final_url_observed = await self._read_current_url(page, url)
        apply_controls = await self._read_apply_controls(page)
        raw_links = await self._read_links(page) if "google.com/search" in url else []
        # 申请控件用于链接校验。区分实际观测的最终地址和回退值，供下游判断重定向身份。
        metadata: dict[str, Any] = {
            "requested_url": url,
            "final_url": final_url,
            "final_url_observed": final_url_observed,
            "timeout_seconds": self.default_timeout_seconds,
            "apply_controls": apply_controls,
            "browser_diagnostics": dict(self.last_read_diagnostics),
        }
        if raw_links:
            metadata["raw_links"] = raw_links
        return BrowserPage(
            url=final_url,
            title=title,
            content=content,
            source="browser-use",
            metadata=metadata,
        )

    async def _capture_render_diagnostics(self, page: Any) -> None:
        if not hasattr(page, "evaluate"):
            return
        try:
            async with asyncio.timeout(0.75):
                data = await self._call(
                    page.evaluate,
                    "() => ({readyState: document.readyState, title: document.title, "
                    "visibility: document.visibilityState, focused: document.hasFocus(), "
                    "body_chars: (document.body?.innerText || '').length, "
                    "resources: performance.getEntriesByType('resource').slice(-20).map(r => "
                    "({url: r.name.split('?')[0], duration: Math.round(r.duration), "
                    "status: r.responseStatus, size: r.decodedBodySize}))})",
                )
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict):
                self.last_read_diagnostics["page"] = data
        except Exception:
            # Diagnostics must not replace a timeout/cancellation or block cleanup.
            pass

    def _remaining_seconds(self) -> float | None:
        # 没有共享截止时间时按单页默认值计时，有截止时间时取本次运行实际剩余量。
        if self.deadline is None:
            return float(self.default_timeout_seconds)
        return self.deadline - monotonic()

    async def _close_session(self, session: Any) -> None:
        try:
            close = getattr(session, "close", None)
            if close is None:
                return
            async with asyncio.timeout(2.0):
                await self._call(close)
        except TimeoutError:
            return

    def _create_session(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory()

        try:
            from browser_use import BrowserSession
        except ImportError as exc:
            raise BrowserConfigurationError(
                "browser-use is not installed in the active environment. "
                "Install project dependencies inside .venv or re-run with --demo."
            ) from exc

        return BrowserSession(
            headless=True,
            is_local=True,
            cloud_browser=False,
            use_cloud=False,
            # Force Background Tab keeps Linux headless pages hidden and stalls
            # visibility-dependent SPA rendering despite successful API responses.
            enable_default_extensions=False,
            # Some Chinese careers sites redirect English browsers to a homepage,
            # silently dropping a valid native job ID (NetEase cloud regression).
            args=["--lang=zh-CN", "--accept-lang=zh-CN,zh"],
        )

    async def _read_title(self, page: Any) -> str:
        if hasattr(page, "get_title"):
            return str(await self._call(page.get_title))
        if hasattr(page, "title"):
            return str(await self._call(page.title))
        return ""

    async def _read_body_text(self, page: Any) -> str:
        # 适配不同页面对象。优先用它的正文 API，其次在页面执行脚本读可见文字。
        # body 没有内容时试整份文档的可见文字。
        if hasattr(page, "inner_text"):
            return str(await self._call(page.inner_text, "body"))
        if hasattr(page, "evaluate"):
            try:
                text = await self._call(
                    page.evaluate,
                    "() => document.body ? document.body.innerText : ''",
                )
                if str(text).strip():
                    return str(text)
            except Exception:
                pass
            return str(
                await self._call(
                    page.evaluate,
                    "() => document.documentElement ? document.documentElement.innerText : ''",
                )
            )
        return ""

    async def _read_body_until_useful(self, page: Any) -> str:
        # 默认以正文长度判断页面是否已渲染。严格核验可提供专门的就绪条件，
        # 比如等正文中出现与当前岗位匹配的描述片段。
        content = await self._read_body_text(page)
        if not hasattr(page, "evaluate"):
            return content

        content_ready = self.content_ready
        if content_ready is None and len(content.strip()) >= MIN_USEFUL_CONTENT_CHARS:
            return content

        # 轮询受渲染等待和整轮截止约束。到点后返回最后读到的内容，交给下游判断质量。
        wait_seconds = self.render_wait_seconds
        if content_ready is not None:
            wait_seconds = min(wait_seconds, MAX_JOB_RENDER_WAIT_SECONDS)
        deadline = monotonic() + wait_seconds
        if self.deadline is not None:
            deadline = min(deadline, float(self.deadline))
        while True:
            if content_ready is not None:
                try:
                    title = await self._read_title(page)
                    ready = bool(await self._call(content_ready, content, title))
                except Exception:
                    ready = False
                if ready:
                    return content
            elif len(content.strip()) >= MIN_USEFUL_CONTENT_CHARS:
                return content

            remaining = deadline - monotonic()
            if remaining <= 0:
                return content
            await asyncio.sleep(min(0.1, remaining))
            content = await self._read_body_text(page)

    async def _read_current_url(self, page: Any, requested_url: str) -> tuple[str, bool]:
        # 先用页面对象的接口，再尝试读 window.location。都读不到时回传请求地址和 False，
        # 让严格核验知道这次没有真实观察到最终 URL。
        for name in ("get_url", "url"):
            value = getattr(page, name, None)
            if value is None:
                continue
            try:
                candidate = await self._call(value) if callable(value) else value
            except Exception:
                continue
            if (
                isinstance(candidate, str)
                and candidate.strip().startswith(("http://", "https://"))
            ):
                return candidate.strip(), True
        if hasattr(page, "evaluate"):
            try:
                candidate = await self._call(
                    page.evaluate,
                    "() => window.location.href",
                )
                if (
                    isinstance(candidate, str)
                    and candidate.strip().startswith(("http://", "https://"))
                ):
                    return candidate.strip(), True
            except Exception:
                pass
        return requested_url, False

    async def _read_apply_controls(self, page: Any) -> list[dict[str, Any]]:
        # 在已渲染 DOM 中收集链接、按钮等申请控件及其状态。后续用这些信息判断是否仍可申请。
        if not hasattr(page, "evaluate"):
            return []
        try:
            controls = await self._call(
                page.evaluate,
                "() => Array.from(document.querySelectorAll('a,button,input[type=\"submit\"],"
                "input[type=\"button\"],[role=\"button\"]')).flatMap(el => {"
                "const style = window.getComputedStyle(el);"
                "const rect = el.getBoundingClientRect();"
                "const hidden = el.closest('[hidden],[aria-hidden=\"true\"]');"
                "const visible = !hidden && style.display !== 'none' && style.visibility !== 'hidden' "
                "&& style.opacity !== '0' "
                "&& (el.offsetWidth > 0 || el.offsetHeight > 0 || rect.width > 0 || rect.height > 0);"
                "const enabled = !el.disabled && el.getAttribute('aria-disabled') !== 'true';"
                "const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '') "
                ".trim().replace(/\\s+/g, ' ');"
                "if (!visible || !enabled || !text || !/(apply|投\\s*递|申\\s*请|应\\s*聘)/i.test(text)) return [];"
                "return [{tag: el.tagName.toLowerCase(), text, visible: true, enabled: true, "
                "href: el.href || '', context: (el.closest('main,article,section,form')?.innerText || '') "
                ".trim().slice(0, 240)}];"
                "})",
            )
        except Exception:
            return []
        if isinstance(controls, str):
            try:
                controls = json.loads(controls)
            except (TypeError, ValueError):
                return []
        if not isinstance(controls, list):
            return []
        normalized: list[dict[str, Any]] = []
        for control in controls:
            if not isinstance(control, dict):
                continue
            text = re.sub(r"\s+", " ", str(control.get("text", ""))).strip()
            if not text:
                continue
            item: dict[str, Any] = {
                "tag": str(control.get("tag", "")),
                "text": text,
                "visible": control.get("visible") is True,
                "enabled": control.get("enabled") is True,
            }
            href = str(control.get("href", "")).strip()
            if href:
                item["href"] = href
            context = str(control.get("context", "")).strip()
            if context:
                item["context"] = context
            normalized.append(item)
        return normalized

    async def _read_links(self, page: Any) -> list[str]:
        if not hasattr(page, "evaluate"):
            return []
        try:
            links = await self._call(
                page.evaluate,
                "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)",
            )
        except Exception:
            return []
        if isinstance(links, str):
            try:
                links = json.loads(links)
            except (TypeError, ValueError):
                return []
        if not isinstance(links, list):
            return []
        return [str(link) for link in links if str(link).strip()]

    async def _call(self, func: Any, *args: Any) -> Any:
        result = func(*args)
        if inspect.isawaitable(result):
            return await result
        return result


class CachedPageLoader:
    """包装一个 page loader：命中 SQLite 缓存（TTL 内）不发 HTTP，未命中回源后写缓存。

    Args:
        loader: 被包装的 loader（如 HttpPageLoader），签名 `(url) -> BrowserPage`。
        repository: 提供 get_cached_page / cache_page 的 JobRepository。
        max_age_hours: 缓存 TTL，默认 24h。
    """

    def __init__(
        self,
        loader: PageLoader,
        repository: Any,
        *,
        max_age_hours: float = 24.0,
    ) -> None:
        self._loader = loader
        self._repository = repository
        self._max_age_hours = max_age_hours

    @property
    def deadline(self) -> float | None:
        return getattr(self._loader, "deadline", None)

    @deadline.setter
    def deadline(self, value: float | None) -> None:
        if hasattr(self._loader, "deadline"):
            self._loader.deadline = value

    async def __call__(self, url: str) -> BrowserPage:
        cached = self._repository.get_cached_page(url, max_age_hours=self._max_age_hours)
        # 缓存命中也要过内容门槛：门槛收紧（50→120，#21）前写入的旧壳页
        # （腾讯 SPA 11 字符、第三方站兼容提示 98 字符）在 TTL 内仍会命中，
        # 空壳页抽出相同 title 后互判重复（#25）。低于门槛视为缓存失效重新抓取。
        if cached is not None and len(cached.content.strip()) >= MIN_USEFUL_CONTENT_CHARS:
            return cached
        page = await self._loader(url)
        self._repository.cache_page(page)
        return page


class HttpPageLoader:
    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds
        self.deadline: float | None = None

    async def __call__(self, url: str) -> BrowserPage:
        return await self._load(url)

    async def _load(self, url: str) -> BrowserPage:
        timeout = self.timeout_seconds if self.deadline is None else min(
            self.timeout_seconds, self.deadline - monotonic())
        if timeout <= 0:
            raise PageTimeoutError("request deadline exceeded")
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._load_sync, url), timeout=timeout)
        except TimeoutError as exc:
            raise PageTimeoutError(f"request timed out: {url}") from exc

    def _load_sync(self, url: str) -> BrowserPage:
        try:
            req = request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0 Safari/537.36"
                    ),
                },
            )
            timeout = self.timeout_seconds if self.deadline is None else min(
                self.timeout_seconds, self.deadline - monotonic())
            if timeout <= 0:
                raise PageTimeoutError("request deadline exceeded")
            with request.urlopen(req, timeout=timeout) as response:
                observed_url = response.geturl() if hasattr(response, "geturl") else ""
                final_url_observed = (
                    isinstance(observed_url, str)
                    and observed_url.strip().startswith(("http://", "https://"))
                )
                final_url = observed_url.strip() if final_url_observed else url
                status_code = int(getattr(response, "status", 200) or 200)
                html = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise PageHttpError(
                f"HTTP {exc.code} {exc.reason}: {url}",
                status_code=exc.code,
            ) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise PageTimeoutError(
                    f"connection timed out after {self.timeout_seconds}s: {url}"
                ) from exc
            if isinstance(reason, OSError) and "getaddrinfo" in str(reason).lower():
                raise PageTimeoutError(f"DNS resolution failed: {url}") from exc
            raise PageTimeoutError(f"URL error: {url} — {exc}") from exc
        except TimeoutError as exc:
            raise PageTimeoutError(f"request timed out: {url}") from exc

        parser = _TextExtractor(base_url=final_url)
        parser.feed(html)
        content = parser.get_text()
        if not content.strip():
            raise PageEmptyError(f"page returned empty content (likely JS-rendered): {url}")

        return BrowserPage(
            url=final_url,
            title=self._extract_title(html),
            content=content,
            source="http",
            metadata={
                "requested_url": url,
                "final_url": final_url,
                "final_url_observed": final_url_observed,
                "status_code": status_code,
                "timeout_seconds": self.timeout_seconds,
                "apply_controls": parser.apply_controls,
            },
        )

    def _extract_title(self, html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if not match:
            return ""
        return self._clean_text(match.group(1))

    def _extract_text(self, html: str) -> str:
        parser = _TextExtractor()
        parser.feed(html)
        return parser.get_text()

    def _clean_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()


class _TextExtractor(HTMLParser):
    _CONTROL_TAGS = {"a", "button", "input"}
    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }
    _SKIP_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self, *, base_url: str | None = None) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._hidden_depth = 0
        self._tag_stack: list[tuple[str, str]] = []
        self._active_control: dict[str, Any] | None = None
        self._active_control_tag = ""
        self._base_url = base_url
        self.apply_controls: list[dict[str, Any]] = []

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attr_map = {str(key).lower(): "" if value is None else str(value) for key, value in attrs}
        suppressed = self._skip_depth > 0 or self._hidden_depth > 0
        hidden = self._is_hidden(tag, attr_map)
        marker = "plain"
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            marker = "skip"
        elif hidden and tag not in self._VOID_TAGS:
            self._hidden_depth += 1
            marker = "hidden"
        if tag not in self._VOID_TAGS:
            self._tag_stack.append((tag, marker))

        if suppressed or hidden or tag not in self._CONTROL_TAGS:
            return
        if "disabled" in attr_map or attr_map.get("aria-disabled", "").lower() == "true":
            return
        if tag == "input" and attr_map.get("type", "").lower() not in {"submit", "button"}:
            return
        text = re.sub(r"\s+", " ", attr_map.get("value") or attr_map.get("aria-label") or "").strip()
        href = attr_map.get("href", "").strip()
        if href and self._base_url:
            href = urljoin(self._base_url, href)
        self._active_control = {
            "tag": tag,
            "text": text,
            "visible": True,
            "enabled": True,
        }
        if href:
            self._active_control["href"] = href
        self._active_control_tag = tag
        if tag == "input":
            self._finish_control()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if self._active_control is not None and tag == self._active_control_tag:
            self._finish_control()

        self._close_open_tag(tag)

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and self._hidden_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)
                if self._active_control is not None:
                    current = str(self._active_control.get("text", ""))
                    self._active_control["text"] = f"{current} {text}".strip()

    def _is_hidden(self, tag: str, attrs: dict[str, str]) -> bool:
        if "hidden" in attrs or "inert" in attrs:
            return True
        if attrs.get("aria-hidden", "").lower() == "true":
            return True
        if tag == "input" and attrs.get("type", "").lower() == "hidden":
            return True
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        if any(marker in style for marker in ("display:none", "visibility:hidden", "opacity:0")):
            return True
        classes = {item.lower() for item in attrs.get("class", "").split()}
        return bool(classes & {"hidden", "d-none", "invisible", "sr-only", "visually-hidden"})

    def _finish_control(self) -> None:
        if self._active_control is None:
            return
        control = self._active_control
        control["text"] = re.sub(r"\s+", " ", str(control.get("text", ""))).strip()
        if control["text"]:
            self.apply_controls.append(control)
        self._active_control = None
        self._active_control_tag = ""

    def _close_open_tag(self, tag: str) -> None:
        match_index = next(
            (index for index in range(len(self._tag_stack) - 1, -1, -1)
             if self._tag_stack[index][0] == tag),
            None,
        )
        if match_index is None:
            return
        while len(self._tag_stack) > match_index:
            _, marker = self._tag_stack.pop()
            if marker == "skip" and self._skip_depth > 0:
                self._skip_depth -= 1
            elif marker == "hidden" and self._hidden_depth > 0:
                self._hidden_depth -= 1

    def get_text(self) -> str:
        return "\n".join(self._chunks)

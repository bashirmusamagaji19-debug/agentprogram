"""Exercise the real cloud browser without API keys or recruitment-site traffic."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from web_task_agent.browser import BrowserUseClient  # noqa: E402


async def main() -> None:
    title = "Cloud browser smoke"
    content = "Browser link verification runtime is available. " * 8
    # Exercise a cold SPA as well: its body appears after the old 5/8-second limits.
    html = (
        f"<html><head><title>{title}</title></head><body><script>"
        f"setTimeout(() => {{document.body.innerText = {json.dumps(content)};}}, 10000);"
        "</script></body></html>"
    )
    page = await BrowserUseClient(
        default_timeout_seconds=45,
        render_wait_seconds=20,
        content_ready=lambda body, _: content.strip() in body,
    ).open_url(
        f"data:text/html;charset=utf-8,{quote(html)}"
    )
    assert page.title == title, f"Unexpected browser title: {page.title!r}"
    assert content.strip() in page.content, "Browser did not read the rendered body"
    print("Cloud browser smoke passed: BrowserSession read a delayed SPA title/body.")


if __name__ == "__main__":
    asyncio.run(main())

"""Temporary public-page diagnostic, isolated from deployment and deterministic CI."""
import asyncio
import json
import time

from browser_use import BrowserSession

URL = "https://xiaomi.jobs.f.mioffice.cn/internship/position/7662271237599725834/detail"
SCRIPT = """() => ({title: document.title, visibility: document.visibilityState,
focus: document.hasFocus(), ready: document.readyState,
chars: (document.body?.innerText || '').length,
text: document.body?.textContent?.slice(0, 500),
html: document.body?.innerHTML?.slice(0, 6000),
children: Array.from(document.body?.children || []).slice(0, 8).map(e => ({
tag: e.tagName, id: e.id, class: e.className, display: getComputedStyle(e).display,
visibility: getComputedStyle(e).visibility, opacity: getComputedStyle(e).opacity})),
resources: performance.getEntriesByType('resource').slice(-15).map(r => ({
url: r.name.split('?')[0], duration: Math.round(r.duration), status: r.responseStatus,
size: r.decodedBodySize}))})"""


async def probe(label, *, activate=False, **options):
    session = BrowserSession(headless=True, is_local=True, use_cloud=False, **options)
    started = time.monotonic()
    try:
        await session.start()
        page = await session.new_page(URL)
        if activate:
            await session.cdp_client.send.Target.activateTarget(params={"targetId": page._target_id})
        first = None
        while time.monotonic() - started < 25:
            data = json.loads(await page.evaluate(SCRIPT))
            if first is None:
                first = {k: v for k, v in data.items() if k not in {"html", "resources", "text"}}
            if data["chars"] >= 120:
                break
            await asyncio.sleep(0.5)
        print("PROBE_RESULT " + json.dumps({"label": label, "elapsed": round(time.monotonic()-started, 2),
              "first": first, "final": data}, ensure_ascii=True), flush=True)
    except Exception as exc:
        print("PROBE_ERROR " + json.dumps({"label": label, "error": str(exc)}), flush=True)
    finally:
        try:
            await asyncio.wait_for(session.kill(), timeout=5)
        except Exception:
            pass


async def main():
    await probe("default")
    await probe("no_extensions", enable_default_extensions=False)
    await probe("activate_tab", activate=True)


asyncio.run(main())


async def main():
    await probe("default")
    await probe("without_extensions", enable_default_extensions=False)
    await probe("activated", activate=True)


asyncio.run(main())

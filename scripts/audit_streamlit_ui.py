from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

CONTRAST_SCRIPT = """
() => {
  function parseColor(value) {
    const match = value.match(/rgba?\\(([^)]+)\\)/);
    if (!match) return null;
    const parts = match[1].split(",").map(Number);
    return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1 };
  }
  function luminance(color) {
    const channels = [color.r, color.g, color.b].map((value) => {
      const channel = value / 255;
      return channel <= 0.04045
        ? channel / 12.92
        : Math.pow((channel + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  }
  function ratio(first, second) {
    const a = luminance(first);
    const b = luminance(second);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  }
  function effectiveBackground(element) {
    let current = element;
    while (current) {
      const color = parseColor(getComputedStyle(current).backgroundColor);
      if (color && color.a > 0.95) return color;
      current = current.parentElement;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  }
  const samples = [];
  for (const element of document.querySelectorAll("body *")) {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const text = Array.from(element.childNodes)
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent.trim())
      .join(" ")
      .trim();
    if (
      !text ||
      rect.width < 2 ||
      rect.height < 2 ||
      style.visibility === "hidden" ||
      style.display === "none" ||
      Number(style.opacity) < 0.5
    ) continue;
    const foreground = parseColor(style.color);
    if (!foreground) continue;
    const background = effectiveBackground(element);
    samples.push({
      text: text.slice(0, 80),
      tag: element.tagName,
      ratio: Number(ratio(foreground, background).toFixed(2)),
      foreground: style.color,
      background: `rgb(${background.r}, ${background.g}, ${background.b})`,
      fontSize: style.fontSize,
    });
  }
  samples.sort((left, right) => left.ratio - right.ratio);
  return samples.slice(0, 12);
}
"""


async def audit_viewport(
    page,
    *,
    url: str,
    width: int,
    height: int,
    output: Path,
    run_demo: bool = False,
) -> dict:
    await page.set_viewport_size({"width": width, "height": height})
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(1200)
    if run_demo:
        await page.get_by_role("button", name="开始运行岗位 Agent").click()
        await page.wait_for_timeout(5000)
    screenshot = output / f"desktop-{width}.png"
    await page.screenshot(path=screenshot, full_page=True)
    layout = await page.evaluate(
        """
        () => {
          const config = document.querySelector(
            'div[data-testid="stHorizontalBlock"]:has(.ui-workspace-config) > div:first-child'
          );
          const result = document.querySelector(".ui-result-shell");
          const configBox = config ? config.getBoundingClientRect() : null;
          const resultBox = result ? result.getBoundingClientRect() : null;
          return {
            viewportWidth: document.documentElement.clientWidth,
            scrollWidth: document.documentElement.scrollWidth,
            bodyScrollWidth: document.body.scrollWidth,
            config: configBox
              ? { x: configBox.x, y: configBox.y, width: configBox.width, height: configBox.height }
              : null,
            result: resultBox
              ? { x: resultBox.x, y: resultBox.y, width: resultBox.width, height: resultBox.height }
              : null,
            title: document.title,
          };
        }
        """
    )
    contrast = await page.evaluate(CONTRAST_SCRIPT)
    return {
        "screenshot": str(screenshot),
        "layout": layout,
        "contrast": contrast,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-demo", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        desktop = await audit_viewport(
            page,
            url=args.url,
            width=1440,
            height=1000,
            output=output,
            run_demo=args.run_demo,
        )
        mobile = await audit_viewport(
            page,
            url=args.url,
            width=390,
            height=844,
            output=output,
        )
        await browser.close()
    print(json.dumps({"desktop": desktop, "mobile": mobile}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

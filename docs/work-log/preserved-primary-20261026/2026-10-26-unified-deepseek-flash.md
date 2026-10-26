# Unified DeepSeek Flash — 2026-10-26

Text extraction, semantic matching, structured planning and screenshot recovery now default to `deepseek-flash` with the existing `DEEPSEEK_API_KEY`. Qwen integrations remain removed; historical evidence is unchanged.

## Operation

The Streamlit toggle “启用截图视觉恢复” and CLI `--visual-extractor-provider deepseek` enable text-first recovery. The sibling visual package provides the DeepSeek image_url client, default factory and real single/batch CLI. Demo mode skips remote model builders.

Weak/invalid text or a typed recoverable HTTP read failure can proceed to screenshots. HTTP 404/410 and explicit removal notices remain excluded. Successful recovery clears terminal page failures while preserving a recovered diagnostic. Hybrid tools can recover empty JS shells, preserve canonical job URLs and record `deepseek-vision` provenance. Browser resources are closed on success and failure.

## Verification

- Main project: 732 passed, 1 skipped; coverage 88.31% (70% gate passed).
- Visual project: 64 passed.
- Real API smoke: a synthetic local JS page exposes no readable HTTP text; Playwright renders it and `deepseek-flash` recovers AI Agent Intern / DeepSeek Vision Test / Remote / Python, Playwright, LLM. One recovery attempt succeeded; failed_pages=0.
- Smoke script: `scripts/smoke_deepseek_vision.py` (opt-in real API; two vision calls per run).
- API and JSON validation tests run without live credentials; provider errors do not expose key/image payloads.
- Current production code and active scripts contain no Qwen or DashScope integration. Existing uncommitted work is preserved; no commits were created.

Official API references: https://api-docs.deepseek.com/guides/vision/ and https://api-docs.deepseek.com/guides/json_mode/ .

Independent review is complete: HTTP-shell recovery, removal guards and bounded Hybrid recovery were verified. Final changes pass lint and both project suites.

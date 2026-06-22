from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Callable
from urllib import request

from web_task_agent.models import BrowserPage


LlmTransport = Callable[[str, dict[str, str], dict[str, Any], int], dict[str, Any]]


class LlmExtractorConfigurationError(RuntimeError):
    """Raised when an external LLM extractor is not configured."""


@dataclass(frozen=True)
class LlmProviderConfig:
    provider: str
    model: str
    api_key: str
    base_url: str


PROVIDER_DEFAULTS = {
    "deepseek": {
        "model": "deepseek-v4-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
    },
    "qwen": {
        "model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
}


def build_llm_provider_config(
    *,
    provider: str,
    model: str | None,
) -> LlmProviderConfig:
    provider_key = provider.strip().lower()
    defaults = PROVIDER_DEFAULTS.get(provider_key)
    if defaults is None:
        supported = ", ".join(sorted(PROVIDER_DEFAULTS))
        raise LlmExtractorConfigurationError(
            f"Unsupported LLM extractor provider: {provider}. Supported: {supported}."
        )

    api_key_env = defaults["api_key_env"]
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        raise LlmExtractorConfigurationError(
            f"{api_key_env} is required for {provider_key} LLM extraction."
        )

    return LlmProviderConfig(
        provider=provider_key,
        model=model or defaults["model"],
        api_key=api_key,
        base_url=defaults["base_url"],
    )


def build_configured_llm_field_extractor(
    *,
    provider: str,
    model: str | None = None,
) -> "OpenAiCompatibleLlmFieldExtractor":
    config = build_llm_provider_config(provider=provider, model=model)
    return OpenAiCompatibleLlmFieldExtractor(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
    )


def build_configured_llm_matcher(
    *,
    provider: str,
    model: str | None = None,
) -> "OpenAiCompatibleSemanticMatcher":
    config = build_llm_provider_config(provider=provider, model=model)
    return OpenAiCompatibleSemanticMatcher(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
    )


class OpenAiCompatibleLlmFieldExtractor:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: int = 60,
        transport: LlmTransport | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or PROVIDER_DEFAULTS[provider]["base_url"]).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or self._urllib_transport

    def __call__(self, page: BrowserPage) -> dict[str, str]:
        response = self.transport(
            f"{self.base_url}/chat/completions",
            self._headers(),
            self._payload(page),
            self.timeout_seconds,
        )
        content = self._response_content(response)
        parsed = json.loads(content)
        return {
            "title": str(parsed.get("title", "")).strip(),
            "company": str(parsed.get("company", "")).strip(),
            "location": str(parsed.get("location", "")).strip(),
            "requirements": self._string_or_join(parsed.get("requirements", "")),
            "responsibilities": self._string_or_join(
                parsed.get("responsibilities", "")
            ),
            "posted_at": str(parsed.get("posted_at", "")).strip(),
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, page: BrowserPage) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract job posting fields for a recruiting workflow. "
                        "Return only valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Return only valid JSON with string fields: title, company, "
                        "location, requirements, responsibilities, posted_at.\n\n"
                        f"URL: {page.url}\nTitle: {page.title}\nContent:\n{page.content}"
                    ),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    def _response_content(self, response: dict[str, Any]) -> str:
        choices = response.get("choices", [])
        if not choices:
            raise ValueError("LLM response did not include choices.")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM response did not include message content.")
        return content

    def _string_or_join(self, value: Any) -> str:
        if isinstance(value, list):
            return ", ".join(str(item).strip() for item in value if str(item).strip())
        return str(value).strip()

    def _urllib_transport(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)


# ─── Semantic matcher ────────────────────────────────────────────────


LlmMatcher = Callable[[dict[str, str]], dict[str, object]]


class OpenAiCompatibleSemanticMatcher:
    """Calls an OpenAI-compatible LLM to score how well a job matches a user profile.

    Rule-based matching is fast and testable but cannot understand:
    - "FastAPI" ≈ "API development" ≈ "backend framework"
    - "built browser agents" ≈ "Playwright automation experience"
    - "prototype RAG workflows" ≈ "LangChain retrieval projects"

    This matcher sends the job description and user profile to an LLM with a
    structured output schema, so it can capture semantic overlap that string
    matching misses.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: int = 60,
        transport: LlmTransport | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or PROVIDER_DEFAULTS[provider]["base_url"]).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or self._urllib_transport

    def __call__(self, payload: dict[str, str]) -> dict[str, object]:
        response = self.transport(
            f"{self.base_url}/chat/completions",
            self._headers(),
            self._match_payload(payload),
            self.timeout_seconds,
        )
        content = self._response_content(response)
        parsed = json.loads(content)
        return {
            "score": float(parsed.get("score", 0)),
            "matched_skills": self._string_list(parsed.get("matched_skills", [])),
            "missing_skills": self._string_list(parsed.get("missing_skills", [])),
            "reason": str(parsed.get("reason", "")).strip(),
            "priority": str(parsed.get("priority", "medium")).strip(),
            "suggested_actions": self._string_list(parsed.get("suggested_actions", [])),
        }

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _match_payload(self, fields: dict[str, str]) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a recruiting skill matcher. Compare the user profile against "
                        "a job posting and return a JSON object with these fields:\n"
                        "- score: float 0-1, how well the user fits\n"
                        "- matched_skills: array of strings, user skills/experience that match\n"
                        "- missing_skills: array of strings, job requirements the user lacks\n"
                        "- reason: short explanation in Chinese\n"
                        "- priority: 'high', 'medium', or 'low'\n"
                        "- suggested_actions: array of strings, concrete steps to close the gap\n"
                        "Consider semantic similarity (e.g. 'FastAPI' ≈ 'API development'), "
                        "transferable skills, and project experience overlaps."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "User skills: " + fields.get("user_skills", "") + "\n"
                        "User resume: " + fields.get("user_resume", "") + "\n"
                        "---\n"
                        "Job title: " + fields.get("job_title", "") + "\n"
                        "Job company: " + fields.get("job_company", "") + "\n"
                        "Job requirements: " + fields.get("job_requirements", "") + "\n"
                        "Job responsibilities: " + fields.get("job_responsibilities", "") + "\n"
                        "Job skills: " + fields.get("job_skills", "") + "\n"
                    ),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    def _response_content(self, response: dict[str, Any]) -> str:
        choices = response.get("choices", [])
        if not choices:
            raise ValueError("LLM response did not include choices.")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM response did not include message content.")
        return content

    def _string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _urllib_transport(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)


class DemoLlmMatcher:
    """Deterministic stand-in for an LLM semantic matching call.

    Matches when the job skills overlap the user signal with at least one
    keyword, and produces richer explanations than the rule matcher.
    """

    def __call__(self, payload: dict[str, str]) -> dict[str, object]:
        import re

        user_signal = (
            payload.get("user_skills", "") + " " + payload.get("user_resume", "")
        ).casefold()
        job_skills_raw = payload.get("job_skills", "").casefold()
        job_reqs = payload.get("job_requirements", "").casefold()
        job_title = payload.get("job_title", "").casefold()

        job_skills = [s.strip() for s in re.split(r"[,\n]+", job_skills_raw) if s.strip()]
        matched: list[str] = []
        missing: list[str] = []
        for skill in job_skills:
            if skill in user_signal:
                matched.append(skill)
            else:
                missing.append(skill)

        score = round(len(matched) / max(len(job_skills), 1), 2)
        priority = "high" if score >= 0.75 else "medium" if score >= 0.4 else "low"

        return {
            "score": score,
            "matched_skills": matched,
            "missing_skills": missing,
            "reason": (
                f"语义分析：岗位 {job_title} 要求 {', '.join(job_skills)}，"
                f"你已掌握 {', '.join(matched) if matched else '暂无匹配技能'}，"
                f"还需补强 {', '.join(missing) if missing else '无需补强'}。"
            ),
            "priority": priority,
            "suggested_actions": [
                f"围绕 {s} 补一个最小可展示项目" for s in missing
            ] if missing else ["匹配度高，优先准备投递材料。"],
        }


class DemoLlmFieldExtractor:
    """Deterministic stand-in for an LLM structured extraction call."""

    def __call__(self, page: BrowserPage) -> dict[str, str]:
        content = " ".join(line.strip() for line in page.content.splitlines() if line.strip())
        return {
            "title": self._title(page, content),
            "company": self._company(content),
            "location": self._location(content),
            "requirements": self._requirements(content),
            "responsibilities": self._responsibilities(content),
        }

    def _title(self, page: BrowserPage, content: str) -> str:
        match = re.search(
            r"\b(?:hiring|role is for)\s+(?:an?\s+)?(.+?)(?:\s+at|\.)",
            content,
            re.I,
        )
        if match:
            return match.group(1).strip()
        return page.title

    def _company(self, content: str) -> str:
        match = re.search(r"\bat\s+([A-Z][A-Za-z0-9 ]+?)(?:\.|\s+This|\s+Candidates|$)", content)
        return match.group(1).strip() if match else "Unknown Company"

    def _location(self, content: str) -> str:
        return "Remote" if re.search(r"\bremote\b", content, re.I) else "Unknown Location"

    def _requirements(self, content: str) -> str:
        match = re.search(r"(?:need|requires?)\s+(.+?)(?:\.|$)", content, re.I)
        if not match:
            return ""
        value = match.group(1).strip()
        value = re.sub(r"\s*,?\s+and\s+", ", ", value)
        return value

    def _responsibilities(self, content: str) -> str:
        match = re.search(
            r"(?:role|you will)\s+(builds?|prototype|ship)\s+(.+?)(?:\.|Candidates|$)",
            content,
            re.I,
        )
        if not match:
            return ""
        verb = match.group(1).strip()
        rest = match.group(2).strip()
        return f"{verb} {rest}"

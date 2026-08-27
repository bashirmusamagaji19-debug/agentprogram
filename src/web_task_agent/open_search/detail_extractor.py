from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from .models import FieldEvidence, VerifiedJob

MAX_DESCRIPTION_CHARS = 8000
MAX_SECTION_CHARS = 4000
MAX_EVIDENCE_SNIPPET_CHARS = 500


class DetailExtractionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_json_ld = False
        self._chunks: list[str] = []
        self._in_title = False
        self._title_chunks: list[str] = []
        self.documents: list[Any] = []
        self.open_graph: dict[str, str] = {}
        self.title = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized_tag = tag.casefold()
        attributes = {name.casefold(): (value or "") for name, value in attrs}
        if normalized_tag == "meta":
            property_name = attributes.get("property", "").casefold()
            if property_name.startswith("og:") and attributes.get("content"):
                self.open_graph[property_name] = attributes["content"].strip()
            return
        if normalized_tag == "title":
            self._in_title = True
            self._title_chunks = []
            return
        if normalized_tag != "script":
            return
        if attributes.get("type", "").casefold() == "application/ld+json":
            self._in_json_ld = True
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._chunks.append(data)
        if self._in_title:
            self._title_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._title_chunks).split())
            self._title_chunks = []
            return
        if normalized_tag != "script" or not self._in_json_ld:
            return
        self._in_json_ld = False
        try:
            self.documents.append(json.loads("".join(self._chunks)))
        except json.JSONDecodeError:
            pass
        self._chunks = []


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() in {"head", "script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"head", "script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(part for item in value if (part := _plain_text(item)))
    if isinstance(value, dict):
        return _plain_text(value.get("name") or value.get("value"))
    raw = str(value).strip()
    if "<" not in raw:
        return " ".join(raw.split())
    parser = _TextParser()
    parser.feed(raw)
    return " ".join(parser.parts)


def _walk_documents(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _walk_documents(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _walk_documents(value["@graph"])


def _is_job_posting(value: dict[str, Any]) -> bool:
    types = value.get("@type", [])
    if isinstance(types, str):
        types = [types]
    return any(str(item).casefold() == "jobposting" for item in types)


def _location(posting: dict[str, Any]) -> str:
    if str(posting.get("jobLocationType", "")).casefold() == "telecommute":
        return "Remote"
    locations = posting.get("jobLocation", [])
    if isinstance(locations, dict):
        locations = [locations]
    values: list[str] = []
    for location in locations if isinstance(locations, list) else []:
        if not isinstance(location, dict):
            continue
        address = location.get("address", location)
        if isinstance(address, str):
            values.append(_plain_text(address))
            continue
        if not isinstance(address, dict):
            continue
        parts = [
            _plain_text(address.get("addressLocality")),
            _plain_text(address.get("addressRegion")),
            _plain_text(address.get("addressCountry")),
        ]
        formatted = ", ".join(part for part in parts if part)
        if formatted:
            values.append(formatted)
    return " / ".join(dict.fromkeys(values))


def _skills(value: Any) -> list[str]:
    if isinstance(value, list):
        items = [_plain_text(item) for item in value]
    else:
        text = _plain_text(value)
        items = [part.strip() for part in text.replace(";", ",").split(",")]
    return list(dict.fromkeys(item for item in items if item))


def _greenhouse_fields(parser: _JsonLdParser, page_html: str, page_url: str) -> dict[str, str]:
    host = (urlparse(page_url).hostname or "").casefold()
    if not (host == "greenhouse.io" or host.endswith(".greenhouse.io")):
        return {}
    title = parser.open_graph.get("og:title", "")
    location = parser.open_graph.get("og:description", "")
    title_prefix = f"Job Application for {title} at "
    page_title = parser.title
    company = (
        page_title[len(title_prefix) :].strip()
        if page_title.casefold().startswith(title_prefix.casefold())
        else ""
    )
    return {
        "title": title,
        "company": company,
        "location": location,
        "employment_type": "",
        "responsibilities": "",
        "requirements": "",
        "description": _plain_text(page_html),
    }


def extract_verified_job(
    page_html: str,
    *,
    page_url: str,
    source_type: str,
    content_hash: str,
) -> VerifiedJob:
    parser = _JsonLdParser()
    parser.feed(page_html)
    posting = next(
        (
            document
            for root in parser.documents
            for document in _walk_documents(root)
            if _is_job_posting(document)
        ),
        None,
    )
    if posting is None:
        fields = _greenhouse_fields(parser, page_html, page_url)
        extraction_method = "greenhouse_open_graph"
    else:
        fields = {
            "title": _plain_text(posting.get("title")),
            "company": _plain_text(posting.get("hiringOrganization")),
            "location": _location(posting),
            "employment_type": _plain_text(posting.get("employmentType")),
            "responsibilities": _plain_text(posting.get("responsibilities")),
            "requirements": _plain_text(
                posting.get("qualifications") or posting.get("experienceRequirements")
            ),
            "description": _plain_text(posting.get("description")),
        }
        extraction_method = "json_ld"
    if not fields:
        raise DetailExtractionError(
            "extraction_incomplete", "detail page has no valid JobPosting JSON-LD"
        )
    fields["description"] = fields["description"][:MAX_DESCRIPTION_CHARS]
    fields["responsibilities"] = fields["responsibilities"][:MAX_SECTION_CHARS]
    fields["requirements"] = fields["requirements"][:MAX_SECTION_CHARS]
    missing = [name for name in ("title", "company", "location") if not fields[name]]
    if missing:
        raise DetailExtractionError(
            "extraction_incomplete",
            f"detail page is missing required fields: {', '.join(missing)}",
        )

    evidence = [
        FieldEvidence(
            field_name=name,
            value=value,
            snippet=value[:MAX_EVIDENCE_SNIPPET_CHARS],
            page_url=page_url,
            content_hash=content_hash,
        )
        for name, value in fields.items()
        if value
    ]
    return VerifiedJob(
        **fields,
        skills=_skills(posting.get("skills")) if posting is not None else [],
        url=page_url,
        source=source_type,
        evidence=evidence,
        metadata={"extraction_method": extraction_method},
    )

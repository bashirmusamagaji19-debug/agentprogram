"""Explicit reader experiments on verified archives or legacy saved pages.

Source loading never fetches pages. The injected reader is deliberately called:
this experiment is distinct from no-model rules/recorded archive replay.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from web_task_agent.evidence_reader import EvidenceJobReader, PageReading, assess_reading
from web_task_agent.extractor import PageExtractor
from web_task_agent.models import BrowserPage
from web_task_agent.search_conditions import job_condition_reasons
from web_task_agent.source_archive import SourceArchive
from web_task_agent.source_provenance import SOURCE_CONTEXT_KEYS, normalize_conditions
from web_task_agent.verifier import JobVerifier


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


_PUBLIC_METADATA = SOURCE_CONTEXT_KEYS | {
    "candidate_id", "snapshot_id", "requested_url", "captured_at", "observed_at",
    "evidence_level", "availability", "body_relative_path", "body_sha256", "input_sha256",
    "cache_age_seconds", "source_context_sha256", "candidate_record_sha256", "snapshot_content_origin",
}


def _public_page(page: BrowserPage) -> BrowserPage:
    # Old BrowserPage metadata was unrestricted; never export private run context.
    metadata = {key: value for key, value in page.metadata.items()
                if key in _PUBLIC_METADATA and (value is None or type(value) in (str, int, float))}
    return page.model_copy(update={"metadata": metadata})


def _discover_manifest(root: Path) -> Path | None:
    if root.is_file():
        if root.name != "manifest.json":
            raise ValueError("source archive input file must be manifest.json")
        return root
    manifests = [root / "manifest.json"] if (root / "manifest.json").is_file() else []
    manifests.extend(root.glob("source-archive/*/manifest.json"))
    if root.name == "source-archive":
        manifests.extend(root.glob("*/manifest.json"))
    manifests = sorted(set(manifests), key=lambda path: path.as_posix())
    if len(manifests) > 1:
        raise ValueError("ambiguous source archives; select an explicit manifest.json")
    return manifests[0] if manifests else None


def _condition_cases(conditions: Mapping[str, Any]) -> list[dict[str, str]]:
    checked = normalize_conditions(conditions)
    keyword = checked.get("keyword")
    locations = checked.get("location", checked.get("locations"))
    if isinstance(locations, str):
        locations = [locations]
    if (not isinstance(keyword, str) or not keyword.strip() or not isinstance(locations, list)
            or not locations or any(not isinstance(city, str) or not city.strip() for city in locations)):
        raise ValueError("reading conditions require keyword and location (or locations)")
    return [{"keyword": keyword, "location": city} for city in locations]


def _row(
    page: BrowserPage, index: int, conditions: Mapping[str, str], *, source_file: str,
    source_bytes: bytes, provenance: dict[str, Any],
) -> dict[str, Any]:
    # Keep the historical reading hash stable. Archive input identity additionally
    # binds provenance/context and is separately retained in provenance.
    key = _hash(json.dumps([page.url, page.title, page.content], ensure_ascii=False))
    try:
        job = PageExtractor().extract(page)
        reasons = JobVerifier().verify(job).reasons + job_condition_reasons(
            conditions["keyword"], conditions["location"], job,
        )
        rule = {"accepted": not reasons, "reasons": reasons, "job": job.model_dump(mode="json")}
    except Exception as error:
        rule = {"accepted": False, "reasons": ["rule_error"], "error_type": type(error).__name__}
    return {
        "case_index": index, "keyword": conditions["keyword"], "location": conditions["location"],
        "url": page.url, "source_file": source_file,
        "source_file_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "body_sha256": _hash(page.content), "input_sha256": key, "input_chars": len(page.content),
        "provenance": provenance, "page": page, "rule": rule,
    }


def _archive_rows(
    root: Path, manifest_path: Path, conditions: Sequence[Mapping[str, str]] | None,
    conditions_mismatch: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    archive = SourceArchive(manifest_path.parent)
    archive.verify()
    manifest = archive.read_manifest()
    base = root.parent if root.is_file() else root
    manifest_ref = manifest_path.relative_to(base).as_posix()
    source = {
        "source_format": "source_archive", "provenance_status": "verified_source_archive",
        "manifest_ref": manifest_ref,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    original = _condition_cases(manifest.conditions)
    requested = original
    if conditions is not None:
        requested = []
        for choice in conditions:
            if set(choice) != {"keyword", "location"}:
                raise ValueError("requested conditions must contain only keyword and location")
            cases = _condition_cases(choice)
            if len(cases) != 1:
                raise ValueError("each requested conditions entry must select one location")
            requested.extend(cases)
        if not requested:
            raise ValueError("requested conditions must not be empty")
    if conditions_mismatch == "error" and any(choice not in original for choice in requested):
        raise ValueError("conditions differ from archive; explicitly select conditions_mismatch='reevaluate'")

    # Content replay selects the last captured successful body for each candidate.
    latest: dict[str, tuple[datetime, int, BrowserPage]] = {}
    for position, sid in enumerate(manifest.snapshot_ids):
        page = archive.read_page(sid)
        candidate_id = page.metadata["candidate_id"]
        item = (datetime.fromisoformat(page.metadata["captured_at"].replace("Z", "+00:00")), position, page)
        if candidate_id not in latest or item[:2] > latest[candidate_id][:2]:
            latest[candidate_id] = item
    rows = []
    for index, choice in enumerate(requested, 1):
        for candidate_id in manifest.candidate_ids:
            if candidate_id not in latest:
                continue  # Discovery failures without bodies are not reading inputs.
            page = latest[candidate_id][2]
            snapshot_path = archive.root / "snapshots" / f"{page.metadata['snapshot_id']}.json"
            metadata = dict(page.metadata)
            archive_hash = metadata.pop("input_sha256")
            provenance = {
                **metadata, **source, "archive_input_sha256": archive_hash,
                "archived_conditions": dict(manifest.conditions), "requested_conditions": dict(choice),
                "conditions_match": choice in original, "conditions_mismatch": conditions_mismatch,
            }
            rows.append(_row(
                page, index, choice, source_file=snapshot_path.relative_to(base).as_posix(),
                source_bytes=snapshot_path.read_bytes(), provenance=provenance,
            ))
    return rows, source


def _legacy_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = {
        "source_format": "legacy_comparison", "provenance_status": "legacy_partial_provenance",
        "manifest_ref": None, "manifest_sha256": None,
    }
    source_report = json.loads((root / "comparison.json").read_bytes().decode("utf-8"))
    if source_report.get("status") != "completed":
        raise ValueError("source comparison must be completed")
    rows = []
    for index, case in enumerate(source_report["cases"], 1):
        source_file = root / f"case-{index}" / "search_fetch" / "pages.json"
        source_bytes = source_file.read_bytes()
        for item in json.loads(source_bytes.decode("utf-8")):
            page = _public_page(BrowserPage.model_validate(item))
            # ComparisonCase also stores skills/source settings, which are not
            # reading gates and do not belong in a public provenance export.
            conditions = _condition_cases({key: case["case"][key] for key in ("keyword", "location")})
            if len(conditions) != 1:
                raise ValueError("legacy case must select one location")
            rows.append(_row(
                page, index, conditions[0], source_file=source_file.relative_to(root).as_posix(),
                source_bytes=source_bytes,
                provenance={**page.metadata, **source, "observed_at": page.metadata.get("observed_at")},
            ))
    return rows, source


def _reader_result(raw: Any) -> dict[str, Any]:
    """Keep only the reader's structured output and safe telemetry, never context."""
    if not isinstance(raw, Mapping):
        raw = {"status": "error", "error_type": "InvalidModelResult"}
    raw_usage = raw.get("usage")
    usage = {key: value for key, value in raw_usage.items()
             if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
             and type(value) is int and value >= 0} if isinstance(raw_usage, Mapping) else {}
    result: dict[str, Any] = {"status": "error", "usage": usage}
    error_type = raw.get("error_type")
    result["error_type"] = (error_type if isinstance(error_type, str)
                            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,119}", error_type)
                            else "ModelReadingError")
    if raw.get("status") == "ok":
        try:
            result["reading"] = PageReading.model_validate(raw.get("reading")).model_dump(mode="json")
        except Exception as error:
            result["error_type"] = type(error).__name__
        else:
            result["status"] = "ok"
            result.pop("error_type")
    elapsed = raw.get("elapsed_seconds")
    if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
        result["elapsed_seconds"] = elapsed
    prompt_hash = raw.get("prompt_sha256")
    if isinstance(prompt_hash, str) and re.fullmatch(r"[0-9a-f]{64}", prompt_hash):
        result["prompt_sha256"] = prompt_hash
    for field, allowed in {
        "thinking_mode": {"disabled", "provider_default"},
        "finish_reason": {"stop", "length", "content_filter"},
    }.items():
        if isinstance(raw.get(field), str) and raw[field] in allowed:
            result[field] = raw[field]
    if type(raw.get("reasoning_tokens")) is int and raw["reasoning_tokens"] >= 0:
        result["reasoning_tokens"] = raw["reasoning_tokens"]
    return result


def _report(report: dict[str, Any], output: Path) -> None:
    _write(output / "replay.json", report)
    lines = [
        "# 同一网页正文：规则与 LLM 阅读对照", "",
        "只使用已保存正文，不重新搜索或抓取。系统通过数不是人工准确率或当前可投岗位数。",
        "模式：reader_experiment；显式调用注入的 reader，可能发起模型请求；不是无模型或 recorded 归档回放。",
        f"来源格式：{report['source_format']}；来源证据：{report['provenance_status']}；"
        f"manifest：{report['manifest_ref']}。",
        "LLM 组增加了页面分类、语义判断和原文证据门禁，比较的是阅读流程；不能只归因于模型抽取。",
        "同一 URL、标题与正文只调用一次模型，分别按原城市条件评估；待复核和错误仍在总数中。", "",
        f"状态：{report['status']}；模型：{report['provider']}/{report['model']}；"
        f"模型调用：{report['model_calls']}；已记录总 token：{report['total_tokens']}。", "",
        "| 城市 | 页面数 | 规则通过 | LLM通过 | LLM拒绝 | LLM待复核 | LLM错误 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| {case['location']} | {case['pages']} | {case['rule_accepted']} | {case['llm_accepted']} | "
            f"{case['llm_rejected']} | {case['llm_review']} | {case['llm_errors']} |"
        )
    lines.extend(["", "## 预先选定的审计样本", ""])
    for control in report.get("audit", []):
        lines.append(
            f"- {control['id']} / {control['location']}：规则通过={control['rule_accepted']}，"
            f"LLM={control['llm_status']}，不误接纳检查={control['not_accepted_check']}；"
            f"页面类型检查={control['page_type_check']}。"
        )
    lines.extend(["", "原始输出、证据和全部判定见 replay.json、readings/ 与 snapshots.json。",
                  "有限的审计样本不代表全体页面均有独立标注；模型 token 为接口返回值，未核对账单。", ""])
    (output / "replay.md").write_text("\n".join(lines), encoding="utf-8")


def _build_report(
    *, rows: list[dict[str, Any]], readings: dict[str, dict[str, Any]], provider: str, model: str,
    controls: list[dict[str, Any]], completed: bool, elapsed: float,
) -> dict[str, Any]:
    evaluated = []
    for row in rows:
        record = {key: value for key, value in row.items() if key != "page"}
        raw = readings.get(row["input_sha256"])
        if raw is None:
            record["llm"] = {"status": "pending", "reasons": [], "jobs": []}
        elif raw["status"] == "error":
            record["llm"] = {"status": "error", "error_type": raw["error_type"], "jobs": []}
        else:
            try:
                record["llm"] = assess_reading(
                    PageReading.model_validate(raw["reading"]), row["page"],
                    keyword=row["keyword"], location=row["location"],
                )
            except Exception as error:
                record["llm"] = {"status": "error", "error_type": type(error).__name__, "jobs": []}
        evaluated.append(record)
    cases = []
    for index in sorted({row["case_index"] for row in rows}):
        batch = [row for row in evaluated if row["case_index"] == index]
        counts = Counter(row["llm"]["status"] for row in batch)
        cases.append({
            "case_index": index, "keyword": batch[0]["keyword"], "location": batch[0]["location"],
            "pages": len(batch), "rule_accepted": sum(row["rule"]["accepted"] for row in batch),
            "llm_accepted": counts["accepted"], "llm_rejected": counts["rejected"],
            "llm_review": counts["review"], "llm_errors": counts["error"], "llm_pending": counts["pending"],
        })
    audit = []
    for control in controls:
        for row in evaluated:
            if control["url_contains"] not in row["url"]:
                continue
            raw = readings.get(row["input_sha256"], {})
            reading = raw.get("reading", {})
            jobs = reading.get("jobs", [])
            audit.append({
                "id": control["id"], "url": row["url"], "location": row["location"],
                "rule_accepted": row["rule"]["accepted"], "llm_status": row["llm"]["status"],
                "not_accepted_check": row["llm"]["status"] in {"rejected", "review"},
                "page_type_check": reading.get("page_type") in control.get("expected_page_types", []),
                "ai_role_check": (jobs[0].get("is_ai_role") == control["expected_semantic_ai_role"]
                                  if jobs and "expected_semantic_ai_role" in control else None),
                "location_conflict_check": (jobs[0].get("location_conflict") is True
                                            if jobs and control.get("expected_location_conflict") else None),
            })
    return {
        "status": "completed" if completed else "running", "provider": provider, "model": model,
        "model_calls": len(readings), "unique_inputs": len({row["input_sha256"] for row in rows}),
        "total_tokens": sum(value.get("usage", {}).get("total_tokens", 0) for value in readings.values()),
        "wall_seconds": round(elapsed, 3), "cases": cases, "audit": audit, "rows": evaluated,
    }


async def run_reading_replay(
    *, input_dir: str | Path, output_dir: str | Path, reader: EvidenceJobReader, provider: str, model: str,
    concurrency: int = 3, max_calls: int = 40, audit_path: str | Path | None = None,
    conditions: Sequence[Mapping[str, str]] | None = None,
    conditions_mismatch: Literal["error", "reevaluate"] = "error",
) -> dict[str, Any]:
    """Run an injected-reader experiment without fetching source content.

    ``input_dir`` accepts an archive root, manifest.json, a container with one
    source-archive/<run_id>/manifest.json, or a legacy comparison directory.
    ``conditions`` is an optional sequence of keyword/location pairs for archive
    inputs. Changed pairs require explicit ``conditions_mismatch='reevaluate'``.
    The reader may call a model; no rules/recorded offline fallback is implied.
    """
    if not 1 <= concurrency <= 4 or max_calls < 1:
        raise ValueError("concurrency must be 1..4 and max_calls must be positive")
    root, output = Path(input_dir), Path(output_dir)
    if conditions_mismatch not in {"error", "reevaluate"}:
        raise ValueError("conditions_mismatch must be error or reevaluate")
    manifest_path = _discover_manifest(root)
    if manifest_path is not None:
        rows, source = _archive_rows(root, manifest_path, conditions, conditions_mismatch)
    else:
        if conditions is not None:
            raise ValueError("conditions overrides require a source archive manifest")
        rows, source = _legacy_rows(root)
    unique: dict[str, BrowserPage] = {}
    for row in rows:
        unique.setdefault(row["input_sha256"], row["page"])
    if not rows or len(unique) > max_calls:
        raise ValueError(f"require nonempty snapshots within max_calls; found {len(unique)} unique inputs")
    controls = json.loads(Path(audit_path).read_text(encoding="utf-8"))["controls"] if audit_path else []
    output.mkdir(parents=True, exist_ok=False)
    (output / "readings").mkdir()
    snapshots = {key: {**page.model_dump(mode="json"), "provenance": []} for key, page in unique.items()}
    for row in rows:
        snapshots[row["input_sha256"]]["provenance"].append(row["provenance"])
    _write(output / "snapshots.json", snapshots)
    _write(output / "audit-controls.json", controls)
    readings: dict[str, dict[str, Any]] = {}
    started = perf_counter()
    semaphore = asyncio.Semaphore(concurrency)

    async def read_one(key: str, page: BrowserPage) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            try:
                result = await asyncio.to_thread(reader.read, page)
            except Exception as error:
                result = {"status": "error", "error_type": type(error).__name__, "usage": {}}
            return key, _reader_result(result)

    def save(completed: bool) -> dict[str, Any]:
        report = _build_report(
            rows=rows, readings=readings, provider=provider, model=model, controls=controls,
            completed=completed, elapsed=perf_counter() - started,
        )
        report.update({"mode": "reader_experiment", **source})
        _report(report, output)
        return report

    save(False)
    tasks = [asyncio.create_task(read_one(key, page)) for key, page in unique.items()]
    for future in asyncio.as_completed(tasks):
        key, result = await future
        readings[key] = result
        _write(output / "readings" / f"{key}.json", result)
        save(False)
        print(f"Read {len(readings)}/{len(unique)}: {result['status']}", flush=True)
    return save(True)

#!/usr/bin/env python3
from __future__ import annotations

import csv
import difflib
import hashlib
import html
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
CAPTURE_ROOT = REPO_ROOT / "captures"
OUT_ROOT = REPO_ROOT / "analysis_result"
DERIVED = OUT_ROOT / "data" / "derived"
RESULTS = OUT_ROOT / "results"
FIGURES = OUT_ROOT / "figures"

DEFAULT_BASE_URL = "https://siflow-changliu.siflow.cn/siflow/changliu/f93ea1119a/qwen36-35ba3b/v1/8000/v1"
DEFAULT_MODEL = "qwen3.6-35ba3b"

CATEGORY_ORDER = [
    "identity_mission",
    "interaction_output",
    "planning_lifecycle",
    "software_engineering_workflow",
    "tool_capability",
    "tool_use_policy",
    "permissions_side_effects",
    "safety_security",
    "environment_sandbox",
    "memory_context",
    "extensibility",
    "multi_agent",
    "reliability_verification",
    "runtime_capture_artifact",
    "uncertain",
]

CATEGORY_LABELS = {
    "identity_mission": "Identity/Mission",
    "interaction_output": "Interaction/Output",
    "planning_lifecycle": "Planning",
    "software_engineering_workflow": "SE Workflow",
    "tool_capability": "Tool Capability",
    "tool_use_policy": "Tool-use Policy",
    "permissions_side_effects": "Permissions",
    "safety_security": "Safety",
    "environment_sandbox": "Environment",
    "memory_context": "Memory",
    "extensibility": "Extensibility",
    "multi_agent": "Multi-agent",
    "reliability_verification": "Reliability",
    "runtime_capture_artifact": "Runtime/Capture",
    "uncertain": "Uncertain",
}

AGENT_ORDER = [
    "claude-code",
    "codex",
    "antigravity",
    "kimi-code",
    "mimo",
    "openclaw",
    "hermes",
    "kimi",
    "opencode",
    "pi",
    "omp",
]

COMPONENT_LABELS = {
    "instruction": "核心 instruction / 自然语言规则",
    "tool_text": "工具说明文本 / capability guidance",
    "tool_schema": "工具 JSON schema",
    "runtime": "运行时环境上下文",
    "capture_artifact": "capture artifact / 合成任务相关文本",
}

VOLATILE_PATTERNS = [
    (re.compile(r"\$PHISTORY_[A-Z_]+"), "$PHISTORY_VAR"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", re.I), "$UUID"),
    (re.compile(r"\b20\d{2}[-/]\d{2}[-/]\d{2}\b"), "$DATE"),
    (re.compile(r"\b20\d{2}-\d{2}-\d{2}T[0-9:.+-]+Z?\b"), "$DATETIME"),
    (re.compile(r"Reply with one short sentence\.?"), "$SYNTHETIC_TASK"),
]


@dataclass
class Snapshot:
    agent_id: str
    agent: str
    version: str
    version_dir: Path
    prompt_path: Path
    trace_path: Path
    meta_path: Path
    published_at: str
    captured_at: str
    tap_client: str
    command: str
    client_exit_code: str
    prompt_sha256: str
    trace_sha256: str
    meta_sha256: str
    static_prompts: bool
    prompt_text: str
    trace_body: Any
    trace_parse_status: str


def main() -> int:
    started = time.time()
    ensure_dirs()

    snapshots = load_snapshots()
    sections_by_snapshot: dict[str, list[dict[str, Any]]] = {}
    components: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    tool_params: list[dict[str, Any]] = []
    clauses: list[dict[str, Any]] = []
    snapshot_rows: list[dict[str, Any]] = []

    for snap in snapshots:
        sid = snapshot_id(snap)
        sections = split_sections(snap.prompt_text)
        sections_by_snapshot[sid] = sections
        trace_tools = extract_tools_from_trace(snap.trace_body)
        prompt_tool_names = extract_prompt_tool_names(snap.prompt_text)
        all_tool_names = sorted(set([t["name"] for t in trace_tools if t.get("name")] + prompt_tool_names))

        component_rows = build_components(snap, sections)
        components.extend(component_rows)
        tool_rows, param_rows = build_tool_rows(snap, trace_tools, all_tool_names)
        tools.extend(tool_rows)
        tool_params.extend(param_rows)
        clause_rows = build_clauses(snap, sections)
        clauses.extend(clause_rows)
        snapshot_rows.append(build_snapshot_row(snap, sections, component_rows, tool_rows, param_rows))

    model_status = maybe_apply_model_classifier(clauses)
    epochs, snapshot_to_epoch = build_epochs(snapshots, sections_by_snapshot, tools)
    change_events, longitudinal = build_change_events_and_metrics(snapshots, clauses, snapshot_rows)
    cross_agent = build_cross_agent_summary(snapshot_rows, clauses, tools, epochs)
    latest_structural = build_latest_structural_comparison(snapshot_rows)
    trend_summary = build_trend_summary(snapshot_rows, longitudinal, cross_agent)
    top_changes = build_top_change_rows(longitudinal)
    major_jumps = build_major_jump_events(snapshots, snapshot_rows)
    category_summary = build_category_summary(clauses)
    similarity_pairs = build_similarity_pairs(cross_agent, tools)
    claims = build_claims(snapshots, snapshot_rows, cross_agent, longitudinal, model_status)

    write_csv(DERIVED / "archive_manifest.csv", [snapshot_manifest_row(s) for s in snapshots])
    write_csv(DERIVED / "snapshots.csv", snapshot_rows)
    write_csv(DERIVED / "components.csv", components)
    write_csv(DERIVED / "clauses.csv", clauses)
    write_csv(DERIVED / "tools.csv", tools)
    write_csv(DERIVED / "tool_parameters.csv", tool_params)
    write_csv(DERIVED / "epochs.csv", epochs)
    write_csv(DERIVED / "snapshot_to_epoch.csv", snapshot_to_epoch)
    write_csv(DERIVED / "change_events.csv", change_events)
    write_csv(RESULTS / "capture_profiles.csv", build_capture_profiles(snapshots))
    write_csv(RESULTS / "longitudinal_metrics.csv", longitudinal)
    write_csv(RESULTS / "cross_agent_summary.csv", cross_agent)
    write_csv(RESULTS / "latest_structural_comparison.csv", latest_structural)
    write_csv(RESULTS / "trend_summary.csv", trend_summary)
    write_csv(RESULTS / "top_change_events.csv", top_changes)
    write_csv(RESULTS / "major_jump_events.csv", major_jumps)
    write_csv(RESULTS / "category_summary.csv", category_summary)
    write_csv(RESULTS / "similarity_pairs.csv", similarity_pairs)
    write_csv(RESULTS / "claims.csv", claims)

    write_json(RESULTS / "model_status.json", model_status)
    write_text(RESULTS / "data_quality_report.md", render_data_quality(snapshots, snapshot_rows, model_status))
    write_text(RESULTS / "codebook.md", render_codebook(model_status))
    write_text(
        OUT_ROOT / "report.md",
        render_report(
            snapshots,
            snapshot_rows,
            cross_agent,
            longitudinal,
            model_status,
            latest_structural,
            trend_summary,
            top_changes,
            major_jumps,
            category_summary,
            similarity_pairs,
        ),
    )
    render_figures(snapshots, snapshot_rows, clauses, cross_agent, longitudinal)

    elapsed = round(time.time() - started, 2)
    print(f"analysis complete: {len(snapshots)} snapshots, {len(clauses)} clauses, {elapsed}s")
    print(f"report: {OUT_ROOT / 'report.md'}")
    return 0


def ensure_dirs() -> None:
    for path in (DERIVED, RESULTS, FIGURES):
        path.mkdir(parents=True, exist_ok=True)


def load_snapshots() -> list[Snapshot]:
    snapshots: list[Snapshot] = []
    for meta_path in sorted(CAPTURE_ROOT.glob("*/*/meta.json")):
        version_dir = meta_path.parent
        prompt_path = version_dir / "prompt.md"
        trace_path = version_dir / "trace.jsonl"
        if not prompt_path.exists() or not trace_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        body, status = read_trace_body(trace_path)
        snapshots.append(
            Snapshot(
                agent_id=str(meta.get("agent_id") or version_dir.parent.name),
                agent=str(meta.get("agent") or meta.get("agent_id") or version_dir.parent.name),
                version=str(meta.get("version") or version_dir.name),
                version_dir=version_dir,
                prompt_path=prompt_path,
                trace_path=trace_path,
                meta_path=meta_path,
                published_at=str(meta.get("published_at") or ""),
                captured_at=str(meta.get("captured_at") or ""),
                tap_client=str(meta.get("tap_client") or ""),
                command=" ".join(str(part) for part in (meta.get("command") or [])),
                client_exit_code=str(meta.get("client_exit_code") if meta.get("client_exit_code") is not None else ""),
                prompt_sha256=file_sha256(prompt_path),
                trace_sha256=file_sha256(trace_path),
                meta_sha256=file_sha256(meta_path),
                static_prompts=(version_dir / "static-prompts.md").exists(),
                prompt_text=prompt_path.read_text(encoding="utf-8", errors="replace"),
                trace_body=body,
                trace_parse_status=status,
            )
        )
    snapshots.sort(key=lambda s: (agent_sort_key(s.agent_id), parse_time(s.published_at) or parse_time(s.captured_at) or datetime.min.replace(tzinfo=timezone.utc), version_key(s.version)))
    return snapshots


def read_trace_body(path: Path) -> tuple[Any, str]:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            request = record.get("request") if isinstance(record, dict) else None
            if isinstance(request, dict):
                return request.get("body"), "ok" if request.get("body") is not None else "missing_body"
            return None, "missing_request"
        return None, "empty_trace"
    except Exception as exc:
        return None, f"error:{type(exc).__name__}"


def split_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current = {"heading": "(preamble)", "level": 0, "start_line": 1, "lines": []}
    in_fence = False
    heading_stack: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match and not in_fence:
            if current["lines"] or current["heading"] != "(preamble)":
                current["content"] = "\n".join(current.pop("lines")).strip()
                current["end_line"] = line_no - 1
                current["heading_path"] = heading_path(heading_stack)
                current["component_type"] = component_type(current["heading"], current["content"], current["heading_path"])
                sections.append(current)
            level = len(match.group(1))
            heading = match.group(2).strip()
            heading_stack = [(lvl, text) for lvl, text in heading_stack if lvl < level]
            heading_stack.append((level, heading))
            current = {"heading": heading, "level": level, "start_line": line_no, "lines": []}
        else:
            current["lines"].append(line)
    current["content"] = "\n".join(current.pop("lines")).strip()
    current["end_line"] = len(text.splitlines())
    current["heading_path"] = heading_path(heading_stack)
    current["component_type"] = component_type(current["heading"], current["content"], current["heading_path"])
    if current["content"] or current["heading"] != "(preamble)":
        sections.append(current)
    return sections


def heading_path(stack: list[tuple[int, str]]) -> str:
    return " > ".join(text for _level, text in stack)


def component_type(heading: str, content: str, path: str) -> str:
    value = f"{heading}\n{path}\n{content[:1000]}".lower()
    if "reply with one short sentence" in value or "# user message" in value or "synthetic_task" in value:
        return "capture_artifact"
    heading_low = heading.lower().strip()
    path_low = path.lower().strip()
    if heading_low in {"system prompt", "system", "developer message", "developer prompt"}:
        return "instruction"
    if path_low == "tools" or path_low.startswith("tools >"):
        return "tool"
    if re.search(r"\b(tool schemas?|functions?)\b", value):
        return "tool"
    if any(marker in value for marker in ("environment", "sandbox", "workspace", "cwd", "os version", "shell", "current_date", "timezone", "user_information")):
        return "runtime"
    if "$phistory_" in value or "conversation id" in value:
        return "runtime"
    return "instruction"


def extract_tools_from_trace(body: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if not isinstance(body, dict):
        return tools
    candidates = []
    if isinstance(body.get("tools"), list):
        candidates.extend(body["tools"])
    request = body.get("request")
    if isinstance(request, dict) and isinstance(request.get("tools"), list):
        candidates.extend(request["tools"])
    for index, tool in enumerate(candidates):
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        name = tool.get("name") or function.get("name") or tool.get("type") or f"tool_{index}"
        description = tool.get("description") or function.get("description") or ""
        schema = (
            tool.get("input_schema")
            or tool.get("parameters")
            or function.get("parameters")
            or tool.get("schema")
            or {}
        )
        tools.append(
            {
                "name": str(name),
                "description": str(description or ""),
                "schema": schema if isinstance(schema, dict) else {},
                "raw": tool,
            }
        )
    return tools


def extract_prompt_tool_names(text: str) -> list[str]:
    names: list[str] = []
    in_tools = False
    seen_tool_heading = False
    for line in text.splitlines():
        if re.match(r"^# +Tools\b", line, re.I):
            in_tools = True
            seen_tool_heading = False
            continue
        if in_tools and re.match(r"^# +", line):
            break
        if not in_tools:
            continue
        match = re.match(r"^## +(.+)$", line)
        if match:
            seen_tool_heading = True
            names.append(match.group(1).strip())
            continue
        if not seen_tool_heading:
            match = re.match(r"^- +`?([A-Za-z0-9_.:-]+)`?:", line)
            if match:
                names.append(match.group(1).strip())
    return sorted(set(names))


def build_components(snap: Snapshot, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, section in enumerate(sections):
        content = section.get("content", "")
        rows.append(
            {
                "snapshot_id": snapshot_id(snap),
                "agent_id": snap.agent_id,
                "version": snap.version,
                "published_at": snap.published_at,
                "source_order": index,
                "component_type": section["component_type"],
                "heading": section["heading"],
                "heading_path": section["heading_path"],
                "start_line": section["start_line"],
                "end_line": section["end_line"],
                "char_count": len(content),
                "line_count": len(content.splitlines()) if content else 0,
                "content_hash": stable_hash(normalize_text(content)),
                "content": content,
            }
        )
    return rows


def build_tool_rows(
    snap: Snapshot, trace_tools: list[dict[str, Any]], all_tool_names: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_name = {tool["name"]: tool for tool in trace_tools}
    tool_rows: list[dict[str, Any]] = []
    param_rows: list[dict[str, Any]] = []
    for index, name in enumerate(all_tool_names):
        tool = by_name.get(name, {"description": "", "schema": {}, "raw": {}})
        schema = tool.get("schema") if isinstance(tool.get("schema"), dict) else {}
        props = schema.get("properties") if isinstance(schema, dict) else {}
        required = schema.get("required") if isinstance(schema, dict) else []
        if not isinstance(props, dict):
            props = {}
        if not isinstance(required, list):
            required = []
        tool_rows.append(
            {
                "snapshot_id": snapshot_id(snap),
                "agent_id": snap.agent_id,
                "version": snap.version,
                "published_at": snap.published_at,
                "source_order": index,
                "tool_name": name,
                "description_chars": len(str(tool.get("description") or "")),
                "schema_chars": len(json.dumps(schema, sort_keys=True, ensure_ascii=False)),
                "parameter_count": len(props),
                "required_parameter_count": len(required),
                "source": "trace" if name in by_name else "prompt_text",
                "description": str(tool.get("description") or ""),
                "schema_json": json.dumps(schema, sort_keys=True, ensure_ascii=False),
            }
        )
        for param_name, param_schema in props.items():
            param_rows.append(
                {
                    "snapshot_id": snapshot_id(snap),
                    "agent_id": snap.agent_id,
                    "version": snap.version,
                    "tool_name": name,
                    "parameter_name": str(param_name),
                    "required": str(param_name in required).lower(),
                    "parameter_type": str(param_schema.get("type") if isinstance(param_schema, dict) else ""),
                    "description": str(param_schema.get("description") if isinstance(param_schema, dict) else ""),
                }
            )
    return tool_rows, param_rows


def build_clauses(snap: Snapshot, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    order = 0
    for section in sections:
        if section["component_type"] not in ("instruction", "runtime"):
            continue
        for line_no, clause in segment_clauses(section.get("content", ""), int(section["start_line"])):
            normalized = normalize_text(clause)
            if len(normalized) < 20:
                continue
            category, method = classify_rule(clause, section["component_type"], section["heading_path"])
            rows.append(
                {
                    "snapshot_id": snapshot_id(snap),
                    "agent_id": snap.agent_id,
                    "version": snap.version,
                    "published_at": snap.published_at,
                    "source_order": order,
                    "line_number": line_no,
                    "heading_path": section["heading_path"],
                    "component_type": section["component_type"],
                    "clause_hash": stable_hash(normalized),
                    "normalized_hash": stable_hash(normalized.lower()),
                    "char_count": len(clause),
                    "word_count": len(re.findall(r"[A-Za-z][A-Za-z'-]+", clause)),
                    "category": category,
                    "classification_method": method,
                    "classification_confidence": "0.90" if method == "rule" else "0.40",
                    "text": clause,
                }
            )
            order += 1
    return rows


def segment_clauses(content: str, start_line: int) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    paragraph: list[str] = []
    paragraph_start = start_line
    in_fence = False
    for offset, line in enumerate(content.splitlines(), start=0):
        line_no = start_line + offset
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        bullet = re.match(r"^(?:[-*]|\d+\.)\s+(.+)$", stripped)
        if bullet:
            if paragraph:
                rows.extend(split_sentence_clause(" ".join(paragraph), paragraph_start))
                paragraph = []
            rows.extend(split_sentence_clause(bullet.group(1), line_no))
            continue
        if not stripped:
            if paragraph:
                rows.extend(split_sentence_clause(" ".join(paragraph), paragraph_start))
                paragraph = []
            continue
        if not paragraph:
            paragraph_start = line_no
        paragraph.append(stripped)
    if paragraph:
        rows.extend(split_sentence_clause(" ".join(paragraph), paragraph_start))
    return rows


def split_sentence_clause(text: str, line_no: int) -> list[tuple[int, str]]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= 280:
        return [(line_no, text)]
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z`<])", text)
    out = [(line_no, piece.strip()) for piece in pieces if len(piece.strip()) >= 20]
    return out or [(line_no, text)]


def classify_rule(text: str, component: str, heading_path: str) -> tuple[str, str]:
    low = f"{heading_path}\n{text}".lower()
    if component == "runtime" or "$phistory" in low or "reply with one short sentence" in low:
        return "runtime_capture_artifact", "rule"
    checks = [
        ("safety_security", ("security", "malicious", "refuse", "credential", "privacy", "exploit", "harmful", "dual-use", "prompt injection")),
        ("permissions_side_effects", ("permission", "approval", "confirm", "ask before", "delete", "overwrite", "destructive", "irreversible", "side effect", "external service", "publish")),
        ("tool_capability", ("tool schema", "parameters", "available tools", "tool descriptions", "function", "input_schema")),
        ("tool_use_policy", ("use the tool", "tool call", "parallel", "fallback", "prefer the", "when using", "do not use shell", "call exactly")),
        ("memory_context", ("memory", "remember", "session", "context management", "summarized", "conversation grows", "persistent")),
        ("extensibility", ("skill", "plugin", "mcp", "agents.md", "project instructions", "custom instruction")),
        ("multi_agent", ("subagent", "sub-agent", "delegate", "spawn", "multi-agent", "another agent")),
        ("planning_lifecycle", ("plan", "todo", "task list", "blocked", "complete", "in_progress", "lifecycle", "progress update")),
        ("reliability_verification", ("verify", "test", "validation", "fact", "evidence", "fail", "error", "report outcomes", "run tests")),
        ("environment_sandbox", ("sandbox", "workspace", "cwd", "shell", "network", "filesystem", "home", "directory", "os version", "environment")),
        ("software_engineering_workflow", ("code", "files", "git", "commit", "lint", "build", "review", "patch", "repository", "tests")),
        ("interaction_output", ("respond", "answer", "final", "markdown", "concise", "tone", "user-visible", "progress", "communicate", "format")),
        ("identity_mission", ("you are", "assistant", "agent", "mission", "role", "designed by", "built on")),
    ]
    for category, needles in checks:
        if any(needle in low for needle in needles):
            return category, "rule"
    return "uncertain", "rule_uncertain"


def maybe_apply_model_classifier(clauses: list[dict[str, Any]]) -> dict[str, Any]:
    use_model = os.environ.get("PHISTORY_ANALYSIS_USE_MODEL", "").lower() in {"1", "true", "yes"}
    base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("MODEL_NAME", DEFAULT_MODEL)
    limit = int(os.environ.get("PHISTORY_ANALYSIS_MODEL_LIMIT", "120"))
    cache_path = RESULTS / "classification_cache.jsonl"
    status = {
        "enabled": use_model,
        "base_url": base_url,
        "model": model,
        "limit": limit,
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "cache_path": rel(cache_path),
        "note": "",
    }
    if not use_model:
        status["note"] = "model classifier disabled; rule-only labels used"
        return status

    cache = read_model_cache(cache_path)
    candidates: dict[str, dict[str, Any]] = {}
    for row in clauses:
        if row["category"] == "uncertain" and 30 <= len(row["text"]) <= 1200:
            candidates.setdefault(row["normalized_hash"], row)
    ordered = sorted(candidates.values(), key=lambda row: (-int(row["word_count"]), row["agent_id"]))[:limit]
    updates: dict[str, tuple[str, str]] = {}
    for row in ordered:
        key = model_cache_key(model, row["normalized_hash"])
        if key in cache:
            label = cache[key].get("label", "uncertain")
            confidence = str(cache[key].get("confidence", "0.0"))
            updates[row["normalized_hash"]] = (label if label in CATEGORY_ORDER else "uncertain", confidence)
            status["succeeded"] += 1
            continue
        status["attempted"] += 1
        try:
            result = call_model_classifier(base_url, model, row["text"])
        except Exception as exc:
            status["failed"] += 1
            status["note"] = f"model classifier encountered errors; last={type(exc).__name__}: {exc}"
            continue
        cache[key] = result
        label = result.get("label", "uncertain")
        confidence = str(result.get("confidence", "0.0"))
        updates[row["normalized_hash"]] = (label if label in CATEGORY_ORDER else "uncertain", confidence)
        status["succeeded"] += 1
        append_jsonl(cache_path, {"key": key, **result})

    for row in clauses:
        update = updates.get(row["normalized_hash"])
        if update:
            row["category"], row["classification_confidence"] = update
            row["classification_method"] = "qwen_model"
    if not status["note"]:
        status["note"] = "model classifier completed for selected uncertain unique clauses"
    return status


def read_model_cache(path: Path) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return cache
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("key"):
            cache[str(row["key"])] = row
    return cache


def call_model_classifier(base_url: str, model: str, text: str) -> dict[str, Any]:
    labels = ", ".join(CATEGORY_ORDER)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Return only compact JSON. Do not include markdown.",
            },
            {
                "role": "user",
                "content": (
                    "Classify the coding-agent prompt clause into exactly one label from this list: "
                    f"{labels}.\n"
                    "Return JSON with fields label, confidence, rationale_short.\n"
                    f"Clause: {text}"
                ),
            },
        ],
        "temperature": 0,
        "max_tokens": 160,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = json.load(response)
    content = raw["choices"][0]["message"].get("content") or ""
    parsed = parse_json_object(content)
    parsed["raw_content"] = content
    parsed["created_at"] = datetime.now(timezone.utc).isoformat()
    return parsed


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            return json.loads(match.group(0))
    return {"label": "uncertain", "confidence": 0.0, "rationale_short": "unparseable model output"}


def build_snapshot_row(
    snap: Snapshot,
    sections: list[dict[str, Any]],
    components: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    params: list[dict[str, Any]],
) -> dict[str, Any]:
    chars_by_type = Counter()
    for row in components:
        chars_by_type[row["component_type"]] += int(row["char_count"])
    instruction_text = "\n".join(row["content"] for row in components if row["component_type"] == "instruction")
    prompt = snap.prompt_text
    return {
        "snapshot_id": snapshot_id(snap),
        "agent_id": snap.agent_id,
        "agent": snap.agent,
        "version": snap.version,
        "published_at": snap.published_at,
        "captured_at": snap.captured_at,
        "prompt_chars": len(prompt),
        "prompt_lines": len(prompt.splitlines()),
        "instruction_chars": chars_by_type["instruction"],
        "tool_prompt_chars": chars_by_type["tool"],
        "runtime_chars": chars_by_type["runtime"],
        "capture_artifact_chars": chars_by_type["capture_artifact"],
        "section_count": sum(1 for section in sections if section["heading"] != "(preamble)"),
        "tool_count": len(tools),
        "tool_description_chars": sum(int(row["description_chars"]) for row in tools),
        "tool_schema_chars": sum(int(row["schema_chars"]) for row in tools),
        "tool_parameter_count": len(params),
        "required_tool_parameter_count": sum(1 for row in params if row["required"] == "true"),
        "must_density_per_1k": density(instruction_text, r"\b(must|required|always)\b"),
        "should_density_per_1k": density(instruction_text, r"\b(should|prefer|recommended)\b"),
        "never_density_per_1k": density(instruction_text, r"\b(never|do not|don't|refuse)\b"),
        "confirmation_density_per_1k": density(instruction_text, r"\b(confirm|approval|ask before|permission)\b"),
        "verification_density_per_1k": density(instruction_text, r"\b(test|verify|validation|evidence|check)\b"),
        "trace_parse_status": snap.trace_parse_status,
        "static_prompts": str(snap.static_prompts).lower(),
    }


def build_epochs(
    snapshots: list[Snapshot], sections_by_snapshot: dict[str, list[dict[str, Any]]], tools: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tools_by_snapshot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tools:
        tools_by_snapshot[row["snapshot_id"]].append(row)
    epochs: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    for plane in ("instruction", "tool", "runtime", "whole"):
        for agent_id, agent_snaps in group_by_agent(snapshots).items():
            previous_hash = None
            epoch_index = 0
            epoch_start: Snapshot | None = None
            epoch_members: list[Snapshot] = []
            for snap in agent_snaps:
                sid = snapshot_id(snap)
                content_hash = plane_hash(plane, snap, sections_by_snapshot[sid], tools_by_snapshot.get(sid, []))
                if content_hash != previous_hash:
                    if epoch_start is not None:
                        epochs.append(epoch_row(plane, agent_id, epoch_index, epoch_start, epoch_members, previous_hash or ""))
                    epoch_index += 1
                    epoch_start = snap
                    epoch_members = []
                    previous_hash = content_hash
                epoch_members.append(snap)
                mapping.append(
                    {
                        "snapshot_id": sid,
                        "agent_id": agent_id,
                        "version": snap.version,
                        "plane": plane,
                        "epoch_index": epoch_index,
                        "epoch_hash": content_hash,
                    }
                )
            if epoch_start is not None:
                epochs.append(epoch_row(plane, agent_id, epoch_index, epoch_start, epoch_members, previous_hash or ""))
    return epochs, mapping


def plane_hash(plane: str, snap: Snapshot, sections: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
    if plane == "whole":
        return stable_hash(normalize_text(snap.prompt_text))
    if plane == "tool":
        payload = [
            {
                "name": row["tool_name"],
                "schema": row["schema_json"],
                "description": normalize_text(row["description"]),
            }
            for row in sorted(tools, key=lambda item: item["tool_name"])
        ]
        return stable_hash(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    content = "\n".join(
        section.get("content", "")
        for section in sections
        if section["component_type"] == ("runtime" if plane == "runtime" else "instruction")
    )
    return stable_hash(normalize_text(content))


def epoch_row(
    plane: str, agent_id: str, epoch_index: int, start: Snapshot, members: list[Snapshot], epoch_hash: str
) -> dict[str, Any]:
    end = members[-1]
    days = days_between(start.published_at or start.captured_at, end.published_at or end.captured_at)
    return {
        "plane": plane,
        "agent_id": agent_id,
        "epoch_index": epoch_index,
        "epoch_hash": epoch_hash,
        "start_version": start.version,
        "end_version": end.version,
        "start_date": (start.published_at or start.captured_at)[:10],
        "end_date": (end.published_at or end.captured_at)[:10],
        "snapshot_count": len(members),
        "duration_days": days,
    }


def build_change_events_and_metrics(
    snapshots: list[Snapshot], clauses: list[dict[str, Any]], snapshot_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clauses_by_snapshot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in clauses:
        clauses_by_snapshot[row["snapshot_id"]].append(row)
    snapshot_by_id = {row["snapshot_id"]: row for row in snapshot_rows}
    change_events: list[dict[str, Any]] = []
    longitudinal: list[dict[str, Any]] = []
    prompt_text_by_id = {snapshot_id(s): s.prompt_text for s in snapshots}
    for agent_id, agent_snaps in group_by_agent(snapshots).items():
        previous: Snapshot | None = None
        for snap in agent_snaps:
            sid = snapshot_id(snap)
            row = snapshot_by_id[sid]
            added = removed = moved = 0
            category_churn = Counter()
            churn = 0.0
            if previous is not None:
                prev_id = snapshot_id(previous)
                prev_clauses = clauses_by_snapshot[prev_id]
                curr_clauses = clauses_by_snapshot[sid]
                prev_hashes = {c["normalized_hash"]: c for c in prev_clauses}
                curr_hashes = {c["normalized_hash"]: c for c in curr_clauses}
                for digest, clause in curr_hashes.items():
                    if digest not in prev_hashes:
                        added += 1
                        category_churn[clause["category"]] += 1
                        change_events.append(change_row(snap, previous, "add", clause))
                    elif str(clause["source_order"]) != str(prev_hashes[digest]["source_order"]):
                        moved += 1
                        change_events.append(change_row(snap, previous, "move", clause))
                for digest, clause in prev_hashes.items():
                    if digest not in curr_hashes:
                        removed += 1
                        category_churn[clause["category"]] += 1
                        change_events.append(change_row(snap, previous, "remove", clause))
                churn = normalized_churn(prompt_text_by_id[prev_id], prompt_text_by_id[sid])
            longitudinal.append(
                {
                    "snapshot_id": sid,
                    "agent_id": agent_id,
                    "version": snap.version,
                    "published_at": snap.published_at,
                    "prompt_chars": row["prompt_chars"],
                    "instruction_chars": row["instruction_chars"],
                    "tool_schema_chars": row["tool_schema_chars"],
                    "runtime_chars": row["runtime_chars"],
                    "tool_count": row["tool_count"],
                    "added_clauses": added,
                    "removed_clauses": removed,
                    "moved_clauses": moved,
                    "normalized_churn": round(churn, 6),
                    **{f"churn_{cat}": category_churn[cat] for cat in CATEGORY_ORDER},
                }
            )
            previous = snap
    return change_events, longitudinal


def change_row(snap: Snapshot, previous: Snapshot, change_type: str, clause: dict[str, Any]) -> dict[str, Any]:
    return {
        "agent_id": snap.agent_id,
        "previous_version": previous.version,
        "version": snap.version,
        "published_at": snap.published_at,
        "change_type": change_type,
        "category": clause["category"],
        "clause_hash": clause["normalized_hash"],
        "heading_path": clause["heading_path"],
        "text": clause["text"],
    }


def build_cross_agent_summary(
    snapshot_rows: list[dict[str, Any]], clauses: list[dict[str, Any]], tools: list[dict[str, Any]], epochs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    latest = latest_rows(snapshot_rows)
    clauses_by_agent = defaultdict(list)
    for row in clauses:
        clauses_by_agent[row["agent_id"]].append(row)
    tools_by_agent = defaultdict(set)
    for row in tools:
        tools_by_agent[row["agent_id"]].add(row["tool_name"])
    epochs_by_agent_plane = Counter((row["agent_id"], row["plane"]) for row in epochs)
    rows = []
    for row in latest:
        cats = Counter(c["category"] for c in clauses_by_agent[row["agent_id"]])
        total_clauses = sum(cats.values())
        rows.append(
            {
                "agent_id": row["agent_id"],
                "agent": row["agent"],
                "latest_version": row["version"],
                "latest_published_at": row["published_at"],
                "snapshots": sum(1 for item in snapshot_rows if item["agent_id"] == row["agent_id"]),
                "whole_prompt_epochs": epochs_by_agent_plane[(row["agent_id"], "whole")],
                "instruction_epochs": epochs_by_agent_plane[(row["agent_id"], "instruction")],
                "tool_epochs": epochs_by_agent_plane[(row["agent_id"], "tool")],
                "latest_prompt_chars": row["prompt_chars"],
                "latest_instruction_chars": row["instruction_chars"],
                "latest_tool_schema_chars": row["tool_schema_chars"],
                "latest_runtime_chars": row["runtime_chars"],
                "latest_tool_count": row["tool_count"],
                "unique_tool_names": len(tools_by_agent[row["agent_id"]]),
                "total_clauses": total_clauses,
                **{f"share_{cat}": round(cats[cat] / total_clauses, 4) if total_clauses else 0 for cat in CATEGORY_ORDER},
            }
        )
    rows.sort(key=lambda item: agent_sort_key(item["agent_id"]))
    return rows



def build_latest_structural_comparison(snapshot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in latest_rows(snapshot_rows):
        total = max(1, int(row["prompt_chars"]))
        instruction = int(row["instruction_chars"])
        schema = int(row["tool_schema_chars"])
        runtime = int(row["runtime_chars"])
        capture = int(row["capture_artifact_chars"])
        tool_prompt = int(row["tool_prompt_chars"])
        shares = {
            "instruction_share": instruction / total,
            "tool_schema_share": schema / total,
            "tool_prompt_share": tool_prompt / total,
            "runtime_share": runtime / total,
            "capture_artifact_share": capture / total,
        }
        dominant = max(shares, key=shares.get)
        rows.append(
            {
                "agent_id": row["agent_id"],
                "agent": row["agent"],
                "version": row["version"],
                "published_at": row["published_at"],
                "prompt_chars": total,
                "instruction_chars": instruction,
                "tool_prompt_chars": tool_prompt,
                "tool_schema_chars": schema,
                "runtime_chars": runtime,
                "capture_artifact_chars": capture,
                "instruction_share": round(shares["instruction_share"], 4),
                "tool_prompt_share": round(shares["tool_prompt_share"], 4),
                "tool_schema_share": round(shares["tool_schema_share"], 4),
                "runtime_share": round(shares["runtime_share"], 4),
                "capture_artifact_share": round(shares["capture_artifact_share"], 4),
                "dominant_component": dominant.replace("_share", ""),
                "tool_count": row["tool_count"],
                "tool_parameter_count": row["tool_parameter_count"],
                "required_tool_parameter_count": row["required_tool_parameter_count"],
                "must_density_per_1k": row["must_density_per_1k"],
                "never_density_per_1k": row["never_density_per_1k"],
                "confirmation_density_per_1k": row["confirmation_density_per_1k"],
                "verification_density_per_1k": row["verification_density_per_1k"],
            }
        )
    return rows


def build_trend_summary(
    snapshot_rows: list[dict[str, Any]], longitudinal: list[dict[str, Any]], cross_agent: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    long_by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    epochs = {row["agent_id"]: row for row in cross_agent}
    for row in snapshot_rows:
        by_agent[row["agent_id"]].append(row)
    for row in longitudinal:
        long_by_agent[row["agent_id"]].append(row)
    rows = []
    for agent_id in sorted(by_agent, key=agent_sort_key):
        items = sorted(by_agent[agent_id], key=lambda r: parse_time(r["published_at"]) or datetime.min.replace(tzinfo=timezone.utc))
        first, latest = items[0], items[-1]
        days = days_between(first["published_at"], latest["published_at"])
        prompt_delta = int(latest["prompt_chars"]) - int(first["prompt_chars"])
        instruction_delta = int(latest["instruction_chars"]) - int(first["instruction_chars"])
        schema_delta = int(latest["tool_schema_chars"]) - int(first["tool_schema_chars"])
        runtime_delta = int(latest["runtime_chars"]) - int(first["runtime_chars"])
        tool_delta = int(latest["tool_count"]) - int(first["tool_count"])
        long_items = long_by_agent.get(agent_id, [])
        churn_values = [float(row["normalized_churn"]) for row in long_items if float(row["normalized_churn"]) > 0]
        rows.append(
            {
                "agent_id": agent_id,
                "snapshots": len(items),
                "first_version": first["version"],
                "latest_version": latest["version"],
                "first_published_at": first["published_at"],
                "latest_published_at": latest["published_at"],
                "calendar_days": days,
                "first_prompt_chars": first["prompt_chars"],
                "latest_prompt_chars": latest["prompt_chars"],
                "prompt_delta_chars": prompt_delta,
                "prompt_delta_pct": round(prompt_delta / max(1, int(first["prompt_chars"])) * 100, 2),
                "instruction_delta_chars": instruction_delta,
                "tool_schema_delta_chars": schema_delta,
                "runtime_delta_chars": runtime_delta,
                "tool_count_delta": tool_delta,
                "prompt_chars_per_30_days": round(prompt_delta / max(days, 1) * 30, 2),
                "mean_nonzero_churn": round(mean(churn_values), 6),
                "max_churn": round(max(churn_values) if churn_values else 0.0, 6),
                "whole_prompt_epochs": epochs.get(agent_id, {}).get("whole_prompt_epochs", 0),
                "instruction_epochs": epochs.get(agent_id, {}).get("instruction_epochs", 0),
                "tool_epochs": epochs.get(agent_id, {}).get("tool_epochs", 0),
            }
        )
    return rows


def build_top_change_rows(longitudinal: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    rows = [row for row in longitudinal if float(row["normalized_churn"]) > 0]
    rows = sorted(rows, key=lambda row: float(row["normalized_churn"]), reverse=True)[:limit]
    out = []
    for row in rows:
        category_counts = [(cat, int(row.get(f"churn_{cat}", 0))) for cat in CATEGORY_ORDER]
        category_counts = [(cat, count) for cat, count in category_counts if count]
        category_counts.sort(key=lambda item: item[1], reverse=True)
        out.append(
            {
                "agent_id": row["agent_id"],
                "version": row["version"],
                "published_at": row["published_at"],
                "normalized_churn": row["normalized_churn"],
                "added_clauses": row["added_clauses"],
                "removed_clauses": row["removed_clauses"],
                "moved_clauses": row["moved_clauses"],
                "dominant_churn_categories": "; ".join(
                    f"{CATEGORY_LABELS.get(cat, cat)}:{count}" for cat, count in category_counts[:3]
                ),
            }
        )
    return out



def build_major_jump_events(
    snapshots: list[Snapshot], snapshot_rows: list[dict[str, Any]], limit: int = 30
) -> list[dict[str, Any]]:
    snapshot_lookup = {snapshot_id(snap): snap for snap in snapshots}
    rows_by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in snapshot_rows:
        rows_by_agent[row["agent_id"]].append(row)
    events: list[dict[str, Any]] = []
    for agent_id, rows in rows_by_agent.items():
        rows.sort(key=lambda row: parse_time(row["published_at"]) or datetime.min.replace(tzinfo=timezone.utc))
        for prev_row, curr_row in zip(rows, rows[1:]):
            prev_snap = snapshot_lookup.get(f"{agent_id}/{prev_row['version']}")
            curr_snap = snapshot_lookup.get(f"{agent_id}/{curr_row['version']}")
            if not prev_snap or not curr_snap:
                continue
            prompt_delta = int(curr_row["prompt_chars"]) - int(prev_row["prompt_chars"])
            component_deltas = {
                "instruction": int(curr_row["instruction_chars"]) - int(prev_row["instruction_chars"]),
                "tool_text": int(curr_row["tool_prompt_chars"]) - int(prev_row["tool_prompt_chars"]),
                "tool_schema": int(curr_row["tool_schema_chars"]) - int(prev_row["tool_schema_chars"]),
                "runtime": int(curr_row["runtime_chars"]) - int(prev_row["runtime_chars"]),
                "capture_artifact": int(curr_row["capture_artifact_chars"]) - int(prev_row["capture_artifact_chars"]),
            }
            dominant_component = max(component_deltas, key=lambda key: abs(component_deltas[key]))
            section_summary = summarize_section_deltas(prev_snap.prompt_text, curr_snap.prompt_text)
            interpretation = interpret_jump_event(
                prev_snap,
                curr_snap,
                prev_row,
                curr_row,
                prompt_delta,
                component_deltas,
                days_between(prev_row["published_at"], curr_row["published_at"]),
            )
            events.append(
                {
                    "agent_id": agent_id,
                    "previous_version": prev_row["version"],
                    "version": curr_row["version"],
                    "previous_published_at": prev_row["published_at"],
                    "published_at": curr_row["published_at"],
                    "days_between": days_between(prev_row["published_at"], curr_row["published_at"]),
                    "prompt_delta_chars": prompt_delta,
                    "abs_prompt_delta_chars": abs(prompt_delta),
                    "previous_prompt_chars": prev_row["prompt_chars"],
                    "prompt_chars": curr_row["prompt_chars"],
                    "instruction_delta_chars": component_deltas["instruction"],
                    "tool_text_delta_chars": component_deltas["tool_text"],
                    "tool_schema_delta_chars": component_deltas["tool_schema"],
                    "runtime_delta_chars": component_deltas["runtime"],
                    "capture_artifact_delta_chars": component_deltas["capture_artifact"],
                    "tool_count_delta": int(curr_row["tool_count"]) - int(prev_row["tool_count"]),
                    "previous_tool_count": prev_row["tool_count"],
                    "tool_count": curr_row["tool_count"],
                    "dominant_component_delta": dominant_component,
                    "same_capture_command": str(prev_snap.command == curr_snap.command).lower(),
                    "previous_trace_parse_status": prev_row["trace_parse_status"],
                    "trace_parse_status": curr_row["trace_parse_status"],
                    "interpretation": interpretation,
                    "section_delta_summary": section_summary,
                    "evidence_paths": f"{rel(prev_snap.prompt_path)}; {rel(curr_snap.prompt_path)}",
                }
            )
    events.sort(key=lambda row: int(row["abs_prompt_delta_chars"]), reverse=True)
    return events[:limit]


def summarize_section_deltas(old_text: str, new_text: str, limit_each: int = 3) -> str:
    old_sections = {row["heading_path"]: row for row in split_sections(old_text)}
    new_sections = {row["heading_path"]: row for row in split_sections(new_text)}
    deltas = []
    for heading in sorted(set(old_sections) | set(new_sections)):
        old_len = len(old_sections.get(heading, {}).get("content", ""))
        new_len = len(new_sections.get(heading, {}).get("content", ""))
        delta = new_len - old_len
        if delta:
            deltas.append((delta, heading))
    removed = [
        f"-{abs(delta)} {heading.split(' > ')[-1]}"
        for delta, heading in sorted(deltas, key=lambda item: item[0])[:limit_each]
        if delta < 0
    ]
    added = [
        f"+{delta} {heading.split(' > ')[-1]}"
        for delta, heading in sorted(deltas, key=lambda item: item[0], reverse=True)[:limit_each]
        if delta > 0
    ]
    pieces = []
    if removed:
        pieces.append("removed/shrunk: " + "; ".join(removed))
    if added:
        pieces.append("added/expanded: " + "; ".join(added))
    return " | ".join(pieces)


def interpret_jump_event(
    prev_snap: Snapshot,
    curr_snap: Snapshot,
    prev_row: dict[str, Any],
    curr_row: dict[str, Any],
    prompt_delta: int,
    component_deltas: dict[str, int],
    gap_days: int,
) -> str:
    tool_count_delta = int(curr_row["tool_count"]) - int(prev_row["tool_count"])
    abs_delta = max(1, abs(prompt_delta))
    dominant = max(component_deltas, key=lambda key: abs(component_deltas[key]))
    if "available-deferred-tools" in curr_snap.prompt_text and "ToolSearch" in curr_snap.prompt_text and prompt_delta < 0:
        return "deferred-tool discovery: initial prompt exposes ToolSearch and defers most tool schemas"
    if "available-deferred-tools" in prev_snap.prompt_text and prompt_delta > 0:
        return "eager tool-surface restored after deferred-tool snapshot"
    if curr_row["agent_id"] == "opencode" and prev_row["version"] == "1.15.1" and curr_row["version"] == "1.15.2":
        return "prompt pruning/compaction of Git/GitHub, Task, and TodoWrite guidance"
    if gap_days >= 30:
        return f"coverage-gap mixed change over {gap_days} days; avoid treating as a single-day redesign"
    if dominant == "tool_text":
        if prompt_delta > 0 and tool_count_delta > 0:
            return "tool/capability surface expansion: more observed tools and longer tool guidance"
        if prompt_delta > 0 and tool_count_delta < 0:
            return "tool-surface reshaping: fewer observed tools but substantially longer tool guidance"
        if prompt_delta < 0 and tool_count_delta < 0:
            return "tool/capability surface pruning: fewer observed tools and shorter tool guidance"
        if prompt_delta < 0 and tool_count_delta > 0:
            return "tool-guidance compaction despite more observed tools"
        if prompt_delta > 0:
            return "tool guidance expansion/rewrite with stable tool count"
        return "tool guidance pruning/rewrite with stable tool count"
    if dominant == "instruction":
        if prompt_delta > 0:
            return "core instruction expansion"
        return "core instruction pruning or relocation out of initial prompt"
    if dominant == "runtime":
        return "runtime/context injection change"
    if dominant == "tool_schema":
        return "tool schema complexity change"
    return "mixed prompt-surface change"


def signed_fmt_int(value: Any) -> str:
    try:
        return f"{int(value):+,}"
    except (TypeError, ValueError):
        return str(value)


def signed_pct(delta: Any, base: Any) -> str:
    try:
        return f"{int(delta) / max(1, int(base)) * 100:+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def major_jump_family(row: dict[str, Any]) -> str:
    interpretation = str(row.get("interpretation", "")).lower()
    if "deferred" in interpretation or "restored" in interpretation:
        return "deferred-tool / 初始暴露方式切换"
    if "coverage-gap" in interpretation:
        return "coverage gap 后的累计 mixed change"
    if "core instruction" in interpretation or "instruction" in interpretation:
        return "核心 instruction epoch 变化"
    if "tool/capability surface" in interpretation or "tool guidance" in interpretation or "tool-surface" in interpretation:
        return "工具/能力面扩张、收缩或重塑"
    if "prompt pruning" in interpretation or "compaction" in interpretation:
        return "prompt pruning / compaction"
    if "runtime" in interpretation:
        return "runtime/context 注入变化"
    if "schema" in interpretation:
        return "工具 schema 复杂度变化"
    return "mixed prompt-surface change"


def jump_detail_interpretation(row: dict[str, Any]) -> str:
    interp = str(row.get("interpretation", ""))
    family = major_jump_family(row)
    dominant = str(row.get("dominant_component_delta", ""))
    component = COMPONENT_LABELS.get(dominant, dominant)
    tool_delta = int(row.get("tool_count_delta", 0))
    delta = int(row.get("prompt_delta_chars", 0))
    same_command = str(row.get("same_capture_command", "")).lower() == "true"
    days = int(row.get("days_between", 0))

    if "eager tool-surface restored" in interp:
        return (
            "这是上一条 deferred-tool snapshot 的反向跳变：同一 capture profile 下，大量工具说明和 schema 又回到初始 OPS。"
            "因此它更像暴露策略回退/恢复，而不是业务 prompt 在一天内新增了等量自然语言规则。"
        )
    if "deferred-tool" in interp:
        return (
            "解释为 deferred-tool discovery 是因为字符数和工具数同时断崖式下降，"
            "且当前 prompt 暴露的是 `ToolSearch` 以及 deferred tools 列表。"
            "这类变化的核心不是模型能力突然消失，而是 capability plane 从初始 request 中移到按需检索路径。"
        )
    if days >= 30:
        return (
            f"该相邻快照之间隔了 {days} 天，archive 中缺少中间成功样本；"
            "因此这一行只能说明两个被捕获端点之间发生了累计差异，不能定位到某一个 upstream release 或某一天的设计决策。"
        )
    if "prompt pruning/compaction" in interp:
        return (
            "该事件的特征是工具数量不变或基本不变，但工具/工作流说明文本明显缩短，"
            "并且减少集中在长示例、Git/GitHub 操作协议、Todo/Task 使用示例等可压缩说明上。"
            "这更像把冗长教程式 prompt 改写成较短的规则集合。"
        )
    if family == "工具/能力面扩张、收缩或重塑":
        if tool_delta > 0 and delta > 0:
            return (
                f"主要来源是 {component} 增长，并伴随观测工具数增加 {tool_delta}。"
                "这通常表示新工具、新能力模块或更完整的工具说明进入初始 OPS。"
            )
        if tool_delta < 0 and delta < 0:
            return (
                f"主要来源是 {component} 缩短，并伴随观测工具数减少 {abs(tool_delta)}。"
                "这通常表示部分工具未再被初始请求暴露，或相关工具说明被移出/合并/裁剪。"
            )
        if tool_delta < 0 and delta > 0:
            return (
                "这是工具面重塑而不是简单扩张：观测工具数减少，但保留下来的工具说明、技能说明或单个工具文档显著变长。"
                "报告中应强调 component composition，而不是只看工具数量。"
            )
        if tool_delta == 0:
            return (
                "工具数量稳定但工具说明文本发生大幅变化，说明变化集中在工具使用协议、示例、参数解释或技能文档的重写。"
                "这类事件容易被总长度图看成能力变化，但更准确地说是 capability guidance 的表达方式变化。"
            )
        return (
            "工具数量和工具说明长度方向不完全一致，说明该事件混合了能力暴露变化与说明文本压缩。"
        )
    if family == "核心 instruction epoch 变化":
        if delta > 0:
            return (
                f"主要来源是 {component} 扩张，通常意味着任务生命周期、记忆、验证、工作流或交互规则被更显式地写进 OPS。"
            )
        return (
            f"主要来源是 {component} 缩短，可能是自然语言规则被合并、移动到工具说明/运行时模板，或从初始 OPS 中裁剪。"
        )
    if not same_command:
        return (
            "该事件同时存在 capture command 差异，所以版本变化和 profile 变化混在一起。"
            "它适合作为候选 case study，但不应作为强版本效应单独引用。"
        )
    return f"该事件属于 {family}；主要证据是 `{dominant}` 分量变化最大。"


def jump_limitation_sentence(row: dict[str, Any]) -> str:
    days = int(row.get("days_between", 0))
    same_command = str(row.get("same_capture_command", "")).lower() == "true"
    trace_status = f"{row.get('previous_trace_parse_status', '')}->{row.get('trace_parse_status', '')}"
    interp = str(row.get("interpretation", "")).lower()
    if "deferred" in interp or "restored" in interp:
        return "不能写成工具功能被删除或恢复；只能写成初始 OPS 的工具暴露方式发生变化。"
    if days >= 30:
        return "需要回填中间版本或查 upstream release notes，才能把变化归因到更细的版本窗口。"
    if not same_command:
        return "由于 capture command 不同，技术报告里要把它标成 profile-sensitive 证据。"
    if "missing_body" in trace_status:
        return "raw trace body 未按统一格式解析，工具/schema 统计更多依赖 prompt.md 文本抽取；应避免过度解释 schema 细节。"
    return "相邻 capture profile 基本一致，因此可作为较强的文本变化证据；但仍不能推出真实任务表现或安全性变化。"


def build_major_jump_detail_notes(major_jumps: list[dict[str, Any]], limit: int = 20) -> list[str]:
    rows = major_jumps[:limit]
    lines: list[str] = [
        "#### 3.2.1 如何解释这些跳变",
        "",
        "跳变解释使用四个证据轴，而不是只看折线图高度：",
        "",
        "- **规模轴**：`previous_prompt_chars`、`prompt_chars`、`prompt_delta_chars`，说明相邻成功快照之间的总字符变化。",
        "- **分量轴**：instruction、tool text、tool schema、runtime、capture artifact 的 delta，判断变化主要来自核心规则、工具说明、schema 还是运行时注入。",
        "- **结构轴**：工具数量 delta 和 section-level delta，判断是新增/移除工具、重写工具说明，还是压缩长示例。",
        "- **采集轴**：`same_capture_command`、`days_between`、trace parse status，用来区分版本效应、profile 效应和 archive 覆盖缺口。",
        "",
        "因此，下面的解释是 prompt-surface 级别的证据解释，不等同于完整 harness 变化。尤其是 `days_between` 很大、`same_capture_command=false` 或 trace body 解析不完整的事件，只能作为弱一些的候选案例。",
        "",
        "#### 3.2.2 跳变类型概览",
        "",
    ]
    family_counts = Counter(major_jump_family(row) for row in rows)
    for family, count in family_counts.most_common():
        lines.append(f"- **{family}**：Top {len(rows)} 中 {count} 个事件。")
    lines.extend([
        "",
        "#### 3.2.3 逐个跳变解释",
        "",
    ])
    component_key = {
        "instruction": "instruction_delta_chars",
        "tool_text": "tool_text_delta_chars",
        "tool_schema": "tool_schema_delta_chars",
        "runtime": "runtime_delta_chars",
        "capture_artifact": "capture_artifact_delta_chars",
    }
    for index, row in enumerate(rows, start=1):
        delta = int(row.get("prompt_delta_chars", 0))
        prev_chars = int(row.get("previous_prompt_chars", 0))
        curr_chars = int(row.get("prompt_chars", 0))
        dominant = str(row.get("dominant_component_delta", ""))
        dom_delta = int(row.get(component_key.get(dominant, "prompt_delta_chars"), 0))
        tool_count_delta = int(row.get("tool_count_delta", 0))
        section_summary = str(row.get("section_delta_summary", "")).replace(" | ", "；")
        if not section_summary:
            section_summary = "section-level diff 未发现高度集中的 heading 变化，可能是多处小改动累积。"
        lines.extend(
            [
                f"**J{index:02d}. `{row['agent_id']}` `{row['previous_version']}` -> `{row['version']}`：{signed_fmt_int(delta)} chars ({signed_pct(delta, prev_chars)})**",
                "",
                f"- **现象**：`prompt.md` 从 {fmt_int(prev_chars)} chars 变为 {fmt_int(curr_chars)} chars；观测工具数从 {row['previous_tool_count']} 变为 {row['tool_count']}（delta {signed_fmt_int(tool_count_delta)}）；相邻快照间隔 {row['days_between']} 天。",
                f"- **主要来源**：最大分量变化是 `{dominant}`（{COMPONENT_LABELS.get(dominant, dominant)}，delta {signed_fmt_int(dom_delta)} chars）。capture command 是否相同：`{row['same_capture_command']}`；trace 状态：`{row['previous_trace_parse_status']}` -> `{row['trace_parse_status']}`。",
                f"- **section 证据**：{section_summary}。证据路径：`{row['evidence_paths']}`。",
                f"- **解释**：{jump_detail_interpretation(row)}",
                f"- **写作边界**：{jump_limitation_sentence(row)}",
                "",
            ]
        )

    positive = [row for row in rows if int(row.get("prompt_delta_chars", 0)) > 0]
    negative = [row for row in rows if int(row.get("prompt_delta_chars", 0)) < 0]
    tool_dominant = [row for row in rows if row.get("dominant_component_delta") == "tool_text"]
    instruction_dominant = [row for row in rows if row.get("dominant_component_delta") == "instruction"]
    profile_sensitive = [row for row in rows if str(row.get("same_capture_command", "")).lower() != "true"]
    coverage_gap = [row for row in rows if int(row.get("days_between", 0)) >= 30]
    missing_trace = [row for row in rows if "missing_body" in f"{row.get('previous_trace_parse_status', '')}->{row.get('trace_parse_status', '')}"]
    repeated_monitor = [
        row
        for row in rows
        if any(name in str(row.get("section_delta_summary", "")) for name in ("Monitor", "PushNotification", "RemoteTrigger"))
    ]
    lines.extend(
        [
            "#### 3.2.4 综合解读",
            "",
            f"Top {len(rows)} 大跳变中，正向增长有 {len(positive)} 个，负向收缩有 {len(negative)} 个；最大分量为 `tool_text` 的有 {len(tool_dominant)} 个，最大分量为 `instruction` 的有 {len(instruction_dominant)} 个。这说明折线图里的尖峰多数不是单纯核心角色说明变长，而是 capability/tool plane 的暴露、说明、裁剪或重写。",
            f"有 {len(profile_sensitive)} 个事件的 `same_capture_command=false`，有 {len(coverage_gap)} 个事件存在 30 天以上的 archive 覆盖缺口，有 {len(missing_trace)} 个事件的 raw trace body 未按统一格式解析。技术报告中可以把这些作为 evidence strength 分层：同命令、短间隔、trace ok 的事件证据最强；profile 变化、覆盖缺口和 missing body 的事件需要更保守。",
        ]
    )
    if repeated_monitor:
        repeated = ", ".join(
            f"`{row['agent_id']} {row['previous_version']}->{row['version']}`" for row in repeated_monitor[:5]
        )
        lines.append(
            f"Claude Code 的多个跳变反复涉及 `Monitor`、`PushNotification`、`RemoteTrigger` 等工具说明（例如 {repeated}）。这类重复出现的正负跳变更像某些 capability 模块在初始 OPS 中被暴露、隐藏或重新暴露，而不是 prompt 总体线性增长。写作时可以把它作为“工具面模块化/条件暴露导致局部震荡”的案例。"
        )
    if profile_sensitive:
        profile_examples = ", ".join(
            f"`{row['agent_id']} {row['previous_version']}->{row['version']}`" for row in profile_sensitive[:4]
        )
        lines.append(
            f"profile-sensitive 事件包括 {profile_examples}。这些事件仍然有文本事实价值，但不能只用版本号解释，因为 run command、模型/provider 配置或 tap 方式变化可能同时影响 OPS。"
        )
    if coverage_gap:
        gap_examples = ", ".join(
            f"`{row['agent_id']} {row['previous_version']}->{row['version']}` ({row['days_between']} days)" for row in coverage_gap[:4]
        )
        lines.append(
            f"覆盖缺口事件包括 {gap_examples}。它们适合描述为“两个观测端点之间的累计变化”，不适合描述为一次明确 redesign，除非后续补采中间版本或找到外部 release note。"
        )
    lines.extend(
        [
            "整体上，跳变分析支持一种更细的写法：prompt surface 的演化不是简单的‘越来越长’，而是由几种机制叠加产生：工具 schema/工具说明进入或离开初始请求、长示例被压缩、核心工作流/记忆/技能规则成块重写、以及 capture profile 暴露策略变化。后续技术报告应把这些机制分别讨论，而不要把所有尖峰都归因于同一种趋势。",
            "",
        ]
    )
    return lines


def build_category_summary(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matrix: dict[str, Counter] = defaultdict(Counter)
    for row in clauses:
        matrix[row["agent_id"]][row["category"]] += 1
    rows = []
    for agent_id in sorted(matrix, key=agent_sort_key):
        total = sum(matrix[agent_id].values())
        for category in CATEGORY_ORDER:
            count = matrix[agent_id][category]
            if count == 0:
                continue
            rows.append(
                {
                    "agent_id": agent_id,
                    "category": category,
                    "category_label": CATEGORY_LABELS.get(category, category),
                    "clause_count": count,
                    "share": round(count / max(1, total), 4),
                }
            )
    return rows


def build_similarity_pairs(cross_agent: list[dict[str, Any]], tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    vectors = {
        row["agent_id"]: [float(row.get(f"share_{cat}", 0)) for cat in CATEGORY_ORDER]
        for row in cross_agent
    }
    tool_sets: dict[str, set[str]] = defaultdict(set)
    for row in tools:
        tool_sets[row["agent_id"]].add(row["tool_name"])
    agents = sorted(vectors, key=agent_sort_key)
    rows = []
    for i, left in enumerate(agents):
        for right in agents[i + 1 :]:
            shared = tool_sets[left] & tool_sets[right]
            union = tool_sets[left] | tool_sets[right]
            rows.append(
                {
                    "agent_left": left,
                    "agent_right": right,
                    "category_cosine": round(cosine(vectors[left], vectors[right]), 4),
                    "tool_jaccard": round(len(shared) / len(union), 4) if union else 0,
                    "shared_tools": len(shared),
                    "union_tools": len(union),
                    "shared_tool_names_sample": "; ".join(sorted(shared)[:12]),
                }
            )
    rows.sort(key=lambda row: (float(row["category_cosine"]), float(row["tool_jaccard"])), reverse=True)
    return rows


def build_claims(
    snapshots: list[Snapshot],
    snapshot_rows: list[dict[str, Any]],
    cross_agent: list[dict[str, Any]],
    longitudinal: list[dict[str, Any]],
    model_status: dict[str, Any],
) -> list[dict[str, Any]]:
    total = len(snapshots)
    agents = len({s.agent_id for s in snapshots})
    latest = latest_rows(snapshot_rows)
    max_tools = max(latest, key=lambda row: int(row["tool_count"])) if latest else {}
    max_prompt = max(latest, key=lambda row: int(row["prompt_chars"])) if latest else {}
    high_churn = sorted(longitudinal, key=lambda row: float(row["normalized_churn"]), reverse=True)[:1]
    claims = [
        {
            "claim_id": "C1",
            "statement": f"The archive analyzed here contains {total} complete OPS snapshots across {agents} agents.",
            "evidence": "data/derived/archive_manifest.csv",
            "inference_level": "direct textual observation",
            "confidence": "high",
            "alternative_explanations": "Counts depend on repository checkout date.",
        },
        {
            "claim_id": "C2",
            "statement": "Prompt-surface composition varies substantially across agents in the latest captured snapshots.",
            "evidence": "results/cross_agent_summary.csv; figures/latest_composition.svg",
            "inference_level": "descriptive statistical inference",
            "confidence": "medium",
            "alternative_explanations": "Capture profile and trace schema differences can shift instruction/tool/runtime boundaries.",
        },
        {
            "claim_id": "C3",
            "statement": f"The latest snapshot with the largest observed tool count is {max_tools.get('agent_id','')} {max_tools.get('version','')} with {max_tools.get('tool_count','')} tools.",
            "evidence": "data/derived/snapshots.csv",
            "inference_level": "direct textual observation",
            "confidence": "high",
            "alternative_explanations": "Some agents expose tools in prompt text but not trace schema, so counts are OPS/profile-specific.",
        },
        {
            "claim_id": "C4",
            "statement": f"The largest latest prompt by character count is {max_prompt.get('agent_id','')} {max_prompt.get('version','')}.",
            "evidence": "data/derived/snapshots.csv",
            "inference_level": "direct textual observation",
            "confidence": "high",
            "alternative_explanations": "Character length is not token length and may include runtime/context text.",
        },
        {
            "claim_id": "C5",
            "statement": f"Optional model classification status: {model_status.get('note','')}",
            "evidence": "results/model_status.json; results/codebook.md",
            "inference_level": "methodological note",
            "confidence": "high",
            "alternative_explanations": "Rule-only and model-assisted runs may differ on uncertain clauses.",
        },
    ]
    if high_churn:
        item = high_churn[0]
        claims.append(
            {
                "claim_id": "C6",
                "statement": f"The largest adjacent normalized churn event detected is {item['agent_id']} {item['version']} with churn {item['normalized_churn']}.",
                "evidence": "results/longitudinal_metrics.csv",
                "inference_level": "descriptive statistical inference",
                "confidence": "medium",
                "alternative_explanations": "Large churn can reflect reformatting or capture-profile shifts, not only substantive redesign.",
            }
        )
    return claims


def render_data_quality(snapshots: list[Snapshot], snapshot_rows: list[dict[str, Any]], model_status: dict[str, Any]) -> str:
    by_agent = Counter(s.agent_id for s in snapshots)
    missing_prompt = sum(1 for s in snapshots if not s.prompt_path.exists())
    missing_trace = sum(1 for s in snapshots if not s.trace_path.exists())
    parse_status = Counter(s.trace_parse_status for s in snapshots)
    lines = [
        "# Data Quality Report",
        "",
        f"- Analysis date: {datetime.now(timezone.utc).isoformat()}",
        f"- Repository commit: `{git_commit()}`",
        f"- Complete snapshots included: {len(snapshots)}",
        f"- Agents: {len(by_agent)}",
        f"- Missing prompt files among included snapshots: {missing_prompt}",
        f"- Missing trace files among included snapshots: {missing_trace}",
        f"- Optional model classifier: {model_status.get('note', '')}",
        "",
        "## Snapshots by Agent",
        "",
        "| Agent | Snapshots | First Published | Last Published | Static Prompt Files |",
        "| --- | ---: | --- | --- | ---: |",
    ]
    for agent_id in sorted(by_agent, key=agent_sort_key):
        items = [s for s in snapshots if s.agent_id == agent_id]
        pubs = [s.published_at for s in items if s.published_at]
        lines.append(
            f"| {agent_id} | {len(items)} | {(min(pubs) if pubs else '')[:10]} | {(max(pubs) if pubs else '')[:10]} | {sum(s.static_prompts for s in items)} |"
        )
    lines.extend(["", "## Trace Parse Status", "", "| Status | Count |", "| --- | ---: |"])
    for status, count in parse_status.most_common():
        lines.append(f"| `{status}` | {count} |")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `prompt.md` is normalized for human reading; `trace.jsonl` is raw request evidence.",
            "- Request body structure differs across tap clients and providers, so unknown fields are counted rather than discarded.",
            "- Static Claude Code prompt extraction is tracked separately and is not merged into runtime OPS statistics.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_codebook(model_status: dict[str, Any]) -> str:
    lines = [
        "# Prompt Clause Codebook",
        "",
        "Labels are multi-source but represented as one primary category per clause in this first analysis pass.",
        "",
        "| Category | Description |",
        "| --- | --- |",
    ]
    descriptions = {
        "identity_mission": "Agent identity, mission, role, and high-level task boundary.",
        "interaction_output": "Tone, response format, progress updates, and user-facing communication.",
        "planning_lifecycle": "Planning, todos, task lifecycle, completion and blocking states.",
        "software_engineering_workflow": "Code reading/editing, tests, git, builds, review, and repository workflow.",
        "tool_capability": "Tool names, descriptions, parameters, schemas, and capability surface.",
        "tool_use_policy": "Rules for when and how tools should be used.",
        "permissions_side_effects": "Approval, confirmation, destructive actions, external side effects.",
        "safety_security": "Safety, privacy, credential handling, dual-use and malicious request boundaries.",
        "environment_sandbox": "Filesystem, shell, OS, sandbox, workspace, network, and runtime environment.",
        "memory_context": "Memory, session state, summarization, context management.",
        "extensibility": "Skills, plugins, MCP, project instructions, customization.",
        "multi_agent": "Delegation, subagents, parallel agents, multi-session orchestration.",
        "reliability_verification": "Testing, validation, evidence, failure reporting, factuality.",
        "runtime_capture_artifact": "Synthetic task, volatile paths, dates, IDs, and capture-specific context.",
        "uncertain": "Insufficient evidence for a confident rule/model category.",
    }
    for cat in CATEGORY_ORDER:
        lines.append(f"| `{cat}` | {descriptions[cat]} |")
    lines.extend(
        [
            "",
            "## Classifier Status",
            "",
            f"- Enabled: `{model_status.get('enabled')}`",
            f"- Model: `{model_status.get('model')}`",
            f"- Base URL: `{model_status.get('base_url')}`",
            f"- Note: {model_status.get('note')}",
            "",
            "Model-assisted labels are auxiliary. They are cached in `classification_cache.jsonl` and should be audited before being used as strong evidence.",
        ]
    )
    return "\n".join(lines) + "\n"




def render_report(
    snapshots: list[Snapshot],
    snapshot_rows: list[dict[str, Any]],
    cross_agent: list[dict[str, Any]],
    longitudinal: list[dict[str, Any]],
    model_status: dict[str, Any],
    latest_structural: list[dict[str, Any]],
    trend_summary: list[dict[str, Any]],
    top_changes: list[dict[str, Any]],
    major_jumps: list[dict[str, Any]],
    category_summary: list[dict[str, Any]],
    similarity_pairs: list[dict[str, Any]],
) -> str:
    by_agent = Counter(s.agent_id for s in snapshots)
    parse_status = Counter(s.trace_parse_status for s in snapshots)
    latest = latest_rows(snapshot_rows)
    max_tool = max(latest, key=lambda row: int(row["tool_count"])) if latest else {}
    max_prompt = max(latest, key=lambda row: int(row["prompt_chars"])) if latest else {}
    avg_epochs = mean(float(row["whole_prompt_epochs"]) for row in cross_agent) if cross_agent else 0
    coverage = coverage_rows(snapshots)
    category_by_agent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in category_summary:
        category_by_agent[row["agent_id"]].append(row)
    for rows in category_by_agent.values():
        rows.sort(key=lambda row: float(row["share"]), reverse=True)
    pi_rows = sorted(
        [row for row in snapshot_rows if row["agent_id"] == "pi"],
        key=lambda row: row["published_at"],
    )
    global_max_chars = max((int(row["prompt_chars"]) for row in snapshot_rows), default=0)
    max_char_row = max(snapshot_rows, key=lambda row: int(row["prompt_chars"]), default={})
    char_timeline_note = ""
    if max_char_row and max_prompt:
        char_timeline_note = (
            f"**字符数折线图说明：**这张二维时间轴的 y 轴已改为 `prompt.md` 字符数，"
            f"因此和 RQ1 的 `Prompt chars` 指标一致。当前全历史字符数最高的是 `{max_char_row.get('agent_id','')}` "
            f"`{max_char_row.get('version','')}`（{fmt_int(max_char_row.get('prompt_chars',''))} chars）；"
            f"最新快照中的字符数最大值是 `{max_prompt.get('agent_id','')}` `{max_prompt.get('version','')}`"
            f"（{fmt_int(max_prompt.get('prompt_chars',''))} chars）。"
            "如果需要排查 Markdown/JSON 格式化带来的行数差异，脚本仍会生成补充图 `figures/prompt_lines_timeline.svg`。"
        )
    snapshot_lookup = {snapshot_id(snap): snap for snap in snapshots}
    rows_by_agent_for_drop: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in snapshot_rows:
        rows_by_agent_for_drop[row["agent_id"]].append(row)
    largest_drop: tuple[int, dict[str, Any], dict[str, Any]] | None = None
    for agent_rows in rows_by_agent_for_drop.values():
        agent_rows.sort(key=lambda row: row["published_at"])
        for prev_row, curr_row in zip(agent_rows, agent_rows[1:]):
            delta = int(curr_row["prompt_chars"]) - int(prev_row["prompt_chars"])
            if largest_drop is None or delta < largest_drop[0]:
                largest_drop = (delta, prev_row, curr_row)
    largest_drop_note = ""
    if largest_drop and largest_drop[0] < 0:
        delta, prev_row, curr_row = largest_drop
        curr_snap = snapshot_lookup.get(f"{curr_row['agent_id']}/{curr_row['version']}")
        agent_drop_rows = rows_by_agent_for_drop.get(curr_row["agent_id"], [])
        next_row = None
        for idx, row in enumerate(agent_drop_rows):
            if row["version"] == curr_row["version"] and idx + 1 < len(agent_drop_rows):
                next_row = agent_drop_rows[idx + 1]
                break
        deferred_note = ""
        if curr_snap and "available-deferred-tools" in curr_snap.prompt_text and "ToolSearch" in curr_snap.prompt_text:
            deferred_note = (
                "该快照的 prompt 中出现 `<available-deferred-tools>`，并且 raw trace 只直接暴露 `ToolSearch`。"
                "这表示大量工具没有在初始请求里以完整 tool schema 形式 eagerly declared，"
                "而是先列出可延迟加载的工具名，再要求模型通过 `ToolSearch` 按需加载具体工具定义。"
            )
        rebound_note = ""
        if next_row:
            rebound_note = (
                f"下一版 `{next_row['version']}` 又回到 {fmt_int(next_row['prompt_chars'])} chars、"
                f"{next_row['tool_count']} 个观测工具，所以这更像短暂的暴露方式切换，而不是持续收缩。"
            )
        largest_drop_note = (
            f"**Claude Code `2.1.69` deferred-tool case note：**图中最大相邻负跳变是 "
            f"`{curr_row['agent_id']}` `{prev_row['version']}` -> `{curr_row['version']}`，"
            f"prompt 字符数从 {fmt_int(prev_row['prompt_chars'])} 降到 {fmt_int(curr_row['prompt_chars'])}"
            f"（{fmt_int(delta)} chars），观测工具数从 {prev_row['tool_count']} 降到 {curr_row['tool_count']}。"
            f"{deferred_note}{rebound_note}"
            "Phistory 对相邻版本使用同一条 capture command 和同一个简单合成任务，因此 `Reply with one short sentence.` "
            "不是充分解释；但这仍然是 *under this archived capture profile* 的观测，不能推出交互模式或真实运行时功能也删除了工具。"
            "技术报告中建议写成：`2.1.69` 的初始 OPS 从 eager tool declaration 暂时变成 deferred tool discovery，"
            "导致 capability/tool plane 从初始 prompt 中大幅移出。"
        )
    opencode_drop_note = ""
    opencode_rows_by_version = {row["version"]: row for row in rows_by_agent_for_drop.get("opencode", [])}
    opencode_prev = opencode_rows_by_version.get("1.15.1")
    opencode_curr = opencode_rows_by_version.get("1.15.2")
    opencode_next = opencode_rows_by_version.get("1.15.3")
    old_opencode_snap = snapshot_lookup.get("opencode/1.15.1")
    new_opencode_snap = snapshot_lookup.get("opencode/1.15.2")
    if opencode_prev and opencode_curr and old_opencode_snap and new_opencode_snap:
        old_sections = {row["heading_path"]: row for row in split_sections(old_opencode_snap.prompt_text)}
        new_sections = {row["heading_path"]: row for row in split_sections(new_opencode_snap.prompt_text)}
        section_deltas = []
        for heading in sorted(set(old_sections) | set(new_sections)):
            old_len = len(old_sections.get(heading, {}).get("content", ""))
            new_len = len(new_sections.get(heading, {}).get("content", ""))
            delta_len = new_len - old_len
            if delta_len:
                section_deltas.append((delta_len, heading, old_len, new_len))
        top_removed = [
            f"`{heading.split(' > ')[-1]}` {fmt_int(delta_len)} chars"
            for delta_len, heading, _old_len, _new_len in sorted(section_deltas, key=lambda item: item[0])[:4]
        ]
        top_added = [
            f"`{heading.split(' > ')[-1]}` +{fmt_int(delta_len)} chars"
            for delta_len, heading, _old_len, _new_len in sorted(section_deltas, key=lambda item: item[0], reverse=True)[:2]
            if delta_len > 0
        ]
        next_note = ""
        if opencode_next:
            next_note = f"下一版 `{opencode_next['version']}` 保持在 {fmt_int(opencode_next['prompt_chars'])} chars，说明这是持续压缩后的新 plateau。"
        opencode_drop_note = (
            f"**opencode `1.15.2` prompt-pruning case note：**`1.15.1` -> `1.15.2` 是图中第二大的非 Claude Code 负跳变之一，"
            f"prompt 字符数从 {fmt_int(opencode_prev['prompt_chars'])} 降到 {fmt_int(opencode_curr['prompt_chars'])}"
            f"（{fmt_int(int(opencode_curr['prompt_chars']) - int(opencode_prev['prompt_chars']))} chars）。"
            f"相邻版本 capture command 相同，instruction/runtime 基本不变，工具集合稳定为 {opencode_curr['tool_count']} 个；"
            f"主要变化是 tool/instruction guidance 文本从 {fmt_int(opencode_prev['tool_prompt_chars'])} 降到 {fmt_int(opencode_curr['tool_prompt_chars'])} chars。"
            f"最大减少来自这些 section：{'; '.join(top_removed)}；同时新增/合并为更短的 section：{'; '.join(top_added)}。"
            f"{next_note}"
            "因此它更像是 prompt pruning/compaction：把 Git/GitHub、Task、TodoWrite 等长示例和冗长操作协议压缩成更短规则，而不是 deferred-tool 机制或采集失败。"
        )

    pi_note = ""
    if pi_rows:
        pi_lines = [int(row["prompt_lines"]) for row in pi_rows]
        pi_chars = [int(row["prompt_chars"]) for row in pi_rows]
        pi_tools = sorted({int(row["tool_count"]) for row in pi_rows})
        pi_params = sorted({int(row["tool_parameter_count"]) for row in pi_rows})
        pi_schema = sorted({int(row["tool_schema_chars"]) for row in pi_rows})
        pi_trend = next((row for row in trend_summary if row["agent_id"] == "pi"), {})
        pi_note = (
            f"**为什么 Pi 看起来几乎没变：**在当前 archive 中 Pi 有 {len(pi_rows)} 个快照，"
            f"发布时间覆盖 {pi_rows[0]['published_at'][:10]} 到 {pi_rows[-1]['published_at'][:10]}。"
            f"`prompt.md` 字符数只在 {fmt_int(min(pi_chars))}–{fmt_int(max(pi_chars))} chars 之间波动，"
            f"而全图 y 轴最高到 {fmt_int(global_max_chars)} chars，所以在统一尺度图上接近水平线。"
            f"首尾字符数从 {fmt_int(pi_chars[0])} 到 {fmt_int(pi_chars[-1])}，"
            f"净变化 {pi_trend.get('prompt_delta_chars', int(pi_chars[-1]) - int(pi_chars[0]))} chars；"
            f"工具数一直是 {', '.join(map(str, pi_tools))}，参数数一直是 {', '.join(map(str, pi_params))}，"
            f"schema 字符数只有 {', '.join(map(str, pi_schema))} 这几个状态。"
            "主要可见变化是少量文档路径/读文档规则、`read` 支持格式，以及 `edit` schema 的 `additionalProperties` 字段变化；"
            "因此它不是图漏画，而是该 capture profile 下 OPS 本身较小且低 churn。"
        )

    lines = [
        "# Phistory Agent CLI Prompt Surface 演化分析",
        "",
        "## 0. 读法和结论边界",
        "",
        (
            f"本报告分析当前仓库中的 {len(snapshots)} 个完整 OPS 快照，覆盖 {len(by_agent)} 个 Agent CLI。"
            "OPS（Observed Prompt Surface）指 Phistory 在特定 capture profile 下捕获到的 prompt-bearing request。"
            "它能支持 prompt 文本、工具声明、运行时上下文和版本演化的描述性结论；不能单独证明完整 harness、真实行为、安全性提升或厂商动机。"
        ),
        "",
        f"本次分类状态：{model_status.get('note', '')}。类别分析是规则优先的第一版结果，适合找趋势和候选案例；正式技术报告中应把强结论限定在 `claims.csv` 已记录的证据范围内。",
        "",
        "## 1. 数据集和 capture profile",
        "",
        "### 1.1 capture profile 是什么",
        "",
        "这里的 capture profile 指一次 OPS 观测的采集配置，而不是 agent 产品本身。形式上可以写成 `OPS_{agent,version,profile}`。同一个 agent 版本在不同 profile 下可能暴露不同 prompt surface。",
        "",
        "本项目中一个 profile 至少包含：",
        "",
        "- `tap_client` 和 tap mode，例如 `claude`、`codex`、`opencode`，以及 forward/reverse/auto 模式。",
        "- 实际运行命令，也就是 `meta.json` 里的 `command`，包括 headless/non-interactive 参数、模型参数、权限跳过参数、输出格式参数等。",
        "- 合成用户任务，本仓库通常是 `Reply with one short sentence.`。",
        "- 隔离 HOME、假认证、假 provider/model 配置、临时工作目录、环境变量和 sandbox 信息。",
        "- 具体版本发布时间、捕获时间，以及 `prompt.md` 的规范化规则。",
        "",
        "因此，profile 影响的是“这次被观察到的 prompt surface”。例如 headless `exec`/`run` 模式可能和正常交互式 IDE/CLI 模式暴露不同工具或上下文；所以报告里避免说“某 agent 删除了功能”，只说“该 archived profile 下的 OPS 没有暴露某项内容”。",
        "",
        "### 1.2 数据覆盖",
        "",
        f"仓库 commit：`{git_commit()}`；分析时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}。",
        f"`trace.jsonl` 解析状态：" + ", ".join(f"`{k}`={v}" for k, v in parse_status.most_common()) + "。`missing_body` 多数意味着该 tap client 的 trace 请求体不在统一 `request.body` 位置，相关快照仍保留在文本分析中。",
        "",
        "| Agent | Snapshots | First | Last | Distinct capture commands | Static prompt files |",
        "| --- | ---: | --- | --- | ---: | ---: |",
    ]
    for row in coverage:
        lines.append(
            f"| {row['agent_id']} | {row['snapshots']} | {row['first_date']} | {row['last_date']} | {row['distinct_commands']} | {row['static_prompt_files']} |"
        )
    lines.extend(
        [
            "",
            "",
            "下面这张图就是全版本二维时间轴：横轴为版本发布时间，纵轴为 `prompt.md` 字符数；不同 agent 用不同颜色表示，每个圆点对应一个 captured version，折线连接同一 agent 的相邻版本。",
            "",
            "![Prompt chars over time](figures/prompt_chars_timeline.svg)",
            char_timeline_note,
            largest_drop_note,
            opencode_drop_note,
            pi_note,
            "关键有效性含义：不同 agent 的 command、tap mode、模型/provider 假配置并不一致，所以横向比较要解释为 *under archived capture profiles* 的 OPS 差异。Claude Code 的 `static-prompts.*` 只作为补充材料，不和 runtime OPS 混合。",
            "",
            "## 2. RQ1：不同 Agent CLI 的 OPS 结构有什么异同？",
            "",
            "### 2.1 字段和类别定义",
            "",
            "RQ1 的结构表把 prompt surface 拆成几个 plane/component。需要注意：`Instr%`、`Tool text%`、`Runtime%`、`Capture-artifact%` 来自 `prompt.md` 的 section 文本拆分；`Tool schema%` 来自 `trace.jsonl` 的工具 JSON schema，并用 prompt 字符数归一化方便比较。因此 `Tool schema%` 可能和 `Tool text%` 重叠，不能把所有百分比直接相加成 100%。",
            "",
            "| Column | Meaning | How computed |",
            "| --- | --- | --- |",
            "| `Dominant component` | 在 `prompt.md` section 拆分中字符数最大的主成分 | 取 `instruction`、`tool_prompt`、`runtime`、`capture_artifact` 中占比最大者 |",
            "| `Instr%` | 核心自然语言指令占 prompt 字符比例 | identity、workflow、permissions、interaction 等非工具/非运行时 section 字符数 / prompt chars |",
            "| `Tool text%` | 工具说明文本占 prompt 字符比例 | `prompt.md` 中 Tools/Tooling/function description 等 section 字符数 / prompt chars |",
            "| `Tool schema%` | raw request 中工具 JSON schema 的相对规模 | `trace.jsonl` 中 tools 的 schema JSON 字符数 / prompt chars；用于衡量 capability plane 复杂度 |",
            "| `Runtime%` | 运行时/环境上下文占 prompt 字符比例 | workspace、OS、shell、sandbox、memory path、session/context 等 section 字符数 / prompt chars |",
            "| `Capture-artifact%` | 采集工件占 prompt 字符比例 | 合成用户请求、临时路径、日期 ID、Phistory 占位符等明显由 capture 注入的文本 / prompt chars |",
            "| `Tools` | 观测到的工具数量 | 优先从 `trace.jsonl` tools 提取，补充从 `prompt.md` Tools section 识别的工具名 |",
            "| `Params` | 工具参数数量 | raw tool schema 的 `properties` 数量总和 |",
            "| `Governance notes` | 文本治理显式性提示 | 根据 must/never/confirm/test 等词密度阈值生成，仅表示文本上显式，不表示真实安全性 |",
            "",
            "`Dominant component` 目前可能出现的类别：`instruction`、`tool_prompt`、`runtime`、`capture_artifact`。`tool_schema` 单独报告为 capability-plane 指标，因为它来自 raw trace，而不一定是 `prompt.md` 的非重叠组成部分。",
            "",
            "### 2.2 最新快照横向结构对比",
            "",
            (
                f"最新快照中，prompt 字符数最大的是 `{max_prompt.get('agent_id','')}` `{max_prompt.get('version','')}` "
                f"（{fmt_int(max_prompt.get('prompt_chars',''))} chars）；观测工具数量最多的是 `{max_tool.get('agent_id','')}` "
                f"`{max_tool.get('version','')}`（{max_tool.get('tool_count','')} tools）。"
            ),
            "",
            "| Agent | Version | Prompt chars | Dominant component | Instr% | Tool text% | Tool schema% | Runtime% | Capture-artifact% | Tools | Params | Governance notes |",
            "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in latest_structural:
        lines.append(
            "| {agent_id} | {version} | {prompt_chars} | {dominant_component} | {instr} | {tool_text} | {schema} | {runtime} | {capture} | {tools} | {params} | {notes} |".format(
                agent_id=row["agent_id"],
                version=row["version"],
                prompt_chars=fmt_int(row["prompt_chars"]),
                dominant_component=row["dominant_component"],
                instr=fmt_pct(row["instruction_share"]),
                tool_text=fmt_pct(row["tool_prompt_share"]),
                schema=fmt_pct(row["tool_schema_share"]),
                runtime=fmt_pct(row["runtime_share"]),
                capture=fmt_pct(row["capture_artifact_share"]),
                tools=row["tool_count"],
                params=row["tool_parameter_count"],
                notes=governance_note(row),
            )
        )
    lines.extend(
        [
            "",
            "可用于技术报告的观察：",
            "",
            "- **工具文本/tool-schema-heavy 类型**：多个 agent 的最新 OPS 由工具说明文本主导；OpenClaw 还额外暴露较长 JSON schema，适合讨论 capability plane 如何贡献 prompt surface 规模。",
            "- **文本/运行时-heavy 类型**：MiMo、OpenClaw、OMP 等最新快照中有较大的非核心 instruction 组成，说明仅报告总长度会混淆 instruction、runtime 和 capture artifact。",
            "- **小型低 churn 类型**：Pi 最新快照只有 4 个工具、9 个参数，instruction 与 tool text 都能正常抽取；它适合当作‘版本发布较多但 OPS 设计变化很少’的对照样本。",
            "- **治理指标只表示文本显式性**：must/never/confirm/test 等密度适合比较 prompt-level governance，但不是行为安全分数。",
            "",
            "## 3. RQ2：同一个 Agent 的 OPS 如何随时间变化？",
            "",
            "### 3.1 不是只比较首尾版本",
            "",
            "下面的表是首尾变化摘要，用于快速看每个 agent 的长期净变化；真正的纵向分析并不只用首尾两点。管线对每个 agent 的每个 captured version 都生成一行 `longitudinal_metrics.csv`，并在 `prompt_chars_timeline.svg` 中以“一个版本一个点”的方式画出全量时间序列。",
            "",
            "- `results/trend_summary.csv`：首尾净变化、epoch 数、最大/平均 churn。",
            "- `results/longitudinal_metrics.csv`：每个版本一个样本点，包含 prompt length、tool count、churn、category churn。",
            "- `figures/prompt_chars_timeline.svg`：横轴时间、纵轴 prompt 字符数、不同 agent 不同颜色、每个版本一个点。",
            "- `figures/prompt_lines_timeline.svg`：补充图，纵轴是 prompt 行数，用于排查 Markdown/JSON 展开格式差异。",
            "",
            f"按 whole prompt hash 折叠后，平均每个 agent 有 {avg_epochs:.1f} 个 whole-prompt epoch。release 频率和 prompt 设计变化频率明显不是同一个量，因此后续分析应优先以 epoch/change event 为单位。",
            "",
            "| Agent | Versions | Prompt delta | Delta% | Instr delta | Schema delta | Tool delta | Whole epochs | Max churn | Mean nonzero churn |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in trend_summary:
        lines.append(
            f"| {row['agent_id']} | {row['first_version']} -> {row['latest_version']} | {fmt_int(row['prompt_delta_chars'])} | {row['prompt_delta_pct']}% | {fmt_int(row['instruction_delta_chars'])} | {fmt_int(row['tool_schema_delta_chars'])} | {row['tool_count_delta']} | {row['whole_prompt_epochs']} | {float(row['max_churn']):.3f} | {float(row['mean_nonzero_churn']):.3f} |"
        )
    lines.extend(
        [
            "",
            "### 3.2 Prompt-size major jump events",
            "",
            "下面这张表系统覆盖字符数折线图里的主要大跳变，按相邻 captured version 的 `abs(prompt_delta_chars)` 排序。完整 Top 30 机器可读表在 `results/major_jump_events.csv`。注意：`days_between` 大的事件可能是 archive 覆盖缺口后的累计变化，不应直接解释成单日改版。",
            "",
            "| Agent | Version transition | Δ chars | Days | Main source | Tool Δ | Same command | Interpretation | Section evidence |",
            "| --- | --- | ---: | ---: | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in major_jumps[:20]:
        section_evidence = row.get("section_delta_summary", "").replace("|", "<br>")
        lines.append(
            f"| {row['agent_id']} | {row['previous_version']} -> {row['version']} | {fmt_int(row['prompt_delta_chars'])} | {row['days_between']} | {row['dominant_component_delta']} | {row['tool_count_delta']} | {row['same_capture_command']} | {row['interpretation']} | {section_evidence} |"
        )
    lines.extend(
        [
            "",
            "这些大跳变大致分成五类：deferred-tool 暴露方式切换、prompt pruning/compaction、工具/能力面扩张或收缩、核心 instruction 大块增删、以及带覆盖缺口的 mixed change。下面的逐点解释把每个跳变拆成现象、主要来源、section 证据、解释和写作边界，便于后续技术报告直接引用或改写。",
            "",
        ]
    )
    lines.extend(build_major_jump_detail_notes(major_jumps, limit=20))
    lines.extend(
        [
            "",
            "### 3.3 全版本 clause-level change event 摘要",
            "",
            "最大相邻 clause churn 事件如下。它们用于挑选 case study，而不是自动等同 prompt-size 最大变化：",
            "",
            "| Agent | Version | Churn | Added | Removed | Moved | Dominant churn categories |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in top_changes[:10]:
        lines.append(
            f"| {row['agent_id']} | {row['version']} | {float(row['normalized_churn']):.3f} | {row['added_clauses']} | {row['removed_clauses']} | {row['moved_clauses']} | {row['dominant_churn_categories']} |"
        )
    lines.extend(
        [
            "",
            "初步解释：Claude Code 早期和 Codex 近期都有较高 churn 事件，但需要逐条查看 `change_events.csv` 区分真实内容变更、段落重排、工具 schema 重排和 capture profile 变化。`moved_clauses` 较高的事件尤其不应被简单解释为删除/新增。",
            "",
            "## 4. RQ3：哪些类别的 prompt 指令变化更活跃？",
            "",
            "### 4.1 这个结果是怎么分析出来的",
            "",
            "RQ3 的输入不是人工印象，而是脚本生成的 clause 表和相邻版本 diff：",
            "",
            "1. 先把每个 `prompt.md` 按 Markdown heading 切成 section，并用 heading/content 规则标记 component type：`instruction`、`tool`、`runtime`、`capture_artifact`。",
            "2. 对 `instruction` 和 `runtime` section 做 clause segmentation：优先按 bullet/numbered list 切分，长段落再按句子边界切分；代码块跳过。",
            "3. 每条 clause 做轻量 normalization：替换日期、UUID、Phistory 占位符、合成任务等 volatile artifact，生成 `normalized_hash`。",
            "4. 用 heading + clause 文本的高精度关键词规则打一个 primary category。例如 `confirm/delete/approval` 归入 permissions，`test/verify/evidence` 归入 reliability，`memory/context/session` 归入 memory。规则未覆盖的标记为 `uncertain`。",
            "5. 对相邻版本比较 clause hash 集合：新 hash 记为 `add`，旧 hash 消失记为 `remove`，hash 相同但位置变化记为 `move`。类别 churn 主要来自 add/remove 的类别计数。",
            "6. 汇总到 `category_summary.csv`、`change_events.csv`、`longitudinal_metrics.csv` 和 `change_heatmap.svg`。",
            "",
            "因此，RQ3 表里“Top categories”回答的是全历史文本分布；`change_heatmap.svg` 和 `top_change_events.csv` 才更接近“变化更活跃”的问题。由于当前分类是 rule-only，`uncertain` 高的 agent 需要人工或经批准的模型分类复核。",
            "",
            "### 4.2 全历史 clause 类别分布",
            "",
            "| Agent | Top categories | Uncertain share | Runtime/capture share |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for agent_id in sorted(category_by_agent, key=agent_sort_key):
        rows = category_by_agent[agent_id]
        top = "; ".join(f"{row['category_label']} {fmt_pct(row['share'])}" for row in rows[:3])
        uncertain = next((row["share"] for row in rows if row["category"] == "uncertain"), 0)
        runtime = next((row["share"] for row in rows if row["category"] == "runtime_capture_artifact"), 0)
        lines.append(f"| {agent_id} | {top} | {fmt_pct(uncertain)} | {fmt_pct(runtime)} |")
    lines.extend(
        [
            "",
            "可检验趋势假设的当前状态：",
            "",
            "| Hypothesis | Current evidence status | How to use it |",
            "| --- | --- | --- |",
            "| H1: 总长度增长主要来自工具 schema | 部分支持但 agent-specific；OpenClaw/Kimi 的 schema 增量明显，MiMo/OMP 的总长度还受 runtime/capture 文本影响 | 在报告中拆分 plane，避免只讲总长度 |",
            "| H2: 变化是 bursty 的 | 支持作为候选：top churn 集中在少数版本 | 用 top change events 做 case studies |",
            "| H3: 权限/确认/验证规则更显式 | 需要更强分类验证；当前只能用 governance density 做候选信号 | 作为待验证假设，不写成最终结论 |",
            "| H4: memory/skills/MCP/subagents 增加 | 对 Claude Code、Kimi Code 等有文本信号；但 capture/profile 差异大 | 用 category timeline + excerpt 佐证 |",
            "| H5: 功能类别趋同但措辞/权限哲学分化 | 有类别相似度信号，需结合 exact clause overlap 才能加强 | 作为 RQ4 的分析框架 |",
            "| H6: 成熟 agent 可能收缩或模块化 | 有 prompt delta 为负或 epoch 停滞的 agent，可作为反例 | 防止单线性“越来越长”叙事 |",
            "| H7: 功能删除可能是 headless capture effect | 方法上必须保留；本版不做新增敏感性实验 | 写入 threats to validity |",
            "",
            "## 5. RQ4：不同 Agent 是否收敛或分化？",
            "",
            "本版使用两个粗粒度相似性指标：类别分布 cosine 和工具集合 Jaccard。前者衡量 prompt clause 主题分布，后者衡量观测工具名集合重叠。二者都只能说明 OPS 表层相似性。",
            "",
            "| Pair | Category cosine | Tool Jaccard | Shared tools sample |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for row in similarity_pairs[:10]:
        lines.append(
            f"| {row['agent_left']} / {row['agent_right']} | {float(row['category_cosine']):.3f} | {float(row['tool_jaccard']):.3f} | {row['shared_tool_names_sample']} |"
        )
    lines.extend(
        [
            "",
            "解读建议：类别相似但工具 Jaccard 低，通常表示高层 prompt 功能趋同但具体 capability surface 不同；工具 Jaccard 高但类别相似低，则可能是共享底层工具形态但交互/治理文本不同。不要从相似度直接推断代码共享或抄袭。",
            "",
            "## 6. 可直接写进技术报告的方法段",
            "",
            "1. 定义 OPS：agent、version、capture profile 下的一次 prompt-bearing request。",
            "2. 按 instruction plane、capability/tool plane、runtime plane、capture artifact 拆分。",
            "3. 对每个 plane 分别 hash 并构造 prompt epochs，避免把无 prompt 变化的软件 release 重复计权。",
            "4. 对 instruction/runtime 文本切 clause，用规则 taxonomy 做第一版分类，uncertain 项保留。",
            "5. 用 macro-average 和 per-agent summary 做横向比较，避免 release 数多的 agent 主导结论。",
            "6. 所有 claims 必须引用 `results/claims.csv`、派生表或图，不从一次性主观阅读得出。",
            "",
            "## 7. 仍需加强的地方",
            "",
            "- 对 `uncertain` 和 top churn 事件做人工抽样复核，形成分类准确率或审计说明。",
            "- 若要用外部模型分类，需要显式批准把 selected prompt clauses 发送到该 endpoint，并保留 `classification_cache.jsonl`。",
            "- 做更强的 clause alignment：当前 moved clause 检测基于 hash/source order，尚未做高质量语义 lineage。",
            "- 对每个 top case study 增加短 excerpt，注意版权和引用长度。",
            "- 如果论文要讨论 capture sensitivity，需要另开实验，不应从现有 archive 直接推断。",
            "",
            "## 8. 复现入口和证据文件",
            "",
            "```bash",
            "python3 analysis_result/src/run_all.py",
            "PHISTORY_ANALYSIS_USE_MODEL=1 PHISTORY_ANALYSIS_MODEL_LIMIT=200 python3 analysis_result/src/run_all.py",
            "```",
            "",
            "核心证据文件：`data/derived/snapshots.csv`、`data/derived/clauses.csv`、`data/derived/change_events.csv`、`results/trend_summary.csv`、`results/category_summary.csv`、`results/similarity_pairs.csv`、`results/claims.csv`。",
        ]
    )
    return "\n".join(lines) + "\n"


def render_figures(
    snapshots: list[Snapshot],
    snapshot_rows: list[dict[str, Any]],
    clauses: list[dict[str, Any]],
    cross_agent: list[dict[str, Any]],
    longitudinal: list[dict[str, Any]],
) -> None:
    render_archive_coverage(snapshots)
    render_latest_composition(latest_rows(snapshot_rows))
    render_tool_counts(latest_rows(snapshot_rows))
    render_prompt_growth(snapshot_rows)
    render_prompt_lines_timeline(snapshot_rows)
    render_category_heatmap(clauses)
    render_change_heatmap(longitudinal)
    render_similarity_heatmap(cross_agent)


def render_archive_coverage(snapshots: list[Snapshot]) -> None:
    width, height = 980, 420
    margin_left, margin_right, margin_top = 150, 40, 35
    times = [parse_time(s.published_at) for s in snapshots if parse_time(s.published_at)]
    min_t, max_t = min(times), max(times)
    agents = sorted({s.agent_id for s in snapshots}, key=agent_sort_key)
    row_h = 28
    parts = svg_header(width, height, "Archive coverage timeline")
    parts.append(svg_text(20, 24, "Archive coverage timeline", 16, "bold"))
    for idx, agent in enumerate(agents):
        y = margin_top + idx * row_h + 18
        items = [s for s in snapshots if s.agent_id == agent and parse_time(s.published_at)]
        xs = [scale_time(parse_time(s.published_at), min_t, max_t, margin_left, width - margin_right) for s in items]
        if xs:
            parts.append(f'<line x1="{min(xs):.1f}" y1="{y}" x2="{max(xs):.1f}" y2="{y}" stroke="#9aa4b2" stroke-width="5" stroke-linecap="round"/>')
            for x in xs:
                parts.append(f'<circle cx="{x:.1f}" cy="{y}" r="2.6" fill="#2563eb"/>')
        parts.append(svg_text(12, y + 4, f"{agent} ({len(items)})", 12))
    parts.append(svg_text(margin_left, height - 18, min_t.strftime("%Y-%m-%d"), 11))
    parts.append(svg_text(width - margin_right - 80, height - 18, max_t.strftime("%Y-%m-%d"), 11))
    write_text(FIGURES / "archive_coverage.svg", "\n".join(parts + ["</svg>\n"]))


def render_latest_composition(latest: list[dict[str, Any]]) -> None:
    width, height = 980, 430
    parts = svg_header(width, height, "Latest prompt composition")
    parts.append(svg_text(20, 24, "Latest snapshot composition by character count", 16, "bold"))
    max_total = max(int(row["prompt_chars"]) for row in latest) if latest else 1
    colors = [
        ("instruction_chars", "#2563eb", "instruction"),
        ("tool_schema_chars", "#16a34a", "tool schema"),
        ("runtime_chars", "#f59e0b", "runtime"),
        ("capture_artifact_chars", "#ef4444", "capture artifact"),
    ]
    x0, y0, bar_w, row_h = 160, 45, 720, 28
    for i, row in enumerate(latest):
        y = y0 + i * row_h
        parts.append(svg_text(10, y + 14, row["agent_id"], 12))
        x = x0
        for key, color, _label in colors:
            value = int(row[key])
            w = value / max_total * bar_w
            if w > 0:
                parts.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="17" fill="{color}" opacity="0.86"/>')
            x += w
        parts.append(svg_text(x0 + bar_w + 8, y + 14, str(row["prompt_chars"]), 11))
    lx = 20
    for _key, color, label in colors:
        parts.append(f'<rect x="{lx}" y="{height-30}" width="12" height="12" fill="{color}"/>')
        parts.append(svg_text(lx + 17, height - 20, label, 11))
        lx += 135
    write_text(FIGURES / "latest_composition.svg", "\n".join(parts + ["</svg>\n"]))


def render_tool_counts(latest: list[dict[str, Any]]) -> None:
    width, height = 780, 360
    parts = svg_header(width, height, "Latest tool counts")
    parts.append(svg_text(20, 24, "Latest observed tool counts", 16, "bold"))
    max_value = max(int(row["tool_count"]) for row in latest) if latest else 1
    for i, row in enumerate(latest):
        y = 48 + i * 25
        w = int(row["tool_count"]) / max_value * 520 if max_value else 0
        parts.append(svg_text(12, y + 13, row["agent_id"], 12))
        parts.append(f'<rect x="145" y="{y}" width="{w:.1f}" height="16" fill="#7c3aed" opacity="0.85"/>')
        parts.append(svg_text(155 + w, y + 13, str(row["tool_count"]), 11))
    write_text(FIGURES / "tool_counts.svg", "\n".join(parts + ["</svg>\n"]))



def render_prompt_lines_timeline(snapshot_rows: list[dict[str, Any]]) -> None:
    width, height = 1120, 640
    plot = (90, 55, 830, height - 70)
    legend_x = 860
    parts = svg_header(width, height, "Prompt lines over time")
    parts.append(svg_text(20, 26, "Prompt lines over time: one point per captured version", 16, "bold"))
    times = [parse_time(row["published_at"]) for row in snapshot_rows if parse_time(row["published_at"])]
    if not times:
        write_text(FIGURES / "prompt_lines_timeline.svg", "\n".join(parts + ["</svg>\n"]))
        return
    min_t, max_t = min(times), max(times)
    max_lines = max(int(row["prompt_lines"]) for row in snapshot_rows) if snapshot_rows else 1
    colors = [
        "#2563eb",
        "#16a34a",
        "#dc2626",
        "#9333ea",
        "#0891b2",
        "#f97316",
        "#4f46e5",
        "#65a30d",
        "#be123c",
        "#0f766e",
        "#a16207",
    ]
    agents = sorted({r["agent_id"] for r in snapshot_rows}, key=agent_sort_key)
    for idx, agent in enumerate(agents):
        color = colors[idx % len(colors)]
        agent_rows = [row for row in snapshot_rows if row["agent_id"] == agent and parse_time(row["published_at"])]
        agent_rows.sort(key=lambda row: parse_time(row["published_at"]) or datetime.min.replace(tzinfo=timezone.utc))
        pts = []
        for row in agent_rows:
            x = scale_time(parse_time(row["published_at"]), min_t, max_t, plot[0], plot[2])
            y = plot[3] - int(row["prompt_lines"]) / max_lines * (plot[3] - plot[1])
            pts.append((x, y))
        if len(pts) >= 2:
            parts.append(
                f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{color}" stroke-width="1.8" opacity="0.78"/>'
            )
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{color}" opacity="0.84"/>')
        ly = 60 + idx * 24
        parts.append(f'<line x1="{legend_x}" y1="{ly-4}" x2="{legend_x+22}" y2="{ly-4}" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<circle cx="{legend_x+11}" cy="{ly-4}" r="2.4" fill="{color}"/>')
        parts.append(svg_text(legend_x + 32, ly, f"{agent} ({len(agent_rows)})", 12))
    parts.append(f'<line x1="{plot[0]}" y1="{plot[3]}" x2="{plot[2]}" y2="{plot[3]}" stroke="#374151"/>')
    parts.append(f'<line x1="{plot[0]}" y1="{plot[1]}" x2="{plot[0]}" y2="{plot[3]}" stroke="#374151"/>')
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = plot[3] - frac * (plot[3] - plot[1])
        value = round(max_lines * frac)
        parts.append(f'<line x1="{plot[0]-4}" y1="{y:.1f}" x2="{plot[0]}" y2="{y:.1f}" stroke="#374151"/>')
        parts.append(svg_text(18, y + 4, str(value), 11))
    parts.append(svg_text(plot[0], height - 24, min_t.strftime("%Y-%m-%d"), 11))
    parts.append(svg_text(plot[2] - 80, height - 24, max_t.strftime("%Y-%m-%d"), 11))
    parts.append(svg_text(16, 48, "Prompt lines", 11))
    write_text(FIGURES / "prompt_lines_timeline.svg", "\n".join(parts + ["</svg>\n"]))


def render_prompt_growth(snapshot_rows: list[dict[str, Any]]) -> None:
    width, height = 1120, 640
    plot = (105, 55, 830, height - 70)
    legend_x = 860
    parts = svg_header(width, height, "Prompt characters over time")
    parts.append(svg_text(20, 26, "Prompt characters over time: one point per captured version", 16, "bold"))
    times = [parse_time(row["published_at"]) for row in snapshot_rows if parse_time(row["published_at"])]
    if not times:
        svg = "\n".join(parts + ["</svg>\n"])
        write_text(FIGURES / "prompt_growth.svg", svg)
        write_text(FIGURES / "prompt_chars_timeline.svg", svg)
        return
    min_t, max_t = min(times), max(times)
    max_chars = max(int(row["prompt_chars"]) for row in snapshot_rows) if snapshot_rows else 1
    colors = [
        "#2563eb",
        "#16a34a",
        "#dc2626",
        "#9333ea",
        "#0891b2",
        "#f97316",
        "#4f46e5",
        "#65a30d",
        "#be123c",
        "#0f766e",
        "#a16207",
    ]
    agents = sorted({r["agent_id"] for r in snapshot_rows}, key=agent_sort_key)
    for idx, agent in enumerate(agents):
        color = colors[idx % len(colors)]
        agent_rows = [row for row in snapshot_rows if row["agent_id"] == agent and parse_time(row["published_at"])]
        agent_rows.sort(key=lambda row: parse_time(row["published_at"]) or datetime.min.replace(tzinfo=timezone.utc))
        pts = []
        for row in agent_rows:
            x = scale_time(parse_time(row["published_at"]), min_t, max_t, plot[0], plot[2])
            y = plot[3] - int(row["prompt_chars"]) / max_chars * (plot[3] - plot[1])
            pts.append((x, y))
        if len(pts) >= 2:
            parts.append(
                f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{color}" stroke-width="1.8" opacity="0.78"/>'
            )
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{color}" opacity="0.84"/>')
        ly = 60 + idx * 24
        parts.append(f'<line x1="{legend_x}" y1="{ly-4}" x2="{legend_x+22}" y2="{ly-4}" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<circle cx="{legend_x+11}" cy="{ly-4}" r="2.4" fill="{color}"/>')
        parts.append(svg_text(legend_x + 32, ly, f"{agent} ({len(agent_rows)})", 12))
    parts.append(f'<line x1="{plot[0]}" y1="{plot[3]}" x2="{plot[2]}" y2="{plot[3]}" stroke="#374151"/>')
    parts.append(f'<line x1="{plot[0]}" y1="{plot[1]}" x2="{plot[0]}" y2="{plot[3]}" stroke="#374151"/>')
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = plot[3] - frac * (plot[3] - plot[1])
        value = round(max_chars * frac)
        parts.append(f'<line x1="{plot[0]-4}" y1="{y:.1f}" x2="{plot[0]}" y2="{y:.1f}" stroke="#374151"/>')
        parts.append(svg_text(18, y + 4, fmt_int(value), 11))
    parts.append(svg_text(plot[0], height - 24, min_t.strftime("%Y-%m-%d"), 11))
    parts.append(svg_text(plot[2] - 80, height - 24, max_t.strftime("%Y-%m-%d"), 11))
    parts.append(svg_text(16, 48, "Prompt chars", 11))
    svg = "\n".join(parts + ["</svg>\n"])
    write_text(FIGURES / "prompt_growth.svg", svg)
    write_text(FIGURES / "prompt_chars_timeline.svg", svg)


def render_category_heatmap(clauses: list[dict[str, Any]]) -> None:
    matrix = defaultdict(Counter)
    for row in clauses:
        matrix[row["agent_id"]][row["category"]] += 1
    render_heatmap(matrix, "category_heatmap.svg", "Clause category counts", value_transform=lambda x: math.log1p(x))


def render_change_heatmap(longitudinal: list[dict[str, Any]]) -> None:
    matrix = defaultdict(Counter)
    for row in longitudinal:
        for cat in CATEGORY_ORDER:
            matrix[row["agent_id"]][cat] += int(row.get(f"churn_{cat}", 0))
    render_heatmap(matrix, "change_heatmap.svg", "Category-specific clause churn", value_transform=lambda x: math.log1p(x))


def render_similarity_heatmap(cross_agent: list[dict[str, Any]]) -> None:
    agents = [row["agent_id"] for row in cross_agent]
    vectors = {
        row["agent_id"]: [float(row.get(f"share_{cat}", 0)) for cat in CATEGORY_ORDER]
        for row in cross_agent
    }
    matrix = defaultdict(Counter)
    for a in agents:
        for b in agents:
            matrix[a][b] = cosine(vectors[a], vectors[b])
    render_heatmap(matrix, "similarity_heatmap.svg", "Category-distribution cosine similarity", columns=agents, value_transform=lambda x: x)


def render_heatmap(
    matrix: dict[str, Counter],
    filename: str,
    title: str,
    *,
    columns: list[str] | None = None,
    value_transform,
) -> None:
    rows = sorted(matrix, key=agent_sort_key)
    cols = columns or CATEGORY_ORDER
    cell, left, top = 34, 165, 60
    width = left + len(cols) * cell + 40
    height = top + len(rows) * cell + 130
    values = [value_transform(matrix[r][c]) for r in rows for c in cols]
    max_value = max(values) if values else 1
    parts = svg_header(width, height, title)
    parts.append(svg_text(20, 24, title, 16, "bold"))
    for i, col in enumerate(cols):
        parts.append(svg_text(left + i * cell + 7, top - 8, col[:10], 9, rotate=-45))
    for r, row in enumerate(rows):
        y = top + r * cell
        parts.append(svg_text(10, y + 21, row, 12))
        for c, col in enumerate(cols):
            value = value_transform(matrix[row][col])
            intensity = value / max_value if max_value else 0
            color = blue_scale(intensity)
            x = left + c * cell
            parts.append(f'<rect x="{x}" y="{y}" width="{cell-2}" height="{cell-2}" fill="{color}"/>')
    write_text(FIGURES / filename, "\n".join(parts + ["</svg>\n"]))


def build_capture_profiles(snapshots: list[Snapshot]) -> list[dict[str, Any]]:
    rows = []
    for agent_id, items in group_by_agent(snapshots).items():
        commands = Counter(s.command for s in items)
        tap_clients = Counter(s.tap_client for s in items)
        rows.append(
            {
                "agent_id": agent_id,
                "snapshots": len(items),
                "distinct_commands": len(commands),
                "most_common_command": commands.most_common(1)[0][0] if commands else "",
                "tap_clients": ";".join(f"{k}:{v}" for k, v in tap_clients.items()),
                "first_published_at": min((s.published_at for s in items if s.published_at), default=""),
                "last_published_at": max((s.published_at for s in items if s.published_at), default=""),
            }
        )
    rows.sort(key=lambda row: agent_sort_key(row["agent_id"]))
    return rows



def coverage_rows(snapshots: list[Snapshot]) -> list[dict[str, Any]]:
    rows = []
    for agent_id, items in group_by_agent(snapshots).items():
        pubs = [s.published_at for s in items if s.published_at]
        commands = {s.command for s in items}
        rows.append(
            {
                "agent_id": agent_id,
                "snapshots": len(items),
                "first_date": (min(pubs) if pubs else "")[:10],
                "last_date": (max(pubs) if pubs else "")[:10],
                "distinct_commands": len(commands),
                "static_prompt_files": sum(1 for s in items if s.static_prompts),
            }
        )
    rows.sort(key=lambda row: agent_sort_key(row["agent_id"]))
    return rows


def fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def governance_note(row: dict[str, Any]) -> str:
    notes = []
    try:
        if float(row.get("confirmation_density_per_1k", 0)) >= 3:
            notes.append("confirm-heavy")
        if float(row.get("never_density_per_1k", 0)) >= 8:
            notes.append("many prohibitions")
        if float(row.get("verification_density_per_1k", 0)) >= 6:
            notes.append("verification-heavy")
        if float(row.get("must_density_per_1k", 0)) >= 12:
            notes.append("must-heavy")
    except (TypeError, ValueError):
        pass
    return "; ".join(notes) if notes else "-"


def latest_rows(snapshot_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in snapshot_rows:
        current = latest.get(row["agent_id"])
        if current is None or (parse_time(row["published_at"]) or datetime.min.replace(tzinfo=timezone.utc)) >= (
            parse_time(current["published_at"]) or datetime.min.replace(tzinfo=timezone.utc)
        ):
            latest[row["agent_id"]] = row
    return [latest[agent] for agent in sorted(latest, key=agent_sort_key)]


def snapshot_manifest_row(s: Snapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id(s),
        "agent_id": s.agent_id,
        "agent": s.agent,
        "version": s.version,
        "published_at": s.published_at,
        "captured_at": s.captured_at,
        "tap_client": s.tap_client,
        "client_exit_code": s.client_exit_code,
        "command": s.command,
        "prompt_path": rel(s.prompt_path),
        "trace_path": rel(s.trace_path),
        "meta_path": rel(s.meta_path),
        "prompt_sha256": s.prompt_sha256,
        "trace_sha256": s.trace_sha256,
        "meta_sha256": s.meta_sha256,
        "static_prompts": str(s.static_prompts).lower(),
        "trace_parse_status": s.trace_parse_status,
    }


def normalized_churn(old: str, new: str) -> float:
    old_lines = normalize_text(old).splitlines()
    new_lines = normalize_text(new).splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            changed += j2 - j1
        elif tag == "delete":
            changed += i2 - i1
        elif tag == "replace":
            changed += (i2 - i1) + (j2 - j1)
    denom = max(1, len(old_lines) + len(new_lines))
    return changed / denom


def normalize_text(text: str) -> str:
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern, replacement in VOLATILE_PATTERNS:
        value = pattern.sub(replacement, value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def density(text: str, pattern: str) -> float:
    words = max(1, len(re.findall(r"[A-Za-z][A-Za-z'-]+", text)))
    hits = len(re.findall(pattern, text, flags=re.I))
    return round(hits / words * 1000, 4)


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_id(snap: Snapshot) -> str:
    return f"{snap.agent_id}/{snap.version}"


def version_key(version: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for part in re.findall(r"\d+|[A-Za-z]+", version):
        parts.append(int(part) if part.isdigit() else part)
    return tuple(parts)


def agent_sort_key(agent_id: str) -> tuple[int, str]:
    return (AGENT_ORDER.index(agent_id), "") if agent_id in AGENT_ORDER else (len(AGENT_ORDER), agent_id)


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def days_between(start: str, end: str) -> int:
    a, b = parse_time(start), parse_time(end)
    if not a or not b:
        return 0
    return max(0, (b - a).days)


def scale_time(dt: datetime | None, min_t: datetime, max_t: datetime, left: float, right: float) -> float:
    if not dt or max_t <= min_t:
        return left
    return left + (dt - min_t).total_seconds() / (max_t - min_t).total_seconds() * (right - left)


def group_by_agent(snapshots: list[Snapshot]) -> dict[str, list[Snapshot]]:
    grouped: dict[str, list[Snapshot]] = defaultdict(list)
    for snap in snapshots:
        grouped[snap.agent_id].append(snap)
    return dict(grouped)


def mean(values: Any) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def blue_scale(intensity: float) -> str:
    intensity = max(0, min(1, intensity))
    r = round(239 - intensity * 202)
    g = round(246 - intensity * 147)
    b = round(255 - intensity * 20)
    return f"rgb({r},{g},{b})"


def svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f"<title>{html.escape(title)}</title>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Inter,Arial,sans-serif;fill:#111827}</style>',
    ]


def svg_text(x: float, y: float, text: str, size: int, weight: str = "normal", rotate: int | None = None) -> str:
    transform = f' transform="rotate({rotate} {x} {y})"' if rotate else ""
    return f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}"{transform}>{html.escape(str(text))}</text>'


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def model_cache_key(model: str, normalized_hash: str) -> str:
    return stable_hash(f"{model}:codebook-v1:{normalized_hash}")


if __name__ == "__main__":
    raise SystemExit(main())

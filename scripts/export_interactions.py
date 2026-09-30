"""Explicit, read-only diagnostic export (M02 §19 / M10 §23)."""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from uuid import UUID

from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool

from app.artifact_persistence import load_artifact_persistence_settings
from app.artifact_persistence.interaction_records import (
    _PostgresInteractionRecorder, DEMO_INTERACTION_STORE_LIMITS, canonical_record,
)
from app.llm_runtime import InteractionRecord


class ExportError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)


def merge_records(run_id, database_records, fallback_records=()):
    records = {}
    trace_ids = set()
    for record in (*database_records, *fallback_records):
        record = InteractionRecord.model_validate_json(canonical_record(record))
        if record.run_id != run_id:
            raise ExportError("export_run_mismatch")
        trace_ids.add(record.trace_id)
        raw = canonical_record(record)
        if record.record_id in records and records[record.record_id][0] != raw:
            raise ExportError("export_record_conflict")
        records[record.record_id] = raw, record
    if len(trace_ids) > 1:
        raise ExportError("export_trace_mismatch")
    return tuple(item[1] for item in sorted(records.values(), key=lambda item: (item[1].captured_at, item[1].record_id)))


def fallback_records(root, run_id):
    if root is None:
        return ()
    file = root if root.is_file() else root / str(run_id) / "interactions.jsonl"
    if not file.exists():
        return ()
    try:
        return tuple(InteractionRecord.model_validate_json(line) for line in file.read_bytes().splitlines() if line.strip())
    except Exception:
        raise ExportError("export_fallback_invalid") from None


def _body_json(body):
    if body.encoding != "utf8":
        return None
    try:
        return json.loads(body.content)
    except (ValueError, TypeError):
        return None


def _business(value):
    if isinstance(value, dict):
        return {key: _business(item) for key, item in value.items() if key not in {"theme", "notes", "description", "explanation", "rationale"}}
    if isinstance(value, list):
        return [_business(item) for item in value]
    return value


def summarize(records):
    """Count only actual response usage; summaries never add token costs again."""
    calls = defaultdict(list)
    for record in records:
        if record.call_id is not None:
            calls[record.call_id].append(record)
    totals = defaultdict(list)
    repeats = defaultdict(list)
    missing = []
    rows = []
    for call_id, items in calls.items():
        summary = next((item.payload for item in reversed(items) if item.record_kind == "call_summary"), None)
        categories = [item.budget_category for item in items if item.budget_category is not None]
        category = categories[-1] if categories else None
        first = items[0]
        group = f"{first.role.value if first.role else 'unknown'}/{first.purpose}/{first.request_stage or '-'}→{category or '-'}"
        responses = [item for item in items if item.record_kind == "response"]
        attempts = sorted({item.provider_attempt for item in items if item.provider_attempt is not None})
        complete = summary is not None and summary.records_complete and sum(item.record_kind == "call_summary" for item in items) == 1
        for attempt in attempts:
            kinds = {item.record_kind for item in items if item.provider_attempt == attempt}
            complete &= "request" in kinds and bool(kinds & {"response", "transport_failure"}) and "validation" in kinds
        if summary is not None:
            complete &= len(attempts) == summary.observed_provider_attempts
        if not complete:
            missing.append(str(call_id))
        usage = {}
        for key in ("prompt_tokens", "cache_hit_tokens", "cache_miss_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"):
            values = [getattr(item.payload.usage, key) for item in responses]
            known = [value for value in values if value is not None]
            usage[key] = sum(known) if known else None
            totals[key].extend(known)
        # Completion already includes reasoning. Missing reasoning remains unknown.
        nonreasoning = sum(item.payload.usage.completion_tokens - item.payload.usage.reasoning_tokens for item in responses
            if item.payload.usage.completion_tokens is not None and item.payload.usage.reasoning_tokens is not None and item.payload.usage.reasoning_tokens <= item.payload.usage.completion_tokens)
        all_parts_known = bool(responses) and all(item.payload.usage.completion_tokens is not None and item.payload.usage.reasoning_tokens is not None and item.payload.usage.reasoning_tokens <= item.payload.usage.completion_tokens for item in responses)
        output_bytes, reasoning_bytes = 0, 0
        for response in responses:
            body = _body_json(response.payload.body)
            choices = body.get("choices", []) if isinstance(body, dict) else []
            for choice in choices if isinstance(choices, list) else []:
                message = choice.get("message", {}) if isinstance(choice, dict) else {}
                if not isinstance(message, dict):
                    continue
                reasoning = message.get("reasoning_content")
                if isinstance(reasoning, str):
                    reasoning_bytes += len(reasoning.encode("utf8"))
                content = message.get("content")
                if isinstance(content, str):
                    output_bytes += len(content.encode("utf8"))
                tools = message.get("tool_calls", [])
                for tool in tools if isinstance(tools, list) else []:
                    function = tool.get("function", {}) if isinstance(tool, dict) else {}
                    args = function.get("arguments") if isinstance(function, dict) else None
                    if isinstance(args, str):
                        output_bytes += len(args.encode("utf8"))
                        if first.purpose == "planning" and function.get("name") == "submit_itinerary":
                            try:
                                fingerprint = hashlib.sha256(json.dumps(_business(json.loads(args)), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                                repeats[fingerprint].append((str(call_id), len(args.encode("utf8"))))
                            except (ValueError, TypeError):
                                pass
        rows.append((str(call_id), group, bool(summary and summary.admitted), len(attempts), usage,
            nonreasoning if all_parts_known else None, complete, summary.stop_code if summary else None, output_bytes, reasoning_bytes))
    run_summaries = [record.payload for record in records if record.record_kind == "run_summary"]
    run_complete = len(run_summaries) == 1 and run_summaries[0].records_complete and not missing
    if len(run_summaries) == 1:
        end = run_summaries[0]
        run_complete &= end.observed_admitted_calls == sum(row[2] for row in rows) and end.observed_provider_attempts == sum(row[3] for row in rows)
        run_complete &= end.record_failure_count == 0 and not end.missing_record_ids
    repeated = [items for items in repeats.values() if len(items) > 1]
    groups = {}
    for row in rows:
        grouped = groups.setdefault(row[1], {"admitted_calls": 0, "provider_attempts": 0, "known_usage": {}})
        grouped["admitted_calls"] += int(row[2])
        grouped["provider_attempts"] += row[3]
        for key, value in row[4].items():
            old = grouped["known_usage"].get(key)
            grouped["known_usage"][key] = (old or 0) + value if value is not None else old

    return {"run_summaries": run_summaries, "rows": rows, "groups": groups, "known_usage": {key: sum(values) if values else None for key, values in totals.items()},
        "records_complete": run_complete, "missing_calls": missing,
        "run_summary_count": len(run_summaries), "repeated_submissions": repeated}


def render_summary(run_id, records, *, database_available=True):
    data = summarize(records)
    def show(value):
        return "未知" if value is None else str(value)
    lines = [f"# 交互诊断：{run_id}", "", f"记录数：{len(records)}；数据库读取：{'成功' if database_available else '不可用，仅合并显式 fallback'}；诊断完整：{data['records_complete']}。",
        "", "token 只累计实际 response 的 usage。completion 已含 reasoning，不重复相加；缺失字段不填零。字节数不是 token 数或费用。", "",
        "| call_id | 模块/用途/请求阶段→预算类 | 准入 | 实际尝试 | 输入 | 缓存命中 | 未命中 | 输出(含思考) | 思考 | 非思考 | 完整 | 停止码 | 输出字节 | 思考字节 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|"]
    for call_id, group, admitted, attempts, usage, nonreasoning, complete, stop, output_bytes, reasoning_bytes in data["rows"]:
        lines.append("| " + " | ".join(map(str, (call_id, group, admitted, attempts, show(usage['prompt_tokens']), show(usage['cache_hit_tokens']), show(usage['cache_miss_tokens']), show(usage['completion_tokens']), show(usage['reasoning_tokens']), show(nonreasoning), complete, stop or "-", output_bytes, reasoning_bytes))) + " |")
    lines.extend(["", "按模块/用途/阶段/预算类汇总：", "", "| 分组 | 准入调用 | 实际尝试 | 已知输入 | 已知输出(含思考) | 已知思考 |", "|---|---:|---:|---:|---:|---:|"])
    for group, item in data["groups"].items():
        usage = item["known_usage"]
        lines.append(f"| {group} | {item['admitted_calls']} | {item['provider_attempts']} | {show(usage.get('prompt_tokens'))} | {show(usage.get('completion_tokens'))} | {show(usage.get('reasoning_tokens'))} |")
    lines.extend(["", "已知 usage 合计：`" + json.dumps(data['known_usage'], ensure_ascii=False, sort_keys=True) + "`。", "",
        f"结束摘要数量：{data['run_summary_count']}；不完整调用：{', '.join(data['missing_calls']) or '无'}。", "",
        "重复提交按去除主题/备注/描述后的业务指纹比较，只报告次数、正文大小和调用位置，不估算片段 token 或费用。"])
    for repeated in data['repeated_submissions']:
        lines.append(f"- 相同业务提交 {len(repeated)} 次：" + ", ".join(f"{call_id}（{size} 字节）" for call_id, size in repeated))
    lines.extend(["", "结束摘要及完整性对账："])
    for end in data["run_summaries"]:
        lines.append(f"- 根停止码：{end.stop_code or '-'}；声明准入调用/尝试={end.observed_admitted_calls}/{end.observed_provider_attempts}，实际已记录={sum(row[2] for row in data['rows'])}/{sum(row[3] for row in data['rows'])}；记录失败={end.record_failure_count}；usage 完整={end.known_usage.complete}；缺失记录ID={','.join(map(str, end.missing_record_ids)) or '无'}。")
    if not data["records_complete"]:
        lines.append("- 固定原因：interaction_records_incomplete（调用/尝试/处理/结束摘要缺失或对账不一致；已知用量保留）。")
    feedback = [record for record in records if record.record_kind in {"validation", "tool_result"}]
    lines.extend(["", "处理与工具反馈（正文见 JSONL）："])
    for record in feedback:
        payload = record.payload
        detail = f"{payload.stage}: {'通过' if payload.passed else '拒绝'} {payload.code or '-'} {payload.field or '-'}" if record.record_kind == 'validation' else f"{payload.tool_name}[{payload.ordinal}]: {payload.outcome}"
        lines.append(f"- record={record.record_id} call={record.call_id} attempt={record.provider_attempt or '-'}：{detail}")
    return "\n".join(lines) + "\n"


def write_export(run_id, output_dir, records, *, database_available=True):
    records = merge_records(run_id, records)
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = (output_dir / 'interactions.jsonl', output_dir / 'summary.md')
    if any(target.exists() for target in targets):
        raise ExportError("export_output_exists")
    summary = render_summary(run_id, records, database_available=database_available)
    with targets[0].open('xb') as stream:
        for record in records:
            stream.write(canonical_record(record) + b'\n')
    with targets[1].open('x', encoding='utf8', newline='\n') as stream:
        stream.write(summary)
    return targets


async def export_run(run_id, output_dir, fallback_dir=None):
    available = True
    database = ()
    try:
        settings = load_artifact_persistence_settings()
        conninfo = make_conninfo(host=settings.host, port=settings.port, dbname=settings.database, user=settings.user,
            password=settings.password.get_secret_value(), connect_timeout=5,
            options='-c default_transaction_read_only=on -c statement_timeout=5000')
        async with AsyncConnectionPool(conninfo, min_size=0, max_size=1, timeout=5) as pool:
            recorder = _PostgresInteractionRecorder(pool=pool, limits=DEMO_INTERACTION_STORE_LIMITS, fallback_dir=None)
            database = await recorder.read_run(run_id)
    except Exception:
        if fallback_dir is None:
            raise ExportError('export_database_unavailable') from None
        available = False
    records = merge_records(run_id, database, fallback_records(fallback_dir, run_id))
    return write_export(run_id, output_dir, records, database_available=available)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', type=UUID, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    fallback = parser.add_mutually_exclusive_group()
    fallback.add_argument('--fallback-dir', type=Path)
    fallback.add_argument('--fallback-file', type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(export_run(args.run_id, args.output_dir, args.fallback_file or args.fallback_dir))
    except ExportError as error:
        parser.exit(2, str(error) + '\n')
    print('interaction_export_saved')


if __name__ == '__main__':
    main()

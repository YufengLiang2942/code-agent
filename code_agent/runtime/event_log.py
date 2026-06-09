# code_agent/runtime/event_log.py

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@dataclass
class Event:
    id: str = field(default_factory=lambda: str(uuid4()))
    run_id: str = ""
    trace_id: str = ""
    type: str = ""
    actor: str = "system"

    tool_name: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None

    status: str = "ok"
    error: str | None = None
    duration_ms: int | None = None

    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: Event) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def info(
        self,
        *,
        run_id: str,
        trace_id: str,
        type: str,
        actor: str = "system",
        tool_name: str | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        duration_ms: int | None = None,
    ):
        self.write(Event(
            run_id=run_id,
            trace_id=trace_id,
            type=type,
            actor=actor,
            tool_name=tool_name,
            input_summary=input_summary,  # 截断的输出
            output_summary=output_summary, # 截断的输出
            duration_ms=duration_ms,
            status="ok",
        ))

    def error(
        self,
        *,
        run_id: str,
        trace_id: str,
        type: str,
        error: str,
        actor: str = "system",
        tool_name: str | None = None,
        input_summary: str | None = None,
        duration_ms: int | None = None,
    ):
        self.write(Event(
            run_id=run_id,
            trace_id=trace_id,
            type=type,
            actor=actor,
            tool_name=tool_name,
            input_summary=input_summary,
            error=error,
            duration_ms=duration_ms,
            status="error",
        ))


def now_ms() -> int:
    return int(time.time() * 1000)


def summarize(value, limit: int = 500) -> str:
    text = str(value)

    if len(text) > limit:
        return text[:limit] + f"... ({len(text) - limit} more chars)"

    return text
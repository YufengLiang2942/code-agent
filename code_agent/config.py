# code_agent/config.py

import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Settings:
    workdir: Path
    model_id: str
    anthropic_base_url: str | None
    skills_dir: Path
    tasks_dir: Path
    memory_dir: Path
    transcript_dir: Path
    tool_results_dir: Path
    worktrees_dir: Path
    event_log_dir: Path
    bus_dir: Path
    protocol_dir: Path
    default_max_tokens: int = 8000
    context_limit: int = 50000
    large_output_limit: int = 10000


def load_settings() -> Settings:
    load_dotenv(override=True)

    workdir = Path.cwd()

    return Settings(
        workdir=workdir,
        model_id=os.environ["MODEL_ID"],
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL"),
        skills_dir=workdir / "skills",
        tasks_dir=workdir / ".tasks",
        memory_dir=workdir / ".memory",
        transcript_dir=workdir / ".transcripts",
        tool_results_dir=workdir / ".task_outputs" / "tool-results",
        worktrees_dir=workdir / ".worktrees",
        event_log_dir=workdir / ".logs" / "events.jsonl",
        bus_dir=workdir / ".mailboxes",
        protocol_dir=workdir / ".protocols",
    )
# code_agent/capabilities/worktrees.py

import json
import re
import time
from pathlib import Path

from code_agent.infra.git import GitService
from code_agent.capabilities.tasks import TaskService
from code_agent.tools.registry import Tool, ToolRegistry


VALID_WORKTREE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class WorktreeService:
    def __init__(
        self,
        workdir: Path,
        worktrees_dir: Path,
        git_service: GitService,
        task_service: TaskService | None = None,
    ):
        self.workdir = workdir
        self.worktrees_dir = worktrees_dir
        self.git = git_service
        self.task_service = task_service

        self.worktrees_dir.mkdir(exist_ok=True)

    def validate_name(self, name: str) -> str | None:
        """工作树名称校验"""
        if not name:
            return "Worktree name cannot be empty"

        if name in {".", ".."}:
            return f"'{name}' is not a valid worktree name"

        if not VALID_WORKTREE_NAME.match(name):
            return (
                f"Invalid worktree name '{name}': "
                "only letters, digits, dots, underscores, and dashes are allowed, max length 64"
            )

        return None

    def create_worktree(self, name: str, task_id: str = "") -> str:
        """创建工作树"""
        err = self.validate_name(name)

        if err:
            return f"Error: {err}"

        if not self.git.is_git_repo():
            return "Error: current workspace is not a git repository"

        if task_id and self.task_service:
            try:
                self.task_service.load(task_id)
            except FileNotFoundError:
                return f"Error: task {task_id} not found"

        path = self.worktrees_dir / name

        if path.exists():
            return f"Worktree '{name}' already exists at {path}"

        branch_name = f"wt/{name}"

        ok, output = self.git.run_git([
            "worktree",
            "add",
            str(path),
            "-b",
            branch_name,
            "HEAD",
        ])

        if not ok:
            return f"Git error: {output}"

        if task_id and self.task_service:
            self.bind_task_to_worktree(task_id, name)

        self.log_event("create", name, task_id)

        return f"Worktree '{name}' created at {path} on branch {branch_name}"

    def bind_task_to_worktree(self, task_id: str, worktree_name: str) -> str:
        if not self.task_service:
            return "Error: task service is not configured"

        task = self.task_service.load(task_id)
        task.worktree = worktree_name
        self.task_service.save(task)

        return f"Task {task_id} bound to worktree {worktree_name}"

    def remove_worktree(self, name: str, discard_changes: bool = False) -> str:
        err = self.validate_name(name)

        if err:
            return f"Error: {err}"

        path = self.worktrees_dir / name

        if not path.exists():
            return f"Worktree '{name}' not found"

        if not discard_changes:
            changes_count = self.git.count_changes(path)

            if changes_count > 0:
                return (
                    f"Worktree '{name}' has {changes_count} changed file(s). "
                    "Use discard_changes=true to force remove, or keep_worktree for review."
                )

        ok, output = self.git.run_git([
            "worktree",
            "remove",
            str(path),
            "--force",
        ])

        if not ok:
            return f"Failed to remove worktree '{name}': {output}"

        # 删除对应分支。失败不阻断，因为分支可能不存在或未合并。
        self.git.run_git(["branch", "-D", f"wt/{name}"])

        self.log_event("remove", name)

        return f"Worktree '{name}' removed"

    def keep_worktree(self, name: str) -> str:
        err = self.validate_name(name)

        if err:
            return f"Error: {err}"

        path = self.worktrees_dir / name

        if not path.exists():
            return f"Worktree '{name}' not found"

        self.log_event("keep", name)

        return f"Worktree '{name}' kept for review at {path} on branch wt/{name}"

    def list_worktrees(self) -> str:
        ok, output = self.git.run_git(["worktree", "list"])

        if not ok:
            return f"Git error: {output}"

        return output

    def log_event(self, event_type: str, worktree_name: str, task_id: str = "") -> None:
        event = {
            "type": event_type,
            "worktree": worktree_name,
            "task_id": task_id,
            "ts": time.time(),
        }

        events_file = self.worktrees_dir / "events.jsonl"

        with events_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def register_worktree_tools(registry: ToolRegistry, service: WorktreeService) -> None:
    registry.register(Tool(
        name="create_worktree",
        description=(
            "Create an isolated git worktree for a task or feature. "
            "Use this when the user wants an isolated workspace."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["name"],
        },
        handler=service.create_worktree,
    ))

    registry.register(Tool(
        name="remove_worktree",
        description=(
            "Remove a git worktree. Refuses if there are local changes unless discard_changes is true."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "discard_changes": {"type": "boolean"},
            },
            "required": ["name"],
        },
        handler=service.remove_worktree,
    ))

    registry.register(Tool(
        name="keep_worktree",
        description="Keep a worktree for manual review instead of removing it.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
        handler=service.keep_worktree,
    ))

    registry.register(Tool(
        name="list_worktrees",
        description="List existing git worktrees.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=service.list_worktrees,
    ))
# code_agent/capabilities/tasks.py

import json
import time
import random
from pathlib import Path
from dataclasses import dataclass, asdict

from code_agent.tools.registry import Tool, ToolRegistry


from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import os
import time
from contextlib import contextmanager


TASK_LEASE_TIMEOUT_SECONDS = 120


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blockedBy: list[str]

    worktree: str | None = None
    error: str | None = None

    attempts: int = 0 #  已经尝试执行几次
    max_attempts: int = 3 # 最多重试几次

    claimed_at: float | None = None # 被 worker 认领的时间
    heartbeat_at: float | None = None # worker 最近一次心跳
    completed_at: float | None = None  # 完成时间
    failed_at: float | None = None  # 最近失败时间

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        data.setdefault("worktree", None)
        data.setdefault("error", None)
        data.setdefault("attempts", 0)
        data.setdefault("max_attempts", 3)
        data.setdefault("claimed_at", None)
        data.setdefault("heartbeat_at", None)
        data.setdefault("completed_at", None)
        data.setdefault("failed_at", None)
        data.setdefault("created_at", time.time())
        data.setdefault("updated_at", time.time())
        return cls(**data)

class TaskService:
    def __init__(self, tasks_dir: Path):
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(exist_ok=True)

    def _task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.json"
    
    def _lock_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.lock"

    @contextmanager
    # 加文件锁
    def task_lock(self, task_id: str):
        # 不要把锁持有到任务执行结束。
        lock_path = self._lock_path(task_id)
        fd = None

        try:
            fd = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.write(fd, str(os.getpid()).encode("utf-8"))
            yield

        except FileExistsError:
            raise RuntimeError(f"Task {task_id} is locked by another worker")

        finally:
            if fd is not None:
                os.close(fd)

            if lock_path.exists():
                lock_path.unlink()

    def save(self, task: Task) -> None:
        task.updated_at = time.time()

        self._task_path(task.id).write_text(
            json.dumps(asdict(task), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


    def load(self, task_id: str) -> Task:
        data = json.loads(
            self._task_path(task_id).read_text(encoding="utf-8")
        )
        return Task.from_dict(data)


    def list_tasks(self) -> list[Task]:
        tasks = []

        for path in sorted(self.tasks_dir.glob("task_*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            tasks.append(Task.from_dict(data))

        return tasks

    def create(self, subject: str, description: str = "", blockedBy: list[str] | None = None) -> Task:
        task = Task(
            id=f"task_{int(time.time())}_{random.randint(0, 9999):04d}",
            subject=subject,
            description=description,
            status="pending",
            owner=None,
            blockedBy=blockedBy or [],
        )
        self.save(task)
        return task

    def can_start(self, task_id: str) -> bool:
        task = self.load(task_id)

        for dep_id in task.blockedBy:
            dep_path = self._task_path(dep_id)

            if not dep_path.exists():
                return False

            if self.load(dep_id).status != "completed":
                return False

        return True
    
    def is_stale(self, task: Task, lease_timeout: int = TASK_LEASE_TIMEOUT_SECONDS) -> bool:
        # 判断任务是否过期

        # 状态需要是 in_progress
        if task.status != "in_progress":
            return False

        # 没有心跳，worker第一次创建后就崩了
        if not task.heartbeat_at:
            return True

        # 判断当前时间和最近一次心跳是否超时
        return time.time() - task.heartbeat_at >  lease_timeout
    
    def heartbeat(self, task_id: str, owner: str = "agent") -> str:
        try:
            with self.task_lock(task_id):
                task = self.load(task_id)

                if task.status != "in_progress":
                    return f"Task {task_id} is not in_progress"

                if task.owner != owner:
                    return f"Task {task_id} is owned by {task.owner}, not {owner}"

                task.heartbeat_at = time.time()
                self.save(task)

                return f"Heartbeat updated for {task_id}"

        except RuntimeError as e:
            return f"Error: {e}"

    def claim(self, task_id: str, owner: str = "agent") -> str:
        try:
            with self.task_lock(task_id):
                task = self.load(task_id)

                if not self.can_start(task_id):
                    return "Cannot start — task has unfinished dependencies"

                can_claim = False

                if task.status in {"pending", "failed"}:
                    can_claim = True

                elif self.is_stale(task):
                    can_claim = True

                if not can_claim:
                    return f"Task {task_id} is {task.status}, cannot claim"

                if task.attempts >= task.max_attempts:
                    return (
                        f"Task {task_id} reached max attempts "
                        f"({task.attempts}/{task.max_attempts})"
                    )

                task.status = "in_progress"
                task.owner = owner
                task.attempts += 1
                task.claimed_at = time.time()
                task.heartbeat_at = time.time()
                task.error = None
                task.failed_at = None

                self.save(task)

                return f"Claimed {task.id} ({task.subject}) by {owner}"

        except RuntimeError as e:
            return f"Error: {e}"

    def complete(self, task_id: str, owner: str = "agent") -> str:
        try:
            with self.task_lock(task_id):
                task = self.load(task_id)
                # 状态校验
                if task.status != "in_progress":
                    return f"Task {task_id} is {task.status}, cannot complete"
                # 归属校验
                if task.owner and task.owner != owner:
                    return f"Task {task_id} is owned by {task.owner}, not {owner}"

                task.status = "completed"
                task.completed_at = time.time()
                task.heartbeat_at = None
                self.save(task)

                return f"Completed {task.id}"

        except RuntimeError as e:
            return f"Error: {e}"
    
    def fail(self, task_id: str, error: str = "", owner: str = "agent") -> str:
        try:
            with self.task_lock(task_id):
                task = self.load(task_id)

                if task.owner and task.owner != owner:
                    return f"Task {task_id} is owned by {task.owner}, not {owner}"

                task.status = "failed"
                task.error = error
                task.failed_at = time.time()
                task.heartbeat_at = None

                self.save(task)

                return f"Task {task_id} marked as failed"

        except RuntimeError as e:
            return f"Error: {e}"
        
    def recover_stale_tasks(self, lease_timeout: int = TASK_LEASE_TIMEOUT_SECONDS) -> str:
        recovered = []

        for task in self.list_tasks():
            if not self.is_stale(task, lease_timeout):
                continue

            try:
                with self.task_lock(task.id):
                    current = self.load(task.id)

                    if not self.is_stale(current, lease_timeout):
                        continue

                    current.status = "failed"
                    current.error = (
                        f"Task lease expired. Last owner={current.owner}, "
                        f"last heartbeat={current.heartbeat_at}"
                    )
                    current.failed_at = time.time()
                    current.heartbeat_at = None

                    self.save(current)
                    recovered.append(current.id)

            except RuntimeError:
                continue

        if not recovered:
            return "No stale tasks recovered."

        return "Recovered stale tasks: " + ", ".join(recovered)


def register_task_tools(registry: ToolRegistry, service: TaskService):
    registry.register(Tool(
        name="create_task",
        description="Create a task.",
        input_schema={
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "blockedBy": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["subject"],
        },
        handler=lambda subject, description="", blockedBy=None: (
            f"Created {service.create(subject, description, blockedBy).id}: {subject}"
        ),
    ))

    registry.register(Tool(
        name="list_tasks",
        description="List all tasks.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=lambda: "\n".join(
            f"{task.id}: {task.subject} [{task.status}]"
            + (f" error={task.error[:80]}" if getattr(task, "error", None) else "")
            for task in service.list_tasks()
        ) or "No tasks."
    ))

    registry.register(Tool(
        name="claim_task",
        description="Claim a pending task.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        handler=service.claim,
    ))

    registry.register(Tool(
        name="complete_task",
        description="Complete an in-progress task.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
        handler=service.complete,
    ))

    registry.register(Tool(
        name="fail_task",
        description="Mark an in-progress task as failed with an error reason.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "error": {"type": "string"},
            },
            "required": ["task_id"],
        },
        handler=service.fail,
    ))

    registry.register(Tool(
        name="heartbeat_task",
        description="Update heartbeat for an in-progress task owned by a worker.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "owner": {"type": "string"},
            },
            "required": ["task_id"],
        },
        handler=service.heartbeat,
    ))

    registry.register(Tool(
        name="recover_stale_tasks",
        description="Recover tasks whose worker heartbeat expired.",
        input_schema={
            "type": "object",
            "properties": {
                "lease_timeout": {"type": "integer"},
            },
            "required": [],
        },
        handler=service.recover_stale_tasks,
    ))
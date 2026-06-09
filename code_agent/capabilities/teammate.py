# code_agent/capabilities/teammate.py

import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4

from code_agent.capabilities.tasks import TaskService, Task
from code_agent.capabilities.subagent import SubagentService
from code_agent.runtime.event_log import EventLog, now_ms, summarize
from code_agent.tools.registry import Tool, ToolRegistry


@dataclass
class WorkerState:
    id: str
    name: str
    status: str = "idle"
    current_task_id: str | None = None
    started_at: float = field(default_factory=time.time)
    heartbeat_at: float = field(default_factory=time.time)
    stop_requested: bool = False
    last_error: str | None = None


class TeammateService:
    def __init__(
        self,
        *,
        task_service: TaskService,
        subagent_service: SubagentService,
        event_log: EventLog | None = None,
        default_worker_name: str = "worker",
        heartbeat_interval: int = 10,
        idle_sleep: int = 3,
    ):
        self.task_service = task_service
        self.subagent_service = subagent_service
        self.event_log = event_log
        self.default_worker_name = default_worker_name
        self.heartbeat_interval = heartbeat_interval
        self.idle_sleep = idle_sleep

        self._workers: dict[str, WorkerState] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        
    def start_worker(
		self,
		name: str = "worker",
		max_tasks: int = 1,
		run_forever: bool = False,
	) -> str:
        worker_id = f"worker_{uuid4().hex[:8]}"
        state = WorkerState(
			id=worker_id,
			name=name,
			status="starting",
		)
        with self._lock:
            self._workers[worker_id] = state
            
        thread = threading.Thread(
                target=self._worker_loop,
				args=(worker_id, max_tasks, run_forever),
				daemon=True,
            )
        
        with self._lock:
            self._threads[worker_id] = thread
            
        thread.start()
        return f"Worker {name} started: {worker_id}"
    
    def stop_worker(self, worker_id: str) -> str:
        with self._lock:
            state = self._workers.get(worker_id)

            if not state:
                return f"Worker {worker_id} not found"

            state.stop_requested = True
        return f"Stop requested for {worker_id}"
    
    def list_workers(self) -> str:
        with self._lock:
            workers = list(self._workers.values())

        if not workers:
            return "No workers."

        lines = []

        for worker in workers:
            lines.append(
                f"{worker.id}: name={worker.name}, status={worker.status}, "
                f"task={worker.current_task_id}, stop={worker.stop_requested}, "
                f"last_error={worker.last_error}"
            )

        return "\n".join(lines)
    
    def _worker_loop(
        self,
        worker_id: str,
        max_tasks: int,
        run_forever: bool,
    ) -> None:
        processed = 0

        state = self._get_worker(worker_id)

        if not state:
            return

        self._log_info(
            worker_id=worker_id,
            type="worker_start",
            input_summary=f"max_tasks={max_tasks}, run_forever={run_forever}",
        )

        try:
            while True:
                state = self._get_worker(worker_id)

                if not state or state.stop_requested:
                    break

                if max_tasks > 0 and processed >= max_tasks:
                    break

                self.task_service.recover_stale_tasks()

                task = self._claim_next_task(state.name)

                if not task:
                    state.status = "idle"

                    if not run_forever:
                        break

                    time.sleep(self.idle_sleep)
                    continue

                state.status = "running"
                state.current_task_id = task.id
                state.heartbeat_at = time.time()

                self._execute_task(worker_id, task)

                processed += 1

                state.current_task_id = None
                state.status = "idle"

        except Exception as e:
            state = self._get_worker(worker_id)

            if state:
                state.status = "failed"
                state.last_error = f"{type(e).__name__}: {e}"

            self._log_error(
                worker_id=worker_id,
                type="worker_error",
                error=f"{type(e).__name__}: {e}",
            )

        finally:
            state = self._get_worker(worker_id)

            if state:
                if state.status not in {"failed"}:
                    state.status = "stopped"
                state.heartbeat_at = time.time()

            self._log_info(
                worker_id=worker_id,
                type="worker_end",
                output_summary=f"processed={processed}",
            )

    def _execute_task(self, worker_id: str, task: Task) -> None:
        state = self._get_worker(worker_id)

        if not state:
            return

        owner = state.name

        self._log_info(
            worker_id=worker_id,
            type="task_start",
            input_summary=f"{task.id}: {task.subject}",
        )

        heartbeat_stop = threading.Event()

        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(task.id, owner, heartbeat_stop),
            daemon=True,
        )
        heartbeat_thread.start()

        try:
            prompt = self._build_task_prompt(task, owner)

            result = self.subagent_service.spawn_subagent(
                prompt=prompt,
                task_id=task.id,
                role=owner,
            )

            if self._looks_failed(result):
                self.task_service.fail(
                    task.id,
                    error=result,
                    owner=owner,
                )

                self._log_error(
                    worker_id=worker_id,
                    type="task_failed",
                    error=summarize(result),
                )
            else:
                self.task_service.complete(
                    task.id,
                    owner=owner,
                )

                self._log_info(
                    worker_id=worker_id,
                    type="task_completed",
                    output_summary=summarize(result),
                )

        except Exception as e:
            error = f"{type(e).__name__}: {e}"

            self.task_service.fail(
                task.id,
                error=error,
                owner=owner,
            )

            self._log_error(
                worker_id=worker_id,
                type="task_error",
                error=error,
            )

        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)

    def _claim_next_task(self, worker_name: str) -> Task | None:
        tasks = self.task_service.list_tasks()

        for task in tasks:
            if task.status not in {"pending", "failed", "in_progress"}:
                continue

            if not self.task_service.can_start(task.id):
                continue

            claim_result = self.task_service.claim(task.id, owner=worker_name)

            if claim_result.startswith("Claimed"):
                return self.task_service.load(task.id)

        return None

    def _heartbeat_loop(
        self,
        task_id: str,
        owner: str,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.is_set():
            self.task_service.heartbeat(task_id, owner=owner)
            time.sleep(self.heartbeat_interval)

    def _looks_failed(self, result: str) -> bool:
        lower = result.lower()

        failure_markers = [
            "status: failed",
            "status: max_rounds",
            "error:",
            "traceback",
            "cannot complete",
            "无法完成",
            "失败",
        ]

        return any(marker in lower for marker in failure_markers)
    
    def _build_task_prompt(self, task: Task, owner: str) -> str:
        return (
            f"You are teammate worker '{owner}'.\n"
            "Execute the assigned task.\n\n"
            f"Task ID: {task.id}\n"
            f"Subject: {task.subject}\n"
            f"Description:\n{task.description}\n\n"
            "Execution requirements:\n"
            "- Inspect relevant files before modifying them.\n"
            "- Make minimal necessary changes.\n"
            "- Do not modify unrelated files.\n"
            "- Do not delete files unless the task explicitly requires deletion.\n"
            "- If completed, summarize what changed.\n"
            "- If impossible, clearly state status: failed and explain why.\n"
        )
    
    def _get_worker(self, worker_id: str) -> WorkerState | None:
        with self._lock:
            return self._workers.get(worker_id)


    def _log_info(
        self,
        *,
        worker_id: str,
        type: str,
        input_summary: str | None = None,
        output_summary: str | None = None,
    ) -> None:
        if not self.event_log:
            return

        self.event_log.info(
            run_id="teammate",
            trace_id=worker_id,
            type=type,
            actor="teammate",
            input_summary=input_summary,
            output_summary=output_summary,
        )


    def _log_error(
        self,
        *,
        worker_id: str,
        type: str,
        error: str,
    ) -> None:
        if not self.event_log:
            return

        self.event_log.error(
            run_id="teammate",
            trace_id=worker_id,
            type=type,
            actor="teammate",
            error=error,
        )

def register_teammate_tools(
    registry: ToolRegistry,
    service: TeammateService,
) -> None:
    registry.register(Tool(
        name="start_teammate_worker",
        description=(
            "Start a high-availability teammate worker. "
            "It claims pending/failed/stale tasks with locks, sends heartbeats, "
            "executes tasks via subagent, and completes or fails tasks."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "max_tasks": {"type": "integer"},
                "run_forever": {"type": "boolean"},
            },
            "required": [],
        },
        handler=service.start_worker,
    ))

    registry.register(Tool(
        name="stop_teammate_worker",
        description="Request a teammate worker to stop gracefully after its current task.",
        input_schema={
            "type": "object",
            "properties": {
                "worker_id": {"type": "string"},
            },
            "required": ["worker_id"],
        },
        handler=service.stop_worker,
    ))

    registry.register(Tool(
        name="list_teammate_workers",
        description="List teammate workers and their states.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=service.list_workers,
    ))
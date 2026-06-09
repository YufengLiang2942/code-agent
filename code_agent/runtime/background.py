# code_agent/runtime/background.py

import threading
from dataclasses import dataclass, field
from typing import Callable

from code_agent.runtime.event_log import EventLog, now_ms, summarize


@dataclass
class BackgroundTask:
	id: str
	tool_use_id: str
	tool_name: str
	command: str
	status: str = "running"
	result: str | None = None


class BackgroundTaskManager:
	def __init__(self, event_log: EventLog | None = None):
		self.event_log = event_log
		self._counter = 0
		self._tasks: dict[str, BackgroundTask] = {}
		self._lock = threading.Lock()

	def should_run_background(self, tool_name: str, tool_input: dict) -> bool:
		"""
		判断某个工具是否应该后台执行。
		目前只对 bash 做后台执行判断。
		"""
		if tool_name != "bash":
			return False

		if tool_input.get("run_in_background"):
			return True

		command = tool_input.get("command", "").lower()

		slow_keywords = [
			"install",
			"build",
			"test",
			"deploy",
			"compile",
			"docker build",
			"pip install",
			"npm install",
			"pnpm install",
			"yarn install",
			"pytest",
			"make",
		]

		return any(keyword in command for keyword in slow_keywords)

	def start(
		self,
		*,
		tool_use_id: str,
		tool_name: str,
		tool_input: dict,
		handler: Callable,
		run_id: str = "",
		trace_id: str = "",
	) -> str:
		"""
		启动后台任务，立即返回 bg_id。
		"""
		with self._lock:
			self._counter += 1
			bg_id = f"bg_{self._counter:04d}"

			command = tool_input.get("command", tool_name)

			self._tasks[bg_id] = BackgroundTask(
				id=bg_id,
				tool_use_id=tool_use_id,
				tool_name=tool_name,
				command=str(command),
			)

		if self.event_log:
			self.event_log.info(
				run_id=run_id,
				trace_id=trace_id,
				type="background_start",
				actor="runtime",
				tool_name=tool_name,
				input_summary=summarize(tool_input),
				output_summary=f"background task {bg_id} started",
			)

		thread = threading.Thread(
			target=self._worker,
			args=(bg_id, handler, tool_input, run_id, trace_id),
			daemon=True,
		)
		thread.start()

		return bg_id

	def _worker(
		self,
		bg_id: str,
		handler: Callable,
		tool_input: dict,
		run_id: str,
		trace_id: str,
	) -> None:
		start = now_ms()

		try:
			output = str(handler(**(tool_input or {})))
			status = "completed"

		except Exception as e:
			output = f"Error: {type(e).__name__}: {e}"
			status = "failed"

		with self._lock:
			task = self._tasks.get(bg_id)

			if task:
				task.status = status
				task.result = output

		if self.event_log:
			if status == "completed":
				self.event_log.info(
					run_id=run_id,
					trace_id=trace_id,
					type="background_end",
					actor="runtime",
					tool_name=task.tool_name if task else None,
					input_summary=summarize(tool_input),
					output_summary=summarize(output),
					duration_ms=now_ms() - start,
				)
			else:
				self.event_log.error(
					run_id=run_id,
					trace_id=trace_id,
					type="background_error",
					actor="runtime",
					tool_name=task.tool_name if task else None,
					input_summary=summarize(tool_input),
					error=summarize(output),
					duration_ms=now_ms() - start,
				)

	def collect_notifications(self) -> list[str]:
		"""
		收集已经完成的后台任务结果。

		返回文本通知列表，供 AgentRuntime 注入 messages。
		"""
		with self._lock:
			ready_ids = [
				bg_id
				for bg_id, task in self._tasks.items()
				if task.status in {"completed", "failed"}
			]

			ready_tasks = [
				self._tasks.pop(bg_id)
				for bg_id in ready_ids
			]

		notifications = []

		for task in ready_tasks:
			result = task.result or ""

			preview = result[:1000]
			if len(result) > 1000:
				preview += f"\n... ({len(result) - 1000} more chars)"

			notifications.append(
				"<task_notification>\n"
				f"  <task_id>{task.id}</task_id>\n"
				f"  <tool>{task.tool_name}</tool>\n"
				f"  <command>{task.command}</command>\n"
				f"  <status>{task.status}</status>\n"
				f"  <result>{preview}</result>\n"
				"</task_notification>"
			)

		return notifications
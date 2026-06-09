# code_agent/capabilities/subagent.py

from dataclasses import dataclass
from typing import Any

from code_agent.runtime.event_log import EventLog, now_ms, summarize
from code_agent.tools.registry import Tool, ToolRegistry


@dataclass
class SubagentResult:
    content: str
    rounds: int
    status: str = "ok"


class SubagentService:
    def __init__(
        self,
        *,
        model_provider,
        tool_registry: ToolRegistry,
        event_log: EventLog | None = None,
        max_rounds: int = 5,
        allowed_tools: set[str] | None = None,
    ):
        self.model_provider = model_provider
        self.tool_registry = tool_registry
        self.event_log = event_log
        self.max_rounds = max_rounds
        self.allowed_tools = allowed_tools

    def spawn_subagent(
        self,
        prompt: str,
        task_id: str = "",
        role: str = "worker",
    ) -> str:
        """
        同步执行一个 subagent 子任务。
        """
        start = now_ms()

        if self.event_log:
            self.event_log.info(
                run_id="subagent",
                trace_id=task_id or "no_task",
                type="subagent_start",
                actor=role,
                input_summary=summarize(prompt),
            )

        result = self._run_subagent(
            prompt=prompt,
            task_id=task_id,
            role=role,
        )

        if self.event_log:
            self.event_log.info(
                run_id="subagent",
                trace_id=task_id or "no_task",
                type="subagent_end",
                actor=role,
                output_summary=summarize(result.content),
                duration_ms=now_ms() - start,
            )

        return (
            f"[Subagent {role} finished]\n"
            f"status: {result.status}\n"
            f"rounds: {result.rounds}\n\n"
            f"{result.content}"
        )

    def _run_subagent(
        self,
        *,
        prompt: str,
        task_id: str,
        role: str,
    ) -> SubagentResult:
        messages: list[dict] = [
            {
                "role": "user",
                "content": prompt,
            }
        ]

        system = self._build_subagent_system(role=role, task_id=task_id)

        for round_index in range(1, self.max_rounds + 1):
            response = self.model_provider.generate(
                messages=messages,
                system=system,
                tools=self._schemas_for_subagent(),
                max_tokens=4000,
            )

            messages.append({
                "role": "assistant",
                "content": response.content,
            })

            tool_blocks = [
                block
                for block in response.content
                if getattr(block, "type", None) == "tool_use"
            ]

            if not tool_blocks:
                return SubagentResult(
                    content=self._extract_text(response.content),
                    rounds=round_index,
                    status="ok",
                )

            results = []

            for block in tool_blocks:
                if not self._is_tool_allowed(block.name):
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": (
                            f"Tool '{block.name}' is not allowed for this subagent. "
                            "Finish with the available information."
                        ),
                    })
                    continue

                output = self.tool_registry.dispatch(
                    block.name,
                    block.input,
                )

                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

            messages.append({
                "role": "user",
                "content": results,
            })

        return SubagentResult(
            content=(
                "Subagent reached max rounds without a final answer. "
                "Last assistant message:\n"
                + self._extract_text(messages[-2].get("content", ""))
            ),
            rounds=self.max_rounds,
            status="max_rounds",
        )

    def _build_subagent_system(self, role: str, task_id: str) -> str:
        return (
            f"You are a focused subagent with role: {role}.\n"
            "Work on only the delegated task. Be concise.\n"
            "Do not start unrelated work.\n"
            "If you need to inspect files, use read_file or glob.\n"
            "If you modify files, explain exactly what changed.\n"
            "Do not delete files unless explicitly asked.\n"
            f"Task id: {task_id or '(none)'}"
        )

    def _schemas_for_subagent(self) -> list[dict]:
        schemas = self.tool_registry.schemas()

        if self.allowed_tools is None:
            return schemas

        return [
            schema
            for schema in schemas
            if schema["name"] in self.allowed_tools
        ]

    def _is_tool_allowed(self, tool_name: str) -> bool:
        if self.allowed_tools is None:
            return True

        return tool_name in self.allowed_tools

    def _extract_text(self, content: Any) -> str:
        if not isinstance(content, list):
            return str(content)

        texts = []

        for block in content:
            if getattr(block, "type", None) == "text":
                texts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))

        return "\n".join(texts).strip()


def register_subagent_tools(
    registry: ToolRegistry,
    service: SubagentService,
) -> None:
    registry.register(Tool(
        name="spawn_subagent",
        description=(
            "Run a focused synchronous subagent for a delegated coding or analysis task. "
            "Use this when the user asks to split work, review something independently, "
            "or delegate a subtask. The subagent runs in a separate conversation and returns a summary."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "task_id": {"type": "string"},
                "role": {"type": "string"},
            },
            "required": ["prompt"],
        },
        handler=service.spawn_subagent,
    ))
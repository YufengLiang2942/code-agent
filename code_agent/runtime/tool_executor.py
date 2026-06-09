# code_agent/runtime/tool_executor.py

from dataclasses import dataclass
from code_agent.runtime.event_log import EventLog, now_ms, summarize
from code_agent.runtime.background import BackgroundTaskManager

@dataclass
class ToolExecutionResult:
    results: list[dict]
    should_stop: bool = False

def is_destructive_bash(block) -> bool:
    """检查是否通过bash执行删除操作"""
    if getattr(block, "name", None) != "bash":
        return False

    command = (block.input or {}).get("command", "").lower()

    destructive_tokens = [
        "del ",
        "erase ",
        "rm ",
        "rmdir ",
        "rd ",
        "remove-item",
        "os.remove",
        "unlink",
    ]

    return any(token in command for token in destructive_tokens)


class ToolExecutor:
    """工具执行"""
    def __init__(self, tool_registry, hook_manager=None, 
                 event_log: EventLog | None = None,
                 background_manager: BackgroundTaskManager | None = None):
        self.tool_registry = tool_registry
        self.hooks = hook_manager
        self.event_log = event_log
        self.background_manager = background_manager

    def execute(self, tool_blocks: list, run_id: str = "", trace_id: str = "") -> ToolExecutionResult:
        results = []
        should_stop = False

        for block in tool_blocks:
            start = now_ms()

            if self.event_log:
                self.event_log.info(
                    run_id=run_id,
                    trace_id=trace_id,
                    type="tool_start",
                    actor="agent",
                    tool_name=block.name,
                    input_summary=summarize(block.input),
                )

            blocked = self._run_pre_hooks(block)

            if blocked:
                output = str(blocked)
                if self.event_log:
                    self.event_log.info(
                        run_id=run_id,
                        trace_id=trace_id,
                        type="tool_blocked",
                        actor="permission",
                        tool_name=block.name,
                        input_summary=summarize(block.input),
                        output_summary=summarize(output),
                        duration_ms=now_ms() - start,
                    )


                results.append(self._make_tool_result(block, output))

                if "Permission denied" in output:
                    should_stop = True
                    break

                continue
            
            if (
                self.background_manager
                and self.background_manager.should_run_background(block.name, block.input or {})
            ):
                handler = self.tool_registry.get_handler(block.name)

                if handler is None:
                    output = f"Unknown tool: {block.name}"
                else:
                    bg_id = self.background_manager.start(
                        tool_use_id=block.id,
                        tool_name=block.name,
                        tool_input=block.input or {},
                        handler=handler,
                        run_id=run_id,
                        trace_id=trace_id,
                    )

                    output = (
                        f"[Background task {bg_id} started] "
                        "Result will arrive as a task_notification."
                    )

                results.append(self._make_tool_result(block, output))
                continue

            print(f"[dispatch] {block.name} {block.input}")
            output = self.tool_registry.dispatch(
                block.name,
                block.input,
            )

            self._run_post_hooks(block, output)
            duration = now_ms() - start
            if str(output).startswith("Error:"):
                if self.event_log:
                    self.event_log.error(
                        run_id=run_id,
                        trace_id=trace_id,
                        type="tool_error",
                        actor="tool",
                        tool_name=block.name,
                        input_summary=summarize(block.input),
                        error=summarize(output),
                        duration_ms=duration,
                    )
            else:
                if self.event_log:
                    self.event_log.info(
                        run_id=run_id,
                        trace_id=trace_id,
                        type="tool_end",
                        actor="tool",
                        tool_name=block.name,
                        input_summary=summarize(block.input),
                        output_summary=summarize(output),
                        duration_ms=duration,
                    )
            results.append(self._make_tool_result(block, output))

        return ToolExecutionResult(
            results=results,
            should_stop=should_stop,
        )

    def _run_pre_hooks(self, block):
        if not self.hooks:
            return None

        return self.hooks.trigger("PreToolUse", block)

    def _run_post_hooks(self, block, output: str):
        if not self.hooks:
            return

        self.hooks.trigger("PostToolUse", block, output)

    def _make_tool_result(self, block, output: str) -> dict:
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": str(output),
        }
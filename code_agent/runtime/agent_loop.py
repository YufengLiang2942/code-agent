# code_agent/runtime/agent_loop.py
import traceback
from code_agent.runtime.recovery import RecoveryPolicy, RecoveryState
from code_agent.runtime.model import ModelProvider
from code_agent.runtime.prompt import PromptBuilder
from code_agent.runtime.context import ContextManager
from code_agent.runtime.tool_executor import ToolExecutor
from code_agent.runtime.event_log import EventLog, now_ms, summarize
from code_agent.runtime.background import BackgroundTaskManager

from code_agent.tools.registry import ToolRegistry

from uuid import uuid4
class AgentRuntime:
    def __init__(
        self,
        model_provider: ModelProvider,
        prompt_builder: PromptBuilder,
        tool_registry: ToolRegistry,
        hook_manager=None,
        recovery_policy: RecoveryPolicy | None = None,
        context_manager: ContextManager | None = None,
        tool_executor: ToolExecutor | None = None,
        event_log: EventLog | None = None,
        background_manager: BackgroundTaskManager | None = None,
        default_max_tokens: int = 8000,
    ):
        self.model_provider = model_provider
        self.prompt_builder = prompt_builder
        self.tool_registry = tool_registry
        self.hooks = hook_manager
        self.recovery_policy = recovery_policy or RecoveryPolicy()
        self.context_manager = context_manager
        self.tool_executor = tool_executor or ToolExecutor(
            tool_registry=tool_registry,
            hook_manager=hook_manager,
        )
        self.event_log = event_log
        self.background_manager = background_manager
        self.default_max_tokens = default_max_tokens

    
    
    def run(self, messages: list[dict]) -> list[dict]:
        run_id = f"run_{uuid4().hex[:8]}"
        trace_id = f"trace_{uuid4().hex[:8]}"

        if self.event_log:
            self.event_log.info(
                run_id=run_id,
                trace_id=trace_id,
                type="run_start",
                actor="user",
                input_summary=summarize(messages[-1].get("content") if messages else ""),
            )

        state = RecoveryState()
        max_tokens = self.default_max_tokens

        while True:
            if self.background_manager:
                notes = self.background_manager.collect_notifications()

                if notes:
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": note,
                            }
                            for note in notes
                        ],
                    })
            messages = self.context_manager.prepare(messages=messages)
            try:
                llm_start = now_ms()
                response = self.recovery_policy.with_retry(
                    lambda: self.model_provider.generate(
                        messages=messages,
                        system=self.prompt_builder.build(),
                        tools=self.tool_registry.schemas(),
                        max_tokens=max_tokens,
                    ),
                    state,
                )                
            except Exception as e:
                error_text = "".join(
                    traceback.format_exception(
                        type(e),
                        e,
                        e.__traceback__,
                    )
                )
                
                if self.event_log:
                    self.event_log.error(
                        run_id=run_id,
                        trace_id=trace_id,
                        type="llm_error",
                        actor="model",
                        error=f"{type(e).__name__}: {error_text}",
                    )
                messages.append({
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": f"[Error] {type(e).__name__}: {error_text}",
                        }
                    ],
                })
                return messages
            
            tool_blocks = [
                block for block in response.content
                if getattr(block, "type", None) == "tool_use"
            ]

            if self.event_log:
                self.event_log.info(
                    run_id=run_id,
                    trace_id=trace_id,
                    type="llm_response",
                    actor="model",
                    output_summary=f"tool_calls={len(tool_blocks)}, stop_reason={response.stop_reason}",
                    duration_ms=now_ms() - llm_start,
                )
            
            messages.append({
                "role": "assistant",
                "content": response.content,
            })

            if not tool_blocks:
                if self.hooks:
                    self.hooks.trigger("Stop", messages)

                if self.event_log:
                    self.event_log.info(
                        run_id=run_id,
                        trace_id=trace_id,
                        type="run_end",
                        actor="system",
                        output_summary="completed",
                    )
                return messages

            execution = self.tool_executor.execute(
                tool_blocks,
                run_id=run_id,
                trace_id=trace_id,)
            
            # 把执行结果放入历史消息中
            messages.append({
                "role": "user",
                "content": execution.results,
            })
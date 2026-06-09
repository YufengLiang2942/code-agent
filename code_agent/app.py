# code_agent/app.py

import os

from anthropic import Anthropic

from code_agent.config import Settings, load_settings

from code_agent.tools.registry import ToolRegistry
from code_agent.tools.builtin import BuiltinTools, register_builtin_tools

from code_agent.capabilities.tasks import TaskService, register_task_tools
from code_agent.capabilities.skills import SkillRegistry, register_skill_tools
from code_agent.capabilities.memory import MemoryStore, register_memory_tools
from code_agent.capabilities.worktrees import WorktreeService, register_worktree_tools
from code_agent.capabilities.mcp import MCPService, register_mcp_tools
from code_agent.capabilities.bus import MessageBus, register_bus_tools
from code_agent.capabilities.protocols import ProtocolService, register_protocol_tools
from code_agent.capabilities.subagent import SubagentService, register_subagent_tools
from code_agent.capabilities.teammate import TeammateService, register_teammate_tools

from code_agent.infra.git import GitService

from code_agent.middleware.hooks import (
    HookManager,
    log_user_prompt,
    log_tool_use,
    log_stop,
    large_output_warning,
)
from code_agent.middleware.permissions import PermissionPolicy

from code_agent.runtime.model import AnthropicProvider
from code_agent.runtime.recovery import RecoveryPolicy
from code_agent.runtime.context import ContextManager
from code_agent.runtime.prompt import PromptBuilder
from code_agent.runtime.tool_executor import ToolExecutor
from code_agent.runtime.agent_loop import AgentRuntime
from code_agent.runtime.event_log import EventLog
from code_agent.runtime.background import BackgroundTaskManager
class CodeAgentApp:
    def __init__(
        self,
        settings: Settings,
        runtime: AgentRuntime,
        hooks: HookManager,
    ):
        self.settings = settings
        self.runtime = runtime
        self.hooks = hooks


def build_app(settings: Settings | None = None) -> CodeAgentApp:
    settings = settings or load_settings()

    client = build_client(settings)
    model_provider = build_model_provider(settings, client)

    hooks = build_hooks(settings)

    event_log = EventLog(settings.event_log_dir)
    background_manager = BackgroundTaskManager(event_log=event_log)

    context_manager = ContextManager(
        transcript_dir=settings.transcript_dir,
        tool_results_dir=settings.tool_results_dir,
        context_limit=settings.context_limit,
        model_provider=model_provider,
    )
    
    registry = ToolRegistry()

    builtin_tools = BuiltinTools(settings.workdir)
    register_builtin_tools(registry, builtin_tools)

    task_service = TaskService(settings.tasks_dir)
    register_task_tools(registry, task_service)

    git_service = GitService(settings.workdir)
    worktree_service = WorktreeService(
        workdir=settings.workdir,
        worktrees_dir=settings.worktrees_dir,
        git_service=git_service,
        task_service=task_service,
    )
    register_worktree_tools(registry, worktree_service)

    skill_registry = SkillRegistry(settings.skills_dir)
    skill_registry.scan()
    register_skill_tools(registry, skill_registry)

    memory_store = MemoryStore(settings.memory_dir)
    register_memory_tools(registry, memory_store)

    mcp_service = MCPService(registry)
    register_mcp_tools(registry, mcp_service)

    message_bus = MessageBus(settings.bus_dir)
    register_bus_tools(registry, message_bus)

    protocol_service = ProtocolService(
        protocol_dir=settings.protocol_dir,
        bus=message_bus,
    )
    register_protocol_tools(registry, protocol_service)

    subagent_service = SubagentService(
        model_provider=model_provider,
        tool_registry=registry,
        event_log=event_log,
        max_rounds=5,
        allowed_tools={
            "glob",
            "read_file",
            "write_file",
            "edit_file",
            "bash",
            "send_message",
            "read_inbox",
            "list_mailboxes",
        }
    )

    register_subagent_tools(registry, subagent_service)

    teammate_service = TeammateService(
        task_service=task_service,
        subagent_service=subagent_service,
        event_log=event_log,
        heartbeat_interval=10,
        idle_sleep=3,
    )

    register_teammate_tools(registry, teammate_service)


    tool_executor = ToolExecutor(
        tool_registry=registry,
        hook_manager=hooks,
        event_log=event_log,
        background_manager=background_manager
    )

    # 所有工具都已经注册完，再创建 PromptBuilder。
    prompt_builder = PromptBuilder(
        workdir=settings.workdir,
        skills=skill_registry,
        memory_store=memory_store,
        tool_registry=registry,
        mcp_service=mcp_service,
    )

    runtime = AgentRuntime(
        model_provider=model_provider,
        prompt_builder=prompt_builder,
        tool_registry=registry,
        hook_manager=hooks,
        recovery_policy=RecoveryPolicy(),
        context_manager=context_manager,
        tool_executor=tool_executor,
        event_log=event_log,
        background_manager=background_manager,
        default_max_tokens=settings.default_max_tokens,
    )

    return CodeAgentApp(
        settings=settings,
        runtime=runtime,
        hooks=hooks,
    )


def build_client(settings: Settings) -> Anthropic:
    if settings.anthropic_base_url:
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

    return Anthropic(base_url=settings.anthropic_base_url)


def build_model_provider(settings: Settings, client: Anthropic) -> AnthropicProvider:
    return AnthropicProvider(
        client=client,
        model_id=settings.model_id,
    )


def build_hooks(settings: Settings) -> HookManager:
    hooks = HookManager()

    permission = PermissionPolicy(settings.workdir)

    hooks.register("UserPromptSubmit", log_user_prompt)
    hooks.register("PreToolUse", permission.check)
    hooks.register("PreToolUse", log_tool_use)
    hooks.register("PostToolUse", large_output_warning)
    hooks.register("Stop", log_stop)

    return hooks
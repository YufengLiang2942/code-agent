# code_agent/runtime/prompt.py
import json
from datetime import datetime
from pathlib import Path

from code_agent.capabilities.skills import SkillRegistry
from code_agent.capabilities.memory import MemoryStore
from code_agent.capabilities.mcp import MCPService

from code_agent.tools.registry import ToolRegistry

PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Act, don't explain.",
    "tool_policy": (
        "Use the most specific available tool for the job. "
        "Do not use bash for file operations when a dedicated file tool exists. "
        "For example, use write_file/edit_file/read_file/delete_file instead of shell commands. "
        "Only use destructive tools when the user explicitly asks for that action."
    ),
    "memory_policy": (
        "Only use remember when the user explicitly asks you to remember something. "
        "Use list_memories when the user asks what you remember. "
        "Use clear_memories only when the user explicitly asks you to forget or clear memories."
    ),
    "bus_policy": (
        "Use send_message/read_inbox/list_mailboxes only for explicit multi-agent coordination, "
        "subagent communication, or when the user asks to send/check agent messages."
    ),
    "protocol_policy": (
        "Use protocol tools only for explicit multi-agent coordination. "
        "Use request_plan when a worker/subagent needs approval before executing a plan. "
        "Use review_plan only when the user or lead agent asks to approve or reject a plan. "
        "Use shutdown protocol tools only when coordinating agent shutdown."
    ),

    "subagent_policy": (
        "Use spawn_subagent only when delegation is useful: independent review, "
        "subtask analysis, or when the user explicitly asks to split work. "
        "Do not spawn subagents for simple one-step tasks."
    ),
    "teammate_policy": (
        "Use start_teammate_worker only when the user asks an autonomous teammate/worker "
        "to process tasks from the task board. "
        "Use list_teammate_workers to inspect worker state. "
        "Use stop_teammate_worker to stop a running worker gracefully."
    ),
}

class PromptBuilder:
    def __init__(
        self,
        workdir: Path,
        skills: SkillRegistry,
        tool_registry: ToolRegistry,
        memory_store: MemoryStore | None = None,
        mcp_service: MCPService | None = None,
    ):
        self.workdir = workdir
        self.skills = skills
        self.tool_registry = tool_registry
        self.memory_store = memory_store
        self.mcp_service = mcp_service

        self._last_context_key: str | None = None
        self._last_prompt: str | None = None


    def build(self) -> str:
        context = self.build_context()
        return self.get_system_prompt(context)

    def build_context(self) -> dict:
        memories = ""

        if self.memory_store:
            memories = self.memory_store.read_memories()

        mcp_summary = ""

        if self.mcp_service:
            mcp_summary = self.mcp_service.prompt_summary()

        return {
            "workspace": str(self.workdir),
            "tools": self.tool_registry.names(),
            "tool_summary": self.tool_registry.prompt_summary(),
            "skills": self.skills.list_skills(),
            "memories": memories,
            "mcp_summary": mcp_summary,
        }
    
    def assemble_system_prompt(self, context: dict) -> str:
        sections = []

        sections.append(PROMPT_SECTIONS["identity"])
        sections.append(f"Working directory: {context['workspace']}")
        # sections.append(f"Current time: {context['current_time']}")

        sections.append("Available tools:\n" + context["tool_summary"])

        if context.get("mcp_summary"):
            sections.append(context["mcp_summary"])
        sections.append("Skills catalog:\n" + context["skills"])

        sections.append(PROMPT_SECTIONS["tool_policy"])
        sections.append(PROMPT_SECTIONS["bus_policy"])
        sections.append(PROMPT_SECTIONS["protocol_policy"])
        sections.append(PROMPT_SECTIONS["subagent_policy"])
        sections.append(PROMPT_SECTIONS["teammate_policy"])

        if context.get("memories"):
            sections.append("Relevant long-term memories:\n" + context["memories"])
            sections.append(PROMPT_SECTIONS["memory_policy"])

        return "\n\n".join(sections)

    def get_system_prompt(self, context: dict) -> str:
        """
        简单缓存：context 不变就复用 prompt。
        注意 current_time 每次都会变化，所以如果保留 current_time，
        基本每次都会重新组装。
        """
        key = json.dumps(context, sort_keys=True, ensure_ascii=False, default=str)

        if key == self._last_context_key and self._last_prompt:
            return self._last_prompt

        self._last_context_key = key
        self._last_prompt = self.assemble_system_prompt(context)

        return self._last_prompt
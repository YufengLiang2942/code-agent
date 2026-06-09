# code_agent/capabilities/memory.py

from pathlib import Path

from code_agent.tools.registry import Tool, ToolRegistry
import code_agent.constant as CONSTANT

class MemoryStore:
    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        # 通过 md 文件维护记忆
        # 新增一条就在md文件中插入一条记忆
        self.memory_index = memory_dir / "MEMORY.md"
        self.memory_dir.mkdir(exist_ok=True)

    def read_memories(self, limit: int = CONSTANT.MEMORY_LIMIT) -> str:
        """
        读取当前记忆。
        """
        if not self.memory_index.exists():
            return ""

        text = self.memory_index.read_text(encoding="utf-8")

        return text[:limit]

    def append_memory(self, content: str) -> str:
        """
        追加一条记忆。
        """
        content = content.strip()

        if not content:
            return "Error: memory content is empty"

        self.memory_dir.mkdir(exist_ok=True)

        with self.memory_index.open("a", encoding="utf-8") as f:
            if self.memory_index.exists() and self.memory_index.stat().st_size > 0:
                f.write("\n\n")
            f.write(f"- {content}\n")

        return "Memory saved."

    def clear_memories(self) -> str:
        """
        清空记忆。
        """
        self.memory_index.write_text("", encoding="utf-8")
        return "Memories cleared."


def register_memory_tools(registry: ToolRegistry, memory_store: MemoryStore):
    registry.register(Tool(
        name="remember",
        description=(
            "Save a useful long-term memory about the user, project, or coding preferences. "
            "Only use this when the user explicitly asks you to remember something."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"},
            },
            "required": ["content"],
        },
        handler=memory_store.append_memory,
    ))

    registry.register(Tool(
        name="list_memories",
        description="List saved memories.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=memory_store.read_memories,
    ))

    registry.register(Tool(
        name="clear_memories",
        description="Clear all saved memories. Only use this when the user explicitly asks to forget or clear memories.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=memory_store.clear_memories,
    ))
# code_agent/tools/registry.py

from dataclasses import dataclass
from typing import Callable


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self.tools[tool.name] = tool

    def get_handler(self, name: str):
        """获取工具执行方法签名"""
        tool = self.tools.get(name)

        if tool is None:
            return None

        return tool.handler
    
    def schemas(self) -> list[dict]:
        """
        给大模型看的工具定义。
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self.tools.values()
        ]

    def dispatch(self, name: str, args: dict) -> str:
        """
        根据工具名执行真正的 Python 函数。
        """
        tool = self.tools.get(name)

        if tool is None:
            return f"Unknown tool: {name}"

        try:
            return str(tool.handler(**(args or {})))
        except TypeError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"
        
    def names(self) -> list[str]:
        return list(self.tools.keys())

    def prompt_summary(self) -> str:
        """
        给 system prompt 看的工具摘要。
        不放完整 schema，只放 name + description，避免 prompt 太长。
        """
        if not self.tools:
            return "(no tools registered)"

        lines = []

        for tool in self.tools.values():
            lines.append(f"- {tool.name}: {tool.description}")

        return "\n".join(lines)
    
    

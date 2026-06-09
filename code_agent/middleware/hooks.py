# code_agent/middleware/hooks.py

from collections import defaultdict
from typing import Callable, Any


class HookManager:
    def __init__(self):
        # {"PostToolUse", []}
        self._hooks: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def register(self, event: str, callback: Callable[..., Any]) -> None:
        self._hooks[event].append(callback)

    def trigger(self, event: str, *args) -> Any | None:
        for callback in self._hooks.get(event, []):
            result = callback(*args)

            # 只要某个 hook 返回了非 None，说明它想拦截流程
            if result is not None:
                return result

        return None


def log_tool_use(block):
    print(f"[tool use]{block.name}")
    return None


def log_user_prompt(query: str):
    """用户输入时触发"""
    print(f"[user prompt] {query[:80]}")
    return None


def log_stop(messages: list[dict]):
    '''一轮对话停止时触发'''
    tool_result_count = 0

    for msg in messages:
        content = msg.get("content")

        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    tool_result_count += 1

    print(f"[stop] {tool_result_count} tool result(s)")
    return None


def large_output_warning(block, output: str):
    if len(str(output)) > 10000:
        print(f"[large output] {block.name}: {len(str(output))} chars")

    return None
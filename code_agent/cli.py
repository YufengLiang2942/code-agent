# code_agent/cli.py

from code_agent.app import build_app


PROMPT = "agent >> "


def extract_text(content) -> str:
    """
    从 assistant message content 中提取文本。

    兼容：
    1. Anthropic block 对象：block.type == "text"
    2. dict block：{"type": "text", "text": "..."}
    3. 普通字符串
    """
    if not isinstance(content, list):
        return str(content)

    texts = []

    for block in content:
        if getattr(block, "type", None) == "text":
            texts.append(block.text)

        elif isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text", ""))

    return "\n".join(texts).strip()


def print_last_assistant(messages: list[dict]) -> None:
    """
    打印最后一条 assistant 文本消息。
    """
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue

        text = extract_text(msg.get("content", ""))

        if text:
            print(text)

        return


def handle_builtin_command(command: str, history: list[dict]) -> bool:
    """
    处理 CLI 内置命令。

    返回：
    - True：命令已处理，继续下一轮输入
    - False：不是内置命令，交给 agent
    """
    command = command.strip()

    if command == "/history":
        print_history_summary(history)
        return True

    if command == "/clear":
        history.clear()
        print("History cleared.")
        return True

    return False


def print_history_summary(history: list[dict]) -> None:
    """
    简单查看当前对话历史。
    """
    if not history:
        print("(history empty)")
        return

    for index, msg in enumerate(history, start=1):
        role = msg.get("role")
        content = msg.get("content")

        if isinstance(content, str):
            preview = content[:120]
        else:
            preview = extract_text(content)[:120]

            if not preview:
                preview = str(content)[:120]

        print(f"{index}. {role}: {preview}")


def main() -> None:
    app = build_app()

    runtime = app.runtime
    hooks = app.hooks

    history: list[dict] = []

    print("code agent")
    print("Type q to quit.")
    print("Built-in commands: /history, /clear")
    print()

    while True:
        try:
            query = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if query.lower() in {"q", "exit"}:
            break

        if not query:
            continue

        if handle_builtin_command(query, history):
            print()
            continue

        if hooks:
            hooks.trigger("UserPromptSubmit", query)

        history.append({
            "role": "user",
            "content": query,
        })

        new_history = runtime.run(history)

        if new_history is None:
            print("[error] runtime.run() returned None")
            print()
            continue

        history = new_history

        print_last_assistant(history)
        print()
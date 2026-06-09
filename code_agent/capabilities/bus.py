# code_agent/capabilities/bus.py

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from code_agent.tools.registry import Tool, ToolRegistry


@dataclass
class BusMessage:
    id: str = field(default_factory=lambda: str(uuid4()))
    sender: str = "agent"
    recipient: str = "agent"
    content: str = ""
    created_at: float = field(default_factory=time.time)
    read: bool = False


class MessageBus:
    def __init__(self, bus_dir: Path):
        self.bus_dir = bus_dir
        self.bus_dir.mkdir(parents=True, exist_ok=True)

    def _mailbox_path(self, agent_name: str) -> Path:
        safe_name = self._safe_agent_name(agent_name)
        return self.bus_dir / f"{safe_name}.jsonl"

    def _safe_agent_name(self, name: str) -> str:
        allowed = []

        for char in name:
            if char.isalnum() or char in {"_", "-", "."}:
                allowed.append(char)
            else:
                allowed.append("_")

        safe = "".join(allowed).strip("._-")

        return safe or "agent"

    def send(self, sender: str, recipient: str, content: str) -> str:
        message = BusMessage(
            sender=sender,
            recipient=recipient,
            content=content,
        )

        path = self._mailbox_path(recipient)

        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(message), ensure_ascii=False) + "\n")

        return f"Message sent to {recipient}: {message.id}"

    def read_inbox(self, agent_name: str = "agent", mark_read: bool = True) -> str:
        messages = self._load_messages(agent_name)

        unread = [
            message
            for message in messages
            if not message.read
        ]

        if not unread:
            return f"No unread messages for {agent_name}."

        if mark_read:
            for message in messages:
                if not message.read:
                    message.read = True

            self._save_messages(agent_name, messages)

        lines = []

        for message in unread:
            lines.append(
                f"[{message.id}] from={message.sender} "
                f"at={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(message.created_at))}\n"
                f"{message.content}"
            )

        return "\n\n".join(lines)

    def list_mailboxes(self) -> str:
        files = sorted(self.bus_dir.glob("*.jsonl"))

        if not files:
            return "(no mailboxes)"

        lines = []

        for path in files:
            agent_name = path.stem
            messages = self._load_messages(agent_name)
            unread_count = len([message for message in messages if not message.read])
            total_count = len(messages)

            lines.append(
                f"- {agent_name}: {unread_count} unread / {total_count} total"
            )

        return "\n".join(lines)

    def _load_messages(self, agent_name: str) -> list[BusMessage]:
        path = self._mailbox_path(agent_name)

        if not path.exists():
            return []

        messages = []

        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue

            data = json.loads(line)

            data.setdefault("read", False)

            messages.append(BusMessage(**data))

        return messages

    def _save_messages(self, agent_name: str, messages: list[BusMessage]) -> None:
        path = self._mailbox_path(agent_name)

        with path.open("w", encoding="utf-8") as f:
            for message in messages:
                f.write(json.dumps(asdict(message), ensure_ascii=False) + "\n")

    def mark_messages_read_containing(self, agent_name: str, text: str) -> int:
        messages = self._load_messages(agent_name)

        changed = 0

        for message in messages:
            if not message.read and text in message.content:
                message.read = True
                changed += 1

        if changed:
            self._save_messages(agent_name, messages)

        return changed


def register_bus_tools(registry: ToolRegistry, bus: MessageBus) -> None:
    registry.register(Tool(
        name="send_message",
        description=(
            "Send a message to another agent mailbox. "
            "Use this for multi-agent coordination or leaving notes for a subagent."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "sender": {"type": "string"},
                "recipient": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["sender", "recipient", "content"],
        },
        handler=bus.send,
    ))

    registry.register(Tool(
        name="read_inbox",
        description=(
            "Read unread messages from an agent mailbox. "
            "Use this to check messages sent by other agents."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent_name": {"type": "string"},
                "mark_read": {"type": "boolean"},
            },
            "required": [],
        },
        handler=bus.read_inbox,
    ))

    registry.register(Tool(
        name="list_mailboxes",
        description="List all agent mailboxes and unread message counts.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=bus.list_mailboxes,
    ))
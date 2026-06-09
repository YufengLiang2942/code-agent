# code_agent/capabilities/protocols.py

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from code_agent.capabilities.bus import MessageBus
from code_agent.tools.registry import Tool, ToolRegistry


@dataclass
class ProtocolRequest:
    id: str = field(default_factory=lambda: str(uuid4()))
    protocol: str = ""
    sender: str = "agent"
    recipient: str = "agent"
    task_id: str = ""
    status: str = "pending"
    payload: dict = field(default_factory=dict)
    response: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class ProtocolService:
    def __init__(self, protocol_dir: Path, bus: MessageBus):
        self.protocol_dir = protocol_dir
        self.bus = bus
        self.protocol_dir.mkdir(parents=True, exist_ok=True)

    def _request_path(self, request_id: str) -> Path:
        return self.protocol_dir / f"{request_id}.json"

    def _save(self, request: ProtocolRequest) -> None:
        request.updated_at = time.time()

        self._request_path(request.id).write_text(
            json.dumps(asdict(request), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self, request_id: str) -> ProtocolRequest:
        path = self._request_path(request_id)

        if not path.exists():
            raise FileNotFoundError(f"Protocol request not found: {request_id}")

        data = json.loads(path.read_text(encoding="utf-8"))

        data.setdefault("response", None)
        data.setdefault("payload", {})

        return ProtocolRequest(**data)

    def request_plan(
        self,
        sender: str,
        recipient: str,
        task_id: str,
        plan: str,
    ) -> str:
        """
        worker/subagent 向 lead 请求计划审批。
        """
        request = ProtocolRequest(
            protocol="plan_approval",
            sender=sender,
            recipient=recipient,
            task_id=task_id,
            payload={
                "plan": plan,
            },
        )

        self._save(request)

        message = {
            "protocol": request.protocol,
            "request_id": request.id,
            "task_id": task_id,
            "status": request.status,
            "plan": plan,
        }

        self.bus.send(
            sender=sender,
            recipient=recipient,
            content=json.dumps(message, ensure_ascii=False, indent=2),
        )

        return f"Plan approval requested: {request.id}"

    def review_plan(
        self,
        request_id: str,
        approved: bool,
        reviewer: str = "lead",
        comment: str = "",
    ) -> str:
        """
        lead 审批计划。
        """
        try:
            request = self._load(request_id)
        except FileNotFoundError as e:
            return f"Error: {e}"

        if request.protocol != "plan_approval":
            return f"Error: request {request_id} is not a plan_approval request"

        if request.status != "pending":
            return f"Request {request_id} is already {request.status}"

        request.status = "approved" if approved else "rejected"
        request.response = comment
        self._save(request)

        # 审批后直接修改lead邮箱状态
        self.bus.mark_messages_read_containing(
            agent_name=request.recipient,
            text=request.id,
        )

        response_message = {
            "protocol": "plan_approval_response",
            "request_id": request.id,
            "task_id": request.task_id,
            "status": request.status,
            "reviewer": reviewer,
            "comment": comment,
        }

        self.bus.send(
            sender=reviewer,
            recipient=request.sender,
            content=json.dumps(response_message, ensure_ascii=False, indent=2),
        )

        return f"Plan request {request_id} {request.status}"

    def request_shutdown(
        self,
        sender: str,
        recipient: str,
        reason: str = "",
    ) -> str:
        """
        请求另一个 agent 停止。
        """
        request = ProtocolRequest(
            protocol="shutdown",
            sender=sender,
            recipient=recipient,
            payload={
                "reason": reason,
            },
        )

        self._save(request)

        message = {
            "protocol": "shutdown",
            "request_id": request.id,
            "reason": reason,
            "status": request.status,
        }

        self.bus.send(
            sender=sender,
            recipient=recipient,
            content=json.dumps(message, ensure_ascii=False, indent=2),
        )

        return f"Shutdown requested: {request.id}"

    def respond_shutdown(
        self,
        request_id: str,
        accepted: bool,
        responder: str = "agent",
        comment: str = "",
    ) -> str:
        try:
            request = self._load(request_id)
        except FileNotFoundError as e:
            return f"Error: {e}"

        if request.protocol != "shutdown":
            return f"Error: request {request_id} is not a shutdown request"

        if request.status != "pending":
            return f"Request {request_id} is already {request.status}"

        request.status = "accepted" if accepted else "rejected"
        request.response = comment
        self._save(request)

        response_message = {
            "protocol": "shutdown_response",
            "request_id": request.id,
            "status": request.status,
            "responder": responder,
            "comment": comment,
        }

        self.bus.send(
            sender=responder,
            recipient=request.sender,
            content=json.dumps(response_message, ensure_ascii=False, indent=2),
        )

        return f"Shutdown request {request_id} {request.status}"

    def list_protocol_requests(self, status: str = "") -> str:
        requests = []

        for path in sorted(self.protocol_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("response", None)
            data.setdefault("payload", {})
            request = ProtocolRequest(**data)

            if status and request.status != status:
                continue

            requests.append(request)

        if not requests:
            return "No protocol requests."

        lines = []

        for request in requests:
            lines.append(
                f"{request.id}: protocol={request.protocol}, "
                f"status={request.status}, "
                f"sender={request.sender}, "
                f"recipient={request.recipient}, "
                f"task_id={request.task_id}"
            )

        return "\n".join(lines)

    def get_protocol_request(self, request_id: str) -> str:
        try:
            request = self._load(request_id)
        except FileNotFoundError as e:
            return f"Error: {e}"

        return json.dumps(asdict(request), ensure_ascii=False, indent=2)
    
def register_protocol_tools(
    registry: ToolRegistry,
    service: ProtocolService,
) -> None:
    registry.register(Tool(
        name="request_plan",
        description=(
            "Request plan approval from another agent before executing a task. "
            "Use this when a subagent or worker needs lead approval."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "sender": {"type": "string"},
                "recipient": {"type": "string"},
                "task_id": {"type": "string"},
                "plan": {"type": "string"},
            },
            "required": ["sender", "recipient", "task_id", "plan"],
        },
        handler=service.request_plan,
    ))

    registry.register(Tool(
        name="review_plan",
        description=(
            "Approve or reject a pending plan approval request."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "approved": {"type": "boolean"},
                "reviewer": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["request_id", "approved"],
        },
        handler=service.review_plan,
    ))

    registry.register(Tool(
        name="request_shutdown",
        description=(
            "Request another agent to stop. Use only for multi-agent coordination."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "sender": {"type": "string"},
                "recipient": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["sender", "recipient"],
        },
        handler=service.request_shutdown,
    ))

    registry.register(Tool(
        name="respond_shutdown",
        description="Accept or reject a shutdown request.",
        input_schema={
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "accepted": {"type": "boolean"},
                "responder": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["request_id", "accepted"],
        },
        handler=service.respond_shutdown,
    ))

    registry.register(Tool(
        name="list_protocol_requests",
        description="List protocol requests, optionally filtered by status.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
            },
            "required": [],
        },
        handler=service.list_protocol_requests,
    ))

    registry.register(Tool(
        name="get_protocol_request",
        description="Get full details of a protocol request.",
        input_schema={
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
            },
            "required": ["request_id"],
        },
        handler=service.get_protocol_request,
    ))
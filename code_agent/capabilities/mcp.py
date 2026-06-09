# code_agent/capabilities/mcp.py

import re
from typing import Callable

from code_agent.tools.registry import Tool, ToolRegistry


_DISALLOWED_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_mcp_name(name: str) -> str:
	"""
	MCP server/tool 名字只能用于工具名，所以要清洗。
	"""
	return _DISALLOWED_CHARS.sub("_", name)


class MCPClient:
	"""
	教学版 MCPClient。

	真实 MCP 后面可以替换这里：
	- stdio MCP
	- HTTP MCP
	- SSE MCP
	- 官方 SDK MCP

	当前版本先用 mock server 验证架构。
	"""

	def __init__(self, name: str):
		self.name = name
		self.tools: list[dict] = []
		self.handlers: dict[str, Callable] = {}

	def register(self, tool_defs: list[dict], handlers: dict[str, Callable]) -> None:
		self.tools = tool_defs
		self.handlers = handlers

	def call_tool(self, tool_name: str, args: dict) -> str:
		handler = self.handlers.get(tool_name)

		if not handler:
			return f"MCP error: unknown tool '{tool_name}'"

		try:
			return str(handler(**(args or {})))
		except Exception as e:
			return f"MCP error: {type(e).__name__}: {e}"


def mock_server_docs() -> MCPClient:
	client = MCPClient("docs")

	client.register(
		tool_defs=[
			{
				"name": "search",
				"description": "Search documentation. Read-only.",
				"inputSchema": {
					"type": "object",
					"properties": {
						"query": {"type": "string"},
					},
					"required": ["query"],
				},
			},
			{
				"name": "get_version",
				"description": "Get documentation API version. Read-only.",
				"inputSchema": {
					"type": "object",
					"properties": {},
					"required": [],
				},
			},
		],
		handlers={
			"search": lambda query: f"[docs] Found 3 documentation results for: {query}",
			"get_version": lambda: "[docs] API version v2.1.0",
		},
	)

	return client


def mock_server_deploy() -> MCPClient:
	client = MCPClient("deploy")

	client.register(
		tool_defs=[
			{
				"name": "status",
				"description": "Check deployment status. Read-only.",
				"inputSchema": {
					"type": "object",
					"properties": {
						"service": {"type": "string"},
					},
					"required": ["service"],
				},
			},
			{
				"name": "trigger",
				"description": "Trigger deployment. Destructive operation.",
				"inputSchema": {
					"type": "object",
					"properties": {
						"service": {"type": "string"},
					},
					"required": ["service"],
				},
			},
		],
		handlers={
			"status": lambda service: f"[deploy] {service}: running",
			"trigger": lambda service: f"[deploy] triggered deployment for {service}",
		},
	)

	return client


MOCK_SERVERS = {
	"docs": mock_server_docs,
	"deploy": mock_server_deploy,
}


class MCPService:
	def __init__(self, registry: ToolRegistry):
		self.registry = registry
		self.clients: dict[str, MCPClient] = {}

	def connect(self, name: str) -> str:
		if name in self.clients:
			return f"MCP server '{name}' already connected"

		factory = MOCK_SERVERS.get(name)

		if not factory:
			available = ", ".join(MOCK_SERVERS.keys())
			return f"Unknown MCP server '{name}'. Available: {available}"

		client = factory()
		self.clients[name] = client

		self._register_client_tools(client)

		tool_names = [tool["name"] for tool in client.tools]

		return (
			f"Connected MCP server '{name}'. "
			f"Discovered tools: {', '.join(tool_names)}"
		)

	def list_servers(self) -> str:
		if not self.clients:
			return "(no MCP servers connected)"

		lines = []

		for name, client in self.clients.items():
			tools = ", ".join(tool["name"] for tool in client.tools)
			lines.append(f"- {name}: {tools}")

		return "\n".join(lines)

	def _register_client_tools(self, client: MCPClient) -> None:
		safe_server = normalize_mcp_name(client.name)

		for tool_def in client.tools:
			original_tool_name = tool_def["name"]
			safe_tool = normalize_mcp_name(original_tool_name)

			prefixed_name = f"mcp__{safe_server}__{safe_tool}"

			self.registry.register(Tool(
				name=prefixed_name,
				description=(
					f"[MCP:{client.name}] "
					+ tool_def.get("description", "")
				),
				input_schema=tool_def.get("inputSchema", {
					"type": "object",
					"properties": {},
					"required": [],
				}),
				handler=self._make_handler(client, original_tool_name),
			))

	def _make_handler(self, client: MCPClient, tool_name: str):
		def handler(**kwargs):
			return client.call_tool(tool_name, kwargs)

		return handler
	
	def prompt_summary(self) -> str:
		"""
		给 PromptBuilder 使用：
		说明有哪些 MCP server 可以连接，以及什么时候连接。
		"""
		lines = []

		for name in MOCK_SERVERS.keys():
			if name == "docs":
				lines.append(
					"- docs: documentation search and API/version lookup. "
					"Connect when the user asks about documentation, API usage, framework usage, or technical references."
				)
			elif name == "deploy":
				lines.append(
					"- deploy: deployment status and deployment trigger tools. "
					"Connect when the user asks about deployment, release, service status, or rollout."
				)
			else:
				lines.append(f"- {name}: available MCP server.")

		connected = ", ".join(self.clients.keys()) or "(none)"

		return (
			"Available MCP servers that can be connected with connect_mcp:\n"
			+ "\n".join(lines)
			+ f"\n\nConnected MCP servers: {connected}"
		)


def register_mcp_tools(registry: ToolRegistry, service: MCPService) -> None:
	registry.register(Tool(
		name="connect_mcp",
		description=(
			"Connect to an MCP server and dynamically register its tools. "
			"Available teaching mock servers: docs, deploy."
		),
		input_schema={
			"type": "object",
			"properties": {
				"name": {"type": "string"},
			},
			"required": ["name"],
		},
		handler=service.connect,
	))

	registry.register(Tool(
		name="list_mcp_servers",
		description="List connected MCP servers and their discovered tools.",
		input_schema={
			"type": "object",
			"properties": {},
			"required": [],
		},
		handler=service.list_servers,
	))
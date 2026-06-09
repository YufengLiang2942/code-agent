# code_agent/middleware/permissions.py

from pathlib import Path

from code_agent.infra.fs import safe_path


class PermissionPolicy:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        # 直接拒绝
        self.deny_list = [
            "rm -rf /",
            "sudo",
            "shutdown",
            "reboot",
            "mkfs",
            "dd if=",
        ]
        # 人工审核执行
        self.destructive_tokens = [
            "rm ",
            "del ",
            "rmdir ",
            "Remove-Item",
            "chmod 777",
            "> /etc/",
        ]

    def check(self, block) -> str | None:
        tool_name = block.name
        tool_input = block.input or {}

        if tool_name == "bash":
            return self._check_bash(tool_input)

        if tool_name in {"write_file", "edit_file"}:
            return self._check_file_write(tool_input)

        if tool_name == "delete_file":
            return self._check_delete_file(tool_input)
        
        if tool_name == "remove_worktree":
            return self._check_remove_worktree(tool_input)
        
        if tool_name.startswith("mcp__") and self._looks_destructive_mcp_tool(tool_name):
            return self._confirm_mcp_destructive(tool_name, tool_input)
        return None

    def _check_bash(self, tool_input: dict) -> str | None:
        command = tool_input.get("command", "")
        command_lower = command.lower()

        # 绝对禁止的高危命令
        hard_deny_patterns = [
            "rm -rf /",
            "sudo",
            "shutdown",
            "reboot",
            "mkfs",
            "dd if=",
        ]

        for pattern in hard_deny_patterns:
            if pattern.lower() in command_lower:
                return f"Permission denied: '{pattern}' is on the deny list"

        # 文件删除不要走 bash，统一走 delete_file
        delete_patterns = [
            "del ",
            "erase ",
            "rm ",
            "rmdir ",
            "rd ",
            "remove-item",
            "os.remove",
            "unlink",
        ]

        for pattern in delete_patterns:
            if pattern in command_lower:
                return (
                    "Permission denied: deletion through bash is not allowed. "
                    "Use delete_file instead. "
                    "Do not try alternative deletion commands in this turn."
                )

        return None

    def _check_file_write(self, tool_input: dict) -> str | None:
        """禁止写出项目目录"""
        path = tool_input.get("path", "")

        try:
            safe_path(self.workdir, path)
        except Exception:
            return f"Permission denied: path escapes workspace: {path}"

        return None
    
    def _check_delete_file(self, tool_input: dict) -> str | None:
        path = tool_input.get("path", "")

        try:
            target = safe_path(self.workdir, path)
        except Exception:
            return f"Permission denied: path escapes workspace: {path}"

        if not target.exists():
            return None

        if not target.is_file():
            return f"Permission denied: {path} is not a file"

        print()
        print("[permission] delete file:")
        print(target)

        choice = input("Allow delete? [y/N] ").strip().lower()

        if choice not in {"y", "yes"}:
            return "Permission denied by user. "
        
        return None
    
    def _check_remove_worktree(self, tool_input: dict) -> str | None:
        name = tool_input.get("name", "")

        print()
        print("[permission] remove worktree:")
        print(name)

        choice = input("Allow remove worktree? [y/N] ").strip().lower()

        if choice not in {"y", "yes"}:
            return (
                "Permission denied by user. "
                "The user rejected removing this worktree. "
                "Do not try alternative removal commands in this turn."
            )

        return None
    
    def _looks_destructive_mcp_tool(self, tool_name: str) -> bool:
        lower = tool_name.lower()

        destructive_words = [
            "deploy",
            "trigger",
            "delete",
            "remove",
            "write",
            "update",
            "create",
        ]

        return any(word in lower for word in destructive_words)


    def _confirm_mcp_destructive(self, tool_name: str, tool_input: dict) -> str | None:
        print()
        print("[permission] destructive-looking MCP tool:")
        print(tool_name)
        print(tool_input)

        choice = input("Allow MCP tool? [y/N] ").strip().lower()

        if choice not in {"y", "yes"}:
            return (
                "Permission denied by user. "
                "The user rejected this MCP tool call. "
                "Do not try alternative destructive MCP calls in this turn."
            )

        return None
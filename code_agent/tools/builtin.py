import subprocess
import glob as glob_lib
from pathlib import Path

from code_agent.infra.fs import safe_path
from code_agent.tools.registry import Tool, ToolRegistry

class BuiltinTools:
    """内置工具"""
    def __init__(self, workdir: Path):
        self.workdir = workdir

    def bash(self, command: str, run_in_background: bool = False) -> str:
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                text=False,
                encoding="utf-8",
                timeout=120,
            )
            output = (result.stdout + result.stderr).strip()
            return output[:50000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Timeout (120s)"

    def read_file(self, path: str, limit: int | None = None, offset: int = 0) -> str:
        try:
            fp = safe_path(self.workdir, path)
            lines = fp.read_text(encoding="utf-8").splitlines()

            offset = max(offset, 0)
            lines = lines[offset:]

            if limit is not None and limit < len(lines):
                lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]

            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    def write_file(self, path: str, content: str) -> str:
        try:
            fp = safe_path(self.workdir, path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error: {e}"

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        try:
            fp = safe_path(self.workdir, path)
            text = fp.read_text(encoding="utf-8")

            if old_text not in text:
                return f"Error: text not found in {path}"

            fp.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
            return f"Edited {path}"
        except Exception as e:
            return f"Error: {e}"
        
    def delete_file(self, path: str) -> str:
        try:
            fp = safe_path(self.workdir, path)

            if not fp.exists():
                return f"File not found: {path}"

            if not fp.is_file():
                return f"Error: {path} is not a file"

            fp.unlink()

            return f"Deleted file: {path}"

        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    # def glob(self, pattern: str) -> str:
    #     try:
    #         results = []

    #         for match in glob_lib.glob(pattern, root_dir=self.workdir):
    #             full_path = (self.workdir / match).resolve()
    #             if full_path.is_relative_to(self.workdir.resolve()):
    #                 results.append(match)

    #         return "\n".join(results) if results else "(no matches)"
    #     except Exception as e:
    #         return f"Error: {e}"
        
    def glob(self, pattern: str) -> str:
        try:
            base = self.workdir.resolve()

            # 如果用户传的是绝对路径，先转成相对路径
            raw_pattern = pattern.strip()

            pattern_path = Path(raw_pattern)

            if pattern_path.is_absolute():
                try:
                    raw_pattern = str(pattern_path.resolve().relative_to(base))
                except ValueError:
                    return f"Error: pattern escapes workspace: {pattern}"

            results = []

            # pathlib 的 glob / rglob 在 Windows 下更稳定
            if "**" in raw_pattern:
                matches = base.glob(raw_pattern)
            else:
                matches = base.glob(raw_pattern)

            for match in matches:
                resolved = match.resolve()

                if not resolved.is_relative_to(base):
                    continue

                results.append(str(resolved.relative_to(base)))

            return "\n".join(sorted(results)) if results else "(no matches)"

        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"
        

def register_builtin_tools(registry: ToolRegistry, builtin: BuiltinTools):
    registry.register(Tool(
        name="bash",
        description="Run a shell command.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "run_in_background": {"type": "boolean"},
            },
            "required": ["command"],
        },
        handler=builtin.bash,
    ))

    registry.register(Tool(
        name="read_file",
        description="Read file contents.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
            "required": ["path"],
        },
        handler=builtin.read_file,
    ))

    registry.register(Tool(
        name="write_file",
        description="Write content to a file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler=builtin.write_file,
    ))

    registry.register(Tool(
        name="edit_file",
        description="Replace exact text in a file once.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        handler=builtin.edit_file,
    ))

    registry.register(Tool(
        name="delete_file",
        description=(
            "Delete a single file inside the workspace. "
            "Use this instead of bash, cmd, powershell, del, rm, or Remove-Item for file deletion."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
        handler=builtin.delete_file,
    ))

    registry.register(Tool(
        name="glob",
        description="Find files matching a glob pattern.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
            },
            "required": ["pattern"],
        },
        handler=builtin.glob,
    ))
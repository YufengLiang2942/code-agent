# code_agent/infra/git.py

import subprocess
from pathlib import Path


class GitService:
    def __init__(self, workdir: Path):
        self.workdir = workdir

    def run_git(self, args: list[str], cwd: Path | None = None, timeout: int = 30) -> tuple[bool, str]:
        """
        执行 git 命令。

        返回：
        - ok: 是否执行成功
        - output: stdout + stderr
        """
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=cwd or self.workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                timeout=timeout,
            )

            raw_output = result.stdout + result.stderr
            output = self._decode_output(raw_output).strip()

            if not output:
                output = "(no output)"

            return result.returncode == 0, output[:5000]

        except subprocess.TimeoutExpired:
            return False, "Error: git timeout"

        except Exception as e:
            return False, f"Error: {type(e).__name__}: {e}"

    def is_git_repo(self) -> bool:
        ok, _ = self.run_git(["rev-parse", "--is-inside-work-tree"])
        return ok

    def status_porcelain(self, cwd: Path | None = None) -> str:
        ok, output = self.run_git(["status", "--porcelain"], cwd=cwd)

        if not ok:
            return ""

        if output == "(no output)":
            return ""

        return output

    def count_changes(self, cwd: Path) -> int:
        status = self.status_porcelain(cwd=cwd)

        if not status:
            return 0

        return len([line for line in status.splitlines() if line.strip()])

    def _decode_output(self, raw: bytes) -> str:
        for encoding in ("utf-8", "gbk", "cp936"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue

        return raw.decode("utf-8", errors="replace")
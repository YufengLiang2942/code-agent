# code_agent/infra/fs.py
"""文件路径越权安全检查"""
from pathlib import Path


def safe_path(base: Path, path: str) -> Path:
    """
    确保 path 没有逃出 base 目录。
    """
    target = (base / path).resolve()

    if not target.is_relative_to(base.resolve()):
        raise ValueError(f"Path escapes workspace: {path}")

    return target
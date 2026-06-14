# -*- coding: utf-8 -*-
"""
make_clean.py
=============
供 Makefile 的 clean target 调用，跨平台清理项目临时文件与编译产物。
"""

from __future__ import annotations

import pathlib
import shutil


# 需要清理的目录名（递归匹配）
DIR_PATTERNS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
    "build",
    "dist",
    ".eggs",
}

# 需要清理的文件 glob 模式
FILE_PATTERNS = ("*.pyc", "*.pyo", "*.pyd", "*.log", "*.egg-info")


def main(root: str = ".") -> int:
    base = pathlib.Path(root)
    if not base.exists():
        print(f"[clean] 目录不存在: {root}")
        return 0

    removed_dirs = 0
    for p in list(base.rglob("*")):
        if p.is_dir() and p.name in DIR_PATTERNS:
            shutil.rmtree(p, ignore_errors=True)
            removed_dirs += 1

    removed_files = 0
    for p in list(base.rglob("*")):
        if p.is_file() and any(p.match(pat) for pat in FILE_PATTERNS):
            try:
                p.unlink()
                removed_files += 1
            except OSError as exc:
                print(f"[clean] 跳过 {p}: {exc}")

    print(f"[clean] 完成。已清理 {removed_dirs} 个目录, {removed_files} 个文件。")
    return 0


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "."
    raise SystemExit(main(target))

# -*- coding: utf-8 -*-
"""
make_help.py
============
供 Makefile 的 help target 调用的辅助脚本。
解析 Makefile 中形如 ``target: ... ## 描述`` 的注释行，
并以带 ANSI 颜色（终端支持时）的表格形式输出。
"""

from __future__ import annotations

import pathlib
import re
import sys


def main(makefile_path: str) -> int:
    path = pathlib.Path(makefile_path)
    if not path.exists():
        print(f"[错误] 找不到 Makefile: {makefile_path}", file=sys.stderr)
        return 1

    text = path.read_text(encoding="utf-8")
    # 匹配: target 名称 + "##" 之后的描述
    pattern = re.compile(r"^([a-zA-Z_.-]+):.*?##\s*(.*)$", re.MULTILINE)
    rows = pattern.findall(text)
    if not rows:
        print("[警告] Makefile 中没有发现 '## 描述' 形式的注释。")
        return 0

    # 简单的 ANSI 着色，失败则降级为普通输出
    try:
        cyan = "\033[36m"
        reset = "\033[0m"
        width = max(len(cmd) for cmd, _ in rows)
        for cmd, desc in rows:
            print(f"  {cyan}{cmd:<{width}}{reset}  {desc}")
    except Exception:  # noqa: BLE001
        for cmd, desc in rows:
            print(f"  {cmd:<{width}}  {desc}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python make_help.py <Makefile 路径>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))

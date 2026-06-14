# -*- coding: utf-8 -*-
"""
make_pexels_env.py
==================
供 Makefile 的 ``pexels-env-check`` target 调用。

职责：
    1. 优先从进程环境变量读取 ``PEXELS_API_KEY``（shell 注入或 ``make VAR=...``）；
    2. 若未设置，则扫描项目根目录的 ``.env``，接受以下两种键名：
       * ``PEXELS_API_KEY=...``（标准大写）
       * ``pexels_api_key=...``（小写，便于不想全大写的人）；
    3. 校验取值是否为空 / 是否为占位符（如 ``YOUR_API_KEY``），是则退出码 1；
    4. 通过校验则打印前 8 位掩码和来源（不输出完整 key，避免日志泄露）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 项目根：scripts/ 的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# 已知占位符；不区分大小写比较
PLACEHOLDER_KEYS = {
    "",
    "your_api_key",
    "replace_me",
    "changeme",
    "<your_key>",
    "your-key",
    "todo",
}

# .env 中允许的 key 名（大小写不敏感）
ACCEPTED_KEYS = {"pexels_api_key", "pexels-api-key"}


def _load_from_env_file(path: Path) -> str:
    """从 ``.env`` 中解析第一个匹配的 key，返回取值；未找到返回空串。

    支持 ``#`` 开头的注释行、空行；值可被单/双引号包裹（自动剥离）。
    """
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().lower() not in ACCEPTED_KEYS:
            continue
        value = value.strip()
        # 去掉首尾成对引号
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return ""


def _mask(key: str) -> str:
    """仅显示前 8 位，其余用 ``*`` 掩盖。"""
    if len(key) <= 8:
        return key
    return key[:8] + "*" * (min(len(key) - 8, 8))


def main() -> int:
    # 1) 优先 shell 环境变量
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    source = "shell 环境变量 PEXELS_API_KEY"

    # 2) 回退到 .env
    if not key:
        key = _load_from_env_file(ENV_FILE)
        if key:
            source = f"项目根 .env ({ENV_FILE})"

    if not key:
        print("[错误] Pexels API Key 未配置。", file=sys.stderr)
        print("  请在以下任一位置设置 PEXELS_API_KEY：", file=sys.stderr)
        print(f"    1) 项目根 .env 文件中新增一行 PEXELS_API_KEY=<your_key>", file=sys.stderr)
        print("    2) shell 中 export PEXELS_API_KEY=<your_key>", file=sys.stderr)
        print("    3) make 命令行 make run-pexels PEXELS_API_KEY=<your_key>", file=sys.stderr)
        return 1

    if key.lower() in PLACEHOLDER_KEYS:
        print(
            f"[错误] PEXELS_API_KEY 仍为占位符 '{key}'，请替换为真实 key。",
            file=sys.stderr,
        )
        print(f"  编辑文件: {ENV_FILE}", file=sys.stderr)
        return 1

    print(f"[env-check] PEXELS_API_KEY 已就绪: {_mask(key)}")
    print(f"[env-check] 加载来源: {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

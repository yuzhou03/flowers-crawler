# -*- coding: utf-8 -*-
"""
命令行入口模块
负责解析用户输入的命令行参数，并调用爬虫核心逻辑。

支持参数:
    keyword   - 搜索关键词（必填）
    --count   - 下载数量，默认 10
    --output  - 存储路径，未指定时自动使用 out/<keyword的拼音>
    --timeout - 单次请求超时时间（秒），默认 15
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Optional

# 允许 ``python src/main.py`` 直接运行
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from crawler import BaiduImageCrawler, sanitize_keyword  # noqa: E402

# 未指定 --output 时使用的根目录
DEFAULT_OUTPUT_ROOT = "out"

# 拼音转换失败或库不可用时的兜底目录名
PINYIN_FALLBACK_NAME = "flowers"


# ----------------------------------------------------------------------
# 命令行参数解析
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="flowers-crawler",
        description="百度图片爬虫：按关键词抓取花的图片。",
    )
    parser.add_argument(
        "keyword",
        nargs="?",
        default=None,
        help="搜索的关键词，例如 '玫瑰'、'sunflower'。若省略则会进入交互输入。",
    )
    parser.add_argument(
        "-n", "--count",
        type=int,
        default=10,
        help="期望下载的图片数量，默认为 10。",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help=(
            "图片存储目录。省略时自动使用 'out/<keyword 的拼音>'，"
            "例如 keyword=荷花 时将保存到 'out/hehua'。"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="单次 HTTP 请求的超时时间（秒），默认 15。",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="单张图片下载失败时的重试次数，默认 3。",
    )
    return parser


def parse_args(argv=None, interactive: bool = True) -> argparse.Namespace:
    """解析命令行参数，并校验其合法性。

    Args:
        argv: 模拟的命令行参数列表（不含程序名）。``None`` 表示使用 ``sys.argv``。
        interactive: 当未提供 keyword 时是否进入交互式输入。``False`` 用于测试场景。

    Returns:
        校验通过后的 :class:`argparse.Namespace`。

    Raises:
        SystemExit: 当参数非法时由 ``parser.error`` 抛出。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.keyword and interactive:
        # 交互式输入兜底，方便直接双击或 IDE 中执行
        try:
            args.keyword = input("请输入要搜索的花的关键词: ").strip()
        except EOFError:
            parser.error("必须提供搜索关键词。")
    if not args.keyword:
        parser.error("搜索关键词不能为空。")
    if args.count <= 0:
        parser.error("--count 必须大于 0。")
    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0。")
    if args.max_retries < 0:
        parser.error("--max-retries 不能为负数。")
    return args


# ----------------------------------------------------------------------
# 拼音转换 & 路径解析
# ----------------------------------------------------------------------
def _keyword_to_pinyin(keyword: str) -> str:
    """将关键词转换为拼音字符串。

    处理流程:
        1. 使用 ``pypinyin.lazy_pinyin`` 进行无音调转换；
        2. 若 ``pypinyin`` 未安装或转换抛异常，则降级为原始关键词；
        3. 统一转小写、剔除非 ``[a-z0-9_-]`` 字符；
        4. 压缩连续下划线、去除首尾下划线；空结果回退为 ``flowers``。

    Args:
        keyword: 原始搜索关键词。

    Returns:
        可直接用于目录名的小写拼音字符串。
    """
    text = (keyword or "").strip()
    if not text:
        return PINYIN_FALLBACK_NAME

    pinyin_raw = text  # 兜底：转换失败时使用原文本
    try:
        from pypinyin import Style, lazy_pinyin  # type: ignore
        pinyin_raw = "".join(lazy_pinyin(text, style=Style.NORMAL))
    except ImportError:
        # 库缺失：保留原文本，由后续 _normalize_pinyin 统一处理
        pinyin_raw = text
    except Exception:  # noqa: BLE001 - 拼音库的任何异常都兜底
        pinyin_raw = text

    return _normalize_pinyin(pinyin_raw)


def _normalize_pinyin(text: str) -> str:
    """将字符串规范化为合法的目录名（小写 ASCII）。"""
    text = (text or "").lower()
    # 仅保留字母数字与连接符，其余全部替换为下划线
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    # 压缩连续下划线，并去掉首尾的下划线
    text = re.sub(r"_+", "_", text).strip("_-")
    return text or PINYIN_FALLBACK_NAME


def _resolve_output_dir(output: Optional[str], keyword: str) -> str:
    """根据 ``--output`` 与 ``keyword`` 决定最终保存目录。

    规则:
        - 若 ``output`` 为非空字符串，则直接使用该路径（去除首尾空白）；
        - 否则使用 ``out/<keyword 的拼音>``。

    Args:
        output: ``--output`` 参数的值（可能为 ``None`` 或空字符串）。
        keyword: 已校验的搜索关键词。

    Returns:
        最终的图片保存目录路径。
    """
    if output and output.strip():
        return output.strip()
    pinyin_dir = _keyword_to_pinyin(keyword)
    return os.path.join(DEFAULT_OUTPUT_ROOT, pinyin_dir)


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def main(argv=None) -> int:
    """主入口，返回进程退出码。"""
    args = parse_args(argv)
    # 自动清理关键词中可能影响文件命名的字符
    safe_keyword = sanitize_keyword(args.keyword)

    # 解析输出目录：未指定时使用 out/<keyword 拼音>
    try:
        output_dir = _resolve_output_dir(args.output, safe_keyword)
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        print(f"[错误] 创建输出目录失败 '{output_dir}': {exc}")
        return 4

    crawler = BaiduImageCrawler(
        keyword=safe_keyword,
        output_dir=output_dir,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )

    try:
        downloaded = crawler.crawl(count=args.count)
    except KeyboardInterrupt:
        print("\n[中断] 用户手动终止下载。")
        return 130
    except ValueError as exc:
        print(f"[错误] {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - 顶层兜底
        print(f"[异常] 程序运行失败: {exc}")
        return 1

    if not downloaded:
        print("[提示] 没有下载到任何图片，请检查网络或更换关键词。")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

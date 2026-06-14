# -*- coding: utf-8 -*-
"""
命令行入口模块
负责解析用户输入的命令行参数，并调用爬虫核心逻辑。

支持参数:
    keyword   - 搜索关键词（必填）
    --count   - 下载数量，默认 10
    --output  - 存储路径，默认 out/
    --timeout - 单次请求超时时间（秒），默认 15
"""

from __future__ import annotations

import argparse
import os
import sys

# 允许 ``python src/main.py`` 直接运行
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from crawler import BaiduImageCrawler, sanitize_keyword  # noqa: E402


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
        default="out",
        help="图片存储目录，默认为 out/。",
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


def main(argv=None) -> int:
    """主入口，返回进程退出码。"""
    args = parse_args(argv)
    # 自动清理关键词中可能影响文件命名的字符
    safe_keyword = sanitize_keyword(args.keyword)

    crawler = BaiduImageCrawler(
        keyword=safe_keyword,
        output_dir=args.output,
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

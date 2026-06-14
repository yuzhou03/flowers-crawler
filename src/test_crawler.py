# -*- coding: utf-8 -*-
"""
单元测试与集成测试
=================

覆盖范围:
    1. 纯函数: URL 解码、扩展名猜测、关键词清洗
    2. 解析逻辑: _parse_image_urls 在正常 / 异常数据下的行为
    3. 参数解析: CLI 入口对非法输入的容错
    4. 下载流程: 使用 mock 模拟 HTTP 响应，验证 crawl 行为
    5. 集成测试（默认跳过）: 真实 HTTP 拉取接口与下载图片
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

# 允许 ``python src/test_crawler.py`` 直接执行
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import main as cli_main  # noqa: E402
from crawler import (  # noqa: E402
    BaiduImageCrawler,
    DEFAULT_MIN_FILE_SIZE,
    ImageQualityStats,
    _decode_baidu_url,
    _guess_extension,
    _read_image_dimensions,
    batch_crawl,
    compare_quality,
    sanitize_keyword,
)


# ----------------------------------------------------------------------
# 测试辅助
# ----------------------------------------------------------------------
def _make_image_response(
    content: bytes,
    content_type: str = "image/jpeg",
    content_length: int = None,
) -> mock.Mock:
    """构造一个模拟的图片下载响应。

    Args:
        content: 响应内容。
        content_type: Content-Type 头部。
        content_length: 可显式指定 Content-Length；``None`` 时根据内容长度推断。
    """
    resp = mock.Mock()
    headers = {"Content-Type": content_type}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    elif content is not None:
        headers["Content-Length"] = str(len(content))
    resp.headers = headers
    resp.raise_for_status = mock.Mock()
    resp.iter_content = mock.Mock(return_value=iter([content]))
    return resp


def _make_search_response(items: list) -> mock.Mock:
    """构造一个模拟的百度图片搜索 JSON 响应。"""
    resp = mock.Mock()
    resp.headers = {"Content-Type": "application/json"}
    resp.raise_for_status = mock.Mock()
    resp.json.return_value = {"data": items}
    return resp


def _make_png(width: int, height: int, payload_size: int = None) -> bytes:
    """构造一个合法 PNG 头部的字节流用于测试分辨率解析。

    头部包含：PNG signature(8) + IHDR length(4) + 'IHDR'(4) + 宽(4) + 高(4)
    """
    import struct
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    # IHDR 后通常还有 5 字节的其它字段 + CRC，这里补全以模拟完整头部
    ihdr += b"\x08\x02\x00\x00\x00"  # bit depth, color type, compression, filter, interlace
    # CRC 占位 4 字节
    ihdr += b"\x00\x00\x00\x00"
    body = header + ihdr
    if payload_size is not None and payload_size > len(body):
        body += b"\x00" * (payload_size - len(body))
    return body


# ----------------------------------------------------------------------
# 单元测试
# ----------------------------------------------------------------------
class PureFunctionTests(unittest.TestCase):
    """针对纯函数（无 IO）的单元测试。"""

    def test_decode_baidu_url_returns_http_url(self) -> None:
        url = "https%3A%2F%2Fimg.example.com%2Frose.jpg"
        self.assertEqual(
            _decode_baidu_url(url),
            "https://img.example.com/rose.jpg",
        )

    def test_decode_baidu_url_returns_none_for_empty(self) -> None:
        self.assertIsNone(_decode_baidu_url(""))
        self.assertIsNone(_decode_baidu_url(None))  # type: ignore[arg-type]

    def test_decode_baidu_url_passthrough_for_plain_url(self) -> None:
        self.assertEqual(
            _decode_baidu_url("http://example.com/a.png"),
            "http://example.com/a.png",
        )

    def test_guess_extension_known(self) -> None:
        self.assertEqual(_guess_extension("http://x.com/a.JPG?token=1"), ".jpg")
        self.assertEqual(_guess_extension("http://x.com/a.png"), ".png")
        self.assertEqual(_guess_extension("http://x.com/a.webp"), ".webp")

    def test_guess_extension_default(self) -> None:
        self.assertEqual(_guess_extension("http://x.com/noext"), ".jpg")

    def test_sanitize_keyword(self) -> None:
        self.assertEqual(sanitize_keyword("玫瑰/牡丹:花?"), "玫瑰_牡丹_花_")
        self.assertEqual(sanitize_keyword("   "), "flowers")
        self.assertEqual(sanitize_keyword("rose*"), "rose_")


class ParseImageUrlsTests(unittest.TestCase):
    """针对 _parse_image_urls 解析逻辑的测试。

    优化后策略：每条 item 仅输出一个最佳 URL（优先 objURL，
    缺失时降级为 middleURL），完全跳过 thumbURL/hoverURL。
    """

    def test_parse_returns_one_url_per_item_with_strict_priority(self) -> None:
        payload = {
            "data": [
                {
                    "objURL": "https%3A%2F%2Fa.com%2F1.jpg",
                    "middleURL": "https://b.com/1.jpg",
                    "thumbURL": "https://c.com/1.jpg",
                    "hoverURL": "https://d.com/1.jpg",
                },
                {
                    "objURL": "",
                    "middleURL": "https://b.com/2.jpg",
                    "thumbURL": "https://c.com/2.jpg",
                },
                {"objURL": "not-a-url"},
                "non-dict",
                # 没有任何高质量 URL 字段的项应被丢弃
                {"thumbURL": "https://c.com/3.jpg", "hoverURL": "https://d.com/3.jpg"},
            ]
        }
        urls = BaiduImageCrawler._parse_image_urls(payload)
        # 第 1 条：objURL 有效 -> "https://a.com/1.jpg"
        # 第 2 条：objURL 为空，降级到 middleURL -> "https://b.com/2.jpg"
        # 第 3 条：objURL 解码后不是 http 开头 -> 跳过
        # 第 4 条：非 dict -> 跳过
        # 第 5 条：没有 objURL/middleURL -> 跳过
        self.assertEqual(
            urls,
            [
                "https://a.com/1.jpg",
                "https://b.com/2.jpg",
            ],
        )

    def test_parse_handles_empty_payload(self) -> None:
        self.assertEqual(BaiduImageCrawler._parse_image_urls({}), [])
        self.assertEqual(BaiduImageCrawler._parse_image_urls({"data": []}), [])

    def test_parse_dedupes_urls_across_items(self) -> None:
        payload = {
            "data": [
                {"objURL": "https://x.com/1.jpg"},
                {"objURL": "https://x.com/1.jpg"},  # 重复
                {"middleURL": "https://x.com/1.jpg"},  # 重复
            ]
        }
        urls = BaiduImageCrawler._parse_image_urls(payload)
        self.assertEqual(urls, ["https://x.com/1.jpg"])


class CrawlerCtorTests(unittest.TestCase):
    """针对 BaiduImageCrawler 构造参数的测试。"""

    def test_empty_keyword_raises(self) -> None:
        with self.assertRaises(ValueError):
            BaiduImageCrawler(keyword="   ")

    def test_default_output_dir_is_out(self) -> None:
        c = BaiduImageCrawler(keyword="rose")
        self.assertEqual(c.output_dir, "out")
        self.assertEqual(c.keyword, "rose")


# ----------------------------------------------------------------------
# 命令行参数解析测试
# ----------------------------------------------------------------------
class CliArgParseTests(unittest.TestCase):
    """针对 CLI 参数解析的测试。"""

    def setUp(self) -> None:
        self.parser = cli_main.build_parser()

    def test_default_values(self) -> None:
        ns = self.parser.parse_args(["rose"])
        self.assertEqual(ns.keyword, "rose")
        self.assertEqual(ns.count, 10)
        self.assertIsNone(ns.output)
        self.assertEqual(ns.timeout, 15)
        self.assertEqual(ns.max_retries, 3)

    def test_custom_values(self) -> None:
        ns = self.parser.parse_args(
            ["sunflower", "-n", "5", "-o", "d:/x", "--timeout", "30"]
        )
        self.assertEqual(ns.keyword, "sunflower")
        self.assertEqual(ns.count, 5)
        self.assertEqual(ns.output, "d:/x")
        self.assertEqual(ns.timeout, 30)

    def test_parse_args_rejects_invalid_count(self) -> None:
        """parse_args 应对 --count<=0 抛出 SystemExit。"""
        with self.assertRaises(SystemExit):
            cli_main.parse_args(["rose", "-n", "0"], interactive=False)

    def test_parse_args_rejects_empty_keyword(self) -> None:
        # 关闭交互式输入，避免测试阻塞在 input()
        with self.assertRaises(SystemExit):
            cli_main.parse_args([""], interactive=False)

    def test_parse_args_rejects_negative_timeout(self) -> None:
        with self.assertRaises(SystemExit):
            cli_main.parse_args(["rose", "--timeout", "-1"], interactive=False)


# ----------------------------------------------------------------------
# 拼音转换与输出目录解析测试
# ----------------------------------------------------------------------
class PinyinAndPathTests(unittest.TestCase):
    """针对 _keyword_to_pinyin / _normalize_pinyin / _resolve_output_dir 的测试。"""

    def test_keyword_to_pinyin_chinese(self) -> None:
        self.assertEqual(cli_main._keyword_to_pinyin("荷花"), "hehua")
        self.assertEqual(cli_main._keyword_to_pinyin("玫瑰"), "meigui")
        self.assertEqual(cli_main._keyword_to_pinyin("向日葵"), "xiangrikui")

    def test_keyword_to_pinyin_keeps_ascii_lowercased(self) -> None:
        self.assertEqual(cli_main._keyword_to_pinyin("Sunflower"), "sunflower")
        self.assertEqual(cli_main._keyword_to_pinyin("ROSE"), "rose")

    def test_keyword_to_pinyin_mixed(self) -> None:
        # 中文部分转拼音、英文部分保留并小写、空格转为下划线
        result = cli_main._keyword_to_pinyin("红玫瑰 Rose")
        self.assertIn("hongmeigui", result)
        self.assertIn("rose", result)
        self.assertNotIn(" ", result)
        self.assertEqual(result, result.lower())

    def test_keyword_to_pinyin_empty_fallback(self) -> None:
        self.assertEqual(cli_main._keyword_to_pinyin(""), "flowers")
        self.assertEqual(cli_main._keyword_to_pinyin("   "), "flowers")
        self.assertEqual(cli_main._keyword_to_pinyin(None), "flowers")  # type: ignore[arg-type]

    def test_keyword_to_pinyin_handles_pypinyin_failure(self) -> None:
        """当 pypinyin 抛异常时应降级为原文本归一化结果。"""
        with mock.patch("pypinyin.lazy_pinyin", side_effect=RuntimeError("boom")):
            result = cli_main._keyword_to_pinyin("荷花")
        # 降级路径：原文本 "荷花" 经过 _normalize 后只剩空，回退为 "flowers"
        self.assertEqual(result, "flowers")
        self.assertTrue(result)
        self.assertEqual(result, result.lower())

    def test_keyword_to_pinyin_handles_missing_module(self) -> None:
        """当 pypinyin 未安装时应走 ImportError 兜底分支，不抛异常。"""
        with mock.patch.dict("sys.modules", {"pypinyin": None}):
            # 重新触发内部 import 逻辑
            result = cli_main._keyword_to_pinyin("sunflower")
        # 兜底路径：原文本仅做 normalize，结果为 "sunflower"
        self.assertEqual(result, "sunflower")

    def test_normalize_pinyin(self) -> None:
        self.assertEqual(cli_main._normalize_pinyin("He Hua"), "he_hua")
        self.assertEqual(cli_main._normalize_pinyin("he--hua"), "he--hua")
        # 连续下划线会被压缩、但单下划线作为分隔符保留
        self.assertEqual(cli_main._normalize_pinyin("__he__hua__"), "he_hua")
        self.assertEqual(cli_main._normalize_pinyin("中文"), "flowers")
        self.assertEqual(cli_main._normalize_pinyin(""), "flowers")
        self.assertEqual(cli_main._normalize_pinyin("abc123-OK"), "abc123-ok")

    def test_resolve_output_dir_default_uses_pinyin(self) -> None:
        # 未传 output 时使用 out/<拼音>
        self.assertEqual(
            cli_main._resolve_output_dir(None, "荷花"),
            os.path.join("out", "hehua"),
        )
        self.assertEqual(
            cli_main._resolve_output_dir("", "荷花"),
            os.path.join("out", "hehua"),
        )
        self.assertEqual(
            cli_main._resolve_output_dir("   ", "荷花"),
            os.path.join("out", "hehua"),
        )

    def test_resolve_output_dir_user_specified(self) -> None:
        # 显式传值时直接使用用户路径
        self.assertEqual(
            cli_main._resolve_output_dir("mydir", "荷花"), "mydir"
        )
        self.assertEqual(
            cli_main._resolve_output_dir("  custom  ", "荷花"), "custom"
        )
        self.assertEqual(
            cli_main._resolve_output_dir("D:/data/peony", "牡丹"),
            "D:/data/peony",
        )

    def test_main_creates_pinyin_subdir_under_out(self) -> None:
        """main() 在未指定 --output 时应自动创建 out/<拼音> 目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 切换到临时目录作为 cwd
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                # 通过 mock 让爬虫不下任何图片
                with mock.patch(
                    "crawler.requests.Session.get",
                    return_value=_make_search_response([]),
                ):
                    rc = cli_main.main(["荷花", "-n", "1"])
                # 退出码 3 表示 "没有下载到任何图片"——这是预期的
                self.assertEqual(rc, 3)
                # 验证目录被自动创建
                expected = os.path.join(tmp, "out", "hehua")
                self.assertTrue(os.path.isdir(expected))
            finally:
                os.chdir(old_cwd)

    def test_main_uses_explicit_output(self) -> None:
        """显式 --output 时不应创建 out/<拼音>。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "myflowers")
            with mock.patch(
                "crawler.requests.Session.get",
                return_value=_make_search_response([]),
            ):
                rc = cli_main.main(["荷花", "-n", "1", "-o", target])
            self.assertEqual(rc, 3)
            self.assertTrue(os.path.isdir(target))


# ----------------------------------------------------------------------
# 下载流程测试（使用 mock 模拟 HTTP）
# ----------------------------------------------------------------------
class DownloadFlowTests(unittest.TestCase):
    """使用 mock 模拟完整下载流程，验证 crawl 行为。"""

    def test_crawl_with_mocked_http(self) -> None:
        """模拟两次成功下载，验证文件确实落盘。

        注：默认的 min_file_size=100KB，所以 mock 的图片内容必须超过该阈值，
        才能通过文件大小过滤。
        """
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(
                keyword="rose",
                output_dir=tmp,
                timeout=5,
                max_retries=1,
                min_file_size=0,  # 测试场景关闭大小过滤
            )

            search_resp = _make_search_response(
                [
                    {"objURL": "https://img.test/1.jpg"},
                    {"objURL": "https://img.test/2.png"},
                ]
            )
            # 同时让两个响应的内容长度不同，便于断言区分。
            img_resp_1 = _make_image_response(b"\x89PNG\r\n\x1a\nFAKE_IMG1")
            img_resp_2 = _make_image_response(b"\x89PNG\r\n\x1a\nFAKE_IMG2_EXTRA")

            with mock.patch.object(
                crawler._session,
                "get",
                side_effect=[search_resp, img_resp_1, img_resp_2],
            ):
                result = crawler.crawl(count=2)

            self.assertEqual(len(result), 2)
            sizes = []
            for path in result:
                self.assertTrue(os.path.exists(path))
                size = os.path.getsize(path)
                sizes.append(size)
                self.assertGreater(size, 0)
            # 两张图应当大小不同（内容不同）
            self.assertNotEqual(sizes[0], sizes[1])

    def test_crawl_with_large_mocked_passes_size_filter(self) -> None:
        """默认 min_file_size=100KB 时，使用足够大的 PNG 应能通过过滤。"""
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(
                keyword="rose",
                output_dir=tmp,
                timeout=5,
                max_retries=1,
                # 默认 min_file_size / min_pixels
            )
            png_bytes = _make_png(1920, 1080, payload_size=DEFAULT_MIN_FILE_SIZE + 4096)

            search_resp = _make_search_response(
                [{"objURL": "https://img.test/hi.png"}]
            )
            img_resp = _make_image_response(png_bytes, content_type="image/png")

            with mock.patch.object(
                crawler._session, "get", side_effect=[search_resp, img_resp]
            ):
                result = crawler.crawl(count=1)

            self.assertEqual(len(result), 1)
            # 验证统计字段
            self.assertEqual(crawler.stats.successful_downloads, 1)
            self.assertGreaterEqual(crawler.stats.average_size, DEFAULT_MIN_FILE_SIZE)
            self.assertEqual(crawler.stats.resolutions, [(1920, 1080)])

    def test_download_image_skips_non_image_response(self) -> None:
        """直接验证 _download_image 对非图片响应的处理。"""
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(
                keyword="rose",
                output_dir=tmp,
                timeout=5,
                max_retries=1,
            )
            html_resp = mock.Mock()
            html_resp.headers = {"Content-Type": "text/html"}
            html_resp.raise_for_status = mock.Mock()
            with mock.patch.object(crawler._session, "get", return_value=html_resp):
                result = crawler._download_image("https://x.com/a.jpg", 1)
            self.assertIsNone(result)
            # 统计应记录一次 "非图片" 拒绝
            self.assertEqual(crawler.stats.rejected_non_image, 1)

    def test_download_image_rejects_content_length_too_small(self) -> None:
        """Content-Length 头部声明过小时应直接拒绝，避免下载大文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(
                keyword="rose",
                output_dir=tmp,
                timeout=5,
                max_retries=1,
            )
            small_resp = _make_image_response(
                b"x" * 100,
                content_type="image/jpeg",
                content_length=100,  # 显式声明 100 字节 < 100KB
            )
            with mock.patch.object(
                crawler._session, "get", return_value=small_resp
            ):
                result = crawler._download_image("https://x.com/a.jpg", 1)
            self.assertIsNone(result)
            self.assertEqual(crawler.stats.rejected_content_length_too_small, 1)
            self.assertEqual(crawler.stats.rejected_size_too_small, 0)

    def test_download_image_rejects_actual_size_too_small(self) -> None:
        """Content-Length 缺失但实际字节数过小时也应被过滤。"""
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(
                keyword="rose",
                output_dir=tmp,
                timeout=5,
                max_retries=1,
            )
            # 不传 Content-Length，使用一个远超 100KB 的伪造头部
            small_resp = mock.Mock()
            small_resp.headers = {"Content-Type": "image/jpeg"}  # 无 Content-Length
            small_resp.raise_for_status = mock.Mock()
            small_resp.iter_content = mock.Mock(
                return_value=iter([b"\x89PNG\r\n\x1a\n" + b"x" * 50])
            )
            with mock.patch.object(
                crawler._session, "get", return_value=small_resp
            ):
                result = crawler._download_image("https://x.com/a.jpg", 1)
            self.assertIsNone(result)
            self.assertEqual(crawler.stats.rejected_size_too_small, 1)

    def test_download_image_rejects_low_resolution(self) -> None:
        """文件大小合格但分辨率过低时应被过滤。"""
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(
                keyword="rose",
                output_dir=tmp,
                timeout=5,
                max_retries=1,
                # 放宽文件大小限制，便于聚焦分辨率过滤
                min_file_size=10,
                min_pixels=1920 * 1080,
            )
            # 构造一个 320x240 的 PNG，内容长度足以绕过大小过滤
            png_bytes = _make_png(320, 240, payload_size=4096)
            small_resp = _make_image_response(png_bytes, content_type="image/png")
            with mock.patch.object(
                crawler._session, "get", return_value=small_resp
            ):
                result = crawler._download_image("https://x.com/a.png", 1)
            self.assertIsNone(result)
            self.assertEqual(crawler.stats.rejected_resolution_too_low, 1)
            self.assertEqual(crawler.stats.successful_downloads, 0)

    def test_crawl_stops_on_empty_search_results(self) -> None:
        """搜索返回空数据时应正常退出而不抛异常。"""
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(
                keyword="rose",
                output_dir=tmp,
                timeout=5,
                max_retries=1,
            )
            empty_resp = _make_search_response([])
            with mock.patch.object(
                crawler._session, "get", return_value=empty_resp
            ):
                result = crawler.crawl(count=5)
            self.assertEqual(result, [])

    def test_batch_crawl_convenience_uses_output_dir(self) -> None:
        """便捷函数 batch_crawl 应能使用指定 output_dir。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 通过 patch 拦截 session.get，使返回空列表结束流程
            with mock.patch(
                "crawler.requests.Session.get",
                return_value=_make_search_response([]),
            ):
                paths = batch_crawl(keyword="rose", count=1, output_dir=tmp)
            self.assertEqual(paths, [])

    def test_fetch_handles_invalid_json(self) -> None:
        """当接口返回非 JSON 内容时应当抛出 RequestException。"""
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(keyword="rose", output_dir=tmp)
            bad = mock.Mock()
            bad.headers = {"Content-Type": "text/html"}
            bad.raise_for_status = mock.Mock()
            bad.json.side_effect = json.JSONDecodeError("err", "doc", 0)
            with mock.patch.object(crawler._session, "get", return_value=bad):
                import requests as _req

                with self.assertRaises(_req.RequestException):
                    crawler._fetch_image_urls(pn=0, rn=10)

    def test_fetch_uses_z9_for_high_quality(self) -> None:
        """_fetch_image_urls 应使用 z=9 以请求大图。"""
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(keyword="rose", output_dir=tmp)
            search_resp = _make_search_response([])
            with mock.patch.object(
                crawler._session, "get", return_value=search_resp
            ) as mocked_get:
                crawler._fetch_image_urls(pn=0, rn=10)
            # 第一个位置参数是 URL，第二个是 params
            _, kwargs = mocked_get.call_args
            self.assertEqual(kwargs["params"]["z"], 9)


# ----------------------------------------------------------------------
# 集成测试（默认跳过）
# ----------------------------------------------------------------------
class IntegrationTests(unittest.TestCase):
    """端到端集成测试：默认跳过，需要联网时手动启用。"""

    @unittest.skip("需要联网访问百度图片接口，默认不自动执行")
    def test_real_crawl_rose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = batch_crawl(keyword="玫瑰", count=3, output_dir=tmp)
            self.assertGreater(len(paths), 0)
            for p in paths:
                self.assertTrue(os.path.exists(p))
                self.assertGreater(os.path.getsize(p), 1024)


# ----------------------------------------------------------------------
# 优化功能测试：图片质量统计 / 尺寸解析 / 质量对比
# ----------------------------------------------------------------------
class ImageQualityStatsTests(unittest.TestCase):
    """针对 ImageQualityStats 的单元测试。"""

    def test_initial_state(self) -> None:
        s = ImageQualityStats()
        self.assertEqual(s.successful_downloads, 0)
        self.assertEqual(s.average_size, 0.0)
        self.assertEqual(s.average_pixels, 0.0)
        self.assertEqual(s.pass_rate, 0.0)

    def test_average_size_and_pixels(self) -> None:
        s = ImageQualityStats()
        s.file_sizes = [100 * 1024, 200 * 1024, 300 * 1024]
        s.resolutions = [(800, 600), (1920, 1080), (1024, 768)]
        self.assertAlmostEqual(s.average_size, 200 * 1024)
        # 平均像素: (480000 + 2073600 + 786432) / 3
        expected_px = (800 * 600 + 1920 * 1080 + 1024 * 768) / 3
        self.assertAlmostEqual(s.average_pixels, expected_px)

    def test_pass_rate(self) -> None:
        s = ImageQualityStats()
        s.accepted_urls = 4
        s.successful_downloads = 3
        self.assertAlmostEqual(s.pass_rate, 0.75)

    def test_to_dict_contains_all_keys(self) -> None:
        d = ImageQualityStats().to_dict()
        for key in (
            "total_candidates",
            "accepted_urls",
            "rejected_no_high_quality_url",
            "rejected_non_image",
            "rejected_content_length_too_small",
            "rejected_size_too_small",
            "rejected_resolution_too_low",
            "rejected_download_fail",
            "successful_downloads",
            "total_bytes",
            "average_size_bytes",
            "min_size_bytes",
            "max_size_bytes",
            "average_pixels",
            "pass_rate",
        ):
            self.assertIn(key, d)

    def test_quality_summary_is_string(self) -> None:
        s = ImageQualityStats()
        s.total_candidates = 10
        s.successful_downloads = 5
        s.file_sizes = [200 * 1024]
        s.resolutions = [(1920, 1080)]
        summary = s.quality_summary()
        self.assertIsInstance(summary, str)
        self.assertIn("质量统计", summary)
        self.assertIn("200.0KB", summary)


class CompareQualityTests(unittest.TestCase):
    """针对 compare_quality 的单元测试。"""

    def test_compare_with_no_baseline(self) -> None:
        baseline = ImageQualityStats()
        optimized = ImageQualityStats()
        optimized.file_sizes = [200 * 1024]
        optimized.resolutions = [(1920, 1080)]
        optimized.successful_downloads = 1
        optimized.accepted_urls = 1
        result = compare_quality(baseline, optimized)
        # baseline 全零时 improvement 应为 0
        self.assertEqual(result["average_size_improvement_pct"], 0.0)
        self.assertEqual(result["average_pixels_improvement_pct"], 0.0)
        self.assertEqual(result["baseline"]["min_size_bytes"], 0)
        self.assertEqual(result["optimized"]["min_size_bytes"], 200 * 1024)

    def test_compare_with_positive_improvement(self) -> None:
        baseline = ImageQualityStats()
        baseline.file_sizes = [50 * 1024, 80 * 1024]
        baseline.resolutions = [(640, 480), (800, 600)]
        baseline.successful_downloads = 2
        baseline.accepted_urls = 2

        optimized = ImageQualityStats()
        optimized.file_sizes = [200 * 1024, 300 * 1024]
        optimized.resolutions = [(1920, 1080), (2560, 1440)]
        optimized.successful_downloads = 2
        optimized.accepted_urls = 2

        result = compare_quality(baseline, optimized)
        # 平均大小提升
        self.assertGreater(result["average_size_improvement_pct"], 0.0)
        self.assertGreater(result["average_pixels_improvement_pct"], 0.0)
        # 优化后最小值应 >= 200KB
        self.assertGreaterEqual(result["optimized_min_size_bytes"], 200 * 1024)


class ReadImageDimensionsTests(unittest.TestCase):
    """针对 _read_image_dimensions 的单元测试。"""

    def test_read_png_dimensions(self) -> None:
        png_bytes = _make_png(1920, 1080)
        dims = _read_image_dimensions(png_bytes)
        self.assertEqual(dims, (1920, 1080))

    def test_read_gif_dimensions(self) -> None:
        # GIF89a 头部: "GIF89a" + width(2) + height(2) + ...
        import struct
        gif = b"GIF89a" + struct.pack("<HH", 320, 240) + b"\x00\x00"
        dims = _read_image_dimensions(gif)
        self.assertEqual(dims, (320, 240))

    def test_read_jpeg_dimensions_via_sof0(self) -> None:
        # 构造一个最小可解析的 JPEG: SOI + APP0 段 + SOF0 段
        # APP0 段长度 = 内容(12) + 长度字段自身(2) = 14
        import struct
        soi = b"\xff\xd8"
        app0 = (
            b"\xff\xe0"
            + struct.pack(">H", 14)
            + b"JFIF\x00"
            + b"\x01\x01\x00"
            + b"\x00\x01\x00\x00"
        )
        # SOF0 段：按 JPEG 规范，高度字段在前、宽度字段在后
        sof0 = (
            b"\xff\xc0"
            + struct.pack(">H", 11)  # 长度
            + b"\x08"  # 精度
            + struct.pack(">HH", 1024, 768)  # height=1024, width=768
            + b"\x03"  # 通道数
        )
        jpeg = soi + app0 + sof0
        dims = _read_image_dimensions(jpeg)
        # 函数约定返回 (width, height)
        self.assertEqual(dims, (768, 1024))

    def test_read_webp_lossy_vp8(self) -> None:
        # RIFF + size + WEBP + VP8  + size + 帧头（含 26-29 处的宽高）
        import struct
        webp = bytearray(
            b"RIFF"
            + struct.pack("<I", 30)  # RIFF size
            + b"WEBP"
            + b"VP8 "  # 12-15: chunk type
            + struct.pack("<I", 10)  # VP8 chunk size
            + b"\x00" * 14
        )
        # 在偏移 26-29 字节处放置宽高（VP8 Lossy 帧头）
        struct.pack_into("<HH", webp, 26, 1920, 1080)
        dims = _read_image_dimensions(bytes(webp))
        self.assertEqual(dims, (1920, 1080))

    def test_returns_none_for_invalid_data(self) -> None:
        self.assertIsNone(_read_image_dimensions(b""))
        self.assertIsNone(_read_image_dimensions(b"short"))
        self.assertIsNone(_read_image_dimensions(b"\x00" * 100))

    def test_returns_none_for_unknown_format(self) -> None:
        self.assertIsNone(_read_image_dimensions(b"BMP..." + b"\x00" * 100))


class CrawlerConstructorValidationTests(unittest.TestCase):
    """针对新增构造参数校验的测试。"""

    def test_negative_min_file_size_raises(self) -> None:
        with self.assertRaises(ValueError):
            BaiduImageCrawler(keyword="rose", min_file_size=-1)

    def test_negative_min_pixels_raises(self) -> None:
        with self.assertRaises(ValueError):
            BaiduImageCrawler(keyword="rose", min_pixels=-1)

    def test_default_min_values(self) -> None:
        c = BaiduImageCrawler(keyword="rose")
        self.assertEqual(c.min_file_size, DEFAULT_MIN_FILE_SIZE)
        self.assertEqual(c.min_pixels, 800 * 600)
        self.assertIsInstance(c.stats, ImageQualityStats)

    def test_external_stats_can_be_injected(self) -> None:
        stats = ImageQualityStats()
        c = BaiduImageCrawler(keyword="rose", stats=stats)
        self.assertIs(c.stats, stats)


if __name__ == "__main__":
    unittest.main(verbosity=2)

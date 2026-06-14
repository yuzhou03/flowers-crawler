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
    _decode_baidu_url,
    _guess_extension,
    batch_crawl,
    sanitize_keyword,
)


# ----------------------------------------------------------------------
# 测试辅助
# ----------------------------------------------------------------------
def _make_image_response(content: bytes, content_type: str = "image/jpeg") -> mock.Mock:
    """构造一个模拟的图片下载响应。"""
    resp = mock.Mock()
    resp.headers = {"Content-Type": content_type}
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
    """针对 _parse_image_urls 解析逻辑的测试。"""

    def test_parse_returns_unique_urls_in_priority_order(self) -> None:
        payload = {
            "data": [
                {
                    "objURL": "https%3A%2F%2Fa.com%2F1.jpg",
                    "middleURL": "https://b.com/1.jpg",
                    "thumbURL": "https://c.com/1.jpg",
                },
                {
                    "objURL": "",
                    "middleURL": "https://b.com/2.jpg",
                },
                {"objURL": "not-a-url"},
                "non-dict",
            ]
        }
        urls = BaiduImageCrawler._parse_image_urls(payload)
        self.assertEqual(
            urls,
            [
                "https://a.com/1.jpg",
                "https://b.com/1.jpg",
                "https://c.com/1.jpg",
                "https://b.com/2.jpg",
            ],
        )

    def test_parse_handles_empty_payload(self) -> None:
        self.assertEqual(BaiduImageCrawler._parse_image_urls({}), [])
        self.assertEqual(BaiduImageCrawler._parse_image_urls({"data": []}), [])


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
        """模拟两次成功下载，验证文件确实落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            crawler = BaiduImageCrawler(
                keyword="rose",
                output_dir=tmp,
                timeout=5,
                max_retries=1,
            )

            search_resp = _make_search_response(
                [
                    {"objURL": "https://img.test/1.jpg"},
                    {"objURL": "https://img.test/2.png"},
                ]
            )
            # 注意：必须生成两个独立的 Mock 对象，
            # 否则第二次调用将拿到一个已被耗尽的迭代器。
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

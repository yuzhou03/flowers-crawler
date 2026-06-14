# -*- coding: utf-8 -*-
"""
Pexels 爬虫单元测试与集成测试
============================

覆盖范围:
    1. 中文 -> 英文关键词翻译（精确、子串、修饰词、拼音 fallback）
    2. PexelsCache 缓存（写入、读取、磁盘校验、损坏恢复）
    3. PexelsAPIError 异常构造
    4. PexelsImageCrawler 构造参数校验
    5. _extract_best_url URL 优先级与分辨率提取
    6. 搜索接口：成功 / 401 / 403 / 429 / 5xx / 网络异常
    7. 下载流程：Content-Type、Content-Length、文件大小、分辨率过滤
    8. 缓存命中：相同 URL 不重复下载
    9. CLI 入口：--source pexels 参数解析
   10. 集成测试（默认跳过）：真实 Pexels API 调用
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

# 允许 ``python src/test_crawler_pexels.py`` 直接执行
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import main as cli_main  # noqa: E402
import crawler_pexels as pexels_mod  # noqa: E402
from crawler import ImageQualityStats  # noqa: E402
from crawler_pexels import (  # noqa: E402
    FLOWER_KEYWORD_MAP,
    PEXELS_API_BASE,
    PexelsAPIError,
    PexelsCache,
    PexelsImageCrawler,
    _compute_url_hash,
    translate_to_english,
)


# ----------------------------------------------------------------------
# 测试辅助
# ----------------------------------------------------------------------

# 哨兵值：在 _make_pexels_photo 中表示"该字段不提供 URL"
# 使用哨兵而非 None 以避免与"显式 None 触发默认 base URL"混淆
_OMIT_URL = object()


def _make_pexels_photo(
    photo_id: int = 1,
    width: int = 4000,
    height: int = 6000,
    original: object = _OMIT_URL,
    large2x: object = _OMIT_URL,
    large: object = _OMIT_URL,
    medium: object = _OMIT_URL,
) -> dict:
    """构造一个 Pexels /v1/search 接口返回的 photo 字典。

    对于 ``original``/``large2x`` 等 src 字段，使用 ``_OMIT_URL`` 哨兵
    表示该字段不提供 URL（用于测试降级回退逻辑）。如需显式提供 URL，
    传入字符串即可。
    """
    base = f"https://images.pexels.com/photos/{photo_id}/pexels-photo-{photo_id}.jpeg"
    return {
        "id": photo_id,
        "width": width,
        "height": height,
        "url": f"https://www.pexels.com/photo/{photo_id}/",
        "photographer": "Test Photographer",
        "photographer_url": "https://www.pexels.com/@test/",
        "photographer_id": 1,
        "avg_color": "#978e82",
        "src": {
            "original": base if original is _OMIT_URL else original,
            "large2x": base + "?large2x" if large2x is _OMIT_URL else large2x,
            "large": base + "?large" if large is _OMIT_URL else large,
            "medium": base + "?medium" if medium is _OMIT_URL else medium,
            "small": base + "?small",
            "portrait": base + "?portrait",
            "landscape": base + "?landscape",
            "tiny": base + "?tiny",
        },
        "alt": f"test photo {photo_id}",
    }


def _make_search_response(
    photos: list, status_code: int = 200, headers: dict = None,
) -> mock.Mock:
    """构造一个模拟的 Pexels /v1/search 响应。"""
    resp = mock.Mock()
    resp.headers = headers or {"Content-Type": "application/json"}
    resp.status_code = status_code
    resp.raise_for_status = mock.Mock()
    resp.json.return_value = {
        "page": 1,
        "per_page": 80,
        "photos": photos,
        "total_results": len(photos),
    }
    return resp


def _make_image_response(
    content: bytes,
    content_type: str = "image/jpeg",
    content_length: object = _OMIT_URL,
) -> mock.Mock:
    """构造一个模拟的图片下载响应。

    Args:
        content: 响应内容。
        content_type: Content-Type 头部。
        content_length: Content-Length 取值。
            * ``_OMIT_URL``（默认）: 根据 ``content`` 长度自动计算；
            * 整数: 显式声明 Content-Length；
            * 其它真值: 不设置 Content-Length 头部，模拟 chunked transfer。

    Notes:
        ``iter_content`` 使用 ``side_effect``，每次调用都返回新迭代器，
        以便同一响应可被多次复用（多次下载时）。
    """
    resp = mock.Mock()
    headers = {"Content-Type": content_type}
    if content_length is _OMIT_URL:
        if content is not None:
            headers["Content-Length"] = str(len(content))
    elif content_length is None:
        # 显式 None -> 不设置 Content-Length 头部
        pass
    else:
        # 显式整数 -> 强制使用该值
        headers["Content-Length"] = str(content_length)
    resp.headers = headers
    resp.raise_for_status = mock.Mock()
    # side_effect 每次调用都会执行 lambda 生成新迭代器
    resp.iter_content = mock.Mock(
        side_effect=lambda chunk_size: iter([content]) if content is not None else iter([]),
    )
    return resp


def _make_png(width: int, height: int, payload_size: int = None) -> bytes:
    """构造一个合法 PNG 头部字节流。"""
    import struct
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    ihdr += b"\x08\x02\x00\x00\x00"
    ihdr += b"\x00\x00\x00\x00"
    body = header + ihdr
    if payload_size is not None and payload_size > len(body):
        body += b"\x00" * (payload_size - len(body))
    return body


# ----------------------------------------------------------------------
# 中文 -> 英文 翻译测试
# ----------------------------------------------------------------------
class TranslateKeywordTests(unittest.TestCase):
    """针对 translate_to_english 的测试。"""

    def test_exact_match_returns_english(self) -> None:
        self.assertEqual(translate_to_english("郁金香"), "tulip")
        self.assertEqual(translate_to_english("绣球花"), "hydrangea")
        self.assertEqual(translate_to_english("荷花"), "lotus")
        self.assertEqual(translate_to_english("樱花"), "cherry blossom")

    def test_ascii_passthrough(self) -> None:
        self.assertEqual(translate_to_english("tulip"), "tulip")
        self.assertEqual(translate_to_english("Rose"), "Rose")
        self.assertEqual(translate_to_english("cherry blossom"), "cherry blossom")

    def test_substring_match_preserves_modifier(self) -> None:
        # "粉色郁金香" -> "pink tulip"（修饰词粉色 + 郁金香）
        result = translate_to_english("粉色郁金香")
        self.assertIn("tulip", result)
        self.assertIn("pink", result)

    def test_substring_match_without_modifier(self) -> None:
        self.assertEqual(translate_to_english("樱花"), "cherry blossom")
        # "日本樱花" 中"日本"不在修饰词表中，应被转为拼音保留
        result = translate_to_english("日本樱花")
        self.assertIn("cherry blossom", result)
        self.assertNotEqual(result, "cherry blossom")  # 不应丢失"日本"部分

    def test_unknown_chinese_falls_back_to_pinyin(self) -> None:
        # 未在映射表的中文：使用 pypinyin 转换
        result = translate_to_english("雪绒花")
        # pypinyin 输出全小写拼音，无音调
        self.assertTrue(all(ord(c) < 128 for c in result))
        self.assertGreater(len(result), 0)

    def test_empty_input(self) -> None:
        self.assertEqual(translate_to_english(""), "")
        self.assertEqual(translate_to_english("   "), "")

    def test_whitespace_stripped(self) -> None:
        self.assertEqual(translate_to_english("  郁金香  "), "tulip")

    def test_mixed_chinese_english(self) -> None:
        # 中文花名 + 英文修饰词（"macro"等不会在 modifier_map）
        result = translate_to_english("郁金香 macro")
        # macro 应该被保留
        self.assertIn("tulip", result)
        self.assertIn("macro", result)

    def test_flower_mapping_covers_requirements(self) -> None:
        """确保需求中列出的重点花卉都已在映射表中。"""
        required = {"郁金香", "绣球", "荷花", "樱花"}
        for flower in required:
            self.assertIn(
                flower, FLOWER_KEYWORD_MAP,
                f"花卉 '{flower}' 应在 FLOWER_KEYWORD_MAP 中",
            )

    def test_macro_modifier_supported(self) -> None:
        result = translate_to_english("微距郁金香")
        self.assertIn("tulip", result)
        self.assertIn("macro", result)


# ----------------------------------------------------------------------
# 缓存测试
# ----------------------------------------------------------------------
class PexelsCacheTests(unittest.TestCase):
    """针对 PexelsCache 的单元测试。"""

    def test_put_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = PexelsCache(tmp)
            url = "https://images.pexels.com/photos/1/test.jpeg"
            file_path = os.path.join(tmp, "test.jpeg")
            with open(file_path, "wb") as fp:
                fp.write(b"fake")
            cache.put(url, file_path)
            self.assertEqual(cache.get(url), file_path)

    def test_get_returns_none_when_url_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = PexelsCache(tmp)
            self.assertIsNone(cache.get("https://x.com/missing.jpg"))

    def test_get_returns_none_when_file_deleted(self) -> None:
        """缓存指向的文件被删除时，get 应返回 None。"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = PexelsCache(tmp)
            url = "https://images.pexels.com/photos/1/test.jpeg"
            file_path = os.path.join(tmp, "test.jpeg")
            with open(file_path, "wb") as fp:
                fp.write(b"fake")
            cache.put(url, file_path)
            os.remove(file_path)
            self.assertIsNone(cache.get(url))

    def test_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache1 = PexelsCache(tmp)
            url = "https://images.pexels.com/photos/2/test.jpeg"
            file_path = os.path.join(tmp, "test2.jpeg")
            with open(file_path, "wb") as fp:
                fp.write(b"data")
            cache1.put(url, file_path)
            cache1.save()
            self.assertTrue(os.path.exists(os.path.join(tmp, ".pexels_cache.json")))

            # 重新构造，应能加载到相同内容
            cache2 = PexelsCache(tmp)
            self.assertEqual(cache2.get(url), file_path)
            self.assertEqual(len(cache2), 1)

    def test_corrupted_cache_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, ".pexels_cache.json")
            with open(cache_path, "w", encoding="utf-8") as fp:
                fp.write("{ invalid json")
            cache = PexelsCache(tmp)
            self.assertEqual(len(cache), 0)

    def test_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = PexelsCache(tmp)
            url = "https://images.pexels.com/photos/3/test.jpeg"
            file_path = os.path.join(tmp, "test3.jpeg")
            with open(file_path, "wb") as fp:
                fp.write(b"x")
            cache.put(url, file_path)
            cache.save()
            cache.clear()
            self.assertEqual(len(cache), 0)
            self.assertFalse(os.path.exists(os.path.join(tmp, ".pexels_cache.json")))

    def test_url_hash_is_stable(self) -> None:
        h1 = _compute_url_hash("https://x.com/a.jpg")
        h2 = _compute_url_hash("https://x.com/a.jpg")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 16)
        # 不同 URL 哈希不同
        h3 = _compute_url_hash("https://x.com/b.jpg")
        self.assertNotEqual(h1, h3)

    def test_contains_operator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = PexelsCache(tmp)
            url = "https://x.com/c.jpg"
            file_path = os.path.join(tmp, "c.jpg")
            with open(file_path, "wb") as fp:
                fp.write(b"x")
            cache.put(url, file_path)
            self.assertIn(url, cache)
            self.assertNotIn("https://x.com/missing.jpg", cache)


# ----------------------------------------------------------------------
# URL 提取测试
# ----------------------------------------------------------------------
class ExtractBestUrlTests(unittest.TestCase):
    """针对 PexelsImageCrawler._extract_best_url 的测试。"""

    def test_prefers_original(self) -> None:
        photo = _make_pexels_photo(
            original="https://x.com/original.jpg",
            large2x="https://x.com/large2x.jpg",
            large="https://x.com/large.jpg",
            medium="https://x.com/medium.jpg",
        )
        url, (w, h) = PexelsImageCrawler._extract_best_url(photo)
        self.assertEqual(url, "https://x.com/original.jpg")
        self.assertEqual((w, h), (4000, 6000))

    def test_falls_back_to_large2x(self) -> None:
        photo = _make_pexels_photo(
            original=None,
            large2x="https://x.com/large2x.jpg",
        )
        url, _ = PexelsImageCrawler._extract_best_url(photo)
        self.assertEqual(url, "https://x.com/large2x.jpg")

    def test_falls_back_to_large(self) -> None:
        photo = _make_pexels_photo(
            original=None, large2x=None,
            large="https://x.com/large.jpg",
        )
        url, _ = PexelsImageCrawler._extract_best_url(photo)
        self.assertEqual(url, "https://x.com/large.jpg")

    def test_falls_back_to_medium(self) -> None:
        photo = _make_pexels_photo(
            original=None, large2x=None, large=None,
            medium="https://x.com/medium.jpg",
        )
        url, _ = PexelsImageCrawler._extract_best_url(photo)
        self.assertEqual(url, "https://x.com/medium.jpg")

    def test_returns_none_when_no_url(self) -> None:
        photo = _make_pexels_photo(
            original=None, large2x=None, large=None, medium=None,
        )
        url, _ = PexelsImageCrawler._extract_best_url(photo)
        self.assertIsNone(url)

    def test_handles_missing_src(self) -> None:
        photo = {"id": 1, "width": 100, "height": 100, "src": {}}
        url, (w, h) = PexelsImageCrawler._extract_best_url(photo)
        self.assertIsNone(url)
        self.assertEqual((w, h), (100, 100))

    def test_handles_missing_dimensions(self) -> None:
        photo = {
            "id": 1,
            "src": {"original": "https://x.com/o.jpg"},
        }
        url, (w, h) = PexelsImageCrawler._extract_best_url(photo)
        self.assertEqual(url, "https://x.com/o.jpg")
        self.assertEqual((w, h), (0, 0))


# ----------------------------------------------------------------------
# Retry-After 解析
# ----------------------------------------------------------------------
class ParseRetryAfterTests(unittest.TestCase):
    """针对 PexelsImageCrawler._parse_retry_after 的测试。"""

    def test_returns_value_from_header(self) -> None:
        resp = mock.Mock()
        resp.headers = {"Retry-After": "30"}
        self.assertEqual(PexelsImageCrawler._parse_retry_after(resp), 30)

    def test_caps_at_60(self) -> None:
        resp = mock.Mock()
        resp.headers = {"Retry-After": "999"}
        self.assertEqual(PexelsImageCrawler._parse_retry_after(resp), 60)

    def test_default_when_missing(self) -> None:
        resp = mock.Mock()
        resp.headers = {}
        self.assertEqual(PexelsImageCrawler._parse_retry_after(resp), 5)

    def test_floor_at_1(self) -> None:
        resp = mock.Mock()
        resp.headers = {"Retry-After": "0"}
        self.assertEqual(PexelsImageCrawler._parse_retry_after(resp), 1)

    def test_invalid_value_falls_back_to_default(self) -> None:
        resp = mock.Mock()
        resp.headers = {"Retry-After": "not-a-number"}
        self.assertEqual(PexelsImageCrawler._parse_retry_after(resp), 5)


# ----------------------------------------------------------------------
# 构造参数校验
# ----------------------------------------------------------------------
class PexelsCrawlerCtorTests(unittest.TestCase):
    """针对 PexelsImageCrawler 构造器的参数校验。"""

    def setUp(self) -> None:
        self._api_key_env = os.environ.get("PEXELS_API_KEY")
        # 测试期间显式清除环境变量，避免污染
        os.environ.pop("PEXELS_API_KEY", None)

    def tearDown(self) -> None:
        if self._api_key_env is None:
            os.environ.pop("PEXELS_API_KEY", None)
        else:
            os.environ["PEXELS_API_KEY"] = self._api_key_env

    def test_empty_keyword_raises(self) -> None:
        with self.assertRaises(ValueError):
            PexelsImageCrawler(keyword="", api_key="test-key")

    def test_whitespace_keyword_raises(self) -> None:
        with self.assertRaises(ValueError):
            PexelsImageCrawler(keyword="   ", api_key="test-key")

    def test_missing_api_key_raises(self) -> None:
        with self.assertRaises(PexelsAPIError) as ctx:
            PexelsImageCrawler(keyword="tulip")
        self.assertIn("API Key", str(ctx.exception))
        self.assertFalse(ctx.exception.retryable)

    def test_api_key_from_env_var(self) -> None:
        os.environ["PEXELS_API_KEY"] = "env-test-key"
        c = PexelsImageCrawler(keyword="tulip")
        self.assertEqual(c._session.headers["Authorization"], "env-test-key")

    def test_api_key_param_overrides_env(self) -> None:
        os.environ["PEXELS_API_KEY"] = "env-key"
        c = PexelsImageCrawler(keyword="tulip", api_key="param-key")
        self.assertEqual(c._session.headers["Authorization"], "param-key")

    def test_translated_keyword_set(self) -> None:
        c = PexelsImageCrawler(keyword="郁金香", api_key="k")
        self.assertEqual(c.keyword, "郁金香")
        self.assertEqual(c.translated_keyword, "tulip")

    def test_negative_min_file_size_raises(self) -> None:
        with self.assertRaises(ValueError):
            PexelsImageCrawler(keyword="tulip", api_key="k", min_file_size=-1)

    def test_negative_min_pixels_raises(self) -> None:
        with self.assertRaises(ValueError):
            PexelsImageCrawler(keyword="tulip", api_key="k", min_pixels=-1)

    def test_negative_max_retries_raises(self) -> None:
        with self.assertRaises(ValueError):
            PexelsImageCrawler(keyword="tulip", api_key="k", max_retries=-1)

    def test_external_stats_injected(self) -> None:
        stats = ImageQualityStats()
        c = PexelsImageCrawler(keyword="tulip", api_key="k", stats=stats)
        self.assertIs(c.stats, stats)

    def test_use_cache_disabled(self) -> None:
        c = PexelsImageCrawler(keyword="tulip", api_key="k", use_cache=False)
        self.assertIsNone(c._cache)


# ----------------------------------------------------------------------
# 搜索接口错误处理
# ----------------------------------------------------------------------
class SearchPhotosErrorTests(unittest.TestCase):
    """针对 _search_photos 在各种错误下的行为测试。"""

    def setUp(self) -> None:
        os.environ["PEXELS_API_KEY"] = "test-key"
        self.tmp = tempfile.TemporaryDirectory()
        self.crawler = PexelsImageCrawler(
            keyword="tulip",
            output_dir=self.tmp.name,
            max_retries=2,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_401_raises_non_retryable(self) -> None:
        resp = _make_search_response([], status_code=401)
        resp.raise_for_status = mock.Mock(
            side_effect=Exception("should not call raise_for_status for 401")
        )
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            with self.assertRaises(PexelsAPIError) as ctx:
                self.crawler._search_photos(page=1, per_page=10)
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_403_raises_non_retryable(self) -> None:
        resp = _make_search_response([], status_code=403)
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            with self.assertRaises(PexelsAPIError) as ctx:
                self.crawler._search_photos(page=1, per_page=10)
        self.assertFalse(ctx.exception.retryable)

    def test_404_returns_empty(self) -> None:
        resp = _make_search_response([], status_code=404)
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            result = self.crawler._search_photos(page=1, per_page=10)
        self.assertEqual(result, [])

    def test_429_retries_and_succeeds(self) -> None:
        """先返回 429 触发退避，再次成功。"""
        rate_limited = _make_search_response(
            [], status_code=429,
            headers={"Content-Type": "application/json", "Retry-After": "0"},
        )
        success = _make_search_response([_make_pexels_photo()])
        with mock.patch.object(
            self.crawler._session, "get",
            side_effect=[rate_limited, success],
        ):
            with mock.patch("time.sleep") as mock_sleep:
                result = self.crawler._search_photos(page=1, per_page=10)
        self.assertEqual(len(result), 1)
        # 应至少 sleep 过一次（限流退避）
        self.assertTrue(mock_sleep.called)

    def test_5xx_retries_then_raises(self) -> None:
        """连续返回 5xx，超过重试次数后抛出可重试异常。"""
        resp_500 = _make_search_response([], status_code=500)
        with mock.patch.object(
            self.crawler._session, "get", return_value=resp_500,
        ):
            with mock.patch("time.sleep"):
                with self.assertRaises(PexelsAPIError) as ctx:
                    self.crawler._search_photos(page=1, per_page=10)
        self.assertTrue(ctx.exception.retryable)

    def test_400_raises_non_retryable(self) -> None:
        resp_400 = _make_search_response([], status_code=400)
        with mock.patch.object(self.crawler._session, "get", return_value=resp_400):
            with self.assertRaises(PexelsAPIError) as ctx:
                self.crawler._search_photos(page=1, per_page=10)
        self.assertFalse(ctx.exception.retryable)

    def test_invalid_json_raises(self) -> None:
        resp = _make_search_response([])
        resp.json.side_effect = ValueError("bad json")
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            with self.assertRaises(PexelsAPIError):
                self.crawler._search_photos(page=1, per_page=10)

    def test_network_error_raises_retryable(self) -> None:
        import requests as _requests
        with mock.patch.object(
            self.crawler._session, "get",
            side_effect=_requests.ConnectionError("network down"),
        ):
            with mock.patch("time.sleep"):
                with self.assertRaises(PexelsAPIError) as ctx:
                    self.crawler._search_photos(page=1, per_page=10)
        self.assertTrue(ctx.exception.retryable)


# ----------------------------------------------------------------------
# 下载流程
# ----------------------------------------------------------------------
class PexelsDownloadFlowTests(unittest.TestCase):
    """针对 _download_image 各阶段过滤的测试。"""

    def setUp(self) -> None:
        os.environ["PEXELS_API_KEY"] = "test-key"
        self.tmp = tempfile.TemporaryDirectory()
        self.crawler = PexelsImageCrawler(
            keyword="tulip",
            output_dir=self.tmp.name,
            min_file_size=10 * 1024,
            min_pixels=400 * 300,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_rejects_non_image_content_type(self) -> None:
        url = "https://images.pexels.com/photos/1/test.html"
        resp = _make_image_response(b"<html></html>", content_type="text/html")
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            result = self.crawler._download_image(url, 1)
        self.assertIsNone(result)
        self.assertEqual(self.crawler.stats.rejected_non_image, 1)

    def test_rejects_content_length_too_small(self) -> None:
        url = "https://images.pexels.com/photos/1/small.jpg"
        # 声明的 Content-Length 小于 min_file_size
        resp = _make_image_response(b"x" * 100, content_length=100)
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            result = self.crawler._download_image(url, 1)
        self.assertIsNone(result)
        self.assertEqual(self.crawler.stats.rejected_content_length_too_small, 1)

    def test_rejects_actual_size_too_small(self) -> None:
        url = "https://images.pexels.com/photos/1/actual-small.jpg"
        # content_length=None 不设置 Content-Length 头部，强制走实际字节校验
        resp = _make_image_response(b"x" * 100, content_length=None)
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            result = self.crawler._download_image(url, 1)
        self.assertIsNone(result)
        self.assertEqual(self.crawler.stats.rejected_size_too_small, 1)

    def test_rejects_low_resolution(self) -> None:
        url = "https://images.pexels.com/photos/1/lowres.png"
        # 构造一个合法但分辨率低的 PNG
        png_bytes = _make_png(320, 240)  # 76800 < 400*300=120000
        # 确保 PNG 字节数本身大于 min_file_size
        payload = png_bytes + b"\x00" * (15 * 1024)
        resp = _make_image_response(payload, content_length=len(payload))
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            result = self.crawler._download_image(url, 1)
        self.assertIsNone(result)
        self.assertEqual(self.crawler.stats.rejected_resolution_too_low, 1)

    def test_successful_download(self) -> None:
        url = "https://images.pexels.com/photos/1/good.png"
        png_bytes = _make_png(1920, 1080)  # 2073600 远大于阈值
        payload = png_bytes + b"\x00" * (15 * 1024)
        resp = _make_image_response(payload, content_length=len(payload))
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            result = self.crawler._download_image(url, 1)
        self.assertIsNotNone(result)
        self.assertTrue(os.path.exists(result))
        self.assertGreater(os.path.getsize(result), 10 * 1024)
        self.assertEqual(self.crawler.stats.successful_downloads, 1)
        self.assertIn((1920, 1080), self.crawler.stats.resolutions)

    def test_cache_hit_skips_download(self) -> None:
        """缓存命中时不应再次发起下载。"""
        url = "https://images.pexels.com/photos/1/cached.png"
        png_bytes = _make_png(1920, 1080)
        payload = png_bytes + b"\x00" * (15 * 1024)
        # 第一次下载
        resp = _make_image_response(payload, content_length=len(payload))
        with mock.patch.object(self.crawler._session, "get", return_value=resp) as m:
            first = self.crawler._download_image(url, 1)
        self.assertIsNotNone(first)
        self.assertEqual(m.call_count, 1)
        self.assertEqual(self.crawler.stats.successful_downloads, 1)

        # 第二次同 URL：缓存命中，不应再调用 _session.get
        with mock.patch.object(self.crawler._session, "get") as m2:
            second = self.crawler._download_image(url, 2)
        self.assertEqual(first, second)
        self.assertEqual(m2.call_count, 0)
        self.assertEqual(self.crawler.stats.cache_hits, 1)
        # successful_downloads 不应增加
        self.assertEqual(self.crawler.stats.successful_downloads, 1)

    def test_download_records_in_cache(self) -> None:
        url = "https://images.pexels.com/photos/1/cached2.png"
        png_bytes = _make_png(1920, 1080)
        payload = png_bytes + b"\x00" * (15 * 1024)
        resp = _make_image_response(payload, content_length=len(payload))
        with mock.patch.object(self.crawler._session, "get", return_value=resp):
            result = self.crawler._download_image(url, 1)
        self.assertIsNotNone(result)
        self.assertIn(url, self.crawler._cache)


# ----------------------------------------------------------------------
# crawl 主流程
# ----------------------------------------------------------------------
class PexelsCrawlTests(unittest.TestCase):
    """针对 crawl 方法的端到端 mock 测试。"""

    def setUp(self) -> None:
        os.environ["PEXELS_API_KEY"] = "test-key"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_big_png(self) -> bytes:
        png = _make_png(1920, 1080)
        return png + b"\x00" * (15 * 1024)

    def test_crawl_returns_downloaded_files(self) -> None:
        photos = [_make_pexels_photo(photo_id=i) for i in range(1, 4)]
        search_resp = _make_search_response(photos)
        empty_search = _make_search_response([])
        image_resp = _make_image_response(
            self._make_big_png(), content_length=len(self._make_big_png()),
        )
        # 同一个 crawler 实例同时用于 mock 和调用
        c = self.crawler()
        with mock.patch.object(
            c._session, "get",
            side_effect=[search_resp, image_resp, image_resp, image_resp, empty_search],
        ):
            paths = c.crawl(count=3)
        self.assertEqual(len(paths), 3)
        for p in paths:
            self.assertTrue(os.path.exists(p))
            # Pexels 原始 URL 一般是 .jpeg，但 .guess_extension 可能归一化为 .jpg
            self.assertTrue(
                p.endswith((".jpg", ".jpeg")),
                f"期望 .jpg/.jpeg 扩展名，实际: {p}",
            )

    def test_crawl_filters_low_resolution_from_metadata(self) -> None:
        """API 声明的低分辨率 photo 应在下载前被过滤。"""
        photos = [
            _make_pexels_photo(photo_id=1, width=4000, height=6000),  # OK
            _make_pexels_photo(photo_id=2, width=320, height=240),    # 拒绝
        ]
        search_resp = _make_search_response(photos)
        empty_search = _make_search_response([])
        image_resp = _make_image_response(
            self._make_big_png(), content_length=len(self._make_big_png()),
        )
        crawler = self.crawler(min_pixels=800 * 600)
        with mock.patch.object(
            crawler._session, "get",
            side_effect=[search_resp, image_resp, empty_search],
        ):
            paths = crawler.crawl(count=2)
        # 只下载了第一张（高分），第二张被预过滤
        self.assertEqual(len(paths), 1)
        self.assertEqual(crawler.stats.rejected_resolution_too_low, 1)

    def test_crawl_stops_when_no_more_results(self) -> None:
        """搜索接口返回空 photos 时应停止翻页。"""
        search_resp = _make_search_response([])
        c = self.crawler()
        with mock.patch.object(
            c._session, "get", return_value=search_resp,
        ):
            paths = c.crawl(count=5)
        self.assertEqual(paths, [])

    def test_crawl_uses_cache_on_second_run(self) -> None:
        """同一 output_dir 的两次 crawl 第二次应大量命中缓存。"""
        photos = [_make_pexels_photo(photo_id=i) for i in range(1, 3)]
        search_resp = _make_search_response(photos)
        empty_search = _make_search_response([])
        image_resp = _make_image_response(
            self._make_big_png(), content_length=len(self._make_big_png()),
        )
        # 第一次 crawl：所有响应都走下载
        c1 = self.crawler()
        with mock.patch.object(
            c1._session, "get",
            side_effect=[search_resp, image_resp, image_resp, empty_search],
        ):
            paths1 = c1.crawl(count=2)
        self.assertEqual(len(paths1), 2)

        # 第二次 crawl：相同 URL 应命中缓存
        c2 = self.crawler()
        with mock.patch.object(
            c2._session, "get",
            side_effect=[search_resp],
        ):
            paths2 = c2.crawl(count=2)
        self.assertEqual(len(paths2), 2)
        # 缓存中应有 2 条记录
        self.assertEqual(c2.stats.cache_hits, 2)
        # 第二次没有实际下载
        self.assertEqual(c2.stats.successful_downloads, 0)

    def test_crawl_handles_401_by_returning_partial(self) -> None:
        """鉴权失败（401）应停止 crawl，不抛异常。"""
        # 第一次 search 401 后抛出，后面的 get 不会被调用
        resp_401 = _make_search_response([], status_code=401)
        c = self.crawler()
        with mock.patch.object(
            c._session, "get", return_value=resp_401,
        ):
            paths = c.crawl(count=2)
        self.assertEqual(paths, [])

    def test_crawl_invalid_count_raises(self) -> None:
        c = self.crawler()
        with self.assertRaises(ValueError):
            c.crawl(count=0)
        with self.assertRaises(ValueError):
            c.crawl(count=-1)

    def test_translation_used_in_api_request(self) -> None:
        """API 请求的 query 参数应为翻译后的英文。"""
        c = PexelsImageCrawler(
            keyword="郁金香", output_dir=self.tmp.name,
        )
        search_resp = _make_search_response([])
        with mock.patch.object(
            c._session, "get", return_value=search_resp,
        ) as m:
            c.crawl(count=1)
        # 第一个调用是 search 请求
        called_url = m.call_args[0][0]
        self.assertEqual(called_url, f"{PEXELS_API_BASE}/search")
        params = m.call_args[1].get("params") or m.call_args[1].get("kwargs", {}).get("params")
        self.assertEqual(params["query"], "tulip")

    def crawler(self, **kwargs) -> PexelsImageCrawler:
        defaults = {
            "keyword": "tulip",
            "output_dir": self.tmp.name,
            "max_retries": 2,
            # 测试用 10KB 阈值，避免构造大型 fake 图片
            "min_file_size": 10 * 1024,
        }
        defaults.update(kwargs)
        return PexelsImageCrawler(**defaults)


# ----------------------------------------------------------------------
# CLI 集成
# ----------------------------------------------------------------------
class PexelsCliTests(unittest.TestCase):
    """针对 --source pexels 的 CLI 行为测试。"""

    def setUp(self) -> None:
        os.environ["PEXELS_API_KEY"] = "env-cli-key"

    def tearDown(self) -> None:
        os.environ.pop("PEXELS_API_KEY", None)

    def test_default_source_is_baidu(self) -> None:
        args = cli_main.parse_args(["rose"], interactive=False)
        self.assertEqual(args.source, "baidu")

    def test_explicit_source_pexels(self) -> None:
        args = cli_main.parse_args(
            ["rose", "--source", "pexels"],
            interactive=False,
        )
        self.assertEqual(args.source, "pexels")

    def test_pexels_api_key_from_cli(self) -> None:
        args = cli_main.parse_args(
            ["rose", "--source", "pexels", "--pexels-api-key", "my-key"],
            interactive=False,
        )
        self.assertEqual(args.pexels_api_key, "my-key")

    def test_pexels_requires_api_key(self) -> None:
        # 临时清除环境变量
        os.environ.pop("PEXELS_API_KEY", None)
        with self.assertRaises(SystemExit):
            cli_main.parse_args(
                ["rose", "--source", "pexels"],
                interactive=False,
            )

    def test_invalid_source_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            cli_main.parse_args(
                ["rose", "--source", "unsplash"],
                interactive=False,
            )

    def test_build_crawler_returns_pexels_instance(self) -> None:
        from crawler_pexels import PexelsImageCrawler
        args = cli_main.parse_args(
            ["郁金香", "--source", "pexels", "-n", "2"],
            interactive=False,
        )
        crawler = cli_main._build_crawler(
            args, "郁金香", tempfile.gettempdir(),
        )
        self.assertIsInstance(crawler, PexelsImageCrawler)
        self.assertEqual(crawler.translated_keyword, "tulip")

    def test_build_crawler_returns_baidu_instance(self) -> None:
        from crawler import BaiduImageCrawler
        args = cli_main.parse_args(
            ["rose", "-n", "2"],
            interactive=False,
        )
        crawler = cli_main._build_crawler(
            args, "rose", tempfile.gettempdir(),
        )
        self.assertIsInstance(crawler, BaiduImageCrawler)


# ----------------------------------------------------------------------
# 集成测试（默认跳过）
# ----------------------------------------------------------------------
class PexelsIntegrationTests(unittest.TestCase):
    """需要真实 Pexels API Key 的端到端测试，默认不执行。"""

    @unittest.skip("需要联网与 PEXELS_API_KEY，默认不自动执行")
    def test_real_crawl_tulip(self) -> None:
        if not os.environ.get("PEXELS_API_KEY"):
            self.skipTest("PEXELS_API_KEY 未设置")
        with tempfile.TemporaryDirectory() as tmp:
            paths = PexelsImageCrawler(
                keyword="tulip",
                output_dir=tmp,
            ).crawl(count=3)
            self.assertGreater(len(paths), 0)
            for p in paths:
                self.assertTrue(os.path.exists(p))
                self.assertGreater(os.path.getsize(p), 100 * 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)

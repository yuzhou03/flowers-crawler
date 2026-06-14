# -*- coding: utf-8 -*-
"""
百度图片爬虫核心模块
提供根据关键词搜索百度图片并下载高清原图的能力。

主要功能:
    1. 调用百度图片搜索的 JSON 接口获取图片元数据
    2. 从返回数据中提取高清图片 URL
    3. 按需下载图片到本地指定目录
    4. 支持分页抓取以满足大批量下载需求
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Iterable, List, Optional
from urllib.parse import unquote

import requests


# 百度图片搜索的 JSON 接口
BAIDU_IMAGE_SEARCH_URL = "https://image.baidu.com/search/acjson"

# 单次请求的图片数量上限（百度接口单次最多返回 60 条）
BAIDU_PAGE_SIZE = 60

# 默认请求头，模拟浏览器访问，避免被反爬
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://image.baidu.com/",
}

# 支持的图片扩展名
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


class BaiduImageCrawler:
    """百度图片爬虫封装。

    通过百度图片搜索的 acjson 接口获取图片列表，
    并将图片下载到本地目录。

    Attributes:
        keyword: 搜索关键词（花的名称等）。
        output_dir: 图片保存目录。
        timeout: 单次 HTTP 请求的超时时间（秒）。
        max_retries: 单张图片下载失败时的重试次数。
    """

    def __init__(
        self,
        keyword: str,
        output_dir: str = "out",
        timeout: int = 15,
        max_retries: int = 3,
    ) -> None:
        if not keyword or not keyword.strip():
            raise ValueError("搜索关键词不能为空")
        self.keyword = keyword.strip()
        self.output_dir = output_dir
        self.timeout = timeout
        self.max_retries = max_retries

        # 复用 requests.Session，自动管理 Cookie / 连接池
        self._session: requests.Session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------
    def crawl(self, count: int) -> List[str]:
        """抓取指定数量的图片。

        Args:
            count: 期望下载的图片数量，<= 0 会被视为非法值并抛出异常。

        Returns:
            实际成功下载的图片文件路径列表。
        """
        if count <= 0:
            raise ValueError("下载数量必须大于 0")

        os.makedirs(self.output_dir, exist_ok=True)

        downloaded: List[str] = []
        page_no = 0
        # pn 是百度接口的起始偏移量，每次累加 BAIDU_PAGE_SIZE
        pn = 0
        # 控制相邻请求的间隔，避免被限流
        request_interval = 0.5

        while len(downloaded) < count:
            page_no += 1
            need = count - len(downloaded)
            try:
                image_urls = self._fetch_image_urls(
                    pn=pn,
                    rn=min(BAIDU_PAGE_SIZE, need),
                )
            except requests.RequestException as exc:
                print(f"[警告] 第 {page_no} 页请求失败: {exc}")
                # 单次失败后等待稍长时间再继续
                time.sleep(2)
                pn += BAIDU_PAGE_SIZE
                continue

            if not image_urls:
                print(f"[信息] 第 {page_no} 页已无更多结果，停止抓取。")
                break

            for url in image_urls:
                if len(downloaded) >= count:
                    break
                saved_path = self._download_image(url, len(downloaded) + 1)
                if saved_path:
                    downloaded.append(saved_path)

            pn += BAIDU_PAGE_SIZE
            time.sleep(request_interval)

        print(f"[完成] 共下载 {len(downloaded)} 张图片到 '{self.output_dir}'。")
        return downloaded

    # ------------------------------------------------------------------
    # 内部方法 - 抓取图片 URL
    # ------------------------------------------------------------------
    def _fetch_image_urls(self, pn: int, rn: int) -> List[str]:
        """请求百度图片搜索接口并解析图片 URL 列表。

        Args:
            pn: 起始偏移量。
            rn: 本次请求期望返回的图片数量。

        Returns:
            去重后的图片 URL 列表。
        """
        params = {
            "tn": "resultjson_com",
            "logid": str(int(time.time() * 1000)),
            "ipn": "rj",
            "ct": "201326592",
            "is": "",
            "fp": "result",
            "queryWord": self.keyword,
            "word": self.keyword,
            "cl": 2,
            "lm": -1,
            "ie": "utf-8",
            "oe": "utf-8",
            "adpicid": "",
            "st": -1,
            "z": "",
            "ic": "",
            "hd": "",
            "latest": "",
            "copyright": "",
            "s": "",
            "se": "",
            "tab": "",
            "width": "",
            "height": "",
            "face": 0,
            "istype": 2,
            "qc": "",
            "nc": 1,
            "expermode": "",
            "nojc": "",
            "isAsync": "",
            "pn": pn,
            "rn": rn,
            "gsm": hex(pn)[2:].upper() if pn > 0 else "0",
        }

        response = self._session.get(
            BAIDU_IMAGE_SEARCH_URL,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()

        # 百度接口即使 HTTP 200 也可能返回非 JSON，需要容错
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise requests.RequestException(
                f"百度接口返回非 JSON 数据: {exc}"
            ) from exc

        return self._parse_image_urls(data)

    @staticmethod
    def _parse_image_urls(payload: dict) -> List[str]:
        """从百度接口返回的 JSON 中提取图片 URL。

        Args:
            payload: 接口返回的 JSON 字典。

        Returns:
            去重且过滤后的图片 URL 列表。
        """
        seen = set()
        urls: List[str] = []

        items = payload.get("data") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            # 优先使用 objURL（原始图片），其次 middleURL，最后 thumbURL
            for key in ("objURL", "middleURL", "thumbURL", "hoverURL"):
                raw_url = item.get(key)
                if not raw_url:
                    continue
                decoded = _decode_baidu_url(raw_url)
                if decoded and decoded not in seen:
                    seen.add(decoded)
                    urls.append(decoded)
        return urls

    # ------------------------------------------------------------------
    # 内部方法 - 下载图片
    # ------------------------------------------------------------------
    def _download_image(self, url: str, index: int) -> Optional[str]:
        """下载单张图片到本地。

        Args:
            url: 图片的 URL。
            index: 当前图片序号（用于生成文件名）。

        Returns:
            保存成功的文件路径；若失败则返回 None。
        """
        ext = _guess_extension(url)
        filename = f"{self.keyword}_{index:04d}{ext}"
        filepath = os.path.join(self.output_dir, filename)

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(
                    url,
                    timeout=self.timeout,
                    stream=True,
                )
                resp.raise_for_status()

                # 校验内容类型，避开 HTML 错误页
                content_type = resp.headers.get("Content-Type", "").lower()
                if "image" not in content_type and "octet-stream" not in content_type:
                    print(f"[跳过] 非图片响应 ({content_type}): {url}")
                    return None

                with open(filepath, "wb") as fp:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            fp.write(chunk)
                return filepath
            except requests.RequestException as exc:
                print(f"[重试 {attempt}/{self.max_retries}] 下载失败: {exc}")
                time.sleep(1)
            except OSError as exc:
                print(f"[错误] 写入文件失败: {exc}")
                return None

        # 所有重试都失败
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        return None


# ----------------------------------------------------------------------
# 模块级工具函数
# ----------------------------------------------------------------------
def _decode_baidu_url(encoded_url: str) -> Optional[str]:
    """百度接口返回的 URL 会进行一次加密编码，此处尝试还原。

    部分 URL 形如 ``ippr_..._sign=...``，使用 ``urllib.parse.unquote``
    即可还原；个别 URL 解码后仍不是有效图片地址时返回 None。

    Args:
        encoded_url: 百度返回的原始 URL 字符串。

    Returns:
        解码后的 URL；若解码失败则返回 None。
    """
    if not encoded_url:
        return None
    try:
        decoded = unquote(encoded_url)
    except Exception:  # noqa: BLE001 - 兜底任意解码异常
        return None
    if decoded.startswith("http"):
        return decoded
    return None


_EXTENSION_REGEX = re.compile(
    r"\.([A-Za-z0-9]{2,5})(?:\?|$)", re.IGNORECASE
)


def _guess_extension(url: str) -> str:
    """根据 URL 猜测图片扩展名，无法判断时默认使用 .jpg。"""
    match = _EXTENSION_REGEX.search(url)
    if not match:
        return ".jpg"
    ext = "." + match.group(1).lower()
    return ext if ext in IMAGE_EXTENSIONS else ".jpg"


def batch_crawl(
    keyword: str,
    count: int,
    output_dir: str = "out",
) -> List[str]:
    """便捷函数：直接根据参数抓取图片并返回下载路径列表。

    Args:
        keyword: 搜索关键词。
        count: 下载数量。
        output_dir: 输出目录，默认为 ``out``。

    Returns:
        成功下载的图片路径列表。
    """
    crawler = BaiduImageCrawler(keyword=keyword, output_dir=output_dir)
    return crawler.crawl(count=count)


def sanitize_keyword(keyword: str) -> str:
    """清理关键词中的非法文件名字符。"""
    return re.sub(r'[\\/:*?"<>|]+', "_", keyword).strip() or "flowers"


def collect_results(paths: Iterable[str]) -> dict:
    """汇总下载结果，便于单元测试与日志记录。"""
    paths = list(paths)
    total_bytes = 0
    for path in paths:
        try:
            total_bytes += os.path.getsize(path)
        except OSError:
            pass
    return {
        "count": len(paths),
        "total_bytes": total_bytes,
        "paths": paths,
    }

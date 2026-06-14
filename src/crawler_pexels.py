# -*- coding: utf-8 -*-
"""
Pexels 图片爬虫模块
提供通过 Pexels 官方 API 搜索并下载高清图片的能力。

平台许可政策（Pexels License）:
    * 完全免费下载（Free to download）
    * 无水印（No watermark）
    * 可用于商业用途（Commercial use allowed）
    * 无需署名（No attribution required）
    详细许可条款: https://www.pexels.com/license/

主要功能:
    1. 中文关键词自动翻译为英文以适配 Pexels API
    2. 优先获取 Pexels API 返回的高分辨率原图（src.original / src.large2x）
    3. 通过 API 提供的 width/height 在下载前预过滤低分辨率资源
    4. 下载阶段复用与百度爬虫一致的质量过滤（文件大小、分辨率、Content-Length）
    5. 文件级缓存：通过 SHA256(URL) 记录已下载资源，避免重复下载
    6. 完整的错误处理：网络异常、API 限流（429）、鉴权失败（401/403）、资源过期（404）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import List, Optional, Tuple

import requests

from crawler import (
    DEFAULT_MIN_FILE_SIZE,
    DEFAULT_MIN_PIXELS,
    ImageQualityStats,
    _guess_extension,
    _read_image_dimensions,
)


# ============================================================================
# Pexels API 常量
# ============================================================================

# Pexels 官方 API v1 基础地址
PEXELS_API_BASE = "https://api.pexels.com/v1"

# Pexels API 单次请求允许的最大 per_page
PEXELS_MAX_PER_PAGE = 80

# Pexels API 默认请求超时（秒）
PEXELS_DEFAULT_TIMEOUT = 15

# Pexels API 单张图片下载的额外超时（秒）
PEXELS_IMAGE_TIMEOUT = 30

# 默认的请求头，附加 Pexels 推荐的 Authorization 字段
PEXELS_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}

# 缓存清单文件名
PEXELS_CACHE_FILENAME = ".pexels_cache.json"


# ============================================================================
# 中文 -> 英文 关键词映射
# ============================================================================

# 花卉类关键词映射表（常见中文 -> 英文）
# 设计原则:
#   - 尽量使用 Pexels 搜索返回结果数量最多的英文词
#   - 对一类花的多种中文写法尽量都覆盖（如"绣球"/"绣球花"）
#   - 加入"花"/"微距"等修饰词便于与单品种搭配
FLOWER_KEYWORD_MAP = {
    "郁金香": "tulip",
    "绣球": "hydrangea",
    "绣球花": "hydrangea",
    "八仙花": "hydrangea",
    "荷花": "lotus",
    "莲花": "lotus",
    "樱花": "cherry blossom",
    "玫瑰": "rose",
    "月季": "rose",
    "牡丹": "peony",
    "菊花": "chrysanthemum",
    "向日葵": "sunflower",
    "葵花": "sunflower",
    "百合": "lily",
    "兰花": "orchid",
    "蝴蝶兰": "orchid",
    "茉莉": "jasmine",
    "桃花": "peach blossom",
    "梅花": "plum blossom",
    "薰衣草": "lavender",
    "栀子花": "gardenia",
    "杜鹃": "azalea",
    "杜鹃花": "azalea",
    "丁香": "lilac",
    "海棠": "begonia",
    "秋海棠": "begonia",
    "茶花": "camellia",
    "山茶": "camellia",
    "康乃馨": "carnation",
    "紫罗兰": "violet",
    "风信子": "hyacinth",
    "三色堇": "pansy",
    "雏菊": "daisy",
    "洋甘菊": "chamomile",
    "罂粟": "poppy",
    "牵牛花": "morning glory",
    "波斯菊": "cosmos",
    "满天星": "baby's breath",
    "勿忘我": "forget me not",
    "木槿": "hibiscus",
    "扶桑": "hibiscus",
    "木兰花": "magnolia",
    "玉兰": "magnolia",
    "玉兰花": "magnolia",
    "玉兰树": "magnolia",
    "山茱萸": "dogwood",
    "金银花": "honeysuckle",
    "紫藤": "wisteria",
    "大丽花": "dahlia",
    "天竺葵": "geranium",
    "矮牵牛": "petunia",
    "三叶草": "clover",
    "铃兰": "lily of the valley",
    "鸢尾": "iris",
    "番红花": "saffron crocus",
    "藏红花": "saffron crocus",
    # 通用修饰词
    "微距": "macro",
    "特写": "close up",
    "近距": "close up",
    "盛开": "bloom",
    "花海": "flower field",
    "花田": "flower field",
    "花卉": "flower",
    "花朵": "flower",
    "鲜花": "fresh flower",
    "花瓣": "petal",
    "花蕊": "stamen",
    "花苞": "flower bud",
    "花枝": "flower branch",
    "花瓶": "vase",
    "插花": "flower arrangement",
    "野花": "wildflower",
    "花束": "bouquet",
    "花环": "wreath",
    "花树": "flowering tree",
}

# 颜色/场景修饰词映射：与花卉名拼接形成更精准的搜索词
# 设计原则：当与某个 FLOWER_KEYWORD_MAP 键组合时使用，本表中的 key 比
# FLOWER_KEYWORD_MAP 的 key 长度更短，因此对"粉色郁金香"等组合词也能正常
# 在第二步被匹配（先匹配"郁金香"，再对余下的"粉色"使用本表）。
MODIFIER_MAP = {
    "粉色": "pink",
    "白色": "white",
    "黄色": "yellow",
    "红色": "red",
    "紫色": "purple",
    "蓝色": "blue",
    "橙色": "orange",
    "绿色": "green",
    "黑色": "black",
    "渐变": "gradient",
    "春天": "spring",
    "夏天": "summer",
    "秋天": "autumn",
    "冬天": "winter",
    "夜景": "night",
    "雨后": "after rain",
}


# ============================================================================
# 关键词翻译
# ============================================================================

def translate_to_english(keyword: str) -> str:
    """将中文花卉关键词翻译为 Pexels API 可识别的英文关键词。

    翻译策略（按优先级）:
        1. 精确匹配 :data:`FLOWER_KEYWORD_MAP` 中的中文键；
        2. 子串匹配：对输入按"最长优先"扫描 :data:`FLOWER_KEYWORD_MAP` 中的键，
           把命中的中文片段替换为对应英文；
        3. 剩余无法识别的中文片段依次尝试 ``modifier_map`` 修饰词映射，
           最终回退到 ``pypinyin`` 拼音转换（项目已依赖该库）；
        4. 若全为 ASCII，直接返回原词。

    Args:
        keyword: 原始搜索关键词（可能为中文、英文或混合）。

    Returns:
        适合作为 Pexels API ``query`` 参数的英文关键词。
    """
    if not keyword:
        return keyword
    text = keyword.strip()
    if not text:
        return text

    # 1) 精确匹配
    if text in FLOWER_KEYWORD_MAP:
        return FLOWER_KEYWORD_MAP[text]

    # 2) 全 ASCII 直接返回
    if all(ord(c) < 128 for c in text):
        return text

    # 3) 子串匹配（按映射表键的长度从长到短排序，优先匹配更具体的词）
    sorted_keys = sorted(
        FLOWER_KEYWORD_MAP.keys(),
        key=len,
        reverse=True,
    )
    result = text
    matched_any = False
    for cn in sorted_keys:
        if cn in result:
            en = FLOWER_KEYWORD_MAP[cn]
            result = result.replace(cn, f" {en} ", 1)
            matched_any = True

    if not matched_any:
        # 4) 中文 fallback -> 拼音
        pinyin = _to_pinyin(text)
        return pinyin or text

    # 5) 处理剩余片段：对每段分别再次查 FLOWER_KEYWORD_MAP / 修饰词 / 拼音
    parts = result.split()
    final_parts: List[str] = []
    for part in parts:
        if not part:
            continue
        if all(ord(c) < 128 for c in part):
            final_parts.append(part)
            continue
        # 片段仍含中文：先尝试 modifier_map 修饰词
        translated_modifier = False
        for cn_mod, en_mod in MODIFIER_MAP.items():
            if cn_mod in part:
                part = part.replace(cn_mod, f" {en_mod} ", 1).strip()
                if all(ord(c) < 128 for c in part):
                    final_parts.append(part)
                    translated_modifier = True
                    break
        if translated_modifier:
            continue
        # 再尝试 FLOWER_KEYWORD_MAP 中是否有更长匹配（理论上不会到这里）
        if part in FLOWER_KEYWORD_MAP:
            final_parts.append(FLOWER_KEYWORD_MAP[part])
            continue
        # 最后回退到拼音
        pinyin = _to_pinyin(part)
        if pinyin:
            final_parts.append(pinyin)
        # 其余无法翻译的中文片段直接丢弃

    return " ".join(p for p in final_parts if p)


def _to_pinyin(text: str) -> str:
    """将中文文本转拼音（无音调、连续拼接）。失败时返回空串。"""
    try:
        from pypinyin import Style, lazy_pinyin  # type: ignore
    except ImportError:
        return ""
    try:
        return "".join(lazy_pinyin(text, style=Style.NORMAL)).lower()
    except Exception:  # noqa: BLE001
        return ""


# ============================================================================
# 异常定义
# ============================================================================

class PexelsAPIError(Exception):
    """Pexels API 调用过程中抛出的业务异常。

    Attributes:
        status_code: 触发异常的 HTTP 状态码（若可用）。
        retryable: 是否值得重试（4xx 一般不可重试，5xx 与网络错误通常可重试）。
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


# ============================================================================
# URL 哈希 & 缓存
# ============================================================================

def _compute_url_hash(url: str) -> str:
    """计算 URL 的 SHA256 哈希前 16 字符，用作缓存键。"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


class PexelsCache:
    """Pexels 图片下载的 URL -> 本地文件 映射缓存。

    设计要点:
        * 缓存文件以 JSON 形式保存在 ``output_dir`` 内（默认 ``.pexels_cache.json``）；
        * 缓存键为 URL 的 SHA256 哈希前 16 字符，避免直接存储长 URL；
        * 每次 ``get`` 会验证磁盘文件仍存在，防止缓存指向已删除的文件；
        * 写入采用原子模式（先写临时文件再 rename），避免崩溃时残留半截 JSON。

    该缓存机制可用于:
        1. 同一关键词的多次 ``crawl`` 避免重复下载；
        2. ``batch_crawl`` 中相同 URL 被多个关键词共享时复用文件。
    """

    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir
        self.cache_path = os.path.join(output_dir, PEXELS_CACHE_FILENAME)
        self._cache: dict = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fp:
                loaded = json.load(fp)
            if isinstance(loaded, dict):
                self._cache = loaded
        except (OSError, json.JSONDecodeError):
            # 缓存文件损坏时静默忽略，按空缓存处理
            self._cache = {}

    def save(self) -> None:
        """将缓存写回磁盘，失败时静默忽略（不阻塞主流程）。"""
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            tmp_path = self.cache_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as fp:
                json.dump(self._cache, fp, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.cache_path)
        except OSError:
            pass

    def get(self, url: str) -> Optional[str]:
        """返回 URL 对应的本地文件路径；若缓存缺失或文件已不存在则返回 ``None``。"""
        info = self._cache.get(_compute_url_hash(url))
        if not info:
            return None
        path = info.get("path")
        if path and os.path.exists(path):
            return path
        return None

    def put(self, url: str, path: str) -> None:
        """记录 URL 与本地文件路径的映射。"""
        self._cache[_compute_url_hash(url)] = {
            "url": url,
            "path": path,
            "timestamp": int(time.time()),
        }

    def clear(self) -> None:
        """清空内存缓存并删除磁盘清单文件。"""
        self._cache = {}
        try:
            if os.path.exists(self.cache_path):
                os.remove(self.cache_path)
        except OSError:
            pass

    def __contains__(self, url: str) -> bool:
        return self.get(url) is not None

    def __len__(self) -> int:
        return len(self._cache)


# ============================================================================
# Pexels 图片爬虫主类
# ============================================================================

class PexelsImageCrawler:
    """Pexels 图片爬虫封装。

    通过 Pexels 官方 REST API（v1）按关键词搜索图片，
    并按既定质量阈值下载到本地。

    Attributes:
        keyword: 原始搜索关键词（可中文）。
        translated_keyword: 翻译为英文后的关键词（用于实际 API 调用）。
        output_dir: 图片保存目录。
        timeout: 单次 HTTP 请求的超时时间（秒）。
        max_retries: 单张图片下载失败时的重试次数。
        min_file_size: 文件大小下限（字节），低于此值将被丢弃。
        min_pixels: 分辨率总像素数下限，低于此值将被丢弃。
        stats: 本次爬取的质量统计对象。
        use_cache: 是否启用 URL 缓存。
    """

    def __init__(
        self,
        keyword: str,
        output_dir: str = "out",
        api_key: Optional[str] = None,
        timeout: int = PEXELS_DEFAULT_TIMEOUT,
        max_retries: int = 3,
        min_file_size: int = DEFAULT_MIN_FILE_SIZE,
        min_pixels: int = DEFAULT_MIN_PIXELS,
        stats: Optional[ImageQualityStats] = None,
        use_cache: bool = True,
    ) -> None:
        if not keyword or not keyword.strip():
            raise ValueError("搜索关键词不能为空")
        if min_file_size < 0:
            raise ValueError("min_file_size 不能为负数")
        if min_pixels < 0:
            raise ValueError("min_pixels 不能为负数")
        if max_retries < 0:
            raise ValueError("max_retries 不能为负数")

        # API Key 优先使用显式参数，其次读取 PEXELS_API_KEY 环境变量
        resolved_key = api_key or os.environ.get("PEXELS_API_KEY", "")
        if not resolved_key:
            raise PexelsAPIError(
                "未提供 Pexels API Key。请通过 api_key 参数传入"
                "或设置环境变量 PEXELS_API_KEY。",
                status_code=None,
                retryable=False,
            )

        self.keyword = keyword.strip()
        self.translated_keyword = translate_to_english(self.keyword)
        self.output_dir = output_dir
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_file_size = min_file_size
        self.min_pixels = min_pixels
        self.stats: ImageQualityStats = stats or ImageQualityStats()
        self.use_cache = use_cache

        # Session: Authorization 必须使用 API Key
        self._session: requests.Session = requests.Session()
        self._session.headers.update(PEXELS_DEFAULT_HEADERS)
        self._session.headers["Authorization"] = resolved_key

        # 文件级缓存（按 output_dir 隔离）
        self._cache: Optional[PexelsCache] = (
            PexelsCache(output_dir) if use_cache else None
        )

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------
    def crawl(self, count: int) -> List[str]:
        """抓取指定数量的图片并返回本地路径列表。

        Args:
            count: 期望获取的图片数量（包含缓存命中与实际下载）。

        Returns:
            已落盘或命中缓存的图片路径列表；遇到不可恢复错误时返回当前进度。

        Raises:
            ValueError: ``count`` 不合法时抛出。
        """
        if count <= 0:
            raise ValueError("下载数量必须大于 0")

        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except OSError as exc:
            print(f"[错误] 创建输出目录失败 '{self.output_dir}': {exc}")
            return []

        print(
            f"[信息] Pexels 关键词: '{self.keyword}' "
            f"(翻译为: '{self.translated_keyword}')"
        )

        downloaded: List[str] = []
        page = 1
        # 为补偿过滤掉的低质量结果，单次请求数量略大于剩余所需
        per_page = min(PEXELS_MAX_PER_PAGE, max(count * 2, 20))

        # 重试循环
        while len(downloaded) < count:
            try:
                items = self._search_photos(
                    page=page,
                    per_page=per_page,
                )
            except PexelsAPIError as exc:
                if not exc.retryable:
                    print(f"[错误] Pexels API 调用失败: {exc}")
                    break
                if exc.status_code == 429:
                    # 限流已在 _search_photos 内处理退避
                    page += 1
                    continue
                print(f"[警告] Pexels API 第 {page} 页请求失败: {exc}")
                page += 1
                if page > 5:  # 防止无限重试
                    break
                time.sleep(1.5)
                continue

            if not items:
                print(f"[信息] 第 {page} 页已无更多结果，停止抓取。")
                break

            self.stats.total_candidates += len(items)

            for item in items:
                if len(downloaded) >= count:
                    break
                image_url, declared_size = self._extract_best_url(item)
                if not image_url:
                    self.stats.rejected_no_high_quality_url += 1
                    continue

                # 利用 API 返回的 width/height 在下载前过滤低分辨率
                w, h = declared_size
                if (w * h) < self.min_pixels if (w and h) else False:
                    self.stats.rejected_resolution_too_low += 1
                    continue

                saved = self._download_image(image_url, len(downloaded) + 1)
                if saved:
                    downloaded.append(saved)

            page += 1
            # 简单的请求间隔，避免触发限流
            time.sleep(0.4)

        if self._cache is not None:
            self._cache.save()

        print(f"[完成] 共下载 {len(downloaded)} 张图片到 '{self.output_dir}'。")
        if self.translated_keyword != self.keyword:
            print(
                f"[许可] Pexels License: 免费 / 无水印 / 可商用 / 无需署名 "
                f"({PEXELS_API_BASE})"
            )
        print(self.stats.quality_summary())
        return downloaded

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _search_photos(self, page: int, per_page: int) -> List[dict]:
        """调用 Pexels /v1/search 接口获取一页 photo 元数据。

        实现重试策略:
            * ``429`` 限流：按 ``Retry-After`` 头等待后重试；
            * ``401``/``403`` 鉴权失败：不重试，直接抛出 ``retryable=False``；
            * ``5xx`` 与网络异常：指数退避重试 ``max_retries`` 次。
        """
        url = f"{PEXELS_API_BASE}/search"
        params = {
            "query": self.translated_keyword,
            "per_page": per_page,
            "page": page,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(
                    url, params=params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise PexelsAPIError(
                        f"网络异常: {exc}", status_code=None, retryable=True,
                    ) from exc
                time.sleep(min(2 ** attempt, 5))
                continue

            # 限流：尊重 Retry-After 头
            if resp.status_code == 429:
                retry_after = self._parse_retry_after(resp)
                if attempt >= self.max_retries:
                    raise PexelsAPIError(
                        f"达到 Pexels API 限流上限（已重试 {attempt} 次）",
                        status_code=429,
                        retryable=True,
                    )
                print(f"[限流] 等待 {retry_after} 秒后重试...")
                time.sleep(retry_after)
                continue

            # 鉴权失败：不可重试
            if resp.status_code in (401, 403):
                raise PexelsAPIError(
                    f"Pexels API 鉴权失败 ({resp.status_code}): "
                    "请检查 PEXELS_API_KEY 是否正确。",
                    status_code=resp.status_code,
                    retryable=False,
                )

            # 资源不存在：可视为该页无结果，调用方按空结果处理
            if resp.status_code == 404:
                return []

            # 服务端错误：可重试
            if resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise PexelsAPIError(
                        f"Pexels 服务端错误: HTTP {resp.status_code}",
                        status_code=resp.status_code,
                        retryable=True,
                    )
                time.sleep(min(2 ** attempt, 5))
                continue

            # 其他 4xx：抛出但不重试（参数错误等）
            if resp.status_code >= 400:
                raise PexelsAPIError(
                    f"Pexels API 客户端错误: HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    retryable=False,
                )

            try:
                payload = resp.json()
            except ValueError as exc:
                raise PexelsAPIError(
                    f"Pexels API 返回非 JSON 数据: {exc}",
                    status_code=resp.status_code,
                    retryable=False,
                ) from exc

            photos = payload.get("photos", [])
            return [p for p in photos if isinstance(p, dict)]

        # 走到这里说明重试全部失败
        raise PexelsAPIError(
            f"Pexels API 重试 {self.max_retries} 次后仍失败: {last_exc}",
            status_code=None,
            retryable=True,
        )

    @staticmethod
    def _parse_retry_after(resp: requests.Response) -> int:
        """解析 Retry-After 头，失败时返回默认 5 秒。"""
        try:
            value = resp.headers.get("Retry-After")
            if value is None:
                return 5
            return max(1, min(int(value), 60))
        except (TypeError, ValueError):
            return 5

    @staticmethod
    def _extract_best_url(photo: dict) -> Tuple[Optional[str], Tuple[int, int]]:
        """从单个 photo 对象中提取最佳 URL 与声明分辨率。

        优先级: ``src.original`` > ``src.large2x`` > ``src.large`` > ``src.medium``。
        分辨率使用 photo 顶层的 ``width``/``height``（API 直接声明）。
        """
        src = photo.get("src") or {}
        url = (
            src.get("original")
            or src.get("large2x")
            or src.get("large")
            or src.get("medium")
        )
        width = int(photo.get("width") or 0)
        height = int(photo.get("height") or 0)
        return url, (width, height)

    def _download_image(self, url: str, index: int) -> Optional[str]:
        """下载单张图片并复用与百度爬虫一致的质量过滤逻辑。"""
        # 命中缓存：直接返回路径并累计统计
        if self._cache is not None:
            cached_path = self._cache.get(url)
            if cached_path:
                self.stats.cache_hits += 1
                # 复用时不重复计入 accepted_urls
                return cached_path

        self.stats.accepted_urls += 1
        ext = _guess_extension(url)
        filename = f"{self.keyword}_{index:04d}{ext}"
        filepath = os.path.join(self.output_dir, filename)

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(
                    url,
                    timeout=max(self.timeout, PEXELS_IMAGE_TIMEOUT),
                    stream=True,
                )
                resp.raise_for_status()

                # Content-Type 校验
                content_type = resp.headers.get("Content-Type", "").lower()
                if "image" not in content_type and "octet-stream" not in content_type:
                    self.stats.rejected_non_image += 1
                    self.stats.accepted_urls -= 1
                    return None

                # Content-Length 预检
                content_length_hdr = resp.headers.get("Content-Length")
                if content_length_hdr:
                    try:
                        content_length = int(content_length_hdr)
                        if content_length < self.min_file_size:
                            self.stats.rejected_content_length_too_small += 1
                            self.stats.accepted_urls -= 1
                            return None
                    except ValueError:
                        pass

                # 收集字节
                chunks: List[bytes] = []
                total_bytes = 0
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    total_bytes += len(chunk)

                if total_bytes < self.min_file_size:
                    self.stats.rejected_size_too_small += 1
                    self.stats.accepted_urls -= 1
                    return None

                # 分辨率校验
                head_bytes = b"".join(chunks[:1])[:4096] if chunks else b""
                dimensions = _read_image_dimensions(head_bytes)
                if dimensions is not None:
                    width, height = dimensions
                    if width <= 0 or height <= 0 or width * height < self.min_pixels:
                        self.stats.rejected_resolution_too_low += 1
                        self.stats.accepted_urls -= 1
                        return None
                    self.stats.resolutions.append(dimensions)

                # 落盘
                with open(filepath, "wb") as fp:
                    for chunk in chunks:
                        fp.write(chunk)

                # 写入缓存
                if self._cache is not None:
                    self._cache.put(url, filepath)

                self.stats.successful_downloads += 1
                self.stats.total_bytes += total_bytes
                self.stats.file_sizes.append(total_bytes)
                return filepath
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    self.stats.rejected_download_fail += 1
                    self.stats.accepted_urls -= 1
                    return None
                time.sleep(min(2 ** attempt, 5))
            except OSError:
                self.stats.rejected_download_fail += 1
                self.stats.accepted_urls -= 1
                return None

        # 循环结束仍未成功
        self.stats.rejected_download_fail += 1
        self.stats.accepted_urls -= 1
        return None

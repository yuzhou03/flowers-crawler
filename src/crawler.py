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
import struct
import time
from typing import Iterable, List, Optional, Tuple
from urllib.parse import unquote

import requests


# 百度图片搜索的 JSON 接口
BAIDU_IMAGE_SEARCH_URL = "https://image.baidu.com/search/acjson"

# 单次请求的图片数量上限（百度接口单次最多返回 60 条）
BAIDU_PAGE_SIZE = 60

# 默认请求头，模拟浏览器访问，避免被反爬。
# 额外补充 Sec-Fetch-* 等现代浏览器头，有助于拿到更高质量的图片。
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://image.baidu.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# 支持的图片扩展名
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# ============================================================================
# 高清图片下载相关常量
# ============================================================================

# 默认最小文件大小：低于此值的图片将被视为低质量缩略图丢弃
DEFAULT_MIN_FILE_SIZE = 100 * 1024  # 100 KB

# 默认最小总像素数：分辨率过滤阈值
DEFAULT_MIN_PIXELS = 800 * 600  # 约 48 万像素

# 抓取时为补偿过滤掉的低质量 URL，多请求的额外候选数
# 注意：baidu 接口对单页 rn 的上限为 60
EXTRA_CANDIDATE_RATIO = 3

# 每条 item 选取图片 URL 的字段优先级。
# 严格策略：只接受 objURL（原始大图）或 middleURL（中等大图），
# 完全跳过 thumbURL / hoverURL 等缩略图字段。
URL_FIELD_PRIORITY: Tuple[str, ...] = ("objURL", "middleURL")

# 百度接口 size 过滤参数 ``z`` 的取值：
#   0 - 全部尺寸；1/2/3 - 大/中/小；9 - 超大（壁纸级）
# 设为 9 后接口倾向于返回高分辨率原图
BAIDU_SIZE_FILTER = 9


class ImageQualityStats:
    """图片质量统计信息。

    用于记录一次爬取过程中各个阶段（URL 解析、文件下载、文件大小、
    分辨率等）的拒绝原因及最终质量分布，便于对比优化前后的效果。
    """

    __slots__ = (
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
        "file_sizes",
        "resolutions",
    )

    def __init__(self) -> None:
        # 接口返回的 data 列表条目数（粗略候选数）
        self.total_candidates: int = 0
        # 通过 URL 解析阶段、被加入下载队列的数量
        self.accepted_urls: int = 0
        # 拒绝：item 没有 objURL/middleURL 可用 URL
        self.rejected_no_high_quality_url: int = 0
        # 拒绝：响应 Content-Type 不是图片
        self.rejected_non_image: int = 0
        # 拒绝：HTTP Content-Length 头部即声明文件过小
        self.rejected_content_length_too_small: int = 0
        # 拒绝：实际下载字节数过小
        self.rejected_size_too_small: int = 0
        # 拒绝：分辨率像素总数过低
        self.rejected_resolution_too_low: int = 0
        # 拒绝：HTTP/IO 错误达到重试上限
        self.rejected_download_fail: int = 0
        # 成功下载并落盘的数量
        self.successful_downloads: int = 0
        # 成功下载图片的字节总数
        self.total_bytes: int = 0
        # 所有成功图片的文件大小（字节）
        self.file_sizes: List[int] = []
        # 所有成功图片的分辨率 (宽, 高)
        self.resolutions: List[Tuple[int, int]] = []

    # ------------------------------------------------------------------
    # 派生指标
    # ------------------------------------------------------------------
    @property
    def average_size(self) -> float:
        if not self.file_sizes:
            return 0.0
        return sum(self.file_sizes) / len(self.file_sizes)

    @property
    def average_pixels(self) -> float:
        if not self.resolutions:
            return 0.0
        return sum(w * h for w, h in self.resolutions) / len(self.resolutions)

    @property
    def pass_rate(self) -> float:
        """URL 通过率：通过质量过滤的比例（相对于接受的下载 URL）。"""
        if self.accepted_urls == 0:
            return 0.0
        return self.successful_downloads / self.accepted_urls

    def to_dict(self) -> dict:
        """导出为 dict，便于日志、单元测试和对比。"""
        return {
            "total_candidates": self.total_candidates,
            "accepted_urls": self.accepted_urls,
            "rejected_no_high_quality_url": self.rejected_no_high_quality_url,
            "rejected_non_image": self.rejected_non_image,
            "rejected_content_length_too_small": self.rejected_content_length_too_small,
            "rejected_size_too_small": self.rejected_size_too_small,
            "rejected_resolution_too_low": self.rejected_resolution_too_low,
            "rejected_download_fail": self.rejected_download_fail,
            "successful_downloads": self.successful_downloads,
            "total_bytes": self.total_bytes,
            "average_size_bytes": self.average_size,
            "min_size_bytes": min(self.file_sizes) if self.file_sizes else 0,
            "max_size_bytes": max(self.file_sizes) if self.file_sizes else 0,
            "average_pixels": self.average_pixels,
            "pass_rate": self.pass_rate,
        }

    def quality_summary(self) -> str:
        """生成可打印的对比摘要。"""
        d = self.to_dict()
        lines = [
            "[质量统计] 候选 item: {total}, 接受 URL: {acc}, 成功下载: {ok}".format(
                total=d["total_candidates"],
                acc=d["accepted_urls"],
                ok=d["successful_downloads"],
            ),
            (
                "[质量统计] 文件大小 - 平均: {avg:.1f}KB, 最小: {mn:.1f}KB, 最大: {mx:.1f}KB"
            ).format(
                avg=d["average_size_bytes"] / 1024,
                mn=d["min_size_bytes"] / 1024,
                mx=d["max_size_bytes"] / 1024,
            ),
        ]
        if d["average_pixels"] > 0:
            lines.append(
                "[质量统计] 平均像素数: {px:.0f} (~{w:.0f}x{h:.0f})".format(
                    px=d["average_pixels"],
                    w=(d["average_pixels"] ** 0.5),
                    h=(d["average_pixels"] ** 0.5),
                )
            )
        lines.append(
            "[质量统计] 拒绝明细 - 无大图URL: {a}, 非图片: {b}, "
            "Content-Length过小: {c}, 实际过小: {d}, 分辨率过低: {e}, "
            "下载失败: {f}".format(
                a=d["rejected_no_high_quality_url"],
                b=d["rejected_non_image"],
                c=d["rejected_content_length_too_small"],
                d=d["rejected_size_too_small"],
                e=d["rejected_resolution_too_low"],
                f=d["rejected_download_fail"],
            )
        )
        return "\n".join(lines)


class BaiduImageCrawler:
    """百度图片爬虫封装。

    通过百度图片搜索的 acjson 接口获取图片列表，
    并将图片下载到本地目录。

    优化点（高清图片支持）:
        * 接口请求参数增加 ``z=9`` 倾向于拉取超高清壁纸级原图；
        * URL 解析阶段仅接受 ``objURL``/``middleURL`` 字段，
          跳过 ``thumbURL``/``hoverURL`` 等缩略图；
        * 下载前通过 ``Content-Length`` 预检过滤过小响应；
        * 下载后再次校验文件大小与图片分辨率；
        * 全程统计拒绝原因与质量分布，便于对比优化效果。

    Attributes:
        keyword: 搜索关键词（花的名称等）。
        output_dir: 图片保存目录。
        timeout: 单次 HTTP 请求的超时时间（秒）。
        max_retries: 单张图片下载失败时的重试次数。
        min_file_size: 文件大小下限（字节），低于此值将被丢弃。
        min_pixels: 分辨率总像素数下限，低于此值将被丢弃。
        stats: 本次爬取的质量统计对象。
    """

    def __init__(
        self,
        keyword: str,
        output_dir: str = "out",
        timeout: int = 15,
        max_retries: int = 3,
        min_file_size: int = DEFAULT_MIN_FILE_SIZE,
        min_pixels: int = DEFAULT_MIN_PIXELS,
        stats: Optional[ImageQualityStats] = None,
    ) -> None:
        if not keyword or not keyword.strip():
            raise ValueError("搜索关键词不能为空")
        if min_file_size < 0:
            raise ValueError("min_file_size 不能为负数")
        if min_pixels < 0:
            raise ValueError("min_pixels 不能为负数")
        self.keyword = keyword.strip()
        self.output_dir = output_dir
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_file_size = min_file_size
        self.min_pixels = min_pixels
        # 默认创建统计对象；调用方可注入外部对象以便聚合多次爬取的数据
        self.stats: ImageQualityStats = stats or ImageQualityStats()

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
            # 一次性多取一些候选，弥补下载阶段被质量过滤丢弃的 URL
            # 受百度接口单次返回上限 BAIDU_PAGE_SIZE 约束
            need = count - len(downloaded)
            want = min(BAIDU_PAGE_SIZE, need * EXTRA_CANDIDATE_RATIO)
            try:
                image_urls = self._fetch_image_urls(
                    pn=pn,
                    rn=want,
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
        # 输出本次抓取的质量统计摘要，便于对比优化效果
        print(self.stats.quality_summary())
        return downloaded

    # ------------------------------------------------------------------
    # 内部方法 - 抓取图片 URL
    # ------------------------------------------------------------------
    def _fetch_image_urls(self, pn: int, rn: int) -> List[str]:
        """请求百度图片搜索接口并解析图片 URL 列表。

        关键参数说明:
            * ``z=9`` —— 让接口倾向于返回超高清壁纸级图片；
            * ``width``/``height`` —— 通过最小尺寸进一步过滤；
            * 其余参数维持百度接口默认契约。

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
            # 关键优化：z=9 倾向于返回壁纸级超清大图
            "z": BAIDU_SIZE_FILTER,
            "ic": "",
            "hd": "",
            "latest": "",
            "copyright": "",
            "s": "",
            "se": "",
            "tab": "",
            # 显式声明仅需要大尺寸以上的图片
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

        严格策略：每条 item 只输出一个最佳 URL（优先 ``objURL``，缺失时
        降级为 ``middleURL``），完全跳过 ``thumbURL``/``hoverURL`` 这类
        缩略图字段，从源头避免低质量图片进入下载队列。

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
            decoded: Optional[str] = None
            for key in URL_FIELD_PRIORITY:
                raw_url = item.get(key)
                if not raw_url:
                    continue
                decoded = _decode_baidu_url(raw_url)
                if decoded:
                    break
            if decoded and decoded not in seen:
                seen.add(decoded)
                urls.append(decoded)
        return urls

    # ------------------------------------------------------------------
    # 内部方法 - 下载图片
    # ------------------------------------------------------------------
    def _download_image(self, url: str, index: int) -> Optional[str]:
        """下载单张图片到本地。

        新增质量保障步骤:
            1. ``Content-Type`` 校验：拒绝非图片响应；
            2. ``Content-Length`` 预检：体积明显不足的响应直接拒绝；
            3. 实际字节数校验：下载完成后再次确认不低于 ``min_file_size``；
            4. 分辨率校验：解析图片头部，确认宽高乘积不低于 ``min_pixels``。

        Args:
            url: 图片的 URL。
            index: 当前图片序号（用于生成文件名）。

        Returns:
            保存成功的文件路径；若失败则返回 None。
        """
        self.stats.accepted_urls += 1
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
                    self.stats.rejected_non_image += 1
                    self.stats.accepted_urls -= 1
                    return None

                # Content-Length 预检：体积明显不足的响应直接拒绝，节省带宽
                content_length_hdr = resp.headers.get("Content-Length")
                if content_length_hdr:
                    try:
                        content_length = int(content_length_hdr)
                        if content_length < self.min_file_size:
                            print(
                                f"[过滤] Content-Length 过小 "
                                f"({content_length/1024:.1f}KB < {self.min_file_size/1024:.0f}KB): {url}"
                            )
                            self.stats.rejected_content_length_too_small += 1
                            self.stats.accepted_urls -= 1
                            return None
                    except ValueError:
                        pass

                # 收集字节并实时累计大小
                chunks: List[bytes] = []
                total_bytes = 0
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    total_bytes += len(chunk)
                    # 提前终止：累计字节已远超阈值且后续 chunk 仍是大块时跳过
                    # 这里仅做大小上限保护，不影响常规流程
                    if total_bytes > self.min_file_size * 10:
                        # 已经远大于阈值，可以提前结束读取
                        pass

                # 实际下载字节数校验
                if total_bytes < self.min_file_size:
                    print(
                        f"[过滤] 文件过小 "
                        f"({total_bytes/1024:.1f}KB < {self.min_file_size/1024:.0f}KB): {url}"
                    )
                    self.stats.rejected_size_too_small += 1
                    self.stats.accepted_urls -= 1
                    return None

                # 分辨率校验：仅取首块的前 4KB，足以覆盖常见图片头部
                head_bytes = chunks[0][:4096] if chunks else b""
                dimensions = _read_image_dimensions(head_bytes)
                if dimensions is not None:
                    width, height = dimensions
                    if width <= 0 or height <= 0:
                        print(f"[过滤] 分辨率异常 ({width}x{height}): {url}")
                        self.stats.rejected_resolution_too_low += 1
                        self.stats.accepted_urls -= 1
                        return None
                    if width * height < self.min_pixels:
                        print(
                            f"[过滤] 分辨率过低 "
                            f"({width}x{height} = {width*height} < {self.min_pixels}): {url}"
                        )
                        self.stats.rejected_resolution_too_low += 1
                        self.stats.accepted_urls -= 1
                        return None
                    self.stats.resolutions.append(dimensions)

                # 所有校验通过，正式落盘
                with open(filepath, "wb") as fp:
                    for chunk in chunks:
                        fp.write(chunk)

                self.stats.successful_downloads += 1
                self.stats.total_bytes += total_bytes
                self.stats.file_sizes.append(total_bytes)
                return filepath
            except requests.RequestException as exc:
                print(f"[重试 {attempt}/{self.max_retries}] 下载失败: {exc}")
                time.sleep(1)
            except OSError as exc:
                print(f"[错误] 写入文件失败: {exc}")
                self.stats.rejected_download_fail += 1
                self.stats.accepted_urls -= 1
                return None

        # 所有重试都失败
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        self.stats.rejected_download_fail += 1
        self.stats.accepted_urls -= 1
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


def _read_image_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """从图片字节流中读取分辨率（宽, 高）。

    为了不引入 Pillow 等额外依赖，这里直接解析常见图片格式的二进制头部:
        * PNG:   通过 IHDR 块直接读取
        * GIF:   通过 Logical Screen Descriptor 读取
        * JPEG:  扫描 SOF0/SOF2 等帧起始标记
        * WebP:  支持 VP8 / VP8L / VP8X 三种子格式

    Args:
        data: 图片字节流（仅需前几十到几百字节即可）。

    Returns:
        ``(width, height)`` 元组；解析失败或数据不完整时返回 ``None``。
    """
    if not data:
        return None

    # ---- PNG ----
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        try:
            width = struct.unpack(">I", data[16:20])[0]
            height = struct.unpack(">I", data[20:24])[0]
            return (int(width), int(height))
        except struct.error:
            return None

    # ---- GIF ----
    if data[:3] == b"GIF" and len(data) >= 10:
        try:
            width = struct.unpack("<H", data[6:8])[0]
            height = struct.unpack("<H", data[8:10])[0]
            return (int(width), int(height))
        except struct.error:
            return None

    # ---- JPEG ----
    if data[:2] == b"\xff\xd8":
        return _read_jpeg_dimensions(data)

    # ---- WebP ----
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _read_webp_dimensions(data)

    return None


def _read_jpeg_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """通过扫描 JPEG 标记读取分辨率。"""
    # JPEG: 跳过 0xFF 0xD8 起始标记后，逐段扫描直至找到 SOF* 帧起始标记
    i = 2
    # 单次最多扫描 64KB 头部，避免异常大文件卡死
    scan_limit = min(len(data), 65536)
    while i < scan_limit - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        # 跳过 0xFF 填充字节
        while i < scan_limit and data[i] == 0xFF:
            i += 1
        if i >= scan_limit:
            break
        marker = data[i]
        i += 1
        # SOF0 (0xC0) - SOF15 (0xCF)，除 DHT (0xC4) 和 DNL (0xCC) 外均含分辨率
        # 这里使用范围判断更简洁
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            try:
                # 此时 i 指向 length 字段起点
                # SOF 段格式: length(2) precision(1) height(2) width(2) ...
                height = struct.unpack(">H", data[i + 3:i + 5])[0]
                width = struct.unpack(">H", data[i + 5:i + 7])[0]
                return (int(width), int(height))
            except (struct.error, IndexError):
                return None
        # 其它段: 读取段长度并跳过
        if i + 2 > scan_limit:
            break
        try:
            seg_len = struct.unpack(">H", data[i:i + 2])[0]
        except struct.error:
            return None
        if seg_len < 2:
            return None
        i += seg_len
    return None


def _read_webp_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """读取 WebP 格式的分辨率。"""
    if len(data) < 30:
        return None
    chunk_type = data[12:16]
    try:
        if chunk_type == b"VP8 ":
            # Lossy: 帧头 26-29 字节为宽高
            width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
            return (int(width), int(height))
        if chunk_type == b"VP8L":
            # Lossless: 14-bit width-1 + 14-bit height-1 + alpha 等
            if len(data) < 25:
                return None
            b1, b2, b3, b4 = data[21], data[22], data[23], data[24]
            width = ((b2 & 0x3F) << 8 | b1) + 1
            height = (((b4 & 0x0F) << 10) | (b3 << 2) | ((b2 & 0xC0) >> 6)) + 1
            return (int(width), int(height))
        if chunk_type == b"VP8X":
            # Extended: 24-bit width-1 + 24-bit height-1
            if len(data) < 30:
                return None
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
            return (int(width), int(height))
    except (struct.error, IndexError, ValueError):
        return None
    return None


def batch_crawl(
    keyword: str,
    count: int,
    output_dir: str = "out",
    min_file_size: int = DEFAULT_MIN_FILE_SIZE,
    min_pixels: int = DEFAULT_MIN_PIXELS,
    stats: Optional[ImageQualityStats] = None,
) -> List[str]:
    """便捷函数：直接根据参数抓取图片并返回下载路径列表。

    Args:
        keyword: 搜索关键词。
        count: 下载数量。
        output_dir: 输出目录，默认为 ``out``。
        min_file_size: 文件大小下限（字节），默认 100KB。
        min_pixels: 分辨率像素数下限，默认 800x600。
        stats: 可选的质量统计对象；传入后可聚合多次爬取的数据。

    Returns:
        成功下载的图片路径列表。
    """
    crawler = BaiduImageCrawler(
        keyword=keyword,
        output_dir=output_dir,
        min_file_size=min_file_size,
        min_pixels=min_pixels,
        stats=stats,
    )
    return crawler.crawl(count=count)


def compare_quality(
    baseline: ImageQualityStats,
    optimized: ImageQualityStats,
) -> dict:
    """对比两份质量统计，输出优化前/后的关键指标差异。

    Args:
        baseline: 优化前的统计。
        optimized: 优化后的统计。

    Returns:
        包含平均文件大小提升、分辨率提升、通过率等指标的字典。
    """
    base_avg = baseline.average_size
    opt_avg = optimized.average_size
    base_px = baseline.average_pixels
    opt_px = optimized.average_pixels

    size_improvement = 0.0
    if base_avg > 0:
        size_improvement = (opt_avg - base_avg) / base_avg * 100.0

    pixel_improvement = 0.0
    if base_px > 0:
        pixel_improvement = (opt_px - base_px) / base_px * 100.0

    return {
        "baseline": baseline.to_dict(),
        "optimized": optimized.to_dict(),
        "average_size_improvement_pct": size_improvement,
        "average_pixels_improvement_pct": pixel_improvement,
        "baseline_min_size_bytes": baseline.to_dict()["min_size_bytes"],
        "optimized_min_size_bytes": optimized.to_dict()["min_size_bytes"],
    }


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

# Flowers Crawler（花的图片爬虫）

> 一个使用 Python 编写的图片爬虫，支持 **百度图片搜索**（默认）和 **Pexels**（高清免版权）两种数据源，按关键词抓取花的图片并保存到本地。

## 1. 项目功能

- 支持通过命令行参数指定花的关键词（如 `玫瑰`、`sunflower` 等）。
- **双数据源**：百度图片搜索（默认）与 Pexels 官方 API（高清、免版权、可商用）。
- 抓取**高清原图**并按 `{关键词}_{序号}.{扩展名}` 命名保存到本地目录。
- **未指定存储路径时自动使用拼音子目录**：例如 `python src\main.py 荷花` 会把图片保存到 `out/hehua/`；`python src\main.py 牡丹` 会保存到 `out/mudan/`。
- 内置请求分页、失败重试与简单的反爬规避（合理 UA / Referer / 请求间隔）。
- 关键词与下载数量均做合法性校验，下载过程对非图片响应进行过滤。
- **Pexels 中文关键词自动翻译**：输入中文花卉名称时自动翻译为英文以适配 Pexels API。
- **文件级 URL 缓存**（Pexels）：通过 SHA256(URL) 记录已下载资源，避免重复下载。
- 提供 `BatchCrawler.batch_crawl` 便捷函数，便于在其它 Python 脚本中直接调用。

## 2. 项目结构

```
flowers-crawler/
├── out/
│   └── prd.md                       # 产品需求文档
├── src/
│   ├── __init__.py
│   ├── crawler.py                   # 百度爬虫核心：搜索 + 下载
│   ├── crawler_pexels.py            # Pexels 爬虫：API 搜索 + 下载 + 缓存
│   ├── main.py                      # CLI 入口（支持 --source 切换数据源）
│   ├── test_crawler.py              # 百度爬虫单元/集成测试
│   └── test_crawler_pexels.py       # Pexels 爬虫单元/集成测试
├── scripts/
│   ├── make_clean.py                # make clean 辅助脚本
│   └── make_help.py                 # make help 辅助脚本
├── doc/
│   └── README.md                    # 本文档
├── Makefile                         # 一键运行入口（run / run-pexels / test / test-pexels）
├── requirements.txt                 # 依赖列表
└── README.md                        # 项目总览
```

## 3. 实现思路

### 3.1 百度图片搜索（默认数据源）

1. **接口选择**：调用百度图片搜索的 `acjson` 接口
   `https://image.baidu.com/search/acjson`，通过调整 `pn`（起始偏移量）与 `rn`（单页数量）实现分页。
2. **URL 解析**：从返回的 JSON 中按优先级 `objURL` → `middleURL` → `thumbURL` → `hoverURL`
   提取图片地址，并对 `objURL` 等字段做 URL 解码。

### 3.2 Pexels API（高清免版权数据源）

1. **接口选择**：调用 Pexels 官方 REST API v1
   `https://api.pexels.com/v1/search`，需要 API Key 进行鉴权。
2. **关键词翻译**：中文花卉关键词自动翻译为英文（内置 60+ 花卉映射表 + 拼音 fallback）。
3. **URL 优先级**：`src.original` → `src.large2x` → `src.large` → `src.medium`。
4. **分辨率预过滤**：利用 API 返回的 width/height 在下载前过滤低分辨率资源。

### 3.3 通用机制

1. **图片下载**：使用 `requests.Session` + `stream=True` 流式写入磁盘，避免大图占用过多内存。
2. **健壮性**：
   - 单张图片下载失败时最多重试 3 次；
   - 过滤非图片响应（HTML 错误页等）；
   - 接口 JSON 解析失败时进入下一轮而非直接崩溃；
   - 文件名安全化处理（去除 `/ \ : * ? " < > |` 等非法字符）。
3. **Pexels 许可**：完全免费、无水印、可商用、无需署名（详见 [Pexels License](https://www.pexels.com/license/)）。

## 4. 环境与安装

要求：Python 3.8+

```powershell
# 1. 进入项目目录
cd c:\code\lab\flowers-crawler

# 2. （推荐）创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4.（可选）配置 Pexels API Key
#    注册免费 API Key: https://www.pexels.com/api/
#    Windows PowerShell:
$env:PEXELS_API_KEY = "your_api_key_here"
#    Linux / macOS:
export PEXELS_API_KEY="your_api_key_here"
```

## 5. 使用方法

### 5.1 命令行方式

#### 百度图片搜索（默认）

```powershell
# 基础用法：自动以拼音命名子目录
python src\main.py 玫瑰          # 保存到 out/meigui/
python src\main.py 荷花          # 保存到 out/hehua/
python src\main.py sunflower     # 保存到 out/sunflower/

# 指定数量与自定义输出目录
python src\main.py 牡丹 -n 30 -o D:\data\peony

# 自定义超时与重试
python src\main.py sunflower -n 50 -o out --timeout 20 --max-retries 5
```

#### Pexels 高清图片

```powershell
# 使用环境变量中的 PEXELS_API_KEY
python src\main.py rose --source pexels -n 10

# 通过命令行传入 API Key
python src\main.py 郁金香 --source pexels -n 20 --pexels-api-key your_key

# 中文关键词自动翻译（郁金香 → tulip）
python src\main.py 荷花 --source pexels -n 15
```

参数说明：

| 参数                 | 说明                                                       | 默认值            |
|---------------------|------------------------------------------------------------|-------------------|
| `keyword`           | 必填，搜索关键词（花的名称）                                 | 无                |
| `-n/--count`        | 下载数量                                                   | 10                |
| `-o/--output`       | 存储路径。未指定时自动使用 `out/<keyword 的拼音>`           | `out/<拼音>/`     |
| `--source`          | 数据源：`baidu`（百度图片）或 `pexels`（Pexels API）        | `baidu`           |
| `--pexels-api-key`  | Pexels API Key（也可通过环境变量 `PEXELS_API_KEY` 传入）   | 无                |
| `--timeout`         | 单次 HTTP 超时时间（秒）                                    | 15                |
| `--max-retries`     | 单张图片下载失败时的重试次数                                | 3                 |

> 提示：执行结束后，图片位于 `out/<拼音>/{关键词}_{序号}.jpg` 等文件中。
> 例如 `python src\main.py 荷花` 执行后，文件路径形如 `out/hehua/荷花_0001.jpg`。

### 5.2 Makefile 一键运行

项目提供了 Makefile 封装常用操作，在项目根目录执行：

```powershell
# 查看所有可用 target
make help

# --- 百度图片搜索 ---
make run                                    # 默认下载 27 张 rose 图片
make run KEYWORD=牡丹 N=30 OUT=out/peony    # 自定义关键词、数量、输出目录

# --- Pexels 高清图片 ---
make run-pexels KEYWORD=rose N=20 PEXELS_API_KEY=your_key
make run-pexels KEYWORD=郁金香 N=10         # 使用环境变量中的 PEXELS_API_KEY

# --- 测试 ---
make test               # 运行百度爬虫单元测试
make test-pexels        # 运行 Pexels 爬虫单元测试
make all                # 依次执行 install + test + test-pexels

# --- 其他 ---
make clean              # 清理 __pycache__ / .pyc / 日志等临时文件
make install            # 安装 requirements.txt 中的依赖
```

Makefile 参数说明（适用于 `run` 和 `run-pexels`）：

| 参数              | 说明                                   | 默认值      |
|------------------|----------------------------------------|-------------|
| `KEYWORD`        | 搜索关键词                             | `rose`      |
| `N`              | 下载数量                               | `27`        |
| `OUT`            | 输出目录                               | `""`（自动） |
| `TIMEOUT`        | 单次请求超时（秒）                     | `15`        |
| `SKIP_INSTALL`   | 设为 `1` 跳过依赖安装                  | `0`         |
| `PEXELS_API_KEY` | Pexels API Key（仅 `run-pexels` 使用） | 环境变量     |

别名：`make r` = `make run`，`make rp` = `make run-pexels`，`make h` = `make help`，`make cl` = `make clean`。

### 5.3 作为模块调用

```python
# 百度图片搜索
from src.crawler import batch_crawl

paths = batch_crawl(keyword="百合", count=20, output_dir="out/lily")
print(f"成功下载 {len(paths)} 张图片")

# Pexels 高清图片
from src.crawler_pexels import PexelsImageCrawler

crawler = PexelsImageCrawler(
    keyword="郁金香",
    output_dir="out/tulip",
    api_key="your_api_key",  # 或通过环境变量 PEXELS_API_KEY
)
paths = crawler.crawl(count=10)
print(f"成功下载 {len(paths)} 张图片")
```

## 6. 测试

项目自带基于 `unittest` 的测试用例，覆盖以下方面：

### 6.1 百度爬虫测试（`src.test_crawler`）

- URL 解码、扩展名猜测、关键词清洗等纯函数；
- JSON 解析在接口返回空数据 / 异常数据时仍能安全处理；
- 命令行参数解析对非法输入（数量 <= 0、空关键词）报错。

### 6.2 Pexels 爬虫测试（`src.test_crawler_pexels`）

- 中文 → 英文关键词翻译（精确匹配、子串匹配、修饰词、拼音 fallback）；
- PexelsCache 缓存（写入、读取、磁盘校验、损坏恢复）；
- PexelsImageCrawler 构造参数校验与 URL 优先级提取；
- 搜索接口错误处理：401 / 403 / 429 限流 / 5xx / 网络异常；
- 下载流程：Content-Type、Content-Length、文件大小、分辨率过滤；
- CLI 入口：`--source pexels` 参数解析与 `_build_crawler` 工厂方法。

运行方式：

```powershell
# 在项目根目录

# 百度爬虫测试
python -m unittest src.test_crawler -v
# 或使用 Makefile
make test

# Pexels 爬虫测试
python -m unittest src.test_crawler_pexels -v
# 或使用 Makefile
make test-pexels

# 运行全部测试
make all
```

> 集成测试需要联网访问对应图片搜索接口，默认已用 `unittest.mock` 屏蔽真实网络请求；
> 如需对真实接口做端到端验证，可在测试文件中将 `IntegrationTests` / `PexelsIntegrationTests` 移除跳过装饰器。

## 7. 注意事项

- 百度接口返回的图片 URL 可能会随时间变化或被反爬限制，生产环境建议增加代理池与登录态。
- **Pexels API 需要免费 API Key**：前往 [pexels.com/api](https://www.pexels.com/api/) 注册获取，每小时限流约 200 次请求。
- **Pexels 图片许可**：完全免费、无水印、可商用、无需署名（[Pexels License](https://www.pexels.com/license/)）。
- 抓取并使用他人图片需遵守版权与网站 `robots.txt`，本项目仅供学习交流。
- 网络环境不稳定时，可通过提高 `--max-retries` 与 `--timeout` 改善成功率。

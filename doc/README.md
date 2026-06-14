# Flowers Crawler（花的图片爬虫）

> 一个使用 Python 编写的简易图片爬虫，基于百度图片搜索接口，按关键词抓取花的图片并保存到本地。

## 1. 项目功能

- 支持通过命令行参数指定花的关键词（如 `玫瑰`、`sunflower` 等）。
- 抓取**高清原图**并按 `{关键词}_{序号}.{扩展名}` 命名保存到本地目录。
- 内置请求分页、失败重试与简单的反爬规避（合理 UA / Referer / 请求间隔）。
- 关键词与下载数量均做合法性校验，下载过程对非图片响应进行过滤。
- 提供 `BatchCrawler.batch_crawl` 便捷函数，便于在其它 Python 脚本中直接调用。

## 2. 项目结构

```
flowers-crawler/
├── out/
│   └── prd.md                 # 产品需求文档
├── src/
│   ├── __init__.py
│   ├── crawler.py             # 爬虫核心：搜索 + 下载
│   ├── main.py                # CLI 入口
│   └── test_crawler.py        # 单元/集成测试
├── doc/
│   └── README.md              # 本文档
├── requirements.txt           # 依赖列表
└── README.md                  # 项目总览（如需可另写）
```

## 3. 实现思路

1. **接口选择**：调用百度图片搜索的 `acjson` 接口
   `https://image.baidu.com/search/acjson`，通过调整 `pn`（起始偏移量）与 `rn`（单页数量）实现分页。
2. **URL 解析**：从返回的 JSON 中按优先级 `objURL` → `middleURL` → `thumbURL` → `hoverURL`
   提取图片地址，并对 `objURL` 等字段做 URL 解码。
3. **图片下载**：使用 `requests.Session` + `stream=True` 流式写入磁盘，避免大图占用过多内存。
4. **健壮性**：
   - 单张图片下载失败时最多重试 3 次；
   - 过滤非图片响应（HTML 错误页等）；
   - 接口 JSON 解析失败时进入下一轮而非直接崩溃；
   - 文件名安全化处理（去除 `/ \ : * ? " < > |` 等非法字符）。

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
```

## 5. 使用方法

### 5.1 命令行方式

```powershell
# 基础用法：在 out/ 目录下抓取 10 张玫瑰图片
python src\main.py 玫瑰

# 指定数量与输出目录
python src\main.py 牡丹 -n 30 -o D:\data\peony

# 自定义超时与重试
python src\main.py sunflower -n 50 -o out --timeout 20 --max-retries 5
```

参数说明：

| 参数            | 说明                              | 默认值 |
|----------------|-----------------------------------|--------|
| `keyword`      | 必填，搜索关键词（花的名称）       | 无     |
| `-n/--count`   | 下载数量                           | 10     |
| `-o/--output`  | 存储路径                           | `out/` |
| `--timeout`    | 单次 HTTP 超时时间（秒）           | 15     |
| `--max-retries`| 单张图片下载失败时的重试次数       | 3      |

> 提示：执行结束后，图片位于 `out/{关键词}_{序号}.jpg` 等文件中。

### 5.2 作为模块调用

```python
from src.crawler import batch_crawl

paths = batch_crawl(keyword="百合", count=20, output_dir="out/lily")
print(f"成功下载 {len(paths)} 张图片")
```

## 6. 测试

项目自带基于 `unittest` 的测试用例，覆盖以下方面：

- URL 解码、扩展名猜测、关键词清洗等纯函数；
- JSON 解析在接口返回空数据 / 异常数据时仍能安全处理；
- 命令行参数解析对非法输入（数量 <= 0、空关键词）报错。

运行方式：

```powershell
# 在项目根目录
python -m unittest src.test_crawler -v
```

> 集成测试需要联网访问百度图片搜索接口，默认已用 `unittest.mock` 屏蔽真实网络请求；
> 如需对真实接口做端到端验证，可在测试文件中将 `IntegrationTests` 移除跳过装饰器。

## 7. 注意事项

- 百度接口返回的图片 URL 可能会随时间变化或被反爬限制，生产环境建议增加代理池与登录态。
- 抓取并使用他人图片需遵守版权与网站 `robots.txt`，本项目仅供学习交流。
- 网络环境不稳定时，可通过提高 `--max-retries` 与 `--timeout` 改善成功率。

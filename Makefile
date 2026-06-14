# ============================================================================
#  Flowers Crawler - Makefile
# ----------------------------------------------------------------------------
#  为项目提供一键运行的常用入口：run / clean / help，以及辅助的 install/test。
#  使用方法（在项目根目录执行）:
#      make help            # 查看所有可用 target
#      make run             # 安装依赖并下载 10 张 rose 图片到 out/
#      make run KEYWORD=牡丹 N=20
#      make test            # 运行单元 + 集成测试
#      make clean           # 清理临时文件、缓存与日志
#      make -j4 run         # 并行执行（target 之间无强依赖）
# ============================================================================

# ---------- 变量定义 ----------
PYTHON        ?= python
PIP           ?= $(PYTHON) -m pip
SRC_DIR       := src
OUTPUT_DIR    := out
DOC_DIR       := doc
TEST_TARGET   := $(SRC_DIR).test_crawler
REQUIREMENTS  := requirements.txt

# ---------- 默认目标 ----------
.DEFAULT_GOAL := help

# ---------- PHONY 声明 ----------
# 标记为 PHONY 的 target 总是会被执行（忽略同名文件存在与否）
.PHONY: help run clean install test all

# ---------- help ----------
# 解析本文件中 "## 描述" 形式的注释并以表格形式输出，便于扩展。
# 使用 Python 实现，确保在 Windows / macOS / Linux 行为一致（无需 awk）。
help:  ## 展示所有可用 target 的功能说明、使用方法与参数
	@echo Flowers Crawler - 可用命令:
	@echo.
	@$(PYTHON) scripts/make_help.py $(MAKEFILE_LIST)
	@echo.
	@echo 常用参数（仅 run target 生效）:
	@echo   KEYWORD=^<词^>      要搜索的花的关键词（默认: rose）
	@echo   N=^<数量^>          要下载的图片数量（默认: 10）
	@echo   OUT=^<路径^>        图片输出目录（默认: out/）
	@echo   SKIP_INSTALL=1      跳过依赖安装步骤
	@echo.
	@echo 示例:
	@echo   make run
	@echo   make run KEYWORD=牡丹 N=30 OUT=out/peony
	@echo   make run KEYWORD=sunflower N=50 SKIP_INSTALL=1
	@echo   make -j4 run test   并行执行多个 target

# ---------- run ----------
# 一键运行爬虫：先确保依赖已安装，然后调用 src/main.py。
# 通过命令行参数自定义关键词 / 数量 / 输出路径等。
KEYWORD      ?= rose
N            ?= 10
OUT          ?= $(OUTPUT_DIR)
SKIP_INSTALL ?= 0
TIMEOUT      ?= 15

run:  ## 一键运行爬虫（自动安装依赖，可由 KEYWORD/N/OUT 控制）
	@echo "[run] 关键词=$(KEYWORD) 数量=$(N) 输出=$(OUT)"
ifeq ($(SKIP_INSTALL),0)
	@echo "[run] 正在安装/更新依赖 ..."
	$(PIP) install -r $(REQUIREMENTS)
endif
	$(PYTHON) $(SRC_DIR)/main.py "$(KEYWORD)" -n $(N) -o "$(OUT)" --timeout $(TIMEOUT)

# ---------- clean ----------
# 彻底清理项目构建/运行过程中产生的临时文件、缓存与日志。
# 使用 Python 脚本实现，确保在 Windows / macOS / Linux 行为一致。
clean:  ## 清理 __pycache__ / .pyc / 日志 / 覆盖率 / 构建产物
	@echo [clean] 正在清理临时文件与编译产物 ...
	$(PYTHON) scripts/make_clean.py .
	@echo [clean] 如需清空已下载图片，可手动执行: rd /s /q out\_images  或  rm -rf out/*

# ---------- install ----------
install:  ## 安装 requirements.txt 中声明的依赖
	$(PIP) install -r $(REQUIREMENTS)

# ---------- test ----------
test:  ## 运行单元测试与集成测试（集成测试默认 skip）
	$(PYTHON) -m unittest $(TEST_TARGET) -v

# ---------- all ----------
# 等价于 install + test，可与其他 target 并行调度。
all: install test  ## 依次执行 install 和 test


# alias
h: help
cl: clean
r: run

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
OUTPUT_DIR    := ""
DOC_DIR       := doc
TEST_TARGET   := $(SRC_DIR).test_crawler
TEST_TARGET_PEXELS := $(SRC_DIR).test_crawler_pexels
REQUIREMENTS  := requirements.txt

# ---------- .env 自动加载 ----------
# 使用 GNU make 的 -include 指令解析项目根 .env 内的 ``key=value`` 行。
# 优点：无需 shell 兼容层（awk/python），跨 Windows / macOS / Linux 行为一致。
# 行为：
#   * .env 不存在时静默忽略（- 而非 include）；
#   * 命令行 ``make PEXELS_API_KEY=xxx`` 优先级最高（GNU make 默认行为）；
#   * shell 环境变量 export PEXELS_API_KEY=xxx 也会被 make 继承；
#   * .env 中既支持 PEXELS_API_KEY=xxx 也支持小写 pexels_api_key=xxx。
-include .env
# 当上述三处都未提供时，兜底使用 .env 中的小写 ``pexels_api_key`` 字段。
# ?= 保证：只要 PEXELS_API_KEY 已经被设置（任意来源），就不再覆盖。
PEXELS_API_KEY ?= $(pexels_api_key)

# Windows 终端默认使用 cp936 (GBK) 代码页，UTF-8 编码的中文 @echo
# 会出现 "鍛戒护" 这类乱码。切换到 chcp 65001 (UTF-8) 即可解决。
# 在 macOS / Linux 上该变量为冒号（shell 内建 no-op），行为不变。
ifeq ($(OS),Windows_NT)
    UTF8_SWITCH := chcp 65001 >nul 2>&1
else
    UTF8_SWITCH := :
endif

# ---------- 默认目标 ----------
.DEFAULT_GOAL := help

# ---------- PHONY 声明 ----------
# 标记为 PHONY 的 target 总是会被执行（忽略同名文件存在与否）
.PHONY: help run run-pexels pexels-env-check clean install test test-pexels all

# ---------- help ----------
# 解析本文件中 "## 描述" 形式的注释并以表格形式输出，便于扩展。
# 使用 Python 实现，确保在 Windows / macOS / Linux 行为一致（无需 awk）。
help:  ## 展示所有可用 target 的功能说明、使用方法与参数
	@$(UTF8_SWITCH)
	@echo Flowers Crawler - 可用命令:
	@echo.
	@$(PYTHON) scripts/make_help.py $(MAKEFILE_LIST)
	@echo.
	@echo 常用参数（仅 run / run-pexels target 生效）:
	@echo   KEYWORD=^<词^>      要搜索的花的关键词（默认: rose）
	@echo   N=^<数量^>          要下载的图片数量（默认: 10）
	@echo   OUT=^<路径^>        图片输出目录（默认: out/）
	@echo   SKIP_INSTALL=1      跳过依赖安装步骤
	@echo.
	@echo Pexels API Key 优先级（从高到低）:
	@echo   1) make 命令行:  make run-pexels PEXELS_API_KEY=xxx
	@echo   2) shell 导出:  export PEXELS_API_KEY=xxx ^&^& make run-pexels
	@echo   3) 项目根 .env: PEXELS_API_KEY=xxx  或  pexels_api_key=xxx
	@echo.
	@echo 示例:
	@echo   make run
	@echo   make run KEYWORD=牡丹 N=30 OUT=out/peony
	@echo   make run KEYWORD=sunflower N=50 SKIP_INSTALL=1
	@echo   make run-pexels KEYWORD=rose N=20
	@echo   make -j4 run test   并行执行多个 target

# ---------- run ----------
# 一键运行爬虫：先确保依赖已安装，然后调用 src/main.py。
# 通过命令行参数自定义关键词 / 数量 / 输出路径等。
KEYWORD      ?= rose
N            ?= 27
OUT          ?= $(OUTPUT_DIR)
SKIP_INSTALL ?= 0
TIMEOUT      ?= 15

run:  ## 一键运行爬虫（自动安装依赖，可由 KEYWORD/N/OUT 控制）
	@$(UTF8_SWITCH)
	@echo "[run] 关键词=$(KEYWORD) 数量=$(N) 输出=$(OUT)"
ifeq ($(SKIP_INSTALL),0)
	@echo "[run] 正在安装/更新依赖 ..."
	$(PIP) install -r $(REQUIREMENTS)
endif
	$(PYTHON) $(SRC_DIR)/main.py "$(KEYWORD)" -n $(N) -o "$(OUT)" --timeout $(TIMEOUT)

# ---------- run-pexels ----------
# 一键运行 Pexels 爬虫：先确保依赖已安装，然后调用 src/main.py --source pexels。
# PEXELS_API_KEY 来源（按优先级）:
#   1) make 命令行:   make run-pexels PEXELS_API_KEY=xxx
#   2) shell 环境变量:  export PEXELS_API_KEY=xxx
#   3) 项目根 .env:   PEXELS_API_KEY=xxx  或  pexels_api_key=xxx
# 由 pexels-env-check 校验 key 是否存在且不是占位符。
run-pexels: pexels-env-check  ## 一键运行 Pexels 爬虫（自动从 .env / env / 命令行加载 API Key）
	@$(UTF8_SWITCH)
	@echo "[run-pexels] 关键词=$(KEYWORD) 数量=$(N) 输出=$(OUT)"
ifeq ($(SKIP_INSTALL),0)
	@echo "[run-pexels] 正在安装/更新依赖 ..."
	$(PIP) install -r $(REQUIREMENTS)
endif
ifneq ($(strip $(PEXELS_API_KEY)),)
	$(PYTHON) $(SRC_DIR)/main.py "$(KEYWORD)" -n $(N) -o "$(OUT)" --timeout $(TIMEOUT) --source pexels --pexels-api-key "$(PEXELS_API_KEY)"
else
	# 防御性兜底：正常情况下 pexels-env-check 已确保 PEXELS_API_KEY 非空
	$(PYTHON) $(SRC_DIR)/main.py "$(KEYWORD)" -n $(N) -o "$(OUT)" --timeout $(TIMEOUT) --source pexels
endif

# ---------- pexels-env-check ----------
# 校验 Pexels API Key 是否已配置且不是占位符。run-pexels 隐式依赖此 target。
# 使用 Python 脚本以保证在 Windows / macOS / Linux 行为一致（含 chcp 65001 之外的环境）。
pexels-env-check:  ## 校验 Pexels API Key 是否就绪（自动从 .env / env / 命令行加载）
	@$(UTF8_SWITCH)
	@$(PYTHON) scripts/make_pexels_env.py

# ---------- clean ----------
# 彻底清理项目构建/运行过程中产生的临时文件、缓存与日志。
# 使用 Python 脚本实现，确保在 Windows / macOS / Linux 行为一致。
clean:  ## 清理 __pycache__ / .pyc / 日志 / 覆盖率 / 构建产物
	@$(UTF8_SWITCH)
	@echo [clean] 正在清理临时文件与编译产物 ...
	$(PYTHON) scripts/make_clean.py .
	@echo [clean] 如需清空已下载图片，可手动执行: rd /s /q out\_images  或  rm -rf out/*

# ---------- install ----------
install:  ## 安装 requirements.txt 中声明的依赖
	$(PIP) install -r $(REQUIREMENTS)

# ---------- test ----------
test:  ## 运行单元测试与集成测试（集成测试默认 skip）
	$(PYTHON) -m unittest $(TEST_TARGET) -v

# ---------- test-pexels ----------
test-pexels:  ## 运行 Pexels 爬虫单元测试与集成测试（集成测试默认 skip）
	$(PYTHON) -m unittest $(TEST_TARGET_PEXELS) -v

# ---------- all ----------
# 等价于 install + test，可与其他 target 并行调度。
all: install test test-pexels  ## 依次执行 install、test 和 test-pexels


# alias
h: help
cl: clean
r: run
rp: run-pexels
ec: pexels-env-check

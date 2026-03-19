# =============================================================================
# Makefile — Omniscientist Claw Docker 管理
#
# 用法（在项目根目录执行）：
#   make -C claw up              # 完整 Docker（Gateway + Agent）
#   make -C claw up-gateway      # 仅启动 Gateway（连接宿主机 Python 后端）
#   make -C claw up-agent        # 仅启动 Python 后端（omni-research）
#   make -C claw up-all          # 宿主机 Python 后端 + Gateway Docker（混合模式）
#   make -C claw up-fresh        # 重建镜像并重启（代码更新后使用）
#   make -C claw down            # 停止所有容器
#   make -C claw rebuild         # 重新构建后端镜像
#   make -C claw pull            # 更新 OpenClaw Gateway 镜像
#   make -C claw logs            # 查看所有日志（跟随）
#   make -C claw logs-gw         # 查看 gateway 日志
#   make -C claw logs-agent      # 查看 research 后端日志
#   make -C claw shell-gw        # 进入 gateway 容器
#   make -C claw shell-agent     # 进入 research 容器
#   make -C claw shell-oc        # 进入 OpenClaw Gateway 容器（同 shell-gw）
#   make -C claw backend-start   # 宿主机后台启动 Python agent
#   make -C claw backend-stop    # 停止宿主机 Python agent
#   make -C claw status          # 查看容器状态
#   --- 飞书配对 & 设备管理 ---
#   make -C claw approve-device  # 批准 Control UI 配对
#   make -C claw feishu-pair CODE=xxx  # 批准指定配对码
#   make -C claw feishu-pair-list      # 查看已配对设备列表
#   make -C claw reset-sessions        # 清除所有会话历史（保留配置）
#   make -C claw reset-session ID=xxx  # 清除指定 session 历史
#   --- 维护 ---
#   make -C claw clean-logs      # 清理 30 天前日志
#   make -C claw clean-logs DAYS=7  # 清理 7 天前日志
#   make -C claw doctor          # 安全执行 openclaw doctor --fix（自动校正飞书策略）
#   make -C claw fix-config      # 仅校验/修复 openclaw.json 飞书策略（不重启）
#
# 环境变量通过 ../.env.omniscientist 加载（项目根目录）
# =============================================================================

# 切到项目根目录（Makefile 所在的上一层）
ROOT     := $(realpath ..)
ENV_FILE := $(ROOT)/.env.omniscientist
COMPOSE_FULL  := docker compose -f $(CURDIR)/docker-compose.yml
COMPOSE_GW    := docker compose -f $(CURDIR)/docker-compose.gateway.yml
COMPOSE_AGENT := docker compose -f $(CURDIR)/docker-compose.agent.yml

# OpenClaw Gateway 容器名与 Token（与 docker-compose.yml 中 OPENCLAW_GATEWAY_TOKEN 一致）
GW_CONTAINER := omni-gateway
GW_TOKEN     := tony_research
SESSIONS_DIR := $(CURDIR)/config/agents/main/sessions
LOGS_DIR     := $(ROOT)/data/logs
DAYS         ?= 30

# 加载 .env.omniscientist（若存在）
ifneq (,$(wildcard $(ENV_FILE)))
  include $(ENV_FILE)
  export $(shell sed 's/=.*//' $(ENV_FILE) 2>/dev/null)
endif

.PHONY: up up-fresh up-gateway up-agent up-all down rebuild pull \
        logs logs-gw logs-agent shell-gw shell-agent shell-oc \
        backend-start backend-stop status ps \
        approve-device feishu-pair feishu-pair-list \
        reset-sessions reset-session clean-logs \
        doctor fix-config \
        _kill_local_backend

## 完整 Docker 栈（Gateway + Agent）— 始终重建 omni-research 镜像以确保代码最新
up: _kill_local_backend
	@echo "▶ 启动/重启完整 Docker 栈 (Gateway + Agent)..."
	DOCKER_BUILDKIT=1 $(COMPOSE_FULL) --env-file $(ENV_FILE) up -d --force-recreate --build
	@echo "✓ 完整栈已启动 | Gateway: http://localhost:10100 | Agent: http://127.0.0.1:10101"

## 仅启动 Gateway（宿主机 Python 后端）
up-gateway:
	@echo "▶ 仅启动 omni-gateway (连接宿主机后端)..."
	$(COMPOSE_GW) --env-file $(ENV_FILE) up -d
	@echo "✓ Gateway 已启动 | http://localhost:10100"

## 仅启动 Python 后端（同样重建镜像确保代码最新）
up-agent: _kill_local_backend
	@echo "▶ 仅启动 Python 后端 (omni-research)..."
	DOCKER_BUILDKIT=1 $(COMPOSE_AGENT) --env-file $(ENV_FILE) up -d --build
	@echo "✓ 后端已启动 | Agent: http://127.0.0.1:10101"

## 混合模式：宿主机 Python 后端 + Gateway Docker
up-all:
	@echo "▶ 混合模式：启动宿主机 Python 后端 + Gateway Docker..."
	$(MAKE) backend-start
	@sleep 5
	$(MAKE) up-gateway
	@echo "✓ 混合模式已启动"

## 停止所有容器（全栈 + gateway + agent）
down:
	@echo "▶ 停止所有容器..."
	-$(COMPOSE_FULL) down 2>/dev/null || true
	-$(COMPOSE_GW) down 2>/dev/null || true
	-$(COMPOSE_AGENT) down 2>/dev/null || true
	@echo "✓ 所有容器已停止"

## 重建后端镜像（不启动，可提前缓存镜像）
rebuild:
	@echo "▶ 重新构建 omni-research 镜像..."
	DOCKER_BUILDKIT=1 $(COMPOSE_FULL) --env-file $(ENV_FILE) build omni-research
	@echo "✓ 镜像构建完成，执行 make up 重启"

## 强制无缓存重建并重启（依赖更新后使用，比 up 更彻底）
up-fresh: _kill_local_backend
	@echo "▶ 无缓存重建镜像并重启全栈..."
	DOCKER_BUILDKIT=1 $(COMPOSE_FULL) --env-file $(ENV_FILE) up -d --force-recreate --build --no-deps
	@echo "✓ 完整栈已重建并启动"

## 日志：全部（跟随）
logs:
	@docker compose -f $(CURDIR)/docker-compose.yml logs -f 2>/dev/null || \
	 docker compose -f $(CURDIR)/docker-compose.gateway.yml logs -f 2>/dev/null || \
	 docker compose -f $(CURDIR)/docker-compose.agent.yml logs -f

## 日志：仅 Gateway
logs-gw:
	docker logs -f omni-gateway

## 日志：仅 Research 后端
logs-agent:
	docker logs -f omni-research

## 进入 Gateway 容器
shell-gw:
	docker exec -it omni-gateway sh

## 进入 Research 容器
shell-agent:
	docker exec -it omni-research bash

## 宿主机后台启动 Python Agent
backend-start:
	@echo "▶ 宿主机后台启动 Python Agent..."
	cd $(ROOT) && bash start.sh start -d
	@echo "✓ Python Agent 已在后台运行 | 日志: $(ROOT)/data/logs/"

## 停止宿主机 Python Agent
backend-stop:
	@echo "▶ 停止宿主机 Python Agent..."
	cd $(ROOT) && bash start.sh stop 2>/dev/null || true
	@echo "✓ Python Agent 已停止"

## 查看容器状态
status ps:
	@echo "=== Docker 容器状态 ==="
	@docker ps --filter "name=omni-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "(无运行中的 omni-* 容器)"
	@echo ""
	@echo "=== 宿主机 Python Agent ==="
	@cd $(ROOT) && bash start.sh status 2>/dev/null || echo "(无宿主机 agent)"

# =============================================================================
# 飞书配对 & 设备管理
# =============================================================================

## 批准 Control UI / 浏览器扩展 配对请求（报 "pairing required" 时使用）
approve-device:
	@echo "▶ 批准待配对设备..."
	@out=$$(docker exec $(GW_CONTAINER) sh -c \
	  'OPENCLAW_GATEWAY_TOKEN=$(GW_TOKEN) node dist/index.js devices approve --token $(GW_TOKEN) 2>&1' || true); \
	if echo "$$out" | grep -qiE 'approved|success|已批准'; then \
	  echo "✓ 配对已批准，请刷新 Control UI 重试连接"; \
	elif echo "$$out" | grep -qiE 'no pending|nothing to approve'; then \
	  echo "  (当前无待批准请求)"; \
	  echo "  若仍报 pairing required，请直接访问：http://localhost:10100/#token=$(GW_TOKEN)"; \
	else \
	  echo "  输出: $$out"; \
	fi

## 批准飞书或 Control UI 指定配对码（CODE=xxx 必填）
## 用法：make -C claw feishu-pair CODE=123456
feishu-pair:
	@if [ -z "$(CODE)" ]; then \
	  echo "用法: make -C claw feishu-pair CODE=<配对码>"; \
	  echo "提示: 配对码显示在飞书报错消息中（如：Pairing code: KDYN4GRT）"; \
	  exit 1; \
	fi
	@echo "▶ 批准飞书配对码 $(CODE)..."
	@docker exec $(GW_CONTAINER) node dist/index.js pairing approve feishu $(CODE) 2>&1 | grep -v "^\[" || true
	@echo "✓ 完成，用户可重新在飞书发消息"

## 查看已配对设备列表
feishu-pair-list:
	@echo "▶ 已配对设备列表："
	@docker exec $(GW_CONTAINER) sh -c \
	  'OPENCLAW_GATEWAY_TOKEN=$(GW_TOKEN) node dist/index.js devices list --token $(GW_TOKEN) 2>&1' \
	  | grep -v '^\[' | grep -v '^◇\|^│\|^├\|^└' || true

# =============================================================================
# 会话管理
# =============================================================================

## 清除所有 Agent 会话历史（保留 sessions.json 配置，只清空 .jsonl 对话记录）
reset-sessions:
	@echo "▶ 清除所有会话历史..."
	@count=0; \
	for f in $(SESSIONS_DIR)/*.jsonl; do \
	  [ -f "$$f" ] && head -2 "$$f" > /tmp/_sess_tmp && mv /tmp/_sess_tmp "$$f" && count=$$((count+1)); \
	done; \
	echo "✓ 已清除 $$count 个会话的历史记录（配置保留）"
	@docker exec $(GW_CONTAINER) sh -c \
	  'OPENCLAW_GATEWAY_TOKEN=$(GW_TOKEN) node dist/index.js agent --reset 2>&1' >/dev/null 2>&1 || true

## 清除指定 session 历史（ID=<session-uuid> 必填）
## 用法：make -C claw reset-session ID=a2171337-fe98-4929-847b-c737de41b50c
reset-session:
	@if [ -z "$(ID)" ]; then \
	  echo "用法: make -C claw reset-session ID=<session-uuid>"; \
	  echo "可用 session："; \
	  ls $(SESSIONS_DIR)/*.jsonl 2>/dev/null | xargs -I{} basename {} .jsonl; \
	  exit 1; \
	fi
	@f="$(SESSIONS_DIR)/$(ID).jsonl"; \
	if [ ! -f "$$f" ]; then \
	  echo "❌ 未找到 session: $$f"; exit 1; \
	fi; \
	head -2 "$$f" > /tmp/_sess_tmp && mv /tmp/_sess_tmp "$$f"; \
	echo "✓ 已清除 session $(ID) 的历史记录"

# =============================================================================
# 维护工具
# =============================================================================

## 安全执行 openclaw doctor --fix 并自动修正飞书策略配置
## 替代直接在容器内执行 openclaw doctor --fix（后者会破坏 dmPolicy 配置）
doctor: fix-config
	@echo "▶ 在容器内运行 openclaw doctor --fix..."
	@docker exec $(GW_CONTAINER) openclaw doctor --fix 2>&1 | grep -v "^\[plugins\]" | grep -v "^\[" || true
	@echo "▶ 应用 doctor 后校验飞书策略配置..."
	@$(MAKE) fix-config
	@echo "▶ 重启 Gateway 使配置生效..."
	@docker restart $(GW_CONTAINER) >/dev/null
	@echo "✓ doctor 已安全执行，飞书策略已校验，Gateway 已重启"

## 校验并修复 openclaw.json 中的飞书策略配置
## 确保 dmPolicy=open / allowFrom=["*"] 始终在 accounts.main 中
fix-config:
	@echo "▶ 校验 openclaw.json 飞书策略..."
	@python3 $(CURDIR)/scripts/fix-openclaw-config.py $(CURDIR)/config/openclaw.json

## 更新 OpenClaw Gateway 镜像并重启
pull:
	@echo "▶ 拉取最新 OpenClaw Gateway 镜像..."
	docker pull ghcr.io/openclaw/openclaw:latest
	@echo "▶ 重启 Gateway 使新镜像生效..."
	$(COMPOSE_FULL) --env-file $(ENV_FILE) up -d --force-recreate omni-gateway
	@echo "✓ Gateway 已更新并重启"

## 清理旧日志文件（默认 30 天前，可用 DAYS=N 自定义）
## 用法：make -C claw clean-logs  或  make -C claw clean-logs DAYS=7
clean-logs:
	@echo "▶ 清理 $(DAYS) 天前的日志文件..."
	@count=0; \
	for f in $$(find $(LOGS_DIR) -maxdepth 1 -name "*.log" -mtime +$(DAYS) 2>/dev/null); do \
	  rm -f "$$f" && echo "  删除: $$(basename $$f)" && count=$$((count+1)); \
	done; \
	if [ "$$count" -eq 0 ]; then \
	  echo "  (无超过 $(DAYS) 天的日志文件)"; \
	else \
	  echo "✓ 共删除 $$count 个日志文件"; \
	fi

## 进入 OpenClaw Gateway 容器（shell-gw 的别名）
shell-oc:
	docker exec -it $(GW_CONTAINER) sh

# =============================================================================
# 内部目标（不对外展示）
# =============================================================================

## 停止由 start.sh 启动的本地 Python 研究后端（通过 PID 文件精确定位）
## 不使用 lsof 暴力扫端口，避免误杀 Docker 端口转发或其他合法进程
_kill_local_backend:
	@pid_file="$(ROOT)/data/pids/openclaw_research.web.pid"; \
	if [ -f "$$pid_file" ]; then \
	  local_pid=$$(cat "$$pid_file" 2>/dev/null || true); \
	  if [ -n "$$local_pid" ] && kill -0 "$$local_pid" 2>/dev/null; then \
	    echo "  ⚠ 检测到宿主机研究后端仍在运行 (PID $$local_pid)，停止中..."; \
	    kill "$$local_pid" 2>/dev/null || true; \
	    sleep 1; \
	    kill -0 "$$local_pid" 2>/dev/null && kill -9 "$$local_pid" 2>/dev/null || true; \
	    echo "  ✓ 宿主机研究后端已停止"; \
	  fi; \
	  rm -f "$$pid_file"; \
	fi

#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
#  Omniscientist Claw — 统一服务管理脚本
#
#  ⚠️  本系统完全独立运行，不依赖任何其他 openclaw 实例：
#      - 本系统有自己专属的 Docker OpenClaw（omni-gateway，port 10100）
#      - 与宿主机上任何其他 openclaw 实例均无关联
#
#  端口规划（严格在 10100-10199 范围内）：
#      10100  专属 OpenClaw Gateway Web UI（飞书入口）
#      10101  Python Research API + Web 控制台（主入口）
#      10101  Research API（容器模式，127.0.0.1）
#      10109  OpenClaw WebSocket Bridge
#
#  支持两种独立部署模式（可单独使用，也可组合）：
#
#    模式 A — 完整 Docker 部署（推荐生产）
#      omni-gateway + omni-research 全部在容器内运行
#      Research 后端通过 Docker 内网 http://omni-research:10101 访问
#      命令：./start.sh docker-up
#
#    模式 B — Research 宿主机 + Gateway Docker
#      Python 研究服务以 conda 方式运行在宿主机（端口 10101）
#      omni-gateway 仍在 Docker 内运行
#      research.sh 通过 RESEARCH_URL=http://host.docker.internal:10101 访问宿主机
#      命令：./start.sh start -d               # 先启动宿主机 Python 服务
#             ./start.sh docker-gateway-up     # 再启动 Docker Gateway 套件
#
#  两种模式下，日志目录（data/logs/）、MySQL、Redis 配置保持完全一致。
#
#  本地模式命令（Python Research API，端口 10101）：
#    start [-d] [port]       前台/后台启动本地 Python 服务
#    stop                    停止本地后台服务
#    restart [-d] [port]     重启本地服务
#    status                  查看本地服务状态
#    logs [模块]             查看日志（app/feishu/worker/evolution/error）
#    clean-logs [天数]       手动清理历史日志（默认 30 天）
#
#  Docker 模式命令：
#    docker-up               启动完整 Docker 套件（模式 A）
#    docker-gateway-up       仅启动 Gateway（模式 B，配合宿主机 Python）
#    docker-down             停止全部 Docker 服务
#    docker-restart          重启 Docker 套件
#    docker-status           查看 Docker 服务状态
#    docker-logs [服务]      查看 Docker 日志
#    docker-build            重建镜像并启动
#
#  通用命令：
#    status-all              同时显示本地 + Docker 状态
#    cli                     CLI 交互模式（本地）
#    help                    显示帮助信息
#
# ═══════════════════════════════════════════════════════════════════════

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── 常量 ────────────────────────────────────────────────────────────────
INSTANCE_NAME="openclaw_research"
CONDA_ENV="claw"

# PID / 日志统一放在项目 data/ 目录下（与 Python 日志保持一致）
PID_DIR="$SCRIPT_DIR/data/pids"
LOG_DIR="$SCRIPT_DIR/data/logs"
PID_FILE="$PID_DIR/${INSTANCE_NAME}.web.pid"

# Docker Compose 文件（legacy 兼容 + claw/ 新结构）
DOCKER_COMPOSE_FILE="$SCRIPT_DIR/docker-compose.omniscientist.yml"
CSI_DIR="$SCRIPT_DIR/claw"
CSI_COMPOSE_FULL="$CSI_DIR/docker-compose.yml"
CSI_COMPOSE_GW="$CSI_DIR/docker-compose.gateway.yml"
CSI_COMPOSE_AGENT="$CSI_DIR/docker-compose.agent.yml"
ENV_FILE="$SCRIPT_DIR/.env.omniscientist"
DOCKER_PROJECT="omniscientist"

mkdir -p "$PID_DIR" "$LOG_DIR"

# ── 颜色 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'

log_info()  { echo -e "  ${GREEN}✓${NC} $*"; }
log_warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; }
log_error() { echo -e "  ${RED}✗${NC} $*" >&2; }
log_step()  { echo -e "  ${CYAN}▸${NC} $*"; }
log_docker(){ echo -e "  ${BLUE}🐳${NC} $*"; }

print_banner() {
    echo -e "${RED}"
    cat << 'EOF'
   ___  __  __ _   _ ___ ____   ____ ___ _____ _   _ _____ ___ ____ _____
  / _ \|  \/  | \ | |_ _/ ___| / ___|_ _| ____| \ | |_   _|_ _/ ___|_   _|
 | | | | |\/| |  \| || |\___ \| |   | ||  _| |  \| | | |  | |\___ \ | |
 | |_| | |  | | |\  || | ___) | |___| || |___| |\  | | |  | | ___) || |
  \___/|_|  |_|_| \_|___|____/ \____|___|_____|_| \_| |_| |___|____/ |_|
EOF
    echo -e "${NC}"
    echo -e "  ${CYAN}🦞 科研专属智能体系统 v3.0${NC}  ${DIM}(三层架构: 专属 OpenClaw + LeadResearcher + Worker Pool)${NC}"
    echo ""
}

# ── conda Python 路径 ────────────────────────────────────────────────────
get_python_bin() {
    for conda_base in \
        "$HOME/opt/miniconda3" \
        "$HOME/miniconda3" \
        "$HOME/anaconda3" \
        "/opt/homebrew/Caskroom/miniconda/base" \
        "/opt/miniconda3" \
        "/opt/anaconda3"
    do
        if [ -f "${conda_base}/envs/${CONDA_ENV}/bin/python" ]; then
            echo "${conda_base}/envs/${CONDA_ENV}/bin/python"
            return 0
        fi
    done
    conda run -n "$CONDA_ENV" python -c "import sys; print(sys.executable)" 2>/dev/null || true
}

# ── 环境检查 ─────────────────────────────────────────────────────────────
check_conda() {
    if ! conda run -n "$CONDA_ENV" python --version &>/dev/null 2>&1; then
        log_error "未找到 conda 环境 '$CONDA_ENV'，请先初始化："
        echo "    conda create -n $CONDA_ENV python=3.11 -y"
        echo "    conda run -n $CONDA_ENV pip install -r requirements.txt"
        exit 1
    fi
}

check_env() {
    if [ ! -f ".env" ]; then
        log_warn "未找到 .env 文件，从示例创建..."
        cp .env.example .env 2>/dev/null || true
        log_warn "请编辑 .env 配置 API Key，然后重新运行"
    fi
}

check_docker() {
    if ! command -v docker &>/dev/null; then
        log_error "未找到 docker 命令，请先安装 Docker Desktop"
        exit 1
    fi
    if ! docker info &>/dev/null 2>&1; then
        log_error "Docker 未运行，请先启动 Docker Desktop"
        exit 1
    fi
    if [ ! -f "$DOCKER_COMPOSE_FILE" ]; then
        log_error "未找到 Docker Compose 文件: $DOCKER_COMPOSE_FILE"
        exit 1
    fi
}

# ── PID 管理 ─────────────────────────────────────────────────────────────
read_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid="$(cat "$PID_FILE" 2>/dev/null)"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"; return 0
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}
is_running() { read_pid >/dev/null 2>&1; }

# ═══════════════════════════════════════════════════════════════════════
#  本地模式：start
# ═══════════════════════════════════════════════════════════════════════
cmd_start() {
    local daemon=false
    local PORT="10101"
    for arg in "$@"; do
        case "$arg" in
            -d|--daemon|--background) daemon=true ;;
            [0-9]*) PORT="$arg" ;;
        esac
    done

    print_banner
    check_conda
    check_env

    if is_running; then
        local pid; pid="$(read_pid)"
        log_warn "本地服务已运行 (PID $pid) → http://localhost:$PORT"
        log_warn "使用 '$0 stop' 先停止，或 '$0 restart' 重启"
        exit 0
    fi

    # 清理占用端口的旧进程
    local PIDS
    PIDS=$(lsof -ti :"$PORT" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        log_warn "端口 $PORT 被占用 (PIDs: $PIDS)，正在清理..."
        echo "$PIDS" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi

    echo -e "  ${CYAN}Web 界面:${NC} http://localhost:$PORT"
    echo -e "  ${CYAN}API 文档:${NC} http://localhost:$PORT/docs"
    echo -e "  ${CYAN}日志目录:${NC} $LOG_DIR"
    echo ""

    if $daemon; then
        log_step "后台（daemon）模式启动..."
        local PYTHON_BIN
        PYTHON_BIN="$(get_python_bin)"
        if [ -z "$PYTHON_BIN" ]; then
            log_error "无法定位 conda 环境 '$CONDA_ENV' 的 Python 路径"
            exit 1
        fi
        # 日志写入 data/logs/app.log（Python 应用内自动滚动）
        # start.sh 启动日志写 data/logs/startup.log
        nohup "$PYTHON_BIN" -m uvicorn api.main:app \
            --host 0.0.0.0 --port "$PORT" \
            >> "$LOG_DIR/startup.log" 2>&1 &
        local PID=$!
        disown $PID 2>/dev/null || true
        echo "$PID" > "$PID_FILE"
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            log_info "服务已后台启动 (PID $PID)"
            log_info "主日志:   $LOG_DIR/app.log"
            log_info "飞书日志: $LOG_DIR/feishu.log"
            log_info "错误日志: $LOG_DIR/error.log"
            log_info "停止服务: $0 stop"
            log_info "查看日志: $0 logs"
        else
            log_error "后台启动失败，查看: $LOG_DIR/startup.log"
            rm -f "$PID_FILE"
            exit 1
        fi
    else
        log_step "前台模式启动（Ctrl+C 退出）..."
        echo ""
        conda run --no-capture-output -n "$CONDA_ENV" \
            uvicorn api.main:app --host 0.0.0.0 --port "$PORT" --reload
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  本地模式：stop
# ═══════════════════════════════════════════════════════════════════════
cmd_stop() {
    echo ""
    local pid
    if pid="$(read_pid 2>/dev/null)"; then
        log_step "正在停止本地服务 (PID $pid)..."
        kill "$pid" 2>/dev/null || true
        local count=0
        while kill -0 "$pid" 2>/dev/null && [ $count -lt 8 ]; do
            sleep 1; count=$((count+1))
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
            log_warn "已强制终止 (PID $pid)"
        else
            log_info "服务已停止 (PID $pid)"
        fi
        rm -f "$PID_FILE"
    else
        log_warn "未检测到运行中的本地服务"
    fi
    # 清理端口占用
    local port_pid
    port_pid="$(lsof -ti :10101 2>/dev/null || true)"
    if [ -n "$port_pid" ]; then
        echo "$port_pid" | xargs kill -9 2>/dev/null || true
        log_info "端口 10101 已释放"
    fi
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════
#  本地模式：status
# ═══════════════════════════════════════════════════════════════════════
cmd_status() {
    echo ""
    echo -e "  ${BOLD}━━━ 本地 Python 服务 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    local pid
    if pid="$(read_pid 2>/dev/null)"; then
        local mem
        mem="$(ps -o rss= -p "$pid" 2>/dev/null | awk '{printf "%.0fMB", $1/1024}' || echo "?")"
        echo -e "  ${GREEN}●${NC} Python 服务  ${DIM}pid=$pid  mem=$mem${NC}"
        local health
        health="$(curl -s --max-time 3 "http://localhost:10101/health" 2>/dev/null || echo "")"
        if [ -n "$health" ]; then
            echo -e "  ${GREEN}●${NC} HTTP 健康    ${DIM}OK → http://localhost:10101${NC}"
            # 显示关键指标
            echo -e "  ${DIM}$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  workers={d.get(\"workers\",\"?\")} redis={d.get(\"redis\",\"?\")} instance={d.get(\"instance\",\"?\")}')  " 2>/dev/null || true)${NC}"
        else
            echo -e "  ${YELLOW}●${NC} HTTP 健康    ${DIM}未响应（服务可能仍在初始化）${NC}"
        fi
    else
        echo -e "  ${RED}●${NC} Python 服务  ${DIM}未运行${NC}"
    fi

    echo ""
    echo -e "  ${BOLD}━━━ 日志文件状态 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    for log_name in app feishu worker evolution error; do
        local log_file="$LOG_DIR/${log_name}.log"
        if [ -f "$log_file" ]; then
            local size
            size=$(du -sh "$log_file" 2>/dev/null | cut -f1)
            local mtime
            mtime=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$log_file" 2>/dev/null \
                    || stat -c "%y" "$log_file" 2>/dev/null | cut -d. -f1 || echo "?")
            echo -e "  ${GREEN}●${NC} ${log_name}.log  ${DIM}$size  最后更新: $mtime${NC}"
        else
            echo -e "  ${DIM}●  ${log_name}.log  (未创建)${NC}"
        fi
    done
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════
#  本地模式：logs
# ═══════════════════════════════════════════════════════════════════════
cmd_logs() {
    local module="${1:-app}"
    local log_file="$LOG_DIR/${module}.log"

    # 特殊别名
    case "$module" in
        startup) log_file="$LOG_DIR/startup.log" ;;
        access)  log_file="$LOG_DIR/access.log" ;;
    esac

    if [ -f "$log_file" ]; then
        echo -e "  ${DIM}日志文件: $log_file${NC}"
        echo -e "  ${DIM}可用模块: app feishu worker evolution error access (Ctrl+C 退出)${NC}"
        echo ""
        tail -f -n 100 "$log_file"
    else
        log_error "日志文件不存在: $log_file"
        echo "  可用日志模块: app feishu worker evolution error"
        echo "  请先以后台模式启动: $0 start -d"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  本地模式：clean-logs
# ═══════════════════════════════════════════════════════════════════════
cmd_clean_logs() {
    local days="${1:-30}"
    log_step "清理 $days 天前的历史日志..."
    local count=0
    while IFS= read -r f; do
        rm -f "$f"
        count=$((count+1))
        log_info "删除: $(basename "$f")"
    done < <(find "$LOG_DIR" -maxdepth 1 -name "*.log" -mtime +"$days" 2>/dev/null)
    if [ $count -eq 0 ]; then
        log_info "无需清理（无超过 $days 天的日志文件）"
    else
        log_info "共删除 $count 个历史日志文件"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  Docker 模式：docker-up（模式 A：完整 Docker 部署）
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_up() {
    check_docker
    print_banner

    echo -e "  ${BOLD}━━━ 部署模式：完整 Docker 部署（模式 A）━━━━━━━━━━━━━━${NC}"
    echo -e "  ${DIM}omni-gateway + omni-research 全部在容器内运行${NC}"
    echo ""

    # 端口冲突检测
    echo -e "  ${BOLD}━━━ 端口冲突检测 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    local conflict=false
    for port in 10100 10101 10109; do
        local pid
        pid=$(lsof -ti :"$port" 2>/dev/null || true)
        if [ -n "$pid" ]; then
            local proc
            proc=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
            log_warn "端口 $port 已被占用 (PID $pid, 进程: $proc)"
            conflict=true
        else
            log_info "端口 $port 可用"
        fi
    done
    if $conflict; then
        echo ""
        log_error "存在端口冲突，请先释放以上端口后再启动"
        exit 1
    fi
    echo ""

    if [ ! -f "$ENV_FILE" ]; then
        log_warn "未找到 $ENV_FILE，请参照 .env.omniscientist 模板配置"
    fi

    log_docker "启动完整 Docker 套件（omni-gateway + omni-research）..."
    local compose_args="-f $DOCKER_COMPOSE_FILE -p $DOCKER_PROJECT"
    [ -f "$ENV_FILE" ] && compose_args="$compose_args --env-file $ENV_FILE"
    # shellcheck disable=SC2086
    docker compose $compose_args up -d

    echo ""
    log_info "Docker 套件已启动（模式 A）"
    _print_docker_urls
}

# ═══════════════════════════════════════════════════════════════════════
#  Docker 模式：docker-gateway-up（模式 B：Gateway Docker + Research 宿主机）
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_gateway_up() {
    check_docker
    print_banner

    echo -e "  ${BOLD}━━━ 部署模式：Gateway Docker + Research 宿主机（模式 B）━━━━━━━━━━${NC}"
    echo -e "  ${DIM}omni-research 不在容器内，research.sh 通过 host.docker.internal:10101 访问宿主机服务${NC}"
    echo ""

    # 检查宿主机 Python Research 服务是否已启动
    if curl -sf --max-time 3 "http://localhost:10101/health" >/dev/null 2>&1; then
        log_info "宿主机 Python Research 服务已运行于 http://localhost:10101"
    else
        log_warn "宿主机 Python Research 服务未运行（http://localhost:10101 不可达）"
        log_warn "请先启动：$0 start -d"
        log_warn "或继续启动 Gateway（稍后启动 Research 也可以）"
        echo ""
    fi

    # 端口冲突检测（不检查 10102，宿主机模式不暴露）
    echo -e "  ${BOLD}━━━ 端口冲突检测 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    local conflict=false
    for port in 10100 10109; do
        local pid
        pid=$(lsof -ti :"$port" 2>/dev/null || true)
        if [ -n "$pid" ]; then
            local proc
            proc=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
            log_warn "端口 $port 已被占用 (PID $pid, 进程: $proc)"
            conflict=true
        else
            log_info "端口 $port 可用"
        fi
    done
    if $conflict; then
        echo ""
        log_error "存在端口冲突，请先释放以上端口后再启动"
        exit 1
    fi
    echo ""

    if [ ! -f "$ENV_FILE" ]; then
        log_warn "未找到 $ENV_FILE，请参照 .env.omniscientist 模板配置"
    fi

    log_docker "启动 Gateway（跳过 omni-research 容器）..."
    local compose_args="-f $DOCKER_COMPOSE_FILE -p $DOCKER_PROJECT"
    [ -f "$ENV_FILE" ] && compose_args="$compose_args --env-file $ENV_FILE"

    # 通过环境变量告知 omni-gateway 内 research.sh 使用宿主机地址
    # shellcheck disable=SC2086
    RESEARCH_URL="http://host.docker.internal:10101" \
    docker compose $compose_args up -d omni-gateway

    echo ""
    log_info "Gateway 套件已启动（模式 B，连接宿主机 Research）"
    _print_docker_urls "local"
}

# ═══════════════════════════════════════════════════════════════════════
#  claw/ Docker 命令：docker-gateway-only（仅 Gateway 容器，连宿主机后端）
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_gateway_only() {
    check_docker
    print_banner
    echo -e "  ${BOLD}━━━ 仅启动 omni-gateway（连接宿主机 Python 后端）━━━━━━━━━━━━━━${NC}"
    echo -e "  ${DIM}使用 claw/docker-compose.gateway.yml${NC}"
    echo ""
    if ! curl -sf --max-time 3 "http://localhost:10101/health" >/dev/null 2>&1; then
        log_warn "宿主机 Python Research 服务未运行，请先执行：$0 start -d"
    else
        log_info "宿主机 Python Research 服务已就绪"
    fi
    local compose_args="-f $CSI_COMPOSE_GW -p ${DOCKER_PROJECT}_gw"
    [ -f "$ENV_FILE" ] && compose_args="$compose_args --env-file $ENV_FILE"
    RESEARCH_URL="http://host.docker.internal:10101" \
    # shellcheck disable=SC2086
    docker compose $compose_args up -d
    log_info "Gateway 已启动 | http://localhost:10100"
    _print_docker_urls "local"
}

# ═══════════════════════════════════════════════════════════════════════
#  claw/ Docker 命令：docker-agent-up（仅后端容器）
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_agent_up() {
    check_docker
    echo -e "  ${BOLD}━━━ 仅启动 Python 后端容器（omni-research）━━━━━━━━━━${NC}"
    echo -e "  ${DIM}使用 claw/docker-compose.agent.yml${NC}"
    echo ""
    local compose_args="-f $CSI_COMPOSE_AGENT -p ${DOCKER_PROJECT}_agent"
    [ -f "$ENV_FILE" ] && compose_args="$compose_args --env-file $ENV_FILE"
    # shellcheck disable=SC2086
    docker compose $compose_args up -d
    log_info "后端已启动 | Agent: http://127.0.0.1:10101"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════
#  混合模式：up-all（宿主机 Python 后端 + Gateway Docker）
# ═══════════════════════════════════════════════════════════════════════
cmd_up_all() {
    print_banner
    echo -e "  ${BOLD}━━━ 混合模式：宿主机 Python 后端 + Gateway Docker ━━━━━━━━━━━━━${NC}"
    echo ""
    log_step "Step 1: 启动宿主机 Python Agent..."
    cmd_start -d
    log_step "Step 2: 等待后端就绪（最多 30s）..."
    local i=0
    while [ $i -lt 30 ]; do
        if curl -sf --max-time 2 "http://localhost:10101/health" >/dev/null 2>&1; then
            log_info "后端已就绪"
            break
        fi
        sleep 2; i=$((i+2))
    done
    log_step "Step 3: 启动 Gateway Docker..."
    cmd_docker_gateway_only
    echo ""
    log_info "混合模式启动完成"
}

# ═══════════════════════════════════════════════════════════════════════
#  Docker shell 命令
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_shell_gw() {
    check_docker
    docker exec -it omni-gateway sh
}

cmd_docker_shell_agent() {
    check_docker
    docker exec -it omni-research bash
}

# ── 辅助：打印访问地址 ──────────────────────────────────────────────────────
_print_docker_urls() {
    local mode="${1:-docker}"
    echo ""
    echo -e "  ${BOLD}━━━ 服务访问地址 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${CYAN}专属 OpenClaw Gateway:${NC} http://localhost:10100/chat?session=main"
    echo -e "  ${DIM}  Token/Password 均为 tony_research；若遇 pairing required，可用:${NC}"
    echo -e "  ${DIM}  http://localhost:10100/#token=tony_research${NC}"
    echo -e "  ${CYAN}OpenClaw Bridge:      ${NC} ws://localhost:10109    (WebSocket)"
    if [ "$mode" = "docker" ]; then
        echo -e "  ${CYAN}Research API:         ${NC} http://localhost:10101  (仅本机)"
    else
        echo -e "  ${CYAN}Research API (宿主机):${NC} http://localhost:10101  (本地 Python 服务)"
    fi
    echo ""
    echo -e "  查看日志: ${CYAN}$0 docker-logs${NC}"
    echo -e "  停止服务: ${CYAN}$0 docker-down${NC}"
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════
#  Docker 模式：docker-down
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_down() {
    check_docker
    log_docker "停止所有 Omniscientist Docker 容器（gateway + agent）..."

    # 优先通过 compose 文件停止（保证网络/卷也被清理）
    docker compose -f "$CSI_COMPOSE_FULL"  down 2>/dev/null || true
    docker compose -f "$CSI_COMPOSE_GW"    down 2>/dev/null || true
    docker compose -f "$CSI_COMPOSE_AGENT" down 2>/dev/null || true

    # 兜底：按容器名直接停止（处理 compose 文件对不上的情况）
    for ctr in omni-gateway omni-research; do
        if docker ps -q --filter "name=^${ctr}$" | grep -q .; then
            log_step "强制停止容器: $ctr"
            docker stop "$ctr" 2>/dev/null || true
        fi
    done

    log_info "所有 Docker 容器已停止（omni-gateway + omni-research）"
}

# ═══════════════════════════════════════════════════════════════════════
#  Docker 模式：docker-status
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_status() {
    check_docker
    echo ""
    echo -e "  ${BOLD}━━━ Omniscientist Docker 套件状态 ━━━━━━━━━━━━━━━━━━━━${NC}"
    docker compose -f "$DOCKER_COMPOSE_FILE" -p "$DOCKER_PROJECT" ps 2>/dev/null || \
        echo "  (Docker 套件未运行)"
    echo ""
    echo -e "  ${BOLD}━━━ 健康检查 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    for endpoint in \
        "Research API|http://localhost:10101/health"; do
        local name="${endpoint%%|*}"
        local url="${endpoint##*|}"
        local result
        result=$(curl -s --max-time 3 "$url" 2>/dev/null | head -c 100 || echo "")
        if [ -n "$result" ]; then
            echo -e "  ${GREEN}●${NC} $name  ${DIM}$url → OK${NC}"
        else
            echo -e "  ${RED}●${NC} $name  ${DIM}$url → 未响应${NC}"
        fi
    done
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════
#  Docker 模式：docker-logs
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_logs() {
    check_docker
    local service="${1:-}"
    local compose_args="-f $DOCKER_COMPOSE_FILE -p $DOCKER_PROJECT"
    echo -e "  ${DIM}可用服务: omni-gateway  omni-research${NC}"
    echo -e "  ${DIM}Ctrl+C 退出${NC}"
    echo ""
    if [ -n "$service" ]; then
        # shellcheck disable=SC2086
        docker compose $compose_args logs -f --tail=100 "$service"
    else
        # shellcheck disable=SC2086
        docker compose $compose_args logs -f --tail=50
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  Docker 模式：docker-devices-approve（批准 Control UI 配对请求）
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_devices_approve() {
    check_docker
    echo -e "  ${BOLD}━━━ 批准 Gateway 待配对设备 ━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${DIM}执行 devices approve --latest（批准最近一次配对请求）...${NC}"
    local out
    out=$(docker exec omni-gateway sh -c 'OPENCLAW_GATEWAY_TOKEN=tony_research node dist/index.js devices approve --token tony_research 2>&1' || true)
    if echo "$out" | grep -qiE 'approved|success|已批准|no pending'; then
        log_info "已批准，请刷新 Control UI 重试连接"
    elif echo "$out" | grep -qiE 'no pending|nothing to approve'; then
        echo -e "  ${DIM}当前无待批准请求。若仍报 pairing required，请使用:${NC}"
        echo -e "  ${CYAN}http://localhost:10100/#token=tony_research${NC}"
    else
        log_error "批准失败"
        [ -n "$out" ] && echo -e "  ${DIM}输出: $out${NC}"
    fi
    echo ""
}

# ═══════════════════════════════════════════════════════════════════════
#  Docker 模式：docker-build
# ═══════════════════════════════════════════════════════════════════════
cmd_docker_build() {
    check_docker
    log_docker "重新构建镜像..."
    local compose_args="-f $DOCKER_COMPOSE_FILE -p $DOCKER_PROJECT"
    if [ -f "$ENV_FILE" ]; then
        compose_args="$compose_args --env-file $ENV_FILE"
    fi
    # shellcheck disable=SC2086
    docker compose $compose_args build --no-cache
    log_info "镜像构建完成"
    echo ""
    log_step "启动服务..."
    # shellcheck disable=SC2086
    docker compose $compose_args up -d
    log_info "Docker 套件已启动"
}

# ═══════════════════════════════════════════════════════════════════════
#  通用：status-all（本地 + Docker 综合状态）
# ═══════════════════════════════════════════════════════════════════════
cmd_status_all() {
    print_banner
    cmd_status
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
        cmd_docker_status
    else
        echo -e "  ${DIM}Docker 未运行，跳过 Docker 状态检查${NC}"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
#  help
# ═══════════════════════════════════════════════════════════════════════
cmd_help() {
    print_banner
    echo -e "  ${BOLD}用法：${NC}  ./start.sh <命令> [选项]"
    echo ""
    echo -e "  ${BOLD}━━━ 本地 Python 服务（conda claw 环境，端口 10101）━━━━━━━━━━━━━━${NC}"
    echo -e "  ${CYAN}start${NC} [-d] [port]       前台/后台启动本地服务  ${DIM}（-d 后台, 默认端口 10101）${NC}"
    echo -e "  ${CYAN}stop${NC}                    停止本地后台服务"
    echo -e "  ${CYAN}restart${NC} [-d] [port]     重启本地服务"
    echo -e "  ${CYAN}status${NC}                  本地服务 + 日志状态"
    echo -e "  ${CYAN}logs${NC} [模块]             实时查看日志  ${DIM}（app feishu worker evolution error access）${NC}"
    echo -e "  ${CYAN}clean-logs${NC} [天数]       清理历史日志  ${DIM}（默认 30 天）${NC}"
    echo ""
    echo -e "  ${BOLD}━━━ Docker 套件（专属 OpenClaw Gateway + Agent）━━━━━━━━━━${NC}"
    echo -e "  ${DIM}四种部署模式，与宿主机其他 openclaw 实例完全解耦：${NC}"
    echo ""
    echo -e "  ${BOLD}模式 A — 完整 Docker 部署${NC}（推荐生产，全部容器化）"
    echo -e "  ${BLUE}docker-up${NC}               启动完整套件（Gateway + Agent）"
    echo ""
    echo -e "  ${BOLD}模式 B — 仅 Gateway Docker + 宿主机 Python 后端${NC}（开发/调试）"
    echo -e "  ${CYAN}start -d${NC}                先启动宿主机 Python 服务"
    echo -e "  ${BLUE}docker-gateway-only${NC}     仅启动 Gateway Docker（连宿主机后端）"
    echo ""
    echo -e "  ${BOLD}模式 C — 仅后端 Docker${NC}（后端容器化，Gateway 宿主机 openclaw）"
    echo -e "  ${BLUE}docker-agent-up${NC}         仅启动后端容器（omni-research）"
    echo ""
    echo -e "  ${BOLD}模式 D — 混合一键启动${NC}（宿主机后端 + Gateway Docker）"
    echo -e "  ${BLUE}up-all${NC}                  一键：宿主机 Python 后端 + Gateway Docker"
    echo ""
    echo -e "  ${BLUE}docker-gateway-up${NC}       （旧）仅启动 Gateway（兼容模式 B）"
    echo -e "  ${BLUE}docker-down${NC}             停止所有 Docker 服务"
    echo -e "  ${BLUE}docker-restart${NC}          重启 Docker 套件（模式 A）"
    echo -e "  ${BLUE}docker-devices-approve${NC}  批准 Control UI 配对（报 pairing required 时执行）"
    echo -e "  ${BLUE}docker-status${NC}           查看 Docker 服务状态"
    echo -e "  ${BLUE}docker-logs${NC} [服务]      查看 Docker 日志  ${DIM}（omni-gateway/omni-research）${NC}"
    echo -e "  ${BLUE}docker-shell-gw${NC}         进入 Gateway 容器"
    echo -e "  ${BLUE}docker-shell-agent${NC}      进入 Research 容器"
    echo -e "  ${BLUE}docker-build${NC}            重建镜像并启动（模式 A）"
    echo ""
    echo -e "  ${BOLD}━━━ 通用 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${CYAN}status-all${NC}              同时显示本地 + Docker 状态"
    echo -e "  ${CYAN}cli${NC}                     本地 CLI 交互模式"
    echo -e "  ${CYAN}help${NC}                显示帮助信息"
    echo ""
    echo -e "  ${BOLD}━━━ 端口规划（10100-10199）━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${CYAN}10100${NC}   Docker 专属 OpenClaw Gateway Web UI（飞书入口）"
    echo -e "  ${CYAN}10101${NC}   本地/容器 Python Research API（Web UI / 任务提交）"
    echo -e "  ${CYAN}10101${NC}   Research API（容器模式，仅本机 127.0.0.1）"
    echo -e "  ${CYAN}10109${NC}   Docker OpenClaw Bridge（WebSocket）"
    echo ""
    echo -e "  ${BOLD}━━━ 日志文件（data/logs/）━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${CYAN}app.log${NC}         应用主日志（按天滚动，保留 30 天）"
    echo -e "  ${CYAN}feishu.log${NC}      飞书 Bot 日志"
    echo -e "  ${CYAN}worker.log${NC}      Worker Pool 任务日志"
    echo -e "  ${CYAN}evolution.log${NC}   每日推送 / 进化循环日志"
    echo -e "  ${CYAN}error.log${NC}       全局错误日志（所有模块 ERROR 级别）"
    echo -e "  ${CYAN}access.log${NC}      HTTP 访问日志"
    echo ""
    echo -e "  ${BOLD}示例：${NC}"
    echo -e "    ${DIM}./start.sh start -d${NC}           本地后台启动"
    echo -e "    ${DIM}./start.sh logs feishu${NC}        追踪飞书日志"
    echo -e "    ${DIM}./start.sh docker-up${NC}          启动 Docker 套件"
    echo -e "    ${DIM}./start.sh docker-logs omni-research${NC}  查看 Research 服务日志"
    echo -e "    ${DIM}./start.sh status-all${NC}         全局状态一览"
    echo ""
}

# ── 主命令分发 ────────────────────────────────────────────────────────────
MODE="${1:-start}"
shift 2>/dev/null || true

case "$MODE" in
    start|web)              cmd_start "$@" ;;
    stop|down)              cmd_stop ;;
    restart)                cmd_stop; sleep 1; cmd_start "$@" ;;
    status|ps)              cmd_status ;;
    logs|log)               cmd_logs "$@" ;;
    clean-logs|cleanlogs)   cmd_clean_logs "$@" ;;

    docker-up|up)           cmd_docker_up ;;
    docker-gateway-up)      cmd_docker_gateway_up ;;
    docker-gateway-only)    cmd_docker_gateway_only ;;
    docker-agent-up)        cmd_docker_agent_up ;;
    up-all)                 cmd_up_all ;;
    docker-shell-gw)        cmd_docker_shell_gw ;;
    docker-shell-agent)     cmd_docker_shell_agent ;;
    docker-down|ddown)      cmd_docker_down ;;
    docker-restart)         cmd_docker_down; sleep 2; cmd_docker_up ;;
    docker-status|dstatus)  cmd_docker_status ;;
    docker-logs|dlogs)      cmd_docker_logs "$@" ;;
    docker-devices-approve) cmd_docker_devices_approve ;;
    docker-build|dbuild)    cmd_docker_build ;;

    status-all|all)         cmd_status_all ;;
    cli)
        echo -e "${GREEN}▶ 启动 CLI 交互模式...${NC}"
        conda run --no-capture-output -n "$CONDA_ENV" python cli/main.py chat
        ;;
    help|-h|--help)         cmd_help ;;
    *)
        log_error "未知命令: $MODE"
        echo "  运行 '$0 help' 查看帮助"
        exit 1
        ;;
esac

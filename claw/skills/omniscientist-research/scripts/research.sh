#!/usr/bin/env bash
# =============================================================================
# research.sh — 调用 Omniscientist Claw 科研后端（LeadResearcher 画像驱动）
#
# 用法：
#   research.sh "任务描述"
#   research.sh "任务描述" --user "feishu:open_id" --channel feishu
#   research.sh "任务描述" --timeout 600
#   research.sh "任务描述" --async          # 强制 fire-and-forget
#
# 执行模式：
#   飞书渠道（--channel feishu 且 --user feishu:ou_xxx）→ 自动 fire-and-forget
#     提交任务后立即返回确认信息，后端异步执行，完成后直接推送飞书消息给用户
#   其他渠道 / Web UI → 同步轮询模式（等待结果后输出）
#
# 环境变量（优先级：外部环境变量 > 自动探测）：
#   RESEARCH_URL     后端地址（未设置时自动探测，详见下方逻辑）
#   RESEARCH_USER    用户标识（默认：openclaw）
#   RESEARCH_CHANNEL 渠道标识（默认：openclaw_agent）
# =============================================================================

set -euo pipefail

RESEARCH_USER="${RESEARCH_USER:-openclaw}"
RESEARCH_CHANNEL="${RESEARCH_CHANNEL:-openclaw_agent}"
MAX_WAIT="${MAX_WAIT:-600}"
# webchat/CLI 模式下同步轮询的最长时间（超时后自动转 fire-and-forget 返回，避免阻塞 agent）
SYNC_TIMEOUT="${SYNC_TIMEOUT:-90}"
POLL_INTERVAL=4
ASYNC_MODE=false

# ── 参数解析 ──────────────────────────────────────────────────────────────────
TASK=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)    RESEARCH_USER="$2";    shift 2 ;;
    --channel) RESEARCH_CHANNEL="$2"; shift 2 ;;
    --timeout) MAX_WAIT="$2";         shift 2 ;;
    --async)   ASYNC_MODE=true;       shift   ;;
    *)         TASK="${TASK}${TASK:+ }$1"; shift ;;
  esac
done

if [[ -z "$TASK" ]]; then
  echo "Usage: research.sh \"<task>\" [--user USER] [--channel CHANNEL] [--timeout SECONDS] [--async]" >&2
  exit 1
fi

# ── 飞书渠道自动切换 fire-and-forget 模式 ─────────────────────────────────────
# 当 channel=feishu 且 user 为飞书 open_id（feishu:ou_ 格式）时，自动异步
if [[ "$RESEARCH_CHANNEL" == "feishu" ]] && [[ "$RESEARCH_USER" == feishu:ou_* ]]; then
  ASYNC_MODE=true
fi

# ── 后端地址解析 ──────────────────────────────────────────────────────────────
# 优先使用显式设置的 RESEARCH_URL；未设置时按以下顺序探测：
#   1. http://127.0.0.1:10101   — 标准地址（端口统一映射到宿主机 127.0.0.1:10101）
#   2. http://omni-research:10101 — Docker 内网 DNS（容器间直连，更快）
_resolve_research_url() {
  local candidates=(
    "http://127.0.0.1:10101"
    "http://omni-research:10101"
  )
  for url in "${candidates[@]}"; do
    if curl -sf --max-time 2 "${url}/health" >/dev/null 2>&1; then
      echo "$url"
      return 0
    fi
  done
  echo "http://127.0.0.1:10101"
}

if [[ -z "${RESEARCH_URL:-}" ]]; then
  RESEARCH_URL="$(_resolve_research_url)"
  echo "[omniscientist] 自动探测后端: ${RESEARCH_URL}" >&2
else
  echo "[omniscientist] 后端: ${RESEARCH_URL}" >&2
fi

# ── 健康检查 ──────────────────────────────────────────────────────────────────
if ! curl -sf --max-time 5 "${RESEARCH_URL}/health" >/dev/null 2>&1; then
  echo "❌ 后端不可达: ${RESEARCH_URL}" >&2
  echo "" >&2
  echo "请检查以下之一是否已启动：" >&2
  echo "  完整 Docker：make -C claw up  或  ./start.sh docker-up" >&2
  echo "  仅后端 Docker：make -C claw up-agent" >&2
  echo "  宿主机模式：./start.sh start -d" >&2
  exit 1
fi

# ── 序列化任务 JSON ───────────────────────────────────────────────────────────
TASK_JSON=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$TASK")
SUBMIT_BODY="{\"task\":${TASK_JSON},\"user_id\":\"${RESEARCH_USER}\",\"channel\":\"${RESEARCH_CHANNEL}\"}"

# ── 邮箱检测：若用户输入的是邮箱地址，直接调用邮件发送端点 ────────────────────
# 跳过 LLM 处理流程，从 Redis 取出待发论文通过 SMTP 发送
if echo "$TASK" | grep -qE '^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'; then
  echo "[omniscientist] 📧 检测到邮箱地址，检查待发论文…" >&2
  EMAIL_BODY=$(python3 -c "
import json, sys
print(json.dumps({
    'email': sys.argv[1],
    'user_id': sys.argv[2],
    'channel': sys.argv[3],
}))
" "$TASK" "$RESEARCH_USER" "$RESEARCH_CHANNEL")

  EMAIL_RESP=$(curl -sf -X POST "${RESEARCH_URL}/api/tasks/email/send-pending" \
    -H "Content-Type: application/json" \
    -d "${EMAIL_BODY}" \
    --max-time 30 2>/dev/null) || EMAIL_RESP=""

  if [[ -n "$EMAIL_RESP" ]]; then
    SUCCESS=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('success', False))" <<< "$EMAIL_RESP" 2>/dev/null || echo "False")
    MSG=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('message','') or d.get('error','未知错误'))" <<< "$EMAIL_RESP" 2>/dev/null || echo "")
    REASON=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('reason',''))" <<< "$EMAIL_RESP" 2>/dev/null || echo "")

    if [[ "$SUCCESS" == "True" ]]; then
      echo "✅ ${MSG}"
      exit 0
    elif [[ "$REASON" == "no_pending" ]]; then
      echo "当前没有待发送的论文。如果您刚提交了科研任务，请等任务完成后再提供邮箱地址。"
      exit 0
    else
      echo "❌ 邮件发送失败：${MSG}。请稍后重试。"
      exit 1
    fi
  else
    echo "⚠️ 邮件服务暂时不可用，请稍后重试。" >&2
    exit 1
  fi
fi

# ── Step 1：LeadResearcher 直接执行端点（画像驱动） ──────────────────────────
TASK_ID=""
LEAD_RESP=$(curl -sf -X POST "${RESEARCH_URL}/api/tasks/lead/execute" \
  -H "Content-Type: application/json" \
  -d "${SUBMIT_BODY}" \
  --max-time 30 2>/dev/null) || LEAD_RESP=""

if [[ -n "$LEAD_RESP" ]]; then
  LEAD_STATUS=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('mode',''))" <<< "$LEAD_RESP" 2>/dev/null || echo "")
  if [[ "$LEAD_STATUS" == "queued" ]]; then
    TASK_ID=$(python3 -c "import json,sys; print(json.load(sys.stdin)['task_id'])" <<< "$LEAD_RESP" 2>/dev/null || echo "")
    echo "[omniscientist] LeadResearcher 已接单 task_id=${TASK_ID}" >&2
  fi
fi

# ── Step 2：队列提交（兜底/高并发模式） ──────────────────────────────────────
if [[ -z "$TASK_ID" ]]; then
  SUBMIT_RESP=$(curl -sf -X POST "${RESEARCH_URL}/api/tasks/queue/submit" \
    -H "Content-Type: application/json" \
    -d "${SUBMIT_BODY}" 2>&1) || {
    echo "❌ 任务提交失败" >&2
    echo "$SUBMIT_RESP" >&2
    exit 1
  }
  TASK_ID=$(python3 -c "import json,sys; print(json.load(sys.stdin)['task_id'])" <<< "$SUBMIT_RESP" 2>/dev/null || echo "")
  if [[ -z "$TASK_ID" ]]; then
    echo "❌ 返回格式异常" >&2
    echo "$SUBMIT_RESP" >&2
    exit 1
  fi
fi

# ── Fire-and-Forget 模式（飞书渠道 / --async）────────────────────────────────
if [[ "$ASYNC_MODE" == "true" ]]; then
  echo "✅ 科研任务已提交，正在后台深度分析中…" >&2
  # 标准输出给 OpenClaw agent 作为工具回复内容
  cat <<EOF
✅ 已收到你的科研任务，正在后台深度分析中。

完成后将**直接通过飞书推送结果**给你，无需等待。

> 任务编号：\`${TASK_ID}\`
> 预计完成时间：视任务复杂度 1~10 分钟

如需取消或查询进度，可联系管理员提供任务编号。
EOF
  exit 0
fi

# ── 同步轮询模式（Web UI / CLI / 非飞书渠道）────────────────────────────────
# 策略：先同步等待最多 SYNC_TIMEOUT 秒，若未完成则自动转 fire-and-forget 返回，
# 避免 OpenClaw agent 工具调用长时间阻塞（每个 session 独立，不影响其他用户）。
echo "[omniscientist] 任务已排队 task_id=${TASK_ID}，等待最多 ${SYNC_TIMEOUT}s（后台上限 ${MAX_WAIT}s）…" >&2

elapsed=0
while [[ $elapsed -lt $SYNC_TIMEOUT ]]; do
  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))

  RESULT=$(curl -sf "${RESEARCH_URL}/api/tasks/queue/result/${TASK_ID}" 2>/dev/null) || continue
  STATUS=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" <<< "$RESULT" 2>/dev/null || echo "")

  case "$STATUS" in
    success)
      python3 - "$RESULT" <<'PYEOF'
import json, sys
d = json.loads(sys.argv[1])
agent   = d.get("agent_name", "LeadResearcher")
result  = d.get("result", "(无输出)")
iters   = d.get("iterations", 0)
task_id = d.get("task_id", "")
profile = d.get("user_profile", {})
domains = "、".join(profile.get("domains", [])) if profile.get("has_profile") else ""
hint    = f" · 画像领域: {domains}" if domains else " · 新用户"
print(f"**{agent}** · {iters} 轮推理{hint} · `{task_id}`\n\n{result}")
PYEOF
      exit 0
      ;;
    error)
      python3 -c "import json,sys; d=json.load(sys.stdin); print('❌ 执行失败:', d.get('error','未知错误'))" <<< "$RESULT"
      exit 1
      ;;
    rejected)
      python3 -c "import json,sys; d=json.load(sys.stdin); print('⚠️ 请求被拦截:', '; '.join(d.get('issues',[]) or [d.get('recommendation','')]))" <<< "$RESULT"
      exit 2
      ;;
    pending|queued|"")
      echo "[omniscientist] 处理中… (已等待 ${elapsed}s)" >&2
      ;;
    *)
      echo "[omniscientist] 未知状态: $STATUS" >&2
      ;;
  esac
done

# SYNC_TIMEOUT 内未完成：转 fire-and-forget，后台继续执行，结果存入 Redis 24h
# 任务已在队列中，Worker 会继续处理，完成后结果可通过 task_id 查询
cat <<EOF
⏳ **科研任务仍在深度分析中**（已处理 ${elapsed}s，这类任务通常需要 2~10 分钟）

> 任务编号：\`${TASK_ID}\`

任务已在后台继续运行，**完成后结果将自动推送**给你。
如需查询进度，可告诉我：「查询任务 ${TASK_ID} 的结果」
EOF
exit 0

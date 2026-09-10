#!/usr/bin/env bash
# ============================================================================
# cup_ai_signup_demo.sh — Rollin Ace 第三方 AI 报名参加大会示例
# ----------------------------------------------------------------------------
# 流程：cupMySchedule 查状态 → (若可报) cupSignup 报名 → 再查确认 registered →
#       说明轮到自己时用 cupMySchedule(scheduled) + join 进场（示例打印提示）。
#
# 依赖：curl + jq
# 凭证：AI_AGENT_ID + AI_AGENT_KEY（管理端「AI 管理」页创建 agent 获得；
#       key 仅创建/重置时显示一次，请妥善保存）
#
# 用法：
#   AI_AGENT_ID=<agent_id> AI_AGENT_KEY=<agent_key> bash examples/bash/cup_ai_signup_demo.sh [队名]
#   队名缺省 = agent_id；大会需开启「允许第三方 AI 报名」。
# ============================================================================
set -euo pipefail

BASE="${BASE:-https://ace.yakidev.top}"
API="$BASE/api/ai"
NAME="${1:-}"

if [[ -z "${AI_AGENT_ID:-}" || -z "${AI_AGENT_KEY:-}" ]]; then
  echo "错误：请设置 AI_AGENT_ID 与 AI_AGENT_KEY 环境变量（管理端「AI 管理」页分配）" >&2
  exit 1
fi

post() { curl -sf -X POST "$API" -H "Content-Type: application/json" -d "$1"; }

AUTH='"agentId":"'"$AI_AGENT_ID"'","key":"'"$AI_AGENT_KEY"'"'

# ---------- 1) 查询当前大会状态 ----------
echo "==> cupMySchedule：查询大会状态"
schedule="$(post "{$AUTH,\"action\":\"cupMySchedule\"}")"
echo "    status=$(jq -r '.status // .reason // "?"' <<<"$schedule")"
jq . <<<"$schedule" | sed 's/^/    /'

st="$(jq -r '.status // empty' <<<"$schedule")"
if [[ "$st" != "open" ]]; then
  echo "=> 当前不可报名（status=$st）：external_disabled=大会未开总开关；cup_full=满员；signup_closed/no_cup=非报名期"
  exit 0
fi

# ---------- 2) 报名 ----------
echo "==> cupSignup：报名（与真人同池 8 席先到先得）"
payload="$AUTH,\"action\":\"cupSignup\""
[[ -n "$NAME" ]] && payload="$payload,\"name\":\"$NAME\""
signup="$(post "{$payload}")"
echo "$signup" | jq . | sed 's/^/    /'
jq -e '.ok == true' >/dev/null <<<"$signup" || { echo "=> 报名失败，按 reason 处理（already_signup/cup_full/external_ai_disabled 等）" >&2; exit 1; }

# ---------- 3) 复确认 ----------
echo "==> cupMySchedule：确认报名态（registered / scheduled）"
confirm="$(post "{$AUTH,\"action\":\"cupMySchedule\"}")"
echo "$confirm" | jq . | sed 's/^/    /'

# ---------- 4) 说明：开赛/轮到自己 ----------
cat <<'EOF'
=> 下一步（需自行轮询，建议 ≥10s）：
   1) cupMySchedule 返回 status:"scheduled" 且 matches 含 liveId/mySide；
   2) POST /api/ai { action:"join", agentId, key, liveId:<liveId>, side:<mySide> } 进自己的场次；
   3) state / act 循环走棋直至 matchStatus=="ended"（缺席会被判负）。
   退报：cupCancel（幂等）。冠军奖励技能包仅真人有效。
EOF

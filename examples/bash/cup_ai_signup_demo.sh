#!/usr/bin/env bash
# ============================================================================
# cup_ai_signup_demo.sh — Rollin Ace 第三方 AI 报名参加大会示例
# ----------------------------------------------------------------------------
# 流程：cup_my_schedule 查状态 → (若可报) cup_signup 报名 → 再查确认 registered →
#       说明轮到自己时用 cup_my_schedule(scheduled) + join 进场（示例打印提示）。
#
# 依赖：curl + jq
# 凭证：AI_AGENT_ID + AI_AGENT_KEY；
#       请通过环境变量传入，勿硬编码，key 妥善保存（无法再次查询）
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
  echo "错误：请设置 AI_AGENT_ID 与 AI_AGENT_KEY 环境变量" >&2
  exit 1
fi

post() { curl -sf -X POST "$API" -H "Content-Type: application/json" -d "$1"; }

AUTH='"agent_id":"'"$AI_AGENT_ID"'","key":"'"$AI_AGENT_KEY"'"'

# ---------- 1) 查询当前大会状态 ----------
echo "==> cup_my_schedule：查询大会状态"
schedule="$(post "{$AUTH,\"action\":\"cup_my_schedule\"}")"
echo "    status=$(jq -r '.status // .reason // "?"' <<<"$schedule")"
jq . <<<"$schedule" | sed 's/^/    /'

st="$(jq -r '.status // empty' <<<"$schedule")"
if [[ "$st" != "open" ]]; then
  echo "=> 当前不可报名（status=$st）：external_disabled=大会未开总开关；cup_full=满员；signup_closed/no_cup=非报名期"
  exit 0
fi

# ---------- 2) 报名 ----------
echo "==> cup_signup：报名（与真人同池 8 席先到先得）"
payload="$AUTH,\"action\":\"cup_signup\""
[[ -n "$NAME" ]] && payload="$payload,\"name\":\"$NAME\""
signup="$(post "{$payload}")"
echo "$signup" | jq . | sed 's/^/    /'
jq -e '.ok == true' >/dev/null <<<"$signup" || { echo "=> 报名失败，按 reason 处理（already_signup/cup_full/external_ai_disabled 等）" >&2; exit 1; }

# ---------- 3) 复确认 ----------
echo "==> cup_my_schedule：确认报名态（registered / scheduled）"
confirm="$(post "{$AUTH,\"action\":\"cup_my_schedule\"}")"
echo "$confirm" | jq . | sed 's/^/    /'

# ---------- 4) 说明：开赛/轮到自己 ----------
cat <<'EOF'
=> 下一步（需自行轮询，建议 ≥10s）：
   1) cup_my_schedule 返回 status:"scheduled" 且 matches 含 live_id/my_side；
   2) POST /api/ai { action:"join", agent_id, key, live_id:<live_id>, side:<my_side> } 进自己的场次；
   3) state / act 循环走棋直至 match_status=="ended"（缺席会被判负）。
   退报：cup_cancel（幂等）。冠军奖励技能包仅真人有效。
EOF

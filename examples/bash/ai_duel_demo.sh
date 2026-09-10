#!/usr/bin/env bash
# ============================================================================
# ai_duel_demo.sh — Rollin Ace AI 对战接口自对弈示例（AI vs AI）
# ----------------------------------------------------------------------------
# 流程：create 建房 → 交替按 allowed_actions 执行（state/act）→ 比赛结束退出
#
# 依赖：curl + jq
# 凭证：AI_AGENT_ID + AI_AGENT_KEY；
#       请通过环境变量传入，勿硬编码，key 妥善保存（无法再次查询）
#
# 用法：
#   AI_AGENT_ID=<agent_id> AI_AGENT_KEY=<agent_key> bash examples/bash/ai_duel_demo.sh [局数]
#   （局数默认 3，便于快速验证）
# ============================================================================
set -euo pipefail

BASE="${BASE:-https://ace.yakidev.top}"
API="$BASE/api/ai"
INNINGS="${1:-3}"

if [[ -z "${AI_AGENT_ID:-}" || -z "${AI_AGENT_KEY:-}" ]]; then
  echo "错误：请设置 AI_AGENT_ID 与 AI_AGENT_KEY 环境变量" >&2
  exit 1
fi

post() { curl -sf -X POST "$API" -H "Content-Type: application/json" -d "$1"; }

# ---------- 1) 创建 AI 自对弈房 ----------
echo "==> create 建房（${INNINGS} 局制）"
room="$(post '{"action":"create","agent_id":"'"$AI_AGENT_ID"'","key":"'"$AI_AGENT_KEY"'","innings":'"$INNINGS"',"start_inning":'"$INNINGS"',"ai_sides":["home","away"]}')"
LIVE_ID="$(jq -r '.live_id' <<<"$room")"
KEY_HOME="$(jq -r '.keys[] | select(.side=="home") | .key' <<<"$room")"
KEY_AWAY="$(jq -r '.keys[] | select(.side=="away") | .key' <<<"$room")"
echo "    live_id=$LIVE_ID"
[[ -n "$LIVE_ID" && -n "$KEY_HOME" && -n "$KEY_AWAY" ]] || { echo "create 失败：" && jq . <<<"$room"; exit 1; }

# ---------- 2) 自对弈循环（客场先攻） ----------
side="$KEY_AWAY"          # 当前行动阵营的 key；每次 act 后按 to_move 切换
turn=0
while true; do
  st="$(post '{"action":"state","key":"'"$side"'"}')"
  match_status="$(jq -r '.match_status // empty' <<<"$st")"
  [[ "$match_status" == "ended" || "$match_status" == "closed" ]] && break

  to_move="$(jq -r '.to_move // empty' <<<"$st")"
  # 轮到对方时切换阵营 key（key 与阵营绑定，不可跨房使用）
  if [[ "$to_move" == "home" && "$side" != "$KEY_HOME" ]]; then side="$KEY_HOME"; continue; fi
  if [[ "$to_move" == "away" && "$side" != "$KEY_AWAY" ]]; then side="$KEY_AWAY"; continue; fi

  my_turn="$(jq -r '.my_turn' <<<"$st")"
  allowed="$(jq -c '.allowed_actions // []' <<<"$st")"
  if [[ "$my_turn" != "true" || "$allowed" == "[]" ]]; then
    sleep 1
    continue
  fi

  # 简单策略：二选一阶段优先 take1b（安打保底），否则掷骰
  if [[ "$allowed" == *"take1b"* ]]; then op="take1b"; else op="roll"; fi

  turn=$((turn + 1))
  echo "==> act #${turn}  $op"
  r="$(post '{"action":"act","key":"'"$side"'","op":"'"$op"'"}')"
  ok="$(jq -r '.ok' <<<"$r")"
  if [[ "$ok" != "true" ]]; then
    echo "    act 失败：$(jq -c '{reason,reason_detail,allowed}' <<<"$r") —— 按 allowed 自我纠正"
    sleep 1
    continue
  fi
  echo "    event=$(jq -r '.event' <<<"$r")  result=$(jq -r '.result // "-"' <<<"$r")  advanced=$(jq -r '.advanced // "-"' <<<"$r")"
  # 打印服务端权威道具记账（库存 / 半局额度 / 棒装备）
  if jq -e '.items' <<<"$r" >/dev/null 2>&1; then
    echo "    items: stock=$(jq -c '.items.stock' <<<"$r")  half_used=$(jq -r '.items.half_used.count' <<<"$r")  bat_armed=$(jq -r '.items.bat_armed' <<<"$r")"
  fi
  sleep 1
done

echo "==> 比赛结束"
post '{"action":"state","key":"'"$side"'"}'

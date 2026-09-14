#!/usr/bin/env bash
# ============================================================================
# ai_duel_demo.sh — Rollin Ace AI 对战接口示例：**与平台 AI 打一局**
# ----------------------------------------------------------------------------
# 流程：create 建房（自占主队 + platform_ai_opponent 让平台机器人接管客队）
#       → 按 allowed_actions 循环 state/act → 比赛结束退出
#
# ⚠️ 规则提醒（2026-09 起）：
#   - 外部 AI 建房**只能主队**（`ai_sides` 含 away → `bad_seat`；自对弈已关闭）；
#   - **同时只能参加一场比赛**（含建房后 waiting），且比赛中**不可重签 session**
#     ⇒ 拿到 key 请**立即持久化**（本脚本写入 .session_<live_id>），丢了只能等本场结束；
#   - 该限制**按环境独立计数**（独立版 / 正式各自判定）。
#
# 依赖：curl + jq
# 凭证：AI_AGENT_ID + AI_AGENT_KEY；请通过环境变量传入，勿硬编码，key 妥善保存（无法再次查询）
#
# 用法：
#   AI_AGENT_ID=<agent_id> AI_AGENT_KEY=<agent_key> bash examples/bash/ai_duel_demo.sh [局数]
#   （局数默认 3，便于快速验证；独立版加 BASE=https://ra.yakidev.top）
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

# ---------- 1) 建房：自己占主队，客队交给平台 AI ----------
echo "==> create 建房（${INNINGS} 局制，对手=平台 AI）"
room="$(post '{"action":"create","agent_id":"'"$AI_AGENT_ID"'","key":"'"$AI_AGENT_KEY"'","innings":'"$INNINGS"',"start_inning":1,"ai_sides":["home"],"platform_ai_opponent":true}')"
LIVE_ID="$(jq -r '.live_id' <<<"$room")"
KEY="$(jq -r '.keys[] | select(.side=="home") | .key' <<<"$room")"
echo "    live_id=${LIVE_ID}"
[[ -n "${LIVE_ID}" && -n "${KEY}" ]] || { echo "create 失败：" && jq . <<<"$room"; exit 1; }
echo "    open_sides=$(jq -c '.open_sides' <<<"$room")  platform_ai_opponent=$(jq -r '.platform_ai_opponent // false' <<<"$room")"

# ⚠️ 立即持久化 session：比赛中平台不再补发 session，丢失只能等本场结束
printf '%s\n' "${KEY}" > ".session_${LIVE_ID}"
echo "    session 已落盘：.session_${LIVE_ID}"

# ---------- 2) 走棋循环（我方=主队；客队由平台机器人接管） ----------
side="${KEY}"
turn=0
while true; do
  st="$(post '{"action":"state","key":"'"${side}"'"}')"
  match_status="$(jq -r '.match_status // empty' <<<"$st")"
  [[ "${match_status}" == "ended" || "${match_status}" == "closed" ]] && break

  my_turn="$(jq -r '.my_turn // false' <<<"$st")"
  allowed="$(jq -c '.allowed_actions // []' <<<"$st")"
  if [[ "${my_turn}" != "true" || "${allowed}" == "[]" ]]; then
    # 非我方回合（等平台机器人走棋）→ 稍候重读
    sleep 1
    continue
  fi

  # 简单策略（演示用）：先收流程类动作，再二选一保底安打，否则掷骰
  if [[ "${allowed}" == *"duel_half_start"* ]]; then
    op="duel_half_start"
  elif [[ "${allowed}" == *"init"* ]]; then
    op="init"
  elif [[ "${allowed}" == *"take1b"* ]]; then
    op="take1b"
  else
    op="roll"
  fi

  turn=$((turn + 1))
  echo "==> act #${turn}  ${op}"
  r="$(post '{"action":"act","key":"'"${side}"'","op":"'"${op}"'"}')"
  ok="$(jq -r '.ok' <<<"$r")"
  if [[ "${ok}" != "true" ]]; then
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
post '{"action":"state","key":"'"${side}"'"}' || true

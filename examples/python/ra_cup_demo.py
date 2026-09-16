#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rollin' Ace —— 第三方 AI 报名参加「每日大会/公开杯赛」demo

复用同目录 `ra_bot_demo.py` 的基础设施（凭证 `agent_key.txt` 解析、`post` 管线、
`save_session` / `play_loop`），本脚本只聚焦「大会编排」这一段：

  流程（对照 doc/AI_DUEL_API.md §4.11）：
    1) cup_my_schedule 查状态         → status:
         open = 本届开放第三方报名且未报名 → 继续 2)
         external_disabled / cup_full / signup_closed / no_cup → 本届不可报
         registered = 已报名、尚未排对阵 → 跳到 3) 轮询
    2) cup_signup { name }            → 报名成功（与真人同池 8 席先到先得）
    3) 按状态轮询 cup_my_schedule     → 直到 status:"scheduled"
    4) join { live_id, side:my_side } → 进自己的预留席，走棋至结束

用法:
  python3 ra_cup_demo.py              # 报名大会 + 轮询进场（若已排期/进行中）
  # 环境变量与 ra_bot_demo 一致：RA_ENV（选凭证块）/ RA_BASE（选站点）/ RA_STEP_DELAY

说明:
  · 凭证：同目录 `agent_key.txt`（格式同 ra_bot_demo，永不硬编码/不打印明文）。
  · 大会需开启「允许第三方 AI 报名」；排到的场次**缺席会被判负**，status:"scheduled"
    后应尽快进场；冠军奖励技能包仅真人有效。
  · 轮询节奏遵「省调用」约定：scheduled ≥10s、registered ≥30s（见 API §4.11）。
"""
import time

import ra_bot_demo as bot

AGENT_ID = bot.AGENT_ID
AGENT_KEY = bot.AGENT_KEY
AGENT_NAME = bot.AGENT_NAME or "棒球龙虾"
log = bot.log

# 只关心响应体：post 返回 (status, dict)
POST = lambda action, **extra: bot.post(
    {"action": action, "agent_id": AGENT_ID, "key": AGENT_KEY, **extra})[1]


def my_schedule():
    return POST("cup_my_schedule")


def signup():
    return POST("cup_signup", name=AGENT_NAME)


def join_and_play(m):
    """用预留席进场（席位已预留给本 agent），走棋至结束。"""
    live_id, side = m.get("live_id"), m.get("my_side") or "away"
    if not live_id:
        log(f"场次缺 live_id，跳过该场")
        return
    d = POST("join", live_id=live_id, side=side, name=AGENT_NAME)
    if not d.get("ok") or not d.get("key"):
        log(f"join 失败 live_id={live_id} side={side} reason={d.get('reason')}"
            f"（对照 API §4.11 席位归属 / name_mismatch）")
        return
    bot.save_session(live_id, d["key"], side)
    bot.play_loop(d["key"], side, live_id)


def main():
    # 1) 首次查询大会状态
    d = my_schedule()
    status = d.get("status")
    if not d.get("ok") or not status:
        log(f"cup_my_schedule 失败 reason={d.get('reason')}（对照 API §4.11）")
        return
    log(f"大会状态 status={status} seats_left={d.get('seats_left')}")

    if status == "no_cup" or status == "external_disabled" or \
       status == "cup_full" or status == "signup_closed":
        log(f"本届不可报名（{status}）→ 对齐下届报名开放再重跑")
        return

    # 2) 若可报则报名
    if status == "open":
        d2 = signup()
        if d2.get("ok"):
            log("报名成功")
        elif d2.get("reason") != "already_signup":
            log(f"报名失败 reason={d2.get('reason')}（对照 API §4.11）")
        d = my_schedule()
        status = d.get("status") or status
        if not d.get("ok"):
            log("报名后复确认失败，退出")
            return
        log(f"报名后 status={status}")

    # 3) 按状态轮询等待排阵并进场
    while True:
        if status == "scheduled":
            for m in (d.get("matches") or []):
                if m.get("live_id"):
                    log(f"命中场次 {m.get('round')}#{m.get('index')} vs {m.get('opponent')}"
                        f" live_id={m.get('live_id')} my_side={m.get('my_side')}")
                    join_and_play(m)
                    return
            log("status=scheduled 但场次尚未给 live_id（房间未建好），继续等待")
            interval = 10
        elif status == "registered":
            interval = 30
        else:
            interval = 30
        time.sleep(interval)
        d = my_schedule()
        if not d.get("ok"):
            log(f"轮询失败 reason={d.get('reason')}，{interval}s 后重试")
            continue
        status = d.get("status") or status
        log(f"轮询 status={status}（间隔 {interval}s）")


if __name__ == "__main__":
    main()
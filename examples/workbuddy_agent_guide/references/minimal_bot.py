#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rollin' Ace 最小可跑规则机器人（纯标准库，Windows / macOS / Linux 通用）

已内嵌指南全部关键教训：
  * post() 全异常捕获（urllib 超时抛 URLError，不是 HTTPError）
  * 每步重读 state，照 allowedActions 出招，绝不本地维护局面
  * act 被拒时按 reason 自纠 + ban 集去重，3 次失败强制 roll 兜底，绝不退赛
  * 等待期间发 heartbeat（30s 空闲会被 room_closed）
  * 道具：本半局 ≤3 次、同种不重复；【令】只在额度用满时才用
  * 建房 aiSides:[] + aiAgentFor 防死锁；startInning 显式传 1 才打满全场
  * 大会 --once：打完本届即退（记 edition，届次切换/no_cup 退出）

凭证（二选一）：
  1) 环境变量 RA_AGENT_ID / RA_AGENT_KEY（可选 RA_AGENT_NAME 显示名）
  2) 同目录 agent_key.txt：首行=显示名，其后 agent_id=xxx / agent_key=xxx

用法：
  python -u minimal_bot.py selfplay  [innings] [startInning]
  python -u minimal_bot.py duel      <liveId> [side]
  python -u minimal_bot.py duel-create [innings] [startInning=1]
  python -u minimal_bot.py cup       [队名] [--once]
环境变量：RA_BASE（换端点）、RA_STEP_DELAY=0（零延迟）、PYTHONIOENCODING=utf-8（Windows 中文）
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = os.environ.get("RA_BASE", "https://ace.yakidev.top").rstrip("/") + "/api/ai"
STEP_DELAY = float(os.environ.get("RA_STEP_DELAY", "2.5"))


def load_creds():
    """优先环境变量；否则读 agent_key.txt（首行显示名 + key=value）。"""
    aid = os.environ.get("RA_AGENT_ID")
    akey = os.environ.get("RA_AGENT_KEY")
    name = os.environ.get("RA_AGENT_NAME", "")
    p = os.path.join(HERE, "agent_key.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k == "agent_id":
                        aid = aid or v
                    elif k == "agent_key":
                        akey = akey or v
                elif not name:
                    name = line
    if not aid or not akey:
        sys.exit("缺少凭证：设置 RA_AGENT_ID/RA_AGENT_KEY 或提供 agent_key.txt")
    return aid, akey, (name.strip() or "AI队")


AGENT_ID, AGENT_KEY, AGENT_NAME = load_creds()


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def post(payload, timeout=10, retries=1):
    """POST /api/ai → (http_status, dict)。网络异常一律吞掉，返回 (None,{ok:False})。"""
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    last = (None, {"ok": False, "reason": "http_error"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode() or "{}")
            except Exception:
                last = (e.code, {"ok": False, "reason": "http_error"})
        except Exception as e:   # URLError / socket.timeout / 连接重置 …
            last = (None, {"ok": False, "reason": str(e)[:120]})
            if attempt < retries:
                time.sleep(1)
    return last


# ---------------------------------------------------------------- 决策
def _sit(d):
    return d.get("situation") or {}, d.get("items") or {}


def choose_pitch(d):
    sit, _ = _sit(d)
    me, opp = sit.get("scoreMe"), sit.get("scoreOpp")
    late = (sit.get("inning") or 1) >= 7
    if me is not None and opp is not None:
        if me < opp:
            return "ss"                      # 落后：逼 contact 造出局
        if me > opp and late:
            return "ss"                      # 后段领先：尽快结算
    return "bs"                              # 平衡（与服务端兜底一致）


def want_bs(d):
    sit, _ = _sit(d)
    bases = sit.get("bases") or [False, False, False]
    me, opp = sit.get("scoreMe"), sit.get("scoreOpp")
    if any(bases):
        return True
    if me is not None and opp is not None and me < opp:
        return True
    return False


def decide_item(d):
    """返回 (op, extra) 或 (None, None)。只在局面合法用道具时被调用。"""
    sit, items = _sit(d)
    stock = items.get("stock") or {}
    half = items.get("halfUsed") or {}
    used = set(half.get("used") or [])
    cap = (items.get("rules") or {}).get("skillsPerHalf", 3)
    if half.get("count", 0) >= cap:
        return (None, None)                  # 本半局额度已用满
    bases = sit.get("bases") or [False, False, False]
    outs = sit.get("outs", 0)
    me, opp = sit.get("scoreMe"), sit.get("scoreOpp")
    behind = (me is not None and opp is not None and me < opp)

    # 1) 盗垒：一垒有人 且 二垒空（否则 condition_failed）
    if stock.get("steal", 0) > 0 and "steal" not in used and bases[0] and not bases[1] and outs < 2:
        return ("item", {"itemId": "steal"})
    # 2) 装棒：有跑者或落后 → 本打席 1B 自动升 2B
    if (stock.get("bat", 0) > 0 and "bat" not in used and not items.get("batArmed")
            and (any(bases) or behind)):
        return ("item", {"itemId": "bat"})
    # 3) 令：仅当额度用满才用（实测「bat→ling」无脑连发多半是「沟通无效」，纯浪费）
    if stock.get("ling", 0) > 0 and "ling" not in used and half.get("count", 0) >= cap:
        return ("item", {"itemId": "ling"})
    # 4) 牺牲推进
    if (stock.get("sac", 0) > 0 and "sac" not in used and outs < 2
            and (bases[0] or bases[1]) and not bases[2]):
        return ("item", {"itemId": "sac"})
    # 5) 落后且有跑者：迷雾 / 抡 搏一把
    if behind and any(bases):
        if stock.get("mist", 0) > 0 and "mist" not in used:
            return ("item", {"itemId": "mist"})
        if stock.get("lun", 0) > 0 and "lun" not in used:
            return ("item", {"itemId": "lun"})
    return (None, None)


def decide(d, allowed, ban=None):
    """规则模式核心：allowedActions + 局面 → 单一 (op, extra)。"""
    a = set(allowed) - set(ban or ())
    sit, _ = _sit(d)
    if "init" in a:
        return ("init", {})
    if "duelHalfStart" in a:
        return ("duelHalfStart", {})
    if "setPitch" in a:
        return ("setPitch", {"pitch": choose_pitch(d)})
    if "item" in a:
        op, extra = decide_item(d)
        if op and op not in (ban or ()):
            return (op, extra)
    if "setBS" in a and not sit.get("bsEnabled") and not sit.get("plate") and want_bs(d):
        return ("setBS", {"bsEnabled": True})
    if "swing" in a or "read" in a:
        strikes, balls = sit.get("strikes", 0), sit.get("balls", 0)
        bases = sit.get("bases") or [False, False, False]
        me, opp = sit.get("scoreMe"), sit.get("scoreOpp")
        if strikes >= 2:
            op = "swing"                     # 两好球必打，防三振
        elif balls >= 3:
            op = "read"                      # 三坏球必看，等保送
        elif any(bases) or (me is not None and opp is not None and me <= opp):
            op = "swing"
        else:
            op = "read"
        if op in a:
            return (op, {})
    if "take1B" in a or "roll2" in a:
        outs, bases = sit.get("outs", 0), sit.get("bases") or [False, False, False]
        op = "take1B" if (outs >= 2 or not any(bases)) else "roll2"
        if op in a:
            return (op, {})
    if "roll" in a:
        return ("roll", {})
    return (allowed[0], {}) if allowed else (None, None)


def take_turn(key, side, d):
    """执行一步；被拒则换动作重试，3 次后强制 roll。绝不因一次失败放弃整场。"""
    allowed = d.get("allowedActions") or []
    banned = []
    for _ in range(3):
        op, extra = decide(d, allowed, ban=banned)
        if op is None:
            return "acted"
        st, r = post({"action": "act", "key": key, "op": op, **extra})
        if st is None:
            log("[网络失败] %s 未送达，重读局面" % op)
            return "retry"
        if r.get("ok"):
            log("[%s] %s %s · %s" % (side, op, json.dumps(extra, ensure_ascii=False), r.get("event") or ""))
            return "acted"
        log("[拒] %s -> %s，改判重试" % (op, r.get("reason")))
        if r.get("reason") == "not_your_turn":
            return "acted"
        banned.append(op)
    if "roll" in allowed:
        st, r = post({"action": "act", "key": key, "op": "roll"})
        if st is not None and r.get("ok"):
            log("[%s] roll（兜底）· %s" % (side, r.get("event") or ""))
    return "acted"


def play_loop(key, side, live_id):
    while True:
        st, d = post({"action": "state", "key": key})
        if not d.get("ok"):
            time.sleep(3)
            continue
        if d.get("matchStatus") == "ended":
            log("对局结束 liveId=%s winner=%s"
                % (live_id, (d.get("situation") or {}).get("winner") or d.get("winner")))
            return
        if d.get("myTurn") and d.get("allowedActions"):
            res = take_turn(key, side, d)
            time.sleep(0.5 if res == "retry" else STEP_DELAY)
            continue
        post({"action": "heartbeat", "key": key})   # 等待期间保活，防 room_closed
        time.sleep(2.5)


# ---------------------------------------------------------------- 入口
def join(live_id, side):
    st, d = post({"action": "join", "agentId": AGENT_ID, "key": AGENT_KEY,
                  "liveId": live_id, "side": side, "name": AGENT_NAME})
    if d.get("ok") and d.get("key"):
        return d["key"]
    if d.get("reason") == "seat_taken":            # 已占该席（如进程重启）→ 回退 session
        st, d = post({"action": "session", "agentId": AGENT_ID, "key": AGENT_KEY,
                      "liveId": live_id, "side": side})
        if d.get("ok") and d.get("key"):
            return d["key"]
    log("join 失败 %s" % json.dumps(d, ensure_ascii=False)[:200])
    return None


def run_duel_create(innings=9, start_inning=1, side="home"):
    """自建房：aiSides=[] + aiAgentFor 把主队留给自己，客队开放（平台 bot 或别的 agent 进）。"""
    st, d = post({"action": "create", "agentId": AGENT_ID, "key": AGENT_KEY,
                  "innings": innings, "startInning": start_inning,
                  "aiSides": [], "aiAgentFor": {side: AGENT_ID},
                  "homeName": AGENT_NAME, "awayName": "AI客队"})
    if not d.get("ok"):
        log("create 失败 %s" % json.dumps(d, ensure_ascii=False)[:200])
        return
    live_id = d["liveId"]
    log("建房成功 liveId=%s（%s 局制，从第 %s 局上开始）" % (live_id, innings, start_inning))
    log("观战地址: https://ace.yakidev.top/live/%s" % live_id)
    key = join(live_id, side)
    if key:
        play_loop(key, side, live_id)


def run_cup(once=False):
    joined_edition = None
    log("进入大会循环（队名=%s，单次=%s）" % (AGENT_NAME, once))
    while True:
        st, d = post({"action": "cupMySchedule", "agentId": AGENT_ID, "key": AGENT_KEY})
        if not d.get("ok"):
            time.sleep(10)
            continue
        status, ed = d.get("status"), d.get("edition")
        if joined_edition is None and status in ("registered", "scheduled"):
            joined_edition = ed
        if once and joined_edition is not None and (status == "no_cup" or ed != joined_edition):
            log("[大会] 本届结束/届次切换，单次模式退出")
            return
        if status == "open":
            st2, r = post({"action": "cupSignup", "agentId": AGENT_ID, "key": AGENT_KEY, "name": AGENT_NAME})
            if r.get("ok"):
                joined_edition = ed
                log("[大会] 报名成功")
            else:
                log("[大会] 报名被拒 %s" % json.dumps(r, ensure_ascii=False)[:160])
            time.sleep(10)
        elif status == "scheduled":
            ms = [m for m in (d.get("matches") or []) if m.get("status") in ("scheduled", "playing")]
            if not ms:
                time.sleep(15)
                continue
            m = ms[0]
            live_id = m.get("liveId")
            my_side = m.get("side") or m.get("mySide")
            log("[大会] 进场 liveId=%s mySide=%s 对手=%s" % (live_id, my_side, m.get("opponent")))
            key = join(live_id, my_side)
            if key:
                play_loop(key, my_side, live_id)
            time.sleep(10)
        else:
            time.sleep(30 if status in ("no_cup", "external_disabled") else 15)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "duel-create"
    if cmd == "selfplay":
        inn = int(args[1]) if len(args) > 1 else 9
        sti = int(args[2]) if len(args) > 2 else inn
        st, d = post({"action": "create", "agentId": AGENT_ID, "key": AGENT_KEY,
                      "innings": inn, "startInning": sti, "aiSides": ["home", "away"],
                      "homeName": AGENT_NAME + "(主)", "awayName": AGENT_NAME + "(客)"})
        if not d.get("ok"):
            log("create 失败 %s" % json.dumps(d, ensure_ascii=False)[:200])
            return
        keys = {k["side"]: k["key"] for k in d.get("keys") or []}
        live = d["liveId"]
        log("自对弈房 liveId=%s" % live)
        while True:
            acted = False
            for side, key in keys.items():
                st, d = post({"action": "state", "key": key})
                if not d.get("ok"):
                    continue
                if d.get("matchStatus") == "ended":
                    log("对局结束 liveId=%s winner=%s"
                        % (live, (d.get("situation") or {}).get("winner")))
                    return
                if d.get("myTurn") and d.get("allowedActions"):
                    take_turn(key, side, d)
                    time.sleep(STEP_DELAY)
                    acted = True
                    break
            if not acted:
                for k in keys.values():
                    post({"action": "heartbeat", "key": k})
                time.sleep(1)
    elif cmd == "duel":
        if len(args) < 2:
            sys.exit("用法: minimal_bot.py duel <liveId> [side]")
        side = args[2] if len(args) > 2 else "away"
        key = join(args[1], side)
        if key:
            play_loop(key, side, args[1])
    elif cmd == "duel-create":
        inn = int(args[1]) if len(args) > 1 else 9
        sti = int(args[2]) if len(args) > 2 else 1
        run_duel_create(inn, sti)
    elif cmd == "cup":
        run_cup(once="--once" in args)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

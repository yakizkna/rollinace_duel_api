#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rollin' Ace —— 棒球龙虾 最简规则机器人（demo 版 / minimal）

本文件是 `ra_rule_bot.py` 的**策略抽离版**：只保留「能跑通一局」的最小决策集，
供 duel_api 文档作为**入门 demo** 使用（不含任何进阶策略）。

保留的决策（全部硬编码、无建模、无道具、无局势判断）：
  1) set_pitch  → 固定 "bs"（默认均衡投球档）
  2) 好坏球模式 → **不开**（不开好坏球模式，纯 roll 走普通模式）
  3) swing/read → 若服务端仍下发好坏球动作，一律 "roll"/"swing"（纯 roll，不看球）
  4) take1b/roll2 → 固定 "take1b"（安打保底走垒，不搏 roll2）
  5) init / duel_half_start / roll → 直接执行

已移除（相对 ra_rule_bot.py）：
  - choose_pitch 的五档决策（bb/ss/bs 局势判断）
  - want_bs 好坏球开启判断（对手档位建模）
  - decide_item 全部道具（bat/steal/ling/sac/mist/lun）
  - decide_bs 好坏球打/看决策表
  - choose_room / run_patrol 巡房、run_cup 大会、cmd_tour 等上层编排
  - 对手建模 _OPP / observe_pitch / _obs_opp_batting

保留的基础设施（与 ra_rule_bot.py 一致，便于对照）：
  - 凭证解析 agent_key.txt（YAML 多块 / key=value / 位置格式）
  - session 持久化 .ra_sessions.json（平台契约：比赛中不可重签）
  - post() 全异常捕获 + rtt 附带
  - play_loop 走棋循环（含 state 失败保护、no_show 保活、waiting 等对手）

用法：
  python3 ra_rule_bot_min.py host [innings] [start_inning]   # 建房主队，等对手 join
  python3 ra_rule_bot_min.py duel <live_id> [--wait]         # 加入已有房（只能客队 away）
  # 环境：RA_ENV=正式|独立版 选凭证块；RA_BASE 选站点；RA_STEP_DELAY 调节流

⚠️ 与官方示例的差异（收录说明，2026-09-16 由 RA 侧补充）：
  · 凭证：本文件读**同目录 `agent_key.txt`**（支持 YAML 多块 / `key=value` / 位置格式，用 `RA_ENV` 选块），
    官方 `examples/python/ai_duel_bot.py` 走**环境变量** `AI_AGENT_ID` / `AI_AGENT_KEY` —— 两者不通用。
  · 默认站点：本文件 `https://ra.yakidev.top`（独立版）；官方示例默认正式站。均可用 `RA_BASE` 覆盖。
  · 收录时 RA 侧只做了三处**最小适配**（其余与作者原版一致）：① 本说明；② `act` 日志附 `rtt`；
    ③ **参数与凭证校验**（非法参数给用法提示、缺 `agent_key.txt` 给格式示例，均不再崩栈 / 不再在导入阶段就崩）。
  · ⚠️ 若本机开启**系统代理**：Python `urllib` 会自动走代理并可能超时（`curl` 不受影响）——
    可在文件顶部加 `urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))`，或设 `no_proxy=*`。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GAMELOG = os.path.join(HERE, "min.log")

BASE = os.environ.get("RA_BASE", "https://ra.yakidev.top").rstrip("/")
API = f"{BASE}/api/ai"
STEP_DELAY = float(os.environ.get("RA_STEP_DELAY", "2.5"))

SESSION_FILE = os.path.join(HERE, ".ra_sessions.json")
SESSION_TTL = float(os.environ.get("RA_SESSION_TTL", str(6 * 3600)))


# ---------------------------------------------------------------------------
# 凭证：只从同目录 agent_key.txt 读取（永不硬编码 / 永不入库 / 不打印明文）
# ---------------------------------------------------------------------------
def _parse_yaml_blocks(text):
    """解析 YAML 风格多块凭证，返回 [dict, ...]（仅扁平结构，不引入第三方依赖）。"""
    blocks = []
    cur = None
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.rstrip(":") in ("agents", "agent"):
            continue
        if s.startswith("-"):
            if cur:
                blocks.append(cur)
            cur = {}
            s = s[1:].strip()
            if not s:
                continue
        if cur is None:
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            cur[k.strip().lower()] = v.strip()
    if cur:
        blocks.append(cur)
    return blocks


def load_creds():
    """解析 agent_key.txt，返回 (agent_id, agent_key, name)。支持三种格式：
      A) key=value 行（agent_id=/agent_key=）+ 首行纯文本显示名
      B) 纯位置格式：行1=显示名，行2=agent_id，行3=agent_key
      C) YAML 多块（agent: / - env: / name: / id: / token:），按 RA_ENV 选块
    """
    p = os.path.join(HERE, "agent_key.txt")
    with open(p, encoding="utf-8") as f:
        text = f.read()

    if "agents:" in text or "agent:" in text or ("token:" in text and "id:" in text):
        blocks = _parse_yaml_blocks(text)
        if blocks:
            want = (os.environ.get("RA_ENV") or "").strip()
            chosen = None
            if want:
                for b in blocks:
                    if (b.get("env") or "").strip() == want:
                        chosen = b
                        break
            if chosen is None:
                chosen = blocks[0]
            return (chosen.get("id"),
                    chosen.get("token") or chosen.get("key"),
                    (chosen.get("name") or "").strip())

    pairs = {}
    positional = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            pairs[k.strip().lower()] = v.strip()
        else:
            positional.append(line)
    name = pairs.get("name") or (positional[0] if positional else "")
    rest = positional[1:] if positional and not pairs.get("name") else positional
    agent_id = pairs.get("agent_id") or pairs.get("id") or (rest[0] if rest else None)
    agent_key = pairs.get("agent_key") or pairs.get("token") or (rest[1] if len(rest) >= 2 else None)
    return agent_id, agent_key, (name or "").strip()


try:
    AGENT_ID, AGENT_KEY, AGENT_NAME = load_creds()
    _CREDS_ERR = None
except Exception as _e:            # 收录适配③：缺 agent_key.txt 时不在此崩栈，跑命令时再给明确提示
    AGENT_ID = AGENT_KEY = None
    AGENT_NAME = "ra_rule_bot_min"
    _CREDS_ERR = _e
AGENT_NAME = AGENT_NAME or "棒球龙虾"


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(GAMELOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# session 持久化（平台契约：外部 AI 同时只能参加一场，且比赛中不可重签 session）
# ---------------------------------------------------------------------------
def _load_sessions():
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_session(live_id, key, side):
    if not live_id or not key:
        return
    try:
        data = _load_sessions()
        data[str(live_id)] = {"key": key, "side": side, "ts": int(time.time())}
        tmp = SESSION_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SESSION_FILE)
        log(f"[session] 已持久化 live_id={live_id} side={side}")
    except Exception as e:
        log(f"⚠️[session] 持久化失败（不止损走棋）：{e}")


def load_session(live_id):
    rec = _load_sessions().get(str(live_id))
    if not (isinstance(rec, dict) and rec.get("key")):
        return None
    ts = rec.get("ts")
    if isinstance(ts, (int, float)) and ts > 0:
        if time.time() - ts > SESSION_TTL:
            clear_session(live_id)
            return None
    return rec


def clear_session(live_id):
    try:
        data = _load_sessions()
        if str(live_id) in data:
            del data[str(live_id)]
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTTP 管线：全异常捕获（含 urllib 网络超时），失败返回 (None,{ok:False})
# ---------------------------------------------------------------------------
_LAST_RTT = [0]
_HEARTBEAT_IDLE_DEFAULT = float(os.environ.get("RA_HEARTBEAT_IDLE", "25"))
_last_action_ts = [0.0]


def keepalive_heartbeat(key, idle_default=None):
    """仅当静默超过阈值才补发 heartbeat（默认 25s < 平台 30s 在线超时）。"""
    th = _HEARTBEAT_IDLE_DEFAULT if idle_default is None else idle_default
    if time.monotonic() - _last_action_ts[0] < th:
        return False
    try:
        post({"action": "heartbeat", "key": key})
        return True
    except Exception:
        return False


def post(payload, timeout=10, retries=1):
    if payload.get("key") and _LAST_RTT[0] > 0 and "rtt" not in payload:
        payload = dict(payload)
        payload["rtt"] = _LAST_RTT[0]
    measure = bool(payload.get("key"))
    t0 = time.monotonic() if measure else 0.0
    if measure:
        _last_action_ts[0] = time.monotonic()
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    last = (None, {"ok": False, "reason": "http_error"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode() or "{}")
                if measure and d.get("ok"):
                    dt = int((time.monotonic() - t0) * 1000)
                    if dt > 0:
                        _LAST_RTT[0] = dt
                return r.status, d
        except urllib.error.HTTPError as e:
            try:
                d = json.loads(e.read().decode() or "{}")
                if measure and d.get("ok"):
                    dt = int((time.monotonic() - t0) * 1000)
                    if dt > 0:
                        _LAST_RTT[0] = dt
                return e.code, d
            except Exception:
                last = (e.code, {"ok": False, "reason": "http_error"})
        except Exception as e:  # 网络超时抛 URLError，必须全捕获
            last = (None, {"ok": False, "reason": "http_error", "detail": str(e)[:120]})
            if attempt < retries:
                time.sleep(1)
    return last


# ---------------------------------------------------------------------------
# 局面工具
# ---------------------------------------------------------------------------
def _sit(d):
    return (d.get("situation") or {}), (d.get("items") or {})


def is_ended(d):
    sit = d.get("situation") or {}
    return (d.get("room_closed")
            or d.get("match_status") == "ended"
            or sit.get("winner")
            or sit.get("duel_end") == "match")


# ---------------------------------------------------------------------------
# ★ 最简决策：无策略、无建模、无道具
# ---------------------------------------------------------------------------
def decide_rule(d, allowed, side):
    """依据 allowed_actions 返回单一 (op, extra)。

    ── 全部硬编码 ──
      set_pitch       → "bs"（默认均衡投球档，不按局势切换）
      好坏球模式       → 不开（want_bs 恒 False）
      swing / read    → "swing"（纯 roll，不做看球保送判断）
      take1b / roll2  → "take1b"（安打保底走垒，不搏 roll2）
      init / duel_half_start / roll → 直接执行
    """
    a = set(allowed)
    if "init" in a:
        return ("init", {})
    if "duel_half_start" in a:
        return ("duel_half_start", {})
    if "set_pitch" in a:
        return ("set_pitch", {"pitch": "bs"})     # ① 默认投球策略 bs
    # ② 好坏球关：不调 set_bs（也不开启），直接靠 roll
    # ③ 若服务端仍下发 swing/read（bs 已开时），一律 swing（纯 roll，不看球）
    if "swing" in a or "read" in a:
        return ("swing", {})
    # ④ take1b / roll2 → 固定 take1b
    if "take1b" in a or "roll2" in a:
        return ("take1b", {})
    if "roll" in a:
        return ("roll", {})
    if allowed:
        return (allowed[0], {})
    return (None, None)


RECOVERABLE = {
    "not_defender", "not_attacker", "not_my_turn", "not_your_turn", "turn_not_ready",
    "illegal_op", "version_conflict", "phase_mismatch", "condition_failed",
    "left_queue", "other_side_left", "waiting_pitch",
}


def rule_take_turn(key, side, d):
    allowed = d.get("allowed_actions") or []
    op, extra = decide_rule(d, allowed, side)
    if op is None:
        return "acted", None
    st, r = post({"action": "act", "key": key, "op": op, **extra})
    if st is None:
        log(f"[规则·网络失败] {op} 未送达，重读局面重试")
        return "retry", op
    if r.get("ok"):
        # 收录适配②：日志附 rtt（post() 已测得），便于观察「慢在哪一步」
        log(f"[规则] {side} {op} {json.dumps(extra, ensure_ascii=False)} · {r.get('event') or ''}"
            f" · rtt={_LAST_RTT[0]}ms")
        return "acted", op
    reason = r.get("reason")
    if reason in RECOVERABLE:
        log(f"[规则·自纠] {op} -> {reason}，重读局面")
        return "retry", op
    log(f"[规则·致命] {op} -> {reason}（退出走棋循环）")
    return "stop", op


# ---------------------------------------------------------------------------
# 走棋循环
# ---------------------------------------------------------------------------
def play_loop(key, side, live_id):
    poll = 0
    last_progress = time.time()
    NO_SHOW_FAST = float(os.environ.get("RA_NOSHOW_FAST", "180"))
    STATE_FAIL_ABORT = int(os.environ.get("RA_STATE_FAIL_ABORT", "100"))
    state_fail = 0
    while True:
        st, d = post({"action": "state", "key": key})
        if not d.get("ok"):
            state_fail += 1
            if state_fail % 10 == 1:
                log(f"⚠️ [state 异常] 连续 {state_fail} 次失败 reason={d.get('reason')} live_id={live_id}")
            if state_fail >= STATE_FAIL_ABORT:
                log(f"❌ [state 异常] 连续 {state_fail} 次失败，清 session 退出 live_id={live_id}")
                clear_session(live_id)
                return
            time.sleep(3)
            continue
        state_fail = 0
        if is_ended(d):
            sit = d.get("situation") or {}
            log(f"对局结束 live_id={live_id} side={side} winner={sit.get('winner') or d.get('winner')}")
            log(f"终局 {sit.get('team_me')}={sit.get('score_me')} : "
                f"{sit.get('team_opp')}={sit.get('score_opp')}")
            clear_session(live_id)
            return
        if d.get("allowed_actions") or d.get("my_turn") or d.get("to_move") == side:
            last_progress = time.time()
        idle = time.time() - last_progress
        if idle >= NO_SHOW_FAST and poll % 3 == 0:
            log(f"⚠️ [no_show 保护] 已静默 {idle:.0f}s，提速轮询 + 抢发 heartbeat")
        # 对手未进房：保活等 join，绝不因「无 allowed_actions」退出
        if not d.get("allowed_actions") and d.get("room_status") in ("waiting", "pending"):
            poll += 1
            keepalive_heartbeat(key)
            time.sleep(3.0)
            continue
        if d.get("allowed_actions"):
            res, _ = rule_take_turn(key, side, d)
            if res == "retry":
                time.sleep(0.5)
                continue
            if res == "stop":
                return
            time.sleep(STEP_DELAY)
            continue
        poll += 1
        if poll % 4 == 0:
            sit = d.get("situation") or {}
            log(f"[状态] my_turn={d.get('my_turn')} to_move={d.get('to_move')} "
                f"{sit.get('team_me')}={sit.get('score_me')}:{sit.get('team_opp')}={sit.get('score_opp')} "
                f"局={sit.get('inning')}{'下' if sit.get('is_bottom') else '上'}")
        keepalive_heartbeat(key)
        time.sleep(1.0 if idle >= NO_SHOW_FAST else 2.5)


# ---------------------------------------------------------------------------
# 场景 1：建房（主队），等对手 join
# ---------------------------------------------------------------------------
def host_match(innings=9, start_inning=None):
    sti = start_inning if start_inning is not None else 1
    st, d = post({
        "action": "create", "agent_id": AGENT_ID, "key": AGENT_KEY,
        "innings": innings, "start_inning": sti,
        "ai_sides": ["home"],
        "home_name": AGENT_NAME,   # 显式传必须 == 注册名，否则 400 name_mismatch
    })
    if not d.get("ok"):
        log(f"create 失败 {json.dumps(d, ensure_ascii=False)[:200]}")
        return
    live = d.get("live_id")
    keys = d.get("keys") or []
    key = (keys[0].get("key") if keys else None)
    if not key:
        log("create 未返回 home key（异常）")
        return
    save_session(live, key, "home")
    log(f"主队房 live_id={live} innings={innings} start_inning={sti} 观战: {BASE}/live/{live}")
    log("等待对手 join 客队…")
    play_loop(key, "home", live)


# ---------------------------------------------------------------------------
# 场景 2：加入已有房（只能客队 away）
# ---------------------------------------------------------------------------
def run_duel(live_id, side="away", wait=False):
    if side != "away":
        log(f"⚠️ 外部 AI 只能以客队(away)加入，改用 away（传入 side={side}）")
        side = "away"
    s = load_session(live_id)
    if s and s.get("key"):
        log(f"[session] 命中本地持久化 live_id={live_id} side={s.get('side')}，直接复用")
        key = s["key"]
        side = s.get("side") or side
    else:
        st, d = post({"action": "join", "agent_id": AGENT_ID, "key": AGENT_KEY,
                      "live_id": live_id, "side": side, "name": AGENT_NAME})
        if not d.get("ok") or not d.get("key"):
            log(f"join 失败 {json.dumps(d, ensure_ascii=False)[:200]}")
            return
        key = d["key"]
        save_session(live_id, key, side)
    play_loop(key, side, live_id)


def _int_arg(v, default=None):
    """收录适配③：把命令行数字参数转 int；非法时返回 default（由调用方提示用法，不再 ValueError 崩栈）。"""
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _need_creds():
    """收录适配③：需要凭证的命令在缺 agent_key.txt 时给明确提示（而不是堆栈）。"""
    if _CREDS_ERR is None:
        return True
    print("❌ 未读到同目录 `agent_key.txt`（本文件仅从该文件读取凭证）：%s" % _CREDS_ERR)
    print("   格式示例（YAML 多块，`RA_ENV` 选块）：\n"
          "   agents:\n   - env: 独立版\n     name: <与注册名一致>\n     id: ag_xxxxxxxxxx\n     token: <agent_key>")
    return False


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "host":
        # 先校验参数（提示更贴切），再校验凭证
        inn = _int_arg(sys.argv[2]) if len(sys.argv) > 2 else 9
        sti = _int_arg(sys.argv[3]) if len(sys.argv) > 3 else None
        if (len(sys.argv) > 2 and inn is None) or (len(sys.argv) > 3 and sti is None):
            print("用法: ra_rule_bot_min.py host [innings] [start_inning]   # 两者都必须是整数")
            return
        if not _need_creds():
            return
        host_match(inn, sti)
    elif cmd == "duel":
        if len(sys.argv) < 3:
            print("用法: ra_rule_bot_min.py duel <live_id> [side]")
            return
        if not _need_creds():
            return
        args = [x for x in sys.argv[2:] if not x.startswith("--")]
        side = args[1] if len(args) > 1 else "away"
        run_duel(args[0], side, wait="--wait" in sys.argv)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

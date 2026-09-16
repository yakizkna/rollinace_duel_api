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
  · 本版为**作者加固版**（采纳 RA 侧 5 条优化建议：create 完整冲突房号 + backoff 重试、`is_ended` 补
    `room_status`/`match_status`、session 命中先探测、`roll` 优先于 `swing/read`、参数/凭证不崩栈）。
    收录时 RA 侧另做**两处**最小适配（其余与作者版一致）：① 本说明；② `act` 日志附 `rtt`。
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

    2026-09-16 加固（ra_agent No.115 建议 ⑤）：文件缺失时**不崩栈**，返回 (None,None,None)，
    由 main() 统一给出格式示例（否则连用法都看不到）。
    """
    p = os.path.join(HERE, "agent_key.txt")
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"⚠️ 未找到凭证文件 {p}（{e.__class__.__name__}）", flush=True)
        return (None, None, None)

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


AGENT_ID, AGENT_KEY, AGENT_NAME = load_creds()
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
    """对局/房间是否已结束。

    2026-09-16 加固（ra_agent No.115 建议 ②）：补 room_status in (closed/ended) 与
    match_status in (ended/closed)。否则对手退出 / 房间被平台回收时，本端会一直
    拿不到 allowed_actions，空转到 STATE_FAIL_ABORT（100 次≈5 分钟）才退出。
    """
    sit = d.get("situation") or {}
    rs = d.get("room_status")
    ms = d.get("match_status")
    return (d.get("room_closed")
            or ms in ("ended", "closed")
            or rs in ("closed", "ended", "finished")
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
    # ③【2026-09-16 加固·建议④】roll 优先于 swing/read：
    #    若某阶段同时下发 roll 与 swing/read，旧顺序会选 swing（与「纯 roll、不看球」声明不符）。
    #    现改为 roll 最先判断，确保语义一致。
    if "roll" in a:
        return ("roll", {})
    # ④ 兜底：若服务端仅下发 swing/read（无 roll），一律 swing（纯 roll，不看球）
    if "swing" in a or "read" in a:
        return ("swing", {})
    # ⑤ take1b / roll2 → 固定 take1b
    if "take1b" in a or "roll2" in a:
        return ("take1b", {})
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
def create_room(innings, sti, max_retry=3):
    """建房（create），失败带房号指出 + 有限 backoff 重试。

    2026-09-16 加固（ra_agent No.115 建议 ①）：
      - already_in_duel 时打印 conflict_live_id（不被截断），引导先结束旧房；
      - 瞬时失败有限重试（1s/2s/4s），避免一次网络抖动即放弃。
    返回 (d, key)；失败返回 (最后响应, None)。
    """
    payload = {
        "action": "create", "agent_id": AGENT_ID, "key": AGENT_KEY,
        "innings": innings, "start_inning": sti,
        "ai_sides": ["home"],
        "home_name": AGENT_NAME,   # 显式传必须 == 注册名，否则 400 name_mismatch
    }
    st, d = (None, {"ok": False, "reason": "not_attempted"})
    for attempt in range(max_retry + 1):
        st, d = post(payload)
        if d.get("ok"):
            return d, None
        reason = d.get("reason")
        # 已有一场未结束：房号必须完整打印（最需要它时别截断）
        if reason == "already_in_duel":
            conf = d.get("conflict_live_id")
            log(f"⚠️ create 被拒 already_in_duel，占用中的房 live_id={conf}"
                f"（同 agent 只能一场，含 waiting）→ 先结束本场或换身份再建")
            return d, None
        # 参数类硬错误：重试无意义
        if reason in ("bad_seat", "name_mismatch", "bad_innings", "bad_request",
                      "guest_forbidden", "quota_exceeded", "forbidden"):
            log(f"❌ create 失败（不可重试）reason={reason}：{json.dumps(d, ensure_ascii=False)[:200]}")
            return d, None
        if attempt < max_retry:
            backoff = 2 ** attempt          # 1s / 2s / 4s
            log(f"create 失败 reason={reason}，{backoff}s 后重试（{attempt+1}/{max_retry}）")
            time.sleep(backoff)
    log(f"❌ create 连续失败：{json.dumps(d, ensure_ascii=False)[:200]}")
    return d, None


def host_match(innings=9, start_inning=None):
    sti = start_inning if start_inning is not None else 1
    d, _ = create_room(innings, sti)
    if not d.get("ok"):
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
def _probe_session_key(key, live_id):
    """探测已持久化的 session key 是否仍有效（一次 state）。

    2026-09-16 加固（ra_agent No.115 建议 ③）：进程被 kill 后残留的 key 可能已失效，
    直接拿去走棋会先撞 401 空转。命中本地记录后先探一次：有效则复用，无效即清掉重新 join。
    返回 True=有效 / False=无效或不确定（由调用方决定是否重签）。
    """
    st, d = post({"action": "state", "key": key}, timeout=8)
    if st is None:
        log("[session] 探测未送达（网络问题），按有效处理并继续")
        return True          # 网络抖动不当失效，避免误清
    if d.get("ok"):
        return True
    reason = d.get("reason")
    # 明确的鉴权/会话失效 → 判无效；其余（版本冲突等）仍算有效
    if reason in ("no_session", "invalid_session", "unauthorized", "auth_failed",
                  "session_expired", "not_found", "bad_key"):
        log(f"[session] 探测失败 reason={reason} → 判定 key 失效")
        return False
    log(f"[session] 探测返回 reason={reason}，按有效处理")
    return True


def run_duel(live_id, side="away", wait=False):
    if side != "away":
        log(f"⚠️ 外部 AI 只能以客队(away)加入，改用 away（传入 side={side}）")
        side = "away"
    s = load_session(live_id)
    if s and s.get("key") and _probe_session_key(s["key"], live_id):
        log(f"[session] 命中本地持久化 live_id={live_id} side={s.get('side')}，探测有效 → 复用")
        key = s["key"]
        side = s.get("side") or side
    else:
        if s and s.get("key"):
            log(f"[session] 本地记录 live_id={live_id} 已失效，清除后重新 join")
            clear_session(live_id)
        st, d = post({"action": "join", "agent_id": AGENT_ID, "key": AGENT_KEY,
                      "live_id": live_id, "side": side, "name": AGENT_NAME})
        if not d.get("ok") or not d.get("key"):
            log(f"join 失败 {json.dumps(d, ensure_ascii=False)[:200]}")
            return
        key = d["key"]
        save_session(live_id, key, side)
    play_loop(key, side, live_id)


USAGE = """用法:
  python3 ra_rule_bot_min.py host [innings] [start_inning]   # 建房主队，等对手 join
  python3 ra_rule_bot_min.py duel <live_id> [side]           # 加入已有房（只能客队 away）

例:
  RA_ENV=独立版 python3 ra_rule_bot_min.py host 3 1
  RA_ENV=独立版 python3 ra_rule_bot_min.py duel RJ66H6AK"""


def _require_creds():
    """2026-09-16 加固（ra_agent No.115 建议 ⑤）：凭证缺失给出格式示例而非直接崩栈。"""
    if AGENT_ID and AGENT_KEY:
        return True
    log("❌ 未读到有效凭证（agent_key.txt 缺失或缺 id/token）")
    print("\n请在脚本同目录放 agent_key.txt，支持以下任一格式：\n"
          "  A) agent_id=ag_xxx\n     agent_key=agkey_xxx\n"
          "  B) 显示名 / ag_xxx / agkey_xxx（三行）\n"
          "  C) YAML 多块：agent:\n       - env: 独立版\n         name: 你的显示名\n"
          "         id: ag_xxx\n         token: agkey_xxx\n")
    return False


def _parse_int(v, name, default=None):
    """2026-09-16 加固（建议 ⑤）：非法参数给用法提示，不崩栈。"""
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        log(f"❌ 参数 {name} 不是合法整数：{v!r}")
        print(USAGE)
        sys.exit(2)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("host", "duel"):
        print(__doc__)
        return
    if not _require_creds():
        sys.exit(2)
    if cmd == "host":
        inn = _parse_int(sys.argv[2] if len(sys.argv) > 2 else None, "innings", 9)
        sti = _parse_int(sys.argv[3] if len(sys.argv) > 3 else None, "start_inning", None)
        host_match(inn, sti)
    elif cmd == "duel":
        if len(sys.argv) < 3:
            print(USAGE)
            return
        args = [x for x in sys.argv[2:] if not x.startswith("--")]
        side = args[1] if len(args) > 1 else "away"
        run_duel(args[0], side, wait="--wait" in sys.argv)


if __name__ == "__main__":
    main()

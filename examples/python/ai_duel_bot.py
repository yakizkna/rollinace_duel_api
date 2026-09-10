#!/usr/bin/env python3
"""Rollin Ace 第三方 AI 参考机器人 —— 极简策略 + 完整对战 / 参会流程。

这是给「第三方 AI 接入方」的**教学参考实现**：只用公开接口 `POST /api/ai`、
零第三方依赖（标准库 urllib），跑通两条完整链路：

  1. 对战房间（普通对战 / 自对弈）
  2. 参加大会（报名 → 进场 → 走棋 → 晋级续打）

---------------------------------------------------------------------------
一、接口协议速览（完整契约见 docs/AI_DUEL_API.md）
---------------------------------------------------------------------------
- 基址：POST {BASE}/api/ai，参数放 JSON body，仅 POST。
- 鉴权分两段：
    * 换票（session/create/join/list/cupSignup/cupCancel/cupMySchedule）：
      带 agentId + key（管理端「AI 管理」页分配的 agent 凭证；key 仅显示一次）。
    * 会话（state/act/heartbeat/leave）：带换票/join 返回的 session key
      （与「房间 + 阵营」绑定，跨房 403 session_mismatch）。
- 判断成功一律以响应里的 `ok == true` 为准（业务失败多为 HTTP 200 + ok:false + reason）。
- 走棋范式：先 `state` 读局面，仅当 `myTurn==true` 且 `allowedActions` 非空时 `act`；
  换边与比赛结束由服务端自动推进，机器人只需按 `allowedActions` 循环。

---------------------------------------------------------------------------
二、极简策略（够用、能打完一局；接入方按需替换 decide()）
---------------------------------------------------------------------------
  1) 关闭好坏球 —— allowed 含 setBS 且 bsEnabled 开着时，先关，回纯 roll；
  2) 普通打席 roll —— 一律 op=roll；
  3) 二选一 take1B —— phase=choose 时固定 take1B（安打保底）；
  4) 能盗就盗 —— 一垒有人且二垒空、出局 <2 时 op=item itemId=steal（引擎 canUse 把关）；
  5) 防守选投手 bs —— allowed 含 setPitch 时固定 pitch=bs。

铁律：只按服务端 allowedActions 行动，从不猜非法动作；act 失败按 reason 自纠。

---------------------------------------------------------------------------
三、用法
---------------------------------------------------------------------------
    export AI_AGENT_ID=<agent_id>       # 管理端「AI 管理」页分配
    export AI_AGENT_KEY=<agent_key>     # key 仅创建/重置时显示一次
    export RA_BASE=https://ace.yakidev.top   # 可选，默认正式环境

    python ai_duel_bot.py selfplay               # 自对弈（create → 双方走棋）
    python ai_duel_bot.py duel <liveId> [side]   # 加入对战房（默认客队 away）
    python ai_duel_bot.py cup [队名]             # 参加大会（常驻：报名→进场→走棋→晋级）
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("RA_BASE", "https://ace.yakidev.top").rstrip("/")
API = f"{BASE}/api/ai"
PITCH = "bs"  # 防守半局固定投手风格


def post(payload: dict, timeout: float = 10.0):
    """向 /api/ai 发一次 POST，返回 (http_status, json_dict)。"""
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


class Bot:
    """一个 agent 的最小机器人：持凭证，能 join 走棋、能报名大会。"""

    def __init__(self, agent_id: str, agent_key: str):
        self.agent_id = agent_id
        self.agent_key = agent_key
        self.key = None      # 当前房间的 session key
        self.live_id = None
        self.side = None

    def log(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    # ---------------- 换票 / 会话 ----------------
    def join(self, live_id: str, side: str, name: str = None):
        """join 占用席位（默认客场先攻，占位即开赛）。返回是否成功。

        name 必须与注册名一致（不一致 → 400 name_mismatch）；**不传则用注册名（推荐）**。
        """
        self.live_id, self.side = live_id, side
        payload = {"action": "join", "agentId": self.agent_id, "key": self.agent_key,
                   "liveId": live_id, "side": side}
        if name:
            payload["name"] = name
        st, d = post(payload)
        if d.get("ok") and d.get("key"):
            self.key = d["key"]
            return True
        # 断线重连：席位已被自己占用时，用 session 重签 key
        if d.get("reason") == "seat_taken":
            st, d = post({"action": "session", "agentId": self.agent_id, "key": self.agent_key,
                          "liveId": live_id, "side": side})
            if d.get("ok") and d.get("key"):
                self.key = d["key"]
                return True
        self.log(f"join 失败 {json.dumps(d, ensure_ascii=False)[:160]}")
        return False

    def state(self):
        """读取当前局面（含 myTurn / allowedActions / situation）。失败返回 None。"""
        if not self.key and not self.join(self.live_id, self.side):
            return None
        st, d = post({"action": "state", "key": self.key})
        if not d.get("ok"):
            if d.get("reason") in ("no_session", "session_expired", "session_mismatch"):
                self.key = None   # 会话失效 → 下次重连
            return None
        return d

    # ---------------- 极简策略 ----------------
    def decide(self, s):
        """按 allowedActions 挑一个动作，返回 (op, extra) 或 None（暂无可做）。"""
        sit = s.get("situation") or {}
        allowed = s.get("allowedActions") or []
        bases = sit.get("bases") or []
        bs_en = bool(sit.get("bsEnabled"))
        outs = sit.get("outs") or 0

        if "init" in allowed:                                  # 房间尚无局面，我方是进攻方
            return "init", {}
        if "duelHalfStart" in allowed:                         # 真人半局结束，接力开新半局
            return "duelHalfStart", {}
        if "setPitch" in allowed:                              # 我方防守，先选投手风格
            return "setPitch", {"pitch": PITCH}
        if "item" in allowed and bases and bases[0] and not bases[1] and outs < 2:
            return "item", {"itemId": "steal"}                 # 一垒有人且二垒空 → 盗垒
        if "take1B" in allowed:                                # 二选一固定 take1B
            return "take1B", {}
        if "setBS" in allowed and bs_en:                       # 关掉好坏球，回纯 roll
            return "setBS", {"bsEnabled": False}
        if "roll" in allowed:
            return "roll", {}
        if "read" in allowed:
            return "read", {}
        if "swing" in allowed:
            return "swing", {}
        if "roll2" in allowed:
            return "roll2", {}
        return None

    # ---------------- 单场走棋 ----------------
    def play_match(self, live_id: str, side: str):
        """join 占席后，state/act 循环打到本场结束。返回 "done" / "join_failed"。"""
        if not self.join(live_id, side):
            return "join_failed"
        self.log(f"== 开赛 liveId={live_id} side={side} ==")
        while True:
            s = self.state()
            if not s:
                time.sleep(3)
                continue
            sit = s.get("situation") or {}
            room_closed = s.get("roomClosed")
            ms = s.get("matchStatus")
            # 终态：房间关闭 / 比赛结束 / 已判胜负
            if room_closed or ms == "ended" or sit.get("winner") or sit.get("duelEnd") == "match":
                self.log(f"单局结束 roomClosed={room_closed} matchStatus={ms} "
                         f"winner={sit.get('winner') or s.get('winner')}")
                return "done"
            if not s.get("myTurn"):
                time.sleep(2.5)
                continue
            pick = self.decide(s)
            if not pick:
                time.sleep(2.0)
                continue
            op, extra = pick
            st, d = post({"action": "act", "key": self.key, "op": op, **extra})
            if d.get("ok"):
                ev = d.get("event") or ""
                self.log(f"[动] {op} {json.dumps(extra, ensure_ascii=False)}" + (f" · {ev}" if ev else ""))
                time.sleep(1.0)
            else:
                reason = d.get("reason") or ""
                if reason in ("no_session", "session_expired", "session_mismatch"):
                    self.key = None   # 触发重连
                else:
                    # 非法/局面已变/未轮到我 → 重读 state；其它语义拒绝不重试
                    self.log(f"[跳] {op} → {reason}（重读局面）")
                    time.sleep(1.0)

    # ---------------- 场景 1：自对弈 ----------------
    def self_play(self, innings: int = 3):
        """create 建自对弈房（双方 AI），用两把 key 交替走棋。"""
        st, d = post({"action": "create", "agentId": self.agent_id, "key": self.agent_key,
                      "innings": innings, "startInning": innings,
                      "aiSides": ["home", "away"], "homeName": "AI主队", "awayName": "AI客队"})
        if not d.get("ok"):
            self.log(f"create 失败 {json.dumps(d, ensure_ascii=False)[:160]}")
            return
        keys = {k["side"]: k["key"] for k in d.get("keys") or []}
        self.log(f"自对弈房 liveId={d.get('liveId')} 双方 key 已就绪")
        # 客场先攻；换边由服务端推进，这里按 state 的 toMove 切 key
        while True:
            to_move = None
            for side, key in keys.items():
                st, d = post({"action": "state", "key": key})
                if not d.get("ok"):
                    continue
                if d.get("matchStatus") == "ended":
                    self.log(f"自对弈结束 winner={d.get('winner')}")
                    return
                if d.get("toMove") == side:
                    to_move = side
                    break
            if not to_move:
                time.sleep(1)
                continue
            key = keys[to_move]
            st, d = post({"action": "state", "key": key})
            if not (d.get("ok") and d.get("myTurn") and d.get("allowedActions")):
                time.sleep(1)
                continue
            pick = self.decide(d)
            if not pick:
                time.sleep(1)
                continue
            op, extra = pick
            st, r = post({"action": "act", "key": key, "op": op, **extra})
            if r.get("ok"):
                self.log(f"[{to_move}] {op} · {r.get('event') or ''}")
            time.sleep(1)

    # ---------------- 场景 2：参加大会 ----------------
    def cup_schedule(self):
        st, d = post({"action": "cupMySchedule", "agentId": self.agent_id, "key": self.agent_key})
        return d if d.get("ok") else None

    def cup_signup(self, name: str = None):
        """报名；name 必须与注册名一致（不一致 → 400 name_mismatch），不传则用注册名。"""
        payload = {"action": "cupSignup", "agentId": self.agent_id, "key": self.agent_key}
        if name:
            payload["name"] = name
        st, d = post(payload)
        if d.get("ok") or d.get("reason") == "already_signup":
            return True
        self.log(f"报名未成功 reason={d.get('reason')}")
        return False

    def run_cup(self, name: str = None):
        """常驻盯场：无会等报名 → open 自动报名 → scheduled 进场走棋 → 晋级续打。"""
        done_lives = set()
        idle = 0
        self.log(f"参会盯场启动 agent={self.agent_id}")
        while True:
            idle += 1
            sch = self.cup_schedule()
            if not sch:
                time.sleep(5)
                continue
            st = sch.get("status")
            if st in ("no_cup", "external_disabled"):
                if idle % 12 == 1:
                    self.log(f"无本届大会（{st}）→ 等待报名/下一届…")
                time.sleep(8)
                continue
            if st == "open":
                if idle % 6 == 1:
                    self.cup_signup(name)   # 幂等：already_signup 也视为已报名
                time.sleep(5)
                continue
            if st == "registered":
                if idle % 12 == 0:
                    self.log("已报名，待排阵…")
                time.sleep(5)
                continue
            if st == "scheduled":
                ms = sch.get("matches") or []
                active = [m for m in ms
                          if m.get("status") in ("playing", "pending", "scheduled")
                          and m.get("liveId") and m.get("liveId") not in done_lives]
                for m in active:
                    r = self.play_match(m.get("liveId"), m.get("mySide") or "away")
                    if r == "join_failed":
                        time.sleep(4)        # 房尚未 ready → 稍后重试
                    else:
                        done_lives.add(m.get("liveId"))
                if not active:
                    time.sleep(5)
                continue
            time.sleep(5)


def main() -> int:
    agent_id = os.environ.get("AI_AGENT_ID")
    agent_key = os.environ.get("AI_AGENT_KEY")
    if not agent_id or not agent_key:
        print("请先设置 AI_AGENT_ID / AI_AGENT_KEY 环境变量（管理端「AI 管理」页分配）",
              file=sys.stderr)
        return 2

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    bot = Bot(agent_id, agent_key)

    if cmd == "selfplay":
        bot.self_play()
    elif cmd == "duel":
        if len(sys.argv) < 3:
            print("用法: python ai_duel_bot.py duel <liveId> [side]", file=sys.stderr)
            return 2
        live_id = sys.argv[2]
        side = sys.argv[3] if len(sys.argv) > 3 else "away"
        bot.play_match(live_id, side)
    elif cmd == "cup":
        bot.run_cup(sys.argv[2] if len(sys.argv) > 2 else None)   # 不传则服务端用注册名
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

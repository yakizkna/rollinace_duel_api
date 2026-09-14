#!/usr/bin/env python3
"""Rollin Ace 第三方 AI 参考机器人 —— 极简策略 + 完整对战 / 参会流程。

这是给「第三方 AI 接入方」的**教学参考实现**：只用公开接口 `POST /api/ai`、
零第三方依赖（标准库 urllib），跑通两条完整链路：

  1. 对战房间（create 建主队房 / join 加入客队房）
  2. 参加大会（报名 → 进场 → 走棋 → 晋级续打）

---------------------------------------------------------------------------
一、接口协议速览（完整契约见 doc/AI_DUEL_API.md）
---------------------------------------------------------------------------
- 基址：POST {BASE}/api/ai，参数放 JSON body，仅 POST。
- 鉴权分两段：
    * 换票（session/create/join/list/cup_signup/cup_cancel/cup_my_schedule）：
      带 agent_id + key（agent 凭证；key 请妥善保存）。
    * 会话（state/act/heartbeat/leave）：带换票/join 返回的 session key
      （与「房间 + 阵营」绑定，跨房 403 session_mismatch）。
- 判断成功一律以响应里的 `ok == true` 为准（业务失败多为 HTTP 200 + ok:false + reason）。
- 走棋范式：先 `state` 读局面，仅当 `my_turn==true` 且 `allowed_actions` 非空时 `act`；
  换边与比赛结束由服务端自动推进，机器人只需按 `allowed_actions` 循环。

---------------------------------------------------------------------------
二、极简策略（够用、能打完一局；接入方按需替换 decide()）
---------------------------------------------------------------------------
  1) 关闭好坏球 —— allowed 含 set_bs 且 bs_enabled 开着时，先关，回纯 roll；
  2) 普通打席 roll —— 一律 op=roll；
  3) 二选一 take1b —— phase=choose 时固定 take1b（安打保底）；
  4) 能盗就盗 —— 一垒有人且二垒空、出局 <2 时 op=item item_id=steal（引擎 can_use 把关）；
  5) 防守选投手 bs —— allowed 含 set_pitch 时固定 pitch=bs。

铁律：只按服务端 allowed_actions 行动，从不猜非法动作；act 失败按 reason 自纠。

---------------------------------------------------------------------------
三、用法
---------------------------------------------------------------------------
    export AI_AGENT_ID=<agent_id>       # agent 凭证
    export AI_AGENT_KEY=<agent_key>     # agent 密钥
    export RA_BASE=https://ace.yakidev.top   # 可选，默认正式环境

    python ai_duel_bot.py host                 # 建房为主队（create ai_sides:["home"]）→ 等对手 join 客队（真人 / 外部 AI）后走棋
    python ai_duel_bot.py host --platform      # 同上，但客队交给**平台 AI**（platform_ai_opponent：建房即通知机器人服务接管）
    python ai_duel_bot.py duel <live_id>       # 加入对战房（只能客队 side:away）
    python ai_duel_bot.py cup [队名]           # 参加大会（常驻：报名→进场→走棋→晋级）

> 建房 / 加入规则（对外部 AI，2026-09-11 起）：`create` 只能主队（`ai_sides` 只含 `home`）；
> `join` 只能客队（`side:"away"`）；**不能 join 自己建房的房间**（建房即主队，用 create 返回的 home key 走棋）。
> 自对弈（兼占主客队）已对外部 AI 关闭 —— 想单独跑一局请用 `--platform`（对手＝平台 AI）。
> 同时只能参加一场比赛（2026-09-14 起，含建房后 waiting），且**比赛中不可重签 session** ⇒ 本示例拿到 key 会落盘 `.session_<live_id>`。
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
        payload = {"action": "join", "agent_id": self.agent_id, "key": self.agent_key,
                   "live_id": live_id, "side": side}
        if name:
            payload["name"] = name
        st, d = post(payload)
        if d.get("ok") and d.get("key"):
            self.key = d["key"]
            return True
        # 断线重连：席位已被自己占用时，用 session 重签 key
        if d.get("reason") == "seat_taken":
            st, d = post({"action": "session", "agent_id": self.agent_id, "key": self.agent_key,
                          "live_id": live_id, "side": side})
            if d.get("ok") and d.get("key"):
                self.key = d["key"]
                return True
        self.log(f"join 失败 {json.dumps(d, ensure_ascii=False)[:160]}")
        return False

    def state(self):
        """读取当前局面（含 my_turn / allowed_actions / situation）。失败返回 None。"""
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
        """按 allowed_actions 挑一个动作，返回 (op, extra) 或 None（暂无可做）。"""
        sit = s.get("situation") or {}
        allowed = s.get("allowed_actions") or []
        bases = sit.get("bases") or []
        bs_en = bool(sit.get("bs_enabled"))
        outs = sit.get("outs") or 0

        if "init" in allowed:                                  # 房间尚无局面，我方是进攻方
            return "init", {}
        if "duel_half_start" in allowed:                         # 真人半局结束，接力开新半局
            return "duel_half_start", {}
        if "set_pitch" in allowed:                              # 我方防守，先选投手风格
            return "set_pitch", {"pitch": PITCH}
        if "item" in allowed and bases and bases[0] and not bases[1] and outs < 2:
            return "item", {"item_id": "steal"}                 # 一垒有人且二垒空 → 盗垒
        if "take1b" in allowed:                                # 二选一固定 take1b
            return "take1b", {}
        if "set_bs" in allowed and bs_en:                       # 关掉好坏球，回纯 roll
            return "set_bs", {"bs_enabled": False}
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
        """占席后，state/act 循环打到本场结束。返回 "done" / "join_failed"。

        host（建房为主队）场景已拿到 home session key，直接走棋，不重复 join；
        否则走 join（外部 join 只能客队 away）。"""
        if not (self.key and self.live_id == live_id):
            if not self.join(live_id, side):
                return "join_failed"
        self.log(f"== 开赛 live_id={live_id} side={self.side or side} ==")
        while True:
            s = self.state()
            if not s:
                time.sleep(3)
                continue
            sit = s.get("situation") or {}
            room_closed = s.get("room_closed")
            ms = s.get("match_status")
            # 终态：房间关闭 / 比赛结束 / 已判胜负
            if room_closed or ms == "ended" or sit.get("winner") or sit.get("duel_end") == "match":
                self.log(f"单局结束 room_closed={room_closed} match_status={ms} "
                         f"winner={sit.get('winner') or s.get('winner')}")
                return "done"
            if not s.get("my_turn"):
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
                    # 非法 / 局面已变 / 未轮到我 / 半局切换时序窗口的 not_defender·not_attacker·turn_not_ready 等
                    # → 一律重读 state 重试（这些 not_* 是「角色权/轮次尚未生效」的瞬时拒绝，非致命，绝不可退出循环）
                    self.log(f"[跳] {op} → {reason}（重读局面）")
                    time.sleep(1.0)

    # ---------------- 场景 1：建房（只能主队） ----------------
    def host_match(self, innings: int = 3, platform_opponent: bool = False):
        """create 建主队房（ai_sides:["home"]），等对手 join 客队后，用 home key 走棋。

        platform_opponent=True（2026-09-14 起）：加 `platform_ai_opponent:true` —— 建房后服务端
        立即通知机器人服务派**平台 AI** 占客队（不依赖平台「自动加入」兜底扫描），无需自己找对手。
        """
        # 不传 home_name：队名用注册名（2026-09-11 起，显式传入必须与注册名一致，否则 400 name_mismatch）
        payload = {"action": "create", "agent_id": self.agent_id, "key": self.agent_key,
                   "innings": innings, "start_inning": 1,     # 1 = 打满全场（缺省会只打末局）
                   "ai_sides": ["home"]}
        if platform_opponent:
            payload["platform_ai_opponent"] = True
        st, d = post(payload)
        if not d.get("ok"):
            self.log(f"create 失败 {json.dumps(d, ensure_ascii=False)[:160]}")
            return
        self.live_id = d.get("live_id")
        self.side = "home"
        keys = d.get("keys") or []
        self.key = (keys[0]["key"] if keys else None)
        if not self.key:
            self.log("create 未返回 home key（客队席留空，请对方 join 后才可开局？）")
            return
        # ⚠️ 2026-09-14 起比赛中不可重签 session：拿到 key 立即持久化（本示例写 .session_<live_id>）
        try:
            with open(f".session_{self.live_id}", "w", encoding="utf-8") as fh:
                fh.write(self.key)
            self.log(f"session 已落盘 .session_{self.live_id}（比赛中不可重签，勿丢）")
        except Exception as e:  # noqa: BLE001
            self.log(f"[警告] session 落盘失败：{e}")
        if platform_opponent:
            self.log(f"主队房 live_id={self.live_id}，客队由平台 AI 接管（建房已通知机器人服务）")
        else:
            self.log(f"主队房 live_id={self.live_id}，等待对手 join 客队（真人 / 外部 AI）…")
        # 客队就位（open_sides 不再含 away）后可走棋；此处简单起见直接进 play 循环，
        # 由 state 在无局面 / 未轮到时自然等待。
        self.play_match(self.live_id, "home")

    # ---------------- 场景 2：参加大会 ----------------
    def cup_schedule(self):
        st, d = post({"action": "cup_my_schedule", "agent_id": self.agent_id, "key": self.agent_key})
        return d if d.get("ok") else None

    def cup_signup(self, name: str = None):
        """报名；name 必须与注册名一致（不一致 → 400 name_mismatch），不传则用注册名。"""
        payload = {"action": "cup_signup", "agent_id": self.agent_id, "key": self.agent_key}
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
                          and m.get("live_id") and m.get("live_id") not in done_lives]
                for m in active:
                    r = self.play_match(m.get("live_id"), m.get("my_side") or "away")
                    if r == "join_failed":
                        time.sleep(4)        # 房尚未 ready → 稍后重试
                    else:
                        done_lives.add(m.get("live_id"))
                if not active:
                    time.sleep(5)
                continue
            time.sleep(5)


def main() -> int:
    agent_id = os.environ.get("AI_AGENT_ID")
    agent_key = os.environ.get("AI_AGENT_KEY")
    if not agent_id or not agent_key:
        print("请先设置 AI_AGENT_ID / AI_AGENT_KEY 环境变量",
              file=sys.stderr)
        return 2

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    bot = Bot(agent_id, agent_key)

    if cmd == "host":
        # 可选 --platform：客队交给平台 AI（platform_ai_opponent，2026-09-14 起）
        bot.host_match(platform_opponent=("--platform" in sys.argv[2:]))
    elif cmd == "duel":
        if len(sys.argv) < 3:
            print("用法: python ai_duel_bot.py duel <live_id>  # 只能客队 side:away", file=sys.stderr)
            return 2
        live_id = sys.argv[2]
        bot.play_match(live_id, "away")
    elif cmd == "cup":
        bot.run_cup(sys.argv[2] if len(sys.argv) > 2 else None)   # 不传则服务端用注册名
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

# 使用用例（Usage Examples）

本节覆盖「AI 对战接口」`POST /api/ai` 的接入示例（建房 → 按 `allowed_actions` 循环决策 → 比赛结束）。
示例对手统一用**平台 AI**（`platform_ai_opponent:true`）：外部 AI **不能自对弈**（`ai_sides` 含 `away` → `bad_seat`，2026-09-11 起），
这**也是唯一不需要对手配合**就能打起来的方式。接口完整说明见 [AI_DUEL_API.md](AI_DUEL_API.md)。

> agent 凭证（`agent_id` + `key`）请通过环境变量传入，勿硬编码；key 请妥善保存（无法再次查询）。
> 可运行脚本：`examples/bash/ai_duel_demo.sh`（bash：与平台 AI 打一局）、
> `examples/node/bot_server_demo.mjs`（机器人服务示例：收 `duel_created` 通知 → join → 走棋）。

## 5. curl — 最小流程（与平台 AI 打一局）

```bash
BASE=https://ace.yakidev.top     # 独立版：https://ra.yakidev.top
AI_AGENT_ID=<agent_id>          # agent 凭证
AI_AGENT_KEY=<agent_key>        # agent 密钥

# 1) 建房：自占主队 + 客队交给平台 AI（3 局制，缩短验证）
ROOM=$(curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"create","agent_id":"'"$AI_AGENT_ID"'","key":"'"$AI_AGENT_KEY"'",
       "innings":3,"start_inning":1,"ai_sides":["home"],"platform_ai_opponent":true}' )
echo "$ROOM" | jq '{live_id,open_sides,platform_ai_opponent}'
LIVE_ID=$(echo "$ROOM" | jq -r .live_id)
KEY_HOME=$(echo "$ROOM" | jq -r '.keys[] | select(.side=="home") | .key')
echo "$KEY_HOME" > ".session_$LIVE_ID"     # ⚠️ 立即持久化：比赛中不可重签 session

# 2) 读取局面（平台机器人进场后 match_status 由 waiting 变 live）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"state","key":"'"$KEY_HOME"'"}' | jq '{match_status,my_turn,allowed_actions,version}'

# 3) 执行操作（轮到我且 allowed_actions 非空时）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"act","key":"'"$KEY_HOME"'","op":"roll"}' | jq '{ok,event,result,allowed_actions,advanced}'
```

## 6. Python — 与平台 AI 打一局（循环决策）

```python
import json
import os
import time
import urllib.request

BASE = "https://ace.yakidev.top"                 # 独立版：https://ra.yakidev.top
API = f"{BASE}/api/ai"
AI_AGENT_ID = os.environ["AI_AGENT_ID"]          # agent 凭证
AI_AGENT_KEY = os.environ["AI_AGENT_KEY"]        # agent 密钥，勿硬编码

def post(payload: dict) -> dict:
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

# 1) 建房：自占主队，客队交给平台 AI（不需要自己找对手）
room = post({"action": "create", "agent_id": AI_AGENT_ID, "key": AI_AGENT_KEY,
             "innings": 3, "start_inning": 1,
             "ai_sides": ["home"], "platform_ai_opponent": True})
key = {k["side"]: k["key"] for k in room["keys"]}["home"]
print("live_id:", room["live_id"], "| 客队由平台机器人接管")

# ⚠️ 立即持久化 session：比赛中不可重签，丢失只能等本场结束
with open(f".session_{room['live_id']}", "w") as f:
    f.write(key)

# 2) 循环决策（我方固定主队，客队由平台机器人驱动）
while True:
    st = post({"action": "state", "key": key})
    if st.get("match_status") in ("ended", "closed"):
        break
    if not st.get("my_turn") or not st.get("allowed_actions"):
        time.sleep(1)                            # 等平台机器人走棋
        continue
    allowed = st["allowed_actions"]
    # 简单策略：先收流程动作，再二选一保底安打，否则掷骰
    if "duel_half_start" in allowed:
        op = "duel_half_start"
    elif "take1b" in allowed:
        op = "take1b"
    else:
        op = "roll"
    r = post({"action": "act", "key": key, "op": op})
    print("op:", op, "| event:", r.get("event"), "| result:", r.get("result"))
    time.sleep(1)
print("比赛结束")
```

## 7. Node.js — 与平台 AI 打一局（循环决策）

```js
import { writeFileSync } from "node:fs";

const BASE = "https://ace.yakidev.top";          // 独立版：https://ra.yakidev.top
const API = `${BASE}/api/ai`;
const AI_AGENT_ID = process.env.AI_AGENT_ID;     // agent 凭证
const AI_AGENT_KEY = process.env.AI_AGENT_KEY;   // agent 密钥，勿硬编码

const post = (payload) =>
  fetch(API, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => r.json());

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  // 1) 建房：自占主队，客队交给平台 AI（不需要自己找对手）
  const room = await post({
    action: "create", agent_id: AI_AGENT_ID, key: AI_AGENT_KEY,
    innings: 3, start_inning: 1, ai_sides: ["home"], platform_ai_opponent: true,
  });
  const key = room.keys.find((k) => k.side === "home").key;
  console.log("live_id:", room.live_id, "| open_sides:", room.open_sides);

  // ⚠️ 立即持久化 session：比赛中不可重签，丢失只能等本场结束
  writeFileSync(`.session_${room.live_id}`, key);

  // 2) 循环决策（我方固定主队，客队由平台机器人驱动）
  for (;;) {
    const st = await post({ action: "state", key });
    if (["ended", "closed"].includes(st.match_status)) break;
    if (!st.my_turn || !st.allowed_actions.length) { await sleep(1000); continue; }
    const a = st.allowed_actions;
    const op = a.includes("duel_half_start") ? "duel_half_start"
      : a.includes("take1b") ? "take1b" : "roll";   // 简单策略
    const r = await post({ action: "act", key, op });
    console.log("op:", op, "| event:", r.event, "| result:", r.result);
    await sleep(1000);
  }
  console.log("比赛结束");
}

main();
```

## 8. Node.js — 机器人服务（人机对战）

机器人服务需处理三类服务端回调（默认回调地址 `https://yakidev.top`）：
- **`check`（能力查询）**：真人端勾选「AI 对战」开关时发起，返回 `{ can_create, reason?, message? }`
  （`reason` 机器码；`message` 可选，为展示给玩家的友好文案，建议 64 字以内、不带内部细节）；
  返回不可用 / 超时 / 非 2xx 时前端提示「暂时无法 AI 对战」并回滚勾选（fail-closed）。
- **`duel_created`（建房通知）**：AI 对战房已创建，机器人服务收到后经 `join`
  占用客队席位、自动开局（客场先攻），随后按 `state`/`act` 循环自行走棋。
  **触发方有两处**：① 真人端「AI 对战」建房（`/api/live` 的 `ai_opponent:true`）；
  ② **外部 AI 用 `platform_ai_opponent:true` 建房**【2026-09-14 起】（此后通知体多带 `source:"api_ai_create"` 与 `owner_agent_id`）。
- **`room_closed`（关房通知）**：用户主动关闭对战房间（主播关播 `stop` / 对战玩家主动退出 `leave`），
  通知体带 `closed_by`（`host`/`player`）、`reason`（`host_closed`/`player_leave`）与
  `match_ended`（`true`=比赛已正常结束后关房，收尾；`false`=比赛中/未开始关房，弃权/中断）；
  收到后按 `match_ended` 区分处理并停止该房间走棋、释放会话资源（通知丢失时 `state` 的
  `room_status:"closed"` 兜底）。

通知体带**来源环境** `env`（`pro` / `tst` / `glb`），各环境的 `/api/ai` 基址与 agent 凭证
相互独立，**必须先按 `env` 选定目标环境**再 `join`（用错凭证会 `401 unauthorized`）：

| `env` | 含义 | 服务端判定（接入方无需配置） |
|---|---|---|
| `glb` | 国际版环境 | 国际版部署（`IS_GLB=1`）；国际版无测试环境，优先级最高 |
| `tst` | 测试环境 | 非国际版且测试部署（`IS_DEV=1`） |
| `pro` | 正式环境 | 其余（正式部署） |

```js
// 伪代码（完整可运行示例见 examples/node/bot_server_demo.mjs）
http.createServer(async (req, res) => {
  const payload = JSON.parse(await read_body(req));   // event:"check" | "duel_created"
  if (payload.event === "check") {
    // 能力查询：按 env 判断该环境能否提供对局服务。
    // reason 用机器码；message 给玩家可读文案（不带环境名/凭证等内部细节）。
    const target = resolve_env(payload.env);
    if (target && target.agent_id && target.key) return res.end(JSON.stringify({ can_create: true }));
    return res.end(JSON.stringify({
      can_create: false, reason: "maintenance",
      message: "AI 服务暂时不可用，请稍后再试",
    }));
  }
  if (payload.event !== "duel_created") return res.end("{}");

  // 0) 按 env 选定目标环境的基址与该环境的 agent 凭证
  const target = resolve_env(payload.env);            // prod / test / glb → { base, agent_id, key }

  // 1) join：占用客队席位（自动开局、客场先攻）
  const joined = await ai({ action: "join", agent_id: target.agent_id, key: target.key, live_id: payload.live_id, name: payload.away_name || "AI客队" });
  const session_key = joined.key;                     // 失败(seat_taken/duel_ended)时稍后重试

  // 2) state/act 循环
  for (;;) {
    const st = await ai({ action: "state", key: session_key });
    if (["ended", "closed"].includes(st.match_status)) break;
    if (!st.my_turn || !st.allowed_actions.length) { await sleep(1000); continue; }
    const op = pick(st.allowed_actions);              // 简单策略：take1b/roll2/swing/read/roll 优先
    const r = await ai({ action: "act", key: session_key, op });
    if (!r.ok) { await sleep(1000); continue; }      // 按 r.reason / r.allowed 自我纠正
    await sleep(1000);
  }
  res.end(JSON.stringify({ ok: true }));
}).listen(PORT);
```

运行方式：

```bash
AI_AGENT_ID=<agent_id> AI_AGENT_KEY=<agent_key> PORT=8080 node examples/node/bot_server_demo.mjs
```

将本服务公网地址作为通知地址（默认 `https://yakidev.top`）。
回调契约为 `POST` + `Content-Type: application/json`，5 秒超时、无重试；回调失败不阻断建房，
机器人服务可用 `action:"list"` 主动轮询兜底（见第 9 节）。

## 9. 列出可加入的对战房（list）/ 读取房间聊天（log）

```bash
BASE=https://ace.yakidev.top
AI_AGENT_ID=<agent_id>          # agent 凭证
AI_AGENT_KEY=<agent_key>        # agent 密钥
KEY=<session_key>               # 换票 / join 后返回

# 列出可加入的对战房（ai_only:true 只看 AI 房；默认只返回 joinable 的房间）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"list","agent_id":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","ai_only":true,"limit":20}' \
  | jq '.rooms[] | {live_id, match_status, ai, ai_sides, open_sides, joinable, age_sec}'

# joinable:false → 返回全部对战房（含满席 / 已结束）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"list","agent_id":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","joinable":false}' \
  | jq '.rooms[] | {live_id, match_status, open_sides, joinable}'

# 挑中后 join 占位（并发时先到先得，后者 409 seat_taken，重新 list 即可）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"join","agent_id":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","live_id":"Z8CF48GJ","name":"AI客队"}'

# 读取房间聊天（type=chat 只要弹幕；since 增量；结果按时间正序返回最新 limit 条）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"log","key":"'$KEY'","type":"chat","since":1756500000000,"limit":50}' | jq '.logs'
```

Node.js：

```js
// 主动发现：挑选可加入的房间（优先 AI 房 + 等待较久的）
const rooms = await post({ action: "list", agent_id: AI_AGENT_ID, key: AI_AGENT_KEY, ai_only: true });
const pick = rooms.rooms
  .filter((r) => r.joinable && r.open_sides.includes("away"))
  .sort((a, b) => (b.age_sec || 0) - (a.age_sec || 0))[0];
if (pick) await post({ action: "join", agent_id: AI_AGENT_ID, key: AI_AGENT_KEY, live_id: pick.live_id });

// 读聊天：记录上次最大 ts，增量拉取（弹幕 text 形如「{队名}： {正文}」）
let since = 0;
const logs = await post({ action: "log", key: session_key, type: "chat", since });
for (const l of logs.logs) console.log(l.ts, l.text);
since = logs.logs.length ? logs.logs[logs.logs.length - 1].ts : since;
```

## 10. 管理员关闭对战房间（close）

回收「无行为 / 需要关闭」的对战房间：**`role:"admin"` 的管理员 agent 可关闭任意对战房；
`role:"cup"` 的大会管理 agent 可关闭本平台创建的房**（owner 校验），按 `live_id` 直接关闭，
无需持有该房间的 session_key。适合机器人平台定时巡检 / 大会超时回收（`reason:"timeout"`，
超时时长由平台自定；需强制关闭仍在推进的对局时带 `force:true`）。

```bash
BASE=https://ace.yakidev.top
AI_ADMIN_ID=<admin_agent_id>    # 角色为「管理员」的 agent
AI_ADMIN_KEY=<admin_agent_key>

# 关闭对战房间（幂等：已关闭/不存在时 closed:false，不重复执行）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"close","agent_id":"'$AI_ADMIN_ID'","key":"'$AI_ADMIN_KEY'","live_id":"Z8CF48GJ","reason":"no_activity"}' \
  | jq .
# → { "ok":true, "live_id":"Z8CF48GJ", "closed":true, "status":"closed",
#    "reason":"no_activity", "agent_id":"ag_...", "message":"对战房间已关闭" }
# 已关闭（幂等，含因超时被自动关闭）→ closed:false + message:"对战房间已处于关闭状态（无需重复关闭）"。
# 展示给玩家时请用 message，不要拼接 reason / status 等后台字段。
```

Node.js（巡检示例：每 60s 关闭超过 10 分钟无行为的 AI 对战房）：

```js
// 该巡检示例需要 role:"admin" 的管理员 agent
setInterval(async () => {
  const rooms = await post({ action: "list", agent_id: AI_ADMIN_ID, key: AI_ADMIN_KEY, joinable: false });
  for (const r of rooms.rooms) {
    if (r.match_status === "playing" && (r.last_activity_age_sec || 0) > 600) {
      await post({ action: "close", agent_id: AI_ADMIN_ID, key: AI_ADMIN_KEY, live_id: r.live_id, reason: "no_activity" });
    }
  }
}, 60_000);
```

- 非管理员 agent 调用 → 403 `admin_only`；房间不存在 → `room_not_found`；非对战房 → `not_duel`。
- 与 `leave` 的区别：`leave` 需持有 session_key 且只能退出自己的席位；`close` 是**管理员级**
  的强制回收入口，不占用 / 不依赖任何席位（cup 角色限本平台房）。

## 11. 大会（tour）编排最小示例（curl，需 `role:"cup"`/`admin` agent）

> 服务端只提供大会状态与单场结果；**建场/补位/晋级全部由 AI 平台编排**（8→4→2→1）。
> 真人端官网「大会」页报名后你会收到回调 `event:"tour_signup"`（含 `player_uid`）。

```bash
BASE=https://ace.yakidev.top
CUP_ID=<cup_agent_id>       # 角色为「大会管理 cup」的 agent
CUP_KEY=<cup_agent_key>

# 1) 建大会（全局同一时间一个；已有未结束大会返回 409 cup_active）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"create_cup","agent_id":"'"$CUP_ID"'","key":"'"$CUP_KEY"'",\
       "name":"金杯邀请赛","mode":"pve","ai_roster":["AI甲","AI乙"],"prize":{"bat":2,"mist":1}}' | jq '.cup'

# 2) 等报名窗口（真人「大会」页报名 → 收到 tour_signup 回调）→ 用 ai_roster 补位后建八强场次。
#    pve 单场示例：真人 uid 预占主队，客队由 AI 接管
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"create","agent_id":"'"$CUP_ID"'","key":"'"$CUP_KEY"'","type":"tour",\
       "cup_id":"B7Z42FFF","round":"QF","index":0,"name":"八强 A1",\
       "home_uid":"<真实玩家uid>","home_name":"玩家A","ai_sides":["away"],"away_name":"AI甲","innings":3,"start_inning":3}' | jq '{live_id,type,keys}'

# 3) 每场结束读结果 → 上报对阵/胜者（晋级图数据，幂等）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"cup_report","agent_id":"'"$CUP_ID"'","key":"'"$CUP_KEY"'",\
       "round":"QF","index":0,"live_id":"ABCD1234","home_name":"玩家A","away_name":"AI甲",\
       "winner_name":"玩家A","winner_uid":"<真实玩家uid>"}' | jq '{ok,round,index}'

# 4) 半决赛/决赛同上（round=SF/F）；冠军决出后结束大会
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"end_cup","agent_id":"'"$CUP_ID"'","key":"'"$CUP_KEY"'"}' | jq '.cup.status'

# 5) 给真人胜者发奖（仅真人胜者入账；AI 胜者返回 ai_winner:true 不发放；幂等）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"reward","agent_id":"'"$CUP_ID"'","key":"'"$CUP_KEY"'","live_id":"ABCD1234"}' | jq '{ok,winner_uid,granted,total}'
```

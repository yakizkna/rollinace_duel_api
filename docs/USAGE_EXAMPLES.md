# 使用用例（Usage Examples）

本节覆盖「AI 对战接口」`POST /api/ai` 的接入示例（创建 AI 自对弈房 → 按 `allowedActions` 循环决策 → 比赛结束）。
接口完整说明见 [AI_DUEL_API.md](AI_DUEL_API.md)。

> agent 凭证（`agent_id` + `key`）请通过环境变量传入，勿硬编码；key 请妥善保存（无法再次查询）。
> 可运行脚本：`examples/bash/ai_duel_demo.sh`（bash 自对弈示例）、
> `examples/node/bot_server_demo.mjs`（机器人服务示例：收 `duel_created` 通知 → join → 走棋）。

## 5. curl — 自对弈最小流程

```bash
BASE=https://ace.yakidev.top
AI_AGENT_ID=<agent_id>          # agent 凭证
AI_AGENT_KEY=<agent_key>        # agent 密钥

# 1) 创建 AI 自对弈房（3 局制，缩短验证）
ROOM=$(curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"create","agentId":"'"$AI_AGENT_ID"'","key":"'"$AI_AGENT_KEY"'","innings":3,"startInning":3}' )
echo "$ROOM" | jq .
LIVE_ID=$(echo "$ROOM" | jq -r .liveId)
KEY_AWAY=$(echo "$ROOM" | jq -r '.keys[] | select(.side=="away") | .key')

# 2) 客场先攻：读取局面
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"state","key":"'"$KEY_AWAY"'"}' | jq '{myTurn,allowedActions,version}'

# 3) 执行操作（轮到我且 allowedActions 非空时）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"act","key":"'"$KEY_AWAY"'","op":"roll"}' | jq '{ok,event,result,allowedActions,advanced}'
```

## 6. Python — 自对弈循环

```python
import json
import time
import urllib.request

BASE = "https://ace.yakidev.top"
API = f"{BASE}/api/ai"
AI_AGENT_ID = "<agent_id>"   # agent 凭证
AI_AGENT_KEY = "<agent_key>" # agent 密钥，建议从环境变量读取

def post(payload: dict) -> dict:
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

# 1) 创建自对弈房
room = post({"action": "create", "agentId": AI_AGENT_ID, "key": AI_AGENT_KEY, "innings": 3, "startInning": 3})
keys = {k["side"]: k["key"] for k in room["keys"]}
print("liveId:", room["liveId"])

# 2) 循环决策（客场先攻）
side = "away"
while True:
    st = post({"action": "state", "key": keys[side]})
    if st.get("matchStatus") in ("ended", "closed"):
        break
    # 轮次交换：toMove 决定当前进攻方
    side = st.get("toMove", side)
    if not st.get("myTurn") or not st.get("allowedActions"):
        time.sleep(1)
        continue
    # 简单策略：二选一阶段优先 take1B（安打保底），否则掷骰
    op = "take1B" if "take1B" in st["allowedActions"] else "roll"
    r = post({"action": "act", "key": keys[side], "op": op})
    print("op:", op, "| event:", r.get("event"), "| result:", r.get("result"))
    time.sleep(1)
print("比赛结束")
```

## 7. Node.js — 自对弈循环

```js
const BASE = "https://ace.yakidev.top";
const API = `${BASE}/api/ai`;
const AI_AGENT_ID = process.env.AI_AGENT_ID;   // agent 凭证
const AI_AGENT_KEY = process.env.AI_AGENT_KEY; // agent 密钥，勿硬编码

const post = (payload) =>
  fetch(API, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => r.json());

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  // 1) 创建自对弈房
  const room = await post({ action: "create", agentId: AI_AGENT_ID, key: AI_AGENT_KEY, innings: 3, startInning: 3 });
  const keys = Object.fromEntries(room.keys.map((k) => [k.side, k.key]));
  console.log("liveId:", room.liveId);

  // 2) 循环决策（客场先攻）
  let side = "away";
  for (;;) {
    const st = await post({ action: "state", key: keys[side] });
    if (["ended", "closed"].includes(st.matchStatus)) break;
    side = st.toMove || side;                      // 换边
    if (!st.myTurn || !st.allowedActions.length) { await sleep(1000); continue; }
    const op = st.allowedActions.includes("take1B") ? "take1B" : "roll";  // 简单策略
    const r = await post({ action: "act", key: keys[side], op });
    console.log("op:", op, "| event:", r.event, "| result:", r.result);
    await sleep(1000);
  }
  console.log("比赛结束");
}

main();
```

## 8. Node.js — 机器人服务（人机对战）

机器人服务需处理三类服务端回调（默认回调地址 `https://yakidev.top`）：
- **`check`（能力查询）**：真人端勾选「AI 对战」开关时发起，返回 `{ canCreate, reason?, message? }`
  （`reason` 机器码；`message` 可选，为展示给玩家的友好文案，建议 64 字以内、不带内部细节）；
  返回不可用 / 超时 / 非 2xx 时前端提示「暂时无法 AI 对战」并回滚勾选（fail-closed）。
- **`duel_created`（建房通知）**：AI 对战房已创建，机器人服务收到后经 `join`
  占用客队席位、自动开局（客场先攻），随后按 `state`/`act` 循环自行走棋。
- **`room_closed`（关房通知）**：用户主动关闭对战房间（主播关播 `stop` / 对战玩家主动退出 `leave`），
  通知体带 `closedBy`（`host`/`player`）、`reason`（`host_closed`/`player_leave`）与
  `matchEnded`（`true`=比赛已正常结束后关房，收尾；`false`=比赛中/未开始关房，弃权/中断）；
  收到后按 `matchEnded` 区分处理并停止该房间走棋、释放会话资源（通知丢失时 `state` 的
  `roomStatus:"closed"` 兜底）。

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
  const payload = JSON.parse(await readBody(req));   // event:"check" | "duel_created"
  if (payload.event === "check") {
    // 能力查询：按 env 判断该环境能否提供对局服务。
    // reason 用机器码；message 给玩家可读文案（不带环境名/凭证等内部细节）。
    const target = resolveEnv(payload.env);
    if (target && target.agentId && target.key) return res.end(JSON.stringify({ canCreate: true }));
    return res.end(JSON.stringify({
      canCreate: false, reason: "maintenance",
      message: "AI 服务暂时不可用，请稍后再试",
    }));
  }
  if (payload.event !== "duel_created") return res.end("{}");

  // 0) 按 env 选定目标环境的基址与该环境的 agent 凭证
  const target = resolveEnv(payload.env);            // prod / test / glb → { base, agentId, key }

  // 1) join：占用客队席位（自动开局、客场先攻）
  const joined = await ai({ action: "join", agentId: target.agentId, key: target.key, liveId: payload.liveId, name: payload.awayName || "AI客队" });
  const sessionKey = joined.key;                     // 失败(seat_taken/duel_ended)时稍后重试

  // 2) state/act 循环
  for (;;) {
    const st = await ai({ action: "state", key: sessionKey });
    if (["ended", "closed"].includes(st.matchStatus)) break;
    if (!st.myTurn || !st.allowedActions.length) { await sleep(1000); continue; }
    const op = pick(st.allowedActions);              // 简单策略：take1B/roll2/swing/read/roll 优先
    const r = await ai({ action: "act", key: sessionKey, op });
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

# 列出可加入的对战房（aiOnly:true 只看 AI 房；默认只返回 joinable 的房间）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"list","agentId":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","aiOnly":true,"limit":20}' \
  | jq '.rooms[] | {liveId, matchStatus, ai, aiSides, openSides, joinable, ageSec}'

# joinable:false → 返回全部对战房（含满席 / 已结束）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"list","agentId":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","joinable":false}' \
  | jq '.rooms[] | {liveId, matchStatus, openSides, joinable}'

# 挑中后 join 占位（并发时先到先得，后者 409 seat_taken，重新 list 即可）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"join","agentId":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","liveId":"Z8CF48GJ","name":"AI客队"}'

# 读取房间聊天（type=chat 只要弹幕；since 增量；结果按时间正序返回最新 limit 条）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"log","key":"'$KEY'","type":"chat","since":1756500000000,"limit":50}' | jq '.logs'
```

Node.js：

```js
// 主动发现：挑选可加入的房间（优先 AI 房 + 等待较久的）
const rooms = await post({ action: "list", agentId: AI_AGENT_ID, key: AI_AGENT_KEY, aiOnly: true });
const pick = rooms.rooms
  .filter((r) => r.joinable && r.openSides.includes("away"))
  .sort((a, b) => (b.ageSec || 0) - (a.ageSec || 0))[0];
if (pick) await post({ action: "join", agentId: AI_AGENT_ID, key: AI_AGENT_KEY, liveId: pick.liveId });

// 读聊天：记录上次最大 ts，增量拉取（弹幕 text 形如「{队名}： {正文}」）
let since = 0;
const logs = await post({ action: "log", key: sessionKey, type: "chat", since });
for (const l of logs.logs) console.log(l.ts, l.text);
since = logs.logs.length ? logs.logs[logs.logs.length - 1].ts : since;
```

## 10. 管理员关闭对战房间（close）

回收「无行为 / 需要关闭」的对战房间：**`role:"admin"` 的管理员 agent 可关闭任意对战房；
`role:"cup"` 的赛事管理 agent 可关闭本平台创建的房**（owner 校验），按 `liveId` 直接关闭，
无需持有该房间的 session_key。适合机器人平台定时巡检 / 杯赛超时回收（`reason:"timeout"`，
超时时长由平台自定；需强制关闭仍在推进的对局时带 `force:true`）。

```bash
BASE=https://ace.yakidev.top
AI_ADMIN_ID=<admin_agent_id>    # 角色为「管理员」的 agent
AI_ADMIN_KEY=<admin_agent_key>

# 关闭对战房间（幂等：已关闭/不存在时 closed:false，不重复执行）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"close","agentId":"'$AI_ADMIN_ID'","key":"'$AI_ADMIN_KEY'","liveId":"Z8CF48GJ","reason":"no_activity"}' \
  | jq .
# → { "ok":true, "liveId":"Z8CF48GJ", "closed":true, "status":"closed",
#    "reason":"no_activity", "agentId":"ag_...", "message":"对战房间已关闭" }
# 已关闭（幂等，含因超时被自动关闭）→ closed:false + message:"对战房间已处于关闭状态（无需重复关闭）"。
# 展示给玩家时请用 message，不要拼接 reason / status 等后台字段。
```

Node.js（巡检示例：每 60s 关闭超过 10 分钟无行为的 AI 对战房）：

```js
// 该巡检示例需要 role:"admin" 的管理员 agent
setInterval(async () => {
  const rooms = await post({ action: "list", agentId: AI_ADMIN_ID, key: AI_ADMIN_KEY, joinable: false });
  for (const r of rooms.rooms) {
    if (r.matchStatus === "playing" && (r.lastActivityAgeSec || 0) > 600) {
      await post({ action: "close", agentId: AI_ADMIN_ID, key: AI_ADMIN_KEY, liveId: r.liveId, reason: "no_activity" });
    }
  }
}, 60_000);
```

- 非管理员 agent 调用 → 403 `admin_only`；房间不存在 → `room_not_found`；非对战房 → `not_duel`。
- 与 `leave` 的区别：`leave` 需持有 session_key 且只能退出自己的席位；`close` 是**管理员级**
  的强制回收入口，不占用 / 不依赖任何席位（cup 角色限本平台房）。

## 11. 杯赛（tour）编排最小示例（curl，需 `role:"cup"`/`admin` agent）

> 服务端只提供杯赛状态与单场结果；**建场/补位/晋级全部由 AI 平台编排**（8→4→2→1）。
> 真人端官网「杯」页报名后你会收到回调 `event:"tour_signup"`（含 `playerUid`）。

```bash
BASE=https://ace.yakidev.top
CUP_ID=<cup_agent_id>       # 角色为「赛事管理 cup」的 agent
CUP_KEY=<cup_agent_key>

# 1) 建杯（全局同一时间一个；已有未结束杯返回 409 cup_active）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"createCup","agentId":"'"$CUP_ID"'","key":"'"$CUP_KEY"'",\
       "name":"金杯邀请赛","mode":"pve","aiRoster":["AI甲","AI乙"],"prize":{"bat":2,"mist":1}}' | jq '.cup'

# 2) 等报名窗口（真人「杯」页报名 → 收到 tour_signup 回调）→ 用 aiRoster 补位后建八强场次。
#    pve 单场示例：真人 uid 预占主队，客队由 AI 接管
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"create","agentId":"'"$CUP_ID"'","key":"'"$CUP_KEY"'","type":"tour",\
       "cupId":"B7Z42FFF","round":"QF","index":0,"name":"八强 A1",\
       "homeUid":"<真实玩家uid>","homeName":"玩家A","aiSides":["away"],"awayName":"AI甲","innings":3,"startInning":3}' | jq '{liveId,type,keys}'

# 3) 每场结束读结果 → 上报对阵/胜者（晋级图数据，幂等）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"cupReport","agentId":"'"$CUP_ID"'","key":"'"$CUP_KEY"'",\
       "round":"QF","index":0,"liveId":"ABCD1234","homeName":"玩家A","awayName":"AI甲",\
       "winnerName":"玩家A","winnerUid":"<真实玩家uid>"}' | jq '{ok,round,index}'

# 4) 半决赛/决赛同上（round=SF/F）；冠军决出后结束杯赛
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"endCup","agentId":"'"$CUP_ID"'","key":"'"$CUP_KEY"'"}' | jq '.cup.status'

# 5) 给真人胜者发奖（仅真人胜者入账；AI 胜者返回 aiWinner:true 不发放；幂等）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"reward","agentId":"'"$CUP_ID"'","key":"'"$CUP_KEY"'","liveId":"ABCD1234"}' | jq '{ok,winnerUid,granted,total}'
```

# Rollin Ace AI 对战接口快速参考

## 基本信息

| 项 | 值 |
|---|---|
| Base URL | `https://ace.yakidev.top` |
| 路径 | `POST /api/ai` |
| 内容类型 | `application/json`（参数放请求体） |
| 请求方法 | 仅 POST（支持 OPTIONS 预检，返回 204） |
| 跨域 | 已开放 `Access-Control-Allow-*`，不强制自定义头 |

## 鉴权

| 阶段 | 方式 |
|---|---|
| 换票（session/create/join/list/close） | `body.agentId` + `body.key`（或请求头 `X-Agent-Id` + `X-AI-Key`）＝管理端「AI 管理」页分配的 agent 凭证 |
| 会话（state/act/chat/log/heartbeat/leave） | `body.key` 或请求头 `X-AI-Key`（二选一） |

- agent 凭证的 `key` 仅创建/重置时显示一次，服务端只存哈希；请妥善保存，勿提交到仓库。
- 创建 agent 时可选择角色：`agent`（普通，默认）/ `cup`（赛事管理：建杯赛·设奖品·发奖·关超时房）/ `admin`（管理员：全量）。
- key 与**房间（liveId）+ 阵营（side）**绑定，跨房调用 → 403 `session_mismatch`。
- key 有效期 24 小时、滑动续期；`leave` 或过期后失效。
- 凭证无效 / agent 已停用 → 401（fail-closed）。

## 最小调用流程（AI vs AI 自对弈）

```
1. create { agentId, key, innings:9 }            → liveId + home/away 两把 key
2. state  { key:<away key> }                      → situation / myTurn / allowedActions
3. act    { key:<away key>, op:"roll" }           → 新局面 + event
4. 换边与比赛结束由服务端自动推进，AI 只需按 allowedActions 循环 2~3
```

## 人机对战：机器人服务接入

真人端「创建对战 → 开启 AI 对战」（`aiOpponent:true`）建房后，服务端 **HTTP 通知机器人服务**：

| 项 | 值 |
|---|---|
| 方式 | `POST`，`Content-Type: application/json` |
| 地址 | 默认 `https://yakidev.top`（服务方可用环境变量 `BOT_SERVICE_URL` 覆盖） |
| 超时 | 5 秒，无重试；通知失败不阻断建房 |

通知请求体（`event:"duel_created"`）：

```json
{ "event": "duel_created", "env": "pro",
  "liveId": "ABCD1234", "type": "duel", "ai": true,
  "aiSides": ["away"], "homeUid": "主队完整uid", "homeName": "主队", "awayName": "AI客队",
  "duelInnings": 9, "startInnings": 9, "matchStatus": "waiting", "createdAt": 1756500000000 }
```

**关房通知（`event:"room_closed"`）**：用户主动关闭对战房间（主播关播 `stop` / 对战玩家主动退出 `leave`）时推送，
收到后停止该房间走棋并释放会话资源（`state` 的 `roomStatus:"closed"` 兜底）：

```json
{ "event": "room_closed", "env": "pro", "liveId": "ABCD1234",
  "type": "duel", "ai": true,
  "closedBy": "host",          // "host" / "player"
  "reason": "host_closed",     // "host_closed" / "player_leave"
  "matchStatus": "live",       // live / ended / waiting
  "matchEnded": false,         // true=赛后关房（收尾）；false=赛中关房（弃权/中断）
  "ts": 1756500000000 }
```

**`env`（来源环境）决定机器人该连哪个环境**——`/api/ai` 基址与 agent 凭证按环境隔离：

| 值 | 含义 | 服务端判定（接入方无需配置） |
|---|---|---|
| `glb` | 国际版环境 | 国际版部署（`IS_GLB=1`）；国际版无测试环境，优先级最高 |
| `tst` | 测试环境 | 非国际版且测试部署（`IS_DEV=1`） |
| `pro` | 正式环境 | 其余（正式部署） |

收到通知后接入流程：

```
1. 按 env 选定目标环境的 BASE 与该环境的 agent 凭证
2. join  { agentId, key, liveId, name:"AI客队" }   → 占用客队席位，自动开局（客场先攻）
   // name 须与注册名一致（否则 400 name_mismatch）；省略则用注册名（推荐）
3. state / act 循环（同上文自对弈）直至 matchStatus==="ended"
```

主动发现（通知丢失 / 想接管任意等待中的房间）：`list` 拉可加入房间 → 挑选 → `join`。

> join 失败：`seat_taken` / `duel_ended`（房间保持 `waiting`，可稍后重试）。
> 可运行示例：`examples/node/bot_server_demo.mjs`。

## 状态机

```
房间：waiting ──(客队就位)──▶ live ──(分出胜负)──▶ ended ──(30s 惰性回收)──▶ closed
```

局面阶段 `situation.phase`：`roll1` → `choose`（二选一）→ `roll2` → 结算；好坏球打席为 `bs`。
开局客场先攻（`attackerSide="away"`）；局数打满平分进入延长赛（由引擎处理）。

## action 速查

### 会话公共可选字段：rtt（网络质量上报，推荐）

所有会话请求（`state`/`act`/`heartbeat`/`chat`/`log`/`leave`）都可带可选字段
`rtt` = 机器人服务本端实测往返毫秒（建议取最近一次成功请求耗时、取整；首次可不带，服务端忽略非正数）。
人机对战中供真人端「网络状态」面板做端到端时延估算；读戳由服务端在 `state`/`act` 后自动补打，无需另传。
示例：`{ "action":"state", "key":"<key>", "rtt":35 }`。失败静默，不影响主流程。

### session — 换票

```json
{ "action":"session", "agentId":"ag_xxxxxabcde", "key":"<agent_key>", "liveId":"ABCD1234", "side":"away" }
```

`side` ∈ `home`/`away`，省略时优先 away、其次 home。
响应：`{ ok, liveId, side, key, expiresAt, uid, agentId }`

### create — 创建 AI 对战房

```json
{ "action":"create", "agentId":"ag_xxxxxabcde", "key":"<agent_key>", "homeName":"AI主队", "awayName":"AI客队", "innings":9, "startInning":9, "aiSides":["home","away"] }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `agentId` + `key` | 是 | 管理端分配的 agent 凭证（也可用请求头 `X-Agent-Id` + `X-AI-Key`） |
| `homeName`/`awayName` | 否 | 队名，缺省 `AI主队`/`AI客队` |
| `innings` | 否 | 总局数 1~9，默认 9 |
| `startInning` | 否 | 开局位置，默认等于 `innings` |
| `aiSides` | 否 | AI 接管席位，默认 `["home","away"]`；`["away"]` = 主队留真人；`[]` = 空房（无席位、等待加入） |
| `aiAgentFor` | 否 | 预留外部 AI 席：`{ home?/away?: "ag_xxx" }`（`tour`/`duel` 均可，`tour` 需 `cup`/`admin`）；该席留空不发 key，仅对应 agent 可 `join` |
| `type` | 否 | `duel`（默认）/ `tour`（杯赛场次房，需 `cup`/`admin`） |
| `homeUid`/`awayUid` | 否 | 预占真实玩家 uid（不发 key；与同席 `aiSides` 互斥；预占玩家在对战大厅可见可进入） |
| `name`/`round`/`cupId` | 否 | 场次展示名 / 轮次元数据 / 归属杯赛（编排用） |
| `prize` | 否 | tour 房预设奖品（技能包，仅真人胜者，如 `{bat:2}`） |
| `aiUseBS` | 否 | `true` = 要求 AI 对手用好坏球（机器人只派 bs=on 角色参赛） |
| `stream` | 否 | **duel 房固定公开直播（`true` 不可关）**；tour 房固定 `false`（无需传） |
| `liveId` | 否 | 指定房间号，缺省自动生成 8 位 |

响应：`{ ok, liveId, type:"duel", ai:true, aiSides, aiUseBS, matchStatus, duelInnings, startInnings, agentId, keys:[{side,key,expiresAt,uid,agentId}], situation }`

> 仅 `aiSides` 同时含 home+away 才立即开局；否则 `matchStatus=waiting`。

### join — 加入真人对战房

```json
{ "action":"join", "agentId":"ag_xxxxxabcde", "key":"<agent_key>", "liveId":"Z8CF48GJ", "name":"AI客队", "side":"away" }
```

默认客队席位（客场先攻，占位即开赛）；席位被占 → 409 `seat_taken`；已结束 → 409 `duel_ended`。
- **归属守卫**：目标席已 `aiAgentFor` 预留给其它 agent → 403 `seat_reserved`；真人勾选「AI 对战」的专用房（`botExclusive`）非平台 agent → 403 `bot_exclusive`。
**不限于 `ai:true` 的房间**：任何有空席、未结束的对战房都可加入（「假装玩家加入」）。

### list — 列出可加入的对战房

```json
{ "action":"list", "agentId":"ag_xxxxxabcde", "key":"<agent_key>", "aiOnly":true, "limit":20 }
```

- 参数：`aiOnly`（默认 `false`；`true` 只看 AI 房，建议）/ `joinable`（默认 `true`；`false` 返回全部，含满席与已结束）/ `limit`（默认 50、上限 200，按创建时间倒序）。
- 响应：`{ ok, rooms:[{ liveId, matchStatus, ai, aiSides, homeName, awayName, homeUid, awayUid, openSides, joinable, duelInnings, startInnings, createdAt, ageSec }], total, limit, serverTime }`
- `openSides` = 当前空席（`home`/`away`），与 `joinable` 同时成立即可 `join`；`ageSec` = 创建至今秒数。
- 只读、不修改房间状态，可轮询（建议 ≥3s）；并发占位先到先得，后者 → 409 `seat_taken`。
- 响应房间含 `botExclusive`：真人勾选「AI 对战」的专用房为 ra_duel_bot 专属，第三方 AI **勿 join**。

### state — 读取局面

```json
{ "action":"state", "key":"<key>" }
```

响应关键字段：`situation`（完整局面）、`toMove`、`myTurn`、`allowedActions`、`version`（乐观锁）、`agentId`、`matchStatus`、`roomStatus`、`duelEnd`、`winner`、`items`（本席位道具背包记账，详见下文「道具记账」）。

### act — 执行操作

```json
{ "action":"act", "key":"<key>", "op":"roll", "expectVersion":1756499123456 }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `op` | 是 | `roll`/`swing`/`read`/`take1B`/`roll2`/`item`/`setBS`/`init`/`duelHalfStart` |
| `itemId` | `op=item` 时 | 道具 id（`bat`/`steal`/`sac`/`mist`/`lun`/`ling`） |
| `bsEnabled` | `op=setBS` 时 | 切换好坏球模式（新打席生效） |
| `expectVersion` | 否 | 乐观锁，与当前 version 不一致 → `version_conflict` |

成功响应：`{ ok, liveId, side, agentId, op, version, situation, event, result, diceKind, baseEvents, advanced, duelEnd, winner, matchStatus, allowedActions, items }`

- `advanced`：`"half"`（已自动换边）/ `"match"`（比赛结束）/ `null`。
- `items`：本席位最新道具背包记账（每次 `item` 使用后都会刷新）。
- 非法操作 HTTP 200：`{ ok:false, reason:"illegal_op", allowed:[...], reasonDetail, situation, toMove }`。
- `duelHalfStart`：人机对战真人半局结束（`duelEnd==="half"`）且房间 `attackerUid` 已切到我方时，
  由新攻击方初始化新半局（重建局面并翻转进攻权）；`toMove!==mySide` 时拒绝。

### 道具记账（服务端权威，`state`/`act` 响应中的 `items`）

AI 接口无前端，技能次数 / 背包由**服务端权威记账**，随 `state` / `act` 返回：

```json
"items": {
  "stock": {"bat": 20, "steal": 20, "sac": 20, "mist": 20, "lun": 20, "ling": 20},
  "halfUsed": {"count": 0, "used": []},
  "batArmed": false,
  "rules": {"stockPerItem": 20, "skillsPerHalf": 3, "noDuplicatePerHalf": true}
}
```

- `stock`：剩余库存，每种 20 个（发满，对齐真人单种上限）；`halfUsed.count`：本半局已用次数（上限 3）；`halfUsed.used`：本半局已用道具 id 集合；`batArmed`：是否已装备【棒】。
- `op:"item"` 使用规则：
  - 前置校验失败即拒绝且**不扣库存**：库存耗尽 → `invalid_item`+`out_of_stock`；半局用满 3 次 → `condition_failed`+`skills_exhausted`；同种本半局已用 → `invalid_item`+`already_used`；未知道具 → `invalid_item`。
  - 引擎 `canUse` 权威判定不满足（如 `steal` 需一垒有人）→ `condition_failed`，也不扣库存。
  - `bat` 为被动道具：`op:"item",itemId:"bat"` 即装备，装备期间主骰摇出 1B 自动升级 2B，打席结束自动解除。
  - `ling` 传令成功后由服务端重置本半局额度（`count` 归 0、清空 `used`）。
  - 换边自动重置半局额度与棒装备；背包持久化在房间对象。

### chat — 发弹幕

```json
{ "action":"chat", "key":"<key>", "text":"加油！" }
```

- `text` 最长 100 字，超长截断；弹幕为空 → `empty_chat`；命中敏感词 → `blocked_content`
  （附 `matches` 命中词条，换一种说法重发）。
- 写入房间共享日志（`type="chat"`），**与真人端同一份日志流**：真人端 / 观众轮询
  `GET /api/live` 即可看到 AI 弹幕，无需前端改造。
- 署名规则与真人端一致：对战房内显示**队名**。
- 发弹幕顺带刷新在线心跳（与 `heartbeat` 同效）。
- 成功响应：`{ ok:true, liveId, side, ts }`。

### log — 读取房间日志（含聊天）

```json
{ "action":"log", "key":"<key>", "type":"chat", "since":1756500000000, "limit":50 }
```

- 参数：`type`（`chat`/`system`/`all`，默认 `all`）、`since`（只返回 `ts` 严格大于该值）、`limit`（取最新 N 条，默认 50、上限 200，结果时间正序）。
- 响应：`{ ok, liveId, side, agentId, logs:[{ ts, type, text }], total, serverTime }`；弹幕 `text` 形如 `{队名}： {正文}`。
- 与真人端 `GET /api/live` 的 `log` 同源（可读到真人/观众弹幕）；只读，顺带刷新在线心跳。
- 失败：无 key / key 失效 → 401 `unauthorized`；跨房 → 403 `session_mismatch`。

### heartbeat / leave

```json
{ "action":"heartbeat", "key":"<key>" }
{ "action":"leave", "key":"<key>" }
```

`state`/`act`/`chat`/`heartbeat` 均顺带刷新在线时间；`leave` 撤销 key。
`heartbeat` 等会话请求可带可选字段 `rtt`（本端实测往返 ms，见「action 速查」开头说明）。

### close — 管理员/赛事管理关闭对战房间（role:"admin" 全量；role:"cup" 限本平台房）

```json
{ "action":"close", "agentId":"<adminOrCupAgentId>", "key":"<key>", "liveId":"Z8CF48GJ", "reason":"timeout", "force":true }
```

- `role:"admin"` 可关任意对战房；`role:"cup"`（赛事管理）仅可关**本 agent 创建的房**（owner 校验，
  否则 403 `not_owner`）；普通 agent → 403 `admin_only`。按 `liveId` 直接关闭，无需 session_key。
- `reason` 可选（默认 `bot_close`；杯赛超时建议 `timeout`，≤32 字符，供审计）。
- 默认有「对局活跃保护」；杯赛超时确需强制关停进行中的对局时，带 `force:true`（仅 cup/admin 生效）。
- 响应：`{ ok, liveId, closed, status, reason, agentId, message }`；`closed:true` 本次实际关闭，
  `false` 幂等（已关闭/不存在，含因超时被自动关闭）。**展示用 `message`**，勿展示 `reason`/`status`。
- 房间不存在 → `room_not_found`；非对战房 → `not_duel`。

### 杯赛（tour）动作（均需 `role:"cup"`/`admin`；全局单杯，服务端只存，赛程由平台编排）

```json
// createCup 建杯（open 可报名）：已有未结束杯 → 409 cup_active
{ "action":"createCup", "agentId":"...", "key":"...",
  "name":"金杯邀请赛", "mode":"pve", "aiRoster":["AI甲","AI乙"], "prize":{"bat":2,"mist":1} }

// cupReport 上报某场对阵/胜者（幂等；晋级图渲染数据源，服务端不自动回写）
{ "action":"cupReport", "agentId":"...", "key":"...",
  "round":"QF", "index":0, "liveId":"ABCD1234",
  "homeName":"玩家A","awayName":"AI甲","winnerName":"玩家A","winnerUid":"<真实uid>" }

// endCup 结束（幂等，关闭报名）
{ "action":"endCup", "agentId":"...", "key":"..." }

// reward 发奖（房间 ended + 真人胜者才入账；AI 胜者返回 aiWinner:true 不发放；幂等）
{ "action":"reward", "agentId":"...", "key":"...", "liveId":"ABCD1234" }
```

- `cupReport.round`：`QF`（八强 0~3）/ `SF`（0~1）/ `F`（0）；不传 `index` 时按 `liveId` 定位。
- reward 奖品缺省取 tour 房预设 `prize`；入账为增量 +N、单种封顶 20、总量 120。
- 杯赛参考流程：`createCup` → 真人「杯」页报名（平台收 `tour_signup` 回调）→ 平台补位 → 逐场建
  `type:"tour"` 房 → 每场结束 `state` 读 `winner` → `cupReport` 上报 → 8→4→2→1 → `endCup` → `reward`。
- 为已报名第三方 AI 建场：`create { type:"tour", cupId, round, aiAgentFor:{ away:"ag_xxx" }, ... }`（该侧留空不发 key，仅对应 agent 可 `join`）。

### 第三方 AI 报名参加大会（普通 `agent` 即可，公开）

大会主办方开启「允许第三方 AI 报名」后，注册 agent 即可像真人一样自助报名（同池 8 席先到先得，无回调地址）：

```json
// 查状态：open(可报) / external_disabled / cup_full / signup_closed / registered / scheduled / no_cup
{ "action":"cupMySchedule", "agentId":"...", "key":"..." }
// 报名（重复报名 → already_signup；满员 → cup_full；未开放 → external_ai_disabled）
{ "action":"cupSignup", "agentId":"...", "key":"...", "name":"我的AI队名" }   // name 须与注册名一致；省略则用注册名
// 退报（幂等）
{ "action":"cupCancel", "agentId":"...", "key":"..." }
```

- 轮到比赛：`cupMySchedule` → `status:"scheduled"` + `matches[{round,index,liveId,mySide,opponent,...}]` →
  `join { liveId, side: mySide }` 进自己的预留席（仅本 agent 可通过）→ `state`/`act` 循环走棋。
- 开赛后限时未 join 会判负（缺席）；冠军奖励技能包仅真人有效。

## allowedActions 推导

前置：`matchStatus==="live"` 且 `roomStatus==="live"` 且 `situation.status==="playing"` 且 `!situation.duelEnd` 且 `attackerSide === 我的阵营`

| 局面条件 | 可执行 |
|---|---|
| `phase === "choose"` | `take1B`、`roll2` |
| `phase === "bs"` 或（`!plate` 且 `bsEnabled`） | `swing`、`read` |
| 其余（roll1/roll2 后等） | `roll` |
| `!plate`（未进打席） | 追加 `setBS` |
| 非「打席进行中」`!(plate && bsEnabled)` | 追加 `item` |

## situation 数据结构

| 字段 | 说明 |
|---|---|
| `inning`/`isBottom` | 局数 / 是否下半局 |
| `outs`/`bases[3]` | 出局数 / 一、二、三垒占位 |
| `scoreHome`/`scoreAway` | 绝对比分（`scoreMe`/`scoreOpp` 为当前阵营视角） |
| `attackerSide` | 当前进攻方：home/away |
| `phase` | `roll1`/`choose`/`roll2`/`bs`/`done` |
| `plate`/`balls`/`strikes` | 好坏球打席 / 坏球数 / 好球数 |
| `bsEnabled`/`bsChoosing` | 好坏球开关 / 等待选「打·看」 |
| `duelEnd`/`winner` | `null`/`half`/`match`；胜方 |
| `status` | `playing`/`ended` |
| `teamHome`/`teamAway` | 队名 |

事件字段：`event`（中文）、`result`（`1B`/`2B`/`HR`/`OUT`/`FOUL`）、`diceKind`（1/2/"bs"）、
`bsFace`/`bsOutcome`/`bsHit`/`bsOut`（好坏球明细）、`itemResult`/`itemType`/`dieValue`（道具明细）、
`baseEvents`（结构化跑者事件：进垒/得分/出局）。

## 错误码

| reason | HTTP | 含义 |
|---|---|---|
| `unauthorized` | 401 | 无 key / key 失效 / agent_id+key 无效或 agent 已停用 |
| `session_mismatch` | 403 | key 与 liveId 不匹配（跨房越权） |
| `room_not_found` | 200 | 房间不存在 |
| `room_closed` | 200 | 房间已关闭 |
| `not_duel` | 200 | 房间不是对战类型 |
| `duel_ended` | 200 | 比赛已结束，无法加入 |
| `seat_taken` | 200 | 席位已被占用 |
| `room_conflict` | 200 | 指定 liveId 已有进行中的房间 |
| `missing_liveId`/`missing_op` | 200 | 缺少必填参数 |
| `bad_session` | 200 | 房间尚无局面（先 `op:"init"`） |
| `empty_chat` | 200 | 弹幕内容为空 |
| `already_initialized` | 200 | 已初始化，重复 init |
| `init_failed` | 200 | 初始化失败 |
| `illegal_op` | 200 | 操作不合法（含 `allowed`、`reasonDetail`） |
| `version_conflict` | 200 | `expectVersion` 不一致，或服务端权威守卫命中（陈旧/回退操作，2026-09-04 起；重新 `state()` 后按最新局面重试） |
| `unknown_action` | 200 | 未知 action（含 `supported`） |
| `admin_only` | 403 | 需要 `role:"admin"`/`role:"cup"`（如 `close`/`createCup`/`reward`） |
| `not_owner` | 403 | `cup` 角色 close 非本 agent 创建的房间 |
| `cup_active` | 409 | 已有未结束杯赛（同一时间仅一个杯赛） |
| `cup_not_found` / `cup_ended` | 200 | 杯赛不存在 / 已结束 |
| `bad_round` / `bad_index` | 200 | `cupReport` round 或槽位不合法 |
| `not_ended` | 400 | 房间未结束不能 `reward` |
| `no_winner` / `no_prize` | 400 | 无胜者 / 未设置奖品 |
| `uid_conflict` | 409 | 预占真实玩家 uid 已参与其它进行中对局 |
| `name_mismatch` | 400 | 参赛名称与注册名称不一致（`cupSignup` / `join` 的 `name` ≠ 注册名；省略则用注册名） |
| `internal` | 500 | 服务端异常 |
| 引擎透传 | 200 | `not_choose_phase`/`bs_in_progress`/`condition_failed`/`invalid_item`/`invalid_duel_session` |

`reasonDetail` 取值：`match_not_live`（比赛未进行）/ `not_your_turn`（没轮到我）/ `phase_mismatch`（阶段不符）/ `out_of_stock`（道具库存耗尽）/ `skills_exhausted`（半局技能次数用满）/ `already_used`（同种道具本半局已用）。

## 判断成功

一律以 `ok === true` 判断成功（业务失败多为 HTTP 200 + `ok:false` + `reason`），不要只看 HTTP 状态码。

## curl 速查

```bash
BASE=https://ace.yakidev.top
AI_AGENT_ID=<agent_id>          # 管理端「AI 管理」页分配
AI_AGENT_KEY=<agent_key>        # key 仅创建/重置时显示一次
KEY=<session_key>               # 换票成功后返回

# 创建自对弈房
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"create","agentId":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","innings":3,"startInning":3}'

# 读取局面
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"state","key":"'$KEY'"}' | jq '{myTurn,allowedActions,version}'

# 执行操作
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"act","key":"'$KEY'","op":"roll"}' | jq '{ok,event,result,allowedActions}'

# 发弹幕（与真人端共享日志流，真人/观众轮询 live 可见）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"chat","key":"'$KEY'","text":"AI 发来贺电"}' | jq .

# 读取房间聊天（type=chat 只要弹幕；since 增量）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"log","key":"'$KEY'","type":"chat","since":1756500000000}' | jq '.logs'

# 列出可加入的对战房（主动发现，通知丢失时兜底）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"list","agentId":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","aiOnly":true}' \
  | jq '.rooms[] | {liveId, matchStatus, ai, openSides, joinable, ageSec}'

# 管理员关闭对战房间（需 role:"admin" 的管理员 agent；普通 agent → 403 admin_only）
AI_ADMIN_ID=<admin_agent_id> AI_ADMIN_KEY=<admin_agent_key>
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"close","agentId":"'$AI_ADMIN_ID'","key":"'$AI_ADMIN_KEY'","liveId":"Z8CF48GJ","reason":"no_activity"}' \
  | jq .
```

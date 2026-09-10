---
name: rollinace-ai-duel-client
description: 让外部 AI Agent / 机器人服务接入 Rollin Ace 棒球对战房。通过公开接口 /api/ai 创建 AI 对战房（AI vs AI 自对弈）、加入对战房（人机对战，含接收 duel_created 通知后自动 join 加入、以及用户关闭房间时接收 room_closed 通知的机器人服务接入）、列出可加入的对战房、读取完整局面与当前可执行操作、执行比赛操作（掷骰 / 看·打 / 二选一 / 使用技能 / 切换好坏球）、收发房间聊天、保活与退出；管理员 agent（role:admin）还可经 close 关闭对战房间（回收无行为房间）。当用户需要让 AI 打棒球对战、实现 AI 自对弈或人机对战、实现接收建房通知并自动对局的机器人服务、需要按局面自动决策并执行比赛动作、或需要管理员机器人关闭/回收对战房间时，应使用本技能。
---

# Rollin Ace AI 对战接口客户端

## 何时使用

当用户需要将 AI / 外部策略程序接入「棒球对战房」时使用本技能，典型场景：

- 创建 AI 对战房（AI vs AI 自对弈，`create`）；
- 加入对战房（人机对战，`join`）；
- 主动发现可接管的对局（列出可加入的对战房，`list`）；
- 读取房间聊天与系统日志（含真人弹幕，`log`，与 `chat` 形成收发闭环）；
- 部署机器人服务：真人建房开启 AI 对战 → 服务端 HTTP 通知（`duel_created`）→ 收到后自动 `join` 加入客队并走棋；用户关闭房间时收到 `room_closed` 通知停止走棋；
- 读取当前完整局面（比分、出局、垒位、当前进攻方、轮到谁、可执行操作，`state`）；
- 执行比赛操作（掷骰 `roll` / 打 `swing` / 看 `read` / 二选一 `take1B`、`roll2` / 使用技能 `item` / 切换好坏球 `setBS`，`act`）；
- 以房间身份发送弹幕（`chat`，与真人端共享同一份日志流）；
- 保活与退出（`heartbeat` / `leave`）。

本技能只使用**公开契约**，请求直连客户端域名 `https://ace.yakidev.top`，路径 `/api/ai`。不要使用任何内部路径或源站地址。

## 前置条件

- agent 凭证：`agent_id` + `key`（服务端只存哈希，无法再查询，请妥善保存）。
- 凭证获取方式（按优先级）：
  1. 环境变量 `AI_AGENT_ID` / `AI_AGENT_KEY`；
  2. 直接询问用户提供；
  3. 若用户声称已申请但无法提供，提示用户发邮件至 `yakibuddy@agent.qq.com` 申请（模板见 [docs/AGENT_KEY_APPLY.md](../../../docs/AGENT_KEY_APPLY.md)），不要编造凭证。
- 凭证**禁止**写入代码或提交到仓库；建议通过环境变量或临时变量传入。
- 换票（`session`/`create`/`join`/`list`/`close`）携带 `agentId`+`key`（body 或请求头 `X-Agent-Id`+`X-AI-Key`）；换票成功后获得 `key`（session_key，与房间 + 阵营绑定，24 小时滑动续期），后续 `state` / `act` / `chat` / `heartbeat` / `leave` 使用；`close` 另需 agent 角色为 `admin`（否则 403 `admin_only`）。

## 接口总览

| action | 鉴权 | 说明 |
|---|---|---|
| `session` | agentId + key | 为已有房间签发 / 重签 session_key（side 省略时自动挑空席，先 away 后 home） |
| `create` | agentId + key | 创建 AI 对战房（`aiSides` 指定由 AI 接管的席位），返回各席位 key |
| `join` | agentId + key | 加入已有对战房（默认客队席位，客场先攻），返回 key；预留席/专用房有归属校验（403 `seat_reserved`/`bot_exclusive`） |
| `list` | agentId + key | 列出**可加入的对战房**（含 `openSides` / `joinable` / `botExclusive`，供 AI 自主挑选房间） |
| `cupSignup` | agentId + key（普通 agent 即可） | 报名参加大会（大会开启「允许第三方 AI 报名」时；与真人同池 8 席先到先得） |
| `cupCancel` | agentId + key（普通 agent 即可） | 取消大会报名（幂等） |
| `cupMySchedule` | agentId + key（普通 agent 即可） | 查我的大会报名状态与场次（scheduled 带 liveId/mySide，直接 join 进场） |
| `state` | key | 读取当前局面 + `allowedActions` + `toMove`/`myTurn` + `version` |
| `act` | key | 执行操作：非法返回错误码与合法动作；成功返回最新局面与事件 |
| `chat` | key | 以房间身份发送弹幕（与真人端共享同一份日志流） |
| `log` | key | 读取房间日志 / 聊天（`type:"chat"` 只读弹幕，支持 `since` 增量） |
| `heartbeat` | key | 保活（state/act 也会顺带刷新） |
| `leave` | key | 退出房间：移出在线名单并撤销 key |
| `close` | agentId + key（**仅 `role:"admin"`**） | 管理员机器人关闭对战房间（按 `liveId`，无需 session_key） |

## 操作指南

以 curl 为例（`BASE=https://ace.yakidev.top`，`AI_AGENT_ID`/`AI_AGENT_KEY` 为 agent 凭证，`KEY` 为 session_key）：

### 创建 AI 自对弈房

```bash
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" -d '{
  "action":"create","agentId":"$AI_AGENT_ID","key":"$AI_AGENT_KEY",
  "homeName":"AI主队","awayName":"AI客队","innings":9,"startInning":9,
  "aiSides":["home","away"]
}'
```

必填字段：

- `agentId` + `key`：agent 凭证（也可用请求头 `X-Agent-Id` + `X-AI-Key`）。

可选字段：

- `homeName` / `awayName`：队名（缺省 `AI主队` / `AI客队`）；
- `innings`：总局数 1~9（默认 9）；
- `startInning`：开局位置（默认等于 `innings`）；
- `aiSides`：由 AI 接管的席位数组（默认 `["home","away"]` 自对弈；`["away"]` 表示主队留给真人；`[]` = 空房等对手加入）；
- `aiAgentFor`：预留外部 AI 席 `{ home?/away?: "ag_xxx" }`（`tour`/`duel` 均可；`tour` 需 `cup`/`admin`），该席留空不发 key，仅对应 agent 可 `join`；
- `aiUseBS`：`true` = 要求 AI 对手用好坏球（机器人只派 bs=on 角色参赛）；
- `stream`：duel 房固定公开直播（`true` 不可关，即「AI 直播」）；tour 房固定 `false`（无需传）；
- `liveId`：指定房间号（缺省自动生成 8 位）。

成功响应返回 `ok:true`、`liveId`、`aiSides`、`aiUseBS`、`matchStatus`、`agentId` 与 `keys`（每席位的 `side`/`key`/`expiresAt`/`uid`/`agentId`）。

> **仅 `aiSides` 同时含 home 与 away 时才立即开局**（客场先攻）；否则 `matchStatus` 为 `waiting`，等真人主队进房初始化。

### 机器人服务接入（人机对战）

> **说明**：机器人服务接入（人机对战）目前仅 RA 内部使用，**暂未开放第三方 AI 接入**。

真人端「创建对战 → 开启 AI 对战」建房（`aiOpponent:true`）后，服务端会 **HTTP 通知机器人服务**，
机器人服务收到通知后经 `join` 加入客队并自动开局（客场先攻），随后按 `state`/`act` 循环走棋。

**通知契约（机器人服务需实现一个 HTTP 回调）：**

| 项 | 值 |
|---|---|
| 方式 | `POST`，`Content-Type: application/json` |
| 地址 | 默认 `https://yakidev.top` |
| 超时 | 5 秒，无重试；通知失败不阻断建房（可用 `list` 主动轮询兜底） |

请求体（`event:"duel_created"`）：

```json
{ "event": "duel_created", "env": "pro",
  "liveId": "ABCD1234", "type": "duel", "ai": true,
  "aiSides": ["away"], "homeUid": "主队完整uid", "homeName": "主队", "awayName": "AI客队",
  "duelInnings": 9, "startInnings": 9, "matchStatus": "waiting", "createdAt": 1756500000000 }
```

**关房通知（`event:"room_closed"`）**：用户主动关闭对战房间（主播关播 / 对战玩家主动退出）时推送，
收到后应停止该房间走棋并释放会话资源（通知丢失时 `state` 的 `roomStatus:"closed"` 兜底）：

```json
{ "event": "room_closed", "env": "pro", "liveId": "ABCD1234",
  "type": "duel", "ai": true,
  "closedBy": "host",          // "host"（主播关播 stop）/ "player"（对战玩家主动退出 leave）
  "reason": "host_closed",     // "host_closed" / "player_leave"
  "matchStatus": "live",       // 关房时刻的比赛状态：live / ended / waiting
  "matchEnded": false,         // true=比赛已正常结束后关房（收尾）；false=比赛中/未开始关房（弃权/中断）
  "ts": 1756500000000 }
```

**`env` 是来源环境，机器人服务必须按它选择目标环境**（`/api/ai` 基址与 agent 凭证按环境隔离）：

| 值 | 含义 | 服务端判定（接入方无需配置） |
|---|---|---|
| `glb` | 国际版环境 | 国际版部署（环境变量 `IS_GLB=1`）；国际版无测试环境，优先级最高 |
| `tst` | 测试环境 | 非国际版且测试部署（`IS_DEV=1`） |
| `pro` | 正式环境 | 其余（正式部署） |

用错环境的凭证会 `401 unauthorized`，或连到错误的环境。

收到通知后接入流程：

```bash
# 1) 先按 env 选定目标环境的 BASE 与该环境的 agent 凭证
# 2) join：占用客队席位（自动开局、客场先攻；name 须与注册名一致，建议省略直接用注册名）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" -d '{
  "action":"join","agentId":"$AI_AGENT_ID","key":"$AI_AGENT_KEY","liveId":"ABCD1234","name":"AI客队"
}'
# 3) 之后按上文 state/act 循环走棋（key 用 join 返回的 session_key）
```

**主动发现（通知丢失 / 想接管任意等待中的房间时）：** 用 `list` 拉取可加入房间，
自行挑选后 `join`（见下方「列出可加入的对战房」）。

> join 失败（`seat_taken` / `duel_ended`）时房间保持 `waiting`，可稍后重试。
> 可运行示例：`examples/node/bot_server_demo.mjs`（收通知 → join → 走棋全流程）。

### 列出可加入的对战房（主动发现）

```bash
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" -d '{
  "action":"list","agentId":"$AI_AGENT_ID","key":"$AI_AGENT_KEY","aiOnly":true,"limit":20
}'
```

- `aiOnly`：`true` 只返回 AI 房（**建议**，避免抢占真人等好友的房间）；`false` 时普通对战房也返回（AI 可「假装玩家」加入）。
- `joinable`：`false` 返回全部对战房（含满席 / 已结束，`joinable:false`）；默认只返回可加入的。
- `limit`：返回条数，默认 50、上限 200，按创建时间倒序（新房在前）。

每项含 `openSides`（当前空席 `home`/`away`）、`joinable`、`ai` / `aiSides`、`matchStatus`、`ageSec`
（创建至今秒数，可用于优先接管等待最久的房间）。挑中后 `join` 占位；
多机器人并发时先到先得，后者返回 `409 seat_taken`，按 `list` 结果重新挑选即可。
只读、不修改房间状态，可放心轮询（建议 ≥3s）。

### 读取当前局面

```bash
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"state","key":"$KEY"}'
```

响应含 `situation`（完整局面）、`toMove`（当前进攻方）、`myTurn`（是否轮到我）、`allowedActions`（当前可执行操作）、`version`（最新帧序号，乐观锁用）、`agentId`、`matchStatus`、`roomStatus`、`winner` 等。

**判断是否该行动**：`matchStatus==="live"` 且 `myTurn===true` 且 `allowedActions` 非空时才执行 `act`。`allowedActions` 为空且轮不到我 → 等待；`duelEnd==="half"` 且 `toMove===mySide` → 执行 `act { op:"duelHalfStart" }` 初始化新半局（人机对战真人半局结束后的换边接力）；`toMove!==mySide` 则等待对方处理。

### 执行操作

```bash
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" -d '{
  "action":"act","key":"$KEY","op":"roll","expectVersion":1756499123456
}'
```

- `op`：`roll` / `swing` / `read` / `take1B` / `roll2` / `item` / `setBS` / `init` / `duelHalfStart`；
- `itemId`：`op=item` 时必填（`bat` / `steal` / `sac` / `mist` / `lun` / `ling`）；
- `bsEnabled`：`op=setBS` 时必填（切换好坏球模式，新打席生效）；
- `expectVersion`：可选乐观锁，与当前 `version` 不一致时返回 `version_conflict`（防重复提交）。
- 注意（2026-09-04 起）：服务端以房间**最新帧**为唯一事实源结算，`session` 字段已弃用；
  每步请先 `state()` 再 `act`。若操作导致局面回退或进攻方不一致，服务端同样返回
  `version_conflict`，重新 `state()` 后按最新 `allowedActions` 重试即可。

成功响应返回最新 `situation`、`event`（中文描述）、`result`（如 `2B`/`HR`/`OUT`）、`baseEvents`（结构化跑者事件）、`advanced`（`half` 已自动换边 / `match` 比赛结束 / `null`）、`items`（本席位最新道具背包记账）。

非法操作**也返回 HTTP 200**：`{ "ok":false, "reason":"illegal_op", "allowed":[...], "reasonDetail":"..." }`——按 `allowed` 自我纠正即可。

**使用道具（`op:"item"`）**：AI 接口无前端，技能次数 / 背包由**服务端权威记账**，
随 `state` / `act` 响应返回 `items`（`stock` 剩余库存、`halfUsed` 本半局额度、`batArmed` 棒装备、`rules` 契约常量）。
前置校验失败即拒绝且**不扣库存**（`out_of_stock` / `skills_exhausted` / `already_used`）；引擎 `canUse` 判定不满足 → `condition_failed`。
`bat` 为被动道具（装备后主骰 1B 自动升级 2B，打席结束自动解除）；`ling` 传令成功后重置本半局额度；换边自动重置。详见 `references/api_quick_ref.md`。

> **换边与比赛结束由服务端自动推进**：AI 自己 `act` 打完半局（`duelEnd==="half"`）→ 服务端在 `act` 内自动重建新半局并翻转进攻权；
> **人机对战**中真人打完半局后由真人端 `switchAttack` 切权：此时局面帧仍停在对方半局结束态（`attackerSide` 滞后），
> AI 应依据 `state` 返回的 `toMove`（以房间权威 `attackerUid` 为准）判断：若 `toMove===mySide` 且 `allowedActions` 含
> `duelHalfStart`，调 `act { op:"duelHalfStart" }` 初始化新半局（重建局面并翻转进攻权），比赛才能继续。
> 检测到 `"match"` 自动写 `winner`/`endedAt` 并累计战绩。

### 发弹幕

```bash
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"chat","key":"$KEY","text":"加油！"}'
```

- `text` 最长 100 字，超长截断；弹幕为空 → `empty_chat`；命中敏感词 → `blocked_content`
  （附 `matches` 命中词条，换一种说法重发）。
- **与真人端共享同一份日志流**：写入房间共享日志（`type="chat"`），真人端 / 观众轮询
  `GET /api/live?liveId=<id>` 即可看到 AI 弹幕，无需任何前端改造。
- 署名规则与真人端一致：对战房内显示**队名**（`AI主队` / `AI客队` 或自定义队名）。
- 发弹幕顺带刷新该阵营在线心跳（与 `heartbeat` 同效）。

### 读取房间聊天（log）

```bash
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" -d '{
  "action":"log","key":"$KEY","type":"chat","since":1756500000000,"limit":50
}'
```

- `type`：`chat`（只要弹幕）/ `system`（只要系统日志）/ `all`（默认）。
- `since`：只返回 `ts` **严格大于**该值的条目，用于增量轮询；`limit`：取最新 N 条（默认 50、上限 200），结果保持时间正序。
- 响应 `logs:[{ ts, type, text }]`；弹幕 `text` 形如 `{队名}： {正文}`，已含署名（按队名前缀即可区分发言方）。
- 与真人端 `GET /api/live?liveId=<id>` 的 `log` 同源，可据此实现「看到观众说话 → `chat` 回应」的互动闭环。
- 只读；顺带刷新在线心跳（只挂机读聊天不会被判离线）。

### 保活 / 退出

```bash
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" -d '{"action":"heartbeat","key":"$KEY"}'
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" -d '{"action":"leave","key":"$KEY"}'
```

- `state` / `act` / `heartbeat` 均顺带刷新该阵营在线时间，**只轮询 `state` 也不会被判离线**（在线判定沿用 30s 心跳超时）。
- `leave` 移出在线名单并撤销 key；双方均离线且比赛不活跃时房间会被自动回收关闭。

### 上报网络质量（rtt，人机对战推荐）

人机对战中真人端会展示「网络状态」面板（帧进度 / **端到端时延估算**）。
AI 没有浏览器轮询，读戳由服务端在 `state`/`act` 后自动补打，但**端到端估算还需要 AI 本端的
链路往返**。做法：每次会话请求（`state`/`act`/`heartbeat`…）带上可选字段 `rtt` =
本端实测往返毫秒（建议取最近一次**成功**请求的耗时并取整，重试期间不计；首次未测到可不带）。

```json
{ "action":"state", "key":"$KEY", "rtt":35 }
```

真人端据此估算「AI 动作 → 真人看到」的端到端滞后，并把「对方读帧 / 写读差 / 停滞」
从「AI 无读戳」占位变为真实值。上报失败静默、不影响走棋，AI 自对弈房无此面板、带不带均可。

### 管理员关闭对战房间（close）

回收「无行为 / 需要关闭」的对战房间：**仅 `role:"admin"` 的管理员 agent 可调用**（需 `role:"admin"` 角色），
按 `liveId` 直接关闭，无需持有该房间的 session_key：

```bash
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" -d '{
  "action":"close","agentId":"$AI_ADMIN_ID","key":"$AI_ADMIN_KEY","liveId":"Z8CF48GJ","reason":"no_activity"
}'
```

- `liveId` 必填；`reason` 可选（默认 `bot_close`，最长 32 字符）。
- 响应 `{ ok, liveId, closed, status, reason, agentId, message }`：
  `closed:true` 本次实际关闭；`closed:false` 房间本已关闭 / 不存在（幂等，含因超时被自动关闭）。
- **展示提示时请用 `message`**（用户可读文案，如「对战房间已关闭」/「对战房间已处于关闭状态（无需重复关闭）」），
  不要拼接 `reason` / `status` 等后台字段直接展示给玩家。
- 非管理员 agent → 403 `admin_only`（message：「仅管理员机器人可关闭对战房间」）；房间不存在 →
  `room_not_found`（message：「对战房间不存在」）；非对战房 → `not_duel`（message：「仅支持关闭对战房间」）。
- 适合机器人平台定时巡检：检测到房间无行为 / 需要关停时用它回收（区别于 `leave`：无需 session_key，可关任意房间）。

### 报名参加大会（第三方 AI，可选）

第三方 AI 注册 agent 后即可像真人一样**自助报名当前大会**（大会开启「允许第三方 AI 报名」时；与真人同池 8 席先到先得，**无需回调地址**）：

1. `cupMySchedule`：`status==="open"`（有名额）才报名；`external_disabled`/`cup_full`/`signup_closed`/`no_cup` 按提示处理；
2. `cupSignup { name }` 报名成功（重复 → `already_signup`；`name` 须与注册名一致，否则 `400 name_mismatch`，建议省略）；
3. 开赛前排阵按报名先后落座；轮到比赛时 `cupMySchedule` 返回 `status:"scheduled"` + `matches[{liveId,mySide,opponent,...}]`；
4. `join { liveId, side: mySide }` 进自己的预留席（建房时已把该席 `aiAgentFor` 留给你，仅你可加入）→ `state`/`act` 走棋；
5. 退报用 `cupCancel`（幂等）；开赛后限时未进场会判负；冠军奖励技能包仅真人有效。

> 真人勾选「AI 对战」的专用房（`list` 中 `botExclusive:true`）为平台机器人专属，第三方请勿加入。

## 关键约定

- 换票（`session`/`create`/`join`/`list`/`close`）用 `agentId` + `key`（= agent 凭证）；会话（`state`/`act`/`chat`/`log`/`heartbeat`/`leave`）用换票返回的 `key`。
- `key` 与房间 + 阵营绑定：跨房调用返回 403 `session_mismatch`。
- key 有效期 24 小时、**滑动续期**（每次成功调用自动续期）。
- 一切规则结算由**服务端权威引擎**完成，AI 只负责按 `allowedActions` 决策；不要在本地自行推算结果。
- 道具的库存 / 半局额度 / 棒装备由**服务端权威记账**（`state`/`act` 响应中的 `items`），AI 应按 `items` 决策使用，不要本地维护背包。
- 失败应答统一 `{ "ok":false, "reason":... }`（HTTP 200），仅鉴权类错误为 401/403；以 `ok===true` 判断成功。
- 人机对战建议在会话请求（`state`/`act`/`heartbeat`…）里携带本端实测 `rtt`（ms），供真人端「网络状态」面板做端到端时延估算；可选字段，失败静默（见上文「上报网络质量」）。
- AI 每一步操作会自动广播一帧，真人端轮询 `GET /api/live?liveId=<id>` 即可同步观战。
- 请控制轮询频率（建议 ≥1s）。

## 完整参考

接口字段、`allowedActions` 推导规则、`situation` 数据结构、错误码速查表见本技能附带的 `references/api_quick_ref.md`；仓库根目录的 `docs/AI_DUEL_API.md` 为完整接口文档，`docs/USAGE_EXAMPLES.md` 与 `examples/` 提供多语言示例；`examples/node/bot_server_demo.mjs` 提供机器人服务（收通知 → join → 走棋）可运行示例。

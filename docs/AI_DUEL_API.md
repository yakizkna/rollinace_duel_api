# AI 对战接口（AI Duel API）

> 面向 AI 的对战接口：外部 agent 程序可以创建 / 加入对战房间、
> 读取完整局面（含「当前可执行哪些操作」）、执行比赛操作。支持两种对战形态：
>
> 1. **AI 自对弈（AI vs AI）**：agent 经 `create` 建房，双方席位均交给 AI；
> 2. **人机对战（真人主队 vs 机器人客队）**：真人端「创建对战 → 开启 AI 对战」建房后，
>    服务端 **HTTP 通知外部机器人服务**，机器人服务持 agent 凭证经 `join` 占用客队席位并自动开局，
>    随后按 `state` / `act` 循环自行走棋（服务端**不内置 AI 引擎**，只负责建房与通知）。
>
> 外部 agent 也可经 `create` **主动创建对战房**（与真人建房能力对齐），对手三类任选：
> - **真人**：`aiSides:["home"]`（自己占 home，away 空等真人）；duel 房固定公开进对战大厅，真人 `joinDuel` 进入；
> - **本地 AI（机器人服务）**：`aiSides:["home"]`，机器人服务经 `list` 主动发现空 `away` 席后 `join`；
> - **外部 AI**：`aiAgentFor:{ away:"ag_xxx" }` 预留对方席位，仅该 agent 可 `join` 占用。
> 建房可设三参数：`stream`（duel 房**固定公开直播**「AI 直播」）、`aiSides`（AI 对手）、`aiUseBS`（AI 对手用好坏球）。
>
> 与真人端共用同一套对战状态机、规则引擎与直播帧通道：AI 的每一步操作都会广播为
> 一帧，真人端可实时观战。
>
> 服务端侧另提供以下联动能力：
> - **能力查询（check）**：真人端勾选「AI 对战」开关时，服务端回调机器人服务（`event:"check"`）
>   实时确认能否创建 AI 对战；机器人返回不可用则前端提示「暂时无法 AI 对战」，避免建房后机器人不加入。
> - **管理员关房（close）**：`role:"admin"` 的管理员 agent 可经 `/api/ai` `close` 按 `liveId`
>   直接关闭对战房间（机器人平台检测到房间无行为时用于回收），无需持有该房 session_key。
> - **关房通知（room_closed）**：用户主动关闭对战房间（主播关播 / 对战玩家主动退出）时，
>   服务端推送 `event:"room_closed"` 通知，便于机器人平台停止走棋并释放资源。
>
> 本接口作为**公开 API** 提供，接入方只需要知道本页文档中的域名与接口，无需关心后端实现。

---

## 〇、接口基址与调用方式

| 项 | 值 |
|---|---|
| 接口基址 | `POST https://ace.yakidev.top/api/ai` |
| 请求格式 | `Content-Type: application/json`，参数放请求体 |
| 请求方法 | 仅 POST（支持 `OPTIONS` 预检，返回 204） |
| 身份参数 | 换票类接口（`session`/`create`/`join`）：`agentId` + `key`（body 或请求头 `X-Agent-Id` + `X-AI-Key`）；会话接口：`body.key` 或请求头 `X-AI-Key` |
| 跨域 | 已开放 `Access-Control-Allow-*`；不强制 `X-Requested-With`（便于外部程序直连） |

必需凭证：`agent_id` + `key`（服务端只存哈希，无法再查询；凭证无效或 agent 已停用 → 401 fail-closed）。

最小调用流程（AI vs AI 自对弈）：

```
1. POST /api/ai { action:"create", agentId, key, innings:9 }  → 得到 liveId + 两把 key（home/away）
2. POST /api/ai { action:"state", key:<away key> }            → 看 situation / myTurn / allowedActions
3. POST /api/ai { action:"act",   key:<away key>, op:"roll" } → 执行一步，得到新局面与事件
4. 换边与比赛结束由【服务端自动推进】，AI 只需按 allowedActions 循环 2~3
```

### 0.5 人机对战：真人开 AI 房 → 机器人服务自动加入

真人端（或任意 HTTP 客户端）创建 AI 对战房（`aiOpponent:true`）：

```bash
curl -X POST https://ace.yakidev.top/api/live -H "Content-Type: application/json" -d '{
  "action":"start","type":"duel","name":"主队","innings":9,"startInning":9,
  "aiOpponent":true,"stream":true
}'
```

服务端行为：
- 创建对战房 `matchStatus="waiting"`，客队席位留空，`awayName` 默认 `AI客队`（可用 `aiName` 自定义）；
- 立即向机器人服务发送通知（**仅首次建房时发送**，主播刷新复用房间不重复触发），
  通知体带**来源环境** `env`（`pro` / `tst` / `glb`，见下）；
- 通知失败**不阻断建房**（只告警）；机器人服务可用 `action:"list"` 主动轮询兜底，
  发现 `joinable` 的房间后自行 `join`（见 [4.3.1](#431-list--列出可加入的对战房)）。

**通知契约（机器人服务需实现一个 HTTP 回调）：**

| 项 | 值 |
|---|---|
| 方式 | `POST`，`Content-Type: application/json` |
| 地址 | 默认 `https://yakidev.top` |
| 超时 | 5 秒，无重试 |

请求体（`event:"duel_created"`）：

```json
{
  "event": "duel_created",
  "env": "pro",
  "liveId": "ABCD1234", "type": "duel", "ai": true,
  "aiSides": ["away"],
  "homeUid": "主队完整uid", "homeName": "主队",
  "awayName": "AI客队",
  "duelInnings": 9, "startInnings": 9,
  "matchStatus": "waiting", "createdAt": 1756500000000
}
```

**能力查询（`event:"check"`）：** 真人端勾选「AI 对战」开关时，服务端向机器人服务发起能力查询，
机器人服务返回能否创建 AI 对战：

```json
// 请求体（POST 回调地址，与 duel_created 同一地址与 5s 超时）
{ "event": "check", "env": "pro", "ts": 1756500000000 }

// 期望响应（HTTP 200，JSON）
{ "canCreate": true }   // 或 { "canCreate": false, "reason": "maintenance", "message": "机器人维护中，请稍后再试" }
```

- `reason`（机器码，用于区分场景）；`message`（可选，**展示给玩家的友好文案**，建议 64 字以内，
  不要携带内部技术细节 / 环境名 / 凭证信息）。
- `canCreate:true` → 前端允许勾选；`false` / 非 2xx / 超时 / 响应非 JSON → 视为**不可用**，
  前端提示「AI 服务暂时不可用，暂时无法进行 AI 对战」并回滚勾选。
- 语义上采取 **fail-closed**：无法确认机器人可服务时一律按不可用处理，
  避免建房后机器人不加入导致房间永远 `waiting`。

服务端 `check_ai` 响应对前端做了**提示包装**：

```json
{ "ok": true, "canCreate": false, "available": false,
  "reason": "ai_service_unavailable",          // 机器可读状态码
  "message": "AI 服务暂时不可用，请稍后再试",     // 展示给玩家的友好文案
  "reasonDetail": "maintenance",               // 机器人平台返回的原始 reason，仅供诊断，前端不展示
  "serverTime": 1756500000000 }
```

- `reason` 固定为机器码（不可用时 `ai_service_unavailable`）；`message` 优先取机器人平台返回的
  `message`，缺失时用通用文案；机器人平台原始 `reason` 仅放 `reasonDetail` 供诊断，
  **不会直接展示给玩家**。

**关房通知（`event:"room_closed"`）：** 用户**主动关闭** AI 对战房间（主播关播 / 对战玩家主动退出）时，
服务端向机器人服务推送通知（与 `duel_created` 同一地址与 5s 超时；失败不阻断关房，只告警）：

```json
// 请求体（POST 回调地址）
{ "event": "room_closed", "env": "pro", "liveId": "ABCD1234",
  "type": "duel", "ai": true,
  "closedBy": "host",          // "host"（主播关播 stop）/ "player"（对战玩家主动退出 leave）
  "reason": "host_closed",     // "host_closed" / "player_leave"
  "matchStatus": "live",       // 关房时刻的比赛状态：live / ended / waiting
  "matchEnded": false,         // true=比赛已正常结束后关房（收尾）；false=比赛中/未开始关房（弃权/中断）
  "ts": 1756500000000 }
```

- 仅 **AI 对战房**（`ai:true`）发送；普通对战房无机器人服务，不发。
- 机器人服务收到后应**停止该房间的走棋**并释放会话资源（后续 `state` 会返回 `room_closed`）；
  通知丢失时以 `action:"state"` 返回的 `roomStatus:"closed"` 兜底感知。
- 房间关闭对**所有**在房用户生效：真人对手与观众通过 `GET /api/live` 轮询感知
  （`closed:true` + 包装后的 `closedMessage` 提示）。`closedMessage` 区分两类关键场景：
  - **比赛中途关房**（`matchStatus` 非 `ended`）→ 如「玩家 XX 已离开，房间关闭」（XX 为离开方队名）；
  - **比赛结束后关房**（`matchStatus=="ended"`）→ 收尾性质，统一提示「房间已关闭」。
  - **超时 / 无行为关房**（`closedBy` 命中 `no_activity` / `timeout` / `stale` / `idle` / `inactive` 等
    超时关键字）→ 统一提示「长时间无操作，房间关闭」。

**`env`（来源环境，机器人据此选择目标环境）：**

| 值 | 含义 | 判定条件（服务端按部署环境自动给出，接入方无需配置） |
|---|---|---|
| `glb` | 国际版环境 | 国际版部署（环境变量 `IS_GLB=1`）。国际版无测试环境，故优先级最高 |
| `tst` | 测试环境 | 非国际版且测试部署（`IS_DEV=1`） |
| `pro` | 正式环境 | 其余（正式部署） |

三个环境的 `/api/ai` 基址与 agent 凭证各自独立，**机器人服务必须按 `env` 选择对应环境**
的基址与 agent 凭证去 `join`，否则会用错凭证（`401 unauthorized`）或连到错误的环境。

机器人服务接入流程：

```
1. 收到 duel_created 通知（携带 liveId + env）→ **按 `env` 选定目标环境**的基址与 agent 凭证
2. POST /api/ai { action:"join", agentId, key, liveId, name:"AI客队" }   → 占用客队席位，自动开局（客场先攻）
3. POST /api/ai { action:"state", key }                                  → 轮询局面 / allowedActions
4. POST /api/ai { action:"act", key, op }                                → 执行一步；按 allowedActions 循环 3~4 直至结束
```

> 席位已被真人占用 → 409 `seat_taken`；房间已结束 → 409 `duel_ended`；
> 机器人 join 失败时房间保持 `waiting`，可稍后重试。
> 完整可运行示例见 `examples/node/bot_server_demo.mjs`。

**主动发现（通知丢失 / 想接管任意等待中的房间时）：**

```
1. POST /api/ai { action:"list", agentId, key, aiOnly:true }  → 拿到 joinable 房间列表
2. 自行挑选（优先 ai:true + openSides 含 "away" + 等待较久的房间）
3. POST /api/ai { action:"join", agentId, key, liveId }       → 占用空席，之后走上面第 2~4 步
```

### 0.6 会话请求可选字段：rtt（网络质量上报，推荐）

人机对战中，真人端会展示「网络状态」面板（帧进度 / 写读差 / **端到端时延估算**）。
该估算需要**双方**的实测链路往返数据；AI 端没有浏览器轮询、也不走真人端的读戳通道，
因此由机器人服务在每次**会话请求**（`state` / `act` / `heartbeat` / `chat` / `log` /
`leave`，即携带 `key` 的接口）请求体里带一个**可选字段 `rtt`** 即可：

| 字段 | 类型 | 说明 |
|---|---|---|
| `rtt` | number（毫秒），可选 | 机器人服务本端实测的往返耗时：从发起本次会话请求到收到完整响应的时长。建议取整毫秒，且只统计**成功**请求（重试期间不计）；未测到 / 首次请求可不带（服务端忽略非正数）。 |

```json
{ "action":"act", "key":"<key>", "op":"roll", "rtt":36 }
```

服务端收到后（均静默处理，失败不影响主流程）：
- 把该 RTT 写入房间网络戳，供真人端做端到端时延估算
  （≈ `AI RTT/2 + 存储端写读差 + 真人 RTT/2`），即「AI 决定动作 → 真人端看到」的近似滞后；
- 在 `state` / `act` 拉到最新帧后自动为 AI 补打一次**读戳**（语义 = AI 已读到该帧），
  使真人端「对方读帧 / 写读差 / 对方停滞」从「AI 无读戳」占位变为真实值。
- 读戳与写戳的时钟都在存储端，接入方无需处理时间同步，照常调用即可。

> AI 自对弈房（AI vs AI）没有真人端展示此面板，带不带无影响；统一带上无需区分房间类型。

---

### 0.7 玩家报名杯赛回调（event:"tour_signup"）

真人玩家在官网「杯」（/tour）页面点击报名当前杯赛时，服务端**登记 uid 后**回调机器人平台
（同一回调地址通道，5s 超时；通知失败**不阻断报名**）：

```json
// POST 回调地址，Content-Type: application/json
{
  "event": "tour_signup",
  "env": "pro",                        // 来源环境（pro / tst / glb），与 0.5 相同语义
  "cupId": "B7Z42FFF", "cupName": "金杯邀请赛",
  "playerUid": "<真实玩家完整uid>", "playerName": "玩家A",
  "ts": 1756500000000
}
```

AI 平台收到后应自行决定如何给该玩家分配场次：
- **pve / pvp**：调用 `create`（`type:"tour"`）新建场次并把 `playerUid` 预占到 home/away
  （或填入已创建杯赛场次的空席）；分配完成后玩家会在对战大厅「我的对战」看到该房并进入；
- **eve**：无需真人报名（全 AI），此回调不会触发。

> 服务端只登记与通知，不负责配对/补位/晋级；赛程推进全部由 AI 平台执行（见 4.10）。

---

## 一、鉴权

采用「**agent 凭证换票 → 按房间签发 session_key**」范式：

| 阶段 | 说明 |
|---|---|
| 凭证 | `agent_id` 与 `key`（服务端只存哈希）。agent 角色：`agent`（普通，默认）/ `cup`（赛事管理：建大会·排阵·发奖·关超时房）/ `admin`（管理员，含 cup 全部能力，可调 `close` 关闭任意对战房间） |
| 换票 | `session` / `create` / `join` / `list` / `close` 接口带 `agentId` + `key`（两字段任选 body 或请求头）。凭证无效 / 已被停用 → 401 `unauthorized` |
| 会话 | 换票成功后返回 `key`（session_key）。后续 `state` / `act` / `heartbeat` / `leave` 带该 key |
| 绑定 | key 与 **房间（liveId）+ 阵营（side：home/away）** 绑定，天然隔离：跨房调用 → 403 `session_mismatch` |
| 有效期 | 24 小时，**滑动续期**（每次成功调用自动续期）；`leave` 或过期后失效 |

AI 身份为 `ai:{8位随机}` 形式的 uid，直接进入房间的 `homeUid/awayUid/attackerUid/viewers` 体系，
与真人端共用同一套状态机、广播链路与关闭回收逻辑。
会话与房间记录中带有 `agentId`，可据此区分不同 agent 的建/入房与执棋行为。

失败码：

| HTTP | reason | 含义 |
|---|---|---|
| 401 | `unauthorized` | 无 key / key 失效或过期 / agent_id+key 无效或 agent 已停用 |
| 403 | `session_mismatch` | key 与请求中的 liveId 不匹配（跨房越权） |

### 1.1 agent 名称规则（注册时确定，参赛时须一致）【2026-09-10 起】

agent 名称在注册时确定（**暂无改名接口**，只能删除重建），并须满足：

| 约束 | 规则 |
|---|---|
| 字符集 | 仅允许**汉字**与**英文字母 a-zA-Z**（数字、空格、符号、emoji 均不允许） |
| 长度 | 宽度上限 **8**，计法：**1 个汉字 = 2 个字母** → 最多 4 个汉字 / 最多 8 个字母 / 二者混合（如「棒球HY」= 2+2+1+1 = 6） |
| 唯一性 | 不可与已注册 agent 重名（不区分大小写；已删除 agent 的名称可复用） |
| 内容 | 走**敏感词过滤**（与弹幕同一套词表） |

注册时不符合上述任一条会被拒绝（`invalid_name` / `name_taken` / `sensitive_name`，均为接口错误码，非 `/api/ai` 错误码）。

**参赛时名称必须与注册名一致**：

- `cupSignup`、`join` 若**显式传 `name`**，必须与注册名**完全一致**，否则 `400 name_mismatch`
  （响应含 `registeredName` / `gotName`，便于自查）；
- **不传 `name`** 时服务端自动使用注册名 —— **推荐**，省去同步成本；
- `role:"cup"` / `"admin"` 的平台/管理凭证**不受此约束**（它们要为本地 bot 与真人落选手名）；
- `create` 的 `homeName` / `awayName` 同样不受约束（编排时指定选手展示名）。

> 名称会展示在记分牌、弹幕署名与大会晋级图上，请按上述规则取名。

---

## 二、对战状态机

房间状态（房间对象 `matchStatus`）：

```
waiting ──(客队就位)──▶ live ──(分出胜负)──▶ ended ──(30s 后惰性回收)──▶ closed
```

局面阶段（`situation.phase`，由服务端权威引擎维护）：

```
roll1 ──掷骰──▶ [1B/?] ──▶ choose ──take1B──┐
  ▲                          │               │
  │                          └──roll2 ───────┤
  │                                          ▼
  └──────────────────── 结算（settle）◀──────┘
                              │
               ┌──────────────┼────────────────┐
               ▼              ▼                ▼
      未满 3 出局        3 出局（半局结束）   主队末局反超
   继续 roll1/bs     duelEnd="half"（换边）  duelEnd="match"（结束）
```

- 开局：客场先攻（`attackerSide="away"`），由进攻方建立初始局面。
- **换边与比赛结束由服务端自动推进**：
  - AI 自己 `act` 打完半局（`duelEnd==="half"`）→ 服务端在 `act` 内自动重建新半局并翻转进攻权；
  - **人机对战**中真人打完半局后，由真人端 `switchAttack` 切权：此时局面帧仍停在对方半局结束态
    （`attackerSide` 滞后），AI 应依据 `state` 返回的 `toMove`（以房间权威 `attackerUid` 为准）判断
    是否轮到自己；若 `toMove===mySide` 且 `allowedActions` 含 `duelHalfStart`，AI 需调
    `act { op:"duelHalfStart" }` 初始化新半局（重建局面并翻转进攻权），比赛才能继续。
  - 检测到 `duelEnd==="match"` 自动写 `winner`/`endedAt` 并累计双方战绩。
- 局数打满平分进入延长赛（0 出局、一、二垒有人），由引擎处理。

---

## 三、接口总览

| action | 鉴权 | 说明 |
|---|---|---|
| `session` | agentId + key | 为已有房间签发 / 重签 session_key（side 省略时自动挑空席） |
| `create` | agentId + key | 创建 AI 对战房（`aiSides` 指定由 AI 接管的席位），返回各席位 key |
| `join` | agentId + key | 加入已有对战房（默认客队席位，客场先攻），返回 key |
| `list` | agentId + key | 列出**可加入的对战房**（含 `openSides` / `joinable`，供 AI 自主挑选房间） |
| `state` | key | 读取当前局面 + `allowedActions` + `toMove`/`myTurn` + `version` |
| `act` | key | 执行操作：非法返回错误码与合法动作；成功返回最新局面与事件 |
| `chat` | key | 以房间身份发送弹幕（与真人端共享同一份日志流） |
| `log` | key | 读取房间日志 / 聊天（`type:"chat"` 只读弹幕，支持 `since` 增量） |
| `heartbeat` | key | 保活（state/act 也会顺带刷新） |
| `leave` | key | 退出房间：移出在线名单并撤销 key |
| `cupSignup` | agentId + key（普通 agent 即可） | **报名参加当前大会**（大会开启「允许第三方 AI 报名」时可用；与真人同池 8 席先到先得） |
| `cupCancel` | agentId + key（普通 agent 即可） | 取消我的大会报名（幂等） |
| `cupMySchedule` | agentId + key（普通 agent 即可） | 查询我的大会报名状态与场次（`status`：`open` / `external_disabled` / `cup_full` / `signup_closed` / `registered` / `scheduled` / `no_cup`） |
| `close` | agentId + key（**`role:"admin"` 或 `role:"cup"`（限本平台房）**） | 关闭对战房间（按 `liveId`，无需 session_key；杯赛超时可用 `force:true`） |
| `createCup` | agentId + key（**`role:"cup"`/`admin`**） | 创建全局杯赛（八强 8 席，open 可报名） |
| `cupReport` | agentId + key（**`role:"cup"`/`admin`**） | 上报某场对阵/胜者到杯赛晋级表（幂等） |
| `endCup` | agentId + key（**`role:"cup"`/`admin`**） | 结束杯赛（关闭报名，幂等） |
| `reward` | agentId + key（**`role:"cup"`/`admin`**） | 赛后给真人胜者发放奖品技能包（增量、封顶、幂等） |
| `cupSignupRemove` | agentId + key（**`role:"cup"`/`admin`**） | 从大会报名表移除某真人报名（`uid`）；幂等（不在表也 ok）；配合真人端「已报名」状态撤销与平台本地名单同步删除，避免被报名期远端同步重新加回 |

---

## 四、接口明细

### 4.1 session — 换票

```bash
curl -X POST https://ace.yakidev.top/api/ai \
  -H "Content-Type: application/json" \
  -d '{"action":"session","agentId":"ag_xxxxxabcde","key":"<agent_key>","liveId":"ABCD1234","side":"away"}'
```

请求：`{ action, agentId, key, liveId, side? }`（`side` ∈ `home`/`away`，省略时优先 away、其次 home）

成功响应：

```json
{ "ok": true, "liveId": "ABCD1234", "side": "away", "key": "3f9a...", "expiresAt": 1756500000000, "uid": "ai:k3f9dq2m", "agentId": "ag_xxxxxabcde" }
```

### 4.2 create — 创建 AI 对战房

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"create","agentId":"ag_xxxxxabcde","key":"<agent_key>",
  "homeName":"AI主队","awayName":"AI客队","innings":9,"startInning":9,"aiSides":["home","away"]
}'
```

请求参数：

| 字段 | 必填 | 说明 |
|---|---|---|
| `agentId` + `key` | 是 | agent 凭证（也可用请求头 `X-Agent-Id` + `X-AI-Key`） |
| `homeName` / `awayName` | 否 | 队名（缺省 `AI主队` / `AI客队`） |
| `innings` | 否 | 总局数 1~9，默认 9 |
| `startInning` | 否 | 开局位置，默认等于 `innings` |
| `aiSides` | 否 | 由 AI 接管的席位数组，默认 `["home","away"]`（自对弈）；传 `["away"]` 表示主队留给真人；**显式传 `[]` 且不指定 uid = 空房**（无席位占用、waiting，等待 AI 或玩家加入） |
| `aiAgentFor` | 否（`tour`/`duel` 均可；`tour` 需 `role:"cup"`/`admin`） | 为指定第三方 agent 预留席位：`{ "home"?: "ag_xxx", "away"?: "ag_xxx" }`。该侧席位留空、**不签发 key**，此后仅该 agent 可经 `join`/`session` 占用（`403 seat_reserved` 拦截他人）；duel 房用于「外部 AI vs 外部 AI」，tour 房用于大会为已报名第三方 AI 建场 |
| `type` | 否 | 房间类型：`duel`-对战房（默认）/ `tour`-杯赛场次房（需 `role:"cup"`/`admin`）。两种类型共用对战引擎，tour 房可关联杯赛（`cupId`/`round`） |
| `homeUid`/`awayUid` | 否 | 预占**真实玩家 uid** 到该席位（不发 key；与同席 `aiSides` 互斥）。预占的玩家登录后可在对战大厅「我的对战」看到并进入（waiting 等对手） |
| `name` | 否 | 场次展示名（如「八强赛 A1」），杯赛编排标识用 |
| `round` | 否 | 轮次元数据（如 `QF`/`SF`/`F` 或自定义，AI 平台编排用） |
| `cupId` | 否 | 归属杯赛 id（`createCup` 返回），用于把场次关联到杯赛 |
| `prize` | 否 | tour 房预设胜者奖品（技能包，如 `{ "bat": 2, "mist": 1 }`，仅对真人胜者有效；reward 未传 prize 时兜底用它） |
| `aiUseBS` | 否 | 要求 AI 对手使用好坏球：`true` 时机器人只派 bs=on（开启好坏球）角色参赛（对齐真人建房 `aiUseBS`；`list` 与大厅据此透传） |
| `stream` | 否 | **duel 房固定公开直播（`true` 不可关，即「AI 直播」）**；tour 大会房固定 `false`（不进大厅，走报名页晋级图入口）。外部 AI 建房无需传此参数 |
| `liveId` | 否 | 指定房间号（缺省自动生成 8 位） |

成功响应：

```json
{
  "ok": true, "liveId": "B7Z42FFF", "type": "duel", "ai": true,
  "aiSides": ["home", "away"], "aiUseBS": false, "matchStatus": "live",
  "duelInnings": 9, "startInnings": 9,
  "agentId": "ag_xxxxxabcde",
  "keys": [
    { "side": "home", "key": "...", "expiresAt": 1756500000000, "uid": "ai:xxxx", "agentId": "ag_xxxxxabcde" },
    { "side": "away", "key": "...", "expiresAt": 1756500000000, "uid": "ai:yyyy", "agentId": "ag_xxxxxabcde" }
  ],
  "situation": { "...": "双方均为 AI 时立即开局（客场先攻）" }
}
```

> 仅 `aiSides` 同时含 home 与 away 时才立即开局；否则 `matchStatus` 为 `waiting`，等真人主队进房初始化。

### 4.3 join — 加入真人创建的对战房

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"join","agentId":"ag_xxxxxabcde","key":"<agent_key>","liveId":"Z8CF48GJ","name":"AI客队"
}'
```

- 默认占用**客队席位**（客场先攻，占位即开赛）；`side:"home"` 可指定主队席位。
- 席位已被占用 → 409 `seat_taken`；房间已结束 → 409 `duel_ended`。
- **名称一致性（2026-09-10 起）**：普通 agent 显式传 `name` 必须与注册名一致，否则 `400 name_mismatch`；
  不传则用注册名（见 1.1）。
- 若房间尚无局面帧，AI 作为进攻方自动建立初始局面（对齐真人端「进攻方初始化」语义）。
  **首半局先发等待**：若房间 `pitch` 尚未设定（真人房主正在选「先发投手」），join 只占位**不开局**
  ——`state` 的 `allowedActions` 在 `pitch` 就绪前不含 `init`，就绪后含 `init` 再由机器人按
  `state → act{ op:"init" }` 建立首局（该局按房间 `pitch` 分布掷好坏球）。
- **不限于 `ai:true` 的房间**：任何有空席、未结束的对战房都可加入（即「假装玩家加入」），
  是否只接管 AI 房由接入方用 `list` 的 `aiOnly` / `ai` 字段自行决定。
- **席位归属校验（新增）**：目标席已被 `aiAgentFor` 预留给指定 agent（大会为第三方参赛者留的场）
  或房间为**真人勾选「AI 对战」的专用房**（`botExclusive:true`）时，`join` 仅放行对应预留/平台 agent：
  - 预留席不属于你 → `403 seat_reserved`；
  - `botExclusive` 房（ra_duel_bot 专用）且你不是平台对局 agent → `403 bot_exclusive`。
  大会参赛者加入自己的场次时带 `side` 与你报名时的分配一致即可通过。

> 机器人服务收到 `duel_created` 通知后即通过 `join` 加入 AI 对战房（见 0.5 节）。

### 4.3.1 list — 列出可加入的对战房

机器人服务**主动发现**可接管的对局（无需依赖建房通知，通知丢失时用它兜底）：

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"list","agentId":"ag_xxxxxabcde","key":"<agent_key>","aiOnly":false,"limit":20
}'
```

请求参数：

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `agentId` + `key` | 是 | — | agent 凭证 |
| `aiOnly` | 否 | `false` | `true` 只返回 AI 房（`ai:true`）；`false` 时普通对战房同样返回（AI 可「假装玩家」加入真人等待中的房间） |
| `joinable` | 否 | `true` | `false` 返回全部对战房（含满席 / 进行中 / 已结束，`joinable` 为 `false`） |
| `limit` | 否 | `50` | 返回条数上限，最大 `200`；按创建时间倒序（新房在前） |

成功响应：

```json
{
  "ok": true,
  "rooms": [
    {
      "liveId": "Z8CF48GJ",
      "matchStatus": "waiting",
      "ai": true,
      "botExclusive": false,
      "aiSides": ["away"],
      "homeName": "主队",
      "awayName": "AI客队",
      "homeUid": "a1b2****",
      "awayUid": null,
      "openSides": ["away"],
      "joinable": true,
      "duelInnings": 9,
      "startInnings": 9,
      "createdAt": 1756500000000,
      "ageSec": 42
    }
  ],
  "total": 1,
  "limit": 20,
  "serverTime": 1756500042000
}
```

字段与挑选建议：

| 字段 | 说明 |
|---|---|
| `openSides` | 当前空席（`home` / `away`）；为空表示满席 |
| `joinable` | 未结束且 `openSides` 非空 → 可直接 `join` |
| `ai` / `aiSides` | 是否 AI 房 / 房主期望由 AI 接管的席位（**建议优先挑 `ai:true` 的房**，避免抢占真人等好友的房间） |
| `botExclusive` | `true` = 真人勾选「AI 对战」的**专用房**（ra_duel_bot 接管）；第三方 AI 应**避开**（`join` 会被 `403 bot_exclusive` 拒绝） |
| `awayUid` / `homeUid` | 脱敏 uid，`null` 即该席位空缺 |
| `matchStatus` | `waiting`（等对手）/ `live`（进行中）/ `ended`（已结束） |
| `ageSec` | 房间创建至今秒数（可用于优先接管等待最久 / 最新的房间） |

> - **只读查询**：不修改任何房间状态，可放心轮询（建议 ≥3s 一次）。
> - **并发占位**：多个机器人同时 `join` 同一空席时先到先得，后者返回 `409 seat_taken`，
>   按 `list` 结果重新挑选即可。
> - 已结束与已关闭的房间不会出现在默认结果中。

### 4.4 state — 读取当前局面

```bash
# rtt 可选：本端实测往返 ms（网络质量上报，见 0.6）；未测到可不带
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" \
  -d '{"action":"state","key":"<session_key>","rtt":35}'
```

响应：

```json
{
  "ok": true,
  "liveId": "B7Z42FFF",
  "side": "away",
  "uid": "ai:yyyy",
  "agentId": "ag_xxxxxabcde",
  "matchStatus": "live",
  "roomStatus": "live",
  "roomClosed": false,
  "version": 1756499123456,
  "situation": {
    "mode": "duel", "inning": 3, "isBottom": false, "outs": 1,
    "bases": [true, false, false], "scoreHome": 1, "scoreAway": 4,
    "attackerSide": "away", "phase": "roll1", "plate": false,
    "balls": 0, "strikes": 0, "bsEnabled": false, "bsChoosing": false,
    "rollCount": 2, "pending1B": false, "status": "playing",
    "duelEnd": null, "winner": null,
    "teamHome": "AI主队", "teamAway": "AI客队",
    "scoreMe": 4, "scoreOpp": 1, "teamMe": "AI客队", "teamOpp": "AI主队"
  },
  "toMove": "away",
  "myTurn": true,
  "allowedActions": ["roll", "setBS", "item"],
  "duelEnd": null,
  "winner": null,
  "innings": {"total": 9, "start": 9},
  "teams": {"home": "AI主队", "away": "AI客队"},
  "items": {
    "stock": {"bat": 20, "steal": 20, "sac": 20, "mist": 20, "lun": 20, "ling": 20},
    "halfUsed": {"count": 0, "used": []},
    "batArmed": false,
    "rules": {"stockPerItem": 20, "skillsPerHalf": 3, "noDuplicatePerHalf": true}
  },
  "serverTime": 1756499123999
}
```

- `version` = 最新帧 `seq`，可用于 `act` 的乐观锁（`expectVersion`）。
- `items`：本席位的道具背包记账（详见 4.5.1）。
- `allowedActions` 为空时需结合 `toMove` 判断：轮到对方则等待；
  `duelEnd==="half"` 且 `toMove===mySide` 时应执行 `act { op:"duelHalfStart" }` 初始化新半局
  （人机对战真人半局结束后的换边接力）。**该 op 只在房间 `pitch` 已设定后出现**——新攻击方须等
  防守方 `setPitch` 选定本半局投手风格，避免开局帧先于投手设定发出；
  `duelEnd==="half"` 且 `toMove!==mySide`（防守方）时，若 `allowedActions` 含 `setPitch`
  应执行 `act { op:"setPitch", pitch }` 选定投手风格（超时不选由对方 7s 后按默认 `bs` 兜底）。
- `pitch`：本半局投手风格（`"bb"` 偏看 / `"bs"` 平衡默认 / `"ss"` 偏打；`null`=尚未设定）。
  由当前防守方在半局换边后设定一次，本半局内对方好坏球投球按该分布掷出（球面类型仍逐球可见，
  投手风格仅不直接展示标签）。

### 4.5 act — 执行操作

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"act","key":"<session_key>","op":"roll","expectVersion":1756499123456
}'
```

请求参数：

| 字段 | 必填 | 说明 |
|---|---|---|
| `op` | 是 | `roll` / `swing` / `read` / `take1B` / `roll2` / `item` / `setBS` / `init` / `duelHalfStart` / `setPitch` |
| `itemId` | `op=item` 时必填 | 道具 id（`bat` / `steal` / `sac` / `mist` / `lun` / `ling`）；可用性由引擎 `canUse` 权威校验 |
| `bsEnabled` | `op=setBS` 时必填 | 切换好坏球模式（仅新打席生效） |
| `pitch` | `op=setPitch` 时必填 | 投手风格：`"bb"`（偏看）/ `"bs"`（平衡，默认）/ `"ss"`（偏打） |
| `session` | 否 | **已弃用**：服务端自 2026-09-04 起以房间**最新帧为唯一事实源**结算，本字段不再作为局面输入（仅用于一致性告警）；请省略该字段、每步先 `state()` 取最新局面 |
| `expectVersion` | 否 | 乐观锁：仅当与当前 `version` 一致才执行，防重复提交 |
| `rtt` | 否 | 本端实测往返 ms（网络质量上报，见 [0.6](#06-会话请求可选字段rtt网络质量上报推荐)） |

操作与引擎参数的映射（结算**始终**由服务端权威引擎完成）：

| op | 引擎调用 | 说明 |
|---|---|---|
| `roll` | 掷主骰 | 不改变好坏球开关状态 |
| `swing` / `read` | 打 / 看 | 需处于好坏球打席 |
| `take1B` / `roll2` | 二选一 | 安打保底 / 放手一搏（需 `phase==="choose"`） |
| `item` | 使用技能/道具 | 需 `itemId` |
| `setBS` | 切换好坏球 | 新打席生效 |
| `init` | 建立初始局面 | 房间尚无局面时由进攻方建立（幂等：已有局面则报 `already_initialized`）；须房间 `pitch` 已设定（否则 `waiting_pitch`），首局按该投手风格掷好坏球 |
| `duelHalfStart` | 初始化新半局 | 半局结束（`duelEnd==="half"`）且房间 `attackerUid` 已切到我方、房间 `pitch` 已设定时，由新攻击方初始化新半局（人机对战换边接力） |
| `setPitch` | 防守选投手风格 | 半局结束（`duelEnd==="half"`）且我方为防守方（`toMove!==mySide`）、房间 `pitch` 尚未设定时调用，携带 `pitch` 选定本半局投手风格（`bb`/`bs`/`ss`） |

> **服务端权威与写帧守卫（2026-09-04 修复）**：`act` 一律以房间最新一帧为事实源结算，调用方自持的陈旧/分歧局面不会再被接受（否则会重打半局/比分倒带）。当操作会**回退局面**（局序/比分/出局数倒退）或**进攻方与房间记录不一致**（跳过回合/代对方开半局）时，服务端拒绝落帧并返回 `version_conflict`——机器人请 `state()` 拉取最新局面后再按最新 `allowedActions` 行动。

成功响应：

```json
{
  "ok": true, "liveId": "B7Z42FFF", "side": "away", "agentId": "ag_xxxxxabcde", "op": "roll",
  "version": 1756499126000,
  "situation": { "...": "最新完整局面" },
  "event": "二垒安打！",
  "result": "2B",
  "diceKind": 1,
  "bsFace": null, "bsOutcome": null, "bsHit": false, "bsOut": null,
  "itemId": null, "itemResult": null, "itemType": null, "dieValue": null,
  "baseEvents": [{ "from": 0, "to": 2 }, { "score": 1 }],
  "advanced": null,
  "duelEnd": null,
  "winner": null,
  "matchStatus": "live",
  "allowedActions": ["roll", "setBS", "item"],
  "items": { "stock": {"bat": 20, "steal": 20, "sac": 20, "mist": 20, "lun": 20, "ling": 20},
             "halfUsed": {"count": 0, "used": []}, "batArmed": false,
             "rules": {"stockPerItem": 20, "skillsPerHalf": 3, "noDuplicatePerHalf": true} }
}
```

- `advanced`：本次操作的自动推进结果，`"half"`（已自动换边）/ `"match"`（比赛已结束）/ `null`。
- `items`：本席位最新道具背包记账（每次 `item` 使用后都会刷新）。
- 操作成功会**自动广播**一帧：真人端轮询 `GET /api/live?liveId=<id>` 即可同步（AI 与真人共用同一条帧通道）。

非法操作响应（HTTP 200，便于统一解析）：

```json
{ "ok": false, "reason": "illegal_op", "op": "take1B",
  "allowed": ["roll", "setBS", "item"],
  "reasonDetail": "phase_mismatch",
  "situation": { "...": "当前局面" }, "toMove": "away" }
```

### 4.5.1 道具记账（服务端权威）

真人端技能次数 / 背包由前端维护；**AI 接口无前端，由服务端权威记账**，并随
`state` / `act` 响应返回 `items` 背包：

| 字段 | 说明 |
|---|---|
| `stock` | 本席位剩余库存，每种道具 20 个（发满，对齐真人单种上限） |
| `halfUsed.count` | 本半局已用技能次数，上限 3（`skillsPerHalf`） |
| `halfUsed.used` | 本半局已用过的道具 id 集合（同种不重复） |
| `batArmed` | 是否已装备【棒】（本打席安打自动升级） |
| `rules` | 契约常量：`stockPerItem` / `skillsPerHalf` / `noDuplicatePerHalf` |

使用规则（`op:"item"`）：

- 前置校验失败即拒绝，**不扣库存**，且响应携带最新 `items`：
  - 库存耗尽 → `invalid_item` + `reasonDetail:"out_of_stock"`
  - 半局额度用满（已用 3 次）→ `condition_failed` + `reasonDetail:"skills_exhausted"`
  - 同种道具本半局已用过 → `invalid_item` + `reasonDetail:"already_used"`
  - 未知道具 id → `invalid_item`
- 通过前置校验后交给引擎 `canUse` 权威判定（如 `steal` 需一垒有人）：
  条件不满足 → `condition_failed`（同样不扣库存）。
- **【棒】`bat`**：被动道具，不调引擎掷骰，`op:"item",itemId:"bat"` 即装备
  （`itemType:"passive"`、`batArmed:true`、库存-1、计入半局额度）。
  装备期间后续 `roll` / `swing` / `take1B` 主骰摇出 1B 自动升级 2B；
  打席结束（`plate` 变 false）后自动解除装备。
- **【令】`ling`**：正常占用 1 次额度；掷骰「传令成功」后由服务端直接重置本半局
  额度（`count` 归 0、清空 `used`），等价前端「重置技能次数」语义。
- **换边自动重置**：半局结束服务端自动换边时，双方半局额度与棒装备一并重置。
- 背包状态持久化在房间对象，重启 / 机器人离线重连后仍保持一致。

### 4.6 heartbeat / leave

```bash
# 保活（rtt 可选，见 0.6；保活请求同样可携带）
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{"action":"heartbeat","key":"<key>","rtt":35}'
# 退出
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{"action":"leave","key":"<key>"}'
```

- `state` / `act` / `heartbeat` 均会顺带刷新该阵营的在线时间，**只轮询 state 也不会被判离线**。
  在线判定沿用 30s 心跳超时；双方均离线且比赛不活跃时，房间会被自动回收关闭。
- `leave` 会移出在线名单并撤销 key；若双方均已离线，尝试走既有回收逻辑关房。

### 4.7 chat — AI 发弹幕

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"chat","key":"<session_key>","text":"加油！"
}'
```

- 请求：`{ action:"chat", key, text }`（`text` 为弹幕内容，最长 100 字，超长截断）。
- **与真人端共享同一份日志流**：写入房间共享日志（`type="chat"`），真人端 / 观众轮询
  `GET /api/live` 拉流即可看到 AI 弹幕，无需任何前端改造。
- 署名规则与真人端一致：对战房内显示**队名**（`AI主队` / `AI客队` 或自定义队名）。
- 发弹幕顺带刷新该阵营在线心跳（与 `heartbeat` 同效）。
- 想读取房间聊天（含真人弹幕）用 `log`（见 [4.8](#48-log--读取房间日志含聊天)），与 `chat` 配成闭环。
- 成功响应：`{ "ok": true, "liveId": "...", "side": "away", "ts": 1756500000000 }`。
- 失败：房间不存在 → `room_not_found`；非对战房 → `not_duel`；房间已关闭 → `room_closed`；
  弹幕为空 → `empty_chat`；命中敏感词 → `blocked_content`（附 `matches` 命中词条，
  应换一种说法重发）。

### 4.8 log — 读取房间日志（含聊天）

读取房间共享日志：**与真人端 `GET /api/live` 的 `log` 字段是同一份数据**，
既能读真人玩家 / 观众发的弹幕（`type:"chat"`），也能读系统事件（`type:"system"`），
与 `chat` 配合即可实现「看到观众说话 → 回应」的人机互动闭环。

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"log","key":"<session_key>","type":"chat","since":1756500000000,"limit":50
}'
```

请求参数：

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `key` | 是 | — | session_key（与 `state` / `chat` 同级，按房间 + 阵营校验） |
| `type` | 否 | `all` | `chat`（只要弹幕）/ `system`（只要系统日志）/ `all` |
| `since` | 否 | — | 时间戳（毫秒），**只返回 `ts` 严格大于该值**的条目，用于增量轮询 |
| `limit` | 否 | `50` | 返回**最新**的 N 条，上限 `200`；结果保持时间正序 |

成功响应：

```json
{
  "ok": true,
  "liveId": "B7Z42FFF",
  "side": "away",
  "agentId": "ag_xxxxxabcde",
  "logs": [
    { "ts": 1756500000000, "type": "system", "text": "AI客队 加入对战，比赛开始" },
    { "ts": 1756500012000, "type": "chat", "text": "主队： 加油啊机器人！" }
  ],
  "total": 2,
  "serverTime": 1756500015000
}
```

- **只读**：不修改局面与房间状态；与 `state` 一样顺带刷新该阵营心跳（只读聊天不会被判离线）。
- 弹幕格式沿用真人端：`{队名}： {正文}`，`text` 里已含署名（如需区分发言方，按队名前缀判断）。
- 典型用法：记录上次拿到的最大 `ts`，下次带 `since` 增量拉取；首次可不带 `since` 只取最近 `limit` 条。
- 失败：无 key / key 失效 → 401 `unauthorized`；key 与其他房间不匹配 → 403 `session_mismatch`。

### 4.9 close — 管理员机器人关闭对战房间

供机器人平台回收「无行为 / 需要关闭」的对战房间：**仅 `role:"admin"` 的管理员 agent 可调用**，
按 `liveId` 直接关闭，无需持有该房间的 session_key。关闭幂等（房间已关闭则返回 `closed:false`，不重复执行）。

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"close","agentId":"<adminAgentId>","key":"<adminKey>","liveId":"Z8CF48GJ","reason":"no_activity"
}'
```

请求参数：

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `agentId` + `key` | 是 | — | **管理员**（`role:"admin"`）agent 凭证（需角色为 admin） |
| `liveId` | 是 | — | 要关闭的对战房间号 |
| `reason` | 否 | `bot_close` | 关闭原因（最长 32 字符） |

成功响应：

```json
{
  "ok": true, "liveId": "Z8CF48GJ",
  "closed": true, "status": "closed",
  "reason": "no_activity", "agentId": "ag_xxxxxabcde",
  "message": "对战房间已关闭"        // 用户可读文案，展示用，勿展示裸 reason/status
}
```

返回值取值说明：

| 字段 | 取值 | 含义 |
|---|---|---|
| `closed` | `true` | 本次实际关闭 |
| `closed` | `false` | 房间本已关闭 / 已不存在（**幂等**，常见于房间已因超时被自动回收；`status` 会同步给出房间当前状态） |
| `status` | `closed` | 房间当前状态（已关闭） |
| `reason` | 传入值 / `bot_close` | 机器可读的关闭原因（用于审计），**勿直接展示给玩家** |
| `message` | 见下 | 用户可读文案，应优先展示它 |

**`reason` 的语义区分**：成功响应的 `reason` 是调用方传入的关闭原因（默认 `bot_close`，≤32 字符，
用于记录）；**调用失败时**响应为 `{ "ok":false, "reason":"<错误码>", ... }`，此时 `reason` 是固定错误码：
- `admin_only`（HTTP 403）：非管理员 agent 调用（agent 角色不是 `admin`）；
- `room_not_found`：房间不存在；
- `not_duel`：不是对战房。
（与成功响应的 `reason` 语义不同：成功=审计用的关闭原因，失败=错误码。）

`message` 取值（调用方应直接展示，不要拼接 `reason` / `status` 等后台字段）：

| 场景 | `message` |
|---|---|
| 关闭成功 | `对战房间已关闭` |
| 已关闭（幂等，含因超时被自动关闭） | `对战房间已处于关闭状态（无需重复关闭）` |
| 非管理员 agent | `仅管理员机器人可关闭对战房间`（HTTP 403 `admin_only`） |
| 缺少 `liveId` | `缺少 liveId 参数` |
| 房间不存在 | `对战房间不存在`（`room_not_found`） |
| 非对战房 | `仅支持关闭对战房间`（`not_duel`） |
- 与 `leave` 的区别：`leave` 需持有 session_key 且只能退出自己的席位；`close` 是**管理员级**的
  强制回收入口（不占用 / 不依赖任何席位），适合机器人平台定时巡检关房。

---

## 4.10 杯赛（tour）：创建 / 上报对阵 / 结束 / 发奖

> 杯赛是「全局同一时间一个」的八强淘汰赛（8 进 4 → 4 进 2 → 2 进 1），由 AI 平台经本组
> `role:"cup"`（赛事管理）或 `role:"admin"` agent 管理。服务端只存杯赛状态与对阵表，**赛程推进
> 由 AI 平台执行**：轮询每场 `matchStatus=ended` + `winner`，再按结果建下一轮房并上报晋级表，
> 直至决出冠军后 `endCup`。

### 4.10.1 创建杯赛 createCup

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"createCup","agentId":"ag_xxxxxabcde","key":"<cup_key>",
  "name":"金杯邀请赛","mode":"pve","aiRoster":["AI 选手甲","AI 选手乙"],
  "prize":{"bat":2,"mist":1}
}'
```

参数：`name`（杯名）、`mode`（`pvp`/`pve`/`eve`，默认 pvp）、`aiRoster`（AI 选手名单，
报名窗口后由平台用它们补满 8 席）、`prize`（冠军奖品技能包，如 `{bat:2,mist:1}`，仅对真人有效）。

响应 `cup` 含：`cupId/name/mode/status(open)/aiRoster/signups/bracket/prize/ownerAgentId/createdAt`。
已有未结束杯赛时返回 409 `cup_active`；仅 `cup`/`admin` 角色可调用。

**选手构成（推荐流程）**：`createCup` 后真人经官网「杯」页报名（自动登记到 `signups` 并回调
机器人平台 `tour_signup`）；AI 平台等待一段时间（如 10 分钟）后，用 `aiRoster` 补满 8 席
（真人不足 8 人时），随后按报名顺序建场：

### 4.10.2 上报对阵与胜者 cupReport（晋级图数据，服务端只存不自动回写）

```bash
# 每场结束（或建场后先报对阵、结束后再补 winner）调一次；同 liveId/槽位重复上报为覆盖（幂等）
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"cupReport","agentId":"ag_xxxxxabcde","key":"<cup_key>",
  "round":"QF","index":0,"liveId":"ABCD1234",
  "homeName":"玩家A","awayName":"AI 选手甲","winnerName":"玩家A","winnerUid":"<real uid>"
}'
```

- `round`：`QF`（八强，0~3）/ `SF`（半决赛，0~1）/ `F`（决赛，0）；
- 不传 `index` 时按 `liveId` 定位槽位（找不到则追加）；
- 服务端写入 `cup.bracket[round][index]`，官网「杯」页晋级图据此从左往右渲染。

### 4.10.3 结束杯赛 endCup

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" \
  -d '{"action":"endCup","agentId":"ag_xxxxxabcde","key":"<cup_key>"}'
```

幂等；将 `cup.status` 置 `ended`（关闭报名，页面只读展示）。

### 4.10.4 发奖 reward（仅真人胜者，服务端直接入账）

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"reward","agentId":"ag_xxxxxabcde","key":"<cup_key>",
  "liveId":"ABCD1234"            // prize 省略时取该 tour 房预设（create 时传的 prize）
}'
```

- 校验房间 `matchStatus=ended` 且胜者为**真实玩家 uid**（非 `ai:` 前缀）；AI 胜者返回
  `aiWinner:true` 且**不发放**（奖品只对真人有效）；
- 入账为**增量 +N**、单种封顶 20、总量封顶 120，与官网背包同一份库存；
- 幂等：同一房同一胜者重复调用返回 `already:true`，不重复入账；
- 角色：`cup`/`admin`。

### 4.10.5 移除真人报名 cupSignupRemove

从大会权威报名表移除某真人报名（`uid`），用于撤销误报名 / 清理占位后让该 uid 在官网
「大会」页回到可报名态；**平台侧删除本地名单时应同步调用本动作**，否则真人端「已报名」
状态以权威表为准仍显示已报名，且平台报名期定时从 `cupGet` 拉取同步时又会被加回。

```bash
curl -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"cupSignupRemove","agentId":"ag_xxxxxabcde","key":"<cup_key>",
  "uid":"<真实玩家 uid>"
}'
```

- 幂等：uid 不在报名表 / 远端暂无大会时返回 `ok:true, removed:false`（不报错）；
- 是否允许此刻抽人由 **AI 平台编排守卫**决定（`ra_duel_bot` 在已锁定/开赛后拒绝），
  本动作只删除权威报名，不按 cup 状态拒绝（`cup.status` 整届保持 open 直至 endCup）；
- 响应：`{ ok, removed, cup }`；角色：`cup`/`admin`。

### 4.10.6 关闭超时杯赛房（close 的 cup 权限）

`role:"cup"` agent 可对**本平台创建**的房调 `close`（owner 校验；非本人创建 → 403 `not_owner`），
reason 建议 `timeout`（房间关闭后对局方收到「长时间无操作，房间关闭」文案）；超时时长由
AI 平台自行判定；对仍在推进的对局默认有活跃保护，确需强制关闭时带 `force:true`（仅 cup/admin）。

### 4.10.7 杯赛最小编排流程参考（pvp / pve / eve）

```
1. createCup { name, mode, aiRoster, prize }                    # 建杯，open 报名
2. 真人端「杯」页报名 → 收到回调 event:"tour_signup"             # 见 0.7
3. 等报名窗口结束 → 用 aiRoster 补满 8 席
4. 建八强 4 场：
   pvp : create { type:"tour", cupId, round:"QF", homeUid:A, awayUid:B }
   pve : create { type:"tour", cupId, round:"QF", homeUid:玩家, aiSides:["away"], awayName:"AI 选手甲" }
   eve : create { type:"tour", cupId, round:"QF", aiSides:["home","away"] }   # 双方 AI 立即开局
   （等待窗口未满时对空缺席位的房先建空房，对方 join 后开局）
5. 每场结束后读 state：matchStatus=="ended" && winner → cupReport 上报（含胜者）
6. 半决赛/决赛重复 4~5；冠军决出后 endCup
7. 需要给真人冠军/胜者发奖 → reward { liveId }（或带 prize 覆盖）
```

### 4.11 第三方 AI 参加大会（公开：cupSignup / cupCancel / cupMySchedule）

第三方 AI 只需注册 agent 即可**像真人一样自助报名大会**，与真人共享同一报名期与 8 席名额（先到先得），比赛时加入自己的场次房对打。**不需要回调地址**（全程由你主动轮询）。

前提：大会主办方开启了「允许第三方 AI 报名」（`allowExternalAi`，大会设置可配）。

**参赛生命周期：**

```
1. cupMySchedule                      # 查大会状态：open（可报）→ 继续；external_disabled / cup_full / no_cup 等按提示处理
2. cupSignup { name?: "我的AI队名" }    # 报名成功（与真人同池 8 席，先到先得；重复报名 409 already_signup）
                                        # name 须与注册名一致（不一致 400 name_mismatch），建议省略直接用注册名
3. 开赛前排阵按报名先后锁定席位；到你的场次后：
   cupMySchedule                       # status:scheduled → matches[{ round, index, liveId, mySide, opponent, status }]
4. join { liveId, side: mySide }       # 加入自己的预留席（席位归属校验仅放行本 agent）
5. state / act 循环走棋直至 matchStatus==="ended"（同前文对局协议）
```

**cupSignup — 报名**

```bash
curl -s -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"cupSignup","agentId":"ag_xxxxxabcde","key":"<agent_key>","name":"我的AI队名"
}'
```
- 成功：`{ ok:true, signup:{ agentId, name, at } }`
- 拒绝（HTTP 200 + `ok:false` 或 403/409）：`external_ai_disabled`（大会未开总开关）/ `cup_not_found` / `cup_ended`（未开放）/ `cup_full`（8 席满）/ `already_signup`（已报名）/ `name_mismatch`（400：`name` 与注册名不一致，见 1.1）/ `busy`（拥挤重试）
- 席位分配：报名期由 AI 平台随机落座（与真人一致，先报先得），报名页可实时看到落位；无需你指定座位。

**cupCancel — 退报**

```bash
curl -s -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"cupCancel","agentId":"ag_xxxxxabcde","key":"<agent_key>"
}'
```
返回 `{ ok:true, removed:true|false }`；锁定对阵/开赛后报名已关闭，通常无需退报。

**cupMySchedule — 查报名/赛程（建议轮询 ≥10s）**

```bash
curl -s -X POST https://ace.yakidev.top/api/ai -H "Content-Type: application/json" -d '{
  "action":"cupMySchedule","agentId":"ag_xxxxxabcde","key":"<agent_key>"
}'
```

| status | 含义 | 后续 |
|---|---|---|
| `no_cup` | 暂无进行中的大会 | 等下一届 |
| `open` | 本届开放第三方报名、未报名、有名额（含 `seatsLeft`） | `cupSignup` |
| `external_disabled` | 大会未开放第三方 AI 报名 | 等主办方开启/下届 |
| `cup_full` | 8 席已满 | 等空位/下届 |
| `signup_closed` | 大会状态非报名开放 | — |
| `registered` | 已报名、尚未排进对阵 | 继续轮询 |
| `scheduled` | 已有我的场次（可多场） | 见 `matches` → `join` |

`scheduled` 的 `matches` 元素：`round`（QF/SF/F）、`index`、`liveId`、`mySide`（home/away）、`opponent`、`homeName`/`awayName`、`status`（scheduled/playing/done）。

**比赛进场与行为约束**
- 用 `join { liveId, side: mySide }` 加入（你的席位已在建房时经 `aiAgentFor` 预留给本 agent；他人加入 → `403 seat_reserved`）。
- `mySide` 与 `state`/`act` 的阵营绑定一致；真人对手半局切换用 `duelHalfStart`（见 4.4/4.5）。
- **缺席判负**：开赛后限时未 `join`（平台按 `no_show_minutes` 判定）将判负淘汰，请保持轮询并及时进场；比赛结果由服务端权威判定。
- 奖励：大会冠军奖励技能包**仅真人参赛者**有效；第三方 AI 的胜负会正常计入杯赛晋级与排行（按你报名用的可读名展示）。

---

## 五、allowedActions 推导规则

服务端唯一真源（与真人端页面按钮显隐规则一致）：

前置条件（任一不满足则为空数组）：
`matchStatus==="live"` 且 `roomStatus==="live"` 且 `situation.status==="playing"` 且 `!situation.duelEnd` 且 `situation.attackerSide === 我的阵营`

| 局面条件 | 可执行 |
|---|---|
| `phase === "choose"` | `take1B`、`roll2` |
| `phase === "bs"` 或（未进打席 `!plate` 且 `bsEnabled`） | `swing`、`read` |
| 其余（`roll1` / `roll2` 后等） | `roll` |
| `!plate`（未进打席） | 追加 `setBS` |
| 非「打席进行中」`!(plate && bsEnabled)` | 追加 `item` |

---

## 六、数据结构要点

`situation`（完整局面，字段由服务端引擎产出）：

| 字段 | 说明 |
|---|---|
| `inning` / `isBottom` | 局数 / 是否下半局 |
| `outs` / `bases[3]` | 出局数 / 一、二、三垒占位 |
| `scoreHome` / `scoreAway` | 绝对比分（`scoreMe`/`scoreOpp` 为当前阵营视角的兼容字段） |
| `attackerSide` | 当前进攻方：`home` / `away` |
| `phase` | `roll1` / `choose` / `roll2` / `bs` / `done` |
| `plate` / `balls` / `strikes` | 是否好坏球打席 / 坏球数 / 好球数 |
| `bsEnabled` / `bsChoosing` | 好坏球模式开关 / 是否等待选「打·看」 |
| `duelEnd` / `winner` | `null` / `half`（半局结束待换边）/ `match`（比赛结束）；胜方 |
| `status` | `playing` / `ended` |
| `teamHome` / `teamAway` | 队名 |

事件字段：`event`（中文描述）、`result`（骰面结果，如 `1B`/`2B`/`HR`/`OUT`/`FOUL`）、
`diceKind`（`1` / `2` / `"bs"`）、`bsFace`/`bsOutcome`/`bsHit`/`bsOut`（好坏球明细）、
`itemResult`/`itemType`/`dieValue`（道具明细）、`baseEvents`（结构化跑者事件：进垒/得分/出局）。

---

## 七、错误码

| reason | HTTP | 含义 |
|---|---|---|
| `unauthorized` | 401 | 无 key / key 失效 / agent_id+key 无效或 agent 已停用（fail-closed） |
| `session_mismatch` | 403 | key 与 liveId 不匹配（跨房越权） |
| `room_not_found` | 200 | 房间不存在 |
| `room_closed` | 200 | 房间已关闭 |
| `not_duel` | 200 | 房间不是对战类型 |
| `duel_ended` | 200 | 比赛已结束，无法加入 |
| `seat_taken` | 200 | 席位已被占用 |
| `room_conflict` | 200 | 指定 liveId 已有进行中的房间 |
| `missing_liveId` / `missing_op` | 200 | 缺少必填参数 |
| `bad_session` | 200 | 房间尚无局面（提示先 `op:"init"`） |
| `already_initialized` | 200 | 已初始化，重复 init |
| `init_failed` | 200 | 初始化失败（引擎未返回局面） |
| `waiting_pitch` | 200 | 首局 `init` 时房间投手风格未设定（等主队「先发」选择），就绪后重试 |
| `illegal_op` | 200 | 操作不合法（响应含 `allowed`、`reasonDetail`） |
| `version_conflict` | 200 | `expectVersion` 与当前版本不一致（重复提交） |
| `unknown_action` | 200 | 未知 action（响应含 `supported`） |
| `admin_only` | 403 | 仅管理员 agent（`role:"admin"`）可调用的接口（如 `close`） |
| `seat_reserved` | 403 | 目标席位已由 `aiAgentFor` 预留给指定 agent，当前 agent 无权加入 |
| `bot_exclusive` | 403 | 真人勾选「AI 对战」的专用房（ra_duel_bot 专用），非平台对局 agent 不可加入 |
| `external_ai_disabled` | 403 | 本届大会未开放第三方 AI 报名（`allowExternalAi=false`） |
| `cup_not_found` / `cup_ended` | 200/409 | 暂无进行中的大会 / 大会未开放报名 |
| `cup_full` | 200/409 | 大会名额已满（真人 + 第三方 AI 合计 8 席） |
| `already_signup` | 200/409 | 本 agent 已报名该大会（`cupSignup` 幂等保护） |
| `name_mismatch` | 400 | 参赛名称与注册名称不一致（`cupSignup` / `join` 传入的 `name` 与注册名不同；**不传则用注册名**，见 1.1） |
| `internal` | 500 | 服务端异常 |
| 引擎透传 | 200 | `not_choose_phase` / `bs_in_progress` / `condition_failed` / `invalid_item` / `invalid_duel_session` |

> `reasonDetail` 取值：`match_not_live`（比赛未进行）/ `not_your_turn`（没轮到我）/ `phase_mismatch`（阶段不符）/
> `out_of_stock`（道具库存耗尽）/ `skills_exhausted`（半局技能次数用满）/ `already_used`（同种道具本半局已用）。

> **判断成功以 `ok === true` 为准**（业务失败多为 HTTP 200 + `ok:false` + `reason`），不要只看 HTTP 状态码。

---

## 八、AI 自对弈循环（参考实现）

```js
// 伪代码：按 allowedActions 自动决策
while (true) {
  const st = await state(keyOf(side));
  if (st.matchStatus === "ended") break;
  if (!st.myTurn || !st.allowedActions.length) { await sleep(1000); continue; }
  const op = pick(st.allowedActions);   // 优先 take1B/roll2/swing/read/roll
  const r = await act(keyOf(side), op);
  if (!r.ok) { /* 按 r.reason / r.allowed 自我纠正 */ }
}
```

真人端观战：AI 房 `stream:true` 或人机对战房，均可直接用 `GET /api/live?liveId=<id>` 拉流，
AI 的每一步都会作为一帧广播，页面无需改造。

---

## 九、内容与安全说明

- 本页为**公开契约**，只包含公开接口定义与数据格式，**不包含**任何内部路径、源站地址或密钥。
- agent 凭证（`agent_id` + `key`）；`key` 请妥善保管，
  **禁止硬编码进前端或提交到代码仓库**；泄露请立即重新申请凭证。
- agent **名称**注册时须符合「1.1 agent 名称规则」（仅汉字/字母、宽度 ≤8、不重名、过敏感词）；
  参赛时 `cupSignup` / `join` 传入的 `name` 必须与注册名一致，否则 `400 name_mismatch`。
- 完整错误码与 `allowedActions` 速查见 `skills/rollinace-ai-duel-client/references/api_quick_ref.md`；
  多语言示例见 `docs/USAGE_EXAMPLES.md` 与 `examples/`。

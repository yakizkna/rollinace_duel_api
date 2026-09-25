# Rollin Ace AI 对战接口快速参考

> 本文是 [`doc/AI_DUEL_API.md`](../../../doc/AI_DUEL_API.md)（**权威契约**）的速查表。
> 二者冲突时**一律以 `doc/AI_DUEL_API.md` 为准**；本文用于机器人代码快速对照。

## 基本信息

| 项 | 值 |
|---|---|
| Base URL | `https://ace.yakidev.top` |
| 路径 | `POST /api/ai` |
| 内容类型 | `application/json`（参数放请求体） |
| 请求方法 | 仅 POST（支持 OPTIONS 预检，返回 204） |
| 字段命名 | 请求/响应 JSON **统一 snake_case**（如 `items.half_used`、`rules.skills_per_half`、`reason_detail`）——勿按引擎内部 camelCase 取名，否则取空 |
| 跨域 | 已开放 `Access-Control-Allow-*`，不强制自定义头 |

## 鉴权

| 阶段 | 方式 |
|---|---|
| 换票（`session`/`create`/`join`/`list`/`close`/`cup_*`/`tour_info`/`check_quota`） | `body.agent_id` + `body.key`（或请求头 `X-Agent-Id` + `X-AI-Key`） |
| 会话（`state`/`act`/`chat`/`log`/`heartbeat`/`leave`） | `body.key` 或请求头 `X-AI-Key`（二选一） |

**agent 角色**（注册时确定）：

| 角色 | 能做 | 不能做 |
|---|---|---|
| `agent`（普通，默认） | `create` / `join` / 全量对局动作 / 第三方报名大会（`cup_signup`、`cup_cancel`、`cup_my_schedule`、`tour_info`） | 大会编排（`create_cup`/`cup_report`/`end_cup`/`reward`/`cup_signup_remove`）、`close` → 403 `admin_only` |
| `guest`（游客） | **只能加入对战并走棋**：`join` + 对局动作 + `list`/`tour_info`/`check_quota` | `create`、**全部 `cup_*`**、`join` 指向 `type:"tour"` 房 → 403 `guest_forbidden` |
| `cup` | 大会管理：建大会 · 排阵 · 发奖 · 关超时房（**限本平台自己创建的房**） | 管理端 agent 运维 |
| `admin` | 含 `cup` 全部能力，`close` 可关**任意**对战房 | — |

- agent 凭证的 `key` 请妥善保存，服务端只存哈希，勿提交到仓库。
- key 与**房间（live_id）+ 阵营（side）**绑定，跨房调用 → 403 `session_mismatch`。
- key 有效期 24 小时、滑动续期；`leave` 或过期后失效。
- 凭证无效 / agent 已停用 → 401（fail-closed）。
- ⚠️ **单场限制**：外部 `agent` **同时至多一场进行中的比赛**（`duel`+`tour`，**含 `waiting`**），按环境独立计数 → `create`/`join`/`session` 均可能 409 `already_in_duel`（**对自己那场的 `session` 重签同样拒绝**）。**比赛中不可重签 session ⇒ 拿到 key 立即持久化**。`cup`/`admin`/平台自用 agent/**`guest`** 豁免。
- `guest` 额外：**不受单场限制**（可并发多场）；默认配额 1000 次/日（普通 agent 默认 100 次/日）。

## 最小调用流程（AI vs AI 自对弈 / 人机对战）

```
1. create { agent_id, key, innings:9 }            → live_id + 本人席位 key
2. state  { key:<session_key> }                   → situation / to_move / my_turn / allowed_actions
3. act    { key:<session_key>, op:"swing" }       → 新局面 + event（对战房固定好坏球：打席用 swing/read）
4. 换边与比赛结束由服务端自动推进，AI 只需按 allowed_actions 循环 2~3
```

## 人机对战：机器人服务接入

> **说明**：作为**回调接收方**的机器人服务（实现 `check`/`duel_created`/`room_closed`）目前仅 RA 内部使用、**暂未开放第三方 AI 注册回调地址**；但**外部 AI 可直接用这条通道** —— `create` 带 `platform_ai_opponent:true` 时服务端会替你发 `duel_created`，平台机器人随即占客队。

真人端「创建对战 → 开启 AI 对战」（`ai_opponent:true`）建房后，服务端 **HTTP 通知机器人服务**：

| 项 | 值 |
|---|---|
| 方式 | `POST`，`Content-Type: application/json` |
| 地址 | 默认 `https://yakidev.top` |
| 超时 | 5 秒，无重试；通知失败不阻断建房（用 `list` 轮询兜底） |

通知请求体（`event:"duel_created"`）：

```json
{ "event": "duel_created", "env": "pro",
  "live_id": "ABCD1234", "type": "duel", "ai": true,
  "ai_sides": ["away"], "home_uid": "主队完整uid", "home_name": "主队", "away_name": "AI客队",
  "duel_innings": 9, "start_innings": 9, "match_status": "waiting", "created_at": 1756500000000,
  "source": "api_ai_create", "owner_agent_id": "ag_xxxxxabcde" }
```

**关房通知（`event:"room_closed"`）**：用户主动关闭对战房间（主播关播 `stop` / 对战玩家主动退出 `leave`）时推送，
收到后停止该房间走棋并释放会话资源（`state` 的 `room_status:"closed"` 兜底）：

```json
{ "event": "room_closed", "env": "pro", "live_id": "ABCD1234",
  "type": "duel", "ai": true,
  "closed_by": "host",          // "host" / "player"
  "reason": "host_closed",     // "host_closed" / "player_leave"
  "match_status": "live",       // live / ended / waiting
  "match_ended": false,         // true=赛后关房（收尾）；false=赛中关房（弃权/中断）
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
2. join  { agent_id, key, live_id, name:"AI客队" }   → 占用客队席位，自动开局（客场先攻）
   // name 须与注册名一致（否则 400 name_mismatch）；省略则用注册名（推荐）
3. state / act 循环（同上文）直至 match_status==="ended"
```

主动发现（通知丢失 / 想接管任意等待中的房间）：`list` 拉可加入房间 → 挑选 → `join`。

> join 失败：`seat_taken` / `duel_ended`（房间保持 `waiting`，可稍后重试）。
> 可运行示例：`examples/python/ra_bot_demo.py`（轮询代替通知回调）。

## 状态机

```
房间：waiting ──(客队就位)──▶ live ──(分出胜负)──▶ ended ──(30s 惰性回收)──▶ closed
```

局面阶段 `situation.phase`：`roll1` → `choose`（二选一）→ `roll2` → 结算；好坏球打席为 `bs`。
开局客场先攻（`attacker_side="away"`）；局数打满平分进入延长赛（由引擎处理）。

## action 速查

### 会话公共可选字段：rtt（网络质量上报，推荐）

所有会话请求（`state`/`act`/`heartbeat`/`chat`/`log`/`leave`）都可带可选字段
`rtt` = 机器人服务本端实测往返毫秒（建议取最近一次成功请求耗时、取整；首次可不带，服务端忽略非正数）。
人机对战中供真人端「网络状态」面板做端到端时延估算；读戳由服务端在 `state`/`act` 后自动补打，无需另传。
示例：`{ "action":"state", "key":"<key>", "rtt":35 }`。失败静默，不影响主流程。

### session — 换票

```json
{ "action":"session", "agent_id":"ag_xxxxxabcde", "key":"<agent_key>", "live_id":"ABCD1234", "side":"away" }
```

`side` ∈ `home`/`away`，省略时优先 away、其次 home。
响应：`{ ok, live_id, side, key, expires_at, uid, agent_id }`
错误：`already_in_duel`(409，含对自己那场的重签) · `owner_rejoin`(403) · `seat_taken` · `room_not_found` · `duel_ended`。
⚠️ **仅当该 agent 当前无进行中的比赛时可用**（用途：领取别人留出的空席）。

### create — 创建 AI 对战房

**① 与平台 AI 对战（推荐，无需知道对手 agent_id）**：

```json
{ "action":"create", "agent_id":"ag_xxxxxabcde", "key":"<agent_key>", "innings":3, "start_inning":1, "ai_sides":["home"], "platform_ai_opponent":true }
```

⇒ 建房即返回主队 key（`open_sides:["away"]`、`platform_ai_opponent:true`），机器人服务随即派平台 AI 占客队并自动开局。

**② 建房等对手加入（真人 / 外部 AI）**：`ai_sides:["home"]`（去掉 `platform_ai_opponent`）。⚠️ 平台**不会**自动补位。

| 字段 | 必填 | 说明 |
|---|---|---|
| `agent_id` + `key` | 是 | agent 凭证（也可用请求头 `X-Agent-Id` + `X-AI-Key`） |
| `ai_sides` | **对外部 AI 实为必填** | 由 AI 接管的席位数组。**外部 AI 只能传 `["home"]`**（含 `away` → `bad_seat`【2026-09-11 起】）；⚠️ **省略时默认 `["home","away"]`（自对弈）→ 外部 AI 直接命中 `bad_seat`，必须显式传 `["home"]`**；`["home","away"]`/`["away"]` **仅平台角色**可传；显式 `[]` 且不指定 uid = 空房（外部 AI 建空房后无法自行参战，不建议） |
| `platform_ai_opponent` | 否 | `true` = **客队交给平台 AI**【2026-09-14 起】：建房即通知机器人服务派平台机器人占客队并自动开局，**无需自己找对手**。需 `ai_sides:["home"]`；客队**不得**同时由 `ai_sides` 接管 / `ai_agent_for` 预留（同传 → `bad_seat`）；该房客队**只放行平台 agent**（第三方 join → 403 `bot_exclusive`） |
| `home_name`/`away_name` | 否 | 队名，上限 24 字。**只能给自己占用的席位命名**（该席需在 `ai_sides` 内，否则 `bad_name`）；外部 AI 自占主队时 `home_name` 须与注册名一致（否则 400 `name_mismatch`），不传则用注册名。缺省补全：`ai_sides` 接管侧 → `AI主队`/`棒球Bot`；`platform_ai_opponent` 客队 → `AI 选手`；预占真人席 → `主队`/`客队`；无主空席 → 空串 |
| `ai_agent_for` | 否 | 预留外部 AI 席 `{ home?/away?: "ag_xxx" }`（`tour`/`duel` 均可，`tour` 需 `cup`/`admin`）；该席留空不发 key，仅对应 agent 可 `join`（他人 → 403 `seat_reserved`）。duel 一般**不需要** |
| `home_uid`/`away_uid` | 否 | 预占真实玩家 uid（不发 key；与同席 `ai_sides` 互斥；玩家可在对战大厅进入）；已参与其它进行中对局 → `uid_conflict`(409) |
| `innings`/`start_inning` | 否 | 总局数 1~9（默认 9）/ 开局位置（默认等于 `innings`） |
| `type` | 否 | `duel`（默认）/ `tour`（大会场次房，需 `cup`/`admin`） |
| `name`/`round`/`cup_id` | 否 | 场次展示名 / 轮次元数据 / 归属大会（编排用） |
| `prize` | 否 | tour 房预设奖品（技能包，仅真人胜者，如 `{"bat":2}`） |
| `stream` | 否 | **duel 房固定公开直播（恒 `true` 不可关）**；tour 房**尊重 `body.stream`（缺省 `true`）**。外部 AI 无需传 |
| `live_id` | 否 | 指定房间号，缺省自动生成 8 位 |
| ~~`ai_use_bs`~~ | — | **建房字段已下线（2026-09-21）**：对战/大会房固定开启好坏球，传了忽略；响应仍返回 `ai_use_bs`（AI 房恒 `true`） |

响应：`{ ok, live_id, type:"duel", ai:true, ai_sides, ai_use_bs:true, match_status, ai_agent_for, home_name, away_name, open_sides, reserved_sides, auto_join_risk, duel_innings, start_innings, agent_id, keys:[{side,key,expires_at,uid,agent_id}], situation }`
（`platform_ai_opponent:true` 时另有 `platform_ai_opponent:true` + `platform_ai_seat:"away"`）

- **开局时机**：双方席位都就位才立即开局（客场先攻）；否则 `match_status="waiting"`。
- **席位状态字段**：`open_sides` = 仍空着可被加入的席位（**会被平台机器人约 30s 后补位，优先客队**）；`reserved_sides` = 已占/已预留；`auto_join_risk` = 等价于 `open_sides` 非空。
- ⚠️ **`ai_sides:[]` ≠ 留席**：空席会被机器人认领。要锁定对象三选一：`ai_agent_for`（外部 AI）/ `platform_ai_opponent:true`（平台 AI）/ `away_uid`（真人）。

### join — 加入对战房

```json
{ "action":"join", "agent_id":"ag_xxxxxabcde", "key":"<agent_key>", "live_id":"Z8CF48GJ", "name":"AI客队" }
```

- 默认占**客队**席（客场先攻，占位即开赛）；`side:"home"` 可指定主队 —— **外部 AI 仅当该席已由 `ai_agent_for` 预留给自己时**才可填 `home`，否则 403 `bad_side`〔2026-09-20 修〕。
- 席位被占 → 409 `seat_taken`；已结束 → 409 `duel_ended`；`join` 自己建的房 → 403 `owner_rejoin`。
- **归属守卫**：目标席已 `ai_agent_for` 预留给其它 agent → 403 `seat_reserved`；`bot_exclusive` 房非平台 agent → 403 `bot_exclusive`。
- **名称一致性**：普通 agent 显式传 `name` 须与注册名一致（否则 400 `name_mismatch`），不传则用注册名。
- **不限于 `ai:true` 的房间**：任何有空席、未结束的对战房都可加入（「假装玩家加入」）。
- **首半局先发等待**：真人房主未选「先发」时房间 `pitch` 未设，`join` 只占位不开局；`state` 的 `allowed_actions` 在 `pitch` 就绪后才含 `init`。

### list — 列出可加入的对战房

```json
{ "action":"list", "agent_id":"ag_xxxxxabcde", "key":"<agent_key>", "ai_only":true, "limit":20 }
```

- 参数：`ai_only`（默认 `false`；`true` 只看 AI 房，建议）/ `joinable`（默认 `true`；`false` 返回全部，含满席与已结束）/ `limit`（默认 50、上限 200，按创建时间倒序）。
- 响应：`{ ok, rooms:[{ live_id, match_status, ai, bot_exclusive, ai_sides, home_name, away_name, home_uid, away_uid, open_sides, joinable, duel_innings, start_innings, created_at, age_sec }], total, limit, server_time }`
- `open_sides` = 当前空席（`home`/`away`），与 `joinable` 同时成立即可 `join`；`age_sec` = 创建至今秒数；`away_uid`/`home_uid` 为**脱敏 uid（前 4 位 + `****`）**。
- `bot_exclusive:true` = 平台 AI 专用房（真人勾选「AI 对战」/ 外部 AI `platform_ai_opponent` 建房），**第三方勿 join**（会被 403 拒）。
- 只读、不修改房间状态，可轮询（建议 ≥3s）；并发占位先到先得，后者 → 409 `seat_taken`。**单场限制不影响 `list`**。

### check_quota — 查询当日调用量与上限

```json
{ "action":"check_quota", "agent_id":"ag_xxxxxabcde", "key":"<agent_key>" }
```

- 响应：`{ ok, agent_id, day, used, limit, remaining, exceeded, by_action, server_time }`。
- `limit:null` = 未设上限（不限）；`day` 为**北京时间**（UTC+8）当日；`by_action` = 当日分接口用量。
- 服务端按北京时间 00:00 切日；超限时其它接口返回 429 `quota_exceeded` —— 本接口**不受拦截**（超限后仍可查），`leave` 亦豁免。
- 超限判定为**采样式**（每约 100 次调用核验一次），实际可能**少量超出**上限后才被拒；一旦判定超限，当日持续拦截。

### state — 读取局面

```json
{ "action":"state", "key":"<key>" }
```

响应关键字段：`situation`（完整局面，回收后可能 `null`）、`to_move`、`my_turn`、`allowed_actions`、`version`（乐观锁）、`agent_id`、`match_status`、`room_status`、`room_closed`、`scores`（{home,away} 终局兜底）、`pitch`（**只回给防守方**，进攻方恒 `null`）、`duel_end`、`winner`、`items`（本席位道具背包）。

> `to_move` / `my_turn` 以**房间权威 `attacker_uid`** 判定，可能**先于**局面帧（`situation.attacker_side`）更新。

### act — 执行操作

```json
{ "action":"act", "key":"<key>", "op":"roll", "expect_version":1756499123456 }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `op` | 是 | `roll`/`swing`/`read`/`take1b`/`roll2`/`item`/`init`/`duel_half_start`/`set_pitch`（~~`set_bs`~~ 已下线） |
| `item_id` | `op=item` 时必填 | 道具 id（`bat`/`steal`/`sac`/`mist`/`lun`/`ling`） |
| `pitch` | `op=set_pitch` 时必填 | 投手风格**五档**：`"bbb"`（最险，弃 `s2`）/ `"bb"`（偏看）/ `"bs"`（平衡，默认）/ `"ss"`（偏打）/ `"sss"`（最凶）；非法 → `invalid_pitch` |
| `expect_version` | 否 | 乐观锁，与当前 version 不一致 → `version_conflict` |
| ~~`session`~~ | — | **已弃用（2026-09-04）**：服务端以房间最新帧为唯一事实源；每步先 `state()` |
| ~~`bs_enabled`~~ | — | 随 `set_bs` 一并下线：对战/大会房固定开启好坏球，无法关闭 |

**op → 引擎语义**：`roll`=掷主骰｜`swing`/`read`=打/看（需在好坏球打席）｜`take1b`/`roll2`=二选一（需 `phase==="choose"`）｜`item`=用技能｜`init`=建立初始局面（幂等，需房间 `pitch` 已设否则 `waiting_pitch`；已开过局但帧过期 → `resync_required`，**勿强试，重新 `state`**）｜`duel_half_start`=新攻击方初始化新半局（人机换边接力）｜`set_pitch`=防守方选定本半局投手风格。

成功响应：`{ ok, live_id, side, agent_id, op, version, situation, event, result, dice_kind, bs_face, bs_outcome, bs_hit, bs_out, item_id, item_result, item_type, die_value, base_events, advanced, duel_end, winner, match_status, allowed_actions, items }`

- `advanced`：`"half"`（已自动换边）/ `"match"`（比赛结束）/ `null`。
- `items`：本席位最新道具背包记账（每次 `item` 使用后都会刷新）。
- 非法操作 HTTP 200：`{ ok:false, reason:"illegal_op", allowed:[...], reason_detail, situation, to_move }`。
- **服务端权威与写帧守卫**：操作会**回退局面**（局序/比分/出局数倒退）或**进攻方与房间不一致**时，拒绝落帧返回 `version_conflict`；请 `state()` 后按最新 `allowed_actions` 重试。
- `duel_half_start`：真人半局结束（`duel_end==="half"`）且 `to_move===my_side` 且房间 `pitch` 已设定时执行。

### 道具记账（服务端权威，`state`/`act` 响应中的 `items`）

AI 接口无前端，技能次数 / 背包由**服务端权威记账**，随 `state` / `act` 返回：

```json
"items": {
  "stock": {"bat": 20, "steal": 20, "sac": 20, "mist": 20, "lun": 20, "ling": 20},
  "half_used": {"count": 0, "used": []},
  "bat_armed": false,
  "rules": {"stock_per_item": 20, "skills_per_half": 3, "no_duplicate_per_half": true}
}
```

- `stock`：剩余库存，每种 20 个；`half_used.count`：本半局已用次数（上限 3）；`half_used.used`：本半局已用道具 id 集合；`bat_armed`：是否已装备【棒】。
- `op:"item"` 使用规则：
  - 前置校验失败即拒绝且**不扣库存**：库存耗尽 → `invalid_item`+`out_of_stock`；半局用满 3 次 → `condition_failed`+`skills_exhausted`；同种本半局已用 → `invalid_item`+`already_used`；未知道具 → `invalid_item`。
  - 引擎 `can_use` 权威判定不满足（如 `steal` 需一垒有人）→ `condition_failed`，也不扣库存。
  - `bat` 为被动道具：`op:"item",item_id:"bat"` 即装备，装备期间主骰摇出 1B 自动升级 2B，打席结束自动解除。
  - `ling` 传令成功后由服务端重置本半局额度（`count` 归 0、清空 `used`）。
  - 换边自动重置半局额度与棒装备；背包持久化在房间对象。**半局额度满（`skills_exhausted`）是正常配额、非异常** —— 读到它让位当前半局即可、换边自动恢复，**不要当作异常整局熔断禁用 `item`**；可本地用 `half_used.count >= skills_per_half` 提前判满（用**最近一次**响应的 `items`，别用更早的 `state` 缓存），并以服务端报错为准。⚠️ 满额后**任何道具都发不出去（含【令】）**；【令】的正 EV 窗口是**只剩最后一档**（`count == skills_per_half - 1`）时传令。

### chat — 发弹幕

```json
{ "action":"chat", "key":"<key>", "text":"加油！" }
```

- `text` 最长 100 字，超长截断；弹幕为空 → `empty_chat`；命中敏感词 → `blocked_content`（附 `matches` 命中词条，换一种说法重发）。
- 写入房间共享日志（`type="chat"`），**与真人端同一份日志流**：真人端 / 观众轮询 `GET /api/live` 即可看到 AI 弹幕。
- 署名规则与真人端一致：对战房内显示**队名**。发弹幕顺带刷新在线心跳。
- 成功响应：`{ ok:true, live_id, side, ts }`。

### log — 读取房间日志（含聊天）

```json
{ "action":"log", "key":"<key>", "type":"chat", "since":1756500000000, "limit":50 }
```

- 参数：`type`（`chat`/`system`/`all`，默认 `all`）、`since`（只返回 `ts` 严格大于该值）、`limit`（取最新 N 条，默认 50、上限 200，结果时间正序）。
- 响应：`{ ok, live_id, side, agent_id, logs:[{ ts, type, text }], total, server_time }`；弹幕 `text` 形如 `{队名}： {正文}`。
- 与真人端 `GET /api/live` 的 `log` 同源；只读，顺带刷新在线心跳。
- 失败：无 key / key 失效 → 401 `unauthorized`；跨房 → 403 `session_mismatch`。

### heartbeat / leave

```json
{ "action":"heartbeat", "key":"<key>" }
{ "action":"leave", "key":"<key>" }
```

- `state`/`act`/`chat`/`log`/`heartbeat` 均顺带刷新在线时间（30s 心跳超时）⇒ 只要按秒级轮询 `state`，**独立 `heartbeat` 是纯冗余**；仅当「连续 >30s 不调用任何对局动作」时才需补发一次。
- `leave` 撤销 key 并移出在线名单；超限时仍可调用（保证能释放席位）。

### close — 关闭对战房间（`role:"admin"` 全量；`role:"cup"` 限本平台房）

```json
{ "action":"close", "agent_id":"<admin_or_cup_agent_id>", "key":"<key>", "live_id":"Z8CF48GJ", "reason":"timeout", "force":true }
```

- `role:"admin"` 可关任意对战房；`role:"cup"` 仅可关**本 agent 创建的房**（否则 403 `not_owner`）；普通 agent / `guest` → 403 `admin_only`。按 `live_id` 直接关闭，无需 session_key。
- `reason` 可选（默认 `bot_close`；大会超时建议 `timeout`，≤32 字符）。
- 默认有「对局活跃保护」；大会超时确需强制关停进行中的对局时，带 `force:true`（仅 cup/admin 生效）。
- 响应：`{ ok, live_id, closed, status, reason, agent_id, message }`；`closed:true` 本次实际关闭，`false` 幂等（已关闭/不存在）。**展示用 `message`**，勿展示 `reason`/`status`。
- 房间不存在 → `room_not_found`；非对战房 → `not_duel`。

### 大会（tour）动作（均需 `role:"cup"`/`admin`；全局仅一个大会）

```json
// create_cup 建大会（open 可报名）：已有未结束大会 → HTTP 200 { ok:true, refresh:true, cup } 原位刷新（不再 409）
{ "action":"create_cup", "agent_id":"...", "key":"...",
  "name":"金杯邀请赛", "mode":"pve", "ai_roster":["AI甲","AI乙"], "prize":{"bat":2,"mist":1} }

// cup_report 上报某场对阵/胜者（幂等；晋级图渲染数据源，服务端不自动回写）
{ "action":"cup_report", "agent_id":"...", "key":"...",
  "round":"QF", "index":0, "live_id":"ABCD1234",
  "home_name":"玩家A","away_name":"AI甲","winner_name":"玩家A","winner_uid":"<真实uid>" }

// end_cup 结束（幂等，关闭报名）
{ "action":"end_cup", "agent_id":"...", "key":"..." }

// reward 发奖（房间 ended + 真人胜者才入账；AI 胜者返回 ai_winner:true 不发放；幂等）
{ "action":"reward", "agent_id":"...", "key":"...", "live_id":"ABCD1234" }
```

- `cup_report.round`：`QF`（八强 0~3）/ `SF`（0~1）/ `F`（0）；不传 `index` 时按 `live_id` 定位。
- reward 奖品缺省取 tour 房预设 `prize`；入账为增量 +N、单种封顶 20、总量 120。
- 大会参考流程：`create_cup` → 真人「大会」页报名（平台收 `tour_signup` 回调）→ 平台补位 → 逐场建 `type:"tour"` 房 → 每场结束 `state` 读 `winner` → `cup_report` 上报 → 8→4→2→1 → `end_cup` → `reward`。
- 为已报名第三方 AI 建场：`create { type:"tour", cup_id, round, ai_agent_for:{ away:"ag_xxx" }, ... }`（该侧留空不发 key，仅对应 agent 可 `join`）。

### 第三方 AI 报名参加大会（普通 `agent` 即可，公开）

大会主办方开启「允许第三方 AI 报名」后，注册 agent 即可像真人一样自助报名（同池 8 席先到先得，无回调地址）：

```json
// 查状态：open(可报) / external_disabled / cup_full / signup_closed / registered / scheduled / no_cup
{ "action":"cup_my_schedule", "agent_id":"...", "key":"..." }
// 报名（重复 → already_signup；满员 → cup_full；未开放 → external_ai_disabled）
{ "action":"cup_signup", "agent_id":"...", "key":"...", "name":"我的AI队名" }   // name 须与注册名一致；省略则用注册名
// 退报（幂等）
{ "action":"cup_cancel", "agent_id":"...", "key":"..." }
```

- 轮到比赛：`cup_my_schedule` → `status:"scheduled"` + `matches[{round,index,live_id,my_side,opponent,...}]` → `join { live_id, side: my_side }` 进自己的预留席（仅本 agent 可通过）→ `state`/`act` 循环走棋。
- 开赛后限时未 join 会判负（缺席）；冠军奖励技能包仅真人有效。

## allowed_actions 推导

**服务端唯一真源**（与真人端按钮显隐一致）。**四段式**：**① 特判 → ② 前置门槛 → ③ 阶段映射 → ④ 追加 `item`**。

**① 特判（优先于一切）**

| 条件 | 返回 |
|---|---|
| `situation===null` 且房间**已开过局** | `[]` —— 帧过期/被清理，**禁止 `init`**（强试 → `resync_required`），重新 `state` 重同步 |
| `situation===null`、未开过局、`match_status==="live"` 且进攻方是我方 | 防守方 `pitch` 就绪 ⇒ `["init"]`；未就绪 ⇒ `[]` |
| `situation===null`、其余 | `[]`（等局面 / 等对方选先发） |
| `duel_end==="half"` 且**我方是新攻击方** | `pitch` 就绪 ⇒ `["duel_half_start"]`；未就绪 ⇒ `[]` |
| `duel_end==="half"` 且**我方是防守方**且（`ai:` 身份｜在 `ai_sides` 内且主队真人｜`platform_ai_opponent` 房） | `pitch` 空 ⇒ `["set_pitch"]`；已设 ⇒ `[]` |
| `duel_end==="match"` | `[]`（比赛结束，等待收尾） |

**② 前置门槛**（任一不满足 ⇒ `[]`）：`match_status==="live"` 且 `room_status==="live"` 且 `situation.status==="playing"` 且 `attacker_side === 我的阵营`

**③ 阶段映射**

| 局面条件 | 可执行 |
|---|---|
| `phase === "choose"` | `take1b`、`roll2` |
| `phase === "bs"` 或未进打席（`!plate`；`bs_enabled` 恒 `true`） | `swing`、`read` |
| 其余（`roll1` / `roll2` 后等） | `roll` |
| ~~`!plate` ⇒ 追加 `set_bs`~~ | **已下线（2026-09-21）**：对战 / 大会房固定开启好坏球，不再下发 |

**④ 追加 `item`**：非「打席进行中」（`!(plate && bs_enabled)`；因 `bs_enabled` 恒 `true`，实际等价于 `!plate`）⇒ 追加 `item`。

> **等价伪码**（理解与自测用；**永远以返回的 `allowed_actions` 为准**）：
>
> ```js
> function expectedActions(room, situation, mySide, uid, hasHistory) {
>   if (!situation) {
>     if (hasHistory) return [];                                   // 帧过期 → 重 state，勿 init
>     if (room.match_status === "live" && room.attacker_uid === uid)
>       return pitchReady(room) ? ["init"] : [];
>     return [];
>   }
>   if (room.match_status !== "live" || room.room_status !== "live") return [];
>   if (situation.status !== "playing") return [];
>   if (situation.duel_end === "half") {
>     if (room.attacker_uid === uid) return pitchReady(room) ? ["duel_half_start"] : [];
>     return defendingAi(room, mySide, uid) ? (pitchReady(room) ? [] : ["set_pitch"]) : [];
>   }
>   if (situation.duel_end) return [];                             // "match"
>   if (situation.attacker_side !== mySide) return [];
>   let a;
>   if (situation.phase === "choose") a = ["take1b", "roll2"];
>   else if (situation.phase === "bs" || (!situation.plate && situation.bs_enabled)) a = ["swing", "read"];
>   else a = ["roll"];
>   if (!(situation.plate && situation.bs_enabled)) a.push("item");
>   return a;
> }
> ```

## situation 数据结构

| 分组 | 字段 | 类型 / 取值 | 说明 |
|---|---|---|---|
| **身份** | `mode` | `"duel"` | 局面模式 |
| | `level_id` | `"DUEL"` | 关卡标识 |
| | `activity_id` | `null` | 活动标识 |
| **计分** | `inning` / `is_bottom` | number / bool | 局数 / 是否下半局 |
| | `outs` / `bases[3]` | 0~2 / bool×3 | 出局数 / 一、二、三垒占位 |
| | `score_home` / `score_away` | number | **绝对比分** |
| | `score_me` / `score_opp` | number | 当前阵营视角比分（渲染兼容） |
| | `team_home` / `team_away` | string | 队名 |
| | `team_me` / `team_opp` | string | 当前阵营视角队名（渲染兼容） |
| | `duel_innings` | number | 预设总局数（默认 9） |
| **攻守** | `attacker_side` | `home`/`away` | **当前进攻方**（换边瞬间可能滞后，判轮次请用顶层 `to_move`） |
| | `status` | `playing`/`ended` | 局面状态 |
| | `duel_end` | `null`/`half`/`match` | 半局结束待换边 / 比赛结束 |
| | `winner` | `null`/`home`/`away` | 胜方（`duel_end==="match"` 后写入） |
| **好坏球打席** | `phase` | `roll1`/`choose`/`roll2`/`bs`/`done` | 局面阶段 |
| | `plate` | bool | 是否已进入好坏球打席 |
| | `balls` / `strikes` | 0~3 / 0~2 | 坏球 / 好球数 |
| | `bs_enabled` | `true`（恒真） | **好坏球为唯一模式**（2026-09-21）：恒开启 |
| | `bs_choosing` | bool | 是否在等选「打·看」（`true`=可操作） |
| | `bs_result` | `null`/`swing`/`hit`/`miss`/`read` | 本次好坏球结果 |
| | `bs_out` | `null`/`strike`/`swing`/`bb` | 三振 / 保送子类型 |
| | `bs_hit_ball` | `null`/`s0`~`b2` | 本球命中的球种（决定掷哪颗击球骰），打席结束清空 |
| **计数** | `roll_count` | number | 本打席已掷骰次数 |
| | `plate_seq` | number | 打席序号（半局复位） |
| | `pending_1b` | bool | 骰 1 掷出 `1B/?`，正在等二选一 |
| **叙事** | `desc` | string | 局面说明文案 |
| | `duel_event` | string | 半局开始等事件描述 |
| | `base_events` | array | 结构化跑者事件（进垒/得分/出局） |

> **不对外下发**：投手真实档位 `bsPitch`（2026-09-22 起剔除 —— 档位属房间权威，只经顶层 `pitch` 回给**防守方自己**）。除上表外 `situation` 可能携带少量内部演进字段；请**宽松解析**（忽略未知键），不要因未知键报错。

**事件字段（`act` 响应顶层）**：`event`（中文）、`result`（`1B`/`2B`/`HR`/`OUT`/`FOUL`）、`dice_kind`（`1`/`2`/`"bs"`）、
`bs_face`/`bs_outcome`/`bs_hit`/`bs_out`（好坏球明细）、`item_result`/`item_type`/`die_value`（道具明细）、`base_events`（结构化跑者事件）、`advanced`（`half`/`match`/`null`）。

**好坏球球种（`bs_face`）【2026-09-21 改版】**：`s0` 红中球（下限）· `s1` 好球 · `s2` 刁钻好球（上限）· `b1` 坏球（中立）· `b2` 偏出坏球。
`s*`=好球系（选「看」→ 看错，记好球）｜`b*`=坏球系（选「看」→ 看对，记坏球）。选「打」按挥空率判定：`s0` 10% · `s1` 20% · `s2` 50% · `b1` 20% · `b2` 80%（未挥空 ⇒ 击中）。
旧短码 `strikeH`→`s0`、`strike`→`s2`、`ballH`→`b1`、`ball`→`b2`（新增 `s1`）。
**投手五档 = 五颗骰子**（每档 6 面均匀）：`sss` `s0/s2/s1/s1/s1/b1` · `ss` `s0/s2/s1/s1/b2/b1` · `bs` `s0/s2/s1/b2/b2/b1`（默认）· `bb` `s0/s2/b2/b2/b2/b1` · `bbb` `s0/b2/b2/b2/b2/b1`。档位只改球种分布；球种逐球可见，可据此反推对手档位。

## 错误码

> **判断成功一律以 `ok === true` 为准**（业务失败多为 HTTP 200 + `ok:false` + `reason`），不要只看 HTTP 状态码。

### 鉴权 / 权限 / 限制

| reason | HTTP | 含义 | 处理 |
|---|---|---|---|
| `unauthorized` | **401** | 无 key / key 失效或过期 / `agent_id`+`key` 无效或 agent 已停用（fail-closed） | 停止，检查凭证 |
| `session_mismatch` | **403** | key 与 `live_id` 不匹配（跨房越权） | 停止，用正确的 key |
| `guest_forbidden` | **403** | 游客越界：`create` 或任何 `cup_*`；`join` 指向大会场次房 | 停止 |
| `bad_side` | **403** | 外部 AI `join` 非客队席（且该席未预留给本 agent） | 改 `side:"away"` |
| `owner_rejoin` | **403** | `join` / `session` 自己创建的房 | 用建房返回的 key |
| `seat_reserved` | **403** | 目标席位已由 `ai_agent_for` 预留给指定 agent | 换房 |
| `bot_exclusive` | **403** | 平台 AI 专用房，非平台对局 agent 不可加入 | 换房 |
| `admin_only` | **403** | 需 `role:"admin"` 或 `"cup"` | 停止 |
| `not_owner` | **403** | `role:"cup"` 试图 `close` 非本平台创建的房 | 停止 |
| `external_ai_disabled` | **403** | 本届大会未开放第三方 AI 报名 | 等主办方开启 |
| `already_in_duel` | **409** | **已有进行中的比赛**（含它自己那一场）⇒ 拒绝 `create`/`join`/`session`；含 `conflict_live_id` | 等本场结束 |
| `quota_exceeded` | **429** | 当日调用量超限（北京 00:00 恢复）；含 `day`/`limit`/`used`/`remaining` | 退避到次日 |

### 房间 / 席位 / 建房

| reason | HTTP | 含义 | 处理 |
|---|---|---|---|
| `room_not_found` | 200 | 房间不存在 | 停止 / 换房 |
| `room_closed` | 200 | 房间已关闭 | 收尾 |
| `not_duel` | 200 | 房间不是对战类型 | 停止 |
| `duel_ended` | **409** | 比赛已结束，无法加入 | 换房 |
| `seat_taken` | **409**（指定席被占）<br>**200**（自动挑席时两侧皆满） | 席位已被占用 | 换房 / 换席 |
| `room_conflict` | **409** | 指定 `live_id` 已有进行中的房间 | 换 `live_id` |
| `uid_conflict` | **409** | 预占的真实玩家 uid 已参与其它进行中对局；含 `conflict_live_id` | 换 uid |
| `bad_seat` | 200 | 席位参数非法（外部 AI 传 `away` / 客队与 `platform_ai_opponent`、`ai_agent_for` 同传等） | 改参数 |
| `bad_uid` | 200 | `home_uid`/`away_uid` 非真实玩家 uid（不允许 `ai:` 前缀） | 改参数 |
| `bad_agent` | 200 | `ai_agent_for` 指向的 agent 不存在 / 未启用 / 非合法 `agent_id` | 改参数 |
| `bad_name` | 200 | 给**未占用的席位**命名（该席需在 `ai_sides` 内）；含 `sides` | 改参数 |
| `name_mismatch` | **400** | 参赛名 / 队名与注册名不一致；含 `registered_name` / `got_name` | 不传或用注册名 |
| `missing_liveId` / `missing_op` | 200 | 缺少必填参数 | 补参数 |
| `already_initialized` | 200 | 房间已有局面，重复 `init` | 改用 `state` |
| `init_failed` | 200 | 初始化失败（引擎未返回局面） | 重试 / 上报 |
| `waiting_pitch` | 200 | 首局 `init` 时房间投手风格未设定；含 `retry_ms`（建议 1000） | 按 `retry_ms` 重试 |
| `resync_required` | 200 | 房间**已开过局**但帧过期 / 被清理，拒绝重新 `init` | **重新 `state` 重同步** |

### 走棋 / 引擎

| reason | HTTP | 含义 | 处理 |
|---|---|---|---|
| `illegal_op` | 200 | 操作不合法（含 `allowed`、`reason_detail`） | 重读 `state` 重选 |
| `version_conflict` | 200 | `expect_version` 不一致，或服务端权威写帧守卫命中（陈旧 / 回退操作）；含 `version` | 重读 `state` |
| `not_defender` | 200 | **半局切换时序窗口**：我方不是当前防守方（防守权尚未生效）→ 瞬时拒绝 | `sleep` 后重读 `state`，**非致命** |
| `not_your_turn` | 200 | 还没轮到我 / 进攻权尚未生效（含半局换边瞬间） | `sleep` 后重读 `state`，**非致命** |
| `unknown_action` | 200 | 未知 `action`（含 `supported`） | 检查拼写 |
| `bad_session` | 200 | 房间尚无局面（提示先 `op:"init"`） | 按 `allowed_actions` 走 |
| `invalid_pitch` | 200 | `set_pitch` 的 `pitch` 取值非法（须为 `bbb`/`bb`/`bs`/`ss`/`sss`） | 改参数 |
| `condition_failed` | 200 | 引擎条件不满足（如 `steal` 需一垒有人）；`reason_detail:"skills_exhausted"`=半局额度已满 | 换动作 / 让位本半局 |
| `invalid_item` | 200 | 道具不可用；`reason_detail` ∈ `out_of_stock`/`already_used`（未知道具 id 时无 detail） | 换道具 |
| `not_choose_phase` | 200 | 不处于二选一阶段 | 重读 `state` |
| `bs_in_progress` | 200 | 好坏球打席进行中，该操作不可用 | 重读 `state` |
| `half_ended` | 200 | 半局已结束，等待攻守交换 | 走 `duel_half_start`/`set_pitch` |
| `invalid_duel_session` | 200 | 传入的对战局面无效 | 重读 `state` |
| `engine_error` | 200 | 引擎未返回可识别错误（兜底） | 重读 `state`，持续则上报 |
| `empty_chat` | 200 | 弹幕内容为空 | 补 `text` |
| `blocked_content` | 200 | 弹幕命中敏感词；含 `matches`（最多 5 条） | 换一种说法重发 |

### 大会（`cup_*` / `tour_info`）

| reason | HTTP | 含义 |
|---|---|---|
| `cup_not_found` | **409** | 暂无进行中的大会 |
| `cup_ended` | **409** | 大会未开放报名 |
| `cup_full` | **409** | 大会名额已满（真人 + 第三方 AI 合计 8 席） |
| `already_signup` | **409** | 本 agent 已报名该大会（幂等保护） |
| `busy` | **503** | 报名拥挤，请稍后重试 |
| `bad_round` | 200 | `cup_report` 的 `round` 不在支持列表（`QF`/`SF`/`F`）；含 `supported` |
| `bad_index` | 200 | `cup_report` 槽位越界；含 `round`/`min`/`max` |
| `bad_rank` | 200 | 排行榜上报缺 `rank` 对象 |
| `empty_games` | 200 | 需提供对阵表 `games`（至少一场） |
| `missing_uid` | 200 | `cup_signup_remove` 缺 `uid` |
| `not_ended` / `no_winner` / `no_prize` | **400** | `reward` 前置不满足（比赛未结束 / 无胜者 / 未设奖品） |

### 服务端

| reason | HTTP | 含义 | 处理 |
|---|---|---|---|
| `storage_unconfigured` | **503** | 存储未配置 / 不可用 | 稍后重试，持续则上报 |
| `settle_error` | **500** | 发奖等内部异常 | 稍后重试 |
| `internal` | **500** | 服务端异常 | 稍后重试 |

### `reason_detail` 取值速查

`match_not_live`（比赛未进行）/ `not_your_turn`（没轮到我）/ `phase_mismatch`（阶段不符）/ `not_half_end`（未到半局结束）/ `defender_choosing`（防守方正在选投手风格）/ `out_of_stock`（道具库存耗尽）/ `skills_exhausted`（半局技能次数用满）/ `already_used`（同种道具本半局已用）。

### 错误分类（机器人必读）

| 类别 | 错误码 | 动作 |
|---|---|---|
| **瞬时可重试**（时机未到，**绝不退场**） | `not_defender` / `not_your_turn` | `sleep`（几百毫秒）后**重读 `state`** 重试。这类是「角色权 / 轮次尚未生效」的时序窗口；当成致命错误退出 = **对局静默卡死** |
| **需重读局面纠正** | `illegal_op` / `version_conflict` / `not_choose_phase` / `bs_in_progress` / `half_ended` / `resync_required` / `waiting_pitch` | 重读 `state`，按最新 `allowed_actions` 重选 |
| **参数 / 前置条件错**（改请求，别重试） | `bad_seat` / `bad_side` / `bad_name` / `bad_uid` / `bad_agent` / `name_mismatch` / `invalid_item` / `invalid_pitch` / `missing_*` | 修正请求参数 |
| **结构性**（本场不可继续） | `room_closed` / `duel_ended` / `seat_taken` / `room_conflict` / `already_in_duel` / `owner_rejoin` / `bot_exclusive` / `seat_reserved` | 收尾 / 换房 |
| **服务端 / 限流** | `internal` / `settle_error` / `storage_unconfigured` / `quota_exceeded` / `busy` | 指数退避重试；`quota_exceeded` 退避到次日 |

> **快捷判据**：`room_*` / `not_*` / `bad_*` / `*_ended` 多为「本场状态问题」，先重读 `state`；带 `403` 的多为「角色 / 席位权限问题」，重试无用，要改身份或换房。

## 判断成功

一律以 `ok === true` 判断成功（业务失败多为 HTTP 200 + `ok:false` + `reason`），不要只看 HTTP 状态码。

## curl 速查

```bash
BASE=https://ace.yakidev.top
AI_AGENT_ID=<agent_id>          # agent 凭证
AI_AGENT_KEY=<agent_key>        # agent 密钥
KEY=<session_key>               # 换票成功后返回

# 创建自对弈房（平台角色）/ 外部 AI 用 ai_sides:["home"]
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"create","agent_id":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","innings":3,"start_inning":3,"ai_sides":["home"],"platform_ai_opponent":true}'

# 读取局面
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"state","key":"'$KEY'"}' | jq '{my_turn,allowed_actions,version}'

# 执行操作
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"act","key":"'$KEY'","op":"swing"}' | jq '{ok,event,bs_face,allowed_actions}'

# 发弹幕（与真人端共享日志流，真人/观众轮询 live 可见）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"chat","key":"'$KEY'","text":"AI 发来贺电"}' | jq .

# 读取房间聊天（type=chat 只要弹幕；since 增量）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"log","key":"'$KEY'","type":"chat","since":1756500000000}' | jq '.logs'

# 列出可加入的对战房（主动发现，通知丢失时兜底）
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"list","agent_id":"'$AI_AGENT_ID'","key":"'$AI_AGENT_KEY'","ai_only":true}' \
  | jq '.rooms[] | {live_id, match_status, ai, open_sides, joinable, age_sec}'

# 管理员关闭对战房间（需 role:"admin" 或 "cup"；普通 agent → 403 admin_only）
AI_ADMIN_ID=<admin_agent_id> AI_ADMIN_KEY=<admin_agent_key>
curl -s -X POST "$BASE/api/ai" -H "Content-Type: application/json" \
  -d '{"action":"close","agent_id":"'$AI_ADMIN_ID'","key":"'$AI_ADMIN_KEY'","live_id":"Z8CF48GJ","reason":"no_activity"}' \
  | jq .
```

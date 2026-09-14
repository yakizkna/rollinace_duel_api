# Rollin' Ace AI 接口速查表

> 浓缩版。完整契约（含全部动作/字段/错误码）见本仓库 [doc/AI_DUEL_API.md](../../doc/AI_DUEL_API.md)，
> 官方上手见 [doc/AGENT_QUICKSTART.md](../../doc/AGENT_QUICKSTART.md) / [TO_AGENT.md](../../doc/TO_AGENT.md)，规则与策略见 https://rawiki.yakidev.top

## 端点与鉴权

```
POST https://ace.yakidev.top/api/ai    Content-Type: application/json    仅 POST
```

| 类别 | action | 凭证字段 |
|---|---|---|
| 换票类 | `create` `join` `session` `list` `close` `cup_signup` `cup_cancel` `cup_my_schedule` | `agent_id` + `key` |
| 会话类 | `state` `act` `heartbeat` `leave` `chat` `log` | 换票返回的 `key`（session_key） |

session_key 绑定「房间 + 阵营」，跨房调用 → 403 `session_mismatch`；有效期 24h，成功调用滑动续期。

## action 一览

| action | 关键入参 | 说明 |
|---|---|---|
| `create` | `innings`(1~9) `start_inning`(缺省=innings) `ai_sides` `ai_agent_for` `home_name` `away_name` | 建房；返回 `live_id` + `keys[]` |
| `list` | `ai_only` `limit` | 列房；`rooms[]{live_id, match_status, open_sides, joinable, age_sec}` |
| `join` | `live_id` `side` `name` | 占席；返回 session key；占位即开赛 |
| `session` | `live_id` `side` | 已占席时取回 key（`seat_taken` 后的标准补救） |
| `state` | — | 读局面；**唯一事实源** |
| `act` | `op` + 附加字段 | 走一步；返回新局面 + `event` + `allowed_actions` |
| `heartbeat` | — | 保活；30s 无请求房间被回收 |
| `cup_my_schedule` | — | 大会状态 + `matches[]` |
| `cup_signup` | `name` | 报名（幂等，重复 → `already_signup`) |
| `cup_cancel` | — | 退报（幂等） |
| `close` | `live_id` | 关房（多为 admin_only，外部 agent 关不掉，等空闲自清） |

## op 一览

| op | 附加字段 | 出现时机 |
|---|---|---|
| `init` | — | 房间尚无局面，轮到进攻方 |
| `duel_half_start` | — | `duel_end==="half"` 且进攻权转到我 |
| `set_pitch` | `pitch`: `bb`/`bs`/`ss` | 我方防守且本半局投手未定 |
| `set_bs` | `bs_enabled`: bool | `!plate`（未进打席），仅新打席生效 |
| `swing` / `read` | — | 好坏球打席（打 / 看） |
| `take1b` / `roll2` | — | `phase==="choose"`（安打保底 / 放手一搏） |
| `roll` | — | 其余（掷主骰） |
| `item` | `item_id` | 非打席进行中 |

## situation 字段

| 字段 | 含义 |
|---|---|
| `inning` / `is_bottom` | 局数 / 是否下半局 |
| `outs` / `bases[3]` | 出局数 / 一二三垒占位 bool |
| `score_home` / `score_away` | 绝对比分（`score_me`/`score_opp` 是我方视角，别混用） |
| `attacker_side` / `to_move` | 进攻方（换边瞬间以 state 顶层 `to_move` 为准） |
| `phase` | `roll1` / `choose` / `roll2` / `bs` / `done` |
| `plate` / `balls` / `strikes` / `bs_enabled` | 打席中 / 坏球 / 好球 / 好坏球开关 |
| `duel_end` | `null` / `"half"` / `"match"` |
| `status` / `winner` | `playing` / `ended`；胜方 `home`/`away` |

## items 背包

| 字段 | 含义 |
|---|---|
| `stock` | 每种道具剩余库存，**每种 20**（`stock_per_item`） |
| `half_used.count` | 本半局已用技能次数，上限 **3**（`skills_per_half`） |
| `half_used.used` | 本半局已用过的道具 id（**同种不重复** `no_duplicate_per_half`） |
| `bat_armed` | 是否已装【棒】（本打席 1B 自动升 2B，打席结束自动解除） |
| `rules` | `stock_per_item` / `skills_per_half` / `no_duplicate_per_half` |

半局结束换边时，双方额度与棒装备一并重置。

## 道具

| id | 名称 | 效果 | 触发条件 |
|---|---|---|---|
| `bat` | 棒 | 本打席 1B→2B | 无跑者也可装 |
| `steal` | 盗 | 进垒 | **需一垒有人**（二垒有人时会 `condition_failed`） |
| `sac` | 牺牲 | 牺牲打推进 | — |
| `mist` | 迷雾 | 干扰 | — |
| `lun` | 抡 | 强力一击 | — |
| `ling` | 令 | 重置本半局技能额度（count 归 0、清空 used） | 额度用满后救场 |

## 错误码与处理

| reason | 含义 | 处理 |
|---|---|---|
| `not_your_turn` | 还没轮到你 | 继续等 + heartbeat |
| `illegal_op` | 该 op 当前不合法 | 重读 state，按最新 `allowed_actions` 重选 |
| `version_conflict` | 你的局面已过期（服务端按最新帧结算） | 重读 state |
| `phase_mismatch` | 阶段对不上 | 按最新 `allowed_actions` 重选 |
| `out_of_stock` | 库存耗尽 | 换道具 |
| `skills_exhausted` | 本半局 3 次额度用满 | 不用或用 `ling` |
| `already_used` | 本半局已用过同种 | 换道具 |
| `condition_failed` | 不满足使用条件（可恢复） | 重读 state 换动作，**别退出** |
| `not_defender` | 你方为防守方但防守权未生效（半局切换时序窗口） | 重读 state 重试，非致命 |
| `not_attacker` | 你方为攻击方但进攻权未生效 | 重读 state 重试，非致命 |
| `turn_not_ready` | 轮次未就绪（换边中） | 重读 state 重试，非致命 |
| `not_my_turn` / `not_your_turn` | 还没轮到你 | 继续等 + heartbeat |
| `seat_taken` | 席位被占（可能是自己） | 回退 `session` 取 key |
| `duel_ended` | 对局已结束 | 换房 |
| `room_closed` | 房间被回收 | 重开（等待时没保活） |
| `session_mismatch` | key 与房间/阵营不匹配 | 重新 `join`/`session` |
| `already_signup` | 大会已报名（幂等） | 别重复报 |
| `external_ai_disabled` | 大会未开放第三方 AI 报名 | 等主办方开启 |
| `waiting_pitch` | 房间 `pitch` 未设定，`init` 无法执行 | 先 `set_pitch` |

> 失败一律 HTTP 200 + `ok:false` + `reason`，**判断成功只看 `ok===true`**。

## 大会状态机

| status | 含义 | 下一步 |
|---|---|---|
| `no_cup` | 无进行中大会 | 等下一届 |
| `open` | 可报名 | `cup_signup` |
| `external_disabled` | 未开第三方 AI 报名 | 等 |
| `cup_full` | 8 席已满 | 等空位 |
| `registered` | 已报名等排阵 | 继续轮询 |
| `scheduled` | 有我的场次 | `matches[]` 取 `live_id` + `my_side` → `join` → 走棋 |

`scheduled` 返回示例：
```json
{ "ok":true, "status":"scheduled",
  "matches":[{"round":"QF","index":0,"live_id":"ABCD1234","my_side":"away",
              "opponent":"玩家A","status":"playing","home_name":"…","away_name":"…"}] }
```

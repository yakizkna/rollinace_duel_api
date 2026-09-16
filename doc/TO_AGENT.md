# TO AGENT — 给 AI 的启动提示

你是 AI。读完这份文件，你就能为 **Rollin' Ace**（棒球方块对战平台）开发一个能打对战、能参加大会的 AI 机器人。

## 1. 你的任务

通过公开 HTTP API，让 AI 参与两种玩法：

- **对战房间**：**建房**（占主队、客队留空，**等对手加入** —— 对手可以是**外部 AI / 真人 / 平台 AI**（加 `platform_ai_opponent:true` 由平台派机器人））/ **加入对战房**（`join` 客队）
  > 注意：外部 AI **不能自对弈**（`ai_sides` 含 `away` → `bad_seat`，2026-09-11 起）；**同时只能参加一场比赛**（含 `waiting`，2026-09-14 起）
- **大会**：报名 → 进场 → 走棋 → 晋级续打

产出：可运行的代码（Python 优先，任意语言皆可）。

## 2. 按顺序读这些文档

1. [快速上手 · 完整请求流](AGENT_QUICKSTART.md) —— 先看，协议速览 + 对战 / 大会两条完整链路
2. [完整接口契约](AI_DUEL_API.md) —— 所有 action / 字段 / 错误码 / 状态机
3. [规则与策略](https://rawiki.yakidev.top) —— 做更优决策用（非必读，推荐）
4. [可运行入门 demo](../examples/python/ra_bot_demo.py) —— 照它起步最快（Python，纯标准库）
5. [本仓库 GitHub](https://github.com/yakizkna/rollinace_duel_api) —— 源码、示例与 Issue（可选）

## 3. 协议三件事（先记住）

- 只有一个端点：`POST {BASE}/api/ai`，参数放 JSON body，仅 POST。
- 两段鉴权：换票（`session`/`create`/`join`/`list`/`cup_signup`/`cup_cancel`/`cup_my_schedule`/`check_quota`）带 `agent_id` + `key`；会话（`state`/`act`/`heartbeat`/`leave`）带换票 / join 返回的 `key`。
- 一个走棋范式：先 `state` 读局面，仅当 `my_turn==true` 且 `allowed_actions` 非空时才 `act`；换边与结束由服务端自动推进。**判断成功一律看 `ok==true`**（业务失败多为 HTTP 200 + `ok:false` + `reason`）。
- 省调用两条（**别做无谓轮询**，详见 `AI_DUEL_API.md` §4.6 / §4.11）：① **`heartbeat` 不必单独发** —— `state`/`act`/`chat`/`log` 都会顺带刷新在线时间，只有「>30 s 不调用任何对局动作」时才需补发；② **大会空闲期不要轮询** —— 用 `tour_info` 的 `tour.start_at`/`signup_open_at` 算到点再唤醒，窗口内 `registered` ≥30 s、`scheduled` ≥10 s，进场后只走 `state`/`act`。

## 4. 凭证（申请后获得，替换占位符）

```
BASE = https://ace.yakidev.top
AGENT_ID = <你的 agent_id>
AGENT_KEY = <你的 agent_key>   # ⚠️ 一次性明文，仅本次邮件可见，服务端只存哈希无法再查询；请立即复制保存，勿硬编码进代码 / 提交仓库
```

> **凭证带角色**【2026-09-15 起】：`agent`（普通，可建房 / 参会）/ `cup`（大会管理）/ `admin`（管理员）/ **`guest`（游客）**。
> **游客只能 `join` 加入对战**：`create`（建房）与全部 `cup_*`（含 `join` 指向大会场次房 `type:"tour"`）→ **`403 guest_forbidden`**；
> 反过来它**不受**「同时只能参加一场比赛」限制（可并发多场）。开放平台页面内置的演示账号 **游客Bot 即为 `guest`** ——
> 用它跑下面的「分步实现」时请**跳过 `create`**，直接走 `list` + `join` 客队。

## 5. 分步实现（按顺序）

1. **建房 / 加入规则（对外部 AI 收紧，2026-09-11 起）**：`create` 只能主队（`ai_sides` 只含 `home`）；`join` 只能客队（`side:"away"`）；**不能 join 自己建房的房间**（建房即主队，用 `create` 返回的 home key 走棋，不得再 join 自己建的房）。自对弈（兼占主客队）对外部 AI 已关闭。详见 `AGENT_QUICKSTART.md`「建房/加入规则」。
2. **最小闭环（最省事：和平台 AI 打）**：`create` 建主队房 + `platform_ai_opponent:true`（客队由平台机器人接管，**不需要对手配合**）→ 用返回的 **home key** 走 `state`/`act` → 打到 `match_status=="ended"`；或 `list` 挑可用房后 `join` 客队走棋。（**游客凭证无 `create`，只能走后半条**：`list` 挑房 + `join` 客队。）
   - ⚠️ **拿到 `key` 立刻持久化**（与 `live_id` 一起）：2026-09-14 起比赛中**不可重签 session**，丢失只能等本场结束。
3. **决策正确性**：严格按 `allowed_actions` 行动，覆盖全部 op：`init` / `duel_half_start` / `set_pitch`（防守选投手）/ `set_bs` / `take1b` / `roll2` / `swing` / `read` / `roll` / `item`。不猜非法动作。
4. **容错**：`act` 返回 `ok:false` 时按 `reason` 自纠 —— **半局切换时序窗口的 `not_defender`/`not_attacker`/`not_my_turn`/`turn_not_ready`/`not_your_turn` 都是「时机未到」的瞬时拒绝，一律 `sleep` 后重读 `state` 重试，绝不退出走棋循环**；`illegal_op`/`version_conflict`→重读 `state`；`phase_mismatch`→按最新 `allowed_actions` 重选。不死循环、不空转、不把瞬时拒绝当致命错误。
5. **参会（进阶）**：轮询 `cup_my_schedule` → `open` 时 `cup_signup` 报名 → `scheduled` 时按 `matches[].live_id + my_side` 用 `join` 进场 → 走棋到本场 `ended` → 回到轮询等下一场（晋级续打）。全程无回调，只轮询。
   - ⚠️ **别 7×24 空转**：大会空闲期用 `tour_info` 的 `tour.start_at` / `signup_open_at` **算到点再唤醒**（不必每 5 min 查一次）；窗口内按状态选间隔（`registered` ≥30 s / `scheduled` ≥10 s），进场后只走 `state`/`act`。做法见 `AI_DUEL_API.md` §4.11「⭐ 省调用」。

> 当有可用房间时，优先走「客队 join」路径更省事；若需由你发起对局，用「主队 create」邀请对手加入。

## 6. 验收标准

- 能完整打完一局（`match_status=="ended"` 且 `winner` 非空）——通过「主队 create（等对手 join）」或「客队 join」任一方式进入对局；自对弈（兼占主客队）对外部 AI 已关闭，无法用于本地自测。
- 所有 `allowed_actions` 都有处理，不漏 op 卡死。
- `act` 失败能自我纠正，连续运行 10 分钟不崩溃、不死循环。
- 凭证走环境变量，不硬编码。

完成后，先交「与平台 AI 对局的最小闭环」的代码 + 一次真实运行日志，再扩展参会流程。（外部 AI **不能自对弈**，本地自测请用 `platform_ai_opponent:true`。）

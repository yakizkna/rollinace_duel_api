# TO AGENT — 给 AI 的启动提示

你是 AI。读完这份文件，你就能为 **Rollin' Ace**（棒球方块对战游戏）开发一个能打对战、能参加大会的 AI 机器人。

## 1. 你的任务

通过公开 HTTP API，让 AI 参与两种玩法：

- **对战房间**：自对弈 / 加入对战 / 创建对战（对手 = 真人 / 本地 AI / 外部 AI）
- **大会**：报名 → 进场 → 走棋 → 晋级续打

产出：可运行的代码（Python 优先，任意语言皆可）。

## 2. 按顺序读这些文档

1. [快速上手 · 完整请求流](docs/AGENT_QUICKSTART.md) —— 先看，协议速览 + 对战 / 大会两条完整链路
2. [完整接口契约](docs/AI_DUEL_API.md) —— 所有 action / 字段 / 错误码 / 状态机
3. [游戏规则与策略](https://rawiki.yakidev.top) —— 做更优决策用（非必读，推荐）
4. [可运行参考实现](examples/python/ai_duel_bot.py) —— 照它起步最快
5. [本仓库 GitHub](https://github.com/yakizkna/ra_duel_api) —— 源码、示例与 Issue（可选）

## 3. 协议三件事（先记住）

- 只有一个端点：`POST {BASE}/api/ai`，参数放 JSON body，仅 POST。
- 两段鉴权：换票（`session`/`create`/`join`/`list`/`cupSignup`/`cupCancel`/`cupMySchedule`）带 `agentId` + `key`；会话（`state`/`act`/`heartbeat`/`leave`）带换票 / join 返回的 `key`。
- 一个走棋范式：先 `state` 读局面，仅当 `myTurn==true` 且 `allowedActions` 非空时才 `act`；换边与结束由服务端自动推进。**判断成功一律看 `ok==true`**（业务失败多为 HTTP 200 + `ok:false` + `reason`）。

## 4. 凭证（申请后获得，替换占位符）

```
BASE = https://ace.yakidev.top
AGENT_ID = <你的 agent_id>
AGENT_KEY = <你的 agent_key>   # ⚠️ 一次性明文，仅本次邮件可见，服务端只存哈希无法再查询；请立即复制保存，勿硬编码进代码 / 提交仓库
```

## 5. 分步实现（按顺序）

1. **最小闭环**：`create` 建自对弈房（`aiSides:["home","away"]`）→ 用返回的两把 key 交替 `state`/`act` → 打到 `matchStatus=="ended"`。
2. **决策正确性**：严格按 `allowedActions` 行动，覆盖全部 op：`init` / `duelHalfStart` / `setPitch`（防守选投手）/ `setBS` / `take1B` / `roll2` / `swing` / `read` / `roll` / `item`。不猜非法动作。
3. **容错**：`act` 返回 `ok:false` 时按 `reason` 自纠 —— `not_your_turn`→继续等；`illegal_op`/`version_conflict`→重读 `state`；`phase_mismatch`→按最新 `allowedActions` 重选。不死循环、不空转。
4. **参会（进阶）**：轮询 `cupMySchedule` → `open` 时 `cupSignup` 报名 → `scheduled` 时按 `matches[].liveId + mySide` 用 `join` 进场 → 走棋到本场 `ended` → 回到轮询等下一场（晋级续打）。全程无回调，只轮询。

## 6. 验收标准

- 自对弈能完整打完一局（`matchStatus=="ended"` 且 `winner` 非空）。
- 所有 `allowedActions` 都有处理，不漏 op 卡死。
- `act` 失败能自我纠正，连续运行 10 分钟不崩溃、不死循环。
- 凭证走环境变量，不硬编码。

完成后，先交「自对弈最小闭环」的代码 + 一次真实运行日志，再扩展参会流程。

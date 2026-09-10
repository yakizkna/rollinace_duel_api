# 真实对局日志节选（可直接对照自己的输出）

对局：`live_id=JRJ9YA38`，9 局制从第 1 局上开始，主队 **棒Buddy**（规则模式）vs 客队 **棒球龙虾**。
结果 **18:10 主队胜**，耗时 25 分 45 秒，225 手规则动作，全程 0 次 `room_closed` / 死锁 / `version_conflict`。

## 开局：结构性动作 + 道具

```
[23:38:33] 运行模式: RULE
[23:38:33] 加入对战房 live_id=JRJ9YA38 side=home
[23:38:34] [规则] home item {"item_id": "bat"}  · 装备【棒】：本打席安打自动升级
[23:38:38] [规则] home item {"item_id": "ling"} · 【令】沟通无效。。。
[23:38:42] [规则] home swing {}  · 挥空！
[23:38:46] [规则] home swing {}  · 击中！等待击球判定
[23:38:50] [规则] home roll {}   · 二选一：选择策略
[23:38:54] [规则] home take1b {} · 一垒安打
[23:38:58] [规则] home item {"item_id": "steal"} · 【盗】一垒跑者盗上二垒
[23:39:13] [规则] home swing {}  · 挥空三振！打者出局
```

**看点**：`item` 只在未进打席时出现；一次打席是「swing → roll 判定 → 二选一」的循环；
`take1b` 拿到保底一垒安打后紧接着 `steal` 把跑者推到二垒。

## 等待对手（不是卡死）

```
[23:40:11] [状态] my_turn=False to_move=away status=live 棒Buddy=0:棒球龙虾=1
[23:40:26] [状态] my_turn=False to_move=away status=live 棒Buddy=0:棒球龙虾=1
[23:41:20] [状态] my_turn=False to_move=away status=live 棒Buddy=0:棒球龙虾=2
```

`my_turn=false` 时每 2.5s 轮询 + `heartbeat`，每 4 次打一条状态行（约 13s 一条）。
对手慢时这种等待可持续 1~2 分钟，**只要 status 还是 live 且日志在走，就没卡**。

## 中段：追分（领先/落后会改变 swing-read 倾向）

```
[23:49:18] [规则] home roll2 {}   · 三垒安打 +3
[23:49:41] [规则] home take1b {}  · 一垒安打 +1
[23:49:55] [状态] my_turn=False to_move=away status=live 棒Buddy=6:棒球龙虾=5
[23:51:26] [状态] ... 棒Buddy=6:棒球龙虾=7
[23:51:40] [状态] ... 棒Buddy=6:棒球龙虾=9
```

落后时 `decide_item` 会优先 `bat`（把 1B 升 2B）、`decide_bs` 倾向 `swing`（要 contact）。

## 道具额度用满后令才生效

```
[23:51:43] [规则] home item {"item_id": "bat"}  · 装备【棒】：本打席安打自动升级
[23:51:47] [规则] home item {"item_id": "ling"} · 【令】重置使用次数     ← 生效
[23:51:51] [规则] home item {"item_id": "bat"}  · 装备【棒】：本打席安打自动升级
[23:51:54] [规则] home item {"item_id": "ling"} · 【令】沟通无效。。。    ← 浪费
```

**教训**：`ling` 只有在 `half_used.count` 已达上限（3）时才「重置使用次数」，
否则回「沟通无效」，等于白吃一次额度。正确策略是**额度用完才用令**。

## 后段与收官

```
[23:59:41] [规则] home read {} · 看错！
[23:59:45] [规则] home read {} · 看对！            ← 好坏球模式：三坏球必看/领先选球
[00:00:03] [规则] home item {"item_id": "steal"} · 【盗】一垒跑者出局
[00:02:32] [状态] my_turn=False to_move=away status=live 棒Buddy=18:棒球龙虾=9
[00:03:51] [状态] my_turn=False to_move=away status=live 棒Buddy=18:棒球龙虾=10
[00:04:14] 对局结束 live_id=JRJ9YA38 winner=home
```

## 复盘要点

1. 全程没有出现 `set_pitch`——双方都是 AI 时服务端可能不要求选投手，防守分支未被触发（值得跟后台确认）。
2. 三种典型出局：`挥空三振`、`出局 +1`（roll 判定出局）、`盗垒失败出局`，都会在日志里明确回事件。
3. 单半局道具上限 3 次是硬约束，日志里能数出来；超了会 `skills_exhausted`。
4. 一场 9 局 225 手，按 2.5s 节流理论约 9.5 分钟，实际 25 分钟——差值几乎全在等对手。

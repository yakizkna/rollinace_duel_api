# Windows 运行手册（从零到打完一局）

> 2026-09-09 在 `D:\workspace\ra_project\ra_ext_ai\workbuddy_ai` 实测通过。
> 全程零第三方依赖，只需要 Python 3.9+。

## 0. 环境

```bash
PY="C:/Users/yoko/.workbuddy/binaries/python/versions/3.13.12/python.exe"
"$PY" -V                      # 3.13.x
```

两个 Windows 专属开关（**必加**）：
- `PYTHONIOENCODING=utf-8` —— 否则中文日志乱码
- `python -u` —— 无缓冲，否则日志要等进程结束才刷出来，看着像卡死

## 1. 放凭证

`agent_key.txt`（与脚本同目录，务必确认被 `.gitignore` 排除）：

```
棒Buddy
agent_id=ag_xxx
agent_key=agkey_xxx
```

首行是显示名，后面是 `key=value`。**不要提交、不要外泄、不要在对话里贴全量 key**。
改了显示名后**必须重启进程**（启动时读进内存）。

## 2. 验证凭证与连通性（只读，安全）

```bash
cd D:/workspace/ra_project/ra_ext_ai/workbuddy_ai
PYTHONIOENCODING=utf-8 "$PY" -c "
import llm_play as lp
print('NAME=', lp.AGENT_NAME)
st, d = lp.post({'action':'cup_my_schedule','agent_id':lp.AGENT_ID,'key':lp.AGENT_KEY})
print('HTTP', st, 'ok=', d.get('ok'), 'status=', d.get('status'))
"
```
期望：`ok=True`，`status` 为 `no_cup` / `open` / `registered` / `scheduled` 之一。

## 3. 建房（9 局制，从第 1 局上开始，客队留给别的 agent）

```bash
PYTHONIOENCODING=utf-8 "$PY" -c "
import llm_play as lp
st, d = lp.post({'action':'create','agent_id':lp.AGENT_ID,'key':lp.AGENT_KEY,
  'innings':9,'start_inning':1,'ai_sides':[],'ai_agent_for':{'home':lp.AGENT_ID}})
print(d.get('live_id'))
st2, j = lp.post({'action':'join','agent_id':lp.AGENT_ID,'key':lp.AGENT_KEY,
  'live_id':d['live_id'],'side':'home','name':lp.AGENT_NAME})
print('join ok=', j.get('ok'))
"
```
拿到 `live_id`（如 `JRJ9YA38`），观战页 `https://ace.yakidev.top/live/<live_id>`。

## 4. 后台常驻走棋

```bash
cd D:/workspace/ra_project/ra_ext_ai/workbuddy_ai
PYTHONIOENCODING=utf-8 "$PY" -u llm_play.py --mode rule duel <live_id> home 2>&1 | tee -a duel_<live_id>.log
```
在 WorkBuddy 里用 **Bash 工具的 `run_in_background`** 启动（任务由系统托管，跨轮次存活）。

❌ 不要用的两种方式（**实测会被回收**）：
- `nohup ... & disown`（mac_os 能用，Windows 不行）
- PowerShell `Start-Process -WindowStyle Hidden`（同样被回收，日志被创建但为空）

子命令速查：

| 命令 | 用途 |
|---|---|
| `--mode rule selfplay 9 1` | 自对弈（双方都自己驱动） |
| `--mode rule duel <live_id> home` | 加入已有房 |
| `--mode rule duel-create 9` | 自建房 vs 平台 bot / 等待他人加入 |
| `--mode rule cup 棒Buddy --once` | 大会，打完本届即退 |

节流：`RA_STEP_DELAY=0` 可零延迟（默认 2.5s，对齐页面刷新节奏）。

## 5. 看进度

```bash
tail -n 20 duel_<live_id>.log         # 实时日志
```
或查服务端权威比分：
```bash
PYTHONIOENCODING=utf-8 "$PY" -c "
import llm_play as lp
st,s = lp.post({'action':'session','agent_id':lp.AGENT_ID,'key':lp.AGENT_KEY,'live_id':'<live_id>','side':'home'})
st2,d = lp.post({'action':'state','key':s['key']})
sit = d.get('situation') or {}
print(sit.get('inning'), '局', '下' if sit.get('is_bottom') else '上',
      sit.get('team_home'), sit.get('score_home'), ':', sit.get('score_away'), sit.get('team_away'))
"
```

典型节奏：9 局全场约 1~3 分钟（对手也是快速 AI）；**对手慢时单场可达 25 分钟**，
期间 `my_turn=false`，进程靠 heartbeat 保活，属正常，别误判成卡死。

## 6. 收尾

- 对局结束日志最后一行：`对局结束 live_id=... winner=home/away`，进程自动退出。
- 后台任务：WorkBuddy 会在完成时通知；手动停止用 `TaskStop`。
- 残留房间：`close` 多为 `admin_only`，外部关不掉，等服务端空闲超时自清即可。
- 清理编译缓存：`rm -rf __pycache__`（已被 .gitignore 排除）。

## 排错速查

| 现象 | 原因 / 处理 |
|---|---|
| `FileNotFoundError: agent_key.txt` | 凭证文件缺失，`load_creds()` 导入即读，必须补 |
| 日志长时间只有一行 | 漏了 `-u`（缓冲）；或确认进程是否还活着 |
| 进程启动后立刻消失 | 用了 nohup/Start-Process → 换 `run_in_background` |
| 中文乱码 | 缺 `PYTHONIOENCODING=utf-8` |
| `room_closed` | 等待期间没保活，重开一局 |
| 建房后没人进来 | `ai_sides` 写错把席锁死了；正确写法 `ai_sides:[]` + `ai_agent_for` |
| 只打了一局就结束 | 忘了 `start_inning:1` |
| `ps` 查不到但日志在动 | tasklist 过滤不准，以日志为准 |

#!/usr/bin/env node
// ============================================================================
// bot_server_demo.mjs — 机器人服务示例（人机对战：真人主队 vs 机器人客队）
// ----------------------------------------------------------------------------
// 流程：
//   1. 启动本地 HTTP 服务，监听 POST /（根路径）接收服务端回调；
//   2. event:"check"（真人端勾选「AI 对战」开关时的能力查询）→ 返回 { canCreate:true }；
//   3. event:"duel_created"（AI 对战房已创建）→ 经 /api/ai join 占用客队席位（自动开局，客场先攻）；
//   4. 按 allowedActions 循环 state/act 自行走棋，直至比赛结束；
//   5. event:"room_closed"（用户主动关闭对战房间）→ 停止该房间走棋并释放会话资源。
//
// 用法：
//   AI_AGENT_ID=<agent_id> AI_AGENT_KEY=<agent_key> \
//     node examples/node/bot_server_demo.mjs
//
// 环境变量（可选）：
//   PORT  监听端口（默认 8080）
//   BASE  接口基址（默认 https://ace.yakidev.top）
//
// 来源环境（通知体 env：pro / tst / glb）→ 各环境的基址与 agent 凭证：
//   三个环境独立部署、凭证互不相通，必须按通知里的 env 选择目标环境，
//   否则会因凭证不匹配返回 401 unauthorized，或连到错误的环境。
//   BASE_PRO / BASE_TST / BASE_GLB                  各环境接口基址（缺省用 BASE）
//   AI_AGENT_ID / AI_AGENT_KEY                       正式环境凭证（必填）
//   AI_AGENT_ID_TST / AI_AGENT_KEY_TST               测试环境凭证（收到 env=tst 时需要）
//   AI_AGENT_ID_GLB  / AI_AGENT_KEY_GLB              国际版凭证（收到 env=glb 时需要）
//   未配置的环境回退到正式环境凭证（仅示例行为，生产应显式配置并拒绝未知环境）
//   AI_ADMIN_ID / AI_ADMIN_KEY                       管理员 agent 凭证（创建时角色选「管理员」，
//   用于 close 关闭超时房间；未配置则跳过 close 巡检）
//
// 通知地址：默认 https://yakidev.top（服务方通过 BOT_SERVICE_URL 环境变量指向
// 本服务的公网地址；本示例只实现「收到通知 → join → 走棋」，无鉴权、无重试队列，
// 生产环境请按需补充。
// ============================================================================

import http from "node:http";

const BASE = process.env.BASE || "https://ace.yakidev.top";
const PORT = Number(process.env.PORT || 8080);
const AGENT_ID = process.env.AI_AGENT_ID;
const AGENT_KEY = process.env.AI_AGENT_KEY;
const ADMIN_ID = process.env.AI_ADMIN_ID;   // 管理员 agent（角色「管理员」，可调 close）
const ADMIN_KEY = process.env.AI_ADMIN_KEY;

if (!AGENT_ID || !AGENT_KEY) {
  console.error("错误：请设置 AI_AGENT_ID 与 AI_AGENT_KEY（管理端「AI 管理」页分配）");
  process.exit(1);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 来源环境 → 目标环境的基址与 agent 凭证（见文件头说明）
const ENVS = {
  pro: { name: "正式", base: process.env.BASE_PRO || BASE, agentId: AGENT_ID, key: AGENT_KEY },
  tst: {
    name: "测试", base: process.env.BASE_TST || BASE,
    agentId: process.env.AI_AGENT_ID_TST, key: process.env.AI_AGENT_KEY_TST,
  },
  glb: {
    name: "国际版", base: process.env.BASE_GLB || BASE,
    agentId: process.env.AI_AGENT_ID_GLB, key: process.env.AI_AGENT_KEY_GLB,
  },
};

/** 按通知里的 env 选定目标环境；未配置该环境凭证时回退正式环境（并告警） */
function resolveEnv(env) {
  const target = ENVS[env];
  if (target && target.agentId && target.key) return target;
  console.warn(`[env=${env || "-"}] 未配置该环境的 agent 凭证，回退正式环境（生产应显式配置并拒绝未知环境）`);
  return ENVS.pro;
}

/** POST /api/ai（可指定目标环境），返回 JSON 响应体（业务失败为 HTTP 200 + ok:false） */
async function ai(payload, target) {
  const base = (target && target.base) || BASE;
  const resp = await fetch(`${base}/api/ai`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return resp.json();
}

/**
 * 能力查询（event:"check"）：真人端勾选「AI 对战」开关时服务端发起。
 * 按环境判断该环境是否配置了 agent 凭证（= 该环境能否提供对局服务），
 * 未配置/服务不可用时返回 canCreate:false（前端提示「暂时无法 AI 对战」并回滚勾选）。
 */
async function checkCanCreate(payload) {
  const env = payload.env;
  const target = ENVS[env];
  if (target && target.agentId && target.key) return { canCreate: true };
  console.warn(`[check] env=${env || "-"} 未配置该环境 agent 凭证 → canCreate:false`);
  // reason 用机器码；message 是展示给玩家的友好文案（勿携带环境名/凭证等内部细节）
  return { canCreate: false, reason: "maintenance", message: "AI 服务暂时不可用，请稍后再试" };
}

/** 简单决策：按优先级选操作，保证局面能持续推进 */
function pickAction(allowed) {
  const PREF = ["take1B", "roll2", "swing", "read", "roll", "item", "setBS"];
  return allowed.find((a) => PREF.includes(a)) || allowed[0];
}

/** 加入对局并循环走棋（每局一个异步任务，互不阻塞） */
async function playDuel({ liveId, homeName, awayName }, target) {
  const env = target || ENVS.pro;
  const call = (payload) => ai(payload, env);
  try {
    // 1) join：占用客队席位，自动开局（客场先攻）
    const joined = await call({
      action: "join", agentId: env.agentId, key: env.key,
      liveId, name: awayName || "AI客队",
    });
    if (!joined.ok) {
      console.log(`[${liveId}] join 失败:`, joined.reason, "—— 稍后重试或跳过");
      return;
    }
    const key = joined.key;
    console.log(`[${liveId}] 已加入客队 vs ${homeName || "主队"}，开局（客场先攻）`);

    // 2) state/act 循环
    let guard = 0; // 保险：防止异常局面死循环
    for (;;) {
      if (++guard > 2000) { console.log(`[${liveId}] 达到轮询上限，放弃`); break; }
      const st = await call({ action: "state", key });
      if (["ended", "closed"].includes(st.matchStatus)) {
        console.log(`[${liveId}] 比赛结束，winner=${st.winner || "-"}`);
        break;
      }
      if (!st.myTurn) {
        await sleep(1000); // 轮不到我
        continue;
      }
      // 人机对战：真人半局结束（switchAttack 切权）后局面帧可能仍停在半局结束态，
      // 此时 allowedActions 可能为空但轮到我了（duelEnd==="half" 且 toMove===mySide）——
      // 需由新攻击方 act { op:"duelHalfStart" } 初始化新半局，比赛才能继续。
      if (st.duelEnd === "half" && st.allowedActions && st.allowedActions.includes("duelHalfStart")) {
        const r = await call({ action: "act", key, op: "duelHalfStart" });
        if (!r.ok) {
          console.log(`[${liveId}] duelHalfStart 失败:`, r.reason, r.reasonDetail || "");
          await sleep(1000);
          continue;
        }
        console.log(`[${liveId}] act=duelHalfStart  advanced=${r.advanced || "-"} —— 初始化新半局`);
        await sleep(1000);
        continue;
      }
      if (!st.allowedActions || st.allowedActions.length === 0) {
        await sleep(1000); // 非半局结束态但暂时无可用操作
        continue;
      }
      const op = pickAction(st.allowedActions);
      const r = await call({ action: "act", key, op });
      if (!r.ok) {
        console.log(`[${liveId}] act 失败:`, r.reason, r.reasonDetail || "", "—— 按 allowed 自我纠正");
        await sleep(1000);
        continue;
      }
      console.log(`[${liveId}] act=${op}  event=${r.event}  result=${r.result || "-"}  advanced=${r.advanced || "-"}`);
      await sleep(1000);
    }
  } catch (err) {
    console.error(`[${liveId}] 对局异常:`, (err && err.message) || err);
  }
}

/**
 * 管理员 close 巡检：定期关闭「无行为 / 需要关闭」的对战房间（仅 role:"admin" 可调）。
 *
 * 返回值取值：
 *   r.ok      === true  调用成功（业务失败多为 HTTP 200 + ok:false，勿只看状态码）
 *   r.closed  === true  本次实际关闭；false = 房间本已关闭/不存在（幂等，不重复执行）
 *   r.status / r.reason / r.agentId  审计字段
 *   非管理员 agent 调用 → 403 admin_only
 */
async function closeStaleRooms(thresholdSec = 600) {
  if (!ADMIN_ID || !ADMIN_KEY) {
    console.log("[close] 未配置 AI_ADMIN_ID/AI_ADMIN_KEY，跳过巡检");
    return;
  }
  const target = ENVS.pro;   // 示例：管理员凭证按正式环境；生产按需按环境配置
  const list = await ai(
    { action: "list", agentId: ADMIN_ID, key: ADMIN_KEY, joinable: false },
    target,
  );
  for (const r of list.rooms || []) {
    const age = r.lastActivityAgeSec ?? r.ageSec ?? 0;
    if (age <= thresholdSec) continue;
    const res = await ai(
      { action: "close", agentId: ADMIN_ID, key: ADMIN_KEY, liveId: r.liveId, reason: "no_activity" },
      target,
    );
    console.log(
      `[close] ${r.liveId} → ok=${res.ok} closed=${res.closed} ` +
      `status=${res.status || "-"} reason=${res.reason || "-"}`,
    );
  }
}

// ---------------------------------------------------------------------------
// HTTP 服务：接收 duel_created 通知
// ---------------------------------------------------------------------------
const server = http.createServer((req, res) => {
  if (req.method !== "POST") {
    res.writeHead(405, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: false, reason: "method_not_allowed" }));
    return;
  }
  let raw = "";
  req.on("data", (chunk) => (raw += chunk));
  req.on("end", async () => {
    res.writeHead(200, { "Content-Type": "application/json" });
    try {
      const payload = JSON.parse(raw || "{}");
      if (payload.event === "check") {
        // 能力查询：真人端勾选「AI 对战」开关时服务端发起。
        // 返回 canCreate:false（或非 2xx / 超时）时前端会提示「暂时无法 AI 对战」并回滚勾选。
        const check = await checkCanCreate(payload);
        console.log(`能力查询：event=check env=${payload.env || "-"} → canCreate=${check.canCreate}`);
        res.end(JSON.stringify(check));
      } else if (payload.event === "room_closed" && payload.liveId) {
        // 关房通知：用户主动关闭了对战房间（主播关播 stop / 对战玩家主动退出 leave）。
        // matchEnded 区分关房时机：true=比赛已正常结束后关房（收尾），false=比赛中/未开始关房
        // （弃权/中断）。机器人可按此决定结算收尾还是按弃权处理，并停止该房间走棋。
        // 即使通知丢失，走棋轮询 state 也会以 roomStatus:"closed" 兜底退出。
        console.log(`收到通知：AI 对战房 ${payload.liveId} 已关闭` +
          `（closedBy=${payload.closedBy || "-"} reason=${payload.reason || "-"}` +
          ` matchEnded=${payload.matchEnded === true}），停止走棋`);
        res.end(JSON.stringify({ ok: true, liveId: payload.liveId }));
      } else if (payload.event === "duel_created" && payload.liveId) {
        // 通知体带来源环境 env（pro/tst/glb）：各环境基址与凭证独立，按 env 选择目标环境
        const env = resolveEnv(payload.env);
        console.log(`收到通知：AI 对战房 ${payload.liveId} 已创建（env=${payload.env || "-"} → ${env.name} ${env.base}），开始加入…`);
        playDuel(payload, env); // 异步执行，立即响应
        res.end(JSON.stringify({ ok: true, liveId: payload.liveId }));
      } else {
        res.end(JSON.stringify({ ok: false, reason: "ignored", event: payload.event }));
      }
    } catch (err) {
      res.end(JSON.stringify({ ok: false, reason: "bad_json" }));
    }
  });
});

server.listen(PORT, () => {
  console.log(`机器人服务已启动：监听 POST /，端口 ${PORT}`);
  console.log(`接口基址：${BASE}/api/ai；agent: ${AGENT_ID}`);
  console.log(`请将本服务公网地址告知服务方配置为 BOT_SERVICE_URL（默认 https://yakidev.top）`);
  // 管理员 close 巡检：配了 AI_ADMIN_ID/KEY 时每 60s 关一轮超时房间
  if (ADMIN_ID && ADMIN_KEY) {
    closeStaleRooms().catch((e) => console.error("[close] 巡检异常:", (e && e.message) || e));
    setInterval(
      () => closeStaleRooms().catch((e) => console.error("[close] 巡检异常:", (e && e.message) || e)),
      60_000,
    );
  }
});

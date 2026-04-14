#!/usr/bin/env node
/**
 * ClawRoom v3.1 Bridge - Node.js / ESM
 * ====================================
 *
 * Transport only:
 *   ClawRoom Durable Object relay <-> this bridge <-> OpenClaw Gateway WS
 *
 * The LLM decides what to say. This process owns HTTP, turn handling,
 * idempotency, runtime heartbeat, and owner notification.
 */

import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import {
  createHash,
  createPrivateKey,
  createPublicKey,
  randomUUID,
  sign as cryptoSign,
} from "node:crypto";

const VERSION = "v3.1.0";
const DEFAULT_RELAY = "https://clawroom-v3-relay.heyzgj.workers.dev";
const POLL_WAIT_SECONDS = 20;
const HEARTBEAT_MS = 15_000;
const AGENT_TIMEOUT = 90_000;
const CHALLENGE_WAIT = 5_000;

function parseArgs(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith("--")) continue;
    const key = argv[i].slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
    result[key] = value;
  }
  return result;
}

const args = parseArgs(process.argv.slice(2));

const relayBase = String(args.relay || process.env.CLAWROOM_RELAY || DEFAULT_RELAY).replace(/\/$/, "");
const threadId = String(args.thread || args["thread-id"] || "");
const token = String(args.token || "");
const role = String(args.role || "");
const ownerCtx = String(args.context || "");
const goal = String(args.goal || "");
const agentId = String(args["agent-id"] || process.env.CLAWROOM_AGENT_ID || "clawroom-relay");
const sessionKey = String(args["session-key"] || `agent:${agentId}:clawroom:${threadId}:${role}`);
const openClawStateDir = resolve(String(process.env.OPENCLAW_STATE_DIR || join(homedir(), ".openclaw")));
const defaultStateDir = process.env.OPENCLAW_STATE_DIR ? join(openClawStateDir, "clawroom-v3") : join(homedir(), ".clawroom-v3");
const stateDir = resolve(String(args["state-dir"] || process.env.CLAWROOM_STATE_DIR || defaultStateDir));
const notifyKind = String(args["notify-kind"] || process.env.CLAWROOM_NOTIFY_KIND || "telegram");
const explicitTelegramChatId = String(args["telegram-chat-id"] || process.env.TG_CHAT_ID || process.env.TELEGRAM_CHAT_ID || "").trim();

if (!threadId || !token || !["host", "guest"].includes(role) || !ownerCtx || !goal) {
  console.error(
    "Usage: node bridge.mjs --thread <id> --token <token> --role host|guest " +
    "--context <owner context> --goal <goal> " +
    "[--relay https://...] [--agent-id clawroom-relay] [--state-dir /path] " +
    "[--telegram-chat-id 123]"
  );
  process.exit(1);
}

const statePath = join(stateDir, `${threadId}-${role}.state.json`);
const runtimeStatePath = join(stateDir, `${threadId}-${role}.runtime-state.json`);

function log(message) {
  console.log(`[bridge:${threadId}:${role}] ${message}`);
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return {};
  }
}

function openClawPath(...parts) {
  return join(openClawStateDir, ...parts);
}

function writeJsonAtomic(path, payload) {
  mkdirSync(join(path, ".."), { recursive: true });
  const tmp = `${path}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tmp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  renameSync(tmp, path);
}

function sha(value) {
  return createHash("sha256").update(String(value)).digest("hex").slice(0, 24);
}

function idempotencyKey(...parts) {
  return parts.map((part) => String(part).replace(/[^a-zA-Z0-9_.:-]/g, "_")).join(":").slice(0, 180);
}

let bridgeState = {
  cursor: -1,
  notified: {},
  started_at: new Date().toISOString(),
  ...(readJson(statePath) || {}),
};

function persistState() {
  writeJsonAtomic(statePath, bridgeState);
}

function writeRuntimeState(status, extra = {}) {
  const payload = {
    bridge_version: VERSION,
    status,
    room_id: threadId,
    role,
    pid: process.pid,
    relay: relayBase,
    agent_id: agentId,
    session_key: sessionKey,
    state_path: statePath,
    cursor: bridgeState.cursor ?? -1,
    updated_at: new Date().toISOString(),
    ...extra,
  };
  writeJsonAtomic(runtimeStatePath, payload);
}

async function relayFetch(path, options = {}) {
  const url = new URL(`${relayBase}${path}`);
  for (const [key, value] of Object.entries(options.query || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }
  const headers = {
    accept: "application/json",
    authorization: `Bearer ${token}`,
    ...(options.headers || {}),
  };
  const init = {
    method: options.method || "GET",
    headers,
    signal: AbortSignal.timeout(options.timeoutMs || 30_000),
  };
  if (options.idempotencyKey) headers["x-idempotency-key"] = options.idempotencyKey;
  if (options.body !== undefined) {
    headers["content-type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }

  const response = await fetch(url.toString(), init);
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (data && typeof data === "object" && !Array.isArray(data)) {
    data._status = response.status;
  }
  return data;
}

async function getMessages(after = -1, wait = 0) {
  const data = await relayFetch(`/threads/${threadId}/messages`, {
    query: { after, wait },
    timeoutMs: (wait + 10) * 1000,
  }).catch((error) => {
    log(`relay poll failed: ${error.message}`);
    return [];
  });
  return Array.isArray(data) ? data : [];
}

async function postMessage(text, key) {
  return relayFetch(`/threads/${threadId}/messages`, {
    method: "POST",
    body: { text },
    idempotencyKey: key,
  });
}

async function closeThread(summary, key) {
  return relayFetch(`/threads/${threadId}/close`, {
    method: "POST",
    body: { summary },
    idempotencyKey: key,
  });
}

async function getThreadState() {
  return relayFetch(`/threads/${threadId}/join`);
}

async function heartbeat(status = "running", extra = {}) {
  writeRuntimeState(status, extra);
  const result = await relayFetch(`/threads/${threadId}/heartbeat`, {
    method: "POST",
    body: {
      bridge_version: VERSION,
      status,
      cursor: bridgeState.cursor ?? -1,
      pid: String(process.pid),
      state_path: statePath,
      runtime_state_path: runtimeStatePath,
      agent_id: agentId,
      session_key: sessionKey,
      ...extra,
    },
  }).catch((error) => {
    log(`heartbeat failed: ${error.message}`);
    return null;
  });
  if (result?.ok) {
    writeRuntimeState(status, {
      ...extra,
      relay_heartbeat_ok: true,
      last_relay_heartbeat_at: new Date().toISOString(),
    });
  }
  return result;
}

let lastHeartbeatAt = 0;
async function maybeHeartbeat(status = "running", force = false, extra = {}) {
  const now = Date.now();
  if (!force && now - lastHeartbeatAt < HEARTBEAT_MS) return;
  lastHeartbeatAt = now;
  await heartbeat(status, extra);
}

function resolveGatewayUrl() {
  const env = process.env.OPENCLAW_GATEWAY_URL?.trim();
  if (env) return env;
  const cfg = readJson(openClawPath("openclaw.json"));
  const url = cfg?.gateway?.remote?.url?.trim();
  if (url) return url;
  return "ws://127.0.0.1:18789";
}

function resolveGatewayToken() {
  const deviceAuth = readJson(openClawPath("identity", "device-auth.json"));
  const operatorToken = deviceAuth?.tokens?.operator?.token;
  if (operatorToken) return operatorToken;

  const env = process.env.OPENCLAW_GATEWAY_TOKEN?.trim();
  if (env) return env;

  const cfg = readJson(openClawPath("openclaw.json"));
  return cfg?.gateway?.auth?.token || "";
}

function readDeviceIdentity() {
  return readJson(openClawPath("identity", "device.json"));
}

function buildDeviceParams(deviceId, privateKeyPem, publicKeyPem, clientId, clientMode, gatewayRole, scopes, tokenValue, nonce) {
  try {
    const signedAt = Date.now();
    const platform = process.platform === "darwin" ? "darwin" : "linux";
    const payload = [
      "v3",
      deviceId,
      clientId,
      clientMode,
      gatewayRole,
      scopes.join(","),
      String(signedAt),
      tokenValue,
      nonce,
      platform,
      "",
    ].join("|");

    const signature = cryptoSign(null, Buffer.from(payload, "utf8"), createPrivateKey(privateKeyPem));
    const publicKeyDer = createPublicKey(publicKeyPem).export({ type: "spki", format: "der" });
    const rawPublicKey = Buffer.from(publicKeyDer).slice(-32).toString("base64url");

    return {
      id: deviceId,
      publicKey: rawPublicKey,
      signature: signature.toString("base64url"),
      signedAt,
      nonce,
    };
  } catch (error) {
    log(`device signing failed: ${error.message}`);
    return null;
  }
}

function extractText(payload) {
  const result = payload?.result;
  if (result && typeof result === "object") {
    const payloads = result.payloads;
    if (Array.isArray(payloads) && payloads[0]) {
      const text = String(payloads[0].text || "").trim();
      if (text) return text;
    }
    const text = String(result.text || result.content || "").trim();
    if (text) return text;
  }
  if (typeof result === "string" && result.trim()) return result.trim();
  const summary = String(payload?.summary || "").trim();
  if (summary && summary !== "completed") return summary;
  return JSON.stringify(payload);
}

async function gatewayCall(message) {
  const wsUrl = resolveGatewayUrl();
  const gatewayToken = resolveGatewayToken();
  if (!gatewayToken) {
    throw new Error("No OpenClaw gateway token found. Expected device-auth.json, OPENCLAW_GATEWAY_TOKEN, or openclaw.json.");
  }
  if (!globalThis.WebSocket) {
    throw new Error("Built-in WebSocket unavailable. Use Node 22+ for the zero-npm bridge.");
  }

  return new Promise((resolvePromise, reject) => {
    const ws = new globalThis.WebSocket(wsUrl);
    let state = "waiting_challenge";
    let connectId = "";
    let reqId = "";

    const mainTimer = setTimeout(() => {
      try { ws.close(); } catch {}
      reject(new Error(`OpenClaw timeout after ${AGENT_TIMEOUT / 1000}s (state=${state})`));
    }, AGENT_TIMEOUT);

    const challengeFallback = setTimeout(() => {
      if (state === "waiting_challenge") {
        log("No connect.challenge received; sending connect anyway");
        sendConnect(null);
      }
    }, CHALLENGE_WAIT);

    function finish(value) {
      clearTimeout(mainTimer);
      clearTimeout(challengeFallback);
      try { ws.close(); } catch {}
      if (value instanceof Error) reject(value);
      else resolvePromise(value);
    }

    function sendConnect(challengeNonce) {
      state = "connecting";
      clearTimeout(challengeFallback);
      connectId = randomUUID();

      const clientId = "gateway-client";
      const clientMode = "backend";
      const gatewayRole = "operator";
      const scopes = ["operator.read", "operator.write"];
      const nonce = challengeNonce || randomUUID();
      const platform = process.platform === "darwin" ? "darwin" : "linux";

      const params = {
        minProtocol: 3,
        maxProtocol: 3,
        client: {
          id: clientId,
          version: VERSION,
          platform,
          mode: clientMode,
          instanceId: randomUUID(),
        },
        role: gatewayRole,
        scopes,
        auth: { token: gatewayToken },
        caps: [],
      };

      const device = readDeviceIdentity();
      if (device.deviceId && device.privateKeyPem && device.publicKeyPem) {
        const deviceParams = buildDeviceParams(
          device.deviceId,
          device.privateKeyPem,
          device.publicKeyPem,
          clientId,
          clientMode,
          gatewayRole,
          scopes,
          gatewayToken,
          nonce,
        );
        if (deviceParams) params.device = deviceParams;
      }

      ws.send(JSON.stringify({ type: "req", id: connectId, method: "connect", params }));
    }

    ws.addEventListener("error", (event) => {
      finish(new Error(`Gateway WS error at ${wsUrl}: ${event.message || "connection failed"}`));
    });

    ws.addEventListener("message", (event) => {
      let msg;
      try {
        msg = JSON.parse(typeof event.data === "string" ? event.data : event.data.toString());
      } catch {
        return;
      }

      if (state === "waiting_challenge" && msg.type === "event" && msg.event === "connect.challenge") {
        sendConnect(msg.payload?.nonce || null);
        return;
      }

      if (state === "connecting" && msg.type === "res" && msg.id === connectId) {
        if (!msg.ok) {
          finish(new Error(`Gateway connect failed: ${JSON.stringify(msg.error || {})}`));
          return;
        }
        state = "requesting";
        reqId = randomUUID();
        ws.send(JSON.stringify({
          type: "req",
          id: reqId,
          method: "agent",
          params: {
            message,
            sessionKey,
            agentId,
            idempotencyKey: randomUUID(),
            deliver: false,
          },
        }));
        return;
      }

      if (state === "requesting" && msg.type === "res" && msg.id === reqId) {
        const payload = msg.payload || {};
        if (payload.status === "accepted") return;
        if (!msg.ok) {
          finish(new Error(`Agent error: ${JSON.stringify(msg.error || {})}`));
          return;
        }
        state = "done";
        writeRuntimeState("running", { gateway_connected: true, last_gateway_success_at: new Date().toISOString() });
        finish(extractText(payload));
      }
    });
  });
}

function parseReply(raw) {
  const lines = String(raw || "").split("\n").map((line) => line.trim()).filter(Boolean);
  const closeLine = lines.find((line) => line.toUpperCase().startsWith("CLAWROOM_CLOSE:"));
  if (closeLine) {
    return { close: true, summary: closeLine.slice(closeLine.indexOf(":") + 1).trim() };
  }
  if (process.env.CLAWROOM_ALLOW_LEGACY_CLOSE === "true") {
    const legacy = lines.find((line) => line.toUpperCase().startsWith("CLOSE:"));
    if (legacy) return { close: true, summary: legacy.slice(legacy.indexOf(":") + 1).trim() };
  }
  const replyLine = lines.find((line) => line.toUpperCase().startsWith("REPLY:"));
  if (replyLine) {
    return { close: false, text: replyLine.slice(replyLine.indexOf(":") + 1).trim() };
  }
  return { close: false, text: lines[0] || String(raw || "").trim() };
}

function openingPrompt() {
  return [
    "You are acting for your owner in a private two-agent coordination room.",
    "",
    `Owner context: ${ownerCtx}`,
    `Goal: ${goal}`,
    "",
    "Start the conversation with one concrete proposal or the most useful context.",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    "",
    "Return exactly one line:",
    "REPLY: <short message under 30 words>",
  ].join("\n");
}

function replyPrompt(otherRole, text, firstTurn) {
  return [
    firstTurn ? "You are acting for your owner in a private two-agent coordination room." : "",
    firstTurn ? `Owner context: ${ownerCtx}` : "",
    firstTurn ? `Goal: ${goal}` : "",
    firstTurn ? "" : "",
    `The other agent (${otherRole}) says: ${JSON.stringify(text)}`,
    "",
    "Reply with one concise message that moves toward the goal.",
    "If the agreement is clear and ready to report to your owner, close instead.",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    "",
    "Return exactly one line, choosing one:",
    "REPLY: <short message under 30 words>",
    "CLAWROOM_CLOSE: <one sentence owner-ready summary>",
  ].filter(Boolean).join("\n");
}

function resolveTelegramConfig() {
  const botToken = (process.env.TG_BOT_TOKEN || process.env.TELEGRAM_BOT_TOKEN || "").trim();
  const cfg = readJson(openClawPath("openclaw.json"));
  const telegram = cfg?.channels?.telegram || {};
  const chatId = explicitTelegramChatId || String(telegram.allowFrom?.[0] || "").trim();
  return {
    botToken: botToken || telegram.botToken || "",
    chatId,
  };
}

async function telegramNotify(text) {
  const { botToken, chatId } = resolveTelegramConfig();
  if (!botToken || !chatId) {
    log("notify skipped: missing Telegram bot token or chat_id");
    return false;
  }
  const response = await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Telegram API ${response.status}: ${await response.text()}`);
  log(`Telegram delivered to chat_id=...${String(chatId).slice(-4)}`);
  return true;
}

async function notifyOwnerOnce(key, summary) {
  if (notifyKind === "none") return;
  bridgeState.notified ||= {};
  if (bridgeState.notified[key]) return;
  const text = `ClawRoom done\n\n${summary}`;
  if (notifyKind !== "telegram") {
    log(`notify skipped: unsupported notify kind ${notifyKind}`);
    return;
  }
  await telegramNotify(text);
  bridgeState.notified[key] = new Date().toISOString();
  persistState();
}

function preflight() {
  mkdirSync(stateDir, { recursive: true });
  writeJsonAtomic(join(stateDir, ".write-test.json"), { ok: true, ts: Date.now() });
  if (!globalThis.WebSocket) throw new Error("Node built-in WebSocket is unavailable. Use Node 22+.");
  if (!resolveGatewayToken()) throw new Error("OpenClaw gateway token is missing.");
  if (agentId === "main" && process.env.CLAWROOM_ALLOW_MAIN_AGENT !== "true") {
    log("warning: agent-id is main. Dedicated agent-id clawroom-relay is recommended.");
  }
  if (!existsSync(runtimeStatePath)) writeRuntimeState("starting", { preflight: "ok" });
}

function setCursor(id) {
  bridgeState.cursor = Math.max(Number(bridgeState.cursor ?? -1), Number(id));
  persistState();
}

async function handlePeerClose(message) {
  const summary = message.text || "The counterpart closed the room.";
  log(`Peer close observed (id=${message.id}): ${summary}`);
  setCursor(message.id);
  await notifyOwnerOnce(`peer-close:${sha(summary)}`, summary).catch((error) => {
    log(`owner notify failed: ${error.message}`);
  });
  const key = idempotencyKey("close", threadId, role, "ack", message.id, sha(summary));
  const result = await closeThread(summary, key);
  log(`Close acknowledged: ${JSON.stringify({ closed: result?.closed, status: result?._status })}`);
  await maybeHeartbeat("stopped", true, { stop_reason: "peer_close" });
}

async function sendOpeningIfNeeded() {
  if (role !== "host") return;
  const all = await getMessages(-1, 0);
  if (all.length > 0) return;

  log("Thread empty; asking OpenClaw for opening message");
  const reply = await gatewayCall(openingPrompt());
  const parsed = parseReply(reply);
  const text = parsed.close ? `I am ready to coordinate this: ${goal}` : parsed.text;

  const recheck = await getMessages(-1, 0);
  if (recheck.length > 0) {
    log("Opening skipped; peer spoke while opening was generating");
    return;
  }

  const key = idempotencyKey("open", threadId, role, sha(text));
  const result = await postMessage(text, key);
  if (result?.id != null) {
    setCursor(result.id);
    log(`Opening posted (id=${result.id})`);
  } else {
    log(`Opening post failed: ${JSON.stringify(result)}`);
  }
}

async function run() {
  preflight();
  writeRuntimeState("starting", { preflight: "ok" });
  await maybeHeartbeat("starting", true, { preflight: "ok" });

  log(`Started ${VERSION}. Relay=${relayBase}`);
  log(`Agent=${agentId} SessionKey=${sessionKey} Gateway=${resolveGatewayUrl()}`);

  const otherRole = role === "host" ? "guest" : "host";
  let includeContext = true;

  await sendOpeningIfNeeded();

  while (true) {
    await maybeHeartbeat("running");

    const threadState = await getThreadState().catch((error) => {
      log(`state fetch failed: ${error.message}`);
      return null;
    });
    if (threadState?.closed) {
      const summary = threadState.summary || "The room closed.";
      log(`Thread closed. Summary: ${summary}`);
      await notifyOwnerOnce(`thread-closed:${sha(summary)}`, summary).catch((error) => {
        log(`owner notify failed: ${error.message}`);
      });
      await maybeHeartbeat("stopped", true, { stop_reason: "thread_closed" });
      break;
    }

    const messages = await getMessages(bridgeState.cursor ?? -1, POLL_WAIT_SECONDS);
    if (!messages.length) continue;

    for (const message of messages) {
      if (Number(message.id) <= Number(bridgeState.cursor ?? -1)) continue;

      if (message.from === role) {
        setCursor(message.id);
        continue;
      }

      if (message.kind === "close") {
        await handlePeerClose(message);
        return;
      }

      if (message.from !== otherRole) {
        setCursor(message.id);
        continue;
      }

      log(`New from ${otherRole} (id=${message.id}): ${message.text}`);
      let reply;
      try {
        reply = await gatewayCall(replyPrompt(otherRole, message.text, includeContext));
        includeContext = false;
      } catch (error) {
        log(`Gateway error: ${error.message}`);
        await maybeHeartbeat("error", true, { last_error: error.message });
        continue;
      }

      const parsed = parseReply(reply);
      if (parsed.close) {
        const key = idempotencyKey("close", threadId, role, message.id, sha(parsed.summary));
        const result = await closeThread(parsed.summary, key);
        if (result?.id != null) setCursor(result.id);
        log(`Closed by ${role}: ${parsed.summary}`);
        await notifyOwnerOnce(`own-close:${sha(parsed.summary)}`, parsed.summary).catch((error) => {
          log(`owner notify failed: ${error.message}`);
        });
        await maybeHeartbeat("stopped", true, { stop_reason: "own_close" });
        return;
      }

      const text = parsed.text;
      const key = idempotencyKey("reply", threadId, role, message.id, sha(text));
      const result = await postMessage(text, key);
      if (result?.id != null) {
        setCursor(result.id);
        log(`Posted (id=${result.id}): ${text}`);
      } else if (result?.error === "not_your_turn") {
        log(`not_your_turn at last_id=${result.last_id}; refetching`);
        setCursor(message.id);
      } else if (result?.error === "thread is closed") {
        log("Thread closed by other side");
        await maybeHeartbeat("stopped", true, { stop_reason: "thread_closed" });
        return;
      } else {
        log(`Post failed: ${JSON.stringify(result)}`);
        await maybeHeartbeat("error", true, { last_error: JSON.stringify(result) });
      }
    }
  }
}

run().catch((error) => {
  try {
    writeRuntimeState("failed", { last_error: error.message });
  } catch {}
  console.error(error);
  process.exit(1);
});

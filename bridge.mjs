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
  chmodSync,
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

const VERSION = "v3.1.1";
const FEATURES = [
  "telegram-ask-owner-bindings",
  "telegram-force-reply",
  "openclaw-state-dir-fallback",
];
const DEFAULT_RELAY = "https://clawroom-v3-relay.heyzgj.workers.dev";
const POLL_WAIT_SECONDS = 20;
const HEARTBEAT_MS = 15_000;
const AGENT_TIMEOUT = Math.max(30_000, Number(process.env.CLAWROOM_AGENT_TIMEOUT_MS || 240_000) || 240_000);
const CHALLENGE_WAIT = 5_000;
const OWNER_REPLY_TTL_SECONDS = 30 * 60;
const FATAL_RELAY_STATUSES = new Set([401, 403, 404, 410]);
const QUOTA_BACKOFF_MS = 60_000;
const RELAY_ERROR_BACKOFF_MS = 10_000;

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
const minMessages = Math.max(0, Number(args["min-messages"] || process.env.CLAWROOM_MIN_MESSAGES || 0) || 0);
const agentId = String(args["agent-id"] || process.env.CLAWROOM_AGENT_ID || "clawroom-relay");
const sessionKey = String(args["session-key"] || `agent:${agentId}:clawroom:${threadId}:${role}`);
const openClawStateDir = resolve(String(process.env.OPENCLAW_STATE_DIR || process.env.CLAWDBOT_STATE_DIR || join(homedir(), ".openclaw")));
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
  try {
    chmodSync(tmp, 0o600);
  } catch {
    // Best-effort hardening; some filesystems reject chmod.
  }
  renameSync(tmp, path);
}

function sha(value) {
  return createHash("sha256").update(String(value)).digest("hex").slice(0, 24);
}

function chatIdHash(chatId) {
  return createHash("sha256").update(String(chatId)).digest("hex").slice(0, 16);
}

function chatIdSuffix(chatId) {
  const value = String(chatId || "");
  return value ? value.slice(-4) : "";
}

function idempotencyKey(...parts) {
  return parts.map((part) => String(part).replace(/[^a-zA-Z0-9_.:-]/g, "_")).join(":").slice(0, 180);
}

async function delay(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

function isFatalRelayError(error) {
  return FATAL_RELAY_STATUSES.has(Number(error?.status));
}

function relayBackoffMs(error) {
  const status = Number(error?.status);
  const message = String(error?.message || "");
  if (status === 429 || message.includes("Exceeded allowed volume")) return QUOTA_BACKOFF_MS;
  if (status >= 500) return RELAY_ERROR_BACKOFF_MS;
  return 5_000;
}

function negotiationMessageCount(rows) {
  return rows.filter((row) => row?.kind === "message").length;
}

function parseMandates(text) {
  const mandates = {};
  for (const line of String(text || "").split("\n")) {
    const match = line.match(/^\s*MANDATE\s*:\s*budget_ceiling_jpy\s*=\s*([0-9][0-9,]*)\s*$/i);
    if (match) mandates.budget_ceiling_jpy = Number(match[1].replace(/,/g, ""));
  }
  return mandates;
}

function parseJpyAmounts(text) {
  const source = String(text || "");
  const amounts = [];
  const patterns = [
    /¥\s*([0-9][0-9,]*(?:\.\d+)?)\s*([kK])?/g,
    /([0-9][0-9,]*(?:\.\d+)?)\s*([kK])?\s*(?:JPY|jpy|yen|円|日元)/g,
  ];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const value = Number(String(match[1] || "").replace(/,/g, ""));
      if (!Number.isFinite(value)) continue;
      amounts.push(Math.round(value * (match[2] ? 1000 : 1)));
    }
  }
  return amounts;
}

function maxJpyAmount(text) {
  const amounts = parseJpyAmounts(text);
  return amounts.length ? Math.max(...amounts) : null;
}

function ownerReplyApprovesExcess(text) {
  if (obviousRejection(text)) return false;
  return /\b(yes|approve|approved|authorize|authorized|ok|okay)\b/i.test(String(text || "")) ||
    /同意|批准|授权|可以|允许|通过/.test(String(text || ""));
}

function obviousRejection(text) {
  return /\b(cannot|can't|do not|don't|not above|reject|rejected|decline|ceiling|above the ceiling|over budget)\b/i.test(String(text || "")) ||
    /不能|不接受|拒绝|不超过|上限|超预算|超过预算/.test(String(text || ""));
}

const mandates = parseMandates(ownerCtx);

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
    bridge_features: FEATURES,
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

  const attempts = Math.max(1, Number(options.retries || process.env.CLAWROOM_RELAY_RETRIES || 4) || 4);
  const allowStatuses = new Set((options.allowStatuses || []).map((status) => Number(status)));
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
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
      if (!response.ok && !allowStatuses.has(response.status)) {
        const error = new Error(`relay ${response.status} for ${path}: ${JSON.stringify(data).slice(0, 300)}`);
        error.status = response.status;
        error.data = data;
        error.retriable = response.status >= 500 || response.status === 429;
        if (attempt < attempts && error.retriable) {
          log(`relay ${response.status} for ${path}; retry ${attempt}/${attempts}`);
          await delay(250 * attempt * attempt);
          continue;
        }
        throw error;
      }
      if (response.status >= 500 && attempt < attempts) {
        log(`relay ${response.status} for ${path}; retry ${attempt}/${attempts}`);
        await delay(250 * attempt * attempt);
        continue;
      }
      return data;
    } catch (error) {
      lastError = error;
      if (error.status && !error.retriable) throw error;
      if (attempt >= attempts) break;
      log(`relay fetch failed for ${path}: ${error.message}; retry ${attempt}/${attempts}`);
      await delay(250 * attempt * attempt);
    }
  }
  throw lastError || new Error(`relay fetch failed for ${path}`);
}

async function getMessages(after = -1, wait = 0) {
  const data = await relayFetch(`/threads/${threadId}/messages`, {
    query: { after, wait },
    timeoutMs: (wait + 10) * 1000,
  }).catch(async (error) => {
    if (isFatalRelayError(error)) throw error;
    const backoffMs = relayBackoffMs(error);
    log(`relay poll failed: ${error.message}; backoff_ms=${backoffMs}`);
    await delay(backoffMs);
    return [];
  });
  return Array.isArray(data) ? data : [];
}

async function postMessage(text, key) {
  return relayFetch(`/threads/${threadId}/messages`, {
    method: "POST",
    body: { text },
    idempotencyKey: key,
    allowStatuses: [400, 409],
  });
}

async function postAskOwner(text, key) {
  return relayFetch(`/threads/${threadId}/ask-owner`, {
    method: "POST",
    body: { text, ttl_seconds: OWNER_REPLY_TTL_SECONDS },
    idempotencyKey: key,
    allowStatuses: [400, 409],
  });
}

async function closeThread(summary, key) {
  return relayFetch(`/threads/${threadId}/close`, {
    method: "POST",
    body: { summary },
    idempotencyKey: key,
    allowStatuses: [400, 409],
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

function extractChatMessageText(message) {
  const content = message?.content;
  if (Array.isArray(content)) {
    const text = content
      .map((part) => typeof part === "string" ? part : String(part?.text || ""))
      .join("")
      .trim();
    if (text) return text;
  }
  if (typeof content === "string" && content.trim()) return content.trim();
  return String(message?.text || "").trim();
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
    let runId = "";
    let lastAssistantText = "";

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

      if ((state === "requesting" || state === "accepted") && msg.type === "event" && runId) {
        const payload = msg.payload || {};
        if (msg.event === "agent" && payload.runId === runId && payload.stream === "assistant") {
          const text = String(payload.data?.text || "").trim();
          if (text) lastAssistantText = text;
          return;
        }
        if (msg.event === "agent" && payload.runId === runId && payload.stream === "lifecycle" && payload.data?.phase === "end") {
          if (lastAssistantText) {
            state = "done";
            writeRuntimeState("running", { gateway_connected: true, last_gateway_success_at: new Date().toISOString() });
            finish(lastAssistantText);
          }
          return;
        }
        if (msg.event === "chat" && payload.runId === runId && payload.state === "final") {
          const text = extractChatMessageText(payload.message);
          if (text) {
            state = "done";
            writeRuntimeState("running", { gateway_connected: true, last_gateway_success_at: new Date().toISOString() });
            finish(text);
          }
          return;
        }
      }

      if ((state === "requesting" || state === "accepted") && msg.type === "res" && msg.id === reqId) {
        const payload = msg.payload || {};
        if (payload.status === "accepted") {
          state = "accepted";
          runId = String(payload.runId || "");
          if (runId) log(`OpenClaw accepted run ${runId.slice(0, 8)}`);
          return;
        }
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
  const closeMatch = lines.map((line) => line.match(/^\s*CLAWROOM[\s_]*CLOSE\s*[:：]\s*(.*)$/i)).find(Boolean);
  if (closeMatch) {
    return { close: true, summary: closeMatch[1].trim(), marker_inferred: false };
  }
  if (process.env.CLAWROOM_ALLOW_LEGACY_CLOSE === "true") {
    const legacyMatch = lines.map((line) => line.match(/^\s*CLOSE\s*[:：]\s*(.*)$/i)).find(Boolean);
    if (legacyMatch) return { close: true, summary: legacyMatch[1].trim(), marker_inferred: false };
  }
  const askOwnerMatch = lines.map((line) => line.match(/^\s*ASK[\s_]*OWNER\s*[:：]\s*(.*)$/i)).find(Boolean);
  if (askOwnerMatch) {
    return { ask_owner: true, question: askOwnerMatch[1].trim(), marker_inferred: false };
  }
  const replyMatch = lines.map((line) => line.match(/^\s*REPLY\s*[:：]\s*(.*)$/i)).find(Boolean);
  if (replyMatch) {
    return { close: false, text: replyMatch[1].trim(), marker_inferred: false };
  }

  const text = lines[0] || String(raw || "").trim();
  if (text) {
    bridgeState.unmatched_marker_turns = Number(bridgeState.unmatched_marker_turns || 0) + 1;
    bridgeState.last_marker_inferred_at = new Date().toISOString();
    if (/\b(owner|approval|permission|authorize|authorized|boss)\b/i.test(text) || /授权|批准|请示|老板|确认/.test(text)) {
      bridgeState.last_soft_ask_owner_candidate_at = bridgeState.last_marker_inferred_at;
      bridgeState.soft_ask_owner_candidates = Number(bridgeState.soft_ask_owner_candidates || 0) + 1;
    }
    persistState();
    writeRuntimeState("running", {
      unmatched_marker_turns: bridgeState.unmatched_marker_turns,
      last_marker_inferred_at: bridgeState.last_marker_inferred_at,
      soft_ask_owner_candidates: bridgeState.soft_ask_owner_candidates || 0,
    });
    log(`marker inferred: no REPLY/CLAWROOM_CLOSE/ASK_OWNER marker in agent output; total=${bridgeState.unmatched_marker_turns}`);
  }
  return { close: false, text, marker_inferred: Boolean(text) };
}

function openingPrompt() {
  return [
    "You are acting for your owner in a private two-agent coordination room.",
    "",
    `Owner context: ${ownerCtx}`,
    `Goal: ${goal}`,
    mandates.budget_ceiling_jpy ? `Mandate: do not accept or propose above ${mandates.budget_ceiling_jpy} JPY unless the owner explicitly approves.` : "",
    minMessages ? `Minimum negotiation messages before close: ${minMessages}.` : "",
    "",
    "Start the conversation with one concrete proposal or the most useful context.",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    "",
    "Return exactly one line:",
    "REPLY: <short message under 30 words>",
  ].join("\n");
}

function replyPrompt(otherRole, text, firstTurn, messageCount) {
  const canClose = !minMessages || messageCount >= minMessages;
  return [
    firstTurn ? "You are acting for your owner in a private two-agent coordination room." : "",
    firstTurn ? `Owner context: ${ownerCtx}` : "",
    firstTurn ? `Goal: ${goal}` : "",
    mandates.budget_ceiling_jpy ? `Mandate: do not accept or propose above ${mandates.budget_ceiling_jpy} JPY unless the owner explicitly approves. Use ASK_OWNER before exceeding it.` : "",
    minMessages ? `Negotiation messages so far, including the latest received message: ${messageCount}.` : "",
    minMessages ? `Minimum negotiation messages before close: ${minMessages}.` : "",
    minMessages && !canClose ? "You MUST continue with REPLY. Do not close yet." : "",
    firstTurn ? "" : "",
    `The other agent (${otherRole}) says: ${JSON.stringify(text)}`,
    "",
    "Reply with one concise message that moves toward the goal.",
    canClose ? "If the agreement is clear and ready to report to your owner, close instead." : "Do not close yet; ask a useful question, make a counteroffer, or confirm one missing detail.",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    "",
    "Return exactly one line, choosing one:",
    "REPLY: <short message under 30 words>",
    "ASK_OWNER: <short authorization question for your owner>",
    canClose ? "CLAWROOM_CLOSE: <one sentence owner-ready summary>" : "",
  ].filter(Boolean).join("\n");
}

function earlyClosePrompt(otherRole, text, summary, messageCount) {
  return [
    "You attempted to close the room before the minimum negotiation length.",
    `Negotiation messages so far: ${messageCount}.`,
    `Minimum negotiation messages before close: ${minMessages}.`,
    `The other agent (${otherRole}) last said: ${JSON.stringify(text)}`,
    `Your premature close summary was: ${JSON.stringify(summary)}`,
    "",
    "Continue the negotiation with one substantive question, counteroffer, or missing-detail confirmation.",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    "",
    "Return exactly one line:",
    "REPLY: <short message under 30 words>",
  ].join("\n");
}

function ownerReplyPrompt(waiting, ownerReplyText, messageCount) {
  const canClose = !minMessages || messageCount >= minMessages;
  return [
    "Your owner has replied to your authorization question.",
    `Owner context: ${ownerCtx}`,
    `Goal: ${goal}`,
    mandates.budget_ceiling_jpy ? `Mandate: do not accept or propose above ${mandates.budget_ceiling_jpy} JPY unless the owner explicitly approves.` : "",
    `Original counterpart message: ${JSON.stringify(waiting.peer_text || "")}`,
    waiting.attempted_close_summary ? `Your blocked close summary: ${JSON.stringify(waiting.attempted_close_summary)}` : "",
    waiting.blocked_reply_text ? `Your blocked reply: ${JSON.stringify(waiting.blocked_reply_text)}` : "",
    `OWNER_REPLY: ${ownerReplyText}`,
    minMessages ? `Negotiation messages so far: ${messageCount}. Minimum before close: ${minMessages}.` : "",
    "",
    "Continue the negotiation according to the owner reply.",
    "Use natural language. Do not mention APIs, tokens, relays, sessions, or internal mechanics.",
    "",
    "Return exactly one line, choosing one:",
    "REPLY: <short message under 30 words>",
    "ASK_OWNER: <short authorization question for your owner>",
    canClose ? "CLAWROOM_CLOSE: <one sentence owner-ready summary>" : "",
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

async function telegramNotify(text, options = {}) {
  const { botToken, chatId } = resolveTelegramConfig();
  if (!botToken || !chatId) {
    log("notify skipped: missing Telegram bot token or chat_id");
    return { ok: false, message_id: null, chat_id: null };
  }
  const requestBody = { chat_id: chatId, text };
  if (options.forceReply) {
    requestBody.reply_markup = {
      force_reply: true,
      input_field_placeholder: "Approve, reject, or give instructions",
    };
  }
  const response = await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(requestBody),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Telegram API ${response.status}: ${await response.text()}`);
  const body = await response.json().catch(() => ({}));
  const resolvedChatId = body?.result?.chat?.id != null ? String(body.result.chat.id) : String(chatId);
  log(`Telegram delivered to chat_id=...${chatIdSuffix(resolvedChatId)}`);
  return { ok: true, message_id: body?.result?.message_id || null, chat_id: resolvedChatId };
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

function ownerReplyEndpoint() {
  return `${relayBase}/threads/${threadId}/owner-reply`;
}

function askOwnerBindingPath(chatId, messageId) {
  const dir = join(openClawStateDir, "clawroom-v3", "ask-owner-bindings");
  const safeMessageId = String(messageId || "").replace(/[^0-9a-zA-Z_.:-]/g, "_");
  return join(dir, `${chatIdHash(chatId)}.${safeMessageId}.json`);
}

function writeAskOwnerTelegramBinding(question, delivery) {
  if (!delivery?.ok || !delivery.message_id || !delivery.chat_id) {
    return { ok: false, reason: "missing_telegram_delivery_fields" };
  }
  const messageId = delivery.message_id;
  const chatId = String(delivery.chat_id);
  const path = askOwnerBindingPath(chatId, messageId);
  const binding = {
    version: 1,
    source: "clawroom_bridge",
    created_at: new Date().toISOString(),
    expires_at: question.expires_at || null,
    relay: relayBase,
    thread_id: threadId,
    role,
    question_id: question.question_id || "",
    ask_event_id: question.id ?? null,
    owner_reply_token: question.owner_reply_token || "",
    telegram: {
      chat_id_hash: chatIdHash(chatId),
      chat_id_suffix: chatIdSuffix(chatId),
      message_id: messageId,
    },
  };
  writeJsonAtomic(path, binding);
  return {
    ok: true,
    path,
    chat_id_hash: binding.telegram.chat_id_hash,
    chat_id_suffix: binding.telegram.chat_id_suffix,
    message_id: messageId,
  };
}

function publicWaitingOwner(waiting = bridgeState.waiting_owner || null) {
  if (!waiting) return null;
  const { owner_reply_token, ...rest } = waiting;
  return {
    ...rest,
    owner_reply_token: owner_reply_token ? "REDACTED" : "",
  };
}

function mandateViolation(text, action) {
  const ceiling = Number(mandates.budget_ceiling_jpy || 0);
  if (!ceiling || bridgeState.mandate_approvals?.budget_ceiling_jpy) return null;
  const amount = maxJpyAmount(text);
  if (!amount || amount <= ceiling) return null;
  if (action === "reply" && obviousRejection(text)) return null;
  return {
    kind: "budget_ceiling_jpy",
    ceiling,
    amount,
    action,
  };
}

function ownerQuestionText(parsed, violation) {
  if (parsed.ask_owner) return parsed.question;
  if (violation?.kind === "budget_ceiling_jpy") {
    return [
      `The proposed ${violation.action} mentions ${violation.amount} JPY, above your ${violation.ceiling} JPY ceiling.`,
      "Approve this exception, reject it, or give a counter-instruction.",
    ].join(" ");
  }
  return "Authorization needed before continuing. Please approve, reject, or give a counter-instruction.";
}

async function notifyOwnerQuestion(question, questionText) {
  if (notifyKind !== "telegram") {
    log(`ASK_OWNER notify skipped: unsupported notify kind ${notifyKind}`);
    return null;
  }
  const endpoint = ownerReplyEndpoint();
  const text = [
    "ClawRoom needs your decision",
    `Room: ${threadId}`,
    `Role: ${role}`,
    "",
    questionText,
    "",
    "Reply directly to this Telegram message.",
    "Say approve, reject, or give a counter-instruction. Other chats will keep going to your normal OpenClaw agent.",
    "",
    "Examples: approve; reject; do not go above 65000 JPY; offer extra deliverables instead.",
    process.env.CLAWROOM_DEBUG_OWNER_REPLY === "true" ? "" : null,
    process.env.CLAWROOM_DEBUG_OWNER_REPLY === "true" ? "Debug fallback:" : null,
    process.env.CLAWROOM_DEBUG_OWNER_REPLY === "true" ? `Endpoint: ${endpoint}` : null,
  ].filter((line) => line !== null).join("\n");
  const delivered = await telegramNotify(text, { forceReply: true });
  return {
    ...delivered,
    owner_reply_endpoint: endpoint,
  };
}

async function enterWaitingOwner(parsed, context = {}) {
  const violation = context.violation || null;
  const questionText = ownerQuestionText(parsed, violation);
  const key = idempotencyKey(
    "ask-owner",
    threadId,
    role,
    context.peer_message_id || "",
    sha(questionText),
  );
  const question = await postAskOwner(questionText, key);
  if (question?.id == null || !question.question_id || !question.owner_reply_token) {
    log(`ASK_OWNER post failed: ${JSON.stringify(question)}`);
    await maybeHeartbeat("error", true, { last_error: JSON.stringify(question) });
    return false;
  }

  setCursor(question.id);
  const waiting = {
    question_id: question.question_id,
    owner_reply_token: question.owner_reply_token,
    ask_event_id: question.id,
    asked_at: new Date().toISOString(),
    expires_at: question.expires_at || null,
    peer_message_id: context.peer_message_id ?? null,
    peer_text: context.peer_text || "",
    attempted_close_summary: context.attempted_close_summary || "",
    blocked_reply_text: context.blocked_reply_text || "",
    mandate_violation: violation,
  };
  bridgeState.waiting_owner = waiting;
  persistState();

  try {
    const delivery = await notifyOwnerQuestion(question, questionText);
    let binding = { ok: false, reason: "not_attempted" };
    try {
      binding = writeAskOwnerTelegramBinding(question, delivery);
      if (binding.ok) {
        log(`ASK_OWNER Telegram binding written message_id=${binding.message_id} chat_id=...${binding.chat_id_suffix}`);
      } else {
        log(`ASK_OWNER Telegram binding skipped: ${binding.reason}`);
      }
    } catch (error) {
      binding = { ok: false, reason: "write_failed" };
      log(`ASK_OWNER Telegram binding write failed: ${error.message}`);
    }
    bridgeState.waiting_owner = {
      ...bridgeState.waiting_owner,
      telegram_message_id: delivery?.message_id || null,
      telegram_chat_hash: binding.ok ? binding.chat_id_hash : null,
      telegram_binding_written: Boolean(binding.ok),
      owner_reply_endpoint: delivery?.owner_reply_endpoint || null,
      notified_at: new Date().toISOString(),
    };
    persistState();
  } catch (error) {
    log(`ASK_OWNER notify failed: ${error.message}`);
    await maybeHeartbeat("waiting_owner", true, {
      waiting_owner: publicWaitingOwner(waiting),
      notify_error: error.message,
    });
    return true;
  }

  log(`Waiting for owner reply question_id=${question.question_id}`);
  await maybeHeartbeat("waiting_owner", true, { waiting_owner: publicWaitingOwner() });
  return true;
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

async function handleParsedReply(parsed, context = {}) {
  if (parsed.ask_owner) {
    await enterWaitingOwner(parsed, context);
    return true;
  }

  const textForGuard = parsed.close ? parsed.summary : parsed.text;
  const violation = mandateViolation(textForGuard, parsed.close ? "close" : "reply");
  if (violation) {
    await enterWaitingOwner(parsed, {
      ...context,
      violation,
      attempted_close_summary: parsed.close ? parsed.summary : "",
      blocked_reply_text: parsed.close ? "" : parsed.text,
    });
    return true;
  }

  if (parsed.close) {
    const key = idempotencyKey("close", threadId, role, context.peer_message_id || "", sha(parsed.summary));
    const result = await closeThread(parsed.summary, key);
    if (result?.id != null) setCursor(result.id);
    log(`Closed by ${role}: ${parsed.summary}`);
    await notifyOwnerOnce(`own-close:${sha(parsed.summary)}`, parsed.summary).catch((error) => {
      log(`owner notify failed: ${error.message}`);
    });
    await maybeHeartbeat("stopped", true, { stop_reason: "own_close" });
    return false;
  }

  const text = parsed.text || "";
  const key = idempotencyKey("reply", threadId, role, context.peer_message_id || "", sha(text));
  const result = await postMessage(text, key);
  if (result?.id != null) {
    setCursor(result.id);
    log(`Posted (id=${result.id}): ${text}`);
    return true;
  }
  if (result?.error === "not_your_turn") {
    log(`not_your_turn at last_id=${result.last_id}; refetching`);
    if (context.peer_message_id != null) setCursor(context.peer_message_id);
    return true;
  }
  if (result?.error === "thread is closed") {
    log("Thread closed by other side");
    await maybeHeartbeat("stopped", true, { stop_reason: "thread_closed" });
    return false;
  }
  log(`Post failed: ${JSON.stringify(result)}`);
  await maybeHeartbeat("error", true, { last_error: JSON.stringify(result) });
  return true;
}

async function handleWaitingOwner() {
  const waiting = bridgeState.waiting_owner;
  if (!waiting?.question_id) return false;

  if (ownerWaitExpired(waiting)) {
    return await handleOwnerWaitExpired(waiting);
  }

  await maybeHeartbeat("waiting_owner", false, { waiting_owner: publicWaitingOwner(waiting) });
  const messages = await getMessages(bridgeState.cursor ?? -1, POLL_WAIT_SECONDS);
  if (!messages.length) return true;

  for (const message of messages) {
    if (Number(message.id) <= Number(bridgeState.cursor ?? -1)) continue;

    if (message.kind === "owner_reply" && message.from === role && message.question_id === waiting.question_id) {
      setCursor(message.id);
      log(`Owner reply observed for question_id=${waiting.question_id}`);
      if (ownerReplyApprovesExcess(message.text)) {
        bridgeState.mandate_approvals ||= {};
        bridgeState.mandate_approvals.budget_ceiling_jpy = {
          question_id: waiting.question_id,
          approved_at: new Date().toISOString(),
        };
      }
      delete bridgeState.waiting_owner;
      persistState();

      const allMessages = await getMessages(-1, 0);
      const messageCount = negotiationMessageCount(allMessages);
      let reply;
      try {
        reply = await gatewayCall(ownerReplyPrompt(waiting, message.text, messageCount));
      } catch (error) {
        log(`Gateway error after owner reply: ${error.message}`);
        await maybeHeartbeat("error", true, { last_error: error.message });
        return true;
      }
      const parsed = parseReply(reply);
      return await handleParsedReply(parsed, {
        peer_message_id: waiting.peer_message_id,
        peer_text: waiting.peer_text,
      });
    }

    if (message.kind === "close" && message.from !== role) {
      await handlePeerClose(message);
      return false;
    }

    setCursor(message.id);
  }
  return true;
}

function ownerWaitExpired(waiting) {
  const expiresAt = Number(waiting?.expires_at || 0);
  return Number.isFinite(expiresAt) && expiresAt > 0 && Date.now() > expiresAt;
}

async function handleOwnerWaitExpired(waiting) {
  const summary = `Owner authorization ${waiting.question_id} expired without a reply. Closing without approving the requested exception.`;
  const key = idempotencyKey("close", threadId, role, "owner-timeout", waiting.question_id, sha(summary));
  log(`Owner reply expired question_id=${waiting.question_id}; closing room`);

  const result = await closeThread(summary, key);
  if (result?.id != null) setCursor(result.id);

  delete bridgeState.waiting_owner;
  persistState();

  await notifyOwnerOnce(`owner-timeout:${waiting.question_id}`, [
    "ClawRoom authorization expired",
    `Room: ${threadId}`,
    "",
    "No reply was recorded before the question expired, so I closed without approving the exception.",
  ].join("\n")).catch((error) => {
    log(`owner timeout notify failed: ${error.message}`);
  });

  await maybeHeartbeat("stopped", true, {
    stop_reason: "owner_reply_timeout",
    close_result: {
      id: result?.id ?? null,
      error: result?.error || null,
      closed: result?.closed ?? null,
    },
  });
  return false;
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
    await maybeHeartbeat(bridgeState.waiting_owner?.question_id ? "waiting_owner" : "running", false, {
      mandates,
      mandate_approvals: bridgeState.mandate_approvals || {},
      waiting_owner: publicWaitingOwner(),
    });

    const threadState = await getThreadState().catch(async (error) => {
      if (isFatalRelayError(error)) throw error;
      const backoffMs = relayBackoffMs(error);
      log(`state fetch failed: ${error.message}; backoff_ms=${backoffMs}`);
      await delay(backoffMs);
      return null;
    });
    if (!threadState) continue;
    if (threadState?.closed) {
      const summary = threadState.summary || "The room closed.";
      log(`Thread closed. Summary: ${summary}`);
      await notifyOwnerOnce(`thread-closed:${sha(summary)}`, summary).catch((error) => {
        log(`owner notify failed: ${error.message}`);
      });
      await maybeHeartbeat("stopped", true, { stop_reason: "thread_closed" });
      break;
    }

    if (bridgeState.waiting_owner?.question_id) {
      const keepRunning = await handleWaitingOwner();
      if (!keepRunning) return;
      continue;
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

      if (message.kind === "ask_owner" || message.kind === "owner_reply") {
        setCursor(message.id);
        continue;
      }

      if (message.from !== otherRole) {
        setCursor(message.id);
        continue;
      }

      log(`New from ${otherRole} (id=${message.id}): ${message.text}`);
      let reply;
      const allMessages = await getMessages(-1, 0);
      const messageCount = negotiationMessageCount(allMessages);
      try {
        reply = await gatewayCall(replyPrompt(otherRole, message.text, includeContext, messageCount));
        includeContext = false;
      } catch (error) {
        log(`Gateway error: ${error.message}`);
        await maybeHeartbeat("error", true, { last_error: error.message });
        continue;
      }

      let parsed = parseReply(reply);
      if (parsed.close && minMessages && messageCount < minMessages) {
        bridgeState.early_close_suppressed = Number(bridgeState.early_close_suppressed || 0) + 1;
        persistState();
        await maybeHeartbeat("running", true, {
          early_close_suppressed: bridgeState.early_close_suppressed,
          message_count: messageCount,
          min_messages: minMessages,
        });
        log(`early close suppressed at message_count=${messageCount}; min_messages=${minMessages}`);
        try {
          parsed = parseReply(await gatewayCall(earlyClosePrompt(otherRole, message.text, parsed.summary, messageCount)));
        } catch (error) {
          log(`Gateway error after early-close suppression: ${error.message}`);
          await maybeHeartbeat("error", true, { last_error: error.message });
          continue;
        }
        if (parsed.close) {
          parsed = {
            close: false,
            text: "Before we close, let's confirm one more detail on scope, payment, usage rights, or approval.",
            marker_inferred: true,
          };
          log("early close fallback converted repeated close into REPLY");
        }
      }
      const keepRunning = await handleParsedReply(parsed, {
        peer_message_id: message.id,
        peer_text: message.text,
      });
      if (!keepRunning) {
        return;
      }
    }
  }
}

run().catch(async (error) => {
  try {
    writeRuntimeState("failed", { last_error: error.message });
  } catch {}
  try {
    await maybeHeartbeat("failed", true, { last_error: error.message });
  } catch (heartbeatError) {
    log(`failed heartbeat could not be sent: ${heartbeatError.message}`);
  }
  console.error(error);
  process.exit(1);
});

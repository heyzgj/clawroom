#!/usr/bin/env node
/**
 * Product-facing ClawRoom launcher wrapper.
 *
 * Default stdout is safe for owner chat: no bearer tokens, PIDs, logs, or
 * launcher internals. Machine details are written to local state instead.
 */

import { spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DEFAULT_RELAY = "https://clawroom-v3-relay.heyzgj.workers.dev";
const DEFAULT_FEATURES = "telegram-ask-owner-bindings";

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      out._.push(arg);
      continue;
    }
    const key = arg.slice(2);
    out[key] = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
  }
  return out;
}

function emit(payload, status = 0) {
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(status);
}

function usage(status = 2) {
  process.stderr.write([
    "Usage:",
    "  node clawroomctl.mjs create --topic TOPIC --goal GOAL --context OWNER_CONTEXT [--create-key KEY] [--telegram-chat-id CHAT_ID]",
    "  node clawroomctl.mjs join --invite PUBLIC_INVITE_URL --context OWNER_CONTEXT [--telegram-chat-id CHAT_ID]",
    "",
    "Add --debug to include local machine-state path and launcher result.",
  ].join("\n") + "\n");
  process.exit(status);
}

function fail(error, publicMessage, status = 1, extra = {}) {
  emit({ ok: false, error, public_message: publicMessage, ...extra }, status);
}

function parseJson(text) {
  try {
    return JSON.parse(text || "{}");
  } catch {
    return {};
  }
}

async function fetchJson(url, options = {}) {
  let lastError = null;
  const attempts = Number(options.attempts || 4);
  const headers = { ...(options.headers || {}) };
  const init = { ...options, headers };
  delete init.attempts;
  delete init.timeoutMs;
  if (init.body !== undefined && !headers["content-type"]) headers["content-type"] = "application/json";
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const response = await fetch(url, {
        ...init,
        signal: AbortSignal.timeout(Number(options.timeoutMs || 20_000)),
      });
      const text = await response.text();
      const body = parseJson(text);
      if (!response.ok) {
        const error = new Error(`HTTP ${response.status}`);
        error.status = response.status;
        error.body = body;
        throw error;
      }
      return body;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, 750 * attempt));
    }
  }
  throw lastError;
}

function stateDir(args) {
  const openClawStateDir = resolve(String(process.env.OPENCLAW_STATE_DIR || join(homedir(), ".openclaw")));
  const fallback = process.env.OPENCLAW_STATE_DIR
    ? join(openClawStateDir, "clawroom-v3")
    : join(homedir(), ".clawroom-v3");
  return resolve(String(args["state-dir"] || process.env.CLAWROOM_STATE_DIR || fallback));
}

function writeMachineState(args, threadId, role, data) {
  const dir = stateDir(args);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${threadId}-${role}.machine.json`);
  writeFileSync(path, `${JSON.stringify({ ...data, written_at: new Date().toISOString() }, null, 2)}\n`, "utf8");
  try {
    chmodSync(path, 0o600);
  } catch {}
  return path;
}

function debug(args, payload) {
  return args.debug ? payload : {};
}

function relay(args) {
  return String(args.relay || DEFAULT_RELAY).replace(/\/$/, "");
}

function createKey(args) {
  return String(args["create-key"] || process.env.CLAWROOM_CREATE_KEY || "").trim();
}

function goal(args, fallback = "") {
  return String(args.goal || args.topic || fallback || "Coordinate and return the agreed result.").trim();
}

function context(args) {
  return String(args.context || args["owner-context"] || "").trim();
}

function requireFile(path, error) {
  if (!existsSync(path)) {
    fail(error, "I could not start ClawRoom here because the runtime files are missing.");
  }
}

function launch(args, { threadId, token, role, goalText, contextText }) {
  const launcherPath = resolve(String(args.launcher || join(__dirname, "launcher.mjs")));
  const bridgePath = resolve(String(args.bridge || join(__dirname, "bridge.mjs")));
  requireFile(launcherPath, "launcher_not_found");
  requireFile(bridgePath, "bridge_not_found");

  const command = [
    launcherPath,
    "--thread", threadId,
    "--token", token,
    "--role", role,
    "--context", contextText,
    "--goal", goalText,
    "--relay", relay(args),
    "--agent-id", String(args["agent-id"] || "clawroom-relay"),
    "--require-features", String(args["require-features"] || DEFAULT_FEATURES),
    "--bridge", bridgePath,
  ];
  if (args["telegram-chat-id"]) command.push("--telegram-chat-id", String(args["telegram-chat-id"]));
  if (args["min-messages"]) command.push("--min-messages", String(args["min-messages"]));
  if (args["state-dir"]) command.push("--state-dir", String(args["state-dir"]));
  if (args["wait-ms"]) command.push("--wait-ms", String(args["wait-ms"]));

  const result = spawnSync(process.execPath, command, {
    cwd: __dirname,
    encoding: "utf8",
    env: process.env,
    timeout: Number(args["launch-timeout-ms"] || 75_000),
  });
  const body = parseJson(result.stdout);
  if (result.status !== 0 || !body.ok) {
    return { ok: false, status: result.status, launcher: body, stderr: String(result.stderr || "").slice(0, 1000) };
  }
  return { ok: true, launcher: body };
}

async function createThread(args) {
  const key = createKey(args);
  const headers = key ? { "x-clawroom-create-key": key } : {};
  return fetchJson(`${relay(args)}/threads`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      topic: String(args.topic || "ClawRoom coordination"),
      goal: goal(args),
    }),
  });
}

async function resolveInvite(args) {
  const invite = String(args.invite || args.url || "").trim();
  if (!invite) usage();
  let url;
  try {
    url = new URL(invite);
  } catch {
    fail("bad_invite", "I could not read that invite. Please forward the full ClawRoom invite link.");
  }

  if (url.pathname.startsWith("/i/")) {
    const body = await fetchJson(url.toString());
    return { ...body, relay: url.origin };
  }

  const match = url.pathname.match(/^\/threads\/([^/]+)\/join$/);
  const token = url.searchParams.get("token") || "";
  if (match && token) {
    const snapshot = await fetchJson(`${url.origin}/threads/${match[1]}/join?token=${encodeURIComponent(token)}`);
    return { thread_id: match[1], token, role: "guest", relay: url.origin, topic: snapshot.topic || "", goal: snapshot.goal || "" };
  }

  fail("bad_invite", "I could not read that invite. Please forward the full ClawRoom invite link.");
}

async function runCreate(args) {
  const thread = await createThread(args);
  const threadId = String(thread.thread_id || "");
  const token = String(thread.host_token || "");
  if (!threadId || !token) fail("bad_relay_response", "I could not create a reliable room just now. Please try again.");

  const launched = launch(args, {
    threadId,
    token,
    role: "host",
    goalText: goal(args),
    contextText: context(args),
  });
  const machinePath = writeMachineState(args, threadId, "host", { thread, launch: launched });
  if (!launched.ok) {
    fail("launch_failed", "I created the room, but this runtime could not keep it running automatically.", 1, debug(args, { machine_state_path: machinePath, launch: launched }));
  }

  const invite = String(thread.public_invite_url || thread.invite_url || "");
  emit({
    ok: true,
    mode: "host_created",
    public_invite_url: invite,
    public_message: `I started the room. Send this invite to their agent:\n${invite}`,
    ...debug(args, { machine_state_path: machinePath, launcher: launched.launcher }),
  });
}

async function runJoin(args) {
  const invite = await resolveInvite(args);
  const threadId = String(invite.thread_id || "");
  const token = String(invite.token || "");
  if (!threadId || !token) fail("bad_invite", "I could not join from that invite. Please forward the full ClawRoom invite link again.");

  const launchArgs = { ...args, relay: args.relay || invite.relay || DEFAULT_RELAY };
  const launched = launch(launchArgs, {
    threadId,
    token,
    role: "guest",
    goalText: goal(args, invite.goal || invite.topic || ""),
    contextText: context(args),
  });
  const machinePath = writeMachineState(args, threadId, "guest", { invite, launch: launched });
  if (!launched.ok) {
    fail("launch_failed", "I found the room, but this runtime could not keep it running automatically.", 1, debug(args, { machine_state_path: machinePath, launch: launched }));
  }

  emit({
    ok: true,
    mode: "guest_joined",
    public_message: "I joined the room and will report back when the agents settle it.",
    ...debug(args, { machine_state_path: machinePath, launcher: launched.launcher }),
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = String(args._[0] || args.mode || "");
  if (args.help || command === "help") usage(0);
  if (command === "create" || command === "host") return runCreate(args);
  if (command === "join" || command === "guest") return runJoin(args);
  usage();
}

main().catch((error) => {
  const code = error?.body?.error || "unexpected_error";
  if (code === "create_key_required" || code === "invalid_create_key") {
    fail(code, "This hosted ClawRoom relay is private beta. Ask the relay owner for a create key or use your own relay.", 1);
  }
  if (code === "create_disabled" || code === "relay_disabled") {
    fail(code, "This ClawRoom relay is temporarily not accepting new rooms. Try another relay or wait for the owner to reopen it.", 1);
  }
  if (code === "create_keys_not_configured") {
    fail(code, "This ClawRoom relay requires a create key but has not been configured correctly. Ask the relay owner to fix the relay settings.", 1);
  }
  if (code === "create_rate_limited") {
    fail(code, "This ClawRoom relay is busy right now. Please try again later or use your own relay.", 1);
  }
  fail(code, "I could not start ClawRoom from this runtime right now. Please try again or ask for debug details.", 1);
});

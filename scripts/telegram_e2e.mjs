#!/usr/bin/env node
/**
 * ClawRoom v3.1 Telegram E2E harness.
 *
 * Creates one relay thread, sends host/guest bootstrap prompts through the
 * macOS Telegram Desktop app, then monitors the relay until both sides close.
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

const DEFAULT_RELAY = "https://clawroom-v3-relay.heyzgj.workers.dev";
const DEFAULT_HOST_BOT = "@singularitygz_bot";
const DEFAULT_GUEST_BOT = "@link_clawd_bot";

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

function boolArg(args, key) {
  return args[key] === true || args[key] === "true" || args[key] === "1";
}

function normalizeBot(value) {
  return String(value || "")
    .trim()
    .replace(/^https:\/\/t\.me\//, "")
    .replace(/^t\.me\//, "")
    .replace(/^@/, "");
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\"'\"'`)}'`;
}

function run(cmd, args = [], input = undefined) {
  return execFileSync(cmd, args, {
    input,
    encoding: "utf8",
    stdio: input === undefined ? ["ignore", "pipe", "pipe"] : ["pipe", "pipe", "pipe"],
  });
}

function readJsonFile(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return {};
  }
}

function readClipboard() {
  return run("pbpaste").replace(/\n$/, "");
}

function writeClipboard(text) {
  run("pbcopy", [], text);
}

function runAppleScript(lines) {
  const args = [];
  for (const line of lines) args.push("-e", line);
  run("osascript", args);
}

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function sendTelegramMessage(bot, text, { resetSession, waitAfterOpenMs, waitAfterNewMs }) {
  const target = normalizeBot(bot);
  if (!target) throw new Error("Telegram bot target is required.");
  run("open", [`tg://resolve?domain=${target}`]);
  await sleep(waitAfterOpenMs);

  const steps = resetSession
    ? [
        { text: "/new", doubleEnter: true, delay: waitAfterNewMs },
        { text, doubleEnter: false, delay: 0 },
      ]
    : [{ text, doubleEnter: false, delay: 0 }];

  const previous = readClipboard();
  try {
    for (const step of steps) {
      writeClipboard(step.text);
      const lines = [
        'tell application "Telegram" to activate',
        "delay 0.2",
        'tell application "System Events"',
        '  keystroke "v" using {command down}',
        "  delay 0.12",
        "  key code 36",
        "  delay 0.12",
      ];
      if (step.doubleEnter) {
        lines.push("  key code 36");
        lines.push("  delay 0.12");
      }
      lines.push("end tell");
      runAppleScript(lines);
      if (step.delay > 0) await sleep(step.delay);
    }
  } finally {
    writeClipboard(previous);
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    signal: AbortSignal.timeout(Number(options.timeoutMs || 20_000)),
  });
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text };
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} ${url}: ${JSON.stringify(body).slice(0, 500)}`);
  }
  return body;
}

function buildBootstrapPrompt({ role, threadId, token, relay, goal, context, assetBase, minMessages }) {
  const lines = [
    "ClawRoom v3.1 E2E launch request.",
    "",
    `Role: ${role}`,
    `Thread: ${threadId}`,
    `Relay: ${relay}`,
    `Token: ${token}`,
    `Goal: ${goal}`,
    minMessages ? `Minimum negotiation messages before close: ${minMessages}` : "",
    `Owner context: ${context}`,
    "",
    "Run the ClawRoom bridge in your own OpenClaw runtime. Do not negotiate manually in Telegram.",
  ];

  if (assetBase) {
    lines.push(
      "",
      "If launcher.mjs or bridge.mjs are missing, download this exact test bundle first:",
      "```bash",
      "mkdir -p /tmp/clawroom-v3",
      "cd /tmp/clawroom-v3",
      `curl -fsSL ${shellQuote(`${assetBase}/launcher.mjs`)} -o launcher.mjs`,
      `curl -fsSL ${shellQuote(`${assetBase}/bridge.mjs`)} -o bridge.mjs`,
      "```"
    );
  } else {
    lines.push("", "Use the installed ClawRoom v3.1 skill files for launcher.mjs and bridge.mjs.");
  }

  lines.push(
    "",
    "Then start the verified launcher:",
    "```bash",
    "node launcher.mjs \\",
    `  --thread ${shellQuote(threadId)} \\`,
    `  --token ${shellQuote(token)} \\`,
    `  --role ${shellQuote(role)} \\`,
    `  --context ${shellQuote(context)} \\`,
    `  --goal ${shellQuote(goal)} \\`,
    `  --relay ${shellQuote(relay)} \\`,
    minMessages ? `  --min-messages ${shellQuote(minMessages)} \\` : "",
    "  --agent-id clawroom-relay",
    "```",
    "",
    "Reply in Telegram with the launcher JSON only."
  );
  return lines.join("\n");
}

async function createThread({ relay, topic, goal, noCreate }) {
  if (noCreate) {
    return {
      thread_id: "THREAD_ID",
      host_token: "HOST_TOKEN",
      guest_token: "GUEST_TOKEN",
      invite_url: `${relay}/threads/THREAD_ID/join?token=GUEST_TOKEN`,
    };
  }
  const url = `${relay}/threads/new?topic=${encodeURIComponent(topic)}&goal=${encodeURIComponent(goal)}`;
  return fetchJson(url);
}

async function monitorThread({ relay, threadId, hostToken, timeoutSeconds, pollSeconds, artifactPath }) {
  const deadline = Date.now() + timeoutSeconds * 1000;
  let last = null;
  while (Date.now() < deadline) {
    last = await fetchJson(`${relay}/threads/${threadId}/join?token=${encodeURIComponent(hostToken)}`);
    writeFileSync(artifactPath, `${JSON.stringify({ ...readJsonFile(artifactPath), phase: "monitoring", snapshot: last }, null, 2)}\n`);
    const rawHeartbeats = Array.isArray(last.runtime_heartbeats) ? last.runtime_heartbeats : Object.values(last.runtime_heartbeats || {});
    const heartbeats = rawHeartbeats.map((row) => row?.role || row?.status || "?").join(",");
    console.log(
      JSON.stringify({
        closed: Boolean(last.closed),
        last_message: last.last_message || null,
        heartbeats,
        close_state: last.close_state || null,
      })
    );
    if (last.closed) return last;
    await sleep(pollSeconds * 1000);
  }
  throw new Error(`Timed out waiting for thread ${threadId} to close.`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const relay = String(args.relay || process.env.CLAWROOM_RELAY || DEFAULT_RELAY).replace(/\/$/, "");
  const hostBot = String(args["host-bot"] || DEFAULT_HOST_BOT);
  const guestBot = String(args["guest-bot"] || DEFAULT_GUEST_BOT);
  const topic = String(args.topic || "ClawRoom v3.1 Telegram E2E");
  const goal = String(args.goal || "Agree on one 30 minute meeting time and close with a concise owner summary.");
  const hostContext = String(args["host-context"] || "George can meet Wednesday 3pm Shanghai time for 30 minutes.");
  const guestContext = String(args["guest-context"] || "Tom can meet Wednesday afternoon except 4pm and prefers an English summary.");
  const minMessages = String(args["min-messages"] || "").trim();
  const assetBase = String(args["asset-base"] || "").replace(/\/$/, "");
  const send = boolArg(args, "send");
  const noCreate = boolArg(args, "no-create");
  const monitor = boolArg(args, "monitor");
  const waitAfterOpenMs = Number(args["wait-after-open-ms"] || 1200);
  const waitAfterNewMs = Number(args["wait-after-new-ms"] || 30_000);
  const timeoutSeconds = Number(args["timeout-seconds"] || 900);
  const pollSeconds = Number(args["poll-seconds"] || 10);
  const artifactDir = resolve(String(args["artifact-dir"] || join(homedir(), ".clawroom-v3", "e2e")));
  mkdirSync(artifactDir, { recursive: true });

  const thread = await createThread({ relay, topic, goal, noCreate });
  const threadId = thread.thread_id || thread.id;
  const hostToken = thread.host_token;
  const guestToken = thread.guest_token;
  if (!threadId || !hostToken || !guestToken) {
    throw new Error(`Bad create response: ${JSON.stringify(thread)}`);
  }

  const hostPrompt = buildBootstrapPrompt({
    role: "host",
    threadId,
    token: hostToken,
    relay,
    goal,
    context: hostContext,
    assetBase,
    minMessages,
  });
  const guestPrompt = buildBootstrapPrompt({
    role: "guest",
    threadId,
    token: guestToken,
    relay,
    goal,
    context: guestContext,
    assetBase,
    minMessages,
  });

  const artifactPath = join(artifactDir, `${threadId}.json`);
  const hostPromptPath = join(artifactDir, `${threadId}-host-prompt.txt`);
  const guestPromptPath = join(artifactDir, `${threadId}-guest-prompt.txt`);
  writeFileSync(hostPromptPath, `${hostPrompt}\n`);
  writeFileSync(guestPromptPath, `${guestPrompt}\n`);
  writeFileSync(
    artifactPath,
    `${JSON.stringify({ phase: "created", relay, thread, hostBot, guestBot, hostPromptPath, guestPromptPath }, null, 2)}\n`
  );

  console.log(JSON.stringify({ phase: "created", thread_id: threadId, hostPromptPath, guestPromptPath, send, monitor }, null, 2));

  if (send) {
    await sendTelegramMessage(hostBot, hostPrompt, { resetSession: true, waitAfterOpenMs, waitAfterNewMs });
    console.log(JSON.stringify({ phase: "host_sent", bot: hostBot, thread_id: threadId }));
    await sleep(2000);
    await sendTelegramMessage(guestBot, guestPrompt, { resetSession: true, waitAfterOpenMs, waitAfterNewMs });
    console.log(JSON.stringify({ phase: "guest_sent", bot: guestBot, thread_id: threadId }));
  }

  if (monitor) {
    const finalSnapshot = await monitorThread({ relay, threadId, hostToken, timeoutSeconds, pollSeconds, artifactPath });
    writeFileSync(artifactPath, `${JSON.stringify({ ...readJsonFile(artifactPath), phase: "closed", relay, thread, finalSnapshot }, null, 2)}\n`);
    console.log(JSON.stringify({ phase: "closed", thread_id: threadId, finalSnapshot }, null, 2));
  }
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message }, null, 2));
  process.exit(1);
});

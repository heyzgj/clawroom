#!/usr/bin/env node
/**
 * Verified launcher for ClawRoom bridge.
 *
 * Starts bridge.mjs as a detached process, waits for runtime-state.json,
 * verifies the PID is alive, and prints a machine-readable launch result.
 */

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DEFAULT_REQUIRED_FEATURES = ["owner-reply-url"];
const FEATURE_MARKERS = {
  "owner-reply-url": "ownerDecisionUrl",
  "telegram-ask-owner-bindings": "writeAskOwnerTelegramBinding",
  "telegram-force-reply": "force_reply",
  "openclaw-state-dir-fallback": "CLAWDBOT_STATE_DIR",
};

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

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
}

function pidAlive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function emit(payload, status = 0) {
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(status);
}

let ownerFacingOutput = false;

function ownerFacingMessage(payload, role) {
  if (payload?.ok) {
    if (role === "guest") {
      return "I joined the ClawRoom and will report back here when the agents settle it.";
    }
    return "I started the ClawRoom bridge and will report back here when the agents settle it.";
  }
  return "I could not start ClawRoom here. Please try again or ask for debug details.";
}

function emitLaunch(payload, status = 0) {
  if (ownerFacingOutput) {
    process.stdout.write(`${ownerFacingMessage(payload, String(payload?.role || role || ""))}\n`);
    process.exit(status);
  }
  emit(payload, status);
}

function sha256(text) {
  return createHash("sha256").update(text).digest("hex");
}

function csv(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

const args = parseArgs(process.argv.slice(2));
const threadId = String(args.thread || args["thread-id"] || "");
const role = String(args.role || "");
ownerFacingOutput = Boolean(args["owner-facing"] || args.public || args.safe);
const openClawStateDir = resolve(String(process.env.OPENCLAW_STATE_DIR || join(homedir(), ".openclaw")));
const defaultStateDir = process.env.OPENCLAW_STATE_DIR ? join(openClawStateDir, "clawroom") : join(homedir(), ".clawroom");
const stateDir = resolve(String(args["state-dir"] || process.env.CLAWROOM_STATE_DIR || defaultStateDir));
const bridgePath = resolve(String(args.bridge || join(__dirname, "bridge.mjs")));
const waitMs = Math.max(3_000, Math.min(60_000, Number(args["wait-ms"] || 20_000)));
const expectedBridgeSha = String(args["expect-bridge-sha256"] || process.env.CLAWROOM_BRIDGE_SHA256 || "").trim();
const requiredFeatures = csv(args["require-features"] || process.env.CLAWROOM_REQUIRED_BRIDGE_FEATURES);
for (const feature of DEFAULT_REQUIRED_FEATURES) {
  if (!requiredFeatures.includes(feature)) requiredFeatures.push(feature);
}

if (!threadId || !["host", "guest"].includes(role)) {
  emitLaunch({ ok: false, role, error: "usage", hint: "Required: --thread <id> --role host|guest plus bridge args." }, 2);
}
if (!existsSync(bridgePath)) {
  emitLaunch({ ok: false, role, error: "bridge_not_found", bridge_path: bridgePath }, 2);
}

const bridgeSource = readFileSync(bridgePath, "utf8");
const bridgeSha256 = sha256(bridgeSource);
if (expectedBridgeSha && bridgeSha256 !== expectedBridgeSha) {
  emitLaunch({
    ok: false,
    role,
    error: "bridge_sha_mismatch",
    bridge_path: bridgePath,
    expected_bridge_sha256: expectedBridgeSha,
    actual_bridge_sha256: bridgeSha256,
  }, 2);
}
const missingFeatures = requiredFeatures.filter((feature) => {
  const marker = FEATURE_MARKERS[feature];
  return !marker || !bridgeSource.includes(marker);
});
if (missingFeatures.length > 0) {
  emitLaunch({
    ok: false,
    role,
    error: "bridge_feature_missing",
    bridge_path: bridgePath,
    bridge_sha256: bridgeSha256,
    missing_features: missingFeatures,
    required_features: requiredFeatures,
  }, 2);
}

mkdirSync(stateDir, { recursive: true });
const runtimeStatePath = join(stateDir, `${threadId}-${role}.runtime-state.json`);
const logPath = join(stateDir, `${threadId}-${role}.bridge.log`);
const launchPath = join(stateDir, `${threadId}-${role}.launch.json`);

const launcherOnlyValueArgs = new Set(["--bridge", "--wait-ms", "--expect-bridge-sha256"]);
const launcherOnlyBooleanArgs = new Set(["--owner-facing", "--public", "--safe"]);
const childArgs = [bridgePath, ...process.argv.slice(2).filter((arg, index, all) => {
  const prev = all[index - 1];
  return !launcherOnlyValueArgs.has(arg) && !launcherOnlyValueArgs.has(prev) && !launcherOnlyBooleanArgs.has(arg);
})];
if (!childArgs.includes("--state-dir")) {
  childArgs.push("--state-dir", stateDir);
}

const outFd = openSync(logPath, "a");
const errFd = openSync(logPath, "a");
const child = spawn(process.execPath, childArgs, {
  detached: true,
  stdio: ["ignore", outFd, errFd],
  env: process.env,
});
child.unref();
closeSync(outFd);
closeSync(errFd);

writeFileSync(launchPath, `${JSON.stringify({
  bridge_path: bridgePath,
  bridge_sha256: bridgeSha256,
  required_features: requiredFeatures,
  pid: child.pid,
  role,
  room_id: threadId,
  runtime_state_path: runtimeStatePath,
  log_path: logPath,
  launched_at: new Date().toISOString(),
}, null, 2)}\n`, "utf8");

const deadline = Date.now() + waitMs;
let lastState = null;
while (Date.now() < deadline) {
  lastState = readJson(runtimeStatePath);
  if (lastState?.pid && Number(lastState.pid) === child.pid && pidAlive(child.pid)) {
    const relayHeartbeatSeen = Boolean(lastState.last_relay_heartbeat_at || lastState.relay_heartbeat_ok);
    if (["starting", "running"].includes(String(lastState.status || "")) && relayHeartbeatSeen) {
      emitLaunch({
        ok: true,
        role,
        pid: child.pid,
        status: lastState.status,
        bridge_sha256: bridgeSha256,
        required_features: requiredFeatures,
        runtime_state_path: runtimeStatePath,
        log_path: logPath,
        state_dir: stateDir,
      });
    }
    if (String(lastState.status || "") === "failed") {
      emitLaunch({
        ok: false,
        role,
        error: "bridge_failed",
        pid: child.pid,
        runtime_state_path: runtimeStatePath,
        log_path: logPath,
        runtime_state: lastState,
      }, 1);
    }
  }
  if (!pidAlive(child.pid)) {
    emitLaunch({
      ok: false,
      role,
      error: "bridge_exited_before_ready",
      pid: child.pid,
      runtime_state_path: runtimeStatePath,
      log_path: logPath,
      runtime_state: lastState,
    }, 1);
  }
  await sleep(500);
}

emitLaunch({
  ok: false,
  role,
  error: "bridge_ready_timeout",
  pid: child.pid,
  pid_alive: pidAlive(child.pid),
  runtime_state_path: runtimeStatePath,
  log_path: logPath,
  runtime_state: lastState,
}, 1);

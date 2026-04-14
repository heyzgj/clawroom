#!/usr/bin/env node
/**
 * Verified launcher for ClawRoom bridge.
 *
 * Starts bridge.mjs as a detached process, waits for runtime-state.json,
 * verifies the PID is alive, and prints a machine-readable launch result.
 */

import { spawn } from "node:child_process";
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

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

const args = parseArgs(process.argv.slice(2));
const threadId = String(args.thread || args["thread-id"] || "");
const role = String(args.role || "");
const openClawStateDir = resolve(String(process.env.OPENCLAW_STATE_DIR || join(homedir(), ".openclaw")));
const defaultStateDir = process.env.OPENCLAW_STATE_DIR ? join(openClawStateDir, "clawroom-v3") : join(homedir(), ".clawroom-v3");
const stateDir = resolve(String(args["state-dir"] || process.env.CLAWROOM_STATE_DIR || defaultStateDir));
const bridgePath = resolve(String(args.bridge || join(__dirname, "bridge.mjs")));
const waitMs = Math.max(3_000, Math.min(60_000, Number(args["wait-ms"] || 20_000)));

if (!threadId || !["host", "guest"].includes(role)) {
  emit({ ok: false, error: "usage", hint: "Required: --thread <id> --role host|guest plus bridge args." }, 2);
}
if (!existsSync(bridgePath)) {
  emit({ ok: false, error: "bridge_not_found", bridge_path: bridgePath }, 2);
}

mkdirSync(stateDir, { recursive: true });
const runtimeStatePath = join(stateDir, `${threadId}-${role}.runtime-state.json`);
const logPath = join(stateDir, `${threadId}-${role}.bridge.log`);
const launchPath = join(stateDir, `${threadId}-${role}.launch.json`);

const childArgs = [bridgePath, ...process.argv.slice(2).filter((arg, index, all) => {
  const prev = all[index - 1];
  return arg !== "--bridge" && prev !== "--bridge" && arg !== "--wait-ms" && prev !== "--wait-ms";
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
      emit({
        ok: true,
        pid: child.pid,
        status: lastState.status,
        runtime_state_path: runtimeStatePath,
        log_path: logPath,
        state_dir: stateDir,
      });
    }
    if (String(lastState.status || "") === "failed") {
      emit({
        ok: false,
        error: "bridge_failed",
        pid: child.pid,
        runtime_state_path: runtimeStatePath,
        log_path: logPath,
        runtime_state: lastState,
      }, 1);
    }
  }
  if (!pidAlive(child.pid)) {
    emit({
      ok: false,
      error: "bridge_exited_before_ready",
      pid: child.pid,
      runtime_state_path: runtimeStatePath,
      log_path: logPath,
      runtime_state: lastState,
    }, 1);
  }
  await sleep(500);
}

emit({
  ok: false,
  error: "bridge_ready_timeout",
  pid: child.pid,
  pid_alive: pidAlive(child.pid),
  runtime_state_path: runtimeStatePath,
  log_path: logPath,
  runtime_state: lastState,
}, 1);

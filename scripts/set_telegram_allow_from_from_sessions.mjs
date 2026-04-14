#!/usr/bin/env node
/**
 * Recover the latest Telegram sender id from OpenClaw sessions and persist it
 * as channels.telegram.allowFrom for direct Bot API owner notifications.
 */

import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const stateDir = resolve(String(process.env.OPENCLAW_STATE_DIR || "/data/.openclaw"));
const configPath = join(stateDir, "openclaw.json");
const sessionsDir = join(stateDir, "agents", "main", "sessions");

if (!existsSync(configPath)) throw new Error(`Config not found: ${configPath}`);
if (!existsSync(sessionsDir)) throw new Error(`Sessions dir not found: ${sessionsDir}`);

let best = null;
for (const name of readdirSync(sessionsDir)) {
  if (!name.endsWith(".jsonl")) continue;
  const path = join(sessionsDir, name);
  for (const line of readFileSync(path, "utf8").split(/\n+/)) {
    if (!line.includes("ClawRoom v3.1 E2E launch request") || !line.includes("sender_id")) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue;
    }
    const content = record?.message?.content;
    const text = Array.isArray(content)
      ? content.map((item) => String(item?.text || "")).join("\n")
      : String(content || "");
    const match = text.match(/"sender_id"\s*:\s*"([0-9-]+)"/);
    if (!match) continue;
    const ts = Date.parse(record.timestamp || record?.message?.timestamp || "") || 0;
    if (!best || ts > best.ts) best = { id: match[1], ts, path };
  }
}

if (!best) throw new Error("No ClawRoom Telegram sender_id found in sessions.");

const config = JSON.parse(readFileSync(configPath, "utf8"));
config.channels ||= {};
config.channels.telegram ||= {};
const existing = Array.isArray(config.channels.telegram.allowFrom)
  ? config.channels.telegram.allowFrom.map((item) => String(item))
  : [];
if (!existing.includes(best.id)) existing.unshift(best.id);
config.channels.telegram.allowFrom = existing;

mkdirSync(join(stateDir, "config-backups"), { recursive: true });
const backup = join(stateDir, "config-backups", `openclaw.allowFrom-backup-${Date.now()}.json`);
copyFileSync(configPath, backup);
writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8");

console.log(JSON.stringify({
  ok: true,
  configPath,
  backup,
  source: best.path,
  allowFromCount: config.channels.telegram.allowFrom.length,
  recoveredChatId: {
    length: best.id.length,
    suffix: best.id.slice(-4),
  },
}, null, 2));

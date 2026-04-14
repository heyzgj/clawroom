#!/usr/bin/env node
/**
 * Print redacted OpenClaw Telegram notification config.
 */

import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const stateDir = resolve(String(process.env.OPENCLAW_STATE_DIR || "/data/.openclaw"));
const configPath = join(stateDir, "openclaw.json");
const config = existsSync(configPath) ? JSON.parse(readFileSync(configPath, "utf8")) : {};
const telegram = config?.channels?.telegram || {};

function redacted(value) {
  const text = String(value || "");
  if (!text) return { set: false };
  return { set: true, length: text.length, suffix: text.slice(-4) };
}

console.log(JSON.stringify({
  stateDir,
  configPath,
  configExists: existsSync(configPath),
  env: {
    TG_BOT_TOKEN: redacted(process.env.TG_BOT_TOKEN),
    TELEGRAM_BOT_TOKEN: redacted(process.env.TELEGRAM_BOT_TOKEN),
    TG_CHAT_ID: redacted(process.env.TG_CHAT_ID),
    TELEGRAM_CHAT_ID: redacted(process.env.TELEGRAM_CHAT_ID),
  },
  config: {
    botToken: redacted(telegram.botToken),
    allowFromCount: Array.isArray(telegram.allowFrom) ? telegram.allowFrom.length : 0,
    allowFromSuffixes: Array.isArray(telegram.allowFrom) ? telegram.allowFrom.map((item) => redacted(item)) : [],
  },
}, null, 2));

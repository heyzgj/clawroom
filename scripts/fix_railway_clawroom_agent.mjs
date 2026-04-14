#!/usr/bin/env node
/**
 * Fix Railway OpenClaw clawroom-relay workspace to use the persistent state dir.
 */

import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const stateDir = resolve(String(process.env.OPENCLAW_STATE_DIR || "/data/.openclaw"));
const configPath = join(stateDir, "openclaw.json");
const agentId = process.env.CLAWROOM_AGENT_ID || "clawroom-relay";
const workspace = join(stateDir, "workspaces", agentId);

if (!existsSync(configPath)) {
  throw new Error(`OpenClaw config not found: ${configPath}`);
}

const config = JSON.parse(readFileSync(configPath, "utf8"));
const list = config?.agents?.list;
if (!Array.isArray(list)) {
  throw new Error("OpenClaw config has no agents.list array.");
}

const agent = list.find((item) => item && item.id === agentId);
if (!agent) {
  throw new Error(`Agent not found: ${agentId}`);
}

mkdirSync(workspace, { recursive: true });
mkdirSync(join(stateDir, "agents", agentId, "agent"), { recursive: true });

const before = {
  id: agent.id,
  workspace: agent.workspace || "",
  workspaceDir: agent.workspaceDir || "",
  workspace_dir: agent.workspace_dir || "",
};

agent.workspace = workspace;
if ("workspaceDir" in agent) agent.workspaceDir = workspace;
if ("workspace_dir" in agent) agent.workspace_dir = workspace;

const backup = `${configPath}.clawroom-v3-backup-${Date.now()}`;
copyFileSync(configPath, backup);
writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8");

console.log(JSON.stringify({
  ok: true,
  configPath,
  backup,
  agentId,
  before,
  after: {
    id: agent.id,
    workspace: agent.workspace || "",
    workspaceDir: agent.workspaceDir || "",
    workspace_dir: agent.workspace_dir || "",
  },
}, null, 2));

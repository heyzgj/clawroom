#!/usr/bin/env node
/**
 * Validate a ClawRoom v3.1 Telegram E2E artifact without printing secrets.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function parseArgs(argv) {
  const result = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    if (!argv[i].startsWith("--")) {
      result._.push(argv[i]);
      continue;
    }
    const key = argv[i].slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
    result[key] = value;
  }
  return result;
}

function fail(checks, name, detail) {
  checks.push({ name, ok: false, detail });
}

function pass(checks, name, detail) {
  checks.push({ name, ok: true, detail });
}

async function fetchJson(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(20_000) });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${JSON.stringify(body).slice(0, 400)}`);
  }
  return body;
}

function getRuntimeHeartbeats(snapshot) {
  if (Array.isArray(snapshot?.runtime_heartbeats)) return snapshot.runtime_heartbeats;
  if (snapshot?.runtime_heartbeats && typeof snapshot.runtime_heartbeats === "object") {
    return Object.values(snapshot.runtime_heartbeats);
  }
  return [];
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const artifactPath = args.artifact ? resolve(String(args.artifact)) : (args._[0] ? resolve(String(args._[0])) : "");
  if (!artifactPath) throw new Error("Usage: node scripts/validate_e2e_artifact.mjs --artifact <path>");

  const artifact = JSON.parse(readFileSync(artifactPath, "utf8"));
  const relay = String(artifact.relay || "").replace(/\/$/, "");
  const threadId = artifact.thread?.thread_id || artifact.finalSnapshot?.thread_id || artifact.snapshot?.thread_id;
  const hostToken = artifact.thread?.host_token;
  const finalSnapshot = artifact.finalSnapshot || artifact.snapshot || {};
  const checks = [];

  if (!relay || !threadId) {
    throw new Error("Artifact is missing relay or thread id.");
  }

  const embeddedRows = Array.isArray(artifact.transcript) ? artifact.transcript : [];
  let transcriptSource = "embedded";
  let rows = embeddedRows;
  if (hostToken && hostToken !== "REDACTED") {
    const messages = await fetchJson(`${relay}/threads/${threadId}/msgs?token=${encodeURIComponent(hostToken)}&after=-1`);
    rows = Array.isArray(messages) ? messages : messages.messages || messages.events || [];
    transcriptSource = "relay";
  } else if (!embeddedRows.length) {
    throw new Error("Artifact has a redacted/missing host token and no embedded transcript.");
  }

  const closeRows = rows.filter((row) => row?.kind === "close");
  const messageRows = rows.filter((row) => row?.kind === "message");
  const roles = rows.map((row) => row?.from || row?.role).filter(Boolean);
  const consecutiveSameRole = roles.some((role, index) => index > 0 && role === roles[index - 1]);
  const heartbeatRows = getRuntimeHeartbeats(finalSnapshot);
  const stoppedRoles = new Set(heartbeatRows.filter((row) => row?.status === "stopped").map((row) => row.role));
  const closeRoles = new Set(closeRows.map((row) => row?.from || row?.role));
  const texts = rows.map((row) => String(row?.text || "").trim().toLowerCase()).filter(Boolean);
  const uniqueTextCount = new Set(texts).size;

  finalSnapshot.closed === true
    ? pass(checks, "room_closed", "final snapshot is closed")
    : fail(checks, "room_closed", "final snapshot is not closed");

  finalSnapshot.close_state?.host_closed === true && finalSnapshot.close_state?.guest_closed === true
    ? pass(checks, "mutual_close", "host and guest both closed")
    : fail(checks, "mutual_close", "host_closed and guest_closed are not both true");

  rows.length >= Number(args["min-events"] || 4)
    ? pass(checks, "event_count", `${rows.length} relay events`)
    : fail(checks, "event_count", `${rows.length} relay events`);

  messageRows.length >= Number(args["min-messages"] || 2)
    ? pass(checks, "message_count", `${messageRows.length} negotiation messages before close`)
    : fail(checks, "message_count", `${messageRows.length} negotiation messages before close`);

  closeRoles.has("host") && closeRoles.has("guest")
    ? pass(checks, "close_roles", "host and guest close events present")
    : fail(checks, "close_roles", `close roles: ${Array.from(closeRoles).join(",") || "none"}`);

  !consecutiveSameRole
    ? pass(checks, "turn_taking", `roles: ${roles.join(" -> ")}`)
    : fail(checks, "turn_taking", `consecutive same role in ${roles.join(" -> ")}`);

  stoppedRoles.has("host") && stoppedRoles.has("guest")
    ? pass(checks, "runtime_stopped", "host and guest runtime heartbeats stopped")
    : fail(checks, "runtime_stopped", `stopped roles: ${Array.from(stoppedRoles).join(",") || "none"}`);

  String(finalSnapshot.summary || "").trim().length > 0
    ? pass(checks, "summary_present", finalSnapshot.summary)
    : fail(checks, "summary_present", "empty summary");

  uniqueTextCount >= 2
    ? pass(checks, "not_echo_loop", `${uniqueTextCount} unique transcript texts`)
    : fail(checks, "not_echo_loop", `${uniqueTextCount} unique transcript texts`);

  const ok = checks.every((check) => check.ok);
  console.log(JSON.stringify({
    ok,
    room_id: threadId,
    stop_reason: finalSnapshot.close_state?.closed ? "mutual_close" : "unknown",
    turn_count: rows.length,
    message_count: messageRows.length,
    close_count: closeRows.length,
    transcript_source: transcriptSource,
    checks,
  }, null, 2));

  if (!ok) process.exit(1);
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message }, null, 2));
  process.exit(1);
});

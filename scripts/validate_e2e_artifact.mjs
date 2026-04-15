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

function skip(checks, name, detail) {
  checks.push({ name, ok: true, skipped: true, detail });
}

function boolArg(args, key) {
  return args[key] === true || args[key] === "true" || args[key] === "1";
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

function getMandates(artifact, role) {
  const contexts = artifact.owner_contexts || artifact.ownerContexts || {};
  const context = contexts[role] || {};
  return context.mandates || context.mandate || {};
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

function maxJpyAmount(rows) {
  const amounts = rows.flatMap((row) => parseJpyAmounts(row?.text));
  return amounts.length ? Math.max(...amounts) : null;
}

function ownerReplyApprovesExcess(rows) {
  return rows.some((row) => {
    if (row?.kind !== "owner_reply") return false;
    const text = String(row.text || "");
    if (/\b(cannot|can't|do not|don't|not above|reject|rejected|decline|ceiling|above the ceiling|over budget)\b/i.test(text) ||
      /不能|不接受|拒绝|不超过|上限|超预算|超过预算/.test(text)) return false;
    return /\b(yes|approve|approved|authorize|authorized|ok|okay)\b/i.test(text) ||
      /同意|批准|授权|可以|允许|通过/.test(text);
  });
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
  const turnRows = rows.filter((row) => row?.kind === "message" || row?.kind === "close");
  const askOwnerRows = rows.filter((row) => row?.kind === "ask_owner");
  const ownerReplyRows = rows.filter((row) => row?.kind === "owner_reply");
  const roles = turnRows.map((row) => row?.from || row?.role).filter(Boolean);
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

  const placeholderOwnerReplies = ownerReplyRows.filter((row) => /REPLACE_WITH_OWNER_DECISION/i.test(String(row?.text || "")));
  if (!ownerReplyRows.length) {
    skip(checks, "owner_reply_content", "no owner_reply events in transcript");
  } else if (placeholderOwnerReplies.length > 0) {
    fail(checks, "owner_reply_content", `${placeholderOwnerReplies.length} owner_reply events still contain placeholder text`);
  } else {
    pass(checks, "owner_reply_content", `${ownerReplyRows.length} owner_reply events contain concrete owner text`);
  }

  const hostMandates = getMandates(artifact, "host");
  const budgetCeilingJpy = Number(hostMandates.budget_ceiling_jpy || hostMandates.budget_ceiling || 0);
  const maxCloseJpy = maxJpyAmount(closeRows);
  const maxTranscriptJpy = maxJpyAmount(rows);
  const approvedExcess = ownerReplyApprovesExcess(ownerReplyRows);
  const mandateBinding = Number.isFinite(budgetCeilingJpy) && budgetCeilingJpy > 0;
  const closeExceedsMandate = mandateBinding && maxCloseJpy != null && maxCloseJpy > budgetCeilingJpy;
  const transcriptExceedsMandate = mandateBinding && maxTranscriptJpy != null && maxTranscriptJpy > budgetCeilingJpy;

  if (!mandateBinding) {
    skip(checks, "mandate_compliance", "no host budget_ceiling_jpy mandate in artifact");
    skip(checks, "ask_owner_evidence", "no binding mandate requires owner evidence");
  } else if (closeExceedsMandate && !approvedExcess) {
    fail(checks, "mandate_compliance", `close max ¥${maxCloseJpy} exceeds host ceiling ¥${budgetCeilingJpy} without owner approval`);
  } else {
    pass(checks, "mandate_compliance", `close max ${maxCloseJpy == null ? "n/a" : `¥${maxCloseJpy}`} within host ceiling ¥${budgetCeilingJpy}${approvedExcess ? " or owner-approved" : ""}`);
  }

  if (mandateBinding) {
    const requireAskOwner = boolArg(args, "require-ask-owner") || transcriptExceedsMandate;
    if (!requireAskOwner) {
      skip(checks, "ask_owner_evidence", "mandate present but no above-ceiling amount observed");
    } else if (askOwnerRows.length > 0 && ownerReplyRows.length > 0) {
      pass(checks, "ask_owner_evidence", `${askOwnerRows.length} ask_owner and ${ownerReplyRows.length} owner_reply events`);
    } else {
      fail(checks, "ask_owner_evidence", `ask_owner events=${askOwnerRows.length}, owner_reply events=${ownerReplyRows.length}`);
    }
  }

  const ok = checks.every((check) => check.ok);
  console.log(JSON.stringify({
    ok,
    room_id: threadId,
    stop_reason: finalSnapshot.close_state?.closed ? "mutual_close" : "unknown",
    turn_count: rows.length,
    message_count: messageRows.length,
    close_count: closeRows.length,
    transcript_source: transcriptSource,
    mandate: mandateBinding ? {
      host_budget_ceiling_jpy: budgetCeilingJpy,
      max_close_jpy: maxCloseJpy,
      max_transcript_jpy: maxTranscriptJpy,
      approved_excess: approvedExcess,
    } : null,
    checks,
  }, null, 2));

  if (!ok) process.exit(1);
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message }, null, 2));
  process.exit(1);
});

import { badRequest, conflict, json, normalizePlatformError, unauthorized } from "./worker_util";

type Env = {
  ROOM_REGISTRY?: DurableObjectNamespace;
  PARTICIPANT_ONLINE_STALE_SECONDS?: string;
  COMPATIBILITY_JOIN_GRACE_SECONDS?: string;
  COMPATIBILITY_FIRST_RELAY_GRACE_SECONDS?: string;
  COMPATIBILITY_STALL_GRACE_SECONDS?: string;
  MANAGED_FIRST_RELAY_RISK_SECONDS?: string;
  PEER_JOIN_LATENCY_RISK_SECONDS?: string;
  RUNNER_LEASE_LOW_SECONDS?: string;
  MANUAL_REPAIR_PREPARE_SECONDS?: string;
  REPAIR_ISSUED_STALE_SECONDS?: string;
  CLAWROOM_PUBLIC_API_BASE?: string;
};

type RoomStatus = "active" | "closed";
type LifecycleState = "submitted" | "working" | "input_required" | "completed" | "failed" | "canceled";
type ExecutionMode = "managed_attached" | "compatibility" | "managed_hosted";
type ExecutionAttentionState = "healthy" | "attention" | "takeover_recommended" | "takeover_required";
type RunnerCertification = "none" | "candidate" | "certified";
type ManagedCoverage = "none" | "partial" | "full";
type RecoveryPolicy = "automatic" | "takeover_only";
type RootCauseConfidence = "low" | "medium" | "high";
type AttemptStatus =
  | "pending"
  | "ready"
  | "active"
  | "idle"
  | "waiting_owner"
  | "stalled"
  | "restarting"
  | "replaced"
  | "exited"
  | "abandoned";

type RecoveryActionStatus = "pending" | "issued" | "resolved" | "superseded";
type RecoveryActionKind = "repair_invite_reissue";
type RecoveryDeliveryMode = "manual" | "automatic";
type RunnerPhase =
  | "claimed"
  | "joined"
  | "session_ready"
  | "waiting_for_peer_join"
  | "event_polling"
  | "relay_seen"
  | "reply_generating"
  | "reply_ready"
  | "reply_sending"
  | "reply_sent"
  | "owner_wait"
  | "owner_reply_handled";

type Intent = "ASK" | "ANSWER" | "NOTE" | "DONE" | "ASK_OWNER" | "OWNER_REPLY";

type Message = {
  intent: Intent;
  text: string;
  fills: Record<string, string>;
  facts: string[];
  questions: string[];
  expect_reply: boolean;
  meta: Record<string, unknown>;
};

type RoomCreateIn = {
  topic: string;
  goal: string;
  participants: string[];
  required_fields?: string[];
  expected_outcomes?: string[];
  turn_limit?: number;
  timeout_minutes?: number;
  stall_limit?: number;
  ttl_minutes?: number;
  metadata?: Record<string, unknown>;
  mission_id?: string;
  assigned_agent?: string;
};

type ParticipantState = {
  name: string;
  joined: boolean;
  online: boolean;
  last_seen_at: string | null;
  done: boolean;
  waiting_owner: boolean;
  client_name: string | null;
};

type RoomConfig = {
  turn_limit: number;
  stall_limit: number;
  timeout_minutes: number;
  ttl_minutes: number;
};

type StopReason = "goal_done" | "mutual_done" | "turn_limit" | "stall_limit" | "timeout" | "manual_close";

type RootCauseHint = {
  code: string;
  confidence: RootCauseConfidence;
  summary: string;
  evidence: string[];
};

type RoomSnapshot = {
  id: string;
  topic: string;
  goal: string;
  protocol_version: number;
  capabilities: string[];
  execution_mode: ExecutionMode;
  runner_certification: RunnerCertification;
  managed_coverage: ManagedCoverage;
  supervision_origins: string[];
  product_owned: boolean;
  automatic_recovery_eligible: boolean;
  attempt_status: AttemptStatus;
  active_runner_id: string | null;
  active_runner_count: number;
  last_recovery_reason: string | null;
  execution_attention: {
    state: ExecutionAttentionState;
    reasons: string[];
    summary: string | null;
    next_action: string | null;
    takeover_required: boolean;
  };
  root_cause_hints: RootCauseHint[];
  repair_hint: {
    available: boolean;
    strategy: "reissue_invite" | null;
    summary: string | null;
    endpoint_template: string | null;
    invalidates_previous_invite: boolean;
    participants: Array<{
      name: string;
      reason: string;
    }>;
  };
  recovery_actions: Array<{
    action_id: string;
    participant: string;
    kind: RecoveryActionKind;
    delivery_mode: RecoveryDeliveryMode;
    status: RecoveryActionStatus;
    reason: string;
    summary: string | null;
    package_ready: boolean;
    created_at: string;
    updated_at: string;
    issued_at: string | null;
    resolved_at: string | null;
    issue_count: number;
    current: boolean;
  }>;
  start_slo: {
    room_created_at: string;
    first_joined_at: string | null;
    all_joined_at: string | null;
    first_relay_at: string | null;
    join_latency_ms: number | null;
    full_join_latency_ms: number | null;
    first_relay_latency_ms: number | null;
  };
  lifecycle_state: LifecycleState;
  required_fields: string[];
  expected_outcomes: string[];
  fields: Record<string, { value: string; updated_at: string; by: string }>;
  status: RoomStatus;
  stop_reason: StopReason | null;
  stop_detail: string | null;
  created_at: string;
  updated_at: string;
  turn_count: number;
  stall_count: number;
  deadline_at: string;
  participants: Array<{
    name: string;
    joined: boolean;
    joined_at: string | null;
    online: boolean;
    last_seen_at: string | null;
    done: boolean;
    waiting_owner: boolean;
    client_name: string | null;
  }>;
  runner_attempts: Array<{
    attempt_id: string;
    participant: string;
    runner_id: string;
    execution_mode: ExecutionMode;
    status: AttemptStatus;
    claimed_at: string;
    updated_at: string;
    lease_expires_at: string | null;
    released_at: string | null;
    restart_count: number;
    log_ref: string | null;
    last_error: string | null;
    last_recovery_reason: string | null;
    phase: RunnerPhase;
    phase_detail: string | null;
    phase_updated_at: string;
    phase_age_ms: number | null;
    lease_remaining_ms: number | null;
    capabilities: Record<string, unknown>;
    replacement_count: number;
    supersedes_run_id: string | null;
    managed_certified: boolean;
    recovery_policy: RecoveryPolicy;
    current: boolean;
  }>;
};

type EventAudience = "*" | string;

type EventRow = {
  id: number;
  type: string;
  created_at: string;
  audience: EventAudience;
  payload: any;
};

const PROTOCOL_VERSION = 1;
const DEFAULT_MANAGED_FIRST_RELAY_RISK_SECONDS = 45;
const DEFAULT_RUNNER_LEASE_LOW_SECONDS = 20;
const DEFAULT_OWNER_REPLY_OVERDUE_SECONDS = 300;
const DEFAULT_PEER_JOIN_LATENCY_RISK_SECONDS = 30;
const DEFAULT_MANUAL_REPAIR_PREPARE_SECONDS = 15;
const IMMEDIATE_MANUAL_REPAIR_REASONS = new Set([
  "runner_not_claimed_after_wake",
  "runnerd_lost_before_claim",
  "runnerd_restart_exhausted_before_claim",
  "runnerd_lost_after_claim",
  "runnerd_restart_exhausted_after_claim",
]);
const ROOM_CAPABILITIES = Object.freeze([
  "relay_done_even_if_expect_reply_false",
  "strict_required_fields_v1",
  "idempotent_reply_v1",
  "joined_gate_v1",
  "strict_goal_done_v2",
  "close_idempotent_v1",
  "message_bounds_v1",
  "participant_stream_v1",
  "runner_plane_v1",
  "execution_attention_v1",
  "runner_certification_v1",
  "managed_coverage_v1",
  "product_owned_v1",
  "repair_invites_v1",
  "recovery_action_packages_v1",
  "repair_claim_overdue_v1",
  "root_cause_hints_v1",
  "runner_checkpoints_v1",
]);

const MAX_MESSAGE_TEXT = 2_000;
const MAX_FACTS = 12;
const MAX_QUESTIONS = 12;
const MAX_FACT_TEXT = 280;
const MAX_QUESTION_TEXT = 280;
const MAX_FILLS = 16;
const MAX_FILL_KEY = 120;
const MAX_FILL_VALUE = 500;
const DEFAULT_RUNNER_LEASE_SECONDS = 45;
const MAX_RUNNER_LEASE_SECONDS = 900;
const DEFAULT_COMPATIBILITY_JOIN_GRACE_SECONDS = 90;
const DEFAULT_COMPATIBILITY_FIRST_RELAY_GRACE_SECONDS = 180;
const DEFAULT_COMPATIBILITY_STALL_GRACE_SECONDS = 120;
const DEFAULT_COMPLETION_GRACE_SECONDS = 30;
const DEFAULT_REPAIR_ISSUED_STALE_SECONDS = 90;
const DEFAULT_PUBLIC_API_BASE = "https://api.clawroom.cc";
const DEFAULT_PARTICIPANT_TOUCH_DEBOUNCE_SECONDS = 10;
const HOT_PATH_RECONCILE_DEBOUNCE_MS = 3_000;
const HOT_PATH_EXPIRY_CHECK_DEBOUNCE_MS = 1_000;
const LIVE_ATTEMPT_STATUSES = new Set<AttemptStatus>([
  "ready",
  "active",
  "idle",
  "waiting_owner",
  "stalled",
  "restarting",
]);

type RunnerAttemptRecord = {
  attempt_id: string;
  participant: string;
  runner_id: string;
  execution_mode: ExecutionMode;
  status: AttemptStatus;
  claimed_at: string;
  updated_at: string;
  lease_expires_at: string | null;
  released_at: string | null;
  restart_count: number;
  log_ref: string | null;
  last_error: string | null;
  last_recovery_reason: string | null;
  phase: RunnerPhase;
  phase_detail: string | null;
  phase_updated_at: string;
  phase_age_ms: number | null;
  lease_remaining_ms: number | null;
  capabilities: Record<string, unknown>;
  replacement_count: number;
  supersedes_run_id: string | null;
  managed_certified: boolean;
  recovery_policy: RecoveryPolicy;
  current: boolean;
};

function nowIso(): string {
  return new Date().toISOString();
}

function normText(text: string): string {
  const cleaned = String(text || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned.slice(0, 400);
}

function normOutcomeKey(value: unknown): string {
  return String(value || "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function parseOutcomeList(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((x) => String(x).trim()).filter(Boolean).slice(0, 64);
}

function outcomeSignature(values: string[]): string {
  const uniq = new Set<string>();
  for (const value of values) {
    const key = normOutcomeKey(value);
    if (key) uniq.add(key);
  }
  return Array.from(uniq).sort().join("\u0000");
}

async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", data);
  const bytes = new Uint8Array(digest);
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function normalizeIntent(raw: unknown): Intent {
  const value = String(raw || "ANSWER").toUpperCase().trim();
  if (value === "NEED_HUMAN") return "ASK_OWNER";
  const valid = new Set<Intent>(["ASK", "ANSWER", "NOTE", "DONE", "ASK_OWNER", "OWNER_REPLY"]);
  return (valid.has(value as Intent) ? (value as Intent) : "ANSWER") as Intent;
}

function trimmedString(value: unknown, maxLen: number): string {
  return String(value || "").trim().slice(0, maxLen);
}

function normalizeStringList(raw: unknown, limit: number, itemMaxLen: number): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry) => trimmedString(entry, itemMaxLen))
    .filter(Boolean)
    .slice(0, limit);
}

function metaRecord(raw: unknown): Record<string, unknown> {
  return raw && typeof raw === "object" && !Array.isArray(raw) ? { ...(raw as Record<string, unknown>) } : {};
}

function boolish(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const lowered = value.trim().toLowerCase();
    return lowered === "true" || lowered === "1" || lowered === "yes" || lowered === "done";
  }
  return false;
}

function hasCompletionMarker(meta: Record<string, unknown>): boolean {
  return boolish(meta.complete) || boolish(meta.completion_signal) || boolish(meta.room_complete);
}

function normalizeMessage(raw: any): Message {
  const legacy = typeof raw?.wants_reply === "boolean" && typeof raw?.expect_reply !== "boolean";
  const intent = normalizeIntent(raw?.intent);
  const text = trimmedString(raw?.text, MAX_MESSAGE_TEXT);
  if (!text) throw new Error("missing text");

  const fills: Record<string, string> = {};
  if (raw?.fills && typeof raw.fills === "object") {
    const entries = Object.entries(raw.fills).slice(0, MAX_FILLS);
    for (const [k, v] of entries) {
      const key = trimmedString(k, MAX_FILL_KEY);
      const val = trimmedString(v, MAX_FILL_VALUE);
      if (key && val) fills[key] = val;
    }
  }

  const facts = normalizeStringList(raw?.facts, MAX_FACTS, MAX_FACT_TEXT);
  const questions = normalizeStringList(raw?.questions, MAX_QUESTIONS, MAX_QUESTION_TEXT);

  let expectReply =
    typeof raw?.expect_reply === "boolean"
      ? raw.expect_reply
      : legacy
        ? Boolean(raw.wants_reply)
        : true;
  if ((intent === "DONE" || intent === "ASK_OWNER") && typeof raw?.expect_reply !== "boolean" && !legacy) {
    expectReply = false;
  }

  const meta = metaRecord(raw?.meta);

  return {
    intent,
    text,
    fills,
    facts,
    questions,
    expect_reply: Boolean(expectReply),
    meta
  };
}

function parsePositiveInt(value: string | null, fallback: number): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.floor(n);
}

function parseOptionalPositiveInt(value: unknown): number | null {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.floor(n);
}

function normalizeExecutionMode(raw: unknown): ExecutionMode {
  const value = String(raw || "compatibility").trim().toLowerCase();
  if (value === "managed_attached") return "managed_attached";
  if (value === "managed_hosted") return "managed_hosted";
  return "compatibility";
}

function normalizeAttemptStatus(raw: unknown): AttemptStatus {
  const value = String(raw || "pending").trim().toLowerCase();
  const valid = new Set<AttemptStatus>([
    "pending",
    "ready",
    "active",
    "idle",
    "waiting_owner",
    "stalled",
    "restarting",
    "replaced",
    "exited",
    "abandoned",
  ]);
  return valid.has(value as AttemptStatus) ? (value as AttemptStatus) : "pending";
}

function normalizeRecoveryPolicy(raw: unknown): RecoveryPolicy {
  const value = String(raw || "takeover_only").trim().toLowerCase();
  return value === "automatic" ? "automatic" : "takeover_only";
}

function normalizeRunnerPhase(raw: unknown): RunnerPhase {
  const value = String(raw || "claimed").trim().toLowerCase();
  const valid = new Set<RunnerPhase>([
    "claimed",
    "joined",
    "session_ready",
    "waiting_for_peer_join",
    "event_polling",
    "relay_seen",
    "reply_generating",
    "reply_ready",
    "reply_sending",
    "reply_sent",
    "owner_wait",
    "owner_reply_handled",
  ]);
  return valid.has(value as RunnerPhase) ? (value as RunnerPhase) : "claimed";
}

function normalizeRunnerCertification(raw: unknown): RunnerCertification {
  const value = String(raw || "none").trim().toLowerCase();
  if (value === "certified") return "certified";
  if (value === "candidate" || value === "uncertified") return "candidate";
  return "none";
}

function normalizeManagedCoverage(raw: unknown): ManagedCoverage {
  const value = String(raw || "none").trim().toLowerCase();
  if (value === "full") return "full";
  if (value === "partial") return "partial";
  return "none";
}

function normalizeObjectRecord(raw: unknown): Record<string, unknown> {
  return raw && typeof raw === "object" && !Array.isArray(raw) ? { ...(raw as Record<string, unknown>) } : {};
}

function clampLeaseSeconds(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULT_RUNNER_LEASE_SECONDS;
  return Math.max(10, Math.min(MAX_RUNNER_LEASE_SECONDS, Math.floor(parsed)));
}

function isoLatencyMs(fromIso: string, toIso: string | null): number | null {
  if (!toIso) return null;
  const start = Date.parse(String(fromIso || ""));
  const end = Date.parse(String(toIso || ""));
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return Math.max(0, Math.floor(end - start));
}

function formatDurationSeconds(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return remainder > 0 ? `${minutes}m ${remainder}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const minuteRemainder = minutes % 60;
  return minuteRemainder > 0 ? `${hours}h ${minuteRemainder}m` : `${hours}h`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class RoomDurableObject implements DurableObject {
  private state: DurableObjectState;
  private env: Env;

  private sql: SqlStorage;
  private schemaReady = false;
  private onlineStateDirty = false;
  private recoveryStateDirty = false;
  private snapshotVersion = 0;
  private cachedSnapshot: { roomId: string; version: number; snapshot: RoomSnapshot } | null = null;
  private lastRegistrySignature: string | null = null;
  private participantTouchCache = new Map<string, { online: boolean; lastPersistedMs: number }>();
  private lastHotPathRunnerReconcileAtMs = 0;
  private lastHotPathExpiryCheckAtMs = 0;
  private diagnostics = {
    snapshot_cache_hits: 0,
    snapshot_cache_misses: 0,
    participant_touch_skipped: 0,
    participant_touch_persisted: 0,
    registry_publish_skipped: 0,
    registry_publish_sent: 0,
    hot_path_reconcile_skipped: 0,
    hot_path_expiry_check_skipped: 0,
  };

  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;
    this.sql = state.storage.sql;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    try {
      if (request.method === "POST" && url.pathname === "/init") {
        return await this.handleInit(request);
      }

      this.ensureSchema();

      if (url.pathname === "/rooms") {
        return json({ error: "room_endpoint_requires_id" }, { status: 404 });
      }

      const parts = url.pathname.split("/").filter(Boolean);
      // /rooms/:room_id/...
      if (parts.length < 2 || parts[0] !== "rooms") return json({ error: "not_found" }, { status: 404 });
      const roomId = parts[1];
      const tail = "/" + parts.slice(2).join("/");

      // If a room was TTL-purged, treat it as not found (even if the DO instance still exists).
	      const exists = this.sql.exec("SELECT id FROM room WHERE id=? LIMIT 1", roomId).toArray();
	      if (!exists.length) return json({ error: "room_not_found" }, { status: 404 });
        const isHotReadPath =
          request.method === "GET" && (tail === "/" || tail === "/events" || tail === "/result" || tail === "/stream" || tail === "/monitor/stream" || tail === "/monitor/result");
	      await this.closeExpiredRoomIfNeeded(roomId, { debounceMs: isHotReadPath ? HOT_PATH_EXPIRY_CHECK_DEBOUNCE_MS : 0 });
	      const runnerChanges = await this.reconcileRunnerAttempts(roomId, { debounceMs: isHotReadPath ? HOT_PATH_RECONCILE_DEBOUNCE_MS : 0 });
	      if (runnerChanges.length > 0) {
	        const snapshot = await this.snapshot(roomId);
	        await this.publishRoomSnapshot(runnerChanges[runnerChanges.length - 1] || "runner_abandoned", snapshot);
	      }

	      if (request.method === "GET" && (tail === "/" || tail === "")) {
	        return await this.handleGetRoom(request, roomId);
	      }
      if (request.method === "GET" && tail === "/join_info") return await this.handleJoinInfo(request, roomId);
      if (request.method === "POST" && tail === "/join") return await this.handleJoin(request, roomId);
      if (request.method === "POST" && tail === "/runner/claim") return await this.handleRunnerClaim(request, roomId);
      if (request.method === "POST" && tail === "/runner/renew") return await this.handleRunnerRenew(request, roomId);
      if (request.method === "POST" && tail === "/runner/release") return await this.handleRunnerRelease(request, roomId);
      if (request.method === "GET" && tail === "/runner/status") return await this.handleRunnerStatus(request, roomId);
      if (request.method === "GET" && tail === "/recovery_actions") return await this.handleRecoveryActions(request, roomId);
      if (request.method === "POST" && parts.length === 4 && parts[2] === "repair_invites") {
        return await this.handleRepairInvite(request, roomId, parts[3] || "");
      }
      if (request.method === "POST" && tail === "/heartbeat") return await this.handleHeartbeat(request, roomId);
      if (request.method === "POST" && tail === "/leave") return await this.handleLeave(request, roomId);
      if (request.method === "POST" && tail === "/messages") return await this.handleMessage(request, roomId);
      if (request.method === "GET" && tail === "/events") return await this.handleEvents(request, roomId, false);
      if (request.method === "GET" && tail === "/stream") return await this.handleParticipantStream(request, roomId);
      if (request.method === "GET" && tail === "/result") return await this.handleResult(request, roomId, false);
      if (request.method === "POST" && tail === "/close") return await this.handleClose(request, roomId);

      if (request.method === "GET" && tail === "/monitor/events") return await this.handleEvents(request, roomId, true);
      if (request.method === "GET" && tail === "/monitor/result") return await this.handleResult(request, roomId, true);
      if (request.method === "GET" && tail === "/monitor/stream") return await this.handleMonitorStream(request, roomId);
      if (request.method === "GET" && tail === "/monitor/diagnostics") return await this.handleDiagnostics(request, roomId);

      return json({ error: "not_found" }, { status: 404 });
    } catch (err: any) {
      if (err instanceof Response) return err;
      const normalized = normalizePlatformError(err);
      if (normalized) return normalized;
      return json({ error: "internal_error", message: String(err?.message || err) }, { status: 500 });
    }
  }

  async alarm(): Promise<void> {
    this.ensureSchema();
    const row = this.sql.exec("SELECT id, status, expires_at, deadline_at FROM room LIMIT 1").one();
    if (!row) return;
    const status = String(row.status || "");
    const now = Date.now();

    // Alarm has 2 jobs:
    // 1) Close active rooms on timeout (deadline exceeded).
    // 2) Purge closed rooms after TTL (expires_at reached).
    if (status === "active") {
      const roomId = String(row.id || "");
      const runnerChanges = roomId ? await this.reconcileRunnerAttempts(roomId) : [];
      let activeSnapshot: RoomSnapshot | null = null;
      if (roomId) {
        activeSnapshot = await this.snapshot(roomId);
        if (runnerChanges.length > 0 || this.onlineStateDirty) {
          const eventType = runnerChanges.length > 0 ? (runnerChanges[runnerChanges.length - 1] || "runner_abandoned") : "presence_reconciled";
          this.onlineStateDirty = false;
          await this.publishRoomSnapshot(eventType, activeSnapshot);
        }
      }
      const deadlineRaw = String(row.deadline_at || "");
      const deadlineMs = Date.parse(deadlineRaw);
      if (Number.isFinite(deadlineMs) && now >= deadlineMs) {
        const changed = await this.closeRoom("timeout", "deadline exceeded");
        const snapshot = roomId ? await this.snapshot(roomId) : null;
        if (changed && snapshot) await this.publishRoomSnapshot("timeout", snapshot);
        return;
      }
      await this.scheduleActiveAlarm(row as Record<string, unknown>);
      return;
    }

    if (status !== "closed") return;

    const expiresAt = String(row.expires_at || "");
    if (!expiresAt) return;
    const exp = Date.parse(expiresAt);
    if (Number.isFinite(exp) && now >= exp) {
      const roomId = String(row.id || "");
      if (roomId) {
        await this.removeRoomFromRegistry(roomId, "ttl_purge");
      }
      // Purge ephemeral room data but keep schema to avoid 500s on future probes.
      this.sql.exec("DELETE FROM events");
      this.sql.exec("DELETE FROM fields");
      this.sql.exec("DELETE FROM seen_texts");
      this.sql.exec("DELETE FROM participants");
      this.sql.exec("DELETE FROM tokens");
      this.sql.exec("DELETE FROM room");
      this.cachedSnapshot = null;
      this.participantTouchCache.clear();
      this.lastRegistrySignature = null;
    } else if (Number.isFinite(exp)) {
      // Still closed but not yet expired; ensure a cleanup alarm is scheduled.
      await this.state.storage.setAlarm(exp);
    }
  }

  private compatibilityJoinGraceSeconds(): number {
    return parsePositiveInt(this.env.COMPATIBILITY_JOIN_GRACE_SECONDS ?? null, DEFAULT_COMPATIBILITY_JOIN_GRACE_SECONDS);
  }

  private compatibilityFirstRelayGraceSeconds(): number {
    return parsePositiveInt(this.env.COMPATIBILITY_FIRST_RELAY_GRACE_SECONDS ?? null, DEFAULT_COMPATIBILITY_FIRST_RELAY_GRACE_SECONDS);
  }

  private compatibilityStallGraceSeconds(): number {
    return parsePositiveInt(this.env.COMPATIBILITY_STALL_GRACE_SECONDS ?? null, DEFAULT_COMPATIBILITY_STALL_GRACE_SECONDS);
  }

  private managedFirstRelayRiskSeconds(): number {
    return parsePositiveInt(this.env.MANAGED_FIRST_RELAY_RISK_SECONDS ?? null, DEFAULT_MANAGED_FIRST_RELAY_RISK_SECONDS);
  }

  private runnerLeaseLowSeconds(): number {
    return parsePositiveInt(this.env.RUNNER_LEASE_LOW_SECONDS ?? null, DEFAULT_RUNNER_LEASE_LOW_SECONDS);
  }

  private ownerReplyOverdueSeconds(): number {
    return parsePositiveInt(
      (this.env as Env & { OWNER_REPLY_OVERDUE_SECONDS?: string }).OWNER_REPLY_OVERDUE_SECONDS ?? null,
      DEFAULT_OWNER_REPLY_OVERDUE_SECONDS,
    );
  }

  private peerJoinLatencyRiskSeconds(): number {
    return parsePositiveInt(this.env.PEER_JOIN_LATENCY_RISK_SECONDS ?? null, DEFAULT_PEER_JOIN_LATENCY_RISK_SECONDS);
  }

  private manualRepairPrepareSeconds(): number {
    return parsePositiveInt(this.env.MANUAL_REPAIR_PREPARE_SECONDS ?? null, DEFAULT_MANUAL_REPAIR_PREPARE_SECONDS);
  }

  private repairIssuedStaleSeconds(): number {
    return parsePositiveInt(this.env.REPAIR_ISSUED_STALE_SECONDS ?? null, DEFAULT_REPAIR_ISSUED_STALE_SECONDS);
  }

  private participantTouchDebounceSeconds(): number {
    return parsePositiveInt(
      (this.env as Env & { PARTICIPANT_TOUCH_DEBOUNCE_SECONDS?: string }).PARTICIPANT_TOUCH_DEBOUNCE_SECONDS ?? null,
      DEFAULT_PARTICIPANT_TOUCH_DEBOUNCE_SECONDS,
    );
  }

  private invalidateSnapshotCache(): void {
    this.snapshotVersion += 1;
    this.cachedSnapshot = null;
  }

  private buildRegistrySignature(room: RoomSnapshot): string {
    const currentRecovery = room.recovery_actions
      .filter((action) => action.current)
      .map((action) => [action.participant, action.status, action.delivery_mode, action.package_ready ? 1 : 0, action.reason, action.issue_count]);
    const participants = room.participants.map((participant) => [
      participant.name,
      participant.joined ? 1 : 0,
      participant.online ? 1 : 0,
      participant.done ? 1 : 0,
      participant.waiting_owner ? 1 : 0,
      participant.client_name || "",
    ]);
    return JSON.stringify({
      status: room.status,
      lifecycle_state: room.lifecycle_state,
      execution_mode: room.execution_mode,
      runner_certification: room.runner_certification,
      managed_coverage: room.managed_coverage,
      product_owned: room.product_owned,
      automatic_recovery_eligible: room.automatic_recovery_eligible,
      attempt_status: room.attempt_status,
      active_runner_id: room.active_runner_id,
      active_runner_count: room.active_runner_count,
      last_recovery_reason: room.last_recovery_reason,
      execution_attention: {
        state: room.execution_attention.state,
        reasons: room.execution_attention.reasons,
        next_action: room.execution_attention.next_action,
        takeover_required: room.execution_attention.takeover_required ? 1 : 0,
      },
      root_causes: room.root_cause_hints.map((hint) => [hint.code, hint.confidence]),
      repair_hint: {
        available: room.repair_hint.available ? 1 : 0,
        participants: room.repair_hint.participants.map((participant) => [participant.name, participant.reason]),
      },
      start_slo: room.start_slo,
      turn_count: room.turn_count,
      stop_reason: room.stop_reason,
      participants,
      recovery_actions: currentRecovery,
    });
  }

  private shouldSkipRegistryPublish(eventType: string, room: RoomSnapshot): boolean {
    if (!["heartbeat", "runner_renew", "presence_reconciled"].includes(eventType)) {
      return false;
    }
    const signature = this.buildRegistrySignature(room);
    if (this.lastRegistrySignature === signature) {
      return true;
    }
    this.lastRegistrySignature = signature;
    return false;
  }

  private deriveExecutionAttention(
    roomRow: Record<string, unknown>,
    participantRows: Array<Record<string, unknown>>,
    attempts: RunnerAttemptRecord[],
    execution: {
      executionMode: ExecutionMode;
      runnerCertification: RunnerCertification;
      automaticRecoveryEligible: boolean;
      attemptStatus: AttemptStatus;
      activeRunnerId: string | null;
      activeRunnerCount: number;
      lastRecoveryReason: string | null;
    },
    startSlo: {
      room_created_at: string;
      first_joined_at: string | null;
      all_joined_at: string | null;
      first_relay_at: string | null;
      join_latency_ms: number | null;
      full_join_latency_ms: number | null;
      first_relay_latency_ms: number | null;
    },
    currentRecoveryRows: Array<Record<string, unknown>>,
    fieldStats?: { requiredTotal: number; filledCount: number },
  ): RoomSnapshot["execution_attention"] {
    const status = String(roomRow.status || "active");
    if (status !== "active") {
      return { state: "healthy", reasons: [], summary: null, next_action: null, takeover_required: false };
    }

    const reasons: string[] = [];
    let severity = 0;
    const nowMs = Date.now();
    const createdMs = Date.parse(String(roomRow.created_at || ""));
    const updatedMs = Date.parse(String(roomRow.updated_at || ""));
    const firstJoinedMs = Date.parse(String(startSlo.first_joined_at || ""));
    const allJoinedMs = Date.parse(String(startSlo.all_joined_at || ""));
    const firstRelayMs = Date.parse(String(startSlo.first_relay_at || ""));
    const joinedCount = participantRows.filter((participant) => Boolean(participant.joined)).length;
    const joinedParticipants = participantRows
      .filter((participant) => Boolean(participant.joined))
      .map((participant) => String(participant.name || ""))
      .filter(Boolean);
    const onlineCount = participantRows.filter((participant) => Boolean(participant.online)).length;
    const waitingOwner = participantRows.some((participant) => Boolean(participant.waiting_owner));
    const anyDone = participantRows.some((participant) => Boolean(participant.done));
    const everyoneDone = participantRows.length > 0 && participantRows.every((participant) => Boolean(participant.done));
    const liveCurrentAttemptParticipants = new Set(
      attempts
        .filter((attempt) => attempt.current && LIVE_ATTEMPT_STATUSES.has(attempt.status))
        .map((attempt) => attempt.participant)
        .filter(Boolean)
    );
    const liveCurrentAttempts = attempts.filter((attempt) => attempt.current && LIVE_ATTEMPT_STATUSES.has(attempt.status));
    const latestAttemptByParticipant = new Map<string, RunnerAttemptRecord>();
    for (const attempt of attempts) {
      latestAttemptByParticipant.set(attempt.participant, attempt);
    }
    const missingRunnerParticipants =
      execution.executionMode !== "compatibility"
        ? joinedParticipants.filter((participant) => !liveCurrentAttemptParticipants.has(participant))
        : [];
    const missingAutomaticRecoveryParticipants = missingRunnerParticipants.filter((participant) => {
      const latestAttempt = latestAttemptByParticipant.get(participant);
      return Boolean(latestAttempt?.managed_certified) && latestAttempt?.recovery_policy === "automatic";
    });
    const issuedRecoveryParticipants = missingRunnerParticipants.filter((participant) =>
      currentRecoveryRows.some(
        (row) => String(row.participant || "") === participant && String(row.status || "") === "issued"
      )
    );
    const repairClaimOverdueParticipants = missingRunnerParticipants.filter((participant) =>
      currentRecoveryRows.some((row) => {
        if (String(row.participant || "") !== participant) return false;
        if (String(row.status || "") !== "issued") return false;
        const issuedAt = String(row.issued_at || "");
        const issuedMs = Date.parse(issuedAt);
        return Number.isFinite(issuedMs) && nowMs - issuedMs >= this.repairIssuedStaleSeconds() * 1000;
      })
    );
    const turnCount = Number(roomRow.turn_count || 0);
    const latestIntent = String(roomRow.latest_msg_intent || "").trim().toUpperCase();
    const latestExpectReply =
      roomRow.latest_msg_expect_reply === null || roomRow.latest_msg_expect_reply === undefined
        ? null
        : Boolean(roomRow.latest_msg_expect_reply);
    const latestMessageMs = roomRow.latest_msg_created_at ? Date.parse(String(roomRow.latest_msg_created_at)) : NaN;
    const lowLeaseAttempts = liveCurrentAttempts.filter((attempt) => {
      if (attempt.lease_remaining_ms == null) return false;
      return attempt.lease_remaining_ms <= this.runnerLeaseLowSeconds() * 1000;
    });
    const overdueOwnerAttempts = liveCurrentAttempts.filter((attempt) => {
      if (attempt.phase !== "owner_wait") return false;
      if (attempt.phase_age_ms == null) return false;
      return attempt.phase_age_ms >= this.ownerReplyOverdueSeconds() * 1000;
    });

    if (execution.executionMode === "compatibility") {
      reasons.push("compatibility_mode");
      severity = Math.max(severity, 1);
      if (execution.activeRunnerCount <= 0) {
        reasons.push("no_managed_runner");
        severity = Math.max(severity, 1);
      }
      if (joinedCount > 0 && Number.isFinite(firstJoinedMs) && !Number.isFinite(firstRelayMs)) {
        const waitingSeconds = Math.floor((nowMs - firstJoinedMs) / 1000);
        if (waitingSeconds >= this.compatibilityFirstRelayGraceSeconds()) {
          reasons.push("first_relay_overdue");
          severity = Math.max(severity, 3);
        }
      }
      if (turnCount > 0 && Number.isFinite(updatedMs)) {
        const stalledSeconds = Math.floor((nowMs - updatedMs) / 1000);
        if (stalledSeconds >= this.compatibilityStallGraceSeconds()) {
          reasons.push("compatibility_room_stalled");
          severity = Math.max(severity, 3);
        }
      }
      if (joinedCount > 0 && onlineCount <= 0) {
        reasons.push("no_online_participants");
        severity = Math.max(severity, 3);
      }
      if (joinedCount <= 0 && Number.isFinite(createdMs)) {
        const pendingSeconds = Math.floor((nowMs - createdMs) / 1000);
        if (pendingSeconds >= this.compatibilityJoinGraceSeconds()) {
          reasons.push("join_not_started");
          severity = Math.max(severity, 1);
        }
      }
      if (anyDone && !everyoneDone) {
        reasons.push("awaiting_mutual_completion");
        severity = Math.max(severity, 1);
        if (Number.isFinite(latestMessageMs) && nowMs - latestMessageMs >= DEFAULT_COMPLETION_GRACE_SECONDS * 1000) {
          severity = Math.max(severity, 2);
        }
      }
      if (!everyoneDone && (latestIntent === "DONE" || latestExpectReply === false)) {
        reasons.push("terminal_turn_without_room_close");
        severity = Math.max(severity, 1);
      }
    }

    if (execution.executionMode !== "compatibility") {
      if (joinedCount > 0 && execution.activeRunnerCount > 0 && Number.isFinite(firstJoinedMs) && !Number.isFinite(firstRelayMs)) {
        const waitingSeconds = Math.floor((nowMs - firstJoinedMs) / 1000);
        if (waitingSeconds >= this.managedFirstRelayRiskSeconds()) {
          reasons.push("first_relay_at_risk");
          severity = Math.max(severity, 2);
        }
      }
      if (
        participantRows.length > 1 &&
        joinedCount === participantRows.length &&
        Number.isFinite(allJoinedMs) &&
        startSlo.full_join_latency_ms != null &&
        startSlo.full_join_latency_ms >= this.peerJoinLatencyRiskSeconds() * 1000 &&
        !Number.isFinite(firstRelayMs) &&
        liveCurrentAttempts.some((attempt) => attempt.phase === "waiting_for_peer_join" || attempt.phase === "event_polling" || attempt.phase === "relay_seen")
      ) {
        reasons.push("peer_join_latency_high");
        severity = Math.max(severity, 2);
      }
      if (lowLeaseAttempts.length > 0) {
        reasons.push("runner_lease_low");
        severity = Math.max(severity, Number.isFinite(firstRelayMs) ? 1 : 2);
      }
      if (missingRunnerParticipants.length > 0 && !everyoneDone) {
        reasons.push("replacement_pending");
        severity = Math.max(severity, missingAutomaticRecoveryParticipants.length > 0 ? 2 : 3);
        if (issuedRecoveryParticipants.length > 0) {
          reasons.push("repair_package_issued");
          severity = Math.max(severity, 3);
        }
        if (repairClaimOverdueParticipants.length > 0) {
          reasons.push("repair_claim_overdue");
          severity = Math.max(severity, 3);
        }
      }
      if (execution.runnerCertification === "candidate") {
        reasons.push("managed_runner_uncertified");
        severity = Math.max(severity, 1);
      }
      if (execution.attemptStatus === "stalled") {
        reasons.push("runner_stalled");
        severity = Math.max(severity, 2);
      }
      if (execution.attemptStatus === "restarting") {
        reasons.push("runner_restarting");
        severity = Math.max(severity, 2);
      }
      if (execution.attemptStatus === "abandoned") {
        reasons.push("runner_abandoned");
        severity = Math.max(severity, 3);
      }
    }

    if (waitingOwner) {
      reasons.push("waiting_on_owner");
      severity = Math.max(severity, Math.max(1, severity));
      if (overdueOwnerAttempts.length > 0) {
        reasons.push("owner_reply_overdue");
        severity = Math.max(severity, 2);
      }
    }

    // Semantic stall: room has turns but required fields remain empty
    if (fieldStats && fieldStats.requiredTotal > 0 && fieldStats.filledCount === 0 && turnCount >= 3) {
      reasons.push("required_fields_not_progressing");
      severity = Math.max(severity, 2); // takeover_recommended
    } else if (fieldStats && fieldStats.requiredTotal > 0 && fieldStats.filledCount === 0 && turnCount >= 2) {
      reasons.push("required_fields_at_risk");
      severity = Math.max(severity, 1); // attention
    }

    const state: ExecutionAttentionState =
      severity >= 3
        ? "takeover_required"
        : severity === 2
          ? "takeover_recommended"
          : severity === 1
            ? "attention"
            : "healthy";

    let summary: string | null = null;
    let nextAction: string | null = null;
    if (state !== "healthy") {
      if (reasons.includes("first_relay_overdue")) {
        const waited = startSlo.first_joined_at
          ? formatDurationSeconds(Math.max(0, Math.floor((nowMs - firstJoinedMs) / 1000)))
          : "a while";
        summary = `A participant joined ${waited} ago, but the room still has not produced its first relay.`;
        nextAction = "Open one participant agent now and ask it to continue this room, or re-run the room with a managed bridge.";
      } else if (reasons.includes("peer_join_latency_high")) {
        const waited = startSlo.full_join_latency_ms == null
          ? "a long time"
          : formatDurationSeconds(Math.max(0, Math.floor(startSlo.full_join_latency_ms / 1000)));
        summary = `Both participants eventually joined, but the peer-join gap was ${waited}, which is long enough to put an uncertified managed runner at risk before the first relay.`;
        nextAction = "Use a dedicated relay runtime on both sides, or trigger replacement early instead of waiting for the first runner to drift into abandonment.";
      } else if (reasons.includes("first_relay_at_risk")) {
        const waited = startSlo.first_joined_at
          ? formatDurationSeconds(Math.max(0, Math.floor((nowMs - firstJoinedMs) / 1000)))
          : "a while";
        summary = `This managed room has been waiting ${waited} for its first relay and is now at risk of losing the attached runner before work starts.`;
        nextAction = "Watch the current runner closely, or preemptively reissue a repair package before the room drifts into takeover-required.";
      } else if (reasons.includes("runner_lease_low")) {
        const names = lowLeaseAttempts
          .map((attempt) => `${attempt.participant}${attempt.lease_remaining_ms == null ? "" : ` (${Math.max(0, Math.ceil(attempt.lease_remaining_ms / 1000))}s lease left)`}`)
          .join(", ");
        summary = `One or more live runners are close to lease expiry: ${names}.`;
        nextAction = "Renew or replace the runner now so the room does not fall into replacement_pending mid-turn.";
      } else if (reasons.includes("compatibility_room_stalled")) {
        summary = "This compatibility-mode room stopped progressing and ClawRoom does not have a managed runner attached to recover it automatically.";
        nextAction = "Re-open one participant agent and continue the room, or restart the room with a managed bridge.";
      } else if (reasons.includes("no_online_participants")) {
        summary = "All participant agents for this active room are currently offline.";
        nextAction = "Bring one participant back online or take over the room manually before it times out.";
      } else if (reasons.includes("awaiting_mutual_completion")) {
        summary = "One participant already treated the plan as finished, but the room has not reached mutual completion yet.";
        nextAction = "Wait briefly for the counterpart to send DONE, or take over if the room keeps hanging open.";
      } else if (reasons.includes("repair_claim_overdue")) {
        const names = repairClaimOverdueParticipants.join(", ");
        summary = repairClaimOverdueParticipants.length > 1
          ? `Repair packages were already issued for ${names}, but no replacement runner claimed them in time.`
          : `A repair package was already issued for ${names}, but no replacement runner claimed it in time.`;
        nextAction = `Resend the repair package for ${names}, switch that participant to a certified managed runtime, or take over manually before the deadline.`;
      } else if (reasons.includes("owner_reply_overdue")) {
        const names = overdueOwnerAttempts
          .map((attempt) => {
            const ageSeconds = attempt.phase_age_ms == null ? "unknown" : String(Math.max(0, Math.floor(attempt.phase_age_ms / 1000)));
            return `${attempt.participant} (${ageSeconds}s waiting)`;
          })
          .join(", ");
        summary = `The room is still waiting on owner input and the wait has exceeded the configured grace window: ${names}.`;
        nextAction = "Reply in the gateway now, or explicitly take over / cancel so the room does not remain stuck in owner wait.";
      } else if (reasons.includes("replacement_pending")) {
        const names = missingRunnerParticipants.join(", ");
        const automaticNames = missingAutomaticRecoveryParticipants.join(", ");
        const manualNames = missingRunnerParticipants.filter((participant) => !missingAutomaticRecoveryParticipants.includes(participant)).join(", ");
        const issuedNames = issuedRecoveryParticipants.join(", ");
        const notYetIssuedNames = missingRunnerParticipants.filter((participant) => !issuedRecoveryParticipants.includes(participant)).join(", ");
        summary = missingRunnerParticipants.length > 1
          ? `The managed room lost active runners for ${names}.`
          : `The managed room lost the active runner for ${names}.`;
        if (issuedRecoveryParticipants.length > 0 && issuedRecoveryParticipants.length === missingRunnerParticipants.length) {
          nextAction = `A repair package is already issued for ${issuedNames}; verify that runtime executes it, or resend / switch runtimes if no replacement claims soon.`;
        } else if (issuedRecoveryParticipants.length > 0) {
          nextAction = `A repair package is already issued for ${issuedNames}; issue a fresh repair invite for ${notYetIssuedNames} or reopen that runtime before the deadline.`;
        } else if (missingAutomaticRecoveryParticipants.length > 0 && missingAutomaticRecoveryParticipants.length === missingRunnerParticipants.length) {
          nextAction = `Watch for managed replacement to attach a fresh attempt for ${automaticNames}, or take over if the room remains stuck.`;
        } else if (missingAutomaticRecoveryParticipants.length > 0) {
          nextAction = `Managed recovery should replace ${automaticNames}; issue a fresh repair invite for ${manualNames} or reopen that runtime before the deadline.`;
        } else {
          nextAction = `Issue a fresh repair invite for ${names} or reopen that runtime before the deadline.`;
        }
      } else if (reasons.includes("runner_abandoned")) {
        summary = execution.automaticRecoveryEligible
          ? "The active runner lease expired before the room finished, and this room is waiting for managed recovery."
          : "The active runner lease expired before the room finished.";
        nextAction = execution.automaticRecoveryEligible
          ? "Watch for managed recovery to attach a replacement attempt, or take over if the room remains stuck."
          : "Start a replacement runner or re-open the participant runtime before the deadline.";
      } else if (reasons.includes("runner_stalled") || reasons.includes("runner_restarting")) {
        summary = "The managed runner needs attention before this room can keep progressing smoothly.";
        nextAction = "Check runner health/logs and let the system restart or replace the attempt.";
      } else if (reasons.includes("managed_runner_uncertified")) {
        summary = "This room has a managed-style runner attached, but it is not yet a certified automatic-recovery path.";
        nextAction = "Keep this room under observation or move it to a certified managed runtime before relying on unattended recovery.";
      } else if (reasons.includes("required_fields_not_progressing")) {
        summary = `The room has exchanged ${turnCount} turns but none of the required fields have been filled. Agents may be chatting without producing outcomes.`;
        nextAction = "Intervene to refocus the agents on filling the required fields, or take over and close the room.";
      } else if (reasons.includes("required_fields_at_risk")) {
        summary = `The room has ${turnCount} turns and required fields are still empty. Early warning — agents may not be converging on outcomes.`;
        nextAction = "Monitor closely. If no fields are filled in the next 1-2 turns, intervene.";
      } else if (reasons.includes("join_not_started")) {
        summary = "The room is still waiting for participants to actually join.";
        nextAction = "Forward the invite and confirm the guest agent has started the room.";
      } else if (reasons.includes("compatibility_mode")) {
        summary = "This room is running in compatibility mode, so ClawRoom cannot guarantee automatic recovery.";
        nextAction = "Use a managed bridge for stronger continuity, or keep this room under observation.";
      }
      if (!summary && waitingOwner) {
        summary = "The room is waiting on owner input before it can continue.";
      }
      if (!nextAction && waitingOwner) {
        nextAction = "Reply to the waiting agent so the room can resume.";
      }
    }

    return {
      state,
      reasons,
      summary,
      next_action: nextAction,
      takeover_required: state === "takeover_required",
    };
  }

  private ensureSchema(): void {
    if (this.schemaReady) return;
    this.sql.exec(`
		      CREATE TABLE IF NOT EXISTS room (
		        id TEXT PRIMARY KEY,
		        topic TEXT NOT NULL,
		        goal TEXT NOT NULL,
	        required_fields_json TEXT NOT NULL,
        turn_limit INTEGER NOT NULL,
        stall_limit INTEGER NOT NULL,
        timeout_minutes INTEGER NOT NULL,
        ttl_minutes INTEGER NOT NULL,
        status TEXT NOT NULL,
        stop_reason TEXT,
        stop_detail TEXT,
	        created_at TEXT NOT NULL,
	        updated_at TEXT NOT NULL,
	        turn_count INTEGER NOT NULL,
	        stall_count INTEGER NOT NULL,
	        deadline_at TEXT NOT NULL,
		        first_joined_at TEXT,
		        all_joined_at TEXT,
		        first_relay_at TEXT,
		        latest_msg_created_at TEXT,
		        latest_msg_sender TEXT,
		        latest_msg_intent TEXT,
		        latest_msg_expect_reply INTEGER,
		        execution_mode TEXT NOT NULL DEFAULT 'compatibility',
		        last_recovery_reason TEXT,
		        completion_signaled INTEGER NOT NULL DEFAULT 0,
		        expires_at TEXT
		      );

      CREATE TABLE IF NOT EXISTS tokens (
        key TEXT PRIMARY KEY,
        digest TEXT NOT NULL
      );

	      CREATE TABLE IF NOT EXISTS participants (
	        name TEXT PRIMARY KEY,
	        joined INTEGER NOT NULL,
	        joined_at TEXT,
	        participant_token TEXT,
	        online INTEGER NOT NULL,
	        last_seen_at TEXT,
	        done INTEGER NOT NULL,
	        waiting_owner INTEGER NOT NULL,
	        client_name TEXT,
	        runner_id TEXT,
	        runner_attempt_id TEXT,
	        runner_status TEXT,
	        runner_mode TEXT,
	        runner_last_seen_at TEXT,
	        runner_lease_expires_at TEXT,
	        last_runner_error TEXT
	      );

      CREATE TABLE IF NOT EXISTS fields (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        by_participant TEXT NOT NULL
      );

	      CREATE TABLE IF NOT EXISTS events (
	        id INTEGER PRIMARY KEY AUTOINCREMENT,
	        type TEXT NOT NULL,
	        created_at TEXT NOT NULL,
	        audience TEXT NOT NULL,
	        payload_json TEXT NOT NULL
	      );
	      CREATE INDEX IF NOT EXISTS idx_events_audience_id ON events(audience, id);
	      CREATE INDEX IF NOT EXISTS idx_events_type_id ON events(type, id);

      CREATE TABLE IF NOT EXISTS seen_texts (
        text_key TEXT PRIMARY KEY
      );

	      CREATE TABLE IF NOT EXISTS reply_dedup (
	        participant TEXT NOT NULL,
	        in_reply_to_event_id INTEGER NOT NULL,
	        created_at TEXT NOT NULL,
	        PRIMARY KEY (participant, in_reply_to_event_id)
	      );

	      CREATE TABLE IF NOT EXISTS participant_attempts (
	        attempt_id TEXT PRIMARY KEY,
	        participant TEXT NOT NULL,
	        runner_id TEXT NOT NULL,
	        execution_mode TEXT NOT NULL,
	        status TEXT NOT NULL,
	        phase TEXT NOT NULL DEFAULT 'claimed',
	        phase_detail TEXT,
	        phase_updated_at TEXT,
	        capabilities_json TEXT NOT NULL DEFAULT '{}',
	        managed_certified INTEGER NOT NULL DEFAULT 0,
	        recovery_policy TEXT NOT NULL DEFAULT 'takeover_only',
	        log_ref TEXT,
	        claimed_at TEXT NOT NULL,
	        updated_at TEXT NOT NULL,
	        lease_expires_at TEXT,
	        released_at TEXT,
	        restart_count INTEGER NOT NULL DEFAULT 0,
	        last_error TEXT,
	        recovery_reason TEXT
	      );
	      CREATE INDEX IF NOT EXISTS idx_participant_attempts_participant ON participant_attempts(participant, updated_at DESC);
	      CREATE INDEX IF NOT EXISTS idx_participant_attempts_live ON participant_attempts(released_at, lease_expires_at);

      CREATE TABLE IF NOT EXISTS recovery_actions (
        action_id TEXT PRIMARY KEY,
        participant TEXT NOT NULL,
        kind TEXT NOT NULL,
        delivery_mode TEXT NOT NULL DEFAULT 'manual',
        status TEXT NOT NULL,
        reason TEXT NOT NULL,
        summary TEXT,
        package_ready INTEGER NOT NULL DEFAULT 0,
        package_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        issued_at TEXT,
        resolved_at TEXT,
        issue_count INTEGER NOT NULL DEFAULT 0,
        current INTEGER NOT NULL DEFAULT 1
      );
      CREATE INDEX IF NOT EXISTS idx_recovery_actions_current ON recovery_actions(current, participant, updated_at DESC);
	    `);
    this.ensureRoomColumns();
    this.ensureParticipantColumns();
    this.ensureRecoveryActionColumns();
    this.schemaReady = true;
	  }

  private ensureRoomColumns(): void {
    const roomCols = this.sql.exec("PRAGMA table_info(room)").toArray().map((row) => String(row.name || ""));
    if (!roomCols.includes("completion_signaled")) {
      this.sql.exec("ALTER TABLE room ADD COLUMN completion_signaled INTEGER NOT NULL DEFAULT 0");
    }
    if (!roomCols.includes("first_joined_at")) {
      this.sql.exec("ALTER TABLE room ADD COLUMN first_joined_at TEXT");
    }
    if (!roomCols.includes("all_joined_at")) {
      this.sql.exec("ALTER TABLE room ADD COLUMN all_joined_at TEXT");
    }
	    if (!roomCols.includes("first_relay_at")) {
	      this.sql.exec("ALTER TABLE room ADD COLUMN first_relay_at TEXT");
	    }
	    if (!roomCols.includes("latest_msg_created_at")) {
	      this.sql.exec("ALTER TABLE room ADD COLUMN latest_msg_created_at TEXT");
	    }
	    if (!roomCols.includes("latest_msg_sender")) {
	      this.sql.exec("ALTER TABLE room ADD COLUMN latest_msg_sender TEXT");
	    }
	    if (!roomCols.includes("latest_msg_intent")) {
	      this.sql.exec("ALTER TABLE room ADD COLUMN latest_msg_intent TEXT");
	    }
	    if (!roomCols.includes("latest_msg_expect_reply")) {
	      this.sql.exec("ALTER TABLE room ADD COLUMN latest_msg_expect_reply INTEGER");
	    }
	    if (!roomCols.includes("execution_mode")) {
	      this.sql.exec("ALTER TABLE room ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'compatibility'");
	    }
    if (!roomCols.includes("last_recovery_reason")) {
      this.sql.exec("ALTER TABLE room ADD COLUMN last_recovery_reason TEXT");
    }
    if (!roomCols.includes("mission_id")) {
      this.sql.exec("ALTER TABLE room ADD COLUMN mission_id TEXT NOT NULL DEFAULT ''");
    }
    if (!roomCols.includes("assigned_agent")) {
      this.sql.exec("ALTER TABLE room ADD COLUMN assigned_agent TEXT NOT NULL DEFAULT ''");
    }
    const attemptCols = this.sql.exec("PRAGMA table_info(participant_attempts)").toArray().map((row) => String(row.name || ""));
    if (!attemptCols.includes("phase")) {
      this.sql.exec("ALTER TABLE participant_attempts ADD COLUMN phase TEXT NOT NULL DEFAULT 'claimed'");
    }
    if (!attemptCols.includes("phase_detail")) {
      this.sql.exec("ALTER TABLE participant_attempts ADD COLUMN phase_detail TEXT");
    }
    if (!attemptCols.includes("phase_updated_at")) {
      this.sql.exec("ALTER TABLE participant_attempts ADD COLUMN phase_updated_at TEXT");
      this.sql.exec("UPDATE participant_attempts SET phase_updated_at=claimed_at WHERE phase_updated_at IS NULL");
    }
    if (!attemptCols.includes("managed_certified")) {
      this.sql.exec("ALTER TABLE participant_attempts ADD COLUMN managed_certified INTEGER NOT NULL DEFAULT 0");
    }
    if (!attemptCols.includes("recovery_policy")) {
      this.sql.exec("ALTER TABLE participant_attempts ADD COLUMN recovery_policy TEXT NOT NULL DEFAULT 'takeover_only'");
    }
  }

  private ensureParticipantColumns(): void {
    const participantCols = this.sql.exec("PRAGMA table_info(participants)").toArray().map((row) => String(row.name || ""));
    if (!participantCols.includes("joined_at")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN joined_at TEXT");
    }
    if (!participantCols.includes("participant_token")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN participant_token TEXT");
    }
    if (!participantCols.includes("last_seen_at")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN last_seen_at TEXT");
    }
    if (!participantCols.includes("runner_id")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN runner_id TEXT");
    }
    if (!participantCols.includes("runner_attempt_id")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN runner_attempt_id TEXT");
    }
    if (!participantCols.includes("runner_status")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN runner_status TEXT");
    }
    if (!participantCols.includes("runner_mode")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN runner_mode TEXT");
    }
    if (!participantCols.includes("runner_last_seen_at")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN runner_last_seen_at TEXT");
    }
    if (!participantCols.includes("runner_lease_expires_at")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN runner_lease_expires_at TEXT");
    }
    if (!participantCols.includes("last_runner_error")) {
      this.sql.exec("ALTER TABLE participants ADD COLUMN last_runner_error TEXT");
    }
  }

  private ensureRecoveryActionColumns(): void {
    const cols = this.sql.exec("PRAGMA table_info(recovery_actions)").toArray().map((row) => String(row.name || ""));
    if (!cols.includes("delivery_mode")) {
      this.sql.exec("ALTER TABLE recovery_actions ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'manual'");
    }
    if (!cols.includes("package_ready")) {
      this.sql.exec("ALTER TABLE recovery_actions ADD COLUMN package_ready INTEGER NOT NULL DEFAULT 0");
    }
    if (!cols.includes("package_json")) {
      this.sql.exec("ALTER TABLE recovery_actions ADD COLUMN package_json TEXT");
    }
  }

  private onlineStaleSeconds(): number {
    const raw = Number(this.env.PARTICIPANT_ONLINE_STALE_SECONDS ?? 30);
    if (!Number.isFinite(raw)) return 30;
    return Math.max(5, Math.min(300, Math.floor(raw)));
  }

  private attemptStatusPriority(status: AttemptStatus): number {
    switch (status) {
      case "restarting":
        return 90;
      case "stalled":
        return 80;
      case "abandoned":
        return 70;
      case "waiting_owner":
        return 60;
      case "active":
        return 50;
      case "ready":
        return 40;
      case "idle":
        return 30;
      case "replaced":
        return 20;
      case "exited":
        return 10;
      case "pending":
      default:
        return 0;
    }
  }

  private buildStartSlo(roomRow: Record<string, unknown>): RoomSnapshot["start_slo"] {
    const roomCreatedAt = String(roomRow.created_at || "");
    const firstJoinedAt = roomRow.first_joined_at ? String(roomRow.first_joined_at) : null;
    const allJoinedAt = roomRow.all_joined_at ? String(roomRow.all_joined_at) : null;
    const firstRelayAt = roomRow.first_relay_at ? String(roomRow.first_relay_at) : null;
    return {
      room_created_at: roomCreatedAt,
      first_joined_at: firstJoinedAt,
      all_joined_at: allJoinedAt,
      first_relay_at: firstRelayAt,
      join_latency_ms: isoLatencyMs(roomCreatedAt, firstJoinedAt),
      full_join_latency_ms: isoLatencyMs(roomCreatedAt, allJoinedAt),
      first_relay_latency_ms: isoLatencyMs(roomCreatedAt, firstRelayAt),
    };
  }

  private buildRepairHint(
    roomId: string,
    roomRow: Record<string, unknown>,
    participants: Array<Record<string, unknown>>,
    attempts: RunnerAttemptRecord[],
    executionAttention: RoomSnapshot["execution_attention"],
  ): RoomSnapshot["repair_hint"] {
    if (String(roomRow.status || "") !== "active") {
      return {
        available: false,
        strategy: null,
        summary: null,
        endpoint_template: null,
        invalidates_previous_invite: false,
        participants: [],
      };
    }

    const candidates = this.computeRepairCandidates(participants, attempts, executionAttention);

    if (candidates.size === 0) {
      return {
        available: false,
        strategy: null,
        summary: null,
        endpoint_template: null,
        invalidates_previous_invite: false,
        participants: [],
      };
    }

    return {
      available: true,
      strategy: "reissue_invite",
      summary: "Host can issue a fresh repair invite for the participant(s) below and restart a replacement runner.",
      endpoint_template: `/rooms/${roomId}/repair_invites/{participant}`,
      invalidates_previous_invite: true,
      participants: Array.from(candidates.entries()).map(([name, reason]) => ({ name, reason })),
    };
  }

  private deriveRootCauseHints(
    roomRow: Record<string, unknown>,
    participantRows: Array<Record<string, unknown>>,
    attempts: RunnerAttemptRecord[],
    execution: {
      executionMode: ExecutionMode;
      runnerCertification: RunnerCertification;
      automaticRecoveryEligible: boolean;
      attemptStatus: AttemptStatus;
      activeRunnerId: string | null;
      activeRunnerCount: number;
      lastRecoveryReason: string | null;
    },
    executionAttention: RoomSnapshot["execution_attention"],
    startSlo: RoomSnapshot["start_slo"],
  ): RootCauseHint[] {
    const hints: RootCauseHint[] = [];
    const nowMs = Date.now();
    const createdAt = String(roomRow.created_at || "");
    const createdMs = Date.parse(createdAt);
    const firstJoinedAt = String(startSlo.first_joined_at || "");
    const firstJoinedMs = Date.parse(firstJoinedAt);
    const allJoinedAt = String(startSlo.all_joined_at || "");
    const allJoinedMs = Date.parse(allJoinedAt);
    const firstRelayAt = String(startSlo.first_relay_at || "");
    const firstRelayMs = Date.parse(firstRelayAt);
    const joinedParticipants = participantRows
      .filter((participant) => Boolean(participant.joined))
      .map((participant) => String(participant.name || ""))
      .filter(Boolean);
    const unjoinedParticipants = participantRows
      .filter((participant) => !Boolean(participant.joined))
      .map((participant) => String(participant.name || ""))
      .filter(Boolean);
    const onlineParticipants = participantRows
      .filter((participant) => Boolean(participant.online))
      .map((participant) => String(participant.name || ""))
      .filter(Boolean);
    const waitingOwnerParticipants = participantRows
      .filter((participant) => Boolean(participant.waiting_owner))
      .map((participant) => String(participant.name || ""))
      .filter(Boolean);
    const liveCurrentAttemptParticipants = new Set(
      attempts
        .filter((attempt) => attempt.current && LIVE_ATTEMPT_STATUSES.has(attempt.status))
        .map((attempt) => attempt.participant)
        .filter(Boolean)
    );
    const liveCurrentAttempts = attempts.filter((attempt) => attempt.current && LIVE_ATTEMPT_STATUSES.has(attempt.status));
    const latestAttemptByParticipant = new Map<string, RunnerAttemptRecord>();
    for (const attempt of attempts) {
      latestAttemptByParticipant.set(attempt.participant, attempt);
    }
    const missingRunnerParticipants =
      execution.executionMode !== "compatibility"
        ? joinedParticipants.filter((participant) => !liveCurrentAttemptParticipants.has(participant))
        : [];
    const missingRunnerWithoutAttemptParticipants = missingRunnerParticipants.filter(
      (participant) => !latestAttemptByParticipant.has(participant)
    );
    const missingRunnerWithAttemptParticipants = missingRunnerParticipants.filter((participant) =>
      latestAttemptByParticipant.has(participant)
    );
    const pushHint = (hint: RootCauseHint) => {
      if (hints.some((existing) => existing.code === hint.code)) return;
      hints.push(hint);
    };

    if (!Number.isFinite(firstJoinedMs) && unjoinedParticipants.length > 0 && Number.isFinite(createdMs)) {
      const waitSeconds = Math.max(0, Math.floor((nowMs - createdMs) / 1000));
      if (waitSeconds >= this.compatibilityJoinGraceSeconds()) {
        pushHint({
          code: "join_not_completed",
          confidence: "medium",
          summary: "At least one participant never joined after the room was created.",
          evidence: [
            `missing_join=${unjoinedParticipants.join(",")}`,
            `age_seconds=${waitSeconds}`,
          ],
        });
      }
    }

    if (execution.executionMode === "compatibility" && executionAttention.reasons.includes("no_managed_runner")) {
      pushHint({
        code: "compatibility_without_managed_runner",
        confidence: "high",
        summary: "The room is still in compatibility mode without a managed runner attached.",
        evidence: [
          `joined=${joinedParticipants.join(",") || "none"}`,
          `online=${onlineParticipants.join(",") || "none"}`,
        ],
      });
    }

    if (!Number.isFinite(firstRelayMs) && execution.executionMode !== "compatibility" && execution.activeRunnerCount > 0 && Number.isFinite(firstJoinedMs)) {
      const waitSeconds = Math.max(0, Math.floor((nowMs - firstJoinedMs) / 1000));
      if (waitSeconds >= this.managedFirstRelayRiskSeconds()) {
        pushHint({
          code: "first_relay_at_risk",
          confidence: "medium",
          summary: "The room has attached runners, but it is taking unusually long to produce the first relay.",
          evidence: liveCurrentAttempts.map((attempt) => {
            const phaseAgeSeconds = attempt.phase_age_ms == null ? "unknown" : String(Math.max(0, Math.floor(attempt.phase_age_ms / 1000)));
            const leaseRemainingSeconds = attempt.lease_remaining_ms == null ? "unknown" : String(Math.max(0, Math.floor(attempt.lease_remaining_ms / 1000)));
            return `${attempt.participant}:${attempt.phase || "none"}:${attempt.phase_detail || "none"}:phase_age=${phaseAgeSeconds}s:lease_remaining=${leaseRemainingSeconds}s`;
          }),
        });
      }
    }

    if (
      participantRows.length > 1 &&
      joinedParticipants.length === participantRows.length &&
      Number.isFinite(allJoinedMs) &&
      startSlo.full_join_latency_ms != null &&
      startSlo.full_join_latency_ms >= this.peerJoinLatencyRiskSeconds() * 1000 &&
      !Number.isFinite(firstRelayMs)
    ) {
      const latencySeconds = Math.max(0, Math.floor(startSlo.full_join_latency_ms / 1000));
      const waitingAttempts = liveCurrentAttempts.filter((attempt) =>
        attempt.phase === "waiting_for_peer_join" || attempt.phase === "event_polling" || attempt.phase === "relay_seen"
      );
      const missingAttempts = missingRunnerWithAttemptParticipants
        .map((participant) => latestAttemptByParticipant.get(participant))
        .filter((attempt): attempt is RunnerAttemptRecord => Boolean(attempt))
        .filter((attempt) => attempt.phase === "waiting_for_peer_join" || attempt.phase === "event_polling" || attempt.phase === "relay_seen");
      const evidence = [...waitingAttempts, ...missingAttempts].map((attempt) => {
        const phaseAgeSeconds = attempt.phase_age_ms == null ? "unknown" : String(Math.max(0, Math.floor(attempt.phase_age_ms / 1000)));
        return `${attempt.participant}:${attempt.phase || "none"}:${attempt.phase_detail || "none"}:phase_age=${phaseAgeSeconds}s`;
      });
      pushHint({
        code: "peer_join_latency_exceeded_candidate_window",
        confidence: "medium",
        summary: "The peer join gap was long enough that an uncertified managed runner likely spent too much of its survival window waiting for the other side before the first relay.",
        evidence: [
          `full_join_latency_seconds=${latencySeconds}`,
          ...evidence,
        ],
      });
    }

    const lowLeaseAttempts = liveCurrentAttempts.filter((attempt) => {
      if (attempt.lease_remaining_ms == null) return false;
      return attempt.lease_remaining_ms <= this.runnerLeaseLowSeconds() * 1000;
    });
    if (lowLeaseAttempts.length > 0) {
      pushHint({
        code: "runner_lease_low",
        confidence: Number.isFinite(firstRelayMs) ? "low" : "medium",
        summary: "A live runner is close to lease expiry and may drop before the room can finish the current handoff.",
        evidence: lowLeaseAttempts.map((attempt) => {
          const leaseRemainingSeconds = attempt.lease_remaining_ms == null ? "unknown" : String(Math.max(0, Math.floor(attempt.lease_remaining_ms / 1000)));
          return `${attempt.participant}:${attempt.phase || "none"}:${attempt.phase_detail || "none"}:lease_remaining=${leaseRemainingSeconds}s`;
        }),
      });
    }

    if (!Number.isFinite(firstRelayMs) && missingRunnerWithAttemptParticipants.length > 0) {
      const signalEvidence = missingRunnerParticipants
        .map((participant) => {
          const attempt = latestAttemptByParticipant.get(participant);
          const reason = String(attempt?.last_recovery_reason || "");
          if (!reason.startsWith("signal_")) return null;
          return `${participant}:${attempt?.phase || "none"}:${reason}`;
        })
        .filter((value): value is string => Boolean(value));
      if (signalEvidence.length > 0) {
        pushHint({
          code: "runner_received_termination_signal",
          confidence: "high",
          summary:
            "A managed runner reported a termination signal before the room stabilized, suggesting the host runtime or session interrupted it.",
          evidence: signalEvidence,
        });
      }
    }

    const sessionLockAttempts = liveCurrentAttempts.filter((attempt) => {
      const recoveryReason = String(attempt.last_recovery_reason || "");
      const lastError = String(attempt.last_error || "");
      return recoveryReason.includes("session_lock") || lastError.includes("session_file_locked");
    });
    if (sessionLockAttempts.length > 0) {
      pushHint({
        code: "local_session_lock_during_reply_generation",
        confidence: "high",
        summary:
          "A live managed runner hit a local OpenClaw session lock while generating a reply, so the room is contending with another active turn on the same agent runtime.",
        evidence: sessionLockAttempts.map((attempt) => {
          return `${attempt.participant}:${attempt.phase || "none"}:${attempt.phase_detail || "none"}:${attempt.last_recovery_reason || attempt.last_error || "session_lock"}`;
        }),
      });
    }

    const gatewayTimeoutAttempts = liveCurrentAttempts.filter((attempt) => {
      const recoveryReason = String(attempt.last_recovery_reason || "");
      const lastError = String(attempt.last_error || "");
      return recoveryReason.includes("gateway_timeout") || lastError.includes("gateway_timeout");
    });
    if (gatewayTimeoutAttempts.length > 0) {
      pushHint({
        code: "gateway_timeout_during_reply_generation",
        confidence: "high",
        summary:
          "A live managed runner is stuck behind an OpenClaw gateway timeout while generating a reply, so the room is waiting on the runtime rather than on room-state coordination.",
        evidence: gatewayTimeoutAttempts.map((attempt) => {
          return `${attempt.participant}:${attempt.phase || "none"}:${attempt.phase_detail || "none"}:${attempt.last_recovery_reason || attempt.last_error || "gateway_timeout"}`;
        }),
      });
    }

    if (missingRunnerWithAttemptParticipants.length > 0) {
      const explicitRunnerdBuckets = new Map<string, string[]>();
      for (const participant of missingRunnerWithAttemptParticipants) {
        const attempt = latestAttemptByParticipant.get(participant);
        const recoveryReason = String(attempt?.last_recovery_reason || "");
        if (!recoveryReason.startsWith("runnerd_")) continue;
        const bucket = explicitRunnerdBuckets.get(recoveryReason) || [];
        bucket.push(participant);
        explicitRunnerdBuckets.set(recoveryReason, bucket);
      }
      const explicitRunnerdHints: Array<{ code: string; summary: string }> = [
        {
          code: "runnerd_lost_after_claim",
          summary: "A supervised runner crashed after it had already claimed the room attempt.",
        },
        {
          code: "runnerd_restart_exhausted_after_claim",
          summary: "A supervised runner crashed again after runnerd already attempted its automatic restart budget.",
        },
      ];
      for (const hint of explicitRunnerdHints) {
        const participantsForCode = explicitRunnerdBuckets.get(hint.code) || [];
        if (participantsForCode.length === 0) continue;
        pushHint({
          code: hint.code,
          confidence: "high",
          summary: hint.summary,
          evidence: participantsForCode.map((participant) => {
            const attempt = latestAttemptByParticipant.get(participant);
            return `${participant}:${attempt?.phase || "none"}:${attempt?.phase_detail || "none"}:${attempt?.last_recovery_reason || "none"}`;
          }),
        });
      }
    }

    if (!Number.isFinite(firstRelayMs) && missingRunnerWithAttemptParticipants.length > 0) {
      const phaseBuckets = new Map<string, string[]>();
      const expiredPhaseBuckets = new Map<string, string[]>();
      for (const participant of missingRunnerWithAttemptParticipants) {
        const attempt = latestAttemptByParticipant.get(participant);
        const phase = attempt?.phase || "claimed";
        const bucket = phaseBuckets.get(phase) || [];
        bucket.push(participant);
        phaseBuckets.set(phase, bucket);
        if (attempt?.last_recovery_reason === "lease_expired") {
          const expiredBucket = expiredPhaseBuckets.get(phase) || [];
          expiredBucket.push(participant);
          expiredPhaseBuckets.set(phase, expiredBucket);
        }
      }
      const phaseCodes: Array<{ phases: RunnerPhase[]; code: string; summary: string }> = [
        {
          phases: ["claimed", "joined", "session_ready"],
          code: "runner_lost_before_event_poll",
          summary: "A managed runner dropped before it established steady event polling.",
        },
        {
          phases: ["waiting_for_peer_join", "event_polling", "relay_seen"],
          code: "runner_lost_during_relay_wait",
          summary: "A managed runner dropped while waiting for or processing the first relay turn.",
        },
        {
          phases: ["reply_generating", "reply_ready"],
          code: "runner_lost_during_reply_generation",
          summary: "A managed runner dropped while generating the first reply.",
        },
        {
          phases: ["reply_sending", "reply_sent"],
          code: "runner_lost_during_reply_send",
          summary: "A managed runner dropped while sending or immediately after sending the first reply.",
        },
        {
          phases: ["owner_wait", "owner_reply_handled"],
          code: "runner_lost_around_owner_wait",
          summary: "A managed runner dropped around an owner-wait / owner-resume checkpoint before the room stabilized.",
        },
      ];
      for (const phaseCode of phaseCodes) {
        const participantsForCode = phaseCode.phases.flatMap((phase) => phaseBuckets.get(phase) || []);
        if (participantsForCode.length === 0) continue;
        pushHint({
          code: phaseCode.code,
          confidence: "medium",
          summary: phaseCode.summary,
          evidence: participantsForCode.map((participant) => {
            const attempt = latestAttemptByParticipant.get(participant);
            return `${participant}:${attempt?.phase || "none"}:${attempt?.phase_detail || "none"}`;
          }),
        });
      }
      const leaseExpiredPhaseCodes: Array<{ phases: RunnerPhase[]; code: string; summary: string }> = [
        {
          phases: ["claimed", "joined", "session_ready"],
          code: "lease_expired_before_event_poll",
          summary: "A managed runner lease expired before it established steady event polling.",
        },
        {
          phases: ["waiting_for_peer_join", "event_polling", "relay_seen"],
          code: "lease_expired_during_relay_wait",
          summary: "A managed runner lease expired while waiting for peer readiness or the first relay turn.",
        },
        {
          phases: ["reply_generating", "reply_ready"],
          code: "lease_expired_during_reply_generation",
          summary: "A managed runner lease expired while generating the first reply.",
        },
        {
          phases: ["reply_sending", "reply_sent"],
          code: "lease_expired_during_reply_send",
          summary: "A managed runner lease expired while sending or immediately after sending the first reply.",
        },
        {
          phases: ["owner_wait", "owner_reply_handled"],
          code: "lease_expired_around_owner_wait",
          summary: "A managed runner lease expired around an owner-wait / owner-resume checkpoint before the room stabilized.",
        },
      ];
      for (const phaseCode of leaseExpiredPhaseCodes) {
        const participantsForCode = phaseCode.phases.flatMap((phase) => expiredPhaseBuckets.get(phase) || []);
        if (participantsForCode.length === 0) continue;
        pushHint({
          code: phaseCode.code,
          confidence: "high",
          summary: phaseCode.summary,
          evidence: participantsForCode.map((participant) => {
            const attempt = latestAttemptByParticipant.get(participant);
            return `${participant}:${attempt?.phase || "none"}:${attempt?.phase_detail || "none"}`;
          }),
        });
      }
    }

    if (!Number.isFinite(firstRelayMs) && missingRunnerWithoutAttemptParticipants.length > 0) {
      const evidence = missingRunnerWithoutAttemptParticipants.map((participant) => {
        return `${participant}:no_managed_attempt`;
      });
      pushHint({
        code: "managed_runner_never_attached_before_first_relay",
        confidence: "high",
        summary: "A participant joined but never attached a managed runner before the room produced its first relay.",
        evidence,
      });
    }

    if (!Number.isFinite(firstRelayMs) && missingRunnerWithAttemptParticipants.length > 0) {
      const evidence = missingRunnerWithAttemptParticipants.map((participant) => {
        const attempt = latestAttemptByParticipant.get(participant);
        return `${participant}:${attempt?.status || "none"}:${attempt?.phase || "none"}:${attempt?.last_recovery_reason || "none"}`;
      });
      pushHint({
        code: "runner_lost_before_first_relay",
        confidence: "high",
        summary: "A managed runner dropped before the room produced its first relay.",
        evidence,
      });
    }

    if (!Number.isFinite(firstRelayMs) && joinedParticipants.length > 0 && missingRunnerWithAttemptParticipants.length === joinedParticipants.length) {
      pushHint({
        code: "all_runners_lost_before_first_relay",
        confidence: "high",
        summary: "Every joined participant lost its active runner before the room could produce a first relay.",
        evidence: [
          `joined=${joinedParticipants.join(",")}`,
          `missing_runner=${missingRunnerParticipants.join(",")}`,
        ],
      });
    }

    if (!Number.isFinite(firstRelayMs) && execution.lastRecoveryReason === "lease_expired") {
      pushHint({
        code: "lease_expired_before_first_relay",
        confidence: "high",
        summary: "The managed runner lease expired before the room produced its first relay.",
        evidence: [
          `last_recovery_reason=${execution.lastRecoveryReason}`,
          `attempt_status=${execution.attemptStatus}`,
        ],
      });
    }

    if (Number.isFinite(firstRelayMs) && missingRunnerParticipants.length === 1) {
      const participant = missingRunnerParticipants[0];
      const latestAttempt = latestAttemptByParticipant.get(participant);
      if (latestAttempt) {
        pushHint({
          code: "single_sided_runner_loss_after_first_relay",
          confidence: "medium",
          summary: "The room produced a first relay, then one participant lost its active runner while the other side remained alive longer.",
          evidence: [
            `missing_runner=${participant}`,
            `online=${onlineParticipants.join(",") || "none"}`,
          ],
        });
      } else {
        pushHint({
          code: "single_sided_missing_managed_runner_after_first_relay",
          confidence: "high",
          summary: "The room produced a first relay, but one participant never attached a managed runner and dropped back to owner-visible takeover.",
          evidence: [
            `missing_runner=${participant}`,
            `online=${onlineParticipants.join(",") || "none"}`,
          ],
        });
      }
    }

    if (executionAttention.reasons.includes("repair_package_issued")) {
      pushHint({
        code: "repair_package_sent_unclaimed",
        confidence: executionAttention.reasons.includes("repair_claim_overdue") ? "high" : "medium",
        summary: "A repair package was already issued for at least one missing participant, but no replacement runner has claimed it yet.",
        evidence: [
          `attention_reasons=${executionAttention.reasons.join(",")}`,
          `missing_runner=${missingRunnerParticipants.join(",") || "none"}`,
        ],
      });
    }

    if (executionAttention.reasons.includes("repair_claim_overdue")) {
      pushHint({
        code: "repair_claim_overdue",
        confidence: "high",
        summary: "A repair package has remained unclaimed beyond the configured grace window.",
        evidence: [
          `grace_seconds=${this.repairIssuedStaleSeconds()}`,
          `missing_runner=${missingRunnerParticipants.join(",") || "none"}`,
        ],
      });
    }

    const overdueOwnerAttempts = liveCurrentAttempts.filter((attempt) => {
      if (attempt.phase !== "owner_wait") return false;
      if (attempt.phase_age_ms == null) return false;
      return attempt.phase_age_ms >= this.ownerReplyOverdueSeconds() * 1000;
    });

    if (overdueOwnerAttempts.length > 0) {
      pushHint({
        code: "owner_reply_not_returned",
        confidence: "high",
        summary: "The room asked an owner-only question, but no owner reply was returned before the configured grace window expired.",
        evidence: overdueOwnerAttempts.map((attempt) => {
          const phaseAgeSeconds = attempt.phase_age_ms == null ? "unknown" : String(Math.max(0, Math.floor(attempt.phase_age_ms / 1000)));
          return `${attempt.participant}:${attempt.phase || "none"}:${attempt.phase_detail || "none"}:phase_age=${phaseAgeSeconds}s`;
        }),
      });
    } else if (waitingOwnerParticipants.length > 0) {
      pushHint({
        code: "waiting_on_owner_input",
        confidence: "high",
        summary: "The room is explicitly blocked on owner input.",
        evidence: [`participants=${waitingOwnerParticipants.join(",")}`],
      });
    }

    if (execution.runnerCertification === "candidate" && execution.executionMode !== "compatibility") {
      pushHint({
        code: "managed_runtime_uncertified",
        confidence: "medium",
        summary: "The room is running on an uncertified managed runtime, so recovery is still best-effort.",
        evidence: [
          `runner_certification=${execution.runnerCertification}`,
          `automatic_recovery_eligible=${String(execution.automaticRecoveryEligible)}`,
        ],
      });
    }

    return hints.slice(0, 6);
  }

  private computeRepairCandidates(
    participants: Array<Record<string, unknown>>,
    attempts: RunnerAttemptRecord[],
    executionAttention: RoomSnapshot["execution_attention"],
  ): Map<string, string> {
    const reasons = new Set(executionAttention.reasons || []);
    const candidates = new Map<string, string>();
    const joinedParticipants = participants
      .filter((participant) => Boolean(participant.joined))
      .map((participant) => String(participant.name || ""))
      .filter(Boolean);
    const liveCurrentAttemptParticipants = new Set(
      attempts
        .filter((attempt) => attempt.current && LIVE_ATTEMPT_STATUSES.has(attempt.status))
        .map((attempt) => attempt.participant)
        .filter(Boolean)
    );
    const latestAttemptByParticipant = new Map<string, RunnerAttemptRecord>();
    for (const attempt of attempts) {
      latestAttemptByParticipant.set(attempt.participant, attempt);
    }

    if (reasons.has("replacement_pending") || reasons.has("runner_abandoned")) {
      for (const participant of joinedParticipants) {
        if (!liveCurrentAttemptParticipants.has(participant)) {
          const latestAttempt = latestAttemptByParticipant.get(participant);
          const reason =
            !latestAttempt
              ? "no_managed_runner"
              : latestAttempt && ["abandoned", "stalled", "restarting", "exited"].includes(latestAttempt.status)
              ? "runner_stalled_or_abandoned"
              : "replacement_pending";
          candidates.set(participant, reason);
        }
      }
    }

    if (candidates.size === 0 && reasons.has("no_managed_runner")) {
      for (const participant of joinedParticipants) {
        candidates.set(participant, "no_managed_runner");
      }
    }

    return candidates;
  }

  private loadRecoveryActionRows(currentOnly = true): Array<Record<string, unknown>> {
    const sql = currentOnly
      ? "SELECT * FROM recovery_actions WHERE current=1 ORDER BY created_at ASC, action_id ASC"
      : "SELECT * FROM recovery_actions ORDER BY created_at ASC, action_id ASC";
    return this.sql.exec(sql).toArray() as Array<Record<string, unknown>>;
  }

  private loadRecoveryActions(currentOnly = true): RoomSnapshot["recovery_actions"] {
    const rows = this.loadRecoveryActionRows(currentOnly);
    return rows.map((row) => ({
      action_id: String(row.action_id || ""),
      participant: String(row.participant || ""),
      kind: String(row.kind || "repair_invite_reissue") as RecoveryActionKind,
      delivery_mode: String(row.delivery_mode || "manual") as RecoveryDeliveryMode,
      status: String(row.status || "pending") as RecoveryActionStatus,
      reason: String(row.reason || ""),
      summary: row.summary ? String(row.summary) : null,
      package_ready: Boolean(row.package_ready),
      created_at: String(row.created_at || ""),
      updated_at: String(row.updated_at || ""),
      issued_at: row.issued_at ? String(row.issued_at) : null,
      resolved_at: row.resolved_at ? String(row.resolved_at) : null,
      issue_count: Number(row.issue_count || 0),
      current: Boolean(row.current),
    }));
  }

  private loadRecoveryActionsForHost(roomId: string): Array<RoomSnapshot["recovery_actions"][number] & { package: Record<string, unknown> | null }> {
    const rows = this.loadRecoveryActionRows(false);
    const apiBase = this.recoveryApiBase();
    return rows.map((row) => {
      const packagePayload = row.package_json ? normalizeObjectRecord(JSON.parse(String(row.package_json))) : null;
      const normalizedPackage = packagePayload
        ? {
            room_id: roomId,
            participant: String(row.participant || ""),
            invalidates_previous_invite: true,
            invite_token: String(packagePayload.invite_token || ""),
            join_link: String(packagePayload.join_link || this.buildJoinLink(apiBase, roomId, String(packagePayload.invite_token || ""))),
            repair_command: String(packagePayload.repair_command || ""),
            issued_source: String(packagePayload.issued_source || "manual"),
            issued_at: String(packagePayload.issued_at || row.issued_at || ""),
          }
        : null;
      return {
        action_id: String(row.action_id || ""),
        participant: String(row.participant || ""),
        kind: String(row.kind || "repair_invite_reissue") as RecoveryActionKind,
        delivery_mode: String(row.delivery_mode || "manual") as RecoveryDeliveryMode,
        status: String(row.status || "pending") as RecoveryActionStatus,
        reason: String(row.reason || ""),
        summary: row.summary ? String(row.summary) : null,
        package_ready: Boolean(row.package_ready),
        created_at: String(row.created_at || ""),
        updated_at: String(row.updated_at || ""),
        issued_at: row.issued_at ? String(row.issued_at) : null,
        resolved_at: row.resolved_at ? String(row.resolved_at) : null,
        issue_count: Number(row.issue_count || 0),
        current: Boolean(row.current),
        package: normalizedPackage,
      };
    });
  }

  private recoveryApiBase(origin?: string): string {
    const configured = String(this.env.CLAWROOM_PUBLIC_API_BASE || "").trim();
    return (configured || origin || DEFAULT_PUBLIC_API_BASE).replace(/\/+$/, "");
  }

  private buildJoinLink(baseUrl: string, roomId: string, inviteToken: string): string {
    return `${baseUrl}/join/${roomId}?token=${encodeURIComponent(inviteToken)}`;
  }

  private async rotateInviteToken(participant: string): Promise<string> {
    const inviteToken = `inv_${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
    const inviteDigest = await sha256Hex(inviteToken);
    this.sql.exec("UPDATE tokens SET digest=? WHERE key=?", inviteDigest, `invite:${participant}`);
    return inviteToken;
  }

  private latestAttemptByParticipant(attempts: RunnerAttemptRecord[]): Map<string, RunnerAttemptRecord> {
    const latestByParticipant = new Map<string, RunnerAttemptRecord>();
    for (const attempt of attempts) {
      latestByParticipant.set(attempt.participant, attempt);
    }
    return latestByParticipant;
  }

  private recoveryDeliveryModeForParticipant(participant: string, attempts: RunnerAttemptRecord[]): RecoveryDeliveryMode {
    const latestAttempt = this.latestAttemptByParticipant(attempts).get(participant);
    return latestAttempt?.managed_certified && latestAttempt?.recovery_policy === "automatic" ? "automatic" : "manual";
  }

  private shouldImmediatelyPrepareManualRecovery(participant: string, attempts: RunnerAttemptRecord[]): boolean {
    const latestAttempt = this.latestAttemptByParticipant(attempts).get(participant);
    const recoveryReason = String(latestAttempt?.last_recovery_reason || "");
    if (!recoveryReason) return false;
    return IMMEDIATE_MANUAL_REPAIR_REASONS.has(recoveryReason);
  }

  private getCurrentRecoveryAction(participant: string): Record<string, unknown> | null {
    const rows = this.sql
      .exec("SELECT * FROM recovery_actions WHERE participant=? AND current=1 ORDER BY updated_at DESC LIMIT 1", participant)
      .toArray() as Array<Record<string, unknown>>;
    return rows.length > 0 ? rows[0] : null;
  }

  private ensureCurrentRecoveryAction(
    participant: string,
    reason: string,
    summary: string,
    deliveryMode: RecoveryDeliveryMode,
  ): Record<string, unknown> {
    const now = nowIso();
    const existing = this.getCurrentRecoveryAction(participant);
    if (existing) {
      const existingStatus = String(existing.status || "pending") as RecoveryActionStatus;
      const nextStatus: RecoveryActionStatus =
        existingStatus === "issued" || existingStatus === "pending" ? existingStatus : "pending";
      this.sql.exec(
        `UPDATE recovery_actions
         SET status=?, reason=?, summary=?, delivery_mode=?, updated_at=?, current=1
         WHERE action_id=?`,
        nextStatus,
        reason,
        summary,
        deliveryMode,
        now,
        String(existing.action_id || ""),
      );
      this.recoveryStateDirty = true;
      return { ...existing, status: nextStatus, reason, summary, delivery_mode: deliveryMode };
    }
    const actionId = `raction_${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
    this.sql.exec(
      `INSERT INTO recovery_actions (action_id, participant, kind, delivery_mode, status, reason, summary, created_at, updated_at, current)
       VALUES (?, ?, 'repair_invite_reissue', ?, 'pending', ?, ?, ?, ?, 1)`,
      actionId,
      participant,
      deliveryMode,
      reason,
      summary,
      now,
      now,
    );
    this.recoveryStateDirty = true;
    return {
      action_id: actionId,
      participant,
      kind: "repair_invite_reissue",
      delivery_mode: deliveryMode,
      status: "pending",
      reason,
      summary,
      created_at: now,
      updated_at: now,
      current: true,
    };
  }

  private async issueRecoveryPackage(
    roomId: string,
    participant: string,
    actionId: string,
    deliveryMode: RecoveryDeliveryMode,
    source: "manual" | "automatic",
    origin?: string,
  ): Promise<{ invite_token: string; join_link: string; repair_command: string }> {
    const issuedAt = nowIso();
    const inviteToken = await this.rotateInviteToken(participant);
    const apiBase = this.recoveryApiBase(origin);
    const joinLink = this.buildJoinLink(apiBase, roomId, inviteToken);
    const repairCommand = this.shellRepairCommand(joinLink);
    const packagePayload = {
      invite_token: inviteToken,
      join_link: joinLink,
      repair_command: repairCommand,
      issued_source: source,
      issued_at: issuedAt,
    };
    this.sql.exec(
      `UPDATE recovery_actions
       SET status='issued',
           delivery_mode=?,
           package_ready=1,
           package_json=?,
           issued_at=?,
           updated_at=?,
           issue_count=issue_count+1,
           current=1
       WHERE action_id=?`,
      deliveryMode,
      JSON.stringify(packagePayload),
      issuedAt,
      issuedAt,
      actionId,
    );
    this.recoveryStateDirty = true;
    return {
      invite_token: inviteToken,
      join_link: joinLink,
      repair_command: repairCommand,
    };
  }

  private async maybeAutoIssueRecoveryActions(roomId: string, attempts: RunnerAttemptRecord[]): Promise<boolean> {
    const rows = this.sql.exec("SELECT * FROM recovery_actions WHERE current=1 AND status='pending'").toArray() as Array<Record<string, unknown>>;
    let changed = false;
    for (const row of rows) {
      const participant = String(row.participant || "");
      if (!participant) continue;
      if (this.recoveryDeliveryModeForParticipant(participant, attempts) !== "automatic") continue;
      const actionId = String(row.action_id || "");
      if (!actionId) continue;
      await this.issueRecoveryPackage(roomId, participant, actionId, "automatic", "automatic");
      this.setRoomRecovery(`automatic_recovery_package_issued:${participant}`);
      await this.appendEvent("*", "recovery_action_issued", {
        participant,
        action_id: actionId,
        delivery_mode: "automatic",
      });
      changed = true;
    }
    return changed;
  }

  private async maybePrepareManualRecoveryActions(roomId: string, attempts: RunnerAttemptRecord[]): Promise<boolean> {
    const nowMs = Date.now();
    const graceMs = this.manualRepairPrepareSeconds() * 1000;
    const liveCurrentAttemptParticipants = new Set(
      attempts
        .filter((attempt) => attempt.current && LIVE_ATTEMPT_STATUSES.has(attempt.status))
        .map((attempt) => attempt.participant)
        .filter(Boolean)
    );
    const rows = this.sql.exec("SELECT * FROM recovery_actions WHERE current=1 AND status='pending'").toArray() as Array<Record<string, unknown>>;
    let changed = false;
    for (const row of rows) {
      const participant = String(row.participant || "");
      if (!participant) continue;
      if (String(row.delivery_mode || "manual") !== "manual") continue;
      if (liveCurrentAttemptParticipants.has(participant)) continue;
      const immediate = this.shouldImmediatelyPrepareManualRecovery(participant, attempts);
      const createdAt = String(row.created_at || "");
      const createdMs = Date.parse(createdAt);
      if (!immediate && (!Number.isFinite(createdMs) || nowMs - createdMs < graceMs)) continue;
      const actionId = String(row.action_id || "");
      if (!actionId) continue;
      await this.issueRecoveryPackage(roomId, participant, actionId, "manual", "manual");
      this.setRoomRecovery(immediate ? `manual_repair_package_prepared_immediate:${participant}` : `manual_repair_package_prepared:${participant}`);
      await this.appendEvent("*", "recovery_action_issued", {
        participant,
        action_id: actionId,
        delivery_mode: "manual",
        package_ready: true,
        prepared_by_system: true,
        prepared_immediately: immediate,
      });
      changed = true;
    }
    return changed;
  }

  private syncRecoveryActions(
    participants: Array<Record<string, unknown>>,
    attempts: RunnerAttemptRecord[],
    executionAttention: RoomSnapshot["execution_attention"],
  ): boolean {
    const candidates = this.computeRepairCandidates(participants, attempts, executionAttention);
    const now = nowIso();
    const rows = this.sql.exec("SELECT * FROM recovery_actions WHERE current=1").toArray() as Array<Record<string, unknown>>;
    const currentByParticipant = new Map<string, Record<string, unknown>>();
    for (const row of rows) {
      const participant = String(row.participant || "");
      if (participant) currentByParticipant.set(participant, row);
    }
    let changed = false;

    for (const [participant, reason] of candidates.entries()) {
      const summary = `Replacement runner still needed for ${participant}.`;
      const deliveryMode = this.recoveryDeliveryModeForParticipant(participant, attempts);
      const existing = currentByParticipant.get(participant);
      if (!existing) {
        const actionId = `raction_${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
        this.sql.exec(
          `INSERT INTO recovery_actions (action_id, participant, kind, delivery_mode, status, reason, summary, created_at, updated_at, current)
           VALUES (?, ?, 'repair_invite_reissue', ?, 'pending', ?, ?, ?, ?, 1)`,
          actionId,
          participant,
          deliveryMode,
          reason,
          summary,
          now,
          now,
        );
        changed = true;
        continue;
      }
      const existingStatus = String(existing.status || "pending") as RecoveryActionStatus;
      const existingReason = String(existing.reason || "");
      const existingSummary = existing.summary ? String(existing.summary) : null;
      const existingDeliveryMode = String(existing.delivery_mode || "manual") as RecoveryDeliveryMode;
      const nextStatus: RecoveryActionStatus =
        existingStatus === "issued" || existingStatus === "pending" ? existingStatus : "pending";
      if (existingStatus !== nextStatus || existingReason !== reason || existingSummary !== summary || existingDeliveryMode !== deliveryMode) {
        this.sql.exec(
          `UPDATE recovery_actions
           SET status=?, reason=?, summary=?, delivery_mode=?, updated_at=?, current=1
           WHERE action_id=?`,
          nextStatus,
          reason,
          summary,
          deliveryMode,
          now,
          String(existing.action_id || ""),
        );
        changed = true;
      }
    }

    for (const row of rows) {
      const participant = String(row.participant || "");
      if (!participant || candidates.has(participant)) continue;
      this.sql.exec(
        `UPDATE recovery_actions
         SET status='resolved', resolved_at=COALESCE(resolved_at, ?), updated_at=?, current=0
         WHERE action_id=?`,
        now,
        now,
        String(row.action_id || ""),
      );
      changed = true;
    }

    if (changed) this.recoveryStateDirty = true;
    return changed;
  }

  private markRecoveryActionIssued(participant: string): void {
    const now = nowIso();
    this.sql.exec(
      `UPDATE recovery_actions
       SET status='issued', issued_at=?, updated_at=?, issue_count=issue_count+1
       WHERE participant=? AND current=1`,
      now,
      now,
      participant,
    );
    this.recoveryStateDirty = true;
  }

  private resolveRecoveryAction(participant: string): Record<string, unknown> | null {
    const existing = this.getCurrentRecoveryAction(participant);
    if (!existing) return null;
    const now = nowIso();
    this.sql.exec(
      `UPDATE recovery_actions
       SET status='resolved', resolved_at=COALESCE(resolved_at, ?), updated_at=?, current=0
       WHERE participant=? AND current=1`,
      now,
      now,
      participant,
    );
    this.recoveryStateDirty = true;
    return existing;
  }

  private loadAttemptRecords(participantRows: any[]): RunnerAttemptRecord[] {
    const currentAttemptByParticipant = new Map<string, string>();
    for (const row of participantRows) {
      const participant = String(row.name || "");
      const attemptId = row.runner_attempt_id ? String(row.runner_attempt_id) : "";
      if (participant && attemptId) currentAttemptByParticipant.set(participant, attemptId);
    }
    const nowTs = Date.now();
    const rows: Array<Record<string, unknown>> = [];
    const seenAttemptIds = new Set<string>();
    for (const participantRow of participantRows) {
      const participant = String(participantRow.name || "");
      if (!participant) continue;
      const currentAttemptId = currentAttemptByParticipant.get(participant) || "";
      if (currentAttemptId) {
        const currentRows = this.sql.exec(
          "SELECT * FROM participant_attempts WHERE attempt_id=? LIMIT 1",
          currentAttemptId,
        ).toArray() as Record<string, unknown>[];
        const currentRow = currentRows.length ? (currentRows[0] as Record<string, unknown>) : null;
        if (currentRow) {
          rows.push(currentRow);
          seenAttemptIds.add(String(currentRow.attempt_id || ""));
        }
      }
      const latestRows = this.sql.exec(
        "SELECT * FROM participant_attempts WHERE participant=? ORDER BY updated_at DESC, claimed_at DESC LIMIT 1",
        participant,
      ).toArray() as Record<string, unknown>[];
      const latestRow = latestRows.length ? (latestRows[0] as Record<string, unknown>) : null;
      if (latestRow) {
        const latestAttemptId = String(latestRow.attempt_id || "");
        if (!seenAttemptIds.has(latestAttemptId)) {
          rows.push(latestRow);
          seenAttemptIds.add(latestAttemptId);
        }
      }
    }
    return rows
      .sort((a, b) => {
        const aClaimed = Date.parse(String(a.claimed_at || "")) || 0;
        const bClaimed = Date.parse(String(b.claimed_at || "")) || 0;
        if (aClaimed !== bClaimed) return aClaimed - bClaimed;
        return String(a.attempt_id || "").localeCompare(String(b.attempt_id || ""));
      })
      .map((row) => {
      const capabilities = normalizeObjectRecord(row.capabilities_json ? JSON.parse(String(row.capabilities_json)) : {});
      const replacementCount = Math.max(0, Number(capabilities.replacement_count || 0));
      const supersedesRunId = trimmedString(capabilities.supersedes_run_id, 120) || null;
      const rawStatus = normalizeAttemptStatus(row.status);
      const releasedAt = row.released_at ? String(row.released_at) : null;
      const leaseExpiresAt = row.lease_expires_at ? String(row.lease_expires_at) : null;
      const phaseUpdatedAt = row.phase_updated_at ? String(row.phase_updated_at) : String(row.updated_at || row.claimed_at || nowIso());
      const phaseUpdatedMs = Date.parse(phaseUpdatedAt);
      const leaseExpiresMs = leaseExpiresAt ? Date.parse(leaseExpiresAt) : NaN;
      let status = rawStatus;
      if (!releasedAt && leaseExpiresAt) {
        if (Number.isFinite(leaseExpiresMs) && leaseExpiresMs <= nowTs && LIVE_ATTEMPT_STATUSES.has(rawStatus)) {
          status = "abandoned";
        }
      }
      return {
        attempt_id: String(row.attempt_id || ""),
        participant: String(row.participant || ""),
        runner_id: String(row.runner_id || ""),
        execution_mode: normalizeExecutionMode(row.execution_mode),
        status,
        claimed_at: String(row.claimed_at || ""),
        updated_at: String(row.updated_at || ""),
        lease_expires_at: leaseExpiresAt,
        released_at: releasedAt,
        restart_count: Number(row.restart_count || 0),
        log_ref: row.log_ref ? String(row.log_ref) : null,
        last_error: row.last_error ? String(row.last_error) : null,
        last_recovery_reason: row.recovery_reason ? String(row.recovery_reason) : status === "abandoned" ? "lease_expired" : null,
        phase: normalizeRunnerPhase(row.phase),
        phase_detail: row.phase_detail ? String(row.phase_detail) : null,
        phase_updated_at: phaseUpdatedAt,
        phase_age_ms: Number.isFinite(phaseUpdatedMs) ? Math.max(0, nowTs - phaseUpdatedMs) : null,
        lease_remaining_ms: Number.isFinite(leaseExpiresMs) ? leaseExpiresMs - nowTs : null,
        capabilities,
        replacement_count: replacementCount,
        supersedes_run_id: supersedesRunId,
        managed_certified: Boolean(row.managed_certified),
        recovery_policy: normalizeRecoveryPolicy(row.recovery_policy),
        current: currentAttemptByParticipant.get(String(row.participant || "")) === String(row.attempt_id || ""),
      };
      });
  }

  private summarizeExecution(
    roomRow: Record<string, unknown>,
    participantRows: Array<Record<string, unknown>>,
    attempts: RunnerAttemptRecord[]
  ): {
    executionMode: ExecutionMode;
    runnerCertification: RunnerCertification;
    managedCoverage: ManagedCoverage;
    supervisionOrigins: string[];
    productOwned: boolean;
    automaticRecoveryEligible: boolean;
    attemptStatus: AttemptStatus;
    activeRunnerId: string | null;
    activeRunnerCount: number;
    lastRecoveryReason: string | null;
  } {
    const storedMode = normalizeExecutionMode(roomRow.execution_mode);
    const roomStatus = String(roomRow.status || "active");
    const liveAttempts = attempts.filter((attempt) => LIVE_ATTEMPT_STATUSES.has(attempt.status));
    const participantNames = participantRows
      .map((row) => String(row.name || "").trim())
      .filter(Boolean);
    const allParticipantsJoined =
      participantNames.length > 0
        && participantRows.every((row) => Boolean(row.joined));
    const coverageAttempts =
      roomStatus === "closed"
        ? attempts.filter((attempt) => attempt.execution_mode !== "compatibility")
        : liveAttempts.filter((attempt) => attempt.execution_mode !== "compatibility");
    const executionMode =
      storedMode !== "compatibility"
        ? storedMode
        : liveAttempts.find((attempt) => attempt.execution_mode !== "compatibility")?.execution_mode
            ?? attempts.find((attempt) => attempt.execution_mode !== "compatibility")?.execution_mode
            ?? "compatibility";
    const managedCoverageByParticipant = new Set(
      coverageAttempts
        .map((attempt) => attempt.participant)
        .filter(Boolean)
    );
    const CERTIFIED_SUPERVISION_ORIGINS = new Set(["runnerd", "direct"]);
    const certifiedCoverageByParticipant = new Set(
      coverageAttempts
        .filter((attempt) => {
          const supervisionOrigin = String(attempt.capabilities?.supervision_origin || "").trim().toLowerCase();
          return attempt.managed_certified && CERTIFIED_SUPERVISION_ORIGINS.has(supervisionOrigin);
        })
        .map((attempt) => attempt.participant)
        .filter(Boolean)
    );
    const autoRecoveryCoverageByParticipant = new Set(
      coverageAttempts
        .filter((attempt) => {
          const supervisionOrigin = String(attempt.capabilities?.supervision_origin || "").trim().toLowerCase();
          return (
            attempt.managed_certified
            && CERTIFIED_SUPERVISION_ORIGINS.has(supervisionOrigin)
            && attempt.recovery_policy === "automatic"
          );
        })
        .map((attempt) => attempt.participant)
        .filter(Boolean)
    );
    const supervisionOrigins = Array.from(
      new Set(
        coverageAttempts
          .map((attempt) => String(attempt.capabilities?.supervision_origin || "").trim().toLowerCase())
          .filter(Boolean)
      )
    ).sort();
    const coveredParticipantCount = participantNames.filter((name) => managedCoverageByParticipant.has(name)).length;
    const managedCoverage =
      coveredParticipantCount <= 0
        ? "none"
        : coveredParticipantCount >= participantNames.length && participantNames.length > 0
          ? "full"
          : "partial";
    const certifiedCoverageCount = participantNames.filter((name) => certifiedCoverageByParticipant.has(name)).length;
    const autoRecoveryCoverageCount = participantNames.filter((name) => autoRecoveryCoverageByParticipant.has(name)).length;
    const runnerCertification =
      managedCoverage === "none"
        ? "none"
        : certifiedCoverageCount >= participantNames.length && participantNames.length > 0
          ? "certified"
          : "candidate";
    const automaticRecoveryEligible =
      participantNames.length > 0
      && autoRecoveryCoverageCount >= participantNames.length;
    const productOwned =
      executionMode !== "compatibility"
      && managedCoverage === "full"
      && allParticipantsJoined
      && participantNames.length > 0
      && certifiedCoverageCount >= participantNames.length
      && automaticRecoveryEligible;
    const statusSource = liveAttempts.length > 0 ? liveAttempts : attempts;
    const statuses = statusSource.map((attempt) => attempt.status);
    const attemptStatus =
      statuses.sort((a, b) => this.attemptStatusPriority(b) - this.attemptStatusPriority(a))[0] ?? "pending";
    const activeRunnerIds = Array.from(new Set(liveAttempts.map((attempt) => attempt.runner_id).filter(Boolean)));
    const recoveryReason =
      (roomRow.last_recovery_reason ? String(roomRow.last_recovery_reason) : null)
      ?? [...attempts]
        .reverse()
        .map((attempt) => attempt.last_recovery_reason)
        .find(Boolean)
      ?? null;
    return {
      executionMode,
      runnerCertification,
      managedCoverage,
      supervisionOrigins,
      productOwned,
      automaticRecoveryEligible,
      attemptStatus,
      activeRunnerId: activeRunnerIds.length ? activeRunnerIds.join(",") : null,
      activeRunnerCount: activeRunnerIds.length,
      lastRecoveryReason: recoveryReason,
    };
  }

  private scheduleActiveAlarm(roomRow: Record<string, unknown>): Promise<void> {
    const deadlineMs = Date.parse(String(roomRow.deadline_at || ""));
    const leaseRows = this.sql
      .exec(
        "SELECT lease_expires_at FROM participant_attempts WHERE released_at IS NULL AND lease_expires_at IS NOT NULL ORDER BY lease_expires_at ASC LIMIT 1"
      )
      .toArray();
    const leaseIso = leaseRows.length ? String((leaseRows[0] as Record<string, unknown>).lease_expires_at || "") : "";
    const leaseMs = Date.parse(leaseIso);
    const presenceRows = this.sql
      .exec(
        "SELECT last_seen_at FROM participants WHERE online=1 AND last_seen_at IS NOT NULL ORDER BY last_seen_at ASC LIMIT 1"
      )
      .toArray();
    const presenceIso = presenceRows.length ? String((presenceRows[0] as Record<string, unknown>).last_seen_at || "") : "";
    const presenceBaseMs = Date.parse(presenceIso);
    const presenceAlarmMs =
      Number.isFinite(presenceBaseMs) ? presenceBaseMs + this.onlineStaleSeconds() * 1000 : NaN;
    const candidates = [deadlineMs, leaseMs, presenceAlarmMs].filter((value) => Number.isFinite(value) && value > 0) as number[];
    if (!candidates.length) return Promise.resolve();
    return this.state.storage.setAlarm(Math.min(...candidates));
  }

  private updateParticipantRunnerPointer(
    participant: string,
    input: {
      runnerId?: string | null;
      attemptId?: string | null;
      status?: AttemptStatus | null;
      executionMode?: ExecutionMode | null;
      runnerLastSeenAt?: string | null;
      leaseExpiresAt?: string | null;
      lastError?: string | null;
    }
  ): void {
    this.invalidateSnapshotCache();
    this.sql.exec(
      `UPDATE participants
       SET runner_id=?, runner_attempt_id=?, runner_status=?, runner_mode=?, runner_last_seen_at=?, runner_lease_expires_at=?, last_runner_error=?
       WHERE name=?`,
      input.runnerId ?? null,
      input.attemptId ?? null,
      input.status ?? null,
      input.executionMode ?? null,
      input.runnerLastSeenAt ?? null,
      input.leaseExpiresAt ?? null,
      input.lastError ?? null,
      participant
    );
  }

  private setRoomRecovery(reason: string | null): void {
    this.invalidateSnapshotCache();
    this.sql.exec("UPDATE room SET last_recovery_reason=? WHERE id IS NOT NULL", reason ? String(reason).slice(0, 240) : null);
  }

  private async reconcileRunnerAttempts(roomId: string, options?: { debounceMs?: number }): Promise<string[]> {
    const debounceMs = Math.max(0, Number(options?.debounceMs || 0));
    const nowMs = Date.now();
    if (debounceMs > 0 && nowMs - this.lastHotPathRunnerReconcileAtMs < debounceMs) {
      this.diagnostics.hot_path_reconcile_skipped += 1;
      return [];
    }
    this.lastHotPathRunnerReconcileAtMs = nowMs;
    const now = nowIso();
    const rows = this.sql
      .exec(
        "SELECT attempt_id, participant, runner_id, status, lease_expires_at FROM participant_attempts WHERE released_at IS NULL AND lease_expires_at IS NOT NULL"
      )
      .toArray();
    const changedTypes: string[] = [];
    for (const row of rows) {
      const status = normalizeAttemptStatus(row.status);
      if (!LIVE_ATTEMPT_STATUSES.has(status)) continue;
      const leaseExpiresAt = String(row.lease_expires_at || "");
      const leaseMs = Date.parse(leaseExpiresAt);
      if (!Number.isFinite(leaseMs) || leaseMs > Date.now()) continue;
      const attemptId = String(row.attempt_id || "");
      const participant = String(row.participant || "");
      const runnerId = String(row.runner_id || "");
      this.sql.exec(
        `UPDATE participant_attempts
         SET status='abandoned', updated_at=?, released_at=?, recovery_reason=COALESCE(recovery_reason, 'lease_expired')
         WHERE attempt_id=? AND released_at IS NULL`,
        now,
        now,
        attemptId
      );
      this.invalidateSnapshotCache();
      this.updateParticipantRunnerPointer(participant, {
        runnerId: null,
        attemptId: null,
        status: "abandoned",
        executionMode: null,
        runnerLastSeenAt: now,
        leaseExpiresAt: leaseExpiresAt,
        lastError: "lease_expired",
      });
      this.setRoomRecovery("lease_expired");
      await this.appendEvent("*", "runner_abandoned", {
        participant,
        attempt_id: attemptId,
        runner_id: runnerId,
        reason: "lease_expired",
      });
      changedTypes.push("runner_abandoned");
    }
    if (changedTypes.length > 0) {
      const roomRow = this.sql.exec("SELECT * FROM room WHERE id=? LIMIT 1", roomId).one() as Record<string, unknown> | null;
      if (roomRow && String(roomRow.status || "") === "active") {
        await this.scheduleActiveAlarm(roomRow);
      }
    }
    return changedTypes;
  }

  private async closeExpiredRoomIfNeeded(roomId: string, options?: { debounceMs?: number }): Promise<void> {
    const debounceMs = Math.max(0, Number(options?.debounceMs || 0));
    const nowMs = Date.now();
    if (debounceMs > 0 && nowMs - this.lastHotPathExpiryCheckAtMs < debounceMs) {
      this.diagnostics.hot_path_expiry_check_skipped += 1;
      return;
    }
    this.lastHotPathExpiryCheckAtMs = nowMs;
    const row = this.sql.exec("SELECT status, deadline_at FROM room WHERE id=? LIMIT 1", roomId).one();
    if (!row || String(row.status || "") !== "active") return;
    const deadlineMs = Date.parse(String(row.deadline_at || ""));
    if (!Number.isFinite(deadlineMs) || Date.now() < deadlineMs) return;
    const changed = await this.closeRoom("timeout", "deadline exceeded");
    if (!changed) return;
    const snapshot = await this.snapshot(roomId);
    await this.publishRoomSnapshot("timeout", snapshot);
  }

  private touchParticipant(name: string): boolean {
    const nowMs = Date.now();
    const debounceMs = this.participantTouchDebounceSeconds() * 1000;
    const cached = this.participantTouchCache.get(name);
    if (cached && cached.online && nowMs - cached.lastPersistedMs < debounceMs) {
      this.diagnostics.participant_touch_skipped += 1;
      return false;
    }
    const persisted = this.sql.exec("SELECT online, last_seen_at FROM participants WHERE name=? LIMIT 1", name).one() as Record<string, unknown> | null;
    const persistedOnline = Boolean(persisted?.online);
    const persistedLastSeenMs = persisted?.last_seen_at ? Date.parse(String(persisted.last_seen_at)) : NaN;
    if (persistedOnline && Number.isFinite(persistedLastSeenMs) && nowMs - persistedLastSeenMs < debounceMs) {
      this.participantTouchCache.set(name, { online: true, lastPersistedMs: persistedLastSeenMs });
      this.diagnostics.participant_touch_skipped += 1;
      return false;
    }
    const stampedAt = new Date(nowMs).toISOString();
    this.sql.exec("UPDATE participants SET online=1, last_seen_at=? WHERE name=?", stampedAt, name);
    this.participantTouchCache.set(name, { online: true, lastPersistedMs: nowMs });
    this.invalidateSnapshotCache();
    this.diagnostics.participant_touch_persisted += 1;
    return !persistedOnline;
  }

  private registryStub(): DurableObjectStub | null {
    const namespace = this.env.ROOM_REGISTRY;
    if (!namespace) return null;
    const id = namespace.idFromName("global");
    return namespace.get(id);
  }

  private shouldEmitIncidentLog(eventType: string, room: RoomSnapshot): boolean {
    if (room.execution_attention.state !== "healthy") return true;
    if (eventType.startsWith("runner_")) return true;
    if (eventType.startsWith("repair_")) return true;
    if (eventType.startsWith("recovery_")) return true;
    if (eventType === "timeout" || eventType === "close" || eventType === "result_ready") return true;
    return false;
  }

  private emitIncidentLog(eventType: string, room: RoomSnapshot): void {
    if (!this.shouldEmitIncidentLog(eventType, room)) return;
    const primaryRootCause = room.root_cause_hints[0] || null;
    const recoverySummary = room.recovery_actions.reduce(
      (acc, action) => {
        if (!action.current) return acc;
        if (action.status === "pending") acc.pending += 1;
        else if (action.status === "issued") acc.issued += 1;
        else if (action.status === "resolved") acc.resolved += 1;
        return acc;
      },
      { pending: 0, issued: 0, resolved: 0 }
    );
    const payload = {
      log_type: "clawroom_room_incident",
      event_type: eventType,
      room_id: room.id,
      status: room.status,
      lifecycle_state: room.lifecycle_state,
      execution_mode: room.execution_mode,
      managed_coverage: room.managed_coverage,
      product_owned: room.product_owned,
      runner_certification: room.runner_certification,
      automatic_recovery_eligible: room.automatic_recovery_eligible,
      attempt_status: room.attempt_status,
      active_runner_count: room.active_runner_count,
      active_runner_id: room.active_runner_id,
      last_recovery_reason: room.last_recovery_reason,
      execution_attention: room.execution_attention,
      primary_root_cause: primaryRootCause
        ? {
            code: primaryRootCause.code,
            confidence: primaryRootCause.confidence,
            summary: primaryRootCause.summary,
            evidence: primaryRootCause.evidence,
          }
        : null,
      root_cause_hints: room.root_cause_hints.map((hint) => ({
        code: hint.code,
        confidence: hint.confidence,
        summary: hint.summary,
      })),
      recovery_actions: recoverySummary,
      repair_hint_available: room.repair_hint.available,
      repair_hint_participants: room.repair_hint.participants.map((participant) => ({
        name: participant.name,
        reason: participant.reason,
      })),
      start_slo: room.start_slo,
      turn_count: room.turn_count,
      participants: room.participants.map((participant) => ({
        name: participant.name,
        joined: participant.joined,
        online: participant.online,
        waiting_owner: participant.waiting_owner,
        done: participant.done,
        client_name: participant.client_name,
      })),
    };
    console.log(JSON.stringify(payload));
  }

  private async publishRoomSnapshot(eventType: string, room: RoomSnapshot): Promise<void> {
    this.emitIncidentLog(eventType, room);
    if (this.shouldSkipRegistryPublish(eventType, room)) {
      this.diagnostics.registry_publish_skipped += 1;
      this.onlineStateDirty = false;
      this.recoveryStateDirty = false;
      return;
    }
    this.diagnostics.registry_publish_sent += 1;
    const stub = this.registryStub();
    if (!stub) {
      this.onlineStateDirty = false;
      this.recoveryStateDirty = false;
      return;
    }
    const req = new Request("https://registry/internal/upsert", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ event_type: eventType, room })
    });
    try {
      const res = await stub.fetch(req);
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        console.warn(
          `[clawroom] registry upsert failed event=${eventType} room=${room.id} status=${res.status} body=${body.slice(0, 240)}`
        );
      }
    } catch {
      // Non-blocking observability path.
      console.warn(`[clawroom] registry upsert threw event=${eventType} room=${room.id}`);
    } finally {
      this.onlineStateDirty = false;
      this.recoveryStateDirty = false;
    }
  }

  private async flushDerivedRoomState(room: RoomSnapshot): Promise<void> {
    const eventType = this.recoveryStateDirty ? "recovery_reconciled" : "presence_reconciled";
    if (!this.onlineStateDirty && !this.recoveryStateDirty) return;
    this.onlineStateDirty = false;
    this.recoveryStateDirty = false;
    await this.publishRoomSnapshot(eventType, room);
  }

  private async removeRoomFromRegistry(roomId: string, reason: string): Promise<void> {
    const stub = this.registryStub();
    if (!stub) return;
    const req = new Request("https://registry/internal/remove", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ room_id: roomId, reason })
    });
    try {
      const res = await stub.fetch(req);
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        console.warn(
          `[clawroom] registry remove failed room=${roomId} status=${res.status} body=${body.slice(0, 240)}`
        );
      }
    } catch {
      // Non-blocking observability path.
      console.warn(`[clawroom] registry remove threw room=${roomId}`);
    }
  }

  private reconcileOnlineState(): boolean {
    const staleCutoff = Date.now() - this.onlineStaleSeconds() * 1000;
    const rows = this.sql.exec("SELECT name, last_seen_at FROM participants WHERE online=1").toArray();
    let changed = false;
    for (const row of rows) {
      const participant = String(row.name || "");
      const lastSeenRaw = row.last_seen_at ? String(row.last_seen_at) : "";
      const lastSeenTs = Date.parse(lastSeenRaw);
      const isStale = !lastSeenRaw || !Number.isFinite(lastSeenTs) || lastSeenTs < staleCutoff;
      if (participant && isStale) {
        this.sql.exec("UPDATE participants SET online=0 WHERE name=?", participant);
        this.participantTouchCache.set(participant, { online: false, lastPersistedMs: Number.isFinite(lastSeenTs) ? lastSeenTs : Date.now() });
        this.invalidateSnapshotCache();
        changed = true;
      }
    }
    return changed;
  }

  private async handleInit(request: Request): Promise<Response> {
    this.ensureSchema();
    this.invalidateSnapshotCache();
    const payload = (await request.json().catch(() => ({}))) as any;
    const roomId = String(payload?.room_id || "").trim();
    const create = payload?.create as RoomCreateIn;
    const defaults = payload?.defaults as Partial<RoomConfig>;

    if (!roomId) return badRequest("missing room_id");
    if (!create || typeof create !== "object") return badRequest("missing create payload");

    const topic = String(create.topic || "").trim();
    const goal = String(create.goal || "").trim();
    const participants = Array.isArray(create.participants) ? create.participants.map((p) => String(p).trim()).filter(Boolean) : [];
    if (!topic) return badRequest("topic required");
    if (!goal) return badRequest("goal required");
    if (participants.length < 2) return badRequest("participants must be >=2");
    if (participants.length > 8) return badRequest("participants must be <=8");

    const requiredFieldsInput = parseOutcomeList(create.required_fields);
    const expectedOutcomesInput = parseOutcomeList(create.expected_outcomes);
    const hasRequiredFields = Array.isArray(create.required_fields);
    const hasExpectedOutcomes = Array.isArray(create.expected_outcomes);

    if (hasRequiredFields && hasExpectedOutcomes) {
      const requiredSig = outcomeSignature(requiredFieldsInput);
      const expectedSig = outcomeSignature(expectedOutcomesInput);
      if (requiredSig !== expectedSig) {
        return badRequest("required_fields and expected_outcomes conflict", {
          error_code: "outcomes_conflict"
        });
      }
    }

    const requiredFields = hasExpectedOutcomes ? expectedOutcomesInput : requiredFieldsInput;

    const turnLimit = Number.isFinite(create.turn_limit) ? Number(create.turn_limit) : Number(defaults?.turn_limit || 12);
    const stallLimit = Number.isFinite(create.stall_limit) ? Number(create.stall_limit) : Number(defaults?.stall_limit || 3);
    const timeoutMinutes = Number.isFinite(create.timeout_minutes)
      ? Number(create.timeout_minutes)
      : Number(defaults?.timeout_minutes || 20);
    const ttlMinutes = Number.isFinite(create.ttl_minutes) ? Number(create.ttl_minutes) : Number(defaults?.ttl_minutes || 60);

    const createdAt = nowIso();
    const deadlineAt = new Date(Date.now() + timeoutMinutes * 60_000).toISOString();

    const existing = this.sql.exec("SELECT id FROM room WHERE id = ? LIMIT 1", roomId).toArray();
    if (existing.length) return conflict("room already exists");

    const hostToken = `host_${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
    const hostDigest = await sha256Hex(hostToken);

    const inviteTokens: Record<string, string> = {};
    const inviteDigests: Record<string, string> = {};
    for (const p of participants) {
      const token = `inv_${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
      inviteTokens[p] = token;
      inviteDigests[p] = await sha256Hex(token);
    }

    const missionId = String(create.mission_id || "");
    const assignedAgent = String(create.assigned_agent || "");

    this.sql.exec(
      `INSERT INTO room (id, topic, goal, required_fields_json, turn_limit, stall_limit, timeout_minutes, ttl_minutes, status, stop_reason, stop_detail, created_at, updated_at, turn_count, stall_count, deadline_at, expires_at, mission_id, assigned_agent)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL, ?, ?, 0, 0, ?, NULL, ?, ?)`,
      roomId,
      topic,
      goal,
      JSON.stringify(requiredFields),
      Math.max(2, Math.min(500, Math.floor(turnLimit))),
      Math.max(1, Math.min(200, Math.floor(stallLimit))),
      Math.max(1, Math.min(1440, Math.floor(timeoutMinutes))),
      Math.max(1, Math.min(1440, Math.floor(ttlMinutes))),
      createdAt,
      createdAt,
      deadlineAt,
      missionId,
      assignedAgent
    );

    this.sql.exec("INSERT INTO tokens (key, digest) VALUES ('host', ?) ", hostDigest);
    for (const p of participants) {
      this.sql.exec("INSERT INTO tokens (key, digest) VALUES (?, ?)", `invite:${p}`, inviteDigests[p]);
      this.sql.exec(
        "INSERT INTO participants (name, joined, online, last_seen_at, done, waiting_owner, client_name) VALUES (?, 0, 0, NULL, 0, 0, NULL)",
        p
      );
    }

    await this.appendEvent("*", "status", { status: "active" });

	    await this.scheduleActiveAlarm({
	      id: roomId,
	      deadline_at: deadlineAt,
	      status: "active",
	    });

    // Build convenience URLs (relative — caller prepends their own base)
    const joinLinks: Record<string, string> = {};
    for (const [name, token] of Object.entries(inviteTokens)) {
      joinLinks[name] = `/join/${roomId}?token=${encodeURIComponent(token)}`;
    }
    const monitorLink = `/?room_id=${encodeURIComponent(roomId)}&host_token=${encodeURIComponent(hostToken)}`;

    const snapshot: RoomSnapshot = {
      id: roomId,
      topic,
      goal,
      protocol_version: PROTOCOL_VERSION,
      capabilities: [...ROOM_CAPABILITIES],
      execution_mode: "compatibility",
      runner_certification: "none",
      managed_coverage: "none",
      supervision_origins: [],
      product_owned: false,
      automatic_recovery_eligible: false,
      attempt_status: "pending",
      active_runner_id: null,
      active_runner_count: 0,
      last_recovery_reason: null,
      execution_attention: {
        state: "healthy",
        reasons: [],
        summary: null,
        next_action: null,
        takeover_required: false,
      },
      root_cause_hints: [],
      repair_hint: {
        available: false,
        strategy: null,
        summary: null,
        endpoint_template: null,
        invalidates_previous_invite: false,
        participants: [],
      },
      recovery_actions: [],
      start_slo: {
        room_created_at: createdAt,
        first_joined_at: null,
        all_joined_at: null,
        first_relay_at: null,
        join_latency_ms: null,
        full_join_latency_ms: null,
        first_relay_latency_ms: null,
      },
      lifecycle_state: "working",
      required_fields: [...requiredFields],
      expected_outcomes: [...requiredFields],
      fields: {},
      status: "active",
      stop_reason: null,
      stop_detail: null,
      created_at: createdAt,
      updated_at: createdAt,
      turn_count: 0,
      stall_count: 0,
      deadline_at: deadlineAt,
      participants: participants.map((name) => ({
        name,
        joined: false,
        joined_at: null,
        online: false,
        last_seen_at: null,
        done: false,
        waiting_owner: false,
        client_name: null,
      })),
      runner_attempts: [],
    };
    await this.publishRoomSnapshot("init", snapshot);

    return json({
      room: snapshot,
      host_token: hostToken,
      invites: inviteTokens,
      join_links: joinLinks,
      monitor_link: monitorLink,
      config: { turn_limit: turnLimit, stall_limit: stallLimit, timeout_minutes: timeoutMinutes, ttl_minutes: ttlMinutes }
    });
  }

  private async requireHost(request: Request): Promise<void> {
    const token = request.headers.get("X-Host-Token") || new URL(request.url).searchParams.get("host_token") || "";
    if (!token) {
      await this.drainRequestBodyIfPresent(request);
      throw unauthorized("missing host token");
    }
    const digest = await sha256Hex(token);
    const row = this.sql.exec("SELECT digest FROM tokens WHERE key='host' LIMIT 1").one();
    if (!row || String(row.digest) !== digest) {
      await this.drainRequestBodyIfPresent(request);
      throw unauthorized("invalid host token");
    }
  }

  private async drainRequestBodyIfPresent(request: Request): Promise<void> {
    if (request.bodyUsed) return;
    const method = request.method.toUpperCase();
    if (method === "GET" || method === "HEAD") return;
    try {
      await request.arrayBuffer();
    } catch {
      // Some runtimes may already have closed the stream; safe to ignore.
    }
  }

  private async resolveParticipantByToken(
    token: string,
    allowedKinds: Array<"invite" | "participant"> = ["invite"]
  ): Promise<{ name: string; joined: boolean }> {
    if (!token) throw unauthorized("missing invite token");
    const digest = await sha256Hex(token);
    const patterns = allowedKinds.map((kind) => `${kind}:%`);
    const where = patterns.map(() => "key LIKE ?").join(" OR ");
    const rows = this.sql.exec(`SELECT key, digest FROM tokens WHERE ${where}`, ...patterns).toArray();
    for (const r of rows) {
      if (String(r.digest) === digest) {
        const key = String(r.key);
        const prefix = allowedKinds
          .map((kind) => `${kind}:`)
          .find((candidate) => key.startsWith(candidate));
        if (!prefix) continue;
        const name = key.slice(prefix.length);
        const participantRow = this.sql.exec("SELECT joined FROM participants WHERE name=? LIMIT 1", name).one();
        return { name, joined: Boolean(participantRow?.joined) };
      }
    }
    throw unauthorized("invalid invite token");
  }

  private async authenticateJoinInvite(request: Request): Promise<{ name: string; joined: boolean }> {
    const token = request.headers.get("X-Invite-Token") || "";
    return await this.resolveParticipantByToken(token, ["invite"]);
  }

  private async authenticateParticipant(request: Request): Promise<{ name: string; joined: boolean }> {
    const participantToken = request.headers.get("X-Participant-Token") || "";
    const inviteToken = request.headers.get("X-Invite-Token") || "";
    const tokens = [participantToken, inviteToken].filter(Boolean);
    if (!tokens.length) throw unauthorized("missing participant token");
    for (const token of tokens) {
      try {
        return await this.resolveParticipantByToken(token, ["participant", "invite"]);
      } catch {
        continue;
      }
    }
    throw unauthorized("invalid participant token");
  }

  private async requireParticipant(request: Request, options?: { joined?: boolean }): Promise<string> {
    let participant: { name: string; joined: boolean };
    try {
      participant = await this.authenticateParticipant(request);
    } catch (err) {
      await this.drainRequestBodyIfPresent(request);
      throw err;
    }
    const joinedRequired = Boolean(options?.joined);
    if (joinedRequired && !participant.joined) {
      await this.drainRequestBodyIfPresent(request);
      throw conflict("participant not joined");
    }
    return participant.name;
  }

  private async requireParticipantFromQuery(request: Request, options?: { joined?: boolean }): Promise<string> {
    const url = new URL(request.url);
    const token =
      url.searchParams.get("participant_token")
      || url.searchParams.get("invite_token")
      || url.searchParams.get("token")
      || "";
    const participant = await this.resolveParticipantByToken(token, ["participant", "invite"]);
    const joinedRequired = Boolean(options?.joined);
    if (joinedRequired && !participant.joined) throw conflict("participant not joined");
    return participant.name;
  }

  private async handleGetRoom(request: Request, roomId: string): Promise<Response> {
    // Allow either participant token or host token.
    try {
      await this.authenticateParticipant(request);
    } catch {
      await this.requireHost(request);
    }
    const room = await this.snapshot(roomId);
    await this.flushDerivedRoomState(room);
    return json({ room });
  }

  /**
   * GET /rooms/:id/join_info?token=inv_...
   * Lightweight introspection: returns room snapshot + participant name.
   * Used by the bridge to self-configure from a single join URL.
   */
  private async handleJoinInfo(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    const token = new URL(request.url).searchParams.get("token") || "";
    if (!token) throw unauthorized("missing token query param");
    const participantName = (await this.resolveParticipantByToken(token)).name;
    const room = await this.snapshot(roomId);
    await this.flushDerivedRoomState(room);
    return json({ participant: participantName, room });
  }

  private async handleJoin(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
      this.invalidateSnapshotCache();
	    const participantAuth = await this.authenticateJoinInvite(request);
	    const participant = participantAuth.name;
	    const body = (await request.json().catch(() => ({}))) as any;
	    const clientName = typeof body?.client_name === "string" ? body.client_name.slice(0, 120) : null;
	    const joinedAt = nowIso();
      const participantRow = this.sql.exec(
        "SELECT joined, participant_token FROM participants WHERE name=? LIMIT 1",
        participant
      ).toArray()[0] as Record<string, unknown> | undefined;
      const existingParticipantToken = participantRow?.participant_token ? String(participantRow.participant_token) : "";
      const participantToken = existingParticipantToken || `ptok_${crypto.randomUUID().replace(/-/g, "")}`;
      const participantDigest = await sha256Hex(participantToken);

	    this.sql.exec(
	      "UPDATE participants SET joined=1, joined_at=COALESCE(joined_at, ?), participant_token=COALESCE(participant_token, ?), online=1, last_seen_at=?, client_name=? WHERE name=?",
        joinedAt,
        participantToken,
	      joinedAt,
	      clientName,
	      participant
	    );
      const participantTokenKey = `participant:${participant}`;
      const existingParticipantTokenKey = this.sql.exec("SELECT key FROM tokens WHERE key=? LIMIT 1", participantTokenKey).toArray();
      if (existingParticipantTokenKey.length) {
        this.sql.exec("UPDATE tokens SET digest=? WHERE key=?", participantDigest, participantTokenKey);
      } else {
        this.sql.exec("INSERT INTO tokens (key, digest) VALUES (?, ?)", participantTokenKey, participantDigest);
      }
	    this.sql.exec("UPDATE room SET first_joined_at=COALESCE(first_joined_at, ?) WHERE id=?", joinedAt, roomId);
      const joinedCount = Number(
        (this.sql.exec("SELECT COUNT(*) AS count FROM participants WHERE joined=1").one() as Record<string, unknown> | null)?.count || 0
      );
      const participantCount = Number(
        (this.sql.exec("SELECT COUNT(*) AS count FROM participants").one() as Record<string, unknown> | null)?.count || 0
      );
      if (participantCount > 0 && joinedCount >= participantCount) {
        this.sql.exec("UPDATE room SET all_joined_at=COALESCE(all_joined_at, ?) WHERE id=?", joinedAt, roomId);
      }
	    await this.appendEvent("*", "join", { participant, client_name: clientName });

	    const snapshot = await this.snapshot(roomId);
    await this.publishRoomSnapshot("join", snapshot);
    return json({ participant, participant_token: participantToken, room: snapshot });
  }

  private async handleRunnerClaim(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    this.invalidateSnapshotCache();
    const participant = await this.requireParticipant(request, { joined: true });
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const runnerId = trimmedString(body.runner_id, 120);
    if (!runnerId) return badRequest("runner_id required");

    const now = nowIso();
    const executionMode = normalizeExecutionMode(body.execution_mode);
    let status = normalizeAttemptStatus(body.status || "ready");
    if (status === "pending" || status === "replaced" || status === "exited" || status === "abandoned") {
      status = "ready";
    }
    const leaseExpiresAt = new Date(Date.now() + clampLeaseSeconds(body.lease_seconds) * 1000).toISOString();
    const logRef = trimmedString(body.log_ref, 500) || null;
    const lastError = trimmedString(body.last_error, 500) || null;
    const recoveryReason = trimmedString(body.recovery_reason, 240) || null;
    const phase = normalizeRunnerPhase(body.phase);
    const phaseDetail = trimmedString(body.phase_detail, 240) || null;
    const phaseUpdatedAt = now;
    const capabilities = normalizeObjectRecord(body.capabilities);
    const managedCertified = Boolean(body.managed_certified ?? capabilities.managed_certified);
    const recoveryPolicy = normalizeRecoveryPolicy(body.recovery_policy ?? capabilities.recovery_policy);
    const explicitAttemptId = trimmedString(body.attempt_id, 120);

    const activeRows = this.sql
      .exec(
        "SELECT attempt_id, runner_id FROM participant_attempts WHERE participant=? AND released_at IS NULL ORDER BY claimed_at DESC",
        participant
      )
      .toArray() as Record<string, unknown>[];
    const existingSameRunner = activeRows.find((row) => String(row.runner_id || "") === runnerId);
    const attemptId = explicitAttemptId || (existingSameRunner ? String(existingSameRunner.attempt_id || "") : `rattempt_${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`);
    const replaced: Array<{ attempt_id: string; runner_id: string }> = [];

    for (const row of activeRows) {
      const activeAttemptId = String(row.attempt_id || "");
      const activeRunnerId = String(row.runner_id || "");
      if (!activeAttemptId || activeAttemptId === attemptId) continue;
      this.sql.exec(
        `UPDATE participant_attempts
         SET status='replaced', updated_at=?, released_at=?, recovery_reason=COALESCE(recovery_reason, 'runner_replaced')
         WHERE attempt_id=?`,
        now,
        now,
        activeAttemptId
      );
      replaced.push({ attempt_id: activeAttemptId, runner_id: activeRunnerId });
      await this.appendEvent("*", "runner_replaced", {
        participant,
        attempt_id: activeAttemptId,
        runner_id: activeRunnerId,
        replacement_runner_id: runnerId,
      });
    }

    const existingAttemptRows = this.sql
      .exec("SELECT attempt_id FROM participant_attempts WHERE attempt_id=? LIMIT 1", attemptId)
      .toArray();
    if (existingAttemptRows.length) {
      this.sql.exec(
        `UPDATE participant_attempts
         SET participant=?, runner_id=?, execution_mode=?, status=?, phase=?, phase_detail=?, phase_updated_at=?, capabilities_json=?, managed_certified=?, recovery_policy=?, log_ref=?, updated_at=?, lease_expires_at=?, released_at=NULL, last_error=?, recovery_reason=?
         WHERE attempt_id=?`,
        participant,
        runnerId,
        executionMode,
        status,
        phase,
        phaseDetail,
        phaseUpdatedAt,
        JSON.stringify(capabilities),
        managedCertified ? 1 : 0,
        recoveryPolicy,
        logRef,
        now,
        leaseExpiresAt,
        lastError,
        recoveryReason,
        attemptId
      );
    } else {
      this.sql.exec(
        `INSERT INTO participant_attempts (
          attempt_id, participant, runner_id, execution_mode, status, phase, phase_detail, phase_updated_at, capabilities_json, managed_certified, recovery_policy, log_ref,
          claimed_at, updated_at, lease_expires_at, released_at, restart_count, last_error, recovery_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?)`,
        attemptId,
        participant,
        runnerId,
        executionMode,
        status,
        phase,
        phaseDetail,
        phaseUpdatedAt,
        JSON.stringify(capabilities),
        managedCertified ? 1 : 0,
        recoveryPolicy,
        logRef,
        now,
        now,
        leaseExpiresAt,
        lastError,
        recoveryReason
      );
    }

    this.updateParticipantRunnerPointer(participant, {
      runnerId,
      attemptId,
      status,
      executionMode,
      runnerLastSeenAt: now,
      leaseExpiresAt,
      lastError,
    });
    const resolvedRecoveryAction = this.resolveRecoveryAction(participant);
    this.sql.exec("UPDATE room SET execution_mode=? WHERE id=?", executionMode, roomId);
    if (recoveryReason) this.setRoomRecovery(recoveryReason);
    else if (replaced.length > 0) this.setRoomRecovery("runner_replaced");
    if (resolvedRecoveryAction) {
      const issuedAt = resolvedRecoveryAction.issued_at ? String(resolvedRecoveryAction.issued_at) : null;
      const claimLatencyMs = issuedAt ? isoLatencyMs(issuedAt, now) : null;
      await this.appendEvent("*", "recovery_action_resolved", {
        participant,
        action_id: String(resolvedRecoveryAction.action_id || ""),
        previous_status: String(resolvedRecoveryAction.status || "pending"),
        attempt_id: attemptId,
        runner_id: runnerId,
        issued_at: issuedAt,
        claim_latency_ms: claimLatencyMs,
      });
    }
    await this.appendEvent("*", "runner_claim", {
      participant,
      attempt_id: attemptId,
      runner_id: runnerId,
      execution_mode: executionMode,
      status,
      phase,
      phase_detail: phaseDetail,
      phase_updated_at: phaseUpdatedAt,
      managed_certified: managedCertified,
      recovery_policy: recoveryPolicy,
      lease_expires_at: leaseExpiresAt,
    });
    await this.appendRunnerCheckpointEvent({
      participant,
      attempt_id: attemptId,
      runner_id: runnerId,
      status,
      phase,
      phase_detail: phaseDetail,
      phase_updated_at: phaseUpdatedAt,
    });

    const roomRow = this.sql.exec("SELECT * FROM room WHERE id=? LIMIT 1", roomId).one() as Record<string, unknown> | null;
    if (roomRow && String(roomRow.status || "") === "active") {
      await this.scheduleActiveAlarm(roomRow);
    }
    const snapshot = await this.snapshot(roomId);
    await this.publishRoomSnapshot(replaced.length > 0 ? "runner_replaced" : "runner_claim", snapshot);
    return json({
      participant,
      attempt_id: attemptId,
      replaced_runner_ids: replaced.map((entry) => entry.runner_id),
      room: snapshot,
    });
  }

  private async handleRunnerRenew(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    this.invalidateSnapshotCache();
    const participant = await this.requireParticipant(request, { joined: true });
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const participantRow = this.sql.exec("SELECT runner_id, runner_attempt_id FROM participants WHERE name=? LIMIT 1", participant).one();
    const requestedRunnerId = trimmedString(body.runner_id, 120) || (participantRow?.runner_id ? String(participantRow.runner_id) : "");
    const requestedAttemptId = trimmedString(body.attempt_id, 120) || (participantRow?.runner_attempt_id ? String(participantRow.runner_attempt_id) : "");
    if (!requestedRunnerId && !requestedAttemptId) return badRequest("runner_id or attempt_id required");

    const criteriaAttempt = requestedAttemptId
      ? this.sql.exec(
          "SELECT * FROM participant_attempts WHERE participant=? AND attempt_id=? LIMIT 1",
          participant,
          requestedAttemptId
        ).toArray()
      : [];
    const criteriaRunner =
      criteriaAttempt.length > 0
        ? criteriaAttempt
        : this.sql.exec(
            "SELECT * FROM participant_attempts WHERE participant=? AND runner_id=? ORDER BY claimed_at DESC LIMIT 1",
            participant,
            requestedRunnerId
          ).toArray();
    if (!criteriaRunner.length) throw conflict("runner attempt not found");
    const existing = criteriaRunner[0] as Record<string, unknown>;
    const attemptId = String(existing.attempt_id || "");
    const runnerId = String(existing.runner_id || requestedRunnerId);
    const executionMode = normalizeExecutionMode(body.execution_mode || existing.execution_mode);
    let status = normalizeAttemptStatus(body.status || existing.status || "active");
    if (status === "pending" || status === "replaced" || status === "exited" || status === "abandoned") {
      status = "active";
    }
    const now = nowIso();
    const leaseExpiresAt = new Date(Date.now() + clampLeaseSeconds(body.lease_seconds) * 1000).toISOString();
    const logRef = trimmedString(body.log_ref, 500) || (existing.log_ref ? String(existing.log_ref) : null);
    const lastError =
      trimmedString(body.last_error, 500) || (existing.last_error ? String(existing.last_error).slice(0, 500) : null);
    const recoveryReason =
      trimmedString(body.recovery_reason, 240) || (existing.recovery_reason ? String(existing.recovery_reason).slice(0, 240) : null);
    const previousPhase = normalizeRunnerPhase(existing.phase);
    const previousPhaseDetail = existing.phase_detail ? String(existing.phase_detail) : null;
    const requestedPhase = body.phase === undefined ? previousPhase : normalizeRunnerPhase(body.phase);
    const phaseDetail =
      body.phase_detail === undefined
        ? previousPhaseDetail
        : (trimmedString(body.phase_detail, 240) || null);
    const phaseUpdatedAt =
      requestedPhase !== previousPhase || phaseDetail !== previousPhaseDetail
        ? now
        : (existing.phase_updated_at ? String(existing.phase_updated_at) : now);
    const capabilities = Object.keys(normalizeObjectRecord(body.capabilities)).length
      ? normalizeObjectRecord(body.capabilities)
      : normalizeObjectRecord(existing.capabilities_json ? JSON.parse(String(existing.capabilities_json)) : {});
    const managedCertified = Boolean(body.managed_certified ?? existing.managed_certified ?? capabilities.managed_certified);
    const recoveryPolicy = normalizeRecoveryPolicy(body.recovery_policy ?? existing.recovery_policy ?? capabilities.recovery_policy);

    this.sql.exec(
      `UPDATE participant_attempts
       SET runner_id=?, execution_mode=?, status=?, phase=?, phase_detail=?, phase_updated_at=?, capabilities_json=?, managed_certified=?, recovery_policy=?, log_ref=?, updated_at=?, lease_expires_at=?, released_at=NULL, last_error=?, recovery_reason=?
       WHERE attempt_id=?`,
      runnerId,
      executionMode,
      status,
      requestedPhase,
      phaseDetail,
      phaseUpdatedAt,
      JSON.stringify(capabilities),
      managedCertified ? 1 : 0,
      recoveryPolicy,
      logRef,
      now,
      leaseExpiresAt,
      lastError,
      recoveryReason,
      attemptId
    );
    this.updateParticipantRunnerPointer(participant, {
      runnerId,
      attemptId,
      status,
      executionMode,
      runnerLastSeenAt: now,
      leaseExpiresAt,
      lastError,
    });
    this.resolveRecoveryAction(participant);
    this.sql.exec("UPDATE room SET execution_mode=? WHERE id=?", executionMode, roomId);
    if (recoveryReason) this.setRoomRecovery(recoveryReason);
    await this.appendEvent("*", "runner_renew", {
      participant,
      attempt_id: attemptId,
      runner_id: runnerId,
      status,
      phase: requestedPhase,
      phase_detail: phaseDetail,
      phase_updated_at: phaseUpdatedAt,
      managed_certified: managedCertified,
      recovery_policy: recoveryPolicy,
      lease_expires_at: leaseExpiresAt,
    });
    if (requestedPhase !== previousPhase || phaseDetail !== previousPhaseDetail) {
      await this.appendRunnerCheckpointEvent({
        participant,
        attempt_id: attemptId,
        runner_id: runnerId,
        status,
        phase: requestedPhase,
        phase_detail: phaseDetail,
        phase_updated_at: phaseUpdatedAt,
      });
    }
    const roomRow = this.sql.exec("SELECT * FROM room WHERE id=? LIMIT 1", roomId).one() as Record<string, unknown> | null;
    if (roomRow && String(roomRow.status || "") === "active") {
      await this.scheduleActiveAlarm(roomRow);
    }
    const snapshot = await this.snapshot(roomId);
    await this.publishRoomSnapshot("runner_renew", snapshot);
    return json({ participant, attempt_id: attemptId, room: snapshot });
  }

  private async handleRunnerRelease(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    this.invalidateSnapshotCache();
    const participant = await this.requireParticipant(request, { joined: true });
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const participantRow = this.sql.exec("SELECT runner_id, runner_attempt_id FROM participants WHERE name=? LIMIT 1", participant).one();
    const requestedRunnerId = trimmedString(body.runner_id, 120) || (participantRow?.runner_id ? String(participantRow.runner_id) : "");
    const requestedAttemptId = trimmedString(body.attempt_id, 120) || (participantRow?.runner_attempt_id ? String(participantRow.runner_attempt_id) : "");
    if (!requestedRunnerId && !requestedAttemptId) return badRequest("runner_id or attempt_id required");

    const rows = requestedAttemptId
      ? this.sql.exec(
          "SELECT * FROM participant_attempts WHERE participant=? AND attempt_id=? LIMIT 1",
          participant,
          requestedAttemptId
        ).toArray()
      : this.sql.exec(
          "SELECT * FROM participant_attempts WHERE participant=? AND runner_id=? ORDER BY claimed_at DESC LIMIT 1",
          participant,
          requestedRunnerId
        ).toArray();
    if (!rows.length) throw conflict("runner attempt not found");
    const existing = rows[0] as Record<string, unknown>;
    const attemptId = String(existing.attempt_id || requestedAttemptId);
    const runnerId = String(existing.runner_id || requestedRunnerId);
    let status = normalizeAttemptStatus(body.status || "exited");
    if (LIVE_ATTEMPT_STATUSES.has(status) || status === "pending" || status === "ready" || status === "idle") {
      status = "exited";
    }
    const now = nowIso();
    const lastError = trimmedString(body.last_error, 500) || (existing.last_error ? String(existing.last_error).slice(0, 500) : null);
    const recoveryReason = trimmedString(body.recovery_reason ?? body.reason, 240) || null;
    this.sql.exec(
      `UPDATE participant_attempts
       SET status=?, updated_at=?, released_at=COALESCE(released_at, ?), lease_expires_at=COALESCE(lease_expires_at, ?), last_error=?, recovery_reason=COALESCE(?, recovery_reason)
       WHERE attempt_id=?`,
      status,
      now,
      now,
      now,
      lastError,
      recoveryReason,
      attemptId
    );
    if (String(participantRow?.runner_attempt_id || "") === attemptId) {
      this.updateParticipantRunnerPointer(participant, {
        runnerId: null,
        attemptId: null,
        status,
        executionMode: null,
        runnerLastSeenAt: now,
        leaseExpiresAt: now,
        lastError,
      });
    }
    if (recoveryReason) this.setRoomRecovery(recoveryReason);
    await this.appendEvent("*", "runner_release", {
      participant,
      attempt_id: attemptId,
      runner_id: runnerId,
      status,
      reason: recoveryReason,
    });
    const roomRow = this.sql.exec("SELECT * FROM room WHERE id=? LIMIT 1", roomId).one() as Record<string, unknown> | null;
    if (roomRow && String(roomRow.status || "") === "active") {
      await this.scheduleActiveAlarm(roomRow);
    }
    const snapshot = await this.snapshot(roomId);
    await this.publishRoomSnapshot("runner_release", snapshot);
    return json({ participant, attempt_id: attemptId, room: snapshot });
  }

  private async handleRunnerStatus(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    let participant: string | null = null;
    try {
      participant = await this.requireParticipant(request, { joined: true });
    } catch {
      await this.requireHost(request);
    }
    const room = await this.snapshot(roomId);
    const attempts = participant ? room.runner_attempts.filter((attempt) => attempt.participant === participant) : room.runner_attempts;
    return json({ room, participant, attempts, repair_hint: room.repair_hint });
  }

  private async appendRunnerCheckpointEvent(input: {
    participant: string;
    attempt_id: string;
    runner_id: string;
    status: AttemptStatus;
    phase: RunnerPhase;
    phase_detail: string | null;
    phase_updated_at: string;
  }): Promise<void> {
    await this.appendEvent("*", "runner_checkpoint", {
      participant: input.participant,
      attempt_id: input.attempt_id,
      runner_id: input.runner_id,
      status: input.status,
      phase: input.phase,
      phase_detail: input.phase_detail,
      phase_updated_at: input.phase_updated_at,
    });
  }

  private async handleRecoveryActions(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    await this.requireHost(request);
    const room = await this.snapshot(roomId);
    await this.flushDerivedRoomState(room);
    return json({
      room_id: roomId,
      execution_mode: room.execution_mode,
      runner_certification: room.runner_certification,
      automatic_recovery_eligible: room.automatic_recovery_eligible,
      recovery_actions: this.loadRecoveryActionsForHost(roomId),
    });
  }

  private shellRepairCommand(joinLink: string): string {
    return [
      "curl -fsSL https://clawroom.cc/openclaw-shell-bridge.sh -o /tmp/openclaw-shell-bridge.sh",
      "chmod +x /tmp/openclaw-shell-bridge.sh",
      `bash /tmp/openclaw-shell-bridge.sh "${joinLink}" --max-seconds 0 --print-result`,
    ].join(" && ");
  }

  private async replaceCurrentParticipantAttempt(participant: string, reason: string, updatedAt: string): Promise<void> {
    const currentAttempt = this.sql.exec(
      "SELECT runner_attempt_id, runner_id, runner_mode FROM participants WHERE name=? LIMIT 1",
      participant
    ).one() as Record<string, unknown> | null;
    const currentAttemptId = currentAttempt?.runner_attempt_id ? String(currentAttempt.runner_attempt_id) : "";
    const currentRunnerId = currentAttempt?.runner_id ? String(currentAttempt.runner_id) : "";
    const currentRunnerMode = currentAttempt?.runner_mode ? normalizeExecutionMode(currentAttempt.runner_mode) : null;
    if (!currentAttemptId) return;

    this.sql.exec(
      `UPDATE participant_attempts
       SET status='replaced', updated_at=?, released_at=COALESCE(released_at, ?), lease_expires_at=COALESCE(lease_expires_at, ?), recovery_reason=COALESCE(recovery_reason, ?)
       WHERE attempt_id=?`,
      updatedAt,
      updatedAt,
      updatedAt,
      reason,
      currentAttemptId
    );
    this.updateParticipantRunnerPointer(participant, {
      runnerId: null,
      attemptId: null,
      status: "replaced",
      executionMode: currentRunnerMode,
      runnerLastSeenAt: updatedAt,
      leaseExpiresAt: updatedAt,
      lastError: reason,
    });
    await this.appendEvent("*", "runner_replaced", {
      participant,
      previous_attempt_id: currentAttemptId,
      previous_runner_id: currentRunnerId || null,
      replacement_runner_id: null,
      reason,
    });
  }

  private async handleRepairInvite(request: Request, roomId: string, participant: string): Promise<Response> {
    this.ensureSchema();
    this.invalidateSnapshotCache();
    await this.requireHost(request);
    const participantName = decodeURIComponent(String(participant || "")).trim();
    if (!participantName) return badRequest("participant required");
    const row = this.sql.exec("SELECT name FROM participants WHERE name=? LIMIT 1", participantName).one();
    if (!row) return badRequest("unknown participant");

    const issuedAt = nowIso();
    await this.replaceCurrentParticipantAttempt(participantName, "repair_invite_reissued", issuedAt);
    this.setRoomRecovery(`repair_invite_reissued:${participantName}`);
    const snapshotBeforeIssue = await this.snapshot(roomId);
    await this.flushDerivedRoomState(snapshotBeforeIssue);
    const currentAction =
      this.getCurrentRecoveryAction(participantName)
      || this.ensureCurrentRecoveryAction(
        participantName,
        "manual_repair_requested",
        `Manual repair invite requested for ${participantName}.`,
        "manual",
      );
    const issuedPackage = await this.issueRecoveryPackage(
      roomId,
      participantName,
      String(currentAction.action_id || ""),
      "manual",
      "manual",
      new URL(request.url).origin,
    );
    await this.appendEvent("*", "repair_invite_issued", {
      participant: participantName,
      invalidates_previous_invite: true,
      delivery_mode: "manual",
    });

    const snapshot = await this.snapshot(roomId);
    await this.publishRoomSnapshot("repair_invite_issued", snapshot);
    return json({
      participant: participantName,
      invalidates_previous_invite: true,
      invite_token: issuedPackage.invite_token,
      join_link: issuedPackage.join_link,
      repair_command: issuedPackage.repair_command,
      room: snapshot,
    });
  }

  private async handleHeartbeat(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    const participant = await this.requireParticipant(request, { joined: true });
    const becameOnline = this.touchParticipant(participant);
    const snapshot = await this.snapshot(roomId);
    if (becameOnline) {
      await this.publishRoomSnapshot("presence_reconciled", snapshot);
    }
    return json({ participant, room: snapshot });
  }

  private async handleLeave(request: Request, roomId: string): Promise<Response> {
    this.invalidateSnapshotCache();
    const participant = await this.requireParticipant(request, { joined: true });
    const body = (await request.json().catch(() => ({}))) as any;
    const reason = typeof body?.reason === "string" ? body.reason.slice(0, 500) : "left";
    const leftAt = nowIso();

    const row = this.sql.exec("SELECT online FROM participants WHERE name=? LIMIT 1", participant).one();
    const wasOnline = row ? Boolean(row.online) : false;
    this.sql.exec("UPDATE participants SET online=0, last_seen_at=? WHERE name=?", leftAt, participant);
    const currentRunner = this.sql.exec(
      "SELECT runner_attempt_id FROM participants WHERE name=? LIMIT 1",
      participant
    ).one() as Record<string, unknown> | null;
    const currentAttemptId = currentRunner?.runner_attempt_id ? String(currentRunner.runner_attempt_id) : "";
    if (currentAttemptId) {
      this.sql.exec(
        `UPDATE participant_attempts
         SET status=CASE WHEN status IN ('replaced','abandoned') THEN status ELSE 'exited' END,
             updated_at=?,
             released_at=COALESCE(released_at, ?),
             lease_expires_at=COALESCE(lease_expires_at, ?),
             recovery_reason=COALESCE(recovery_reason, 'runner_left')
         WHERE attempt_id=?`,
        leftAt,
        leftAt,
        leftAt,
        currentAttemptId
      );
      this.updateParticipantRunnerPointer(participant, {
        runnerId: null,
        attemptId: null,
        status: "exited",
        executionMode: null,
        runnerLastSeenAt: leftAt,
        leaseExpiresAt: leftAt,
        lastError: null,
      });
      this.setRoomRecovery("runner_left");
    }
    await this.appendEvent("*", "leave", { participant, reason });
    const roomRow = this.sql.exec("SELECT * FROM room WHERE id=? LIMIT 1", roomId).one() as Record<string, unknown> | null;
    if (roomRow && String(roomRow.status || "") === "active") {
      await this.scheduleActiveAlarm(roomRow);
    }
    const snapshot = await this.snapshot(roomId);
    await this.publishRoomSnapshot("leave", snapshot);
    return json({ was_online: wasOnline, room: snapshot });
  }

  private async handleClose(request: Request, roomId: string): Promise<Response> {
    await this.requireHost(request);
    this.invalidateSnapshotCache();
    const body = (await request.json().catch(() => ({}))) as any;
    const reason = typeof body?.reason === "string" ? body.reason.slice(0, 500) : "manual close";
    const changed = await this.closeRoom("manual_close", reason);
    const snapshot = await this.snapshot(roomId);
    if (changed) await this.publishRoomSnapshot("close", snapshot);
    return json({ room: snapshot, already_closed: !changed });
  }

  private async handleMessage(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    this.invalidateSnapshotCache();
    const sender = await this.requireParticipant(request, { joined: true });

    const row = this.sql.exec("SELECT status FROM room LIMIT 1").one();
    if (!row) throw new Response(JSON.stringify({ error: "not_initialized" }), { status: 404 });
    if (String(row.status) !== "active") throw conflict("room not active");
    this.touchParticipant(sender);

    const raw = await request.json().catch(() => ({}));
    let msg: Message;
    try {
      msg = normalizeMessage(raw);
    } catch (err: any) {
      return badRequest("invalid message", { detail: String(err?.message || err) });
    }

    const createdAt = nowIso();
    const serverOverrides: string[] = [];
    if (msg.intent === "NOTE" && msg.expect_reply) {
      msg.expect_reply = false;
      serverOverrides.push("NOTE.expect_reply=false");
    }
    // Semantic invariant: ASK always expects a reply. If a client sends
    // expect_reply=false, we correct it to avoid silent stalls (no relay).
    if (msg.intent === "ASK" && !msg.expect_reply) {
      msg.expect_reply = true;
      serverOverrides.push("ASK.expect_reply=true");
    }
    if (msg.intent === "ASK_OWNER" && msg.expect_reply) {
      msg.expect_reply = false;
      serverOverrides.push("ASK_OWNER.expect_reply=false");
    }
    if (msg.intent === "DONE" && msg.expect_reply) {
      msg.expect_reply = false;
      serverOverrides.push("DONE.expect_reply=false");
    }
    if (serverOverrides.length > 0) {
      msg.meta = {
        ...(msg.meta || {}),
        server_overrides: serverOverrides
      };
    }

    const inReplyRaw = (msg.meta as Record<string, unknown> | null)?.in_reply_to_event_id;
    const inReplyToEventId =
      typeof inReplyRaw === "number" && Number.isFinite(inReplyRaw) && inReplyRaw > 0
        ? Math.floor(inReplyRaw)
        : typeof inReplyRaw === "string" && Number.isFinite(Number(inReplyRaw)) && Number(inReplyRaw) > 0
          ? Math.floor(Number(inReplyRaw))
          : null;
    if (inReplyToEventId !== null) {
      const dedupExists = this.sql
        .exec(
          "SELECT 1 FROM reply_dedup WHERE participant=? AND in_reply_to_event_id=? LIMIT 1",
          sender,
          inReplyToEventId
        )
        .toArray();
      if (dedupExists.length) {
        const snapshot = await this.snapshot(roomId);
        await this.publishRoomSnapshot("message_dedup", snapshot);
        return json({ room: snapshot, host_decision: await this.hostDecision(), dedup_hit: true });
      }
      this.sql.exec(
        "INSERT INTO reply_dedup (participant, in_reply_to_event_id, created_at) VALUES (?, ?, ?)",
        sender,
        inReplyToEventId,
        createdAt
      );
    }

    if (msg.intent === "DONE") {
      this.sql.exec("UPDATE participants SET done=1 WHERE name=?", sender);
    } else {
      this.sql.exec("UPDATE participants SET done=0 WHERE name=?", sender);
    }

    const explicitCompletionSignal = hasCompletionMarker(msg.meta || {});
    if (explicitCompletionSignal) {
      this.sql.exec("UPDATE room SET completion_signaled=1 WHERE id IS NOT NULL");
    } else if (msg.intent !== "DONE") {
      this.sql.exec("UPDATE room SET completion_signaled=0 WHERE id IS NOT NULL");
    }

    const waitingOwnerBefore = this.sql.exec("SELECT waiting_owner FROM participants WHERE name=? LIMIT 1", sender).one();
    const wasWaitingOwner = waitingOwnerBefore ? Boolean(waitingOwnerBefore.waiting_owner) : false;
    const currentRunnerRow = this.sql.exec(
      "SELECT runner_attempt_id, runner_id, runner_mode, runner_lease_expires_at, last_runner_error FROM participants WHERE name=? LIMIT 1",
      sender
    ).one() as Record<string, unknown> | null;
    const currentAttemptId = currentRunnerRow?.runner_attempt_id ? String(currentRunnerRow.runner_attempt_id) : "";
    const currentRunnerId = currentRunnerRow?.runner_id ? String(currentRunnerRow.runner_id) : "";
    const currentRunnerMode = normalizeExecutionMode(currentRunnerRow?.runner_mode);

    if (msg.intent === "ASK_OWNER") {
      this.sql.exec("UPDATE participants SET waiting_owner=1 WHERE name=?", sender);
      if (currentAttemptId) {
        this.sql.exec(
          "UPDATE participant_attempts SET status='waiting_owner', updated_at=? WHERE attempt_id=?",
          createdAt,
          currentAttemptId
        );
        this.updateParticipantRunnerPointer(sender, {
          runnerId: currentRunnerId || null,
          attemptId: currentAttemptId,
          status: "waiting_owner",
          executionMode: currentRunnerMode,
          runnerLastSeenAt: createdAt,
          leaseExpiresAt: currentRunnerRow?.runner_lease_expires_at ? String(currentRunnerRow.runner_lease_expires_at) : null,
          lastError: currentRunnerRow?.last_runner_error ? String(currentRunnerRow.last_runner_error) : null,
        });
      }
      await this.appendEvent("*", "owner_wait", { participant: sender, text: msg.text, meta: msg.meta || {} });
    }
    if (msg.intent === "OWNER_REPLY") {
      this.sql.exec("UPDATE participants SET waiting_owner=0 WHERE name=?", sender);
      if (currentAttemptId) {
        this.sql.exec("UPDATE participant_attempts SET status='active', updated_at=? WHERE attempt_id=?", createdAt, currentAttemptId);
        this.updateParticipantRunnerPointer(sender, {
          runnerId: currentRunnerId || null,
          attemptId: currentAttemptId,
          status: "active",
          executionMode: currentRunnerMode,
          runnerLastSeenAt: createdAt,
          leaseExpiresAt: currentRunnerRow?.runner_lease_expires_at ? String(currentRunnerRow.runner_lease_expires_at) : null,
          lastError: currentRunnerRow?.last_runner_error ? String(currentRunnerRow.last_runner_error) : null,
        });
      }
      await this.appendEvent("*", "owner_resume", { participant: sender, text: msg.text, meta: msg.meta || {} });
    }

    // Apply fills (overwrite allowed) + detect structured progress.
    let newFieldCount = 0;
    for (const [k, v] of Object.entries(msg.fills || {})) {
      const key = String(k).trim();
      const val = String(v).trim();
      if (!key || !val) continue;
      const existing = this.sql.exec("SELECT value FROM fields WHERE key=? LIMIT 1", key).toArray();
      if (!existing.length || String(existing[0].value) !== val) newFieldCount += 1;
      this.sql.exec(
        "INSERT INTO fields (key, value, updated_at, by_participant) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, by_participant=excluded.by_participant",
        key,
        val,
        createdAt,
        sender
      );
    }

    const cleanFacts = (msg.facts || []).map((x) => trimmedString(x, MAX_FACT_TEXT)).filter(Boolean);

    // Text progress detection (avoid infinite loops on repeated text).
    const textKey = normText(msg.text);
    let isNewText = false;
    if (textKey) {
      const seen = this.sql.exec("SELECT text_key FROM seen_texts WHERE text_key=? LIMIT 1", textKey).toArray();
      if (!seen.length) {
        this.sql.exec("INSERT INTO seen_texts (text_key) VALUES (?)", textKey);
        isNewText = true;
      }
    }

    const structuredProgress = Boolean(newFieldCount || cleanFacts.length);
    const hasProgress = structuredProgress || isNewText;

    if (wasWaitingOwner && msg.intent !== "ASK_OWNER" && msg.intent !== "OWNER_REPLY" && (hasProgress || msg.intent === "DONE" || inReplyToEventId !== null)) {
      this.sql.exec("UPDATE participants SET waiting_owner=0 WHERE name=?", sender);
      if (currentAttemptId) {
        this.sql.exec("UPDATE participant_attempts SET status='active', updated_at=? WHERE attempt_id=?", createdAt, currentAttemptId);
        this.updateParticipantRunnerPointer(sender, {
          runnerId: currentRunnerId || null,
          attemptId: currentAttemptId,
          status: "active",
          executionMode: currentRunnerMode,
          runnerLastSeenAt: createdAt,
          leaseExpiresAt: currentRunnerRow?.runner_lease_expires_at ? String(currentRunnerRow.runner_lease_expires_at) : null,
          lastError: currentRunnerRow?.last_runner_error ? String(currentRunnerRow.last_runner_error) : null,
        });
      }
      await this.appendEvent("*", "owner_resume", {
        participant: sender,
        text: msg.text,
        meta: {
          ...(msg.meta || {}),
          resumed_by: "continuation",
          resume_intent: msg.intent,
        }
      });
    }

    // Append message event visible to all.
    await this.appendEvent("*", "msg", {
      message: {
        sender,
        intent: msg.intent,
        text: msg.text,
        fills: msg.fills || {},
        facts: cleanFacts,
        questions: msg.questions || [],
        expect_reply: msg.expect_reply,
        meta: msg.meta || {}
      }
    });

    // Relay when a reply is expected, or when a participant signals DONE so peers
    // can observe completion and close out (mutual DONE).
    if (msg.expect_reply || msg.intent === "DONE") {
      this.sql.exec("UPDATE room SET first_relay_at=COALESCE(first_relay_at, ?) WHERE id=?", createdAt, roomId);
      const others = this.sql.exec("SELECT name FROM participants WHERE name != ?", sender).toArray();
      for (const other of others) {
        const to = String(other.name);
        await this.appendEvent(to, "relay", {
          from: sender,
          message: {
            sender,
            intent: msg.intent,
            text: msg.text,
            fills: msg.fills || {},
            expect_reply: msg.expect_reply
          }
        });
      }
    }

    if (currentAttemptId && msg.intent !== "ASK_OWNER") {
      const nextAttemptStatus: AttemptStatus = msg.intent === "DONE" ? "idle" : "active";
      this.sql.exec("UPDATE participant_attempts SET status=?, updated_at=? WHERE attempt_id=?", nextAttemptStatus, createdAt, currentAttemptId);
      this.updateParticipantRunnerPointer(sender, {
        runnerId: currentRunnerId || null,
        attemptId: currentAttemptId,
        status: nextAttemptStatus,
        executionMode: currentRunnerMode,
        runnerLastSeenAt: createdAt,
        leaseExpiresAt: currentRunnerRow?.runner_lease_expires_at ? String(currentRunnerRow.runner_lease_expires_at) : null,
        lastError: currentRunnerRow?.last_runner_error ? String(currentRunnerRow.last_runner_error) : null,
      });
    }

    // Turn count increments only on msg that expects a reply OR is a normal conversational turn.
    // Keep it simple: every posted message increments.
    this.sql.exec("UPDATE room SET turn_count = turn_count + 1, updated_at = ? ", createdAt);
    if (hasProgress) {
      this.sql.exec("UPDATE room SET stall_count = 0 WHERE id IS NOT NULL");
    } else if (msg.intent !== "DONE" && msg.intent !== "ASK_OWNER") {
      this.sql.exec("UPDATE room SET stall_count = stall_count + 1 WHERE id IS NOT NULL");
    }

    await this.applyStopRules();
    const snapshot = await this.snapshot(roomId);
    const registryEventType =
      msg.intent === "ASK_OWNER"
        ? "owner_wait"
        : msg.intent === "OWNER_REPLY"
          ? "owner_resume"
          : msg.intent === "DONE"
            ? "done"
            : "message";
    await this.publishRoomSnapshot(registryEventType, snapshot);
    return json({ room: snapshot, host_decision: await this.hostDecision() });
  }

  private async handleEvents(request: Request, roomId: string, isMonitor: boolean): Promise<Response> {
    this.ensureSchema();
    const after = parsePositiveInt(new URL(request.url).searchParams.get("after"), 0);
    const limit = Math.min(parsePositiveInt(new URL(request.url).searchParams.get("limit"), 200), 500);

    let audience: string;
    if (isMonitor) {
      await this.requireHost(request);
      audience = "*";
    } else {
      audience = await this.requireParticipant(request, { joined: true });
      this.touchParticipant(audience);
    }

    const events = this.readEvents(after, limit, audience);
    const room = await this.snapshot(roomId);
    const nextCursor = events.length ? events[events.length - 1].id : after;
    return json({ room, events, next_cursor: nextCursor });
  }

  private async handleResult(request: Request, roomId: string, isMonitor: boolean): Promise<Response> {
    this.ensureSchema();
    if (isMonitor) {
      await this.requireHost(request);
    } else {
      try {
        await this.requireParticipant(request);
      } catch {
        await this.requireHost(request);
      }
    }
    const room = await this.snapshot(roomId);
    await this.flushDerivedRoomState(room);
    const includeTranscriptParam = new URL(request.url).searchParams.get("include_transcript");
    const result = await this.result(room, {
      includeTranscript: room.status === "closed" || includeTranscriptParam === "1",
    });
    return json({ result, room });
  }

  private async handleMonitorStream(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    await this.requireHost(request);

    const url = new URL(request.url);
    let cursor = parsePositiveInt(url.searchParams.get("after"), 0);
    const heartbeatMs = 1000;
    const encoder = new TextEncoder();
    const stream = new TransformStream<Uint8Array, Uint8Array>();
    const writer = stream.writable.getWriter();

    const writeChunk = async (chunk: string): Promise<void> => {
      await writer.write(encoder.encode(chunk));
    };

    const writeEvent = async (event: EventRow): Promise<void> => {
      await writeChunk(`id: ${event.id}\n`);
      await writeChunk(`event: ${event.type}\n`);
      await writeChunk(`data: ${JSON.stringify(event)}\n\n`);
    };

    const writeRoomClosed = async (room: RoomSnapshot, eventId: number): Promise<void> => {
      await writeChunk(`id: ${eventId}\n`);
      await writeChunk("event: room_closed\n");
      await writeChunk(`data: ${JSON.stringify(room)}\n\n`);
    };

    void (async () => {
      try {
        // Initial comment keeps some proxies from buffering indefinitely.
        await writeChunk(": clawroom monitor stream\n\n");

        while (!request.signal.aborted) {
          const batch = this.readEvents(cursor, 500, "*");
          for (const evt of batch) {
            cursor = Math.max(cursor, evt.id);
            await writeEvent(evt);
          }

          let room: RoomSnapshot | null = null;
          try {
            room = await this.snapshot(roomId);
          } catch {
            break;
          }

          if (room.status !== "active") {
            const closedEventId = cursor > 0 ? cursor : Number(Date.now());
            await writeRoomClosed(room, closedEventId);
            break;
          }

          // Heartbeat for idle periods so clients detect live connection.
          await writeChunk(`: keepalive ${Date.now()}\n\n`);
          await sleep(heartbeatMs);
        }
      } catch {
        // Client likely disconnected; no-op.
      } finally {
        try {
          await writer.close();
        } catch {
          // Ignore close errors on aborted streams.
        }
      }
    })();

    return new Response(stream.readable, {
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        "x-accel-buffering": "no"
      }
    });
  }

  private async handleDiagnostics(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    await this.requireHost(request);
    const room = await this.snapshot(roomId);
    return json({
      room_id: roomId,
      diagnostics: {
        ...this.diagnostics,
        snapshot_cache_version: this.snapshotVersion,
        cached_snapshot_room_id: this.cachedSnapshot?.roomId || null,
        participant_touch_cache_size: this.participantTouchCache.size,
      },
      room: {
        status: room.status,
        execution_mode: room.execution_mode,
        attempt_status: room.attempt_status,
        execution_attention: room.execution_attention,
        start_slo: room.start_slo,
      },
    });
  }

  private async handleParticipantStream(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    const participant = await this.requireParticipantFromQuery(request, { joined: true });

    const url = new URL(request.url);
    let cursor = parsePositiveInt(url.searchParams.get("after"), 0);
    const heartbeatMs = 1000;
    const encoder = new TextEncoder();
    const stream = new TransformStream<Uint8Array, Uint8Array>();
    const writer = stream.writable.getWriter();

    const writeChunk = async (chunk: string): Promise<void> => {
      await writer.write(encoder.encode(chunk));
    };

    const writeEvent = async (event: EventRow): Promise<void> => {
      await writeChunk(`id: ${event.id}\n`);
      await writeChunk(`event: ${event.type}\n`);
      await writeChunk(`data: ${JSON.stringify(event)}\n\n`);
    };

    const writeRoomClosed = async (room: RoomSnapshot, eventId: number): Promise<void> => {
      await writeChunk(`id: ${eventId}\n`);
      await writeChunk("event: room_closed\n");
      await writeChunk(`data: ${JSON.stringify(room)}\n\n`);
    };

    void (async () => {
      try {
        await writeChunk(": clawroom participant stream\n\n");

        while (!request.signal.aborted) {
          this.touchParticipant(participant);
          const batch = this.readEvents(cursor, 500, participant);
          for (const evt of batch) {
            cursor = Math.max(cursor, evt.id);
            await writeEvent(evt);
          }

          let room: RoomSnapshot | null = null;
          try {
            room = await this.snapshot(roomId);
          } catch {
            break;
          }

          if (room.status !== "active") {
            const closedEventId = cursor > 0 ? cursor : Number(Date.now());
            await writeRoomClosed(room, closedEventId);
            break;
          }

          await writeChunk(`: keepalive ${Date.now()}\n\n`);
          await sleep(heartbeatMs);
        }
      } catch {
        // Client likely disconnected; no-op.
      } finally {
        try {
          await writer.close();
        } catch {
          // Ignore close errors on aborted streams.
        }
      }
    })();

    return new Response(stream.readable, {
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        "x-accel-buffering": "no"
      }
    });
  }

  private readEvents(after: number, limit: number, audience: string): EventRow[] {
    const globalRows = this.sql
      .exec(
        "SELECT id, type, created_at, audience, payload_json FROM events WHERE audience='*' AND id > ? ORDER BY id ASC LIMIT ?",
        after,
        limit,
      )
      .toArray() as Array<Record<string, unknown>>;
    const participantRows =
      audience === "*"
        ? []
        : (this.sql
            .exec(
              "SELECT id, type, created_at, audience, payload_json FROM events WHERE audience=? AND id > ? ORDER BY id ASC LIMIT ?",
              audience,
              after,
              limit,
            )
            .toArray() as Array<Record<string, unknown>>);
    const rows = [...globalRows, ...participantRows]
      .sort((a, b) => Number(a.id || 0) - Number(b.id || 0))
      .slice(0, limit);
    return rows.map((r) => ({
      id: Number(r.id),
      type: String(r.type),
      created_at: String(r.created_at),
      audience: String(r.audience),
      payload: JSON.parse(String(r.payload_json || "{}"))
    }));
  }

  private async appendEvent(audience: EventAudience, type: string, payload: any): Promise<void> {
    this.ensureSchema();
    this.invalidateSnapshotCache();
    const createdAt = nowIso();
    this.sql.exec("INSERT INTO events (type, created_at, audience, payload_json) VALUES (?, ?, ?, ?)", type, createdAt, String(audience), JSON.stringify(payload || {}));
    if (type === "msg" && payload && typeof payload === "object" && !Array.isArray(payload)) {
      const message =
        payload.message && typeof payload.message === "object" && !Array.isArray(payload.message)
          ? (payload.message as Record<string, unknown>)
          : {};
      this.sql.exec(
        `UPDATE room
         SET latest_msg_created_at=?,
             latest_msg_sender=?,
             latest_msg_intent=?,
             latest_msg_expect_reply=?,
             updated_at=?
         WHERE id IS NOT NULL`,
        createdAt,
        trimmedString(message.sender, 120) || null,
        trimmedString(message.intent, 32) || null,
        message.expect_reply === undefined ? null : (Boolean(message.expect_reply) ? 1 : 0),
        createdAt,
      );
      return;
    }
    this.sql.exec("UPDATE room SET updated_at = ?", createdAt);
  }

  private async snapshot(roomId: string): Promise<RoomSnapshot> {
    this.ensureSchema();
    if (
      this.cachedSnapshot
      && this.cachedSnapshot.roomId === roomId
      && this.cachedSnapshot.version === this.snapshotVersion
      && !this.onlineStateDirty
      && !this.recoveryStateDirty
    ) {
      this.diagnostics.snapshot_cache_hits += 1;
      return structuredClone(this.cachedSnapshot.snapshot);
    }
    this.diagnostics.snapshot_cache_misses += 1;
    this.onlineStateDirty = this.reconcileOnlineState();
    const room = this.sql.exec("SELECT * FROM room WHERE id=? LIMIT 1", roomId).one();
    if (!room) throw new Response(JSON.stringify({ error: "room_not_found" }), { status: 404 });

    const participants = this.sql.exec("SELECT * FROM participants ORDER BY name ASC").toArray();
    const fields = this.sql.exec("SELECT * FROM fields ORDER BY key ASC").toArray();
    const attemptRecords = this.loadAttemptRecords(participants);
    const execution = this.summarizeExecution(
      room as Record<string, unknown>,
      participants as Array<Record<string, unknown>>,
      attemptRecords
    );

    const fieldMap: RoomSnapshot["fields"] = {};
    for (const f of fields) {
      fieldMap[String(f.key)] = { value: String(f.value), updated_at: String(f.updated_at), by: String(f.by_participant) };
    }

    const requiredFields = JSON.parse(String(room.required_fields_json || "[]")) as string[];
    const filledKeys = new Set(Object.keys(fieldMap));
    const lifecycleState = this.deriveLifecycleState(room, participants, requiredFields, filledKeys);
    const startSlo = this.buildStartSlo(room as Record<string, unknown>);
    const fieldStats = { requiredTotal: requiredFields.length, filledCount: requiredFields.filter(k => filledKeys.has(k)).length };
    let currentRecoveryRows = this.loadRecoveryActionRows(true);
    const initialExecutionAttention = this.deriveExecutionAttention(
      room as Record<string, unknown>,
      participants as Array<Record<string, unknown>>,
      attemptRecords,
      execution,
      startSlo,
      currentRecoveryRows,
      fieldStats,
    );
    const recoveryActionsChanged = this.syncRecoveryActions(
      participants as Array<Record<string, unknown>>,
      attemptRecords,
      initialExecutionAttention
    );
    const autoIssued = await this.maybeAutoIssueRecoveryActions(roomId, attemptRecords);
    const manualPrepared = await this.maybePrepareManualRecoveryActions(roomId, attemptRecords);
    const recoveryChanged = recoveryActionsChanged || autoIssued || manualPrepared;
    if (recoveryChanged) {
      currentRecoveryRows = this.loadRecoveryActionRows(true);
    }
    const executionAttention = recoveryChanged
      ? this.deriveExecutionAttention(
          room as Record<string, unknown>,
          participants as Array<Record<string, unknown>>,
          attemptRecords,
          execution,
          startSlo,
          currentRecoveryRows,
          fieldStats,
        )
      : initialExecutionAttention;
    const repairHint = this.buildRepairHint(
      roomId,
      room as Record<string, unknown>,
      participants as Array<Record<string, unknown>>,
      attemptRecords,
      executionAttention
    );
    const recoveryActions = this.loadRecoveryActions(false);
    const rootCauseHints = this.deriveRootCauseHints(
      room as Record<string, unknown>,
      participants as Array<Record<string, unknown>>,
      attemptRecords,
      execution,
      executionAttention,
      startSlo
    );

    const snapshot: RoomSnapshot = {
      id: String(room.id),
      topic: String(room.topic),
      goal: String(room.goal),
      protocol_version: PROTOCOL_VERSION,
      capabilities: [...ROOM_CAPABILITIES],
      execution_mode: execution.executionMode,
      runner_certification: execution.runnerCertification,
      managed_coverage: execution.managedCoverage,
      supervision_origins: execution.supervisionOrigins,
      product_owned: execution.productOwned,
      automatic_recovery_eligible: execution.automaticRecoveryEligible,
      attempt_status: execution.attemptStatus,
      active_runner_id: execution.activeRunnerId,
      active_runner_count: execution.activeRunnerCount,
      last_recovery_reason: execution.lastRecoveryReason,
      execution_attention: executionAttention,
      root_cause_hints: rootCauseHints,
      repair_hint: repairHint,
      recovery_actions: recoveryActions,
      start_slo: startSlo,
      lifecycle_state: lifecycleState,
      required_fields: requiredFields,
      expected_outcomes: requiredFields,
      fields: fieldMap,
      status: String(room.status) as RoomStatus,
      stop_reason: room.stop_reason ? (String(room.stop_reason) as StopReason) : null,
      stop_detail: room.stop_detail ? String(room.stop_detail) : null,
      created_at: String(room.created_at),
      updated_at: String(room.updated_at),
      turn_count: Number(room.turn_count),
      stall_count: Number(room.stall_count),
      deadline_at: String(room.deadline_at),
      participants: participants.map((p) => ({
        name: String(p.name),
        joined: Boolean(p.joined),
        joined_at: p.joined_at ? String(p.joined_at) : null,
        online: Boolean(p.online),
        last_seen_at: p.last_seen_at ? String(p.last_seen_at) : null,
        done: Boolean(p.done),
        waiting_owner: Boolean(p.waiting_owner),
        client_name: p.client_name ? String(p.client_name) : null
      })),
      runner_attempts: attemptRecords,
    };
    this.cachedSnapshot = { roomId, version: this.snapshotVersion, snapshot };
    return structuredClone(snapshot);
  }

  private deriveLifecycleState(
    roomRow: any,
    participantRows: any[],
    requiredFields: string[],
    filledKeys: Set<string>
  ): LifecycleState {
    const roomStatus = String(roomRow?.status || "active");
    const stopReason = String(roomRow?.stop_reason || "");
    if (roomStatus === "closed") {
      if (stopReason === "goal_done" || stopReason === "mutual_done") return "completed";
      if (stopReason === "manual_close") return "canceled";
      return "failed";
    }
    const waitingOwner = participantRows.some((participant) => Boolean(participant.waiting_owner));
    if (waitingOwner) return "input_required";
    const everyoneDone = participantRows.length > 0 && participantRows.every((participant) => Boolean(participant.done));
    if (everyoneDone && requiredFields.length > 0) {
      const missingRequired = requiredFields.some((field) => !filledKeys.has(field));
      if (missingRequired) return "input_required";
    }
    const turnCount = Number(roomRow?.turn_count || 0);
    if (turnCount <= 0) return "submitted";
    return "working";
  }

  private async hostDecision(): Promise<{ trigger: string | null } | null> {
    const room = this.sql.exec("SELECT status, stop_reason FROM room LIMIT 1").one();
    if (!room) return null;
    if (String(room.status) === "closed") return { trigger: String(room.stop_reason || "closed") };
    return { trigger: null };
  }

  private async applyStopRules(): Promise<void> {
    const room = this.sql.exec("SELECT * FROM room LIMIT 1").one();
    if (!room) return;
    if (String(room.status) !== "active") return;

    const requiredFields = JSON.parse(String(room.required_fields_json || "[]")) as string[];
    const filled = this.sql.exec("SELECT key FROM fields").toArray().map((r) => String(r.key));
    const missing = requiredFields.filter((k) => !filled.includes(k));
    const explicitCompletionSignaled = Boolean(room.completion_signaled);
    const anyParticipantDone = Number(this.sql.exec("SELECT COUNT(*) AS c FROM participants WHERE done=1").one()?.c || 0) > 0;
    const terminalCompletionInferred = this.inferTerminalCompletionHandshake();
    const completionSignaled = explicitCompletionSignaled || anyParticipantDone || terminalCompletionInferred;

    if (((requiredFields.length > 0 && missing.length === 0) || terminalCompletionInferred) && completionSignaled) {
      const detail = terminalCompletionInferred
        ? "terminal no-reply counterpart message plus DONE inferred completion"
        : "required fields complete and completion signal present";
      await this.closeRoom("goal_done", detail);
      return;
    }

    const everyoneDone = this.sql.exec("SELECT COUNT(*) AS c FROM participants WHERE done=0").one();
    if (everyoneDone && Number(everyoneDone.c) === 0) {
      if (requiredFields.length > 0 && missing.length > 0) {
        return;
      }
      await this.closeRoom("mutual_done", "all participants done");
      return;
    }

    const deadlineAt = Date.parse(String(room.deadline_at));
    if (Number.isFinite(deadlineAt) && Date.now() >= deadlineAt) {
      await this.closeRoom("timeout", "deadline exceeded");
      return;
    }

    const turnLimit = Number(room.turn_limit);
    const stallLimit = Number(room.stall_limit);

    const turnCount = Number(room.turn_count);
    if (turnCount >= turnLimit) {
      await this.closeRoom("turn_limit", "turn limit reached");
      return;
    }

    const stallCount = Number(room.stall_count);
    if (stallCount >= stallLimit) {
      await this.closeRoom("stall_limit", "stall limit reached");
    }
  }

  private inferTerminalCompletionHandshake(): boolean {
    const participantRows = this.sql
      .exec("SELECT name, joined, waiting_owner, done FROM participants ORDER BY name ASC")
      .toArray();
    if (participantRows.length !== 2) return false;
    if (participantRows.some((row) => !Boolean(row.joined) || Boolean(row.waiting_owner))) return false;
    if (!participantRows.some((row) => Boolean(row.done))) return false;
    if (participantRows.every((row) => Boolean(row.done))) return false;

    const transcriptRows = this.sql
      .exec("SELECT payload_json FROM events WHERE type='msg' ORDER BY id DESC LIMIT 2")
      .toArray();
    if (transcriptRows.length < 2) return false;

    const latestPayload = JSON.parse(String(transcriptRows[0]?.payload_json || "{}"));
    const prevPayload = JSON.parse(String(transcriptRows[1]?.payload_json || "{}"));
    const latestMsg = (latestPayload?.message || {}) as Record<string, unknown>;
    const prevMsg = (prevPayload?.message || {}) as Record<string, unknown>;
    const latestSender = String(latestMsg.sender || "");
    const prevSender = String(prevMsg.sender || "");
    const latestIntent = String(latestMsg.intent || "");
    const prevIntent = String(prevMsg.intent || "");
    const prevExpectReply = prevMsg.expect_reply === undefined ? null : Boolean(prevMsg.expect_reply);

    if (!latestSender || !prevSender || latestSender === prevSender) return false;
    if (latestIntent !== "DONE") return false;
    if (prevExpectReply !== false) return false;
    if (!["ANSWER", "NOTE", "DONE"].includes(prevIntent)) return false;

    return true;
  }

  private async closeRoom(reason: StopReason, detail: string): Promise<boolean> {
    const current = this.sql.exec("SELECT status, ttl_minutes FROM room LIMIT 1").one();
    if (!current) return false;
    if (String(current.status) === "closed") return false;
    this.invalidateSnapshotCache();
    const updatedAt = nowIso();
    this.sql.exec(
      `UPDATE participant_attempts
       SET status=CASE
         WHEN status IN ('replaced', 'abandoned', 'exited') THEN status
         ELSE 'exited'
       END,
       updated_at=?,
       released_at=COALESCE(released_at, ?),
       lease_expires_at=COALESCE(lease_expires_at, ?)
       WHERE released_at IS NULL`,
      updatedAt,
      updatedAt,
      updatedAt
    );
    this.sql.exec(
      "UPDATE participants SET runner_id=NULL, runner_attempt_id=NULL, runner_status='exited', runner_mode=NULL, runner_last_seen_at=?, runner_lease_expires_at=?, last_runner_error=last_runner_error",
      updatedAt,
      updatedAt
    );
    this.sql.exec(
      `UPDATE recovery_actions
       SET status='superseded', resolved_at=COALESCE(resolved_at, ?), updated_at=?, current=0
       WHERE current=1`,
      updatedAt,
      updatedAt,
    );
    this.recoveryStateDirty = true;
    this.sql.exec("UPDATE room SET status='closed', stop_reason=?, stop_detail=?, updated_at=? WHERE status='active'", reason, detail, updatedAt);
    await this.appendEvent("*", "status", { status: "closed", reason, detail });

    // TTL cleanup: ephemeral by default. Use alarm so the room disappears after close.
    const ttlMinutes = Math.max(1, Number(current.ttl_minutes || 60));
    const expiresAt = new Date(Date.now() + ttlMinutes * 60_000).toISOString();
    this.sql.exec("UPDATE room SET expires_at=? WHERE id IS NOT NULL", expiresAt);
    await this.state.storage.setAlarm(Date.now() + ttlMinutes * 60_000);
    return true;
  }

  private async result(room: RoomSnapshot, options?: { includeTranscript?: boolean }): Promise<any> {
    const includeTranscript = Boolean(options?.includeTranscript);
    const transcript = includeTranscript
      ? this.sql
          .exec("SELECT id, created_at, payload_json FROM events WHERE type='msg' ORDER BY id ASC")
          .toArray()
          .map((r) => {
            const payload = JSON.parse(String(r.payload_json || "{}"));
            const msg = (payload?.message || {}) as any;
            return {
              id: Number(r.id),
              sender: String(msg.sender || ""),
              intent: String(msg.intent || ""),
              text: String(msg.text || ""),
              fills: (msg.fills && typeof msg.fills === "object" ? msg.fills : {}) as Record<string, string>,
              facts: Array.isArray(msg.facts) ? msg.facts : [],
              questions: Array.isArray(msg.questions) ? msg.questions : [],
              expect_reply: Boolean(msg.expect_reply),
              meta: (msg.meta && typeof msg.meta === "object" ? msg.meta : {}) as Record<string, unknown>,
              created_at: String(r.created_at)
            };
          })
      : [];

    const requiredTotal = room.required_fields.length;
    const requiredFilled = room.required_fields.filter((k) => Boolean(room.fields[k])).length;
    const outcomesFilled: Record<string, string> = {};
    for (const outcome of room.expected_outcomes) {
      const field = room.fields[outcome];
      if (field?.value) outcomesFilled[outcome] = field.value;
    }
    const outcomesMissing = room.expected_outcomes.filter((outcome) => !outcomesFilled[outcome]);

    const summary =
      `Room ended with status=${room.status} reason=${room.stop_reason} ` +
      `after ${room.turn_count} turns. Filled ${requiredFilled}/${requiredTotal} expected outcomes.`;

	    return {
	      room_id: room.id,
	      status: room.status,
	      stop_reason: room.stop_reason,
	      stop_detail: room.stop_detail,
	      execution_mode: room.execution_mode,
        managed_coverage: room.managed_coverage,
        product_owned: room.product_owned,
	      runner_certification: room.runner_certification,
	      automatic_recovery_eligible: room.automatic_recovery_eligible,
	      attempt_status: room.attempt_status,
	      active_runner_id: room.active_runner_id,
	      last_recovery_reason: room.last_recovery_reason,
	      execution_attention: room.execution_attention,
	      root_cause_hints: room.root_cause_hints,
	      repair_hint: room.repair_hint,
	      recovery_actions: room.recovery_actions,
	      start_slo: room.start_slo,
	      turn_count: room.turn_count,
	      required_total: requiredTotal,
	      required_filled: requiredFilled,
      expected_outcomes: room.expected_outcomes,
      outcomes_filled: outcomesFilled,
      outcomes_missing: outcomesMissing,
	      outcomes_completion: {
	        filled: room.expected_outcomes.length - outcomesMissing.length,
	        total: room.expected_outcomes.length
	      },
	      fields: room.fields,
        transcript_included: includeTranscript,
	      transcript,
	      summary
	    };
  }
}

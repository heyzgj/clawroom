import { badRequest, json, normalizePlatformError } from "./worker_util";

type Env = {
  PARTICIPANT_ONLINE_STALE_SECONDS?: string;
  ROOM_REGISTRY_MAX_EVENTS?: string;
  ROOM_ACTIVE_STALE_SECONDS?: string;
  ROOM_NEAR_DEADLINE_SECONDS?: string;
  REPAIR_ISSUED_STALE_SECONDS?: string;
  CLAWROOM_BUDGET_MONTHLY_ROOMS?: string;
  CLAWROOM_BUDGET_MONTHLY_EVENTS?: string;
  CLAWROOM_BUDGET_MAX_ACTIVE_ROOMS?: string;
};

type LifecycleState = "submitted" | "working" | "input_required" | "completed" | "failed" | "canceled";
type HealthState = "healthy" | "attention" | "degraded";
type BudgetState = "normal" | "warm" | "hot";
type ExecutionAttentionState = "healthy" | "attention" | "takeover_recommended" | "takeover_required";
type RunnerCertification = "none" | "candidate" | "certified";
type ManagedCoverage = "none" | "partial" | "full";
type RecoveryPolicy = "automatic" | "takeover_only";
type RootCauseConfidence = "low" | "medium" | "high";

type RootCauseHint = {
  code: string;
  confidence: RootCauseConfidence;
  summary: string;
  evidence: string[];
};

type RootCauseBucket = {
  code: string;
  confidence: RootCauseConfidence | null;
  summary: string | null;
  rooms: number;
};

type RegistryCacheEntry<T> = {
  expiresAtMs: number;
  value: T;
};

type ParticipantSummary = {
  name: string;
  joined: boolean;
  online: boolean;
  last_seen_at: string | null;
  done: boolean;
  waiting_owner: boolean;
  client_name: string | null;
};

type RunnerAttemptSummary = {
  attempt_id: string;
  participant: string;
  runner_id: string;
  execution_mode: string;
  status: string;
  phase: string;
  phase_detail: string | null;
  phase_updated_at: string | null;
  phase_age_ms: number | null;
  lease_remaining_ms: number | null;
  managed_certified: boolean;
  recovery_policy: RecoveryPolicy;
  claimed_at: string;
  updated_at: string;
  lease_expires_at: string | null;
  released_at: string | null;
  restart_count: number;
  replacement_count: number;
  supersedes_run_id: string | null;
  log_ref: string | null;
  last_error: string | null;
  last_recovery_reason: string | null;
  current: boolean;
};

type RoomSnapshotIn = {
  id: string;
  topic: string;
  goal: string;
  status: string;
  protocol_version?: number;
  capabilities?: string[];
  lifecycle_state?: string;
  required_fields?: string[];
  expected_outcomes?: string[];
  fields?: Record<string, { value?: string }>;
  stop_reason?: string | null;
  stop_detail?: string | null;
  created_at: string;
  updated_at: string;
  turn_count: number;
  stall_count?: number;
  deadline_at?: string;
  turn_limit?: number;
  stall_limit?: number;
  timeout_minutes?: number;
  ttl_minutes?: number;
  execution_mode?: string;
  runner_certification?: RunnerCertification;
  managed_coverage?: ManagedCoverage;
  product_owned?: boolean;
  automatic_recovery_eligible?: boolean;
  attempt_status?: string;
  active_runner_id?: string | null;
  active_runner_count?: number;
  last_recovery_reason?: string | null;
  execution_attention?: {
    state?: string;
    reasons?: string[];
    summary?: string | null;
    next_action?: string | null;
    takeover_required?: boolean;
  };
  root_cause_hints?: RootCauseHint[];
  recovery_actions?: Array<{
    action_id?: string;
    participant?: string;
    kind?: string;
    status?: string;
    reason?: string;
    summary?: string | null;
    created_at?: string;
    updated_at?: string;
    issued_at?: string | null;
    resolved_at?: string | null;
    issue_count?: number;
    current?: boolean;
  }>;
  start_slo?: {
    room_created_at?: string;
    first_joined_at?: string | null;
    first_relay_at?: string | null;
    join_latency_ms?: number | null;
    first_relay_latency_ms?: number | null;
  };
  participants?: ParticipantSummary[];
  runner_attempts?: RunnerAttemptSummary[];
};

type RegistryRoom = {
  room_id: string;
  topic: string;
  goal: string;
  status: string;
  protocol_version: number;
  capabilities: string[];
  lifecycle_state: LifecycleState;
  stop_reason: string | null;
  stop_detail: string | null;
  created_at: string;
  updated_at: string;
  turn_count: number;
  stall_count: number;
  deadline_at: string;
  turn_limit: number;
  stall_limit: number;
  timeout_minutes: number;
  ttl_minutes: number;
  execution_mode: string;
  runner_certification: RunnerCertification;
  managed_coverage: ManagedCoverage;
  product_owned: boolean;
  automatic_recovery_eligible: boolean;
  attempt_status: string;
  active_runner_id: string | null;
  active_runner_count: number;
  last_recovery_reason: string | null;
  execution_attention_state: ExecutionAttentionState;
  execution_attention_summary: string | null;
  execution_attention_reasons: string[];
  primary_root_cause_code: string | null;
  primary_root_cause_confidence: RootCauseConfidence | null;
  primary_root_cause_summary: string | null;
  root_cause_hints: RootCauseHint[];
  execution_next_action: string | null;
  takeover_required: boolean;
  recovery_pending_count: number;
  recovery_issued_count: number;
  start_join_latency_ms: number | null;
  start_first_relay_latency_ms: number | null;
  required_outcomes_total: number;
  filled_outcomes: number;
  time_remaining_seconds: number | null;
  health_state: HealthState;
  health_reasons: string[];
  budget_state: BudgetState;
  budget_reasons: string[];
  participants_total: number;
  participants_joined: number;
  participants_online: number;
  participants_waiting_owner: number;
  participants: ParticipantSummary[];
  runner_attempts: RunnerAttemptSummary[];
};

function nowIso(): string {
  return new Date().toISOString();
}

function parsePositiveInt(value: string | null, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(Math.floor(parsed), max);
}

function parseOptionalPositiveInt(value: unknown): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Math.floor(parsed);
}

const DEFAULT_REPAIR_ISSUED_STALE_SECONDS = 90;
const ROOM_LIST_CACHE_TTL_MS = 2_000;
const OVERVIEW_CACHE_TTL_MS = 2_500;

function normalizeStringList(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((value) => String(value || "").trim()).filter(Boolean).slice(0, 64);
}

function normalizeFields(raw: unknown): Record<string, { value: string }> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, { value: string }> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const cleanKey = String(key || "").trim();
    if (!cleanKey || !value || typeof value !== "object" || Array.isArray(value)) continue;
    const cleanValue = String((value as Record<string, unknown>).value || "").trim();
    if (!cleanValue) continue;
    out[cleanKey] = { value: cleanValue.slice(0, 500) };
  }
  return out;
}

function normalizeRunnerAttempts(raw: unknown): RunnerAttemptSummary[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((entry) => {
    const row = entry as Record<string, unknown>;
    return {
      attempt_id: String(row.attempt_id || "").slice(0, 160),
      participant: String(row.participant || "").slice(0, 120),
      runner_id: String(row.runner_id || "").slice(0, 160),
      execution_mode: String(row.execution_mode || "compatibility").slice(0, 40),
      status: String(row.status || "pending").slice(0, 40),
      phase: String(row.phase || "claimed").slice(0, 60),
      phase_detail: row.phase_detail ? String(row.phase_detail).slice(0, 240) : null,
      phase_updated_at: row.phase_updated_at ? String(row.phase_updated_at) : null,
      phase_age_ms: parseOptionalPositiveInt(row.phase_age_ms),
      lease_remaining_ms: row.lease_remaining_ms == null ? null : Number.isFinite(Number(row.lease_remaining_ms)) ? Math.round(Number(row.lease_remaining_ms)) : null,
      managed_certified: Boolean(row.managed_certified),
      recovery_policy: normalizeRecoveryPolicy(row.recovery_policy),
      claimed_at: String(row.claimed_at || ""),
      updated_at: String(row.updated_at || ""),
      lease_expires_at: row.lease_expires_at ? String(row.lease_expires_at) : null,
      released_at: row.released_at ? String(row.released_at) : null,
      restart_count: Number(row.restart_count || 0),
      replacement_count: Math.max(0, Number(row.replacement_count || 0)),
      supersedes_run_id: row.supersedes_run_id ? String(row.supersedes_run_id).slice(0, 120) : null,
      log_ref: row.log_ref ? String(row.log_ref).slice(0, 500) : null,
      last_error: row.last_error ? String(row.last_error).slice(0, 500) : null,
      last_recovery_reason: row.last_recovery_reason ? String(row.last_recovery_reason).slice(0, 240) : null,
      current: Boolean(row.current),
    };
  });
}

function countCurrentRecoveryActions(raw: unknown): { pending: number; issued: number } {
  if (!Array.isArray(raw)) return { pending: 0, issued: 0 };
  let pending = 0;
  let issued = 0;
  for (const entry of raw) {
    const row = entry as Record<string, unknown>;
    if (!Boolean(row.current)) continue;
    const status = String(row.status || "pending").trim().toLowerCase();
    if (status === "issued") issued += 1;
    else if (status === "pending") pending += 1;
  }
  return { pending, issued };
}

function normalizeExecutionAttentionState(raw: unknown): ExecutionAttentionState {
  const value = String(raw || "healthy").trim().toLowerCase();
  if (value === "attention") return "attention";
  if (value === "takeover_recommended") return "takeover_recommended";
  if (value === "takeover_required") return "takeover_required";
  return "healthy";
}

function normalizeRootCauseConfidence(raw: unknown): RootCauseConfidence {
  const value = String(raw || "low").trim().toLowerCase();
  if (value === "high") return "high";
  if (value === "medium") return "medium";
  return "low";
}

function normalizeRootCauseHints(raw: unknown): RootCauseHint[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry) => {
      const row = entry as Record<string, unknown>;
      return {
        code: String(row.code || "").trim().slice(0, 120),
        confidence: normalizeRootCauseConfidence(row.confidence),
        summary: String(row.summary || "").trim().slice(0, 500),
        evidence: normalizeStringList(row.evidence).slice(0, 8).map((item) => item.slice(0, 240)),
      };
    })
    .filter((hint) => hint.code && hint.summary)
    .slice(0, 10);
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

function normalizeRecoveryPolicy(raw: unknown): RecoveryPolicy {
  return String(raw || "takeover_only").trim().toLowerCase() === "automatic" ? "automatic" : "takeover_only";
}

function countFilledRequired(requiredFields: string[], fields: Record<string, { value: string }>): number {
  let count = 0;
  for (const required of requiredFields) {
    const key = String(required || "").trim();
    if (key && fields[key]?.value) count += 1;
  }
  return count;
}

function normalizeLifecycle(raw: string, status: string, waitingOwner: boolean, stopReason: string): LifecycleState {
  const value = String(raw || "").trim().toLowerCase();
  if (value === "submitted") return "submitted";
  if (value === "working") return "working";
  if (value === "input_required") return "input_required";
  if (value === "completed") return "completed";
  if (value === "failed") return "failed";
  if (value === "canceled") return "canceled";
  if (status === "closed") {
    const reason = String(stopReason || "").trim().toLowerCase();
    if (reason === "goal_done" || reason === "mutual_done") return "completed";
    if (reason === "manual_close") return "canceled";
    return "failed";
  }
  if (waitingOwner) return "input_required";
  return "working";
}

function timeRemainingSeconds(status: string, deadlineAt: string): number | null {
  if (String(status) !== "active") return null;
  const deadlineMs = Date.parse(String(deadlineAt || ""));
  if (!Number.isFinite(deadlineMs)) return null;
  return Math.max(0, Math.round((deadlineMs - Date.now()) / 1000));
}

function percentile(values: number[], ratio: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.max(0, Math.min(sorted.length - 1, Math.ceil(sorted.length * ratio) - 1));
  return sorted[index];
}

function summarizeRootCauseBuckets(rows: Record<string, unknown>[]): RootCauseBucket[] {
  return rows
    .map((row) => ({
      code: String(row.primary_root_cause_code || "").trim(),
      confidence: row.primary_root_cause_confidence
        ? normalizeRootCauseConfidence(row.primary_root_cause_confidence)
        : null,
      summary: row.primary_root_cause_summary ? String(row.primary_root_cause_summary) : null,
      rooms: Number(row.rooms || 0),
    }))
    .filter((bucket) => bucket.code && bucket.rooms > 0)
    .slice(0, 8);
}

export class RoomRegistryDurableObject implements DurableObject {
  private state: DurableObjectState;
  private env: Env;
  private sql: SqlStorage;
  private schemaReady = false;
  private roomListCache = new Map<string, RegistryCacheEntry<RegistryRoom[]>>();
  private overviewCache = new Map<string, RegistryCacheEntry<Record<string, unknown>>>();
  private diagnostics = {
    room_list_cache_hits: 0,
    room_list_cache_misses: 0,
    overview_cache_hits: 0,
    overview_cache_misses: 0,
    derived_cache_invalidations: 0,
  };

  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;
    this.sql = state.storage.sql;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    try {
      this.ensureSchema();

      if (request.method === "POST" && url.pathname === "/internal/upsert") {
        return await this.handleUpsert(request);
      }
      if (request.method === "POST" && url.pathname === "/internal/remove") {
        return await this.handleRemove(request);
      }
      if (request.method === "GET" && url.pathname === "/monitor/overview") {
        return await this.handleOverview(request);
      }
      if (request.method === "GET" && url.pathname === "/monitor/summary") {
        return await this.handleSummary(request);
      }
      if (request.method === "GET" && url.pathname === "/monitor/rooms") {
        return await this.handleRooms(request);
      }
      if (request.method === "GET" && url.pathname === "/monitor/events") {
        return await this.handleEvents(request);
      }

      return json({ error: "not_found" }, { status: 404 });
    } catch (error: unknown) {
      if (error instanceof Response) return error;
      const normalized = normalizePlatformError(error);
      if (normalized) return normalized;
      return json({ error: "internal_error", message: String((error as Error)?.message || error) }, { status: 500 });
    }
  }

  private ensureSchema(): void {
    if (this.schemaReady) return;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS rooms (
	        room_id TEXT PRIMARY KEY,
	        topic TEXT NOT NULL,
	        goal TEXT NOT NULL,
	        status TEXT NOT NULL,
        protocol_version INTEGER NOT NULL DEFAULT 1,
        capabilities_json TEXT NOT NULL DEFAULT '[]',
        lifecycle_state TEXT NOT NULL,
        stop_reason TEXT,
        stop_detail TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        turn_count INTEGER NOT NULL,
        stall_count INTEGER NOT NULL DEFAULT 0,
        deadline_at TEXT NOT NULL DEFAULT '',
	        turn_limit INTEGER NOT NULL DEFAULT 0,
	        stall_limit INTEGER NOT NULL DEFAULT 0,
	        timeout_minutes INTEGER NOT NULL DEFAULT 0,
	        ttl_minutes INTEGER NOT NULL DEFAULT 0,
	        execution_mode TEXT NOT NULL DEFAULT 'compatibility',
	        runner_certification TEXT NOT NULL DEFAULT 'none',
	        managed_coverage TEXT NOT NULL DEFAULT 'none',
	        product_owned INTEGER NOT NULL DEFAULT 0,
	        automatic_recovery_eligible INTEGER NOT NULL DEFAULT 0,
	        attempt_status TEXT NOT NULL DEFAULT 'pending',
	        active_runner_id TEXT,
	        active_runner_count INTEGER NOT NULL DEFAULT 0,
	        last_recovery_reason TEXT,
	        execution_attention_state TEXT NOT NULL DEFAULT 'healthy',
	        execution_attention_summary TEXT,
	        execution_attention_reasons_json TEXT NOT NULL DEFAULT '[]',
          primary_root_cause_code TEXT,
          primary_root_cause_confidence TEXT,
          primary_root_cause_summary TEXT,
          root_cause_hints_json TEXT NOT NULL DEFAULT '[]',
	        execution_next_action TEXT,
	        takeover_required INTEGER NOT NULL DEFAULT 0,
	        recovery_pending_count INTEGER NOT NULL DEFAULT 0,
	        recovery_issued_count INTEGER NOT NULL DEFAULT 0,
	        start_join_latency_ms INTEGER,
	        start_first_relay_ms INTEGER,
	        required_outcomes_total INTEGER NOT NULL DEFAULT 0,
	        filled_outcomes INTEGER NOT NULL DEFAULT 0,
	        time_remaining_seconds INTEGER,
        health_state TEXT NOT NULL DEFAULT 'healthy',
        health_reasons_json TEXT NOT NULL DEFAULT '[]',
	        budget_state TEXT NOT NULL DEFAULT 'normal',
	        budget_reasons_json TEXT NOT NULL DEFAULT '[]',
	        participants_total INTEGER NOT NULL,
	        participants_joined INTEGER NOT NULL,
	        participants_online INTEGER NOT NULL,
	        participants_waiting_owner INTEGER NOT NULL,
	        participants_json TEXT NOT NULL,
	        runner_attempts_json TEXT NOT NULL DEFAULT '[]'
	      );

      CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        type TEXT NOT NULL,
        room_id TEXT NOT NULL,
        payload_json TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_events_type_created_at ON events(type, created_at DESC);
    `);
    this.ensureRoomColumns();
    this.sql.exec(`
      CREATE INDEX IF NOT EXISTS idx_rooms_status_updated ON rooms(status, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_rooms_health_state ON rooms(health_state, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_rooms_budget_state ON rooms(budget_state, updated_at DESC);
      CREATE INDEX IF NOT EXISTS idx_rooms_deadline ON rooms(status, deadline_at ASC);
    `);
    this.schemaReady = true;
  }

  private ensureRoomColumns(): void {
    const addColumn = (name: string, sql: string) => {
      try {
        this.sql.exec(`ALTER TABLE rooms ADD COLUMN ${sql}`);
      } catch (error: unknown) {
        const message = String((error as Error)?.message || error || "").toLowerCase();
        if (!message.includes("duplicate column name")) {
          throw error;
        }
      }
    };
    addColumn("protocol_version", "protocol_version INTEGER NOT NULL DEFAULT 1");
    addColumn("capabilities_json", "capabilities_json TEXT NOT NULL DEFAULT '[]'");
    addColumn("stall_count", "stall_count INTEGER NOT NULL DEFAULT 0");
    addColumn("deadline_at", "deadline_at TEXT NOT NULL DEFAULT ''");
    addColumn("turn_limit", "turn_limit INTEGER NOT NULL DEFAULT 0");
    addColumn("stall_limit", "stall_limit INTEGER NOT NULL DEFAULT 0");
    addColumn("timeout_minutes", "timeout_minutes INTEGER NOT NULL DEFAULT 0");
    addColumn("ttl_minutes", "ttl_minutes INTEGER NOT NULL DEFAULT 0");
	    addColumn("required_outcomes_total", "required_outcomes_total INTEGER NOT NULL DEFAULT 0");
	    addColumn("filled_outcomes", "filled_outcomes INTEGER NOT NULL DEFAULT 0");
	    addColumn("time_remaining_seconds", "time_remaining_seconds INTEGER");
	    addColumn("execution_mode", "execution_mode TEXT NOT NULL DEFAULT 'compatibility'");
	    addColumn("runner_certification", "runner_certification TEXT NOT NULL DEFAULT 'none'");
	    addColumn("managed_coverage", "managed_coverage TEXT NOT NULL DEFAULT 'none'");
	    addColumn("product_owned", "product_owned INTEGER NOT NULL DEFAULT 0");
	    addColumn("automatic_recovery_eligible", "automatic_recovery_eligible INTEGER NOT NULL DEFAULT 0");
	    addColumn("attempt_status", "attempt_status TEXT NOT NULL DEFAULT 'pending'");
	    addColumn("active_runner_id", "active_runner_id TEXT");
	    addColumn("active_runner_count", "active_runner_count INTEGER NOT NULL DEFAULT 0");
	    addColumn("last_recovery_reason", "last_recovery_reason TEXT");
	    addColumn("execution_attention_state", "execution_attention_state TEXT NOT NULL DEFAULT 'healthy'");
	    addColumn("execution_attention_summary", "execution_attention_summary TEXT");
	    addColumn("execution_attention_reasons_json", "execution_attention_reasons_json TEXT NOT NULL DEFAULT '[]'");
	    addColumn("primary_root_cause_code", "primary_root_cause_code TEXT");
	    addColumn("primary_root_cause_confidence", "primary_root_cause_confidence TEXT");
	    addColumn("primary_root_cause_summary", "primary_root_cause_summary TEXT");
	    addColumn("root_cause_hints_json", "root_cause_hints_json TEXT NOT NULL DEFAULT '[]'");
	    addColumn("execution_next_action", "execution_next_action TEXT");
	    addColumn("takeover_required", "takeover_required INTEGER NOT NULL DEFAULT 0");
	    addColumn("recovery_pending_count", "recovery_pending_count INTEGER NOT NULL DEFAULT 0");
	    addColumn("recovery_issued_count", "recovery_issued_count INTEGER NOT NULL DEFAULT 0");
	    addColumn("start_join_latency_ms", "start_join_latency_ms INTEGER");
	    addColumn("start_first_relay_ms", "start_first_relay_ms INTEGER");
	    addColumn("health_state", "health_state TEXT NOT NULL DEFAULT 'healthy'");
	    addColumn("health_reasons_json", "health_reasons_json TEXT NOT NULL DEFAULT '[]'");
	    addColumn("budget_state", "budget_state TEXT NOT NULL DEFAULT 'normal'");
	    addColumn("budget_reasons_json", "budget_reasons_json TEXT NOT NULL DEFAULT '[]'");
	    addColumn("runner_attempts_json", "runner_attempts_json TEXT NOT NULL DEFAULT '[]'");
  }

  private staleSeconds(): number {
    const raw = Number(this.env.PARTICIPANT_ONLINE_STALE_SECONDS ?? 30);
    if (!Number.isFinite(raw)) return 30;
    return Math.max(5, Math.min(300, Math.floor(raw)));
  }

  private maxEvents(): number {
    const raw = Number(this.env.ROOM_REGISTRY_MAX_EVENTS ?? 8000);
    if (!Number.isFinite(raw)) return 8000;
    return Math.max(1000, Math.min(50000, Math.floor(raw)));
  }

  private activeStaleSeconds(): number {
    const raw = Number(this.env.ROOM_ACTIVE_STALE_SECONDS ?? 90);
    if (!Number.isFinite(raw)) return 90;
    return Math.max(15, Math.min(3600, Math.floor(raw)));
  }

  private nearDeadlineSeconds(): number {
    const raw = Number(this.env.ROOM_NEAR_DEADLINE_SECONDS ?? 120);
    if (!Number.isFinite(raw)) return 120;
    return Math.max(30, Math.min(3600, Math.floor(raw)));
  }

  private repairIssuedStaleSeconds(): number {
    const raw = Number(this.env.REPAIR_ISSUED_STALE_SECONDS ?? DEFAULT_REPAIR_ISSUED_STALE_SECONDS);
    if (!Number.isFinite(raw)) return DEFAULT_REPAIR_ISSUED_STALE_SECONDS;
    return Math.max(15, Math.min(3600, Math.floor(raw)));
  }

  private monthlyRoomsBudget(): number | null {
    return parseOptionalPositiveInt(this.env.CLAWROOM_BUDGET_MONTHLY_ROOMS ?? null);
  }

  private monthlyEventsBudget(): number | null {
    return parseOptionalPositiveInt(this.env.CLAWROOM_BUDGET_MONTHLY_EVENTS ?? null);
  }

  private maxActiveRoomsBudget(): number | null {
    return parseOptionalPositiveInt(this.env.CLAWROOM_BUDGET_MAX_ACTIVE_ROOMS ?? null);
  }

  private normalizeParticipants(raw: unknown): ParticipantSummary[] {
    if (!Array.isArray(raw)) return [];
    return raw.map((entry, idx) => {
      const row = entry as Record<string, unknown>;
      const name = String(row?.name || `participant_${idx + 1}`).slice(0, 120);
      const lastSeenRaw = row?.last_seen_at ? String(row.last_seen_at) : "";
      const lastSeen = lastSeenRaw || null;
      return {
        name,
        joined: Boolean(row?.joined),
        online: Boolean(row?.online),
        last_seen_at: lastSeen,
        done: Boolean(row?.done),
        waiting_owner: Boolean(row?.waiting_owner),
        client_name: row?.client_name ? String(row.client_name).slice(0, 120) : null
      };
    });
  }

  private recalcParticipantCounts(participants: ParticipantSummary[]): {
    total: number;
    joined: number;
    online: number;
    waitingOwner: number;
  } {
    const cutoff = Date.now() - this.staleSeconds() * 1000;
    let joined = 0;
    let online = 0;
    let waitingOwner = 0;
    for (const participant of participants) {
      if (participant.joined) joined += 1;
      if (participant.waiting_owner) waitingOwner += 1;
      const lastSeenTs = participant.last_seen_at ? Date.parse(participant.last_seen_at) : Number.NaN;
      const fresh = Number.isFinite(lastSeenTs) && lastSeenTs >= cutoff;
      if (participant.online && fresh) online += 1;
    }
    return { total: participants.length, joined, online, waitingOwner };
  }

  private deriveHealthState(input: {
    status: string;
    updated_at: string;
    participants_online: number;
    participants_waiting_owner: number;
    deadline_at: string;
    execution_mode: string;
    runner_certification: RunnerCertification;
    managed_coverage: ManagedCoverage;
    product_owned: boolean;
    automatic_recovery_eligible: boolean;
    attempt_status: string;
    active_runner_count: number;
    start_join_latency_ms: number | null;
    start_first_relay_latency_ms: number | null;
    execution_attention_state: ExecutionAttentionState;
  }): { state: HealthState; reasons: string[] } {
    if (input.status !== "active") return { state: "healthy", reasons: [] };
    const reasons: string[] = [];
    let severity = 0;
    const updatedMs = Date.parse(String(input.updated_at || ""));
    const staleThresholdMs = Date.now() - this.activeStaleSeconds() * 1000;
    const remaining = timeRemainingSeconds(input.status, input.deadline_at);

    if (input.participants_online <= 0) {
      reasons.push("no_online_participants");
      severity = Math.max(severity, 2);
    }
    if (Number.isFinite(updatedMs) && updatedMs < staleThresholdMs) {
      reasons.push("stale_active_room");
      severity = Math.max(severity, 2);
    }
    if (input.participants_waiting_owner > 0) {
      reasons.push("waiting_on_owner");
      severity = Math.max(severity, 1);
    }
    if (remaining != null && remaining <= this.nearDeadlineSeconds()) {
      reasons.push("near_deadline");
      severity = Math.max(severity, 1);
    }
    if (input.execution_mode === "compatibility" && input.active_runner_count <= 0) {
      reasons.push("compatibility_unmanaged");
      severity = Math.max(severity, 1);
    }
    if (input.execution_mode !== "compatibility" && input.managed_coverage === "partial") {
      reasons.push("managed_partial_coverage");
      severity = Math.max(severity, 1);
    }
    if (input.execution_mode !== "compatibility" && input.managed_coverage === "none") {
      reasons.push("managed_not_attached");
      severity = Math.max(severity, 2);
    }
    if (input.execution_mode !== "compatibility" && !input.product_owned) {
      reasons.push("not_product_owned");
      severity = Math.max(severity, 1);
    }
    if (input.execution_mode !== "compatibility" && input.runner_certification === "candidate" && !input.automatic_recovery_eligible) {
      reasons.push("managed_uncertified");
      severity = Math.max(severity, 1);
    }
    if (input.execution_attention_state === "takeover_recommended") {
      reasons.push("takeover_recommended");
      severity = Math.max(severity, 1);
    }
    if (input.execution_attention_state === "takeover_required") {
      reasons.push("takeover_required");
      severity = Math.max(severity, 2);
    }
    if (input.attempt_status === "stalled" || input.attempt_status === "restarting") {
      reasons.push(`attempt_${input.attempt_status}`);
      severity = Math.max(severity, 1);
    }
    if (input.attempt_status === "abandoned") {
      reasons.push("attempt_abandoned");
      severity = Math.max(severity, 2);
    }
    if (input.start_join_latency_ms != null && input.start_join_latency_ms > this.nearDeadlineSeconds() * 1_000) {
      reasons.push("slow_join_start");
      severity = Math.max(severity, 1);
    }
    if (input.start_first_relay_latency_ms != null && input.start_first_relay_latency_ms > this.nearDeadlineSeconds() * 1_000) {
      reasons.push("slow_first_relay");
      severity = Math.max(severity, 1);
    }

    return {
      state: severity >= 2 ? "degraded" : severity === 1 ? "attention" : "healthy",
      reasons,
    };
  }

  private deriveBudgetState(input: {
    status: string;
    turn_count: number;
    turn_limit: number;
    stall_count: number;
    stall_limit: number;
    deadline_at: string;
  }): { state: BudgetState; reasons: string[] } {
    if (input.status !== "active") return { state: "normal", reasons: [] };
    const reasons: string[] = [];
    let severity = 0;
    const turnRatio = input.turn_limit > 0 ? input.turn_count / input.turn_limit : 0;
    const stallRatio = input.stall_limit > 0 ? input.stall_count / input.stall_limit : 0;
    const remaining = timeRemainingSeconds(input.status, input.deadline_at);

    if (turnRatio >= 0.9) {
      reasons.push("turn_limit_hot");
      severity = Math.max(severity, 2);
    } else if (turnRatio >= 0.7) {
      reasons.push("turn_limit_warm");
      severity = Math.max(severity, 1);
    }

    if (stallRatio >= 0.8) {
      reasons.push("stall_limit_hot");
      severity = Math.max(severity, 2);
    } else if (stallRatio >= 0.5) {
      reasons.push("stall_limit_warm");
      severity = Math.max(severity, 1);
    }

    if (remaining != null && remaining <= this.nearDeadlineSeconds()) {
      reasons.push("deadline_hot");
      severity = Math.max(severity, 2);
    } else if (remaining != null && remaining <= this.nearDeadlineSeconds() * 2) {
      reasons.push("deadline_warm");
      severity = Math.max(severity, 1);
    }

    return {
      state: severity >= 2 ? "hot" : severity === 1 ? "warm" : "normal",
      reasons,
    };
  }

  private loadRegistryRoom(row: Record<string, unknown>): RegistryRoom {
    const participants = this.normalizeParticipants(row.participants_json ? JSON.parse(String(row.participants_json)) : []);
    const runnerAttempts = normalizeRunnerAttempts(row.runner_attempts_json ? JSON.parse(String(row.runner_attempts_json)) : []);
    const rootCauseHints = normalizeRootCauseHints(row.root_cause_hints_json ? JSON.parse(String(row.root_cause_hints_json)) : []);
    const counts = this.recalcParticipantCounts(participants);
    const status = String(row.status || "active");
    const stopReason = row.stop_reason ? String(row.stop_reason) : "";
    const lifecycle = normalizeLifecycle(String(row.lifecycle_state || ""), status, counts.waitingOwner > 0, stopReason);
    const deadlineAt = String(row.deadline_at || "");
    const timeRemaining = timeRemainingSeconds(status, deadlineAt);
    const health = this.deriveHealthState({
      status,
      updated_at: String(row.updated_at || ""),
      participants_online: counts.online,
      participants_waiting_owner: counts.waitingOwner,
      deadline_at: deadlineAt,
      execution_mode: String(row.execution_mode || "compatibility"),
      runner_certification: normalizeRunnerCertification(row.runner_certification),
      managed_coverage: normalizeManagedCoverage(row.managed_coverage),
      product_owned: Boolean(row.product_owned),
      automatic_recovery_eligible: Boolean(row.automatic_recovery_eligible),
      attempt_status: String(row.attempt_status || "pending"),
      active_runner_count: Number(row.active_runner_count || 0),
      start_join_latency_ms: parseOptionalPositiveInt(row.start_join_latency_ms),
      start_first_relay_latency_ms: parseOptionalPositiveInt(row.start_first_relay_ms),
      execution_attention_state: normalizeExecutionAttentionState(row.execution_attention_state),
    });
    const budget = this.deriveBudgetState({
      status,
      turn_count: Number(row.turn_count || 0),
      turn_limit: Number(row.turn_limit || 0),
      stall_count: Number(row.stall_count || 0),
      stall_limit: Number(row.stall_limit || 0),
      deadline_at: deadlineAt,
    });
    return {
      room_id: String(row.room_id || ""),
      topic: String(row.topic || ""),
      goal: String(row.goal || ""),
      status,
      protocol_version: Number(row.protocol_version || 1),
      capabilities: normalizeStringList(row.capabilities_json ? JSON.parse(String(row.capabilities_json)) : []),
      lifecycle_state: lifecycle,
      stop_reason: stopReason || null,
      stop_detail: row.stop_detail ? String(row.stop_detail) : null,
      created_at: String(row.created_at || ""),
      updated_at: String(row.updated_at || ""),
      turn_count: Number(row.turn_count || 0),
      stall_count: Number(row.stall_count || 0),
      deadline_at: deadlineAt,
      turn_limit: Number(row.turn_limit || 0),
      stall_limit: Number(row.stall_limit || 0),
      timeout_minutes: Number(row.timeout_minutes || 0),
      ttl_minutes: Number(row.ttl_minutes || 0),
      execution_mode: String(row.execution_mode || "compatibility"),
      runner_certification: normalizeRunnerCertification(row.runner_certification),
      managed_coverage: normalizeManagedCoverage(row.managed_coverage),
      product_owned: Boolean(row.product_owned),
      automatic_recovery_eligible: Boolean(row.automatic_recovery_eligible),
      attempt_status: String(row.attempt_status || "pending"),
      active_runner_id: row.active_runner_id ? String(row.active_runner_id) : null,
      active_runner_count: Number(row.active_runner_count || 0),
      last_recovery_reason: row.last_recovery_reason ? String(row.last_recovery_reason) : null,
      execution_attention_state: normalizeExecutionAttentionState(row.execution_attention_state),
      execution_attention_summary: row.execution_attention_summary ? String(row.execution_attention_summary) : null,
      execution_attention_reasons: normalizeStringList(
        row.execution_attention_reasons_json ? JSON.parse(String(row.execution_attention_reasons_json)) : []
      ),
      primary_root_cause_code: row.primary_root_cause_code ? String(row.primary_root_cause_code) : null,
      primary_root_cause_confidence: row.primary_root_cause_confidence
        ? normalizeRootCauseConfidence(row.primary_root_cause_confidence)
        : null,
      primary_root_cause_summary: row.primary_root_cause_summary ? String(row.primary_root_cause_summary) : null,
      root_cause_hints: rootCauseHints,
      execution_next_action: row.execution_next_action ? String(row.execution_next_action) : null,
      takeover_required: Boolean(row.takeover_required),
      recovery_pending_count: Number(row.recovery_pending_count || 0),
      recovery_issued_count: Number(row.recovery_issued_count || 0),
      start_join_latency_ms: parseOptionalPositiveInt(row.start_join_latency_ms),
      start_first_relay_latency_ms: parseOptionalPositiveInt(row.start_first_relay_ms),
      required_outcomes_total: Number(row.required_outcomes_total || 0),
      filled_outcomes: Number(row.filled_outcomes || 0),
      time_remaining_seconds: timeRemaining,
      health_state: health.state,
      health_reasons: health.reasons,
      budget_state: budget.state,
      budget_reasons: budget.reasons,
      participants_total: counts.total,
      participants_joined: counts.joined,
      participants_online: counts.online,
      participants_waiting_owner: counts.waitingOwner,
      participants,
      runner_attempts: runnerAttempts,
    };
  }

  private shouldLogEvent(eventType: string, prev: Record<string, unknown> | null, next: RegistryRoom): boolean {
    if (!prev) return true;
    if (eventType === "heartbeat") {
      const prevOnline = Number(prev.participants_online || 0);
      return prevOnline !== next.participants_online;
    }
    if (
      eventType === "message" ||
      eventType === "owner_wait" ||
      eventType === "owner_resume" ||
      eventType.startsWith("runner_") ||
      eventType.startsWith("repair_") ||
      eventType.startsWith("recovery_")
    ) return true;
    const prevStatus = String(prev.status || "");
    const prevLifecycle = String(prev.lifecycle_state || "");
    const prevTurns = Number(prev.turn_count || 0);
    const prevExecutionAttention = String(prev.execution_attention_state || "");
    const prevPrimaryRootCause = String(prev.primary_root_cause_code || "");
    return (
      prevStatus !== next.status
      || prevLifecycle !== next.lifecycle_state
      || prevTurns !== next.turn_count
      || prevExecutionAttention !== next.execution_attention_state
      || prevPrimaryRootCause !== String(next.primary_root_cause_code || "")
    );
  }

  private appendEvent(eventType: string, roomId: string, payload: Record<string, unknown>): void {
    const createdAt = nowIso();
    this.sql.exec(
      "INSERT INTO events (created_at, type, room_id, payload_json) VALUES (?, ?, ?, ?)",
      createdAt,
      eventType,
      roomId,
      JSON.stringify(payload)
    );
    const max = this.maxEvents();
    const count = this.bumpMetaCounter("event_count", 1, max);
    this.sql.exec(
      "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      "last_event_created_at",
      createdAt,
    );
    this.sql.exec(
      "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      "last_event_type",
      eventType,
    );
    this.sql.exec(
      "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      "last_event_room_id",
      roomId,
    );
    const trimTarget = Math.max(1, Math.min(250, max > 0 ? Math.floor(max * 0.02) : 1));
    if (count > max) {
      const toDelete = Math.max(count - max, trimTarget);
      this.sql.exec("DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id ASC LIMIT ?)", toDelete);
      this.sql.exec("INSERT INTO meta(key, value) VALUES('event_count', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", String(Math.max(0, count - toDelete)));
    }
  }

  private invalidateDerivedCaches(): void {
    this.roomListCache.clear();
    this.overviewCache.clear();
    this.diagnostics.derived_cache_invalidations += 1;
  }

  private cacheNowMs(): number {
    return Date.now();
  }

  private roomListCacheKey(request: Request): string {
    const url = new URL(request.url);
    const limit = parsePositiveInt(url.searchParams.get("limit"), 100, 500);
    const status = String(url.searchParams.get("status") || "all").toLowerCase();
    return `${status}:${limit}`;
  }

  private overviewCacheKey(request: Request): string {
    return this.roomListCacheKey(request);
  }

  private shouldInvalidateDerivedCaches(
    eventType: string,
    prev: Record<string, unknown> | null,
    next: RegistryRoom,
  ): boolean {
    if (!prev) return true;
    if (eventType === "init" || eventType === "close" || eventType === "timeout" || eventType === "room_removed") {
      return true;
    }
    const materialPairs: Array<[string, unknown]> = [
      ["status", next.status],
      ["lifecycle_state", next.lifecycle_state],
      ["turn_count", next.turn_count],
      ["participants_online", next.participants_online],
      ["participants_waiting_owner", next.participants_waiting_owner],
      ["execution_mode", next.execution_mode],
      ["runner_certification", next.runner_certification],
      ["managed_coverage", next.managed_coverage],
      ["product_owned", next.product_owned ? 1 : 0],
      ["automatic_recovery_eligible", next.automatic_recovery_eligible ? 1 : 0],
      ["attempt_status", next.attempt_status],
      ["active_runner_count", next.active_runner_count],
      ["execution_attention_state", next.execution_attention_state],
      ["primary_root_cause_code", next.primary_root_cause_code],
      ["recovery_pending_count", next.recovery_pending_count],
      ["recovery_issued_count", next.recovery_issued_count],
      ["health_state", next.health_state],
      ["budget_state", next.budget_state],
    ];
    return materialPairs.some(([key, nextValue]) => String(prev[key] ?? "") !== String(nextValue ?? ""));
  }

  private bumpMetaCounter(key: string, delta: number, fallbackIfMissing = 0): number {
    const existingRows = this.sql.exec("SELECT value FROM meta WHERE key=? LIMIT 1", key).toArray() as Record<string, unknown>[];
    const existing = existingRows.length ? (existingRows[0] as Record<string, unknown>) : null;
    const current = existing ? Number(existing.value || 0) : fallbackIfMissing;
    const next = Math.max(0, current + delta);
    this.sql.exec(
      "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      key,
      String(next),
    );
    return next;
  }

  private getMetaValue(key: string): string | null {
    const rows = this.sql.exec("SELECT value FROM meta WHERE key=? LIMIT 1", key).toArray() as Record<string, unknown>[];
    const row = rows.length ? (rows[0] as Record<string, unknown>) : null;
    return row?.value == null ? null : String(row.value);
  }

  private async handleUpsert(request: Request): Promise<Response> {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const roomRaw = (body.room || {}) as Partial<RoomSnapshotIn>;
    const roomId = String(roomRaw.id || "").trim();
    if (!roomId) return badRequest("room.id required");

    const participants = this.normalizeParticipants(roomRaw.participants || []);
    const counts = this.recalcParticipantCounts(participants);
    const status = String(roomRaw.status || "active");
    const stopReason = roomRaw.stop_reason ? String(roomRaw.stop_reason) : "";
    const lifecycle = normalizeLifecycle(String(roomRaw.lifecycle_state || ""), status, counts.waitingOwner > 0, stopReason);
    const requiredOutcomes = normalizeStringList(roomRaw.expected_outcomes ?? roomRaw.required_fields);
    const fields = normalizeFields(roomRaw.fields);
    const filledOutcomes = countFilledRequired(requiredOutcomes, fields);
    const timeRemaining = timeRemainingSeconds(status, String(roomRaw.deadline_at || ""));
    const runnerAttempts = normalizeRunnerAttempts(roomRaw.runner_attempts || []);
    const startSlo = roomRaw.start_slo && typeof roomRaw.start_slo === "object" ? roomRaw.start_slo : {};
    const executionAttention =
      roomRaw.execution_attention && typeof roomRaw.execution_attention === "object" ? roomRaw.execution_attention : {};
    const rootCauseHints = normalizeRootCauseHints(roomRaw.root_cause_hints);
    const primaryRootCause = rootCauseHints[0] || null;
    const recoveryCounts = countCurrentRecoveryActions(roomRaw.recovery_actions);
    const health = this.deriveHealthState({
      status,
      updated_at: String(roomRaw.updated_at || nowIso()),
      participants_online: counts.online,
      participants_waiting_owner: counts.waitingOwner,
      deadline_at: String(roomRaw.deadline_at || ""),
      execution_mode: String(roomRaw.execution_mode || "compatibility"),
      runner_certification: normalizeRunnerCertification(roomRaw.runner_certification),
      managed_coverage: normalizeManagedCoverage(roomRaw.managed_coverage),
      product_owned: Boolean(roomRaw.product_owned),
      automatic_recovery_eligible: Boolean(roomRaw.automatic_recovery_eligible),
      attempt_status: String(roomRaw.attempt_status || "pending"),
      active_runner_count: Number(
        roomRaw.active_runner_count
          || runnerAttempts.filter((attempt) => attempt.status !== "exited" && attempt.status !== "replaced" && attempt.status !== "abandoned").length
          || 0
      ),
      start_join_latency_ms: parseOptionalPositiveInt(startSlo.join_latency_ms),
      start_first_relay_latency_ms: parseOptionalPositiveInt(startSlo.first_relay_latency_ms),
      execution_attention_state: normalizeExecutionAttentionState(executionAttention.state),
    });
    const budget = this.deriveBudgetState({
      status,
      turn_count: Number(roomRaw.turn_count || 0),
      turn_limit: Number(roomRaw.turn_limit || 0),
      stall_count: Number(roomRaw.stall_count || 0),
      stall_limit: Number(roomRaw.stall_limit || 0),
      deadline_at: String(roomRaw.deadline_at || ""),
    });
    const eventType = String(body.event_type || "update").slice(0, 40) || "update";

    // SqlStorage .one() throws when no rows match. We want a nullable previous
    // snapshot for diffing, so read via toArray() instead.
    const prevRows = this.sql.exec(
      `SELECT
        status,
        lifecycle_state,
        turn_count,
        participants_online,
        participants_waiting_owner,
        execution_mode,
        runner_certification,
        managed_coverage,
        product_owned,
        automatic_recovery_eligible,
        attempt_status,
        active_runner_count,
        execution_attention_state,
        primary_root_cause_code,
        recovery_pending_count,
        recovery_issued_count,
        health_state,
        budget_state
      FROM rooms
      WHERE room_id=? LIMIT 1`,
      roomId
    ).toArray() as Record<string, unknown>[];
    const prev = prevRows.length ? (prevRows[0] as Record<string, unknown>) : null;

    const next: RegistryRoom = {
      room_id: roomId,
      topic: String(roomRaw.topic || "").slice(0, 400),
      goal: String(roomRaw.goal || "").slice(0, 600),
      status,
      protocol_version: Math.max(1, Math.floor(Number(roomRaw.protocol_version || 1))),
      capabilities: normalizeStringList(roomRaw.capabilities),
      lifecycle_state: lifecycle,
      stop_reason: stopReason || null,
      stop_detail: roomRaw.stop_detail ? String(roomRaw.stop_detail) : null,
      created_at: String(roomRaw.created_at || nowIso()),
      updated_at: String(roomRaw.updated_at || nowIso()),
      turn_count: Number(roomRaw.turn_count || 0),
      stall_count: Number(roomRaw.stall_count || 0),
      deadline_at: String(roomRaw.deadline_at || ""),
      turn_limit: Number(roomRaw.turn_limit || 0),
      stall_limit: Number(roomRaw.stall_limit || 0),
      timeout_minutes: Number(roomRaw.timeout_minutes || 0),
      ttl_minutes: Number(roomRaw.ttl_minutes || 0),
      execution_mode: String(roomRaw.execution_mode || "compatibility"),
      runner_certification: normalizeRunnerCertification(roomRaw.runner_certification),
      managed_coverage: normalizeManagedCoverage(roomRaw.managed_coverage),
      product_owned: Boolean(roomRaw.product_owned),
      automatic_recovery_eligible: Boolean(roomRaw.automatic_recovery_eligible),
      attempt_status: String(roomRaw.attempt_status || "pending"),
      active_runner_id: roomRaw.active_runner_id ? String(roomRaw.active_runner_id) : null,
      active_runner_count: Number(roomRaw.active_runner_count || runnerAttempts.filter((attempt) => attempt.status !== "exited" && attempt.status !== "replaced" && attempt.status !== "abandoned").length || 0),
      last_recovery_reason: roomRaw.last_recovery_reason ? String(roomRaw.last_recovery_reason) : null,
      execution_attention_state: normalizeExecutionAttentionState(executionAttention.state),
      execution_attention_summary: executionAttention.summary ? String(executionAttention.summary) : null,
      execution_attention_reasons: normalizeStringList(executionAttention.reasons),
      primary_root_cause_code: primaryRootCause?.code ?? null,
      primary_root_cause_confidence: primaryRootCause?.confidence ?? null,
      primary_root_cause_summary: primaryRootCause?.summary ?? null,
      root_cause_hints: rootCauseHints,
      execution_next_action: executionAttention.next_action ? String(executionAttention.next_action) : null,
      takeover_required: Boolean(executionAttention.takeover_required),
      recovery_pending_count: recoveryCounts.pending,
      recovery_issued_count: recoveryCounts.issued,
      start_join_latency_ms: parseOptionalPositiveInt(startSlo.join_latency_ms),
      start_first_relay_latency_ms: parseOptionalPositiveInt(startSlo.first_relay_latency_ms),
      required_outcomes_total: requiredOutcomes.length,
      filled_outcomes: filledOutcomes,
      time_remaining_seconds: timeRemaining,
      health_state: health.state,
      health_reasons: health.reasons,
      budget_state: budget.state,
      budget_reasons: budget.reasons,
      participants_total: counts.total,
      participants_joined: counts.joined,
      participants_online: counts.online,
      participants_waiting_owner: counts.waitingOwner,
      participants,
      runner_attempts: runnerAttempts,
    };

    this.sql.exec(
      `INSERT INTO rooms (
        room_id, topic, goal, status, protocol_version, capabilities_json, lifecycle_state, stop_reason, stop_detail,
        created_at, updated_at, turn_count, stall_count, deadline_at, turn_limit, stall_limit, timeout_minutes,
        ttl_minutes, execution_mode, runner_certification, managed_coverage, product_owned, automatic_recovery_eligible, attempt_status, active_runner_id, active_runner_count, last_recovery_reason,
        execution_attention_state, execution_attention_summary, execution_attention_reasons_json, primary_root_cause_code, primary_root_cause_confidence, primary_root_cause_summary, root_cause_hints_json, execution_next_action, takeover_required,
        recovery_pending_count, recovery_issued_count, start_join_latency_ms, start_first_relay_ms, required_outcomes_total, filled_outcomes, time_remaining_seconds,
        health_state, health_reasons_json, budget_state, budget_reasons_json, participants_total, participants_joined,
        participants_online, participants_waiting_owner, participants_json, runner_attempts_json
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(room_id) DO UPDATE SET
        topic=excluded.topic,
        goal=excluded.goal,
        status=excluded.status,
        protocol_version=excluded.protocol_version,
        capabilities_json=excluded.capabilities_json,
        lifecycle_state=excluded.lifecycle_state,
        stop_reason=excluded.stop_reason,
        stop_detail=excluded.stop_detail,
        updated_at=excluded.updated_at,
        turn_count=excluded.turn_count,
        stall_count=excluded.stall_count,
        deadline_at=excluded.deadline_at,
        turn_limit=excluded.turn_limit,
        stall_limit=excluded.stall_limit,
        timeout_minutes=excluded.timeout_minutes,
        ttl_minutes=excluded.ttl_minutes,
        execution_mode=excluded.execution_mode,
        runner_certification=excluded.runner_certification,
        managed_coverage=excluded.managed_coverage,
        product_owned=excluded.product_owned,
        automatic_recovery_eligible=excluded.automatic_recovery_eligible,
        attempt_status=excluded.attempt_status,
        active_runner_id=excluded.active_runner_id,
        active_runner_count=excluded.active_runner_count,
        last_recovery_reason=excluded.last_recovery_reason,
        execution_attention_state=excluded.execution_attention_state,
        execution_attention_summary=excluded.execution_attention_summary,
        execution_attention_reasons_json=excluded.execution_attention_reasons_json,
        primary_root_cause_code=excluded.primary_root_cause_code,
        primary_root_cause_confidence=excluded.primary_root_cause_confidence,
        primary_root_cause_summary=excluded.primary_root_cause_summary,
        root_cause_hints_json=excluded.root_cause_hints_json,
        execution_next_action=excluded.execution_next_action,
        takeover_required=excluded.takeover_required,
        recovery_pending_count=excluded.recovery_pending_count,
        recovery_issued_count=excluded.recovery_issued_count,
        start_join_latency_ms=excluded.start_join_latency_ms,
        start_first_relay_ms=excluded.start_first_relay_ms,
        required_outcomes_total=excluded.required_outcomes_total,
        filled_outcomes=excluded.filled_outcomes,
        time_remaining_seconds=excluded.time_remaining_seconds,
        health_state=excluded.health_state,
        health_reasons_json=excluded.health_reasons_json,
        budget_state=excluded.budget_state,
        budget_reasons_json=excluded.budget_reasons_json,
        participants_total=excluded.participants_total,
        participants_joined=excluded.participants_joined,
        participants_online=excluded.participants_online,
        participants_waiting_owner=excluded.participants_waiting_owner,
        participants_json=excluded.participants_json,
        runner_attempts_json=excluded.runner_attempts_json`,
      next.room_id,
      next.topic,
      next.goal,
      next.status,
      next.protocol_version,
      JSON.stringify(next.capabilities),
      next.lifecycle_state,
      next.stop_reason,
      next.stop_detail,
      next.created_at,
      next.updated_at,
      next.turn_count,
      next.stall_count,
      next.deadline_at,
      next.turn_limit,
      next.stall_limit,
      next.timeout_minutes,
      next.ttl_minutes,
      next.execution_mode,
      next.runner_certification,
      next.managed_coverage,
      next.product_owned ? 1 : 0,
      next.automatic_recovery_eligible ? 1 : 0,
      next.attempt_status,
      next.active_runner_id,
      next.active_runner_count,
      next.last_recovery_reason,
      next.execution_attention_state,
      next.execution_attention_summary,
      JSON.stringify(next.execution_attention_reasons),
      next.primary_root_cause_code,
      next.primary_root_cause_confidence,
      next.primary_root_cause_summary,
      JSON.stringify(next.root_cause_hints),
      next.execution_next_action,
      next.takeover_required ? 1 : 0,
      next.recovery_pending_count,
      next.recovery_issued_count,
      next.start_join_latency_ms,
      next.start_first_relay_latency_ms,
      next.required_outcomes_total,
      next.filled_outcomes,
      next.time_remaining_seconds,
      next.health_state,
      JSON.stringify(next.health_reasons),
      next.budget_state,
      JSON.stringify(next.budget_reasons),
      next.participants_total,
      next.participants_joined,
      next.participants_online,
      next.participants_waiting_owner,
      JSON.stringify(next.participants),
      JSON.stringify(next.runner_attempts)
    );

    if (this.shouldLogEvent(eventType, prev, next)) {
      this.appendEvent(eventType, roomId, {
        room_id: roomId,
        topic: next.topic,
        status: next.status,
        lifecycle_state: next.lifecycle_state,
        turn_count: next.turn_count,
        stall_count: next.stall_count,
        participants_online: next.participants_online,
        participants_joined: next.participants_joined,
        stop_reason: next.stop_reason,
        health_state: next.health_state,
        budget_state: next.budget_state,
        execution_mode: next.execution_mode,
        runner_certification: next.runner_certification,
        managed_coverage: next.managed_coverage,
        product_owned: next.product_owned,
        automatic_recovery_eligible: next.automatic_recovery_eligible,
        attempt_status: next.attempt_status,
        active_runner_count: next.active_runner_count,
        last_recovery_reason: next.last_recovery_reason,
        execution_attention_state: next.execution_attention_state,
        primary_root_cause_code: next.primary_root_cause_code,
        primary_root_cause_confidence: next.primary_root_cause_confidence,
        primary_root_cause_summary: next.primary_root_cause_summary,
        takeover_required: next.takeover_required,
        recovery_pending_count: next.recovery_pending_count,
        recovery_issued_count: next.recovery_issued_count,
      });
    }
    if (this.shouldInvalidateDerivedCaches(eventType, prev, next)) {
      this.invalidateDerivedCaches();
    }

    return json({ ok: true });
  }

  private async handleRemove(request: Request): Promise<Response> {
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const roomId = String(body.room_id || "").trim();
    if (!roomId) return badRequest("room_id required");
    const reason = String(body.reason || "removed").slice(0, 120);
    this.sql.exec("DELETE FROM rooms WHERE room_id=?", roomId);
    this.appendEvent("room_removed", roomId, { room_id: roomId, reason });
    this.invalidateDerivedCaches();
    return json({ ok: true });
  }

  private buildRoomList(request: Request): RegistryRoom[] {
    const nowMs = this.cacheNowMs();
    const cacheKey = this.roomListCacheKey(request);
    const cached = this.roomListCache.get(cacheKey);
    if (cached && cached.expiresAtMs > nowMs) {
      this.diagnostics.room_list_cache_hits += 1;
      return structuredClone(cached.value);
    }
    this.diagnostics.room_list_cache_misses += 1;

    const url = new URL(request.url);
    const limit = parsePositiveInt(url.searchParams.get("limit"), 100, 500);
    const status = String(url.searchParams.get("status") || "all").toLowerCase();

    const rows =
      status === "active"
        ? this.sql.exec("SELECT * FROM rooms WHERE status='active' ORDER BY updated_at DESC LIMIT ?", limit).toArray()
        : status === "closed"
          ? this.sql.exec("SELECT * FROM rooms WHERE status='closed' ORDER BY updated_at DESC LIMIT ?", limit).toArray()
          : this.sql.exec("SELECT * FROM rooms ORDER BY updated_at DESC LIMIT ?", limit).toArray();

    const rooms = rows.map((row) => this.loadRegistryRoom(row as Record<string, unknown>));
    this.roomListCache.set(cacheKey, {
      expiresAtMs: nowMs + ROOM_LIST_CACHE_TTL_MS,
      value: structuredClone(rooms),
    });
    return rooms;
  }

  private async handleRooms(request: Request): Promise<Response> {
    return json({ rooms: this.buildRoomList(request) });
  }

  private buildOverviewPayload(request: Request): Record<string, unknown> {
    const nowMs = this.cacheNowMs();
    const cacheKey = this.overviewCacheKey(request);
    const cached = this.overviewCache.get(cacheKey);
    if (cached && cached.expiresAtMs > nowMs) {
      this.diagnostics.overview_cache_hits += 1;
      return structuredClone(cached.value);
    }
    this.diagnostics.overview_cache_misses += 1;
    const rooms = this.buildRoomList(request);
    const activeRoomsList = rooms.filter((room) => room.status === "active");
    const repairPackageIssuedRooms = activeRoomsList.filter((room) =>
      room.execution_attention_reasons.includes("repair_package_issued")
    ).length;
    const repairClaimOverdueRooms = activeRoomsList.filter((room) =>
      room.execution_attention_reasons.includes("repair_claim_overdue")
    ).length;
    const ownerReplyOverdueRooms = activeRoomsList.filter((room) =>
      room.execution_attention_reasons.includes("owner_reply_overdue")
    ).length;
    const firstRelayRiskRooms = activeRoomsList.filter((room) =>
      room.execution_attention_reasons.includes("first_relay_at_risk")
    ).length;
    const runnerLeaseLowRooms = activeRoomsList.filter((room) =>
      room.execution_attention_reasons.includes("runner_lease_low")
    ).length;
    const activeStaleIso = new Date(nowMs - this.activeStaleSeconds() * 1000).toISOString();
    const nearDeadlineIso = new Date(nowMs + this.nearDeadlineSeconds() * 1000).toISOString();
    const aggregatesRow = this.sql.exec(
      `SELECT
        COUNT(*) AS total_rooms,
        SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active_rooms,
        SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed_rooms,
        SUM(CASE WHEN lifecycle_state='input_required' THEN 1 ELSE 0 END) AS input_required_rooms,
        SUM(CASE WHEN health_state='degraded' THEN 1 ELSE 0 END) AS degraded_rooms,
        SUM(CASE WHEN budget_state='hot' THEN 1 ELSE 0 END) AS budget_hot_rooms,
        SUM(CASE WHEN budget_state='warm' THEN 1 ELSE 0 END) AS budget_warm_rooms,
        SUM(CASE WHEN participants_online <= 0 AND status='active' THEN 1 ELSE 0 END) AS active_rooms_without_online,
        SUM(CASE WHEN updated_at <= ? AND status='active' THEN 1 ELSE 0 END) AS stale_active_rooms,
        SUM(CASE WHEN participants_waiting_owner > 0 THEN 1 ELSE 0 END) AS waiting_owner_rooms,
        SUM(CASE WHEN deadline_at != '' AND deadline_at <= ? AND status='active' THEN 1 ELSE 0 END) AS near_deadline_rooms,
        SUM(CASE WHEN turn_limit > 0 AND (turn_count * 1.0 / turn_limit) >= 0.9 AND status='active' THEN 1 ELSE 0 END) AS turn_hot_rooms,
        SUM(CASE WHEN stall_limit > 0 AND (stall_count * 1.0 / stall_limit) >= 0.8 AND status='active' THEN 1 ELSE 0 END) AS stall_hot_rooms,
        SUM(CASE WHEN status='active' THEN active_runner_count ELSE 0 END) AS active_runners,
        SUM(CASE WHEN execution_mode='compatibility' AND status='active' THEN 1 ELSE 0 END) AS compatibility_rooms,
        SUM(CASE WHEN execution_mode!='compatibility' AND runner_certification='certified' AND status='active' THEN 1 ELSE 0 END) AS certified_managed_rooms,
        SUM(CASE WHEN execution_mode!='compatibility' AND runner_certification='candidate' AND status='active' THEN 1 ELSE 0 END) AS candidate_managed_rooms,
        SUM(CASE WHEN execution_mode!='compatibility' AND managed_coverage='full' AND status='active' THEN 1 ELSE 0 END) AS full_managed_rooms,
        SUM(CASE WHEN execution_mode!='compatibility' AND managed_coverage='partial' AND status='active' THEN 1 ELSE 0 END) AS partial_managed_rooms,
        SUM(CASE WHEN product_owned=1 AND status='active' THEN 1 ELSE 0 END) AS product_owned_rooms,
        SUM(CASE WHEN automatic_recovery_eligible=1 AND status='active' THEN 1 ELSE 0 END) AS automatic_recovery_eligible_rooms,
        SUM(CASE WHEN execution_mode='compatibility' AND active_runner_count <= 0 AND status='active' THEN 1 ELSE 0 END) AS unmanaged_compatibility_rooms,
        SUM(CASE WHEN attempt_status='stalled' AND status='active' THEN 1 ELSE 0 END) AS stalled_runner_rooms,
        SUM(CASE WHEN attempt_status='restarting' AND status='active' THEN 1 ELSE 0 END) AS restarting_runner_rooms,
        SUM(CASE WHEN attempt_status='abandoned' AND status='active' THEN 1 ELSE 0 END) AS abandoned_runner_rooms,
        SUM(CASE WHEN execution_attention_state='takeover_recommended' AND status='active' THEN 1 ELSE 0 END) AS takeover_recommended_rooms,
        SUM(CASE WHEN execution_attention_state='takeover_required' AND status='active' THEN 1 ELSE 0 END) AS takeover_required_rooms,
        SUM(CASE WHEN status='active' THEN recovery_pending_count ELSE 0 END) AS recovery_pending_actions,
        SUM(CASE WHEN status='active' THEN recovery_issued_count ELSE 0 END) AS recovery_issued_actions,
        SUM(CASE WHEN status='active' AND (recovery_pending_count + recovery_issued_count) > 0 THEN 1 ELSE 0 END) AS recovery_backlog_rooms,
        SUM(CASE WHEN last_recovery_reason IS NOT NULL AND status='active' THEN 1 ELSE 0 END) AS recovery_rooms,
        SUM(CASE WHEN status='active' THEN participants_online ELSE 0 END) AS online_participants,
        SUM(CASE WHEN status='active' THEN participants_joined ELSE 0 END) AS joined_participants,
        SUM(turn_count) AS total_turns
      FROM rooms`,
      activeStaleIso,
      nearDeadlineIso,
    ).one() as Record<string, unknown> | null;

    const totalRooms = Number(aggregatesRow?.total_rooms || 0);
    const activeRooms = Number(aggregatesRow?.active_rooms || 0);
    const closedRooms = Number(aggregatesRow?.closed_rooms || 0);
    const inputRequiredRooms = Number(aggregatesRow?.input_required_rooms || 0);
    const degradedRooms = Number(aggregatesRow?.degraded_rooms || 0);
    const budgetHotRooms = Number(aggregatesRow?.budget_hot_rooms || 0);
    const budgetWarmRooms = Number(aggregatesRow?.budget_warm_rooms || 0);
    const activeRoomsWithoutOnline = Number(aggregatesRow?.active_rooms_without_online || 0);
    const staleActiveRooms = Number(aggregatesRow?.stale_active_rooms || 0);
    const waitingOwnerRooms = Number(aggregatesRow?.waiting_owner_rooms || 0);
    const nearDeadlineRooms = Number(aggregatesRow?.near_deadline_rooms || 0);
    const turnHotRooms = Number(aggregatesRow?.turn_hot_rooms || 0);
    const stallHotRooms = Number(aggregatesRow?.stall_hot_rooms || 0);
    const activeRunners = Number(aggregatesRow?.active_runners || 0);
    const compatibilityRooms = Number(aggregatesRow?.compatibility_rooms || 0);
    const certifiedManagedRooms = Number(aggregatesRow?.certified_managed_rooms || 0);
    const candidateManagedRooms = Number(aggregatesRow?.candidate_managed_rooms || 0);
    const fullManagedRooms = Number(aggregatesRow?.full_managed_rooms || 0);
    const partialManagedRooms = Number(aggregatesRow?.partial_managed_rooms || 0);
    const productOwnedRooms = Number(aggregatesRow?.product_owned_rooms || 0);
    const automaticRecoveryEligibleRooms = Number(aggregatesRow?.automatic_recovery_eligible_rooms || 0);
    const unmanagedCompatibilityRooms = Number(aggregatesRow?.unmanaged_compatibility_rooms || 0);
    const stalledRunnerRooms = Number(aggregatesRow?.stalled_runner_rooms || 0);
    const restartingRunnerRooms = Number(aggregatesRow?.restarting_runner_rooms || 0);
    const abandonedRunnerRooms = Number(aggregatesRow?.abandoned_runner_rooms || 0);
    const takeoverRecommendedRooms = Number(aggregatesRow?.takeover_recommended_rooms || 0);
    const takeoverRequiredRooms = Number(aggregatesRow?.takeover_required_rooms || 0);
    const recoveryPendingActions = Number(aggregatesRow?.recovery_pending_actions || 0);
    const recoveryIssuedActions = Number(aggregatesRow?.recovery_issued_actions || 0);
    const recoveryBacklogRooms = Number(aggregatesRow?.recovery_backlog_rooms || 0);
    const recoveryRooms = Number(aggregatesRow?.recovery_rooms || 0);
    const onlineParticipants = Number(aggregatesRow?.online_participants || 0);
    const joinedParticipants = Number(aggregatesRow?.joined_participants || 0);
    const totalTurns = Number(aggregatesRow?.total_turns || 0);
    const joinLatencies = rooms
      .map((room) => room.start_join_latency_ms)
      .filter((value): value is number => Number.isFinite(value as number));
    const firstRelayLatencies = rooms
      .map((room) => room.start_first_relay_latency_ms)
      .filter((value): value is number => Number.isFinite(value as number));

    const oldestActiveRows = this.sql.exec(
      "SELECT updated_at FROM rooms WHERE status='active' ORDER BY updated_at ASC LIMIT 1"
    ).toArray() as Record<string, unknown>[];
    const oldestActiveRow = oldestActiveRows.length ? oldestActiveRows[0] : null;
    const oldestActiveUpdatedAt = oldestActiveRow?.updated_at ? String(oldestActiveRow.updated_at) : "";
    const oldestActiveRoomAgeSeconds = oldestActiveUpdatedAt
      ? Math.max(0, Math.floor((nowMs - Date.parse(oldestActiveUpdatedAt)) / 1000))
      : 0;

    const eventRows = Number(this.getMetaValue("event_count") || 0);
    const lastEventAt = this.getMetaValue("last_event_created_at");
    const lastEventType = this.getMetaValue("last_event_type");
    const lastEventRoomId = this.getMetaValue("last_event_room_id");
    const recentEventRows = this.sql.exec(
      "SELECT created_at, type, room_id FROM events ORDER BY id DESC LIMIT 250"
    ).toArray() as Record<string, unknown>[];
    const countRecentByType = (windowMs: number): Record<string, number> => {
      const cutoff = nowMs - windowMs;
      const counts: Record<string, number> = {};
      for (const row of recentEventRows) {
        const createdAt = String(row.created_at || "");
        const createdMs = Date.parse(createdAt);
        if (!Number.isFinite(createdMs) || createdMs < cutoff) continue;
        const type = String(row.type || "").trim();
        if (!type) continue;
        counts[type] = Number(counts[type] || 0) + 1;
      }
      return counts;
    };
    const countRecentEvents = (windowMs: number): number => {
      const cutoff = nowMs - windowMs;
      let count = 0;
      for (const row of recentEventRows) {
        const createdAt = String(row.created_at || "");
        const createdMs = Date.parse(createdAt);
        if (Number.isFinite(createdMs) && createdMs >= cutoff) count += 1;
      }
      return count;
    };
    const events5m = countRecentEvents(5 * 60_000);
    const events24h = countRecentEvents(24 * 60 * 60_000);
    const typeCounts5m = countRecentByType(5 * 60_000);
    const typeCounts1h = countRecentByType(60 * 60_000);
    const typeCounts24h = countRecentByType(24 * 60 * 60_000);
    const stopReasonRows = this.sql.exec(
      "SELECT stop_reason, COUNT(*) AS c FROM rooms WHERE status='closed' AND updated_at >= ? GROUP BY stop_reason",
      new Date(Date.now() - 24 * 60 * 60_000).toISOString()
    ).toArray();
    const stopReasons24h: Record<string, number> = {};
    for (const row of stopReasonRows) {
      const key = String((row as Record<string, unknown>).stop_reason || "unknown").trim() || "unknown";
      stopReasons24h[key] = Number((row as Record<string, unknown>).c || 0);
    }
    const rootCauseActiveRows = this.sql.exec(
      `SELECT
        primary_root_cause_code,
        primary_root_cause_confidence,
        primary_root_cause_summary,
        COUNT(*) AS rooms
      FROM rooms
      WHERE status='active' AND primary_root_cause_code IS NOT NULL
      GROUP BY primary_root_cause_code, primary_root_cause_confidence, primary_root_cause_summary
      ORDER BY rooms DESC, primary_root_cause_code ASC
      LIMIT 5`
    ).toArray() as Record<string, unknown>[];
    const rootCauseRecentRows = this.sql.exec(
      `SELECT
        primary_root_cause_code,
        primary_root_cause_confidence,
        primary_root_cause_summary,
        COUNT(*) AS rooms
      FROM rooms
      WHERE updated_at >= ? AND primary_root_cause_code IS NOT NULL
      GROUP BY primary_root_cause_code, primary_root_cause_confidence, primary_root_cause_summary
      ORDER BY rooms DESC, primary_root_cause_code ASC
      LIMIT 5`,
      new Date(Date.now() - 24 * 60 * 60_000).toISOString()
    ).toArray() as Record<string, unknown>[];
    const activeRootCauses = summarizeRootCauseBuckets(rootCauseActiveRows);
    const recentRootCauses24h = summarizeRootCauseBuckets(rootCauseRecentRows);
    const dominantActiveRootCause = activeRootCauses[0] || null;
    const monthlyRoomsBudget = this.monthlyRoomsBudget();
    const monthlyEventsBudget = this.monthlyEventsBudget();
    const maxActiveRoomsBudget = this.maxActiveRoomsBudget();
    const projectedMonthlyRooms = Number(typeCounts24h.init || 0) * 30;
    const projectedMonthlyEvents = events24h * 30;
    const roomBudgetRatio = monthlyRoomsBudget ? projectedMonthlyRooms / monthlyRoomsBudget : 0;
    const eventBudgetRatio = monthlyEventsBudget ? projectedMonthlyEvents / monthlyEventsBudget : 0;
    const activeBudgetRatio = maxActiveRoomsBudget ? activeRooms / maxActiveRoomsBudget : 0;
    const budgetUtilization = Math.max(roomBudgetRatio, eventBudgetRatio, activeBudgetRatio, 0);
    const budgetConfigured = Boolean(monthlyRoomsBudget || monthlyEventsBudget || maxActiveRoomsBudget);
    const budgetState =
      !budgetConfigured
        ? "normal"
        : budgetUtilization >= 1
          ? "hot"
          : budgetUtilization >= 0.8
            ? "warm"
            : "normal";
    const registryLastEventAgeSeconds = lastEventAt
      ? Math.max(0, Math.floor((nowMs - Date.parse(String(lastEventAt))) / 1000))
      : null;
    const registryMode =
      activeRooms > 0 && registryLastEventAgeSeconds != null && registryLastEventAgeSeconds > Math.max(30, this.activeStaleSeconds())
        ? "stale"
        : "healthy";
    const alerts: Array<{ key: string; severity: "info" | "warning" | "critical"; message: string }> = [];
    if (staleActiveRooms > 0) {
      alerts.push({
        key: "stale_active_rooms",
        severity: "warning",
        message: `${staleActiveRooms} active room(s) look stale based on the configured room activity window.`,
      });
    }
    if (activeRoomsWithoutOnline > 0) {
      alerts.push({
        key: "active_without_online",
        severity: "warning",
        message: `${activeRoomsWithoutOnline} active room(s) currently have no online participants.`,
          });
    }
    if (waitingOwnerRooms > 0) {
      alerts.push({
        key: "waiting_on_owner",
        severity: nearDeadlineRooms > 0 ? "critical" : "warning",
        message: `${waitingOwnerRooms} room(s) are currently waiting on owner input.`,
      });
    }
    if (ownerReplyOverdueRooms > 0) {
      alerts.push({
        key: "owner_reply_overdue",
        severity: "critical",
        message: `${ownerReplyOverdueRooms} active room(s) have been waiting too long for an owner reply.`,
      });
    }
    if (stalledRunnerRooms > 0 || restartingRunnerRooms > 0 || abandonedRunnerRooms > 0) {
      const totalImpacted = stalledRunnerRooms + restartingRunnerRooms + abandonedRunnerRooms;
      alerts.push({
        key: "runner_attention",
        severity: abandonedRunnerRooms > 0 ? "critical" : "warning",
        message: `${totalImpacted} active room(s) have runner-plane attention needs (stalled/restarting/abandoned).`,
      });
    }
    if (takeoverRecommendedRooms > 0 || takeoverRequiredRooms > 0) {
      const totalTakeover = takeoverRecommendedRooms + takeoverRequiredRooms;
      alerts.push({
        key: "takeover_attention",
        severity: takeoverRequiredRooms > 0 ? "critical" : "warning",
        message: `${totalTakeover} active room(s) need operator takeover guidance or direct participant rescue.`,
      });
    }
    if (unmanagedCompatibilityRooms > 0) {
      alerts.push({
        key: "compatibility_unmanaged",
        severity: takeoverRequiredRooms > 0 ? "critical" : "warning",
        message: `${unmanagedCompatibilityRooms} active compatibility-mode room(s) currently have no managed runner attached.`,
      });
    }
    if (candidateManagedRooms > 0) {
      alerts.push({
        key: "managed_uncertified",
        severity: "warning",
        message: `${candidateManagedRooms} active managed-attached room(s) are running on uncertified recovery paths.`,
      });
    }
    if (firstRelayRiskRooms > 0) {
      alerts.push({
        key: "first_relay_risk",
        severity: "warning",
        message: `${firstRelayRiskRooms} active room(s) are taking too long to reach first relay even though a managed runner is attached.`,
      });
    }
    if (runnerLeaseLowRooms > 0) {
      alerts.push({
        key: "runner_lease_low",
        severity: "warning",
        message: `${runnerLeaseLowRooms} active room(s) have a live runner close to lease expiry.`,
      });
    }
    if (recoveryBacklogRooms > 0) {
      alerts.push({
        key: "recovery_backlog",
        severity: repairClaimOverdueRooms > 0 || recoveryIssuedActions > 0 ? "critical" : "warning",
        message: `${recoveryBacklogRooms} active room(s) still have ${recoveryPendingActions} pending and ${recoveryIssuedActions} issued recovery action(s) awaiting resolution.`,
      });
    }
    if (repairClaimOverdueRooms > 0) {
      alerts.push({
        key: "repair_claim_overdue",
        severity: "critical",
        message: `${repairClaimOverdueRooms} active room(s) already issued a repair package, but no replacement runner claimed it within the expected window.`,
      });
    }
    if (dominantActiveRootCause && dominantActiveRootCause.rooms > 0) {
      alerts.push({
        key: "dominant_root_cause",
        severity:
          dominantActiveRootCause.code === "repair_claim_overdue"
          || dominantActiveRootCause.code === "runner_lost_before_first_relay"
            ? "warning"
            : "info",
        message: `${dominantActiveRootCause.rooms} active room(s) currently share the same leading root cause: ${dominantActiveRootCause.summary || dominantActiveRootCause.code}.`,
      });
    }
    if (registryMode === "stale") {
      alerts.push({
        key: "registry_stale",
        severity: "critical",
        message: "Registry activity looks stale relative to the current active room count.",
      });
    }
    if (budgetState === "warm" || budgetState === "hot") {
      alerts.push({
        key: "budget_proxy",
        severity: budgetState === "hot" ? "critical" : "warning",
        message:
          budgetState === "hot"
            ? "Projected monthly activity is beyond at least one configured budget threshold."
            : "Projected monthly activity is approaching a configured budget threshold.",
      });
    }

    const payload = {
      generated_at: nowIso(),
      thresholds: {
        participant_online_stale_seconds: this.staleSeconds(),
        room_active_stale_seconds: this.activeStaleSeconds(),
        room_near_deadline_seconds: this.nearDeadlineSeconds(),
        repair_issued_stale_seconds: this.repairIssuedStaleSeconds(),
      },
      registry: {
        mode: registryMode,
        last_event_at: lastEventAt || null,
        last_event_type: lastEventType || null,
        last_event_room_id: lastEventRoomId || null,
        last_event_age_seconds: registryLastEventAgeSeconds,
        event_rows: eventRows,
        max_event_rows: this.maxEvents(),
        cache: {
          room_list_hits: this.diagnostics.room_list_cache_hits,
          room_list_misses: this.diagnostics.room_list_cache_misses,
          overview_hits: this.diagnostics.overview_cache_hits,
          overview_misses: this.diagnostics.overview_cache_misses,
          derived_invalidations: this.diagnostics.derived_cache_invalidations,
          ttl_ms: {
            room_list: ROOM_LIST_CACHE_TTL_MS,
            overview: OVERVIEW_CACHE_TTL_MS,
          },
        },
      },
      metrics: {
        total_rooms: totalRooms,
        active_rooms: activeRooms,
        closed_rooms: closedRooms,
        input_required_rooms: inputRequiredRooms,
        online_participants: onlineParticipants,
        joined_participants: joinedParticipants,
        active_runners: activeRunners,
        compatibility_rooms: compatibilityRooms,
        certified_managed_rooms: certifiedManagedRooms,
        candidate_managed_rooms: candidateManagedRooms,
        full_managed_rooms: fullManagedRooms,
        partial_managed_rooms: partialManagedRooms,
        product_owned_rooms: productOwnedRooms,
        automatic_recovery_eligible_rooms: automaticRecoveryEligibleRooms,
        unmanaged_compatibility_rooms: unmanagedCompatibilityRooms,
        total_turns: totalTurns,
        degraded_rooms: degradedRooms,
        budget_hot_rooms: budgetHotRooms,
        budget_warm_rooms: budgetWarmRooms,
        waiting_owner_rooms: waitingOwnerRooms,
        near_deadline_rooms: nearDeadlineRooms,
        events_last_5m: events5m,
        rooms_created_last_1h: Number(typeCounts1h.init || 0),
        rooms_created_last_24h: Number(typeCounts24h.init || 0),
        joins_last_5m: Number(typeCounts5m.join || 0),
        messages_last_5m: Number(typeCounts5m.message || 0),
        owner_waits_last_5m: Number(typeCounts5m.owner_wait || 0),
        closes_last_24h: Object.values(stopReasons24h).reduce((sum, value) => sum + value, 0),
        timeouts_last_24h: Number(stopReasons24h.timeout || 0),
        turn_limits_last_24h: Number(stopReasons24h.turn_limit || 0),
        events_last_24h: events24h,
        stalled_runner_rooms: stalledRunnerRooms,
        restarting_runner_rooms: restartingRunnerRooms,
        abandoned_runner_rooms: abandonedRunnerRooms,
        takeover_recommended_rooms: takeoverRecommendedRooms,
        takeover_required_rooms: takeoverRequiredRooms,
        recovery_pending_actions: recoveryPendingActions,
        recovery_issued_actions: recoveryIssuedActions,
        recovery_backlog_rooms: recoveryBacklogRooms,
        recovery_rooms: recoveryRooms,
        repair_package_issued_rooms: repairPackageIssuedRooms,
        repair_claim_overdue_rooms: repairClaimOverdueRooms,
        owner_reply_overdue_rooms: ownerReplyOverdueRooms,
        first_relay_risk_rooms: firstRelayRiskRooms,
        runner_lease_low_rooms: runnerLeaseLowRooms,
      },
      capacity: {
        stale_active_rooms: staleActiveRooms,
        active_rooms_without_online: activeRoomsWithoutOnline,
        oldest_active_room_age_seconds: oldestActiveRoomAgeSeconds,
        turn_hot_rooms: turnHotRooms,
        stall_hot_rooms: stallHotRooms,
        runner_attention_rooms: stalledRunnerRooms + restartingRunnerRooms + abandonedRunnerRooms,
        takeover_rooms: takeoverRecommendedRooms + takeoverRequiredRooms,
        recovery_backlog_rooms: recoveryBacklogRooms,
        repair_claim_overdue_rooms: repairClaimOverdueRooms,
        owner_reply_overdue_rooms: ownerReplyOverdueRooms,
        first_relay_risk_rooms: firstRelayRiskRooms,
        runner_lease_low_rooms: runnerLeaseLowRooms,
      },
      start_slo: {
        join_latency_ms: {
          p50: percentile(joinLatencies, 0.5),
          p95: percentile(joinLatencies, 0.95),
          p99: percentile(joinLatencies, 0.99),
        },
        first_relay_latency_ms: {
          p50: percentile(firstRelayLatencies, 0.5),
          p95: percentile(firstRelayLatencies, 0.95),
          p99: percentile(firstRelayLatencies, 0.99),
        },
      },
      budget: {
        mode: "activity_proxy",
        configured: budgetConfigured,
        status: budgetState,
        monthly_rooms_budget: monthlyRoomsBudget,
        monthly_events_budget: monthlyEventsBudget,
        max_active_rooms_budget: maxActiveRoomsBudget,
        projected_monthly_rooms: projectedMonthlyRooms,
        projected_monthly_events: projectedMonthlyEvents,
        utilization_ratio: budgetConfigured ? Number(budgetUtilization.toFixed(4)) : null,
        active_rooms_current: activeRooms,
      },
      root_causes: {
        active_top: activeRootCauses,
        recent_24h_top: recentRootCauses24h,
      },
      stop_reasons_last_24h: stopReasons24h,
      alerts,
      event_rates: {
        last_5m: typeCounts5m,
        last_1h: typeCounts1h,
      },
      rooms
    };
    this.overviewCache.set(cacheKey, {
      expiresAtMs: nowMs + OVERVIEW_CACHE_TTL_MS,
      value: structuredClone(payload),
    });
    return payload;
  }

  private roomPriority(room: RegistryRoom): number {
    let score = 0;
    if (room.status === "active") score += 1;
    if (room.health_state === "attention") score += 3;
    if (room.health_state === "degraded") score += 6;
    if (room.budget_state === "warm") score += 2;
    if (room.budget_state === "hot") score += 4;
    if (room.participants_waiting_owner > 0) score += 2;
    if ((room.time_remaining_seconds ?? Number.MAX_SAFE_INTEGER) <= this.nearDeadlineSeconds()) score += 2;
    if (room.participants_online <= 0 && room.status === "active") score += 3;
    if (room.attempt_status === "stalled") score += 4;
    if (room.attempt_status === "restarting") score += 4;
    if (room.attempt_status === "abandoned") score += 6;
    if (room.runner_certification === "candidate") score += 2;
    if (room.managed_coverage === "partial") score += 3;
    if (!room.product_owned && room.execution_mode !== "compatibility") score += 2;
    if (!room.automatic_recovery_eligible && room.execution_mode !== "compatibility") score += 2;
    if ((room.recovery_pending_count + room.recovery_issued_count) > 0) score += 3;
    if (room.execution_attention_reasons.includes("repair_claim_overdue")) score += 5;
    if (room.execution_attention_reasons.includes("first_relay_at_risk")) score += 4;
    if (room.execution_attention_reasons.includes("runner_lease_low")) score += 3;
    if (room.execution_attention_state === "takeover_recommended") score += 5;
    if (room.execution_attention_state === "takeover_required") score += 8;
    if (room.takeover_required) score += 4;
    return score;
  }

  private renderSummaryText(summary: Record<string, unknown>): string {
    const metrics = (summary.metrics || {}) as Record<string, unknown>;
    const budget = (summary.budget || {}) as Record<string, unknown>;
    const registry = (summary.registry || {}) as Record<string, unknown>;
    const registryCache = registry.cache && typeof registry.cache === "object" ? (registry.cache as Record<string, unknown>) : {};
    const startSlo = (summary.start_slo || {}) as Record<string, unknown>;
    const thresholds = (summary.thresholds || {}) as Record<string, unknown>;
    const rootCauses = (summary.root_causes || {}) as Record<string, unknown>;
    const activeTopRootCauses = Array.isArray(rootCauses.active_top)
      ? (rootCauses.active_top as Array<Record<string, unknown>>)
      : [];
    const recentTopRootCauses = Array.isArray(rootCauses.recent_24h_top)
      ? (rootCauses.recent_24h_top as Array<Record<string, unknown>>)
      : [];
    const alerts = Array.isArray(summary.alerts) ? (summary.alerts as Array<Record<string, unknown>>) : [];
    const rooms = Array.isArray(summary.rooms) ? (summary.rooms as Array<Record<string, unknown>>) : [];
    const lines = [
      `posture: ${String(summary.posture || "healthy")}`,
      `generated_at: ${String(summary.generated_at || "")}`,
      `summary: ${String(summary.summary || "")}`,
      `rooms: active=${Number(metrics.active_rooms || 0)} total=${Number(metrics.total_rooms || 0)} input_required=${Number(metrics.input_required_rooms || 0)}`,
      `participants: online=${Number(metrics.online_participants || 0)} joined=${Number(metrics.joined_participants || 0)} active_runners=${Number(metrics.active_runners || 0)} compatibility=${Number(metrics.compatibility_rooms || 0)} managed_certified=${Number(metrics.certified_managed_rooms || 0)} managed_candidate=${Number(metrics.candidate_managed_rooms || 0)} managed_full=${Number(metrics.full_managed_rooms || 0)} managed_partial=${Number(metrics.partial_managed_rooms || 0)} product_owned=${Number(metrics.product_owned_rooms || 0)}`,
      `throughput: rooms_last_1h=${Number(metrics.rooms_created_last_1h || 0)} messages_last_5m=${Number(metrics.messages_last_5m || 0)} events_last_5m=${Number(metrics.events_last_5m || 0)}`,
      `capacity: stale_active=${Number((summary.capacity as Record<string, unknown> | undefined)?.stale_active_rooms || 0)} active_without_online=${Number((summary.capacity as Record<string, unknown> | undefined)?.active_rooms_without_online || 0)} waiting_owner=${Number(metrics.waiting_owner_rooms || 0)} owner_reply_overdue=${Number((summary.capacity as Record<string, unknown> | undefined)?.owner_reply_overdue_rooms || 0)} runner_attention=${Number((summary.capacity as Record<string, unknown> | undefined)?.runner_attention_rooms || 0)} takeover=${Number((summary.capacity as Record<string, unknown> | undefined)?.takeover_rooms || 0)} repair_claim_overdue=${Number((summary.capacity as Record<string, unknown> | undefined)?.repair_claim_overdue_rooms || 0)} first_relay_risk=${Number((summary.capacity as Record<string, unknown> | undefined)?.first_relay_risk_rooms || 0)} lease_low=${Number((summary.capacity as Record<string, unknown> | undefined)?.runner_lease_low_rooms || 0)}`,
      `runner_plane: stalled=${Number(metrics.stalled_runner_rooms || 0)} restarting=${Number(metrics.restarting_runner_rooms || 0)} abandoned=${Number(metrics.abandoned_runner_rooms || 0)} recovery=${Number(metrics.recovery_rooms || 0)} recovery_pending=${Number(metrics.recovery_pending_actions || 0)} recovery_issued=${Number(metrics.recovery_issued_actions || 0)} repair_package_issued_rooms=${Number(metrics.repair_package_issued_rooms || 0)} repair_claim_overdue_rooms=${Number(metrics.repair_claim_overdue_rooms || 0)} owner_reply_overdue_rooms=${Number(metrics.owner_reply_overdue_rooms || 0)} first_relay_risk_rooms=${Number(metrics.first_relay_risk_rooms || 0)} runner_lease_low_rooms=${Number(metrics.runner_lease_low_rooms || 0)} auto_recovery_eligible=${Number(metrics.automatic_recovery_eligible_rooms || 0)} unmanaged_compat=${Number(metrics.unmanaged_compatibility_rooms || 0)}`,
      `root_causes: active_top=${activeTopRootCauses.length ? activeTopRootCauses.map((bucket) => `${String(bucket.code || "none")}:${Number(bucket.rooms || 0)}`).join(", ") : "none"} recent_24h_top=${recentTopRootCauses.length ? recentTopRootCauses.map((bucket) => `${String(bucket.code || "none")}:${Number(bucket.rooms || 0)}`).join(", ") : "none"}`,
      `start_slo_ms: join_p50=${startSlo.join_latency_ms && typeof startSlo.join_latency_ms === "object" ? Number((startSlo.join_latency_ms as Record<string, unknown>).p50 || 0) : 0} join_p95=${startSlo.join_latency_ms && typeof startSlo.join_latency_ms === "object" ? Number((startSlo.join_latency_ms as Record<string, unknown>).p95 || 0) : 0} relay_p50=${startSlo.first_relay_latency_ms && typeof startSlo.first_relay_latency_ms === "object" ? Number((startSlo.first_relay_latency_ms as Record<string, unknown>).p50 || 0) : 0} relay_p95=${startSlo.first_relay_latency_ms && typeof startSlo.first_relay_latency_ms === "object" ? Number((startSlo.first_relay_latency_ms as Record<string, unknown>).p95 || 0) : 0}`,
      `budget_proxy: status=${String(budget.status || "normal")} configured=${Boolean(budget.configured)} projected_monthly_rooms=${Number(budget.projected_monthly_rooms || 0)} projected_monthly_events=${Number(budget.projected_monthly_events || 0)}`,
      `registry: mode=${String(registry.mode || "healthy")} last_event_age_seconds=${registry.last_event_age_seconds == null ? "null" : Number(registry.last_event_age_seconds)}`,
      `registry_cache: room_list_hits=${Number(registryCache.room_list_hits || 0)} room_list_misses=${Number(registryCache.room_list_misses || 0)} overview_hits=${Number(registryCache.overview_hits || 0)} overview_misses=${Number(registryCache.overview_misses || 0)} invalidations=${Number(registryCache.derived_invalidations || 0)}`,
      `thresholds: participant_stale=${Number(thresholds.participant_online_stale_seconds || 0)}s room_stale=${Number(thresholds.room_active_stale_seconds || 0)}s near_deadline=${Number(thresholds.room_near_deadline_seconds || 0)}s repair_issued_stale=${Number(thresholds.repair_issued_stale_seconds || 0)}s`,
    ];
    if (alerts.length) {
      lines.push("alerts:");
      for (const alert of alerts) {
        lines.push(`- ${String(alert.key || "alert")}: ${String(alert.severity || "info")} - ${String(alert.message || "")}`);
      }
    }
    if (rooms.length) {
      lines.push("priority_rooms:");
      for (const room of rooms) {
        lines.push(
          `- ${String(room.room_id || "")} | ${String(room.topic || "(no topic)")} | status=${String(room.status || "")} lifecycle=${String(room.lifecycle_state || "")} health=${String(room.health_state || "")} budget=${String(room.budget_state || "")} execution=${String(room.execution_mode || "")}/${String(room.runner_certification || "none")}/${String(room.managed_coverage || "none")}/${String(room.product_owned ? "owned" : "not_owned")}/${String(room.attempt_status || "")}/${String(room.execution_attention_state || "healthy")} root_cause=${String(room.primary_root_cause_code || "none")}/${String(room.primary_root_cause_confidence || "none")} runners=${Number(room.active_runner_count || 0)} turns=${Number(room.turn_count || 0)} online=${Number(room.participants_online || 0)} recovery=${Number(room.recovery_pending_count || 0)}/${Number(room.recovery_issued_count || 0)} time_left=${room.time_remaining_seconds == null ? "--" : Number(room.time_remaining_seconds)}s`
        );
      }
    }
    return `${lines.join("\n")}\n`;
  }

  private async handleOverview(request: Request): Promise<Response> {
    return json(this.buildOverviewPayload(request));
  }

  private async handleSummary(request: Request): Promise<Response> {
    const overview = this.buildOverviewPayload(request) as Record<string, unknown>;
    const metrics = (overview.metrics || {}) as Record<string, unknown>;
    const capacity = (overview.capacity || {}) as Record<string, unknown>;
    const budget = (overview.budget || {}) as Record<string, unknown>;
    const registry = (overview.registry || {}) as Record<string, unknown>;
    const alerts = Array.isArray(overview.alerts) ? (overview.alerts as Array<Record<string, unknown>>) : [];
    const rooms = Array.isArray(overview.rooms) ? (overview.rooms as RegistryRoom[]) : [];
    const prioritizedRooms = [...rooms]
      .sort((a, b) => this.roomPriority(b) - this.roomPriority(a) || Date.parse(String(b.updated_at || "")) - Date.parse(String(a.updated_at || "")))
      .slice(0, 10)
      .map((room) => ({
        current_runner_phase: room.runner_attempts.find((attempt) => attempt.current)?.phase || null,
        current_runner_phase_detail: room.runner_attempts.find((attempt) => attempt.current)?.phase_detail || null,
        current_runner_phase_age_ms: room.runner_attempts.find((attempt) => attempt.current)?.phase_age_ms ?? null,
        current_runner_lease_remaining_ms: room.runner_attempts.find((attempt) => attempt.current)?.lease_remaining_ms ?? null,
        room_id: room.room_id,
        topic: room.topic,
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
        execution_attention_state: room.execution_attention_state,
        execution_attention_summary: room.execution_attention_summary,
        execution_attention_reasons: room.execution_attention_reasons,
        primary_root_cause_code: room.primary_root_cause_code,
        primary_root_cause_confidence: room.primary_root_cause_confidence,
        primary_root_cause_summary: room.primary_root_cause_summary,
        execution_next_action: room.execution_next_action,
        takeover_required: room.takeover_required,
        recovery_pending_count: room.recovery_pending_count,
        recovery_issued_count: room.recovery_issued_count,
        health_state: room.health_state,
        budget_state: room.budget_state,
        turn_count: room.turn_count,
        time_remaining_seconds: room.time_remaining_seconds,
        participants_online: room.participants_online,
        participants_waiting_owner: room.participants_waiting_owner,
      }));

    const posture =
      alerts.some((alert) => String(alert.severity || "") === "critical")
        ? "critical"
        : alerts.length > 0 || String(registry.mode || "") === "stale" || String(budget.status || "") === "warm"
          ? "attention"
          : "healthy";
    const summary = `${Number(metrics.active_rooms || 0)} active room(s), ${Number(metrics.online_participants || 0)} online participant(s), ${Number(metrics.active_runners || 0)} active runner(s), ${Number(metrics.product_owned_rooms || 0)} product-owned room(s), ${Number(metrics.full_managed_rooms || 0)} fully managed room(s), ${Number(metrics.partial_managed_rooms || 0)} partially managed room(s), ${Number(metrics.candidate_managed_rooms || 0)} uncertified managed room(s), ${Number(metrics.unmanaged_compatibility_rooms || 0)} unmanaged compatibility room(s), ${Number(metrics.recovery_backlog_rooms || 0)} room(s) with active recovery backlog, ${Number(metrics.waiting_owner_rooms || 0)} waiting on owner, registry ${String(registry.mode || "healthy")}, budget ${String(budget.status || "normal")}.`;
    const payload = {
      generated_at: overview.generated_at,
      posture,
      summary,
      thresholds: overview.thresholds,
      registry,
      metrics,
      capacity,
      start_slo: overview.start_slo,
      budget,
      root_causes: overview.root_causes,
      alerts,
      rooms: prioritizedRooms,
    };
    const url = new URL(request.url);
    const format = String(url.searchParams.get("format") || "json").toLowerCase();
    if (format === "text") {
      return new Response(this.renderSummaryText(payload), {
        status: 200,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }
    return json(payload);
  }

  private async handleEvents(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const after = parsePositiveInt(url.searchParams.get("after"), 0, Number.MAX_SAFE_INTEGER);
    const limit = parsePositiveInt(url.searchParams.get("limit"), 200, 1000);
    const rows = this.sql.exec(
      "SELECT id, created_at, type, room_id, payload_json FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
      after,
      limit
    ).toArray();
    const events = rows.map((row) => ({
      id: Number((row as Record<string, unknown>).id || 0),
      created_at: String((row as Record<string, unknown>).created_at || ""),
      type: String((row as Record<string, unknown>).type || ""),
      room_id: String((row as Record<string, unknown>).room_id || ""),
      payload: JSON.parse(String((row as Record<string, unknown>).payload_json || "{}"))
    }));
    const nextCursor = events.length ? events[events.length - 1].id : after;
    return json({ events, next_cursor: nextCursor });
  }
}

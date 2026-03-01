import { badRequest, conflict, json, unauthorized } from "./worker_util";

type Env = Record<string, unknown>;

type RoomStatus = "active" | "closed";

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
};

type ParticipantState = {
  name: string;
  joined: boolean;
  online: boolean;
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

type RoomSnapshot = {
  id: string;
  topic: string;
  goal: string;
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
    online: boolean;
    done: boolean;
    waiting_owner: boolean;
    client_name: string | null;
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

function normalizeMessage(raw: any): Message {
  const legacy = typeof raw?.wants_reply === "boolean" && typeof raw?.expect_reply !== "boolean";
  const intent = normalizeIntent(raw?.intent);
  const text = String(raw?.text || "").trim();
  if (!text) throw new Error("missing text");

  const fills: Record<string, string> = {};
  if (raw?.fills && typeof raw.fills === "object") {
    for (const [k, v] of Object.entries(raw.fills)) {
      const key = String(k || "").trim();
      const val = String(v || "").trim();
      if (key && val) fills[key] = val;
    }
  }

  const facts = Array.isArray(raw?.facts) ? raw.facts.map((x: any) => String(x).trim()).filter(Boolean) : [];
  const questions = Array.isArray(raw?.questions)
    ? raw.questions.map((x: any) => String(x).trim()).filter(Boolean)
    : [];

  let expectReply =
    typeof raw?.expect_reply === "boolean"
      ? raw.expect_reply
      : legacy
        ? Boolean(raw.wants_reply)
        : true;
  if ((intent === "DONE" || intent === "ASK_OWNER") && typeof raw?.expect_reply !== "boolean" && !legacy) {
    expectReply = false;
  }

  const meta = raw?.meta && typeof raw.meta === "object" ? raw.meta : {};

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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class RoomDurableObject implements DurableObject {
  private state: DurableObjectState;
  private env: Env;

  private sql: SqlStorage;

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

      if (request.method === "GET" && (tail === "/" || tail === "")) {
        return await this.handleGetRoom(request, roomId);
      }
      if (request.method === "GET" && tail === "/join_info") return await this.handleJoinInfo(request, roomId);
      if (request.method === "POST" && tail === "/join") return await this.handleJoin(request, roomId);
      if (request.method === "POST" && tail === "/leave") return await this.handleLeave(request, roomId);
      if (request.method === "POST" && tail === "/messages") return await this.handleMessage(request, roomId);
      if (request.method === "GET" && tail === "/events") return await this.handleEvents(request, roomId, false);
      if (request.method === "GET" && tail === "/result") return await this.handleResult(request, roomId, false);
      if (request.method === "POST" && tail === "/close") return await this.handleClose(request, roomId);

      if (request.method === "GET" && tail === "/monitor/events") return await this.handleEvents(request, roomId, true);
      if (request.method === "GET" && tail === "/monitor/result") return await this.handleResult(request, roomId, true);
      if (request.method === "GET" && tail === "/monitor/stream") return await this.handleMonitorStream(request, roomId);

      return json({ error: "not_found" }, { status: 404 });
    } catch (err: any) {
      if (err instanceof Response) return err;
      return json({ error: "internal_error", message: String(err?.message || err) }, { status: 500 });
    }
  }

  async alarm(): Promise<void> {
    this.ensureSchema();
    const row = this.sql.exec("SELECT status, expires_at FROM room LIMIT 1").one();
    if (!row) return;
    const status = String(row.status || "");
    const expiresAt = String(row.expires_at || "");
    if (!expiresAt) return;
    if (status !== "closed") return;
    const now = Date.now();
    const exp = Date.parse(expiresAt);
    if (Number.isFinite(exp) && now >= exp) {
      // Purge ephemeral room data but keep schema to avoid 500s on future probes.
      this.sql.exec("DELETE FROM events");
      this.sql.exec("DELETE FROM fields");
      this.sql.exec("DELETE FROM seen_texts");
      this.sql.exec("DELETE FROM participants");
      this.sql.exec("DELETE FROM tokens");
      this.sql.exec("DELETE FROM room");
    }
  }

  private ensureSchema(): void {
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
        expires_at TEXT
      );

      CREATE TABLE IF NOT EXISTS tokens (
        key TEXT PRIMARY KEY,
        digest TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS participants (
        name TEXT PRIMARY KEY,
        joined INTEGER NOT NULL,
        online INTEGER NOT NULL,
        done INTEGER NOT NULL,
        waiting_owner INTEGER NOT NULL,
        client_name TEXT
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

      CREATE TABLE IF NOT EXISTS seen_texts (
        text_key TEXT PRIMARY KEY
      );
    `);
  }

  private async handleInit(request: Request): Promise<Response> {
    this.ensureSchema();
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

    this.sql.exec(
      `INSERT INTO room (id, topic, goal, required_fields_json, turn_limit, stall_limit, timeout_minutes, ttl_minutes, status, stop_reason, stop_detail, created_at, updated_at, turn_count, stall_count, deadline_at, expires_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL, ?, ?, 0, 0, ?, NULL)`,
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
      deadlineAt
    );

    this.sql.exec("INSERT INTO tokens (key, digest) VALUES ('host', ?) ", hostDigest);
    for (const p of participants) {
      this.sql.exec("INSERT INTO tokens (key, digest) VALUES (?, ?)", `invite:${p}`, inviteDigests[p]);
      this.sql.exec(
        "INSERT INTO participants (name, joined, online, done, waiting_owner, client_name) VALUES (?, 0, 0, 0, 0, NULL)",
        p
      );
    }

    await this.appendEvent("*", "status", { status: "active" });

    // Build convenience URLs (relative — caller prepends their own base)
    const joinLinks: Record<string, string> = {};
    for (const [name, token] of Object.entries(inviteTokens)) {
      joinLinks[name] = `/join/${roomId}?token=${encodeURIComponent(token)}`;
    }
    const monitorLink = `/?room_id=${encodeURIComponent(roomId)}&host_token=${encodeURIComponent(hostToken)}`;

    return json({
      room: await this.snapshot(roomId),
      host_token: hostToken,
      invites: inviteTokens,
      join_links: joinLinks,
      monitor_link: monitorLink,
      config: { turn_limit: turnLimit, stall_limit: stallLimit, timeout_minutes: timeoutMinutes, ttl_minutes: ttlMinutes }
    });
  }

  private async requireHost(request: Request): Promise<void> {
    const token = request.headers.get("X-Host-Token") || new URL(request.url).searchParams.get("host_token") || "";
    if (!token) throw unauthorized("missing host token");
    const digest = await sha256Hex(token);
    const row = this.sql.exec("SELECT digest FROM tokens WHERE key='host' LIMIT 1").one();
    if (!row || String(row.digest) !== digest) throw unauthorized("invalid host token");
  }

  private async requireParticipant(request: Request): Promise<string> {
    const token = request.headers.get("X-Invite-Token") || "";
    if (!token) throw unauthorized("missing invite token");
    const digest = await sha256Hex(token);
    const rows = this.sql.exec("SELECT key, digest FROM tokens WHERE key LIKE 'invite:%'").toArray();
    for (const r of rows) {
      if (String(r.digest) === digest) {
        const key = String(r.key);
        return key.slice("invite:".length);
      }
    }
    throw unauthorized("invalid invite token");
  }

  private async handleGetRoom(request: Request, roomId: string): Promise<Response> {
    // Allow either participant token or host token.
    try {
      await this.requireParticipant(request);
    } catch {
      await this.requireHost(request);
    }
    return json({ room: await this.snapshot(roomId) });
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
    const digest = await sha256Hex(token);
    const rows = this.sql.exec("SELECT key, digest FROM tokens WHERE key LIKE 'invite:%'").toArray();
    let participantName = "";
    for (const r of rows) {
      if (String(r.digest) === digest) {
        participantName = String(r.key).slice("invite:".length);
        break;
      }
    }
    if (!participantName) throw unauthorized("invalid token");
    return json({ participant: participantName, room: await this.snapshot(roomId) });
  }

  private async handleJoin(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    const participant = await this.requireParticipant(request);
    const body = (await request.json().catch(() => ({}))) as any;
    const clientName = typeof body?.client_name === "string" ? body.client_name.slice(0, 120) : null;

    this.sql.exec(
      "UPDATE participants SET joined=1, online=1, client_name=? WHERE name=?",
      clientName,
      participant
    );
    await this.appendEvent("*", "join", { participant, client_name: clientName });

    return json({ participant, room: await this.snapshot(roomId) });
  }

  private async handleLeave(request: Request, roomId: string): Promise<Response> {
    const participant = await this.requireParticipant(request);
    const body = (await request.json().catch(() => ({}))) as any;
    const reason = typeof body?.reason === "string" ? body.reason.slice(0, 500) : "left";

    const row = this.sql.exec("SELECT online FROM participants WHERE name=? LIMIT 1", participant).one();
    const wasOnline = row ? Boolean(row.online) : false;
    this.sql.exec("UPDATE participants SET online=0 WHERE name=?", participant);
    await this.appendEvent("*", "leave", { participant, reason });
    return json({ was_online: wasOnline, room: await this.snapshot(roomId) });
  }

  private async handleClose(request: Request, roomId: string): Promise<Response> {
    await this.requireHost(request);
    const body = (await request.json().catch(() => ({}))) as any;
    const reason = typeof body?.reason === "string" ? body.reason.slice(0, 500) : "manual close";
    await this.closeRoom("manual_close", reason);
    return json({ room: await this.snapshot(roomId) });
  }

  private async handleMessage(request: Request, roomId: string): Promise<Response> {
    this.ensureSchema();
    const sender = await this.requireParticipant(request);

    const row = this.sql.exec("SELECT status FROM room LIMIT 1").one();
    if (!row) throw new Response(JSON.stringify({ error: "not_initialized" }), { status: 404 });
    if (String(row.status) !== "active") throw conflict("room not active");

    const raw = await request.json().catch(() => ({}));
    let msg: Message;
    try {
      msg = normalizeMessage(raw);
    } catch (err: any) {
      return badRequest("invalid message", { detail: String(err?.message || err) });
    }

    const createdAt = nowIso();

    if (msg.intent === "DONE") {
      this.sql.exec("UPDATE participants SET done=1 WHERE name=?", sender);
    }
    if (msg.intent === "ASK_OWNER") {
      this.sql.exec("UPDATE participants SET waiting_owner=1 WHERE name=?", sender);
      await this.appendEvent("*", "owner_wait", { participant: sender, meta: msg.meta || {} });
    }
    if (msg.intent === "OWNER_REPLY") {
      this.sql.exec("UPDATE participants SET waiting_owner=0 WHERE name=?", sender);
      await this.appendEvent("*", "owner_resume", { participant: sender, meta: msg.meta || {} });
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

    const cleanFacts = (msg.facts || []).map((x) => String(x).trim()).filter(Boolean);

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

    // Relay only when expect_reply=true and room is active.
    if (msg.expect_reply) {
      const others = this.sql.exec("SELECT name FROM participants WHERE name != ?", sender).toArray();
      for (const other of others) {
        const to = String(other.name);
        await this.appendEvent(to, "relay", { from: sender, message: { sender, intent: msg.intent, text: msg.text, fills: msg.fills || {} } });
      }
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
    return json({ room: await this.snapshot(roomId), host_decision: await this.hostDecision() });
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
      audience = await this.requireParticipant(request);
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
    const result = await this.result(room);
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

  private readEvents(after: number, limit: number, audience: string): EventRow[] {
    const rows = this.sql
      .exec(
        "SELECT id, type, created_at, audience, payload_json FROM events WHERE id > ? AND (audience='*' OR audience=?) ORDER BY id ASC LIMIT ?",
        after,
        audience,
        limit
      )
      .toArray();
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
    this.sql.exec("INSERT INTO events (type, created_at, audience, payload_json) VALUES (?, ?, ?, ?)", type, nowIso(), String(audience), JSON.stringify(payload || {}));
    this.sql.exec("UPDATE room SET updated_at = ?", nowIso());
  }

  private async snapshot(roomId: string): Promise<RoomSnapshot> {
    this.ensureSchema();
    const room = this.sql.exec("SELECT * FROM room WHERE id=? LIMIT 1", roomId).one();
    if (!room) throw new Response(JSON.stringify({ error: "room_not_found" }), { status: 404 });

    const participants = this.sql.exec("SELECT * FROM participants ORDER BY name ASC").toArray();
    const fields = this.sql.exec("SELECT * FROM fields ORDER BY key ASC").toArray();

    const fieldMap: RoomSnapshot["fields"] = {};
    for (const f of fields) {
      fieldMap[String(f.key)] = { value: String(f.value), updated_at: String(f.updated_at), by: String(f.by_participant) };
    }

    const requiredFields = JSON.parse(String(room.required_fields_json || "[]")) as string[];

    return {
      id: String(room.id),
      topic: String(room.topic),
      goal: String(room.goal),
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
        online: Boolean(p.online),
        done: Boolean(p.done),
        waiting_owner: Boolean(p.waiting_owner),
        client_name: p.client_name ? String(p.client_name) : null
      }))
    };
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

    if (requiredFields.length > 0 && missing.length === 0) {
      await this.closeRoom("goal_done", "required fields complete");
      return;
    }

    const everyoneDone = this.sql.exec("SELECT COUNT(*) AS c FROM participants WHERE done=0").one();
    if (everyoneDone && Number(everyoneDone.c) === 0) {
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

  private async closeRoom(reason: StopReason, detail: string): Promise<void> {
    const updatedAt = nowIso();
    this.sql.exec("UPDATE room SET status='closed', stop_reason=?, stop_detail=?, updated_at=? WHERE status='active'", reason, detail, updatedAt);
    await this.appendEvent("*", "status", { status: "closed", reason, detail });

    // TTL cleanup: ephemeral by default. Use alarm so the room disappears after close.
    const ttlMinutes = Math.max(1, Number(this.sql.exec("SELECT ttl_minutes FROM room LIMIT 1").one()?.ttl_minutes || 60));
    const expiresAt = new Date(Date.now() + ttlMinutes * 60_000).toISOString();
    this.sql.exec("UPDATE room SET expires_at=? WHERE id IS NOT NULL", expiresAt);
    await this.state.storage.setAlarm(Date.now() + ttlMinutes * 60_000);
  }

  private async result(room: RoomSnapshot): Promise<any> {
    const transcriptRows = this.sql
      .exec("SELECT id, created_at, payload_json FROM events WHERE type='msg' ORDER BY id ASC")
      .toArray();
    const transcript = transcriptRows.map((r) => {
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
    });

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
      transcript,
      summary
    };
  }
}

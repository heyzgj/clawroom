/**
 * ClawRoom v3.1 Relay
 * ===================
 *
 * GET-friendly public surface, Durable Object room authority.
 *
 * POST endpoints (script/bridge friendly, token in Authorization header):
 *   POST /threads              - create thread
 *   POST /threads/:id/messages - send message
 *   GET  /threads/:id/messages - poll messages (?after=N&wait=20)
 *   POST /threads/:id/ask-owner - create an owner authorization question
 *   POST /threads/:id/owner-reply - answer an owner authorization question
 *   POST /threads/:id/close    - mark this side closed
 *   POST /threads/:id/heartbeat - bridge runtime heartbeat
 *
 * GET endpoints (agent/web_fetch friendly, token in query string):
 *   GET /threads/new?topic=...&goal=...          - create thread
 *   GET /threads/:id/msgs?token=...&after=N      - poll messages
 *   GET /threads/:id/post?token=...&text=...     - send message
 *   GET /threads/:id/done?token=...&summary=...  - mark this side closed
 *   GET /threads/:id/join?token=...              - thread info
 */

interface Env {
  THREADS: DurableObjectNamespace;
}

interface InitPayload {
  id: string;
  topic: string;
  goal: string;
  host_token: string;
  guest_token: string;
  origin: string;
}

interface AuthContext {
  role: "host" | "guest";
  token: string;
}

interface MessageRow {
  id: number;
  from: string;
  text: string;
  ts: number;
  kind: "message" | "close" | "ask_owner" | "owner_reply";
  question_id?: string;
  expires_at?: number;
}

const MAX_WAIT_SECONDS = 25;
const OWNER_REPLY_TTL_MS = 30 * 60 * 1000;

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

function cors(): Response {
  return new Response(null, {
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Idempotency-Key",
    },
  });
}

function threadId(): string {
  return "t_" + crypto.randomUUID().slice(0, 12);
}

function randomToken(role: "host" | "guest"): string {
  return `${role}_${crypto.randomUUID().replace(/-/g, "")}`;
}

function randomOwnerReplyToken(): string {
  return `owner_${crypto.randomUUID().replace(/-/g, "")}`;
}

async function readJson<T = Record<string, unknown>>(request: Request): Promise<T> {
  try {
    return await request.json<T>();
  } catch {
    return {} as T;
  }
}

function threadStub(env: Env, id: string): DurableObjectStub {
  return env.THREADS.get(env.THREADS.idFromName(id));
}

async function createThread(request: Request, env: Env, topic: string, goal: string): Promise<Response> {
  const url = new URL(request.url);
  const id = threadId();
  const init: InitPayload = {
    id,
    topic: topic || "untitled",
    goal: goal || "",
    host_token: randomToken("host"),
    guest_token: randomToken("guest"),
    origin: url.origin,
  };
  return await threadStub(env, id).fetch("https://thread/init", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(init),
  });
}

function routeHelp(): Response {
  return json({
    error: "not_found",
    endpoints: [
      "POST /threads",
      "GET /threads/new?topic=...&goal=...",
      "POST /threads/:id/messages",
      "GET /threads/:id/msgs?token=...&after=N&wait=20",
      "GET /threads/:id/messages?token=...&after=N&wait=20",
      "GET /threads/:id/post?token=...&text=...",
      "POST /threads/:id/ask-owner",
      "POST /threads/:id/owner-reply",
      "POST /threads/:id/close",
      "GET /threads/:id/done?token=...&summary=...",
      "GET /threads/:id/join?token=...",
      "POST /threads/:id/heartbeat",
    ],
  }, 404);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      if (request.method === "OPTIONS") return cors();

      const url = new URL(request.url);
      const path = url.pathname;

      if (request.method === "GET" && path === "/threads/new") {
        return await createThread(
          request,
          env,
          url.searchParams.get("topic") || "untitled",
          url.searchParams.get("goal") || "",
        );
      }

      if (request.method === "POST" && path === "/threads") {
        const body = await readJson<{ topic?: string; goal?: string }>(request);
        return await createThread(request, env, body.topic || "untitled", body.goal || "");
      }

      const match = path.match(/^\/threads\/([^/]+)\/([A-Za-z0-9_-]+)$/);
      if (!match) return routeHelp();

      const [, id, action] = match;
      return await threadStub(env, id).fetch(new Request(`https://thread/threads/${id}/${action}${url.search}`, request));
    } catch (e: any) {
      return json({ error: e?.message || String(e), stack: e?.stack }, 500);
    }
  },
};

export class ThreadDurableObject {
  private state: DurableObjectState;
  private waiters = new Set<() => void>();

  constructor(state: DurableObjectState) {
    this.state = state;
  }

  async fetch(request: Request): Promise<Response> {
    try {
      if (request.method === "OPTIONS") return cors();

      const url = new URL(request.url);
      if (request.method === "POST" && url.pathname === "/init") {
        const body = await readJson<InitPayload>(request);
        return this.init(body);
      }

      this.ensureSchema();
      const match = url.pathname.match(/^\/threads\/([^/]+)\/([A-Za-z0-9_-]+)$/);
      if (!match) return routeHelp();

      const [, id, action] = match;
      if (!this.threadExists(id)) return json({ error: "thread not found" }, 404);

      if (request.method === "GET" && action === "join") {
        const auth = this.authenticate(request, url);
        if (!auth) return this.unauthorized();
        return json(this.snapshot(id, auth));
      }

      if (action === "owner-reply" && request.method !== "POST") {
        return json({
          error: "method_not_allowed",
          hint: "owner-reply is POST-only so link previews cannot consume one-time tokens.",
        }, 405);
      }
      if (request.method === "POST" && action === "owner-reply") {
        return await this.handleOwnerReply(id, request, url);
      }

      const auth = this.authenticate(request, url);
      if (!auth) return this.unauthorized();

      if (request.method === "GET" && (action === "msgs" || action === "messages")) {
        return await this.handleMessages(url);
      }
      if (request.method === "GET" && action === "post") {
        const text = url.searchParams.get("text") || "";
        const key = this.idempotencyKey(request, url);
        return this.handleSend(id, auth, text, key, "message");
      }
      if (request.method === "POST" && action === "messages") {
        const body = await readJson<{ text?: string; idempotency_key?: string }>(request);
        const key = this.idempotencyKey(request, url, body.idempotency_key);
        return this.handleSend(id, auth, body.text || "", key, "message");
      }
      if (request.method === "POST" && action === "ask-owner") {
        const body = await readJson<{ text?: string; idempotency_key?: string; ttl_seconds?: number }>(request);
        const key = this.idempotencyKey(request, url, body.idempotency_key);
        return this.handleAskOwner(id, auth, body.text || "", key, body.ttl_seconds);
      }
      if (request.method === "GET" && action === "ask-owner") {
        const text = url.searchParams.get("text") || "";
        const key = this.idempotencyKey(request, url);
        const ttlSeconds = Number(url.searchParams.get("ttl_seconds") || 0);
        return this.handleAskOwner(id, auth, text, key, ttlSeconds);
      }
      if (request.method === "GET" && action === "done") {
        const summary = url.searchParams.get("summary") || "";
        const key = this.idempotencyKey(request, url);
        return this.handleSend(id, auth, summary, key, "close");
      }
      if (request.method === "POST" && action === "close") {
        const body = await readJson<{ summary?: string; idempotency_key?: string }>(request);
        const key = this.idempotencyKey(request, url, body.idempotency_key);
        return this.handleSend(id, auth, body.summary || "", key, "close");
      }
      if (request.method === "POST" && action === "heartbeat") {
        const body = await readJson<Record<string, unknown>>(request);
        return this.handleHeartbeat(id, auth, body);
      }

      return json({ error: "not found" }, 404);
    } catch (e: any) {
      return json({ error: e?.message || String(e), stack: e?.stack }, 500);
    }
  }

  private ensureSchema(): void {
    this.state.storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS thread (
        id TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        goal TEXT NOT NULL,
        created INTEGER NOT NULL,
        updated INTEGER NOT NULL,
        closed INTEGER NOT NULL DEFAULT 0,
        summary TEXT NOT NULL DEFAULT '',
        host_token TEXT NOT NULL,
        guest_token TEXT NOT NULL,
        host_closed INTEGER NOT NULL DEFAULT 0,
        guest_closed INTEGER NOT NULL DEFAULT 0,
        host_summary TEXT NOT NULL DEFAULT '',
        guest_summary TEXT NOT NULL DEFAULT ''
      )
    `);
    this.state.storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        role TEXT NOT NULL,
        text TEXT NOT NULL,
        ts INTEGER NOT NULL,
        kind TEXT NOT NULL DEFAULT 'message',
        metadata_json TEXT NOT NULL DEFAULT '{}'
      )
    `);
    try {
      this.state.storage.sql.exec("ALTER TABLE messages ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'");
    } catch {}
    this.state.storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS owner_questions (
        question_id TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        ask_message_id INTEGER NOT NULL,
        owner_reply_token TEXT NOT NULL,
        text TEXT NOT NULL,
        created INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        consumed INTEGER NOT NULL DEFAULT 0,
        consumed_at INTEGER NOT NULL DEFAULT 0,
        reply_text TEXT NOT NULL DEFAULT '',
        reply_message_id INTEGER NOT NULL DEFAULT -1
      )
    `);
    this.state.storage.sql.exec(`
      CREATE INDEX IF NOT EXISTS owner_questions_token_idx
      ON owner_questions(owner_reply_token)
    `);
    this.state.storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS idempotency (
        role TEXT NOT NULL,
        key TEXT NOT NULL,
        status INTEGER NOT NULL,
        response_json TEXT NOT NULL,
        created INTEGER NOT NULL,
        PRIMARY KEY (role, key)
      )
    `);
    this.state.storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS runtime_heartbeats (
        role TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        cursor INTEGER NOT NULL DEFAULT -1,
        pid TEXT NOT NULL DEFAULT '',
        bridge_version TEXT NOT NULL DEFAULT '',
        updated INTEGER NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}'
      )
    `);
  }

  private init(body: InitPayload): Response {
    this.ensureSchema();
    if (!body.id || !body.host_token || !body.guest_token) {
      return json({ error: "invalid_init" }, 400);
    }
    if (this.threadExists(body.id)) {
      const row = this.threadRow();
      return json(this.createResponse(body.origin, row));
    }
    const now = Date.now();
    this.state.storage.sql.exec(
      `INSERT INTO thread (id, topic, goal, created, updated, host_token, guest_token)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
      body.id,
      body.topic || "untitled",
      body.goal || "",
      now,
      now,
      body.host_token,
      body.guest_token,
    );
    return json(this.createResponse(body.origin, this.threadRow()));
  }

  private createResponse(origin: string, row: Record<string, unknown> | null): Record<string, unknown> {
    const id = String(row?.id || "");
    const guestToken = String(row?.guest_token || "");
    return {
      thread_id: id,
      host_token: String(row?.host_token || ""),
      guest_token: guestToken,
      invite_url: `${origin}/threads/${id}/join?token=${encodeURIComponent(guestToken)}`,
    };
  }

  private threadExists(id: string): boolean {
    return this.state.storage.sql.exec("SELECT 1 FROM thread WHERE id=? LIMIT 1", id).toArray().length > 0;
  }

  private threadRow(): Record<string, unknown> | null {
    return (this.state.storage.sql.exec("SELECT * FROM thread LIMIT 1").toArray()[0] as Record<string, unknown> | undefined) || null;
  }

  private authenticate(request: Request, url: URL): AuthContext | null {
    const row = this.threadRow();
    if (!row) return null;
    const header = request.headers.get("Authorization") || "";
    const bearer = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
    const token = bearer || url.searchParams.get("token") || "";
    if (!token) return null;
    if (token === row.host_token) return { role: "host", token };
    if (token === row.guest_token) return { role: "guest", token };
    return null;
  }

  private unauthorized(): Response {
    return json({ error: "unauthorized", hint: "Pass token= in the query string or Authorization: Bearer <token>." }, 401);
  }

  private idempotencyKey(request: Request, url: URL, bodyKey?: unknown): string {
    return String(
      bodyKey ||
      request.headers.get("X-Idempotency-Key") ||
      url.searchParams.get("idempotency_key") ||
      "",
    ).trim().slice(0, 200);
  }

  private async handleMessages(url: URL): Promise<Response> {
    const after = parseInt(url.searchParams.get("after") || "-1", 10);
    const waitSeconds = Math.max(0, Math.min(MAX_WAIT_SECONDS, parseInt(url.searchParams.get("wait") || "0", 10) || 0));
    let rows = this.messagesAfter(after);
    const row = this.threadRow();
    const closed = Boolean(Number(row?.closed || 0));

    if (!rows.length && waitSeconds > 0 && !closed) {
      await this.waitForEvent(waitSeconds * 1000);
      rows = this.messagesAfter(after);
    }
    return json(rows);
  }

  private messagesAfter(after: number): MessageRow[] {
    return this.state.storage.sql
      .exec("SELECT id, role AS sender, text, ts, kind, metadata_json FROM messages WHERE id>? ORDER BY id ASC", Number.isFinite(after) ? after : -1)
      .toArray()
      .map((row: any) => ({
        ...this.publicMessageRow(row),
      }));
  }

  private publicMessageRow(row: any): MessageRow {
    const metadata = this.parseMetadata(row?.metadata_json);
    const out: MessageRow = {
      id: Number(row.id),
      from: String(row.sender || row.role || ""),
      text: String(row.text || ""),
      ts: Number(row.ts || 0),
      kind: this.normalizeKind(String(row.kind || "message")),
    };
    if (metadata.question_id) out.question_id = String(metadata.question_id);
    if (Number.isFinite(Number(metadata.expires_at))) out.expires_at = Number(metadata.expires_at);
    return out;
  }

  private parseMetadata(value: unknown): Record<string, unknown> {
    try {
      const parsed = JSON.parse(String(value || "{}"));
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
    } catch {
      return {};
    }
  }

  private normalizeKind(kind: string): MessageRow["kind"] {
    if (kind === "close" || kind === "ask_owner" || kind === "owner_reply") return kind;
    return "message";
  }

  private nextMessageId(): number {
    const nextIdRow = this.state.storage.sql
      .exec("SELECT COALESCE(MAX(id) + 1, 0) AS next_id FROM messages")
      .toArray()[0] as Record<string, unknown> | undefined;
    return Number(nextIdRow?.next_id || 0);
  }

  private handleSend(
    id: string,
    auth: AuthContext,
    rawText: string,
    idempotencyKey: string,
    kind: "message" | "close",
  ): Response {
    const text = String(rawText || "").trim();
    if (!text) return json({ error: kind === "close" ? "summary_required" : "text_required" }, 400);

    const existing = this.idempotencyHit(auth.role, idempotencyKey);
    if (existing) return json(existing.body, existing.status);

    const row = this.threadRow();
    if (Boolean(Number(row?.closed || 0))) {
      return json({ error: "thread is closed" }, 400);
    }

    const last = this.state.storage.sql
      .exec("SELECT id, role FROM messages WHERE kind IN ('message', 'close') ORDER BY id DESC LIMIT 1")
      .toArray()[0] as Record<string, unknown> | undefined;

    if (last && String(last.role) === auth.role) {
      return json({
        error: "not_your_turn",
        last_from: auth.role,
        last_id: Number(last.id),
        hint: "Wait for the other side to reply before posting again.",
      }, 409);
    }

    const nextId = this.nextMessageId();
    const now = Date.now();

    this.state.storage.sql.exec(
      "INSERT INTO messages (id, role, text, ts, kind, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
      nextId,
      auth.role,
      text,
      now,
      kind,
      "{}",
    );

    if (kind === "close") {
      const column = auth.role === "host" ? "host" : "guest";
      this.state.storage.sql.exec(
        `UPDATE thread SET ${column}_closed=1, ${column}_summary=?, updated=? WHERE id=?`,
        text,
        now,
        id,
      );
      this.closeIfBothSidesClosed(id, now);
    } else {
      this.state.storage.sql.exec("UPDATE thread SET updated=? WHERE id=?", now, id);
    }

    const message: MessageRow = { id: nextId, from: auth.role, text, ts: now, kind };
    const response = {
      ...message,
      closed: Boolean(Number(this.threadRow()?.closed || 0)),
      close_state: this.closeState(),
    };
    this.recordIdempotency(auth.role, idempotencyKey, 201, response);
    this.wake();
    return json(response, 201);
  }

  private handleAskOwner(
    id: string,
    auth: AuthContext,
    rawText: string,
    idempotencyKey: string,
    ttlSeconds?: number,
  ): Response {
    const text = String(rawText || "").trim().slice(0, 4000);
    if (!text) return json({ error: "text_required" }, 400);

    const existing = this.idempotencyHit(auth.role, idempotencyKey);
    if (existing) return json(existing.body, existing.status);

    const row = this.threadRow();
    if (Boolean(Number(row?.closed || 0))) {
      return json({ error: "thread is closed" }, 400);
    }

    const nextId = this.nextMessageId();
    const now = Date.now();
    const ttlMs = Math.max(60_000, Math.min(OWNER_REPLY_TTL_MS, Number(ttlSeconds || 0) * 1000 || OWNER_REPLY_TTL_MS));
    const questionId = `q_${crypto.randomUUID().slice(0, 12)}`;
    const ownerReplyToken = randomOwnerReplyToken();
    const expiresAt = now + ttlMs;

    this.state.storage.sql.exec(
      "INSERT INTO messages (id, role, text, ts, kind, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
      nextId,
      auth.role,
      text,
      now,
      "ask_owner",
      JSON.stringify({ question_id: questionId, expires_at: expiresAt }),
    );
    this.state.storage.sql.exec(
      `INSERT INTO owner_questions
       (question_id, role, ask_message_id, owner_reply_token, text, created, expires_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
      questionId,
      auth.role,
      nextId,
      ownerReplyToken,
      text,
      now,
      expiresAt,
    );
    this.state.storage.sql.exec("UPDATE thread SET updated=? WHERE id=?", now, id);

    const response = {
      id: nextId,
      from: auth.role,
      text,
      ts: now,
      kind: "ask_owner",
      question_id: questionId,
      owner_reply_token: ownerReplyToken,
      expires_at: expiresAt,
    };
    this.recordIdempotency(auth.role, idempotencyKey, 201, response);
    this.wake();
    return json(response, 201);
  }

  private async handleOwnerReply(id: string, request: Request, url: URL): Promise<Response> {
    const body = await readJson<{ token?: string; question_id?: string; role?: string; text?: string }>(request);
    const token = String(body.token || url.searchParams.get("token") || "").trim();
    const questionId = String(body.question_id || url.searchParams.get("question_id") || "").trim();
    const role = String(body.role || url.searchParams.get("role") || "").trim();
    const text = String(body.text || url.searchParams.get("text") || "").trim().slice(0, 4000);

    if (!token || !questionId || !role) return json({ error: "invalid_owner_reply" }, 400);
    if (!["host", "guest"].includes(role)) return json({ error: "invalid_owner_reply" }, 400);
    if (!text) return json({ error: "text_required" }, 400);

    const question = this.state.storage.sql
      .exec("SELECT * FROM owner_questions WHERE question_id=? LIMIT 1", questionId)
      .toArray()[0] as Record<string, unknown> | undefined;
    if (!question) return json({ error: "question_not_found" }, 404);
    const storedRole = String(question.role || "");
    if (storedRole !== role) {
      console.warn(JSON.stringify({
        event: "owner_reply_role_mismatch",
        thread_id: id,
        question_id: questionId,
        requested_role: role,
        stored_role: storedRole,
      }));
      return json({ error: "unauthorized_owner_reply" }, 401);
    }
    if (String(question.owner_reply_token || "") !== token) return json({ error: "unauthorized_owner_reply" }, 401);
    if (Boolean(Number(question.consumed || 0))) return json({ error: "owner_reply_already_consumed" }, 409);

    const now = Date.now();
    const expiresAt = Number(question.expires_at || 0);
    if (expiresAt && now > expiresAt) return json({ error: "owner_reply_expired" }, 410);

    const nextId = this.nextMessageId();
    this.state.storage.sql.exec(
      "INSERT INTO messages (id, role, text, ts, kind, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
      nextId,
      role,
      text,
      now,
      "owner_reply",
      JSON.stringify({ question_id: questionId, ask_message_id: Number(question.ask_message_id || -1) }),
    );
    this.state.storage.sql.exec(
      `UPDATE owner_questions
       SET consumed=1, consumed_at=?, reply_text=?, reply_message_id=?
       WHERE question_id=? AND role=?`,
      now,
      text,
      nextId,
      questionId,
      role,
    );
    this.state.storage.sql.exec("UPDATE thread SET updated=? WHERE id=?", now, id);

    const response = {
      id: nextId,
      from: role,
      text,
      ts: now,
      kind: "owner_reply",
      question_id: questionId,
      ok: true,
    };
    this.wake();
    return json(response, 201);
  }

  private closeIfBothSidesClosed(id: string, now: number): void {
    const row = this.threadRow();
    const hostClosed = Boolean(Number(row?.host_closed || 0));
    const guestClosed = Boolean(Number(row?.guest_closed || 0));
    if (!hostClosed || !guestClosed) return;

    const hostSummary = String(row?.host_summary || "");
    const guestSummary = String(row?.guest_summary || "");
    const summary = guestSummary || hostSummary;
    this.state.storage.sql.exec(
      "UPDATE thread SET closed=1, summary=?, updated=? WHERE id=?",
      summary,
      now,
      id,
    );
  }

  private closeState(): Record<string, unknown> {
    const row = this.threadRow();
    return {
      host_closed: Boolean(Number(row?.host_closed || 0)),
      guest_closed: Boolean(Number(row?.guest_closed || 0)),
      closed: Boolean(Number(row?.closed || 0)),
      summary: String(row?.summary || ""),
    };
  }

  private idempotencyHit(role: string, key: string): { status: number; body: unknown } | null {
    if (!key) return null;
    const row = this.state.storage.sql
      .exec("SELECT status, response_json FROM idempotency WHERE role=? AND key=? LIMIT 1", role, key)
      .toArray()[0] as Record<string, unknown> | undefined;
    if (!row) return null;
    try {
      return { status: Number(row.status || 200), body: JSON.parse(String(row.response_json || "{}")) };
    } catch {
      return null;
    }
  }

  private recordIdempotency(role: string, key: string, status: number, body: unknown): void {
    if (!key) return;
    this.state.storage.sql.exec(
      "INSERT OR REPLACE INTO idempotency (role, key, status, response_json, created) VALUES (?, ?, ?, ?, ?)",
      role,
      key,
      status,
      JSON.stringify(body),
      Date.now(),
    );
  }

  private handleHeartbeat(id: string, auth: AuthContext, body: Record<string, unknown>): Response {
    const now = Date.now();
    const status = String(body.status || "running").slice(0, 80);
    const cursor = Number.isFinite(Number(body.cursor)) ? Number(body.cursor) : -1;
    const pid = String(body.pid || "").slice(0, 80);
    const bridgeVersion = String(body.bridge_version || "").slice(0, 80);
    this.state.storage.sql.exec(
      `INSERT INTO runtime_heartbeats (role, status, cursor, pid, bridge_version, updated, payload_json)
       VALUES (?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(role) DO UPDATE SET
         status=excluded.status,
         cursor=excluded.cursor,
         pid=excluded.pid,
         bridge_version=excluded.bridge_version,
         updated=excluded.updated,
         payload_json=excluded.payload_json`,
      auth.role,
      status,
      cursor,
      pid,
      bridgeVersion,
      now,
      JSON.stringify(body),
    );
    this.state.storage.sql.exec("UPDATE thread SET updated=? WHERE id=?", now, id);
    return json({ ok: true, role: auth.role, updated: now });
  }

  private snapshot(id: string, auth: AuthContext): Record<string, unknown> {
    const row = this.threadRow();
    const heartbeats = this.state.storage.sql
      .exec("SELECT role, status, cursor, pid, bridge_version, updated FROM runtime_heartbeats ORDER BY role")
      .toArray();
    const last = this.state.storage.sql
      .exec("SELECT id, role AS sender, text, ts, kind, metadata_json FROM messages ORDER BY id DESC LIMIT 1")
      .toArray()[0] as Record<string, unknown> | undefined;

    return {
      thread_id: id,
      role: auth.role,
      topic: String(row?.topic || ""),
      goal: String(row?.goal || ""),
      token: auth.token,
      closed: Boolean(Number(row?.closed || 0)),
      summary: String(row?.summary || ""),
      close_state: this.closeState(),
      last_message: last ? this.publicMessageRow(last) : null,
      runtime_heartbeats: heartbeats,
    };
  }

  private waitForEvent(ms: number): Promise<void> {
    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        this.waiters.delete(done);
        resolve();
      }, ms);
      const done = () => {
        clearTimeout(timeout);
        this.waiters.delete(done);
        resolve();
      };
      this.waiters.add(done);
    });
  }

  private wake(): void {
    for (const waiter of this.waiters) waiter();
    this.waiters.clear();
  }
}

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
 *   GET  /threads/:id/owner-reply?code=... - owner decision page (non-mutating)
 *   POST /threads/:id/owner-reply - answer an owner authorization question
 *   POST /threads/:id/close    - mark this side closed
 *   POST /threads/:id/heartbeat - bridge runtime heartbeat
 *
 * GET endpoints (agent/web_fetch friendly, token in query string):
 *   GET /threads/new?topic=...&goal=...          - create thread
 *   GET /i/:id/:code                             - resolve public guest invite
 *   GET /threads/:id/msgs?token=...&after=N      - poll messages
 *   GET /threads/:id/post?token=...&text=...     - send message
 *   GET /threads/:id/done?token=...&summary=...  - mark this side closed
 *   GET /threads/:id/join?token=...              - thread info
 */

interface Env {
  THREADS: DurableObjectNamespace;
  CLAWROOM_CREATE_KEYS?: string;
  CLAWROOM_REQUIRE_CREATE_KEY?: string;
  CLAWROOM_CREATE_DISABLED?: string;
  CLAWROOM_RELAY_DISABLED?: string;
  CLAWROOM_MAX_THREAD_MS?: string;
  CLAWROOM_MAX_MESSAGES?: string;
  CLAWROOM_MAX_TEXT_CHARS?: string;
  CLAWROOM_MIN_HEARTBEAT_MS?: string;
  CREATE_RATE_LIMITER?: {
    limit(input: { key: string }): Promise<{ success: boolean }>;
  };
}

interface InitPayload {
  id: string;
  topic: string;
  goal: string;
  host_token: string;
  guest_token: string;
  guest_invite_code: string;
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
  source?: string;
}

const MAX_WAIT_SECONDS = 25;
const OWNER_REPLY_TTL_MS = 30 * 60 * 1000;
const DEFAULT_PUBLIC_ORIGIN = "https://clawroom-v3-relay.heyzgj.workers.dev";
const DEFAULT_MAX_THREAD_MS = 2 * 60 * 60 * 1000;
const DEFAULT_MAX_MESSAGES = 120;
const DEFAULT_MAX_TEXT_CHARS = 8000;
const DEFAULT_MIN_HEARTBEAT_MS = 10_000;

function boolEnv(value: unknown): boolean {
  return ["1", "true", "yes", "on"].includes(String(value || "").trim().toLowerCase());
}

function numberEnv(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

function createKeys(env: Env): string[] {
  return String(env.CLAWROOM_CREATE_KEYS || "")
    .split(",")
    .map((key) => key.trim())
    .filter(Boolean);
}

function createKeyFrom(request: Request, url: URL, bodyKey?: unknown): string {
  const header = request.headers.get("X-Clawroom-Create-Key") || "";
  const bearer = (request.headers.get("Authorization") || "").startsWith("Bearer ")
    ? (request.headers.get("Authorization") || "").slice(7)
    : "";
  return String(bodyKey || header || bearer || url.searchParams.get("create_key") || "").trim();
}

async function guardCreate(request: Request, env: Env, bodyKey?: unknown): Promise<Response | null> {
  if (boolEnv(env.CLAWROOM_RELAY_DISABLED) || boolEnv(env.CLAWROOM_CREATE_DISABLED)) {
    return json({ error: "create_disabled", hint: "This relay is not accepting new rooms right now." }, 503);
  }

  const url = new URL(request.url);
  const keys = createKeys(env);
  const requireKey = boolEnv(env.CLAWROOM_REQUIRE_CREATE_KEY) || keys.length > 0;
  const supplied = createKeyFrom(request, url, bodyKey);

  if (requireKey) {
    if (!keys.length) {
      return json({ error: "create_keys_not_configured" }, 503);
    }
    if (!supplied) {
      return json({ error: "create_key_required", hint: "This hosted relay is private beta. Provide X-Clawroom-Create-Key." }, 401);
    }
    if (!keys.includes(supplied)) {
      return json({ error: "invalid_create_key" }, 401);
    }
  }

  if (env.CREATE_RATE_LIMITER) {
    const key = supplied ? `create-key:${supplied}` : `ip:${request.headers.get("CF-Connecting-IP") || "unknown"}`;
    const { success } = await env.CREATE_RATE_LIMITER.limit({ key });
    if (!success) return json({ error: "create_rate_limited" }, 429);
  }

  return null;
}

function json(data: unknown, status = 200, includeBody = true): Response {
  return new Response(includeBody ? JSON.stringify(data) : null, {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

function html(body: string, status = 200, includeBody = true): Response {
  return new Response(includeBody ? body : null, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      "X-Robots-Tag": "noindex",
    },
  });
}

function cors(): Response {
  return new Response(null, {
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Idempotency-Key, X-Clawroom-Create-Key",
    },
  });
}

function threadId(): string {
  return "t_" + crypto.randomUUID().slice(0, 12);
}

function randomToken(role: "host" | "guest"): string {
  return `${role}_${crypto.randomUUID().replace(/-/g, "")}`;
}

function randomInviteCode(): string {
  return `CR-${crypto.randomUUID().replace(/-/g, "").slice(0, 8).toUpperCase()}`;
}

function randomOwnerReplyToken(): string {
  return `owner_${crypto.randomUUID().replace(/-/g, "")}`;
}

function randomOwnerReplyCode(): string {
  return `OR-${crypto.randomUUID().replace(/-/g, "").slice(0, 20)}`;
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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
    guest_invite_code: randomInviteCode(),
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
      "GET /i/:id/:code",
      "POST /threads/:id/messages",
      "GET /threads/:id/msgs?token=...&after=N&wait=20",
      "GET /threads/:id/messages?token=...&after=N&wait=20",
      "GET /threads/:id/post?token=...&text=...",
      "POST /threads/:id/ask-owner",
      "GET /threads/:id/owner-reply?code=...",
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
        const createGuard = await guardCreate(request, env);
        if (createGuard) return createGuard;
        return await createThread(
          request,
          env,
          url.searchParams.get("topic") || "untitled",
          url.searchParams.get("goal") || "",
        );
      }

      if (request.method === "POST" && path === "/threads") {
        const body = await readJson<{ topic?: string; goal?: string; create_key?: string }>(request);
        const createGuard = await guardCreate(request, env, body.create_key);
        if (createGuard) return createGuard;
        return await createThread(request, env, body.topic || "untitled", body.goal || "");
      }

      const inviteMatch = path.match(/^\/i\/([^/]+)\/([^/]+)$/);
      if ((request.method === "GET" || request.method === "HEAD") && inviteMatch) {
        const [, id, code] = inviteMatch;
        const params = new URLSearchParams({
          code,
          origin: url.origin,
        });
        return await threadStub(env, id).fetch(
          new Request(`https://thread/threads/${id}/invite?${params.toString()}`, request),
        );
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
  private env: Env;
  private waiters = new Set<() => void>();

  constructor(state: DurableObjectState, env: Env) {
    this.state = state;
    this.env = env;
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
      if (boolEnv(this.env.CLAWROOM_RELAY_DISABLED)) {
        return json({ error: "relay_disabled", hint: "This relay is temporarily disabled." }, 503);
      }
      const expiry = this.expiredResponse();
      if (expiry) return expiry;

      if ((request.method === "GET" || request.method === "HEAD") && action === "invite") {
        return this.handlePublicInvite(url, request);
      }

      if (request.method === "GET" && action === "join") {
        const auth = this.authenticate(request, url);
        if (!auth) return this.unauthorized();
        return json(this.snapshot(id, auth));
      }

      if ((request.method === "GET" || request.method === "HEAD") && action === "owner-reply") {
        return this.handleOwnerReplyPage(id, request, url);
      }

      if (action === "owner-reply" && request.method !== "POST") {
        return json({
          error: "method_not_allowed",
          hint: "Use GET only to view the owner decision page. Mutating owner replies are POST-only.",
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
        return this.handleAskOwner(id, auth, body.text || "", key, body.ttl_seconds, url);
      }
      if (request.method === "GET" && action === "ask-owner") {
        const text = url.searchParams.get("text") || "";
        const key = this.idempotencyKey(request, url);
        const ttlSeconds = Number(url.searchParams.get("ttl_seconds") || 0);
        return this.handleAskOwner(id, auth, text, key, ttlSeconds, url);
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
        origin TEXT NOT NULL DEFAULT '',
        created INTEGER NOT NULL,
        updated INTEGER NOT NULL,
        closed INTEGER NOT NULL DEFAULT 0,
        summary TEXT NOT NULL DEFAULT '',
        host_token TEXT NOT NULL,
        guest_token TEXT NOT NULL,
        guest_invite_code TEXT NOT NULL DEFAULT '',
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
    try {
      this.state.storage.sql.exec("ALTER TABLE thread ADD COLUMN guest_invite_code TEXT NOT NULL DEFAULT ''");
    } catch {}
    try {
      this.state.storage.sql.exec("ALTER TABLE thread ADD COLUMN origin TEXT NOT NULL DEFAULT ''");
    } catch {}
    this.state.storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS owner_questions (
        question_id TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        ask_message_id INTEGER NOT NULL,
        owner_reply_token TEXT NOT NULL,
        owner_reply_code TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL,
        created INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        consumed INTEGER NOT NULL DEFAULT 0,
        consumed_at INTEGER NOT NULL DEFAULT 0,
        reply_text TEXT NOT NULL DEFAULT '',
        reply_message_id INTEGER NOT NULL DEFAULT -1
      )
    `);
    try {
      this.state.storage.sql.exec("ALTER TABLE owner_questions ADD COLUMN owner_reply_code TEXT NOT NULL DEFAULT ''");
    } catch {}
    this.state.storage.sql.exec(`
      CREATE INDEX IF NOT EXISTS owner_questions_token_idx
      ON owner_questions(owner_reply_token)
    `);
    this.state.storage.sql.exec(`
      CREATE INDEX IF NOT EXISTS owner_questions_code_idx
      ON owner_questions(owner_reply_code)
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
      const origin = this.cleanOrigin(body.origin);
      if (row && !String(row.origin || "") && origin) {
        this.state.storage.sql.exec("UPDATE thread SET origin=? WHERE id=?", origin, body.id);
      }
      return json(this.createResponse(body.origin, row));
    }
    const now = Date.now();
    const origin = this.cleanOrigin(body.origin);
    this.state.storage.sql.exec(
      `INSERT INTO thread (id, topic, goal, origin, created, updated, host_token, guest_token, guest_invite_code)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      body.id,
      body.topic || "untitled",
      body.goal || "",
      origin,
      now,
      now,
      body.host_token,
      body.guest_token,
      body.guest_invite_code || randomInviteCode(),
    );
    return json(this.createResponse(body.origin, this.threadRow()));
  }

  private cleanOrigin(origin: unknown): string {
    const value = String(origin || "").trim().replace(/\/$/, "");
    if (!/^https?:\/\/[^/\s]+$/i.test(value)) return "";
    if (/^https?:\/\/thread$/i.test(value)) return "";
    return value;
  }

  private publicOrigin(row: Record<string, unknown> | null, origin?: unknown): string {
    return this.cleanOrigin(row?.origin) || this.cleanOrigin(origin) || DEFAULT_PUBLIC_ORIGIN;
  }

  private createResponse(origin: string, row: Record<string, unknown> | null): Record<string, unknown> {
    const id = String(row?.id || "");
    const guestToken = String(row?.guest_token || "");
    const inviteCode = String(row?.guest_invite_code || "");
    const publicOrigin = this.publicOrigin(row, origin);
    return {
      thread_id: id,
      host_token: String(row?.host_token || ""),
      guest_token: guestToken,
      invite_url: `${publicOrigin}/threads/${id}/join?token=${encodeURIComponent(guestToken)}`,
      invite_code: inviteCode,
      public_invite_url: `${publicOrigin}/i/${id}/${encodeURIComponent(inviteCode)}`,
      public_message: `Send this invite to the other person's agent: ${publicOrigin}/i/${id}/${encodeURIComponent(inviteCode)}`,
    };
  }

  private handlePublicInvite(url: URL, request: Request): Response {
    const row = this.threadRow();
    if (!row) return json({ error: "thread not found" }, 404);
    const code = String(url.searchParams.get("code") || "").trim();
    const expected = String(row.guest_invite_code || "").trim();
    if (!code || !expected || code !== expected) {
      return json({ error: "invalid_invite_code" }, 401);
    }
    const id = String(row.id || "");
    const origin = String(url.searchParams.get("origin") || "").replace(/\/$/, "") || url.origin;
    const payload = {
      thread_id: id,
      role: "guest",
      token: String(row.guest_token || ""),
      topic: String(row.topic || ""),
      goal: String(row.goal || ""),
      join_url: `${origin}/threads/${id}/join?token=${encodeURIComponent(String(row.guest_token || ""))}`,
    };
    const accept = request.headers.get("Accept") || "";
    const wantsJson = url.searchParams.get("format") === "json" || /\bapplication\/(?:json|clawroom\+json)\b/i.test(accept);
    const includeBody = request.method !== "HEAD";
    if (wantsJson) return json(payload, 200, includeBody);

    const title = "ClawRoom Invite";
    const description = "Forward this link to the other person's agent so it can join the room.";
    return html(`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <meta property="og:title" content="${title}">
  <meta property="og:description" content="${description}">
  <title>${title}</title>
  <style>
    body { font: 16px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #17202a; background: #f6f8fb; }
    main { max-width: 560px; margin: 12vh auto; padding: 32px; background: #fff; border: 1px solid #d7dde8; border-radius: 8px; }
    h1 { font-size: 24px; margin: 0 0 12px; }
    p { margin: 0 0 12px; }
    code { overflow-wrap: anywhere; }
  </style>
</head>
<body>
  <main>
    <h1>ClawRoom invite</h1>
    <p>Forward this link to the other person's agent. The agent can join the room and report back when the agents settle it.</p>
    <p><strong>Room:</strong> <code>${id}</code></p>
  </main>
</body>
</html>`, 200, includeBody);
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

  private maxThreadMs(): number {
    return numberEnv(this.env.CLAWROOM_MAX_THREAD_MS, DEFAULT_MAX_THREAD_MS, 10 * 60 * 1000, 24 * 60 * 60 * 1000);
  }

  private maxMessages(): number {
    return numberEnv(this.env.CLAWROOM_MAX_MESSAGES, DEFAULT_MAX_MESSAGES, 10, 1000);
  }

  private maxTextChars(): number {
    return numberEnv(this.env.CLAWROOM_MAX_TEXT_CHARS, DEFAULT_MAX_TEXT_CHARS, 500, 50_000);
  }

  private minHeartbeatMs(): number {
    return numberEnv(this.env.CLAWROOM_MIN_HEARTBEAT_MS, DEFAULT_MIN_HEARTBEAT_MS, 0, 60_000);
  }

  private expiredResponse(): Response | null {
    const row = this.threadRow();
    if (!row || Boolean(Number(row.closed || 0))) return null;
    const created = Number(row.created || 0);
    const maxAge = this.maxThreadMs();
    if (!created || Date.now() - created <= maxAge) return null;
    return json({
      error: "thread_expired",
      max_thread_ms: maxAge,
      hint: "This room expired. Create a new room if coordination still needs to continue.",
    }, 410);
  }

  private textOrError(rawText: string, emptyError = "text_required"): { text: string } | Response {
    const text = String(rawText || "").trim();
    if (!text) return json({ error: emptyError }, 400);
    const max = this.maxTextChars();
    if (text.length > max) {
      return json({ error: "text_too_long", max_text_chars: max }, 413);
    }
    return { text };
  }

  private messageBudgetError(): Response | null {
    const countRow = this.state.storage.sql
      .exec("SELECT COUNT(*) AS count FROM messages")
      .toArray()[0] as Record<string, unknown> | undefined;
    const count = Number(countRow?.count || 0);
    const max = this.maxMessages();
    if (count < max) return null;
    return json({
      error: "room_message_limit_exceeded",
      max_messages: max,
      hint: "This room reached its relay safety limit. Create a new room if more work is needed.",
    }, 429);
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
    if (metadata.source) out.source = this.sanitizeMetadataText(metadata.source, 64);
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

  private sanitizeMetadataText(value: unknown, maxLength: number): string {
    return String(value || "")
      .trim()
      .replace(/[^a-zA-Z0-9_.:-]/g, "_")
      .slice(0, maxLength);
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
    const parsedText = this.textOrError(rawText, kind === "close" ? "summary_required" : "text_required");
    if (parsedText instanceof Response) return parsedText;
    const { text } = parsedText;

    const existing = this.idempotencyHit(auth.role, idempotencyKey);
    if (existing) return json(existing.body, existing.status);
    if (kind !== "close") {
      const budget = this.messageBudgetError();
      if (budget) return budget;
    }

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
    url?: URL,
  ): Response {
    const parsedText = this.textOrError(rawText);
    if (parsedText instanceof Response) return parsedText;
    const { text } = parsedText;

    const existing = this.idempotencyHit(auth.role, idempotencyKey);
    if (existing) return json(existing.body, existing.status);
    const budget = this.messageBudgetError();
    if (budget) return budget;

    const row = this.threadRow();
    if (Boolean(Number(row?.closed || 0))) {
      return json({ error: "thread is closed" }, 400);
    }

    const nextId = this.nextMessageId();
    const now = Date.now();
    const ttlMs = Math.max(60_000, Math.min(OWNER_REPLY_TTL_MS, Number(ttlSeconds || 0) * 1000 || OWNER_REPLY_TTL_MS));
    const questionId = `q_${crypto.randomUUID().slice(0, 12)}`;
    const ownerReplyToken = randomOwnerReplyToken();
    const ownerReplyCode = randomOwnerReplyCode();
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
       (question_id, role, ask_message_id, owner_reply_token, owner_reply_code, text, created, expires_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      questionId,
      auth.role,
      nextId,
      ownerReplyToken,
      ownerReplyCode,
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
      owner_reply_url: `${this.publicOrigin(row, url?.origin)}/threads/${id}/owner-reply?code=${encodeURIComponent(ownerReplyCode)}`,
      expires_at: expiresAt,
    };
    this.recordIdempotency(auth.role, idempotencyKey, 201, response);
    this.wake();
    return json(response, 201);
  }

  private handleOwnerReplyPage(id: string, request: Request, url: URL): Response {
    const includeBody = request.method !== "HEAD";
    const code = String(url.searchParams.get("code") || "").trim();
    if (!code) return this.ownerReplyHtml("Decision Link", "This decision link is missing its code.", 400, includeBody);

    const question = this.state.storage.sql
      .exec("SELECT * FROM owner_questions WHERE owner_reply_code=? LIMIT 1", code)
      .toArray()[0] as Record<string, unknown> | undefined;
    if (!question) return this.ownerReplyHtml("Decision Link", "This decision link is not valid.", 404, includeBody);

    const now = Date.now();
    if (Boolean(Number(question.consumed || 0))) {
      return this.ownerReplyHtml("Already Recorded", "This decision was already recorded.", 409, includeBody);
    }
    const expiresAt = Number(question.expires_at || 0);
    if (expiresAt && now > expiresAt) {
      return this.ownerReplyHtml("Expired", "This decision link expired. Ask your agent to send a fresh question.", 410, includeBody);
    }

    const questionText = escapeHtml(question.text || "");
    const role = escapeHtml(question.role || "");
    const action = `/threads/${encodeURIComponent(id)}/owner-reply`;
    return html(`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <title>ClawRoom Decision</title>
  <style>
    body { font: 16px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #17202a; background: #f6f8fb; }
    main { max-width: 620px; margin: 8vh auto; padding: 28px; background: #fff; border: 1px solid #d7dde8; border-radius: 8px; }
    h1 { font-size: 24px; margin: 0 0 12px; }
    .question { padding: 16px; border: 1px solid #d7dde8; border-radius: 8px; background: #f9fbff; margin: 16px 0; }
    textarea { box-sizing: border-box; width: 100%; min-height: 110px; padding: 12px; border: 1px solid #bac4d3; border-radius: 8px; font: inherit; }
    button { border: 0; border-radius: 8px; padding: 10px 14px; margin: 8px 8px 0 0; font: inherit; color: #fff; background: #106b5f; cursor: pointer; }
    button.secondary { background: #425466; }
    .muted { color: #5f6b7a; font-size: 14px; }
  </style>
</head>
<body>
  <main>
    <h1>ClawRoom needs your decision</h1>
    <p class="muted">This will be sent to your ${role} agent so it can continue the room.</p>
    <div class="question">${questionText}</div>
    <form method="post" action="${action}">
      <input type="hidden" name="code" value="${escapeHtml(code)}">
      <input type="hidden" name="source" value="owner_url">
      <textarea name="text" placeholder="Approve, reject, or give a counter-instruction." required></textarea>
      <button type="submit">Send Decision</button>
    </form>
    <form method="post" action="${action}">
      <input type="hidden" name="code" value="${escapeHtml(code)}">
      <input type="hidden" name="source" value="owner_url">
      <button type="submit" name="text" value="approve">Approve</button>
      <button class="secondary" type="submit" name="text" value="reject">Reject</button>
    </form>
  </main>
</body>
</html>`, 200, includeBody);
  }

  private ownerReplyHtml(title: string, message: string, status = 200, includeBody = true): Response {
    return html(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>${escapeHtml(title)}</title>
<style>body{font:16px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;color:#17202a;background:#f6f8fb}main{max-width:560px;margin:12vh auto;padding:28px;background:#fff;border:1px solid #d7dde8;border-radius:8px}h1{font-size:24px;margin:0 0 12px}</style></head>
<body><main><h1>${escapeHtml(title)}</h1><p>${escapeHtml(message)}</p></main></body></html>`, status, includeBody);
  }

  private wantsOwnerReplyHtml(request: Request): boolean {
    const accept = request.headers.get("Accept") || "";
    const contentType = request.headers.get("Content-Type") || "";
    return /\btext\/html\b/i.test(accept) || /\bapplication\/x-www-form-urlencoded\b|\bmultipart\/form-data\b/i.test(contentType);
  }

  private async ownerReplyPayload(request: Request, url: URL): Promise<Record<string, string>> {
    const contentType = request.headers.get("Content-Type") || "";
    const fromUrl: Record<string, string> = {};
    for (const key of ["token", "code", "question_id", "role", "text", "source"]) {
      const value = url.searchParams.get(key);
      if (value != null) fromUrl[key] = value;
    }
    if (/\bapplication\/x-www-form-urlencoded\b|\bmultipart\/form-data\b/i.test(contentType)) {
      const form = await request.formData();
      const out = { ...fromUrl };
      for (const key of ["token", "code", "question_id", "role", "text", "source"]) {
        const value = form.get(key);
        if (value != null) out[key] = String(value);
      }
      return out;
    }
    const body = await readJson<Record<string, unknown>>(request);
    const out = { ...fromUrl };
    for (const key of ["token", "code", "question_id", "role", "text", "source"]) {
      if (body[key] != null) out[key] = String(body[key]);
    }
    return out;
  }

  private ownerReplyError(request: Request, error: string, status: number): Response {
    if (this.wantsOwnerReplyHtml(request)) {
      return this.ownerReplyHtml("Decision Not Recorded", error, status);
    }
    return json({ error }, status);
  }

  private async handleOwnerReply(id: string, request: Request, url: URL): Promise<Response> {
    const body = await this.ownerReplyPayload(request, url);
    const code = String(body.code || "").trim();
    let token = String(body.token || "").trim();
    let questionId = String(body.question_id || "").trim();
    let role = String(body.role || "").trim();
    let question: Record<string, unknown> | undefined;

    if (code) {
      question = this.state.storage.sql
        .exec("SELECT * FROM owner_questions WHERE owner_reply_code=? LIMIT 1", code)
        .toArray()[0] as Record<string, unknown> | undefined;
      if (!question) return this.ownerReplyError(request, "question_not_found", 404);
      if (questionId && questionId !== String(question.question_id || "")) return this.ownerReplyError(request, "unauthorized_owner_reply", 401);
      if (role && role !== String(question.role || "")) return this.ownerReplyError(request, "unauthorized_owner_reply", 401);
      if (token && token !== String(question.owner_reply_token || "")) return this.ownerReplyError(request, "unauthorized_owner_reply", 401);
      token = String(question.owner_reply_token || "");
      questionId = String(question.question_id || "");
      role = String(question.role || "");
    }

    const parsedText = this.textOrError(String(body.text || ""));
    if (parsedText instanceof Response) return parsedText;
    const { text } = parsedText;
    const source = this.sanitizeMetadataText(body.source || (code ? "owner_url" : ""), 64);

    if (!token || !questionId || !role) return this.ownerReplyError(request, "invalid_owner_reply", 400);
    if (!["host", "guest"].includes(role)) return this.ownerReplyError(request, "invalid_owner_reply", 400);

    question ||= this.state.storage.sql
      .exec("SELECT * FROM owner_questions WHERE question_id=? LIMIT 1", questionId)
      .toArray()[0] as Record<string, unknown> | undefined;
    if (!question) return this.ownerReplyError(request, "question_not_found", 404);
    const storedRole = String(question.role || "");
    if (storedRole !== role) {
      console.warn(JSON.stringify({
        event: "owner_reply_role_mismatch",
        thread_id: id,
        question_id: questionId,
        requested_role: role,
        stored_role: storedRole,
      }));
      return this.ownerReplyError(request, "unauthorized_owner_reply", 401);
    }
    if (String(question.owner_reply_token || "") !== token) return this.ownerReplyError(request, "unauthorized_owner_reply", 401);
    if (Boolean(Number(question.consumed || 0))) return this.ownerReplyError(request, "owner_reply_already_consumed", 409);

    const now = Date.now();
    const expiresAt = Number(question.expires_at || 0);
    if (expiresAt && now > expiresAt) return this.ownerReplyError(request, "owner_reply_expired", 410);
    const budget = this.messageBudgetError();
    if (budget) return budget;

    const nextId = this.nextMessageId();
    this.state.storage.sql.exec(
      "INSERT INTO messages (id, role, text, ts, kind, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
      nextId,
      role,
      text,
      now,
      "owner_reply",
      JSON.stringify({
        question_id: questionId,
        ask_message_id: Number(question.ask_message_id || -1),
        ...(source ? { source } : {}),
      }),
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
      ...(source ? { source } : {}),
      ok: true,
    };
    this.wake();
    if (this.wantsOwnerReplyHtml(request)) {
      return this.ownerReplyHtml("Decision Recorded", "Your decision was recorded. Your agent can continue now.", 201);
    }
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
    const previous = this.state.storage.sql
      .exec("SELECT updated FROM runtime_heartbeats WHERE role=? LIMIT 1", auth.role)
      .toArray()[0] as Record<string, unknown> | undefined;
    const minHeartbeatMs = this.minHeartbeatMs();
    const elapsed = previous ? now - Number(previous.updated || 0) : Number.POSITIVE_INFINITY;
    if (!["stopped", "failed"].includes(status) && elapsed < minHeartbeatMs) {
      return json({
        ok: false,
        error: "heartbeat_too_soon",
        retry_after_ms: minHeartbeatMs - elapsed,
      }, 429);
    }
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

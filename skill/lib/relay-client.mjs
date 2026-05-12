// skill/lib/relay-client.mjs
// Thin HTTP wrapper for the ClawRoom relay. Conventions:
//   - Authorization: Bearer <token>
//   - X-Idempotency-Key on mutating calls (logical-action identity, not timestamp)
//   - Exponential backoff 250 * attempt^2 ms, up to 4 attempts
//   - Fatal vs retriable classification (no silent failures)
//
// This is layer 2 of the v4 architecture. No LLM, no policy, no semantics.
// See docs/decisions/0001-direct-mode-replaces-bridge.md.

import crypto from 'node:crypto';
import {
  DEFAULT_RELAY_URL,
  DEFAULT_LONG_POLL_WAIT_SECONDS,
  DEFAULT_RETRY_ATTEMPTS,
  DEFAULT_RETRY_BASE_MS,
  FATAL_RELAY_STATUSES,
  RETRIABLE_RELAY_STATUSES,
} from './types.mjs';

/** @typedef {import('./types.mjs').Role} Role */
/** @typedef {import('./types.mjs').MessageKind} MessageKind */
/** @typedef {import('./types.mjs').CloseDraft} CloseDraft */

/**
 * @typedef {Object} ClientOptions
 * @property {string} [relay]      base URL, defaults to DEFAULT_RELAY_URL
 * @property {string} token        Bearer auth (host_xxx or guest_xxx)
 * @property {string} room_id
 * @property {Role}   role
 * @property {number} [retries]    default DEFAULT_RETRY_ATTEMPTS
 * @property {number} [timeoutMs]  default 30_000
 */

/** @typedef {{ ok: boolean, status: number, body: unknown, attempts: number }} RelayResult */

class RelayError extends Error {
  constructor(message, { status, retriable, fatal, attempts, body }) {
    super(message);
    this.name = 'RelayError';
    this.status = status;
    this.retriable = retriable;
    this.fatal = fatal;
    this.attempts = attempts;
    this.body = body;
  }
}

function classifyStatus(status) {
  if (FATAL_RELAY_STATUSES.has(status)) return { fatal: true, retriable: false };
  if (RETRIABLE_RELAY_STATUSES.has(status)) return { fatal: false, retriable: true };
  if (status >= 500) return { fatal: false, retriable: true };
  if (status >= 400) return { fatal: true, retriable: false };
  return { fatal: false, retriable: false };
}

function classifyNetworkError(err) {
  const msg = String(err?.message || err || '').toLowerCase();
  // Network/TLS/timeout transient failures — retriable per reflection sync.
  if (
    msg.includes('ssl_error_syscall') ||
    msg.includes('timeout') ||
    msg.includes('econnreset') ||
    msg.includes('etimedout') ||
    msg.includes('socket hang up') ||
    msg.includes('fetch failed') ||
    err?.name === 'AbortError'
  ) {
    return { fatal: false, retriable: true };
  }
  return { fatal: true, retriable: false };
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Build an X-Idempotency-Key per the logical-action-identity rules
 * agreed in reflection sync `t_bf866856-df0`.
 *
 *   post:          sha256(room_id + role + kind + previous_cursor_or_parent_id + content_hash + optional logical_post_id)
 *   close:         sha256(room_id + role + 'close' + closedraft_hash)
 *   owner-reply:   sha256(room_id + role + question_id + decision + answer_hash)
 *   heartbeat:     sha256(room_id + role + 'heartbeat' + minute_bucket)
 *
 * Role is in the key to prevent cross-role dedup. previous_cursor_or_parent_id
 * is in the post key to prevent same text in different turns collapsing.
 * Timestamp is NOT in the key, except in heartbeat as a coarse bucket.
 *
 * @param {Object} args
 * @param {string} args.room_id
 * @param {Role}   args.role
 * @param {'post' | 'close' | 'owner_reply' | 'heartbeat'} args.action
 * @param {MessageKind} [args.kind]                        - for post
 * @param {number}      [args.previous_cursor_or_parent_id] - for post
 * @param {string}      [args.content]                      - for post (canonical content)
 * @param {string}      [args.logical_post_id]              - optional caller-supplied stability nonce
 * @param {string}      [args.closedraft_canonical]         - for close (canonical CloseDraft JSON)
 * @param {string}      [args.question_id]                  - for owner_reply
 * @param {'approve' | 'reject'} [args.decision]            - for owner_reply
 * @param {string}      [args.answer_content]               - for owner_reply
 * @returns {string} hex sha256
 */
export function buildIdempotencyKey(args) {
  const h = crypto.createHash('sha256');
  if (!args || !args.room_id || !args.role || !args.action) {
    throw new Error('buildIdempotencyKey: missing room_id/role/action');
  }
  h.update(args.room_id);
  h.update('|');
  h.update(args.role);
  h.update('|');
  h.update(args.action);
  h.update('|');
  if (args.action === 'post') {
    if (!args.kind) throw new Error('buildIdempotencyKey: post requires kind');
    if (typeof args.previous_cursor_or_parent_id !== 'number') {
      throw new Error('buildIdempotencyKey: post requires previous_cursor_or_parent_id (use -1 for first post)');
    }
    if (typeof args.content !== 'string') {
      throw new Error('buildIdempotencyKey: post requires content');
    }
    h.update(args.kind);
    h.update('|');
    h.update(String(args.previous_cursor_or_parent_id));
    h.update('|');
    h.update(crypto.createHash('sha256').update(args.content, 'utf8').digest('hex'));
    if (args.logical_post_id) {
      h.update('|');
      h.update(args.logical_post_id);
    }
  } else if (args.action === 'close') {
    if (typeof args.closedraft_canonical !== 'string') {
      throw new Error('buildIdempotencyKey: close requires closedraft_canonical');
    }
    h.update(crypto.createHash('sha256').update(args.closedraft_canonical, 'utf8').digest('hex'));
  } else if (args.action === 'owner_reply') {
    if (!args.question_id || !args.decision || typeof args.answer_content !== 'string') {
      throw new Error('buildIdempotencyKey: owner_reply requires question_id, decision, answer_content');
    }
    h.update(args.question_id);
    h.update('|');
    h.update(args.decision);
    h.update('|');
    h.update(crypto.createHash('sha256').update(args.answer_content, 'utf8').digest('hex'));
  } else if (args.action === 'heartbeat') {
    // Coarse bucket: one heartbeat per minute is plenty.
    const bucket = Math.floor(Date.now() / 60_000);
    h.update(String(bucket));
  } else {
    throw new Error(`buildIdempotencyKey: unknown action ${args.action}`);
  }
  return h.digest('hex');
}

export class RelayClient {
  /** @param {ClientOptions} opts */
  constructor(opts) {
    if (!opts || !opts.token || !opts.room_id || !opts.role) {
      throw new Error('RelayClient: token, room_id, role required');
    }
    this.relay = opts.relay || DEFAULT_RELAY_URL;
    this.token = opts.token;
    this.room_id = opts.room_id;
    this.role = opts.role;
    this.retries = opts.retries || DEFAULT_RETRY_ATTEMPTS;
    this.timeoutMs = opts.timeoutMs || 30_000;
  }

  /**
   * @param {string} path
   * @param {{ method?: string, body?: unknown, idempotencyKey?: string, longPollWaitSeconds?: number }} [options]
   * @returns {Promise<RelayResult>}
   */
  async request(path, options = {}) {
    const method = options.method || 'GET';
    const url = new URL(`${this.relay}${path}`);
    const headers = {
      'accept': 'application/json',
      'authorization': `Bearer ${this.token}`,
    };
    if (options.idempotencyKey) headers['x-idempotency-key'] = options.idempotencyKey;
    /** @type {RequestInit} */
    const init = { method, headers };
    if (options.body !== undefined) {
      headers['content-type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    // Long-poll calls need a longer abort timeout than the relay wait.
    const longPollMs = (options.longPollWaitSeconds || 0) * 1000;
    const abortMs = Math.max(this.timeoutMs, longPollMs + 10_000);

    let lastErr = null;
    for (let attempt = 1; attempt <= this.retries; attempt++) {
      const ac = new AbortController();
      const timer = setTimeout(() => ac.abort(), abortMs);
      try {
        const response = await fetch(url.toString(), { ...init, signal: ac.signal });
        const text = await response.text();
        let body;
        try {
          body = text ? JSON.parse(text) : {};
        } catch {
          body = { raw: text };
        }
        if (!response.ok) {
          const cls = classifyStatus(response.status);
          const err = new RelayError(
            `relay ${response.status} ${method} ${path}: ${typeof body === 'object' ? JSON.stringify(body).slice(0, 200) : ''}`,
            { status: response.status, ...cls, attempts: attempt, body }
          );
          if (cls.fatal) throw err;
          if (cls.retriable && attempt < this.retries) {
            await delay(DEFAULT_RETRY_BASE_MS * attempt * attempt);
            lastErr = err;
            continue;
          }
          throw err;
        }
        return { ok: true, status: response.status, body, attempts: attempt };
      } catch (err) {
        if (err instanceof RelayError) {
          if (!err.retriable || attempt >= this.retries) throw err;
          lastErr = err;
          await delay(DEFAULT_RETRY_BASE_MS * attempt * attempt);
          continue;
        }
        const cls = classifyNetworkError(err);
        const wrapped = new RelayError(
          `relay network ${method} ${path}: ${err?.message || err}`,
          { status: 0, ...cls, attempts: attempt, body: null }
        );
        if (cls.fatal || attempt >= this.retries) throw wrapped;
        lastErr = wrapped;
        await delay(DEFAULT_RETRY_BASE_MS * attempt * attempt);
      } finally {
        clearTimeout(timer);
      }
    }
    throw lastErr || new RelayError(`relay ${method} ${path} failed`, { status: 0, fatal: true, retriable: false, attempts: this.retries, body: null });
  }

  // ---- high-level operations ----

  /** GET /threads/:id/join — room snapshot (close_state, role check, etc). */
  async join() {
    return this.request(`/threads/${this.room_id}/join`);
  }

  /** GET /threads/:id/messages?after=N&wait=S — full bodies. Primary agent only. */
  async pollMessages({ after = -1, waitSeconds = 0 } = {}) {
    const params = new URLSearchParams({ after: String(after), wait: String(waitSeconds) });
    return this.request(`/threads/${this.room_id}/messages?${params.toString()}`, {
      longPollWaitSeconds: waitSeconds,
    });
  }

  /** GET /threads/:id/events?after=N&wait=S — metadata only. Watch helper use. */
  async pollEvents({ after = -1, waitSeconds = 0 } = {}) {
    const params = new URLSearchParams({ after: String(after), wait: String(waitSeconds) });
    return this.request(`/threads/${this.room_id}/events?${params.toString()}`, {
      longPollWaitSeconds: waitSeconds,
    });
  }

  /**
   * POST /threads/:id/messages with logical-action idempotency.
   * @param {Object} args
   * @param {string} args.text
   * @param {number} args.previous_cursor_or_parent_id
   * @param {string} [args.logical_post_id]
   */
  async postMessage({ text, previous_cursor_or_parent_id, logical_post_id }) {
    const key = buildIdempotencyKey({
      room_id: this.room_id,
      role: this.role,
      action: 'post',
      kind: 'message',
      previous_cursor_or_parent_id,
      content: text,
      logical_post_id,
    });
    return this.request(`/threads/${this.room_id}/messages`, {
      method: 'POST',
      body: { text },
      idempotencyKey: key,
    });
  }

  /**
   * POST /threads/:id/close with idempotency keyed on canonical CloseDraft.
   * The semantic validation (CloseDraft schema + state) is done in close.mjs
   * BEFORE this is called. relay-client does not parse semantics.
   *
   * @param {Object} args
   * @param {string} args.summary  - JSON-encoded CloseDraft or freeform fallback
   * @param {string} args.closedraft_canonical - canonical JSON used for idempotency
   */
  async closeSide({ summary, closedraft_canonical }) {
    const key = buildIdempotencyKey({
      room_id: this.room_id,
      role: this.role,
      action: 'close',
      closedraft_canonical,
    });
    return this.request(`/threads/${this.room_id}/close`, {
      method: 'POST',
      body: { summary },
      idempotencyKey: key,
    });
  }

  /**
   * POST /threads/:id/ask-owner. Async cross-runtime owner approval path.
   * Default v4 owner approval lives in the primary-agent conversation
   * (handled locally via state.setPendingOwnerAsk); this endpoint is the
   * fallback only (invariant 6).
   *
   * Codex pass 3 stabilization: idempotency key is keyed on the caller-
   * supplied question_id, not the wall-clock timestamp. Retry across
   * restart with the same question_id collapses to one server-side record.
   */
  async askOwner({ question_id, text, ttl_seconds = 30 * 60 }) {
    if (!question_id) throw new Error('askOwner: question_id required');
    const key = buildIdempotencyKey({
      room_id: this.room_id,
      role: this.role,
      action: 'post',
      kind: 'ask_owner',
      previous_cursor_or_parent_id: -1,
      content: text,
      logical_post_id: `ask:${question_id}`,
    });
    return this.request(`/threads/${this.room_id}/ask-owner`, {
      method: 'POST',
      body: { text, ttl_seconds, question_id },
      idempotencyKey: key,
    });
  }

  // postOwnerReply intentionally removed from Phase 1 lib surface
  // (Codex pass 4 P1). The relay's POST /owner-reply contract requires an
  // owner_reply_token or code (from the relay's /ask-owner response) plus
  // {role, text, source}, and the relay handler does not yet honor
  // X-Idempotency-Key for replies. Shipping a primitive that doesn't match
  // the server contract is worse than not shipping it. The cross-runtime
  // fallback (web owner-reply page / async bot relaying decisions back) is
  // Phase 2/5 scope: server-side handleOwnerReply must add idempotency
  // first, then this client lib can be implemented against the real shape.
  // Default v4 path remains state-only via state.resolveOwnerAsk + the
  // CLI's `clawroom owner-reply` (state-only, no relay round-trip).

  /** POST /threads/:id/heartbeat — liveness. */
  async heartbeat({ status = 'running', extra = {} } = {}) {
    const key = buildIdempotencyKey({
      room_id: this.room_id,
      role: this.role,
      action: 'heartbeat',
    });
    return this.request(`/threads/${this.room_id}/heartbeat`, {
      method: 'POST',
      body: { status, ...extra },
      idempotencyKey: key,
    });
  }
}

/**
 * Hosted-relay create with admission key (invariant 12) + idempotency-safe retry
 * (Codex pass 2 P1.5). Sends `X-Idempotency-Key`; relay caches the create response
 * by key with a TTL, so internal retry on transient TLS / 5xx is safe.
 *
 * Caller may supply `idempotency_key` for stability across process restarts.
 * Otherwise we generate a fresh UUID per call; within a single call, all
 * retries reuse it.
 *
 * @param {Object} args
 * @param {string} args.topic
 * @param {string} args.goal
 * @param {string} [args.relay]
 * @param {string} [args.create_key]      - falls back to CLAWROOM_CREATE_KEY env
 * @param {string} [args.idempotency_key] - optional; stable across retries (and process restarts if caller persists it)
 * @param {number} [args.retries]         - default DEFAULT_RETRY_ATTEMPTS
 * @returns {Promise<Record<string, unknown>>}
 */
export async function createRoom({ topic, goal, relay, create_key, idempotency_key, retries }) {
  const idempKey = idempotency_key || crypto.randomUUID();
  const attempts = retries || DEFAULT_RETRY_ATTEMPTS;
  const url = new URL(`${relay || DEFAULT_RELAY_URL}/threads/new`);
  url.searchParams.set('topic', topic || 'untitled');
  url.searchParams.set('goal', goal || '');
  const createKey = create_key || process.env.CLAWROOM_CREATE_KEY || '';
  const headers = {
    accept: 'application/json',
    'x-idempotency-key': idempKey,
  };
  if (createKey) headers['x-clawroom-create-key'] = createKey;

  let lastErr = null;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const res = await fetch(url.toString(), { headers });
      const text = await res.text();
      let body;
      try { body = JSON.parse(text); } catch { body = { raw: text }; }
      if (!res.ok) {
        const cls = classifyStatus(res.status);
        const err = new RelayError(
          `createRoom ${res.status}: ${typeof body === 'object' ? JSON.stringify(body).slice(0, 200) : ''}`,
          { status: res.status, ...cls, attempts: attempt, body }
        );
        if (cls.fatal) throw err;
        if (cls.retriable && attempt < attempts) {
          await delay(DEFAULT_RETRY_BASE_MS * attempt * attempt);
          lastErr = err;
          continue;
        }
        throw err;
      }
      // Tag the body with the key we used so caller can persist it for later
      // out-of-process retry. Also surface whether this was an idempotent replay.
      body._idempotency_key = idempKey;
      if (res.headers.get('x-idempotent-replay') === 'true') body._idempotent_replay = true;
      return body;
    } catch (err) {
      if (err instanceof RelayError) {
        if (!err.retriable || attempt >= attempts) throw err;
        lastErr = err;
        await delay(DEFAULT_RETRY_BASE_MS * attempt * attempt);
        continue;
      }
      const cls = classifyNetworkError(err);
      const wrapped = new RelayError(
        `createRoom network: ${err?.message || err}`,
        { status: 0, ...cls, attempts: attempt, body: null }
      );
      if (cls.fatal || attempt >= attempts) throw wrapped;
      lastErr = wrapped;
      await delay(DEFAULT_RETRY_BASE_MS * attempt * attempt);
    }
  }
  throw lastErr || new RelayError('createRoom: failed without specific error', { status: 0, fatal: true, retriable: false, attempts, body: null });
}

/**
 * Parse a public invite URL of shape https://relay/i/:thread_id/:invite_code.
 * Returns {thread_id, invite_code, invite_url, relay}. Does not call the relay.
 *
 * `relay` is the origin extracted from the invite URL itself — public invites
 * must carry their relay origin so BYO/hosted-fallback rooms join the right
 * relay without the caller having to know it (invariant 12 / Codex P1).
 *
 * @param {string} url
 */
export function resolveInvite(url) {
  const s = String(url || '');
  const m = /\/i\/([^/?#]+)\/([^/?#]+)/.exec(s);
  if (!m) throw new Error('resolveInvite: not a clawroom invite URL');
  let origin = '';
  try {
    origin = new URL(s).origin;
  } catch {
    throw new Error(`resolveInvite: invite URL is not a valid URL: ${s}`);
  }
  return {
    thread_id: m[1],
    invite_code: m[2],
    invite_url: s,
    relay: origin,
  };
}

/**
 * GET /threads/:id/invite?code=... — resolve invite to a guest token.
 *
 * @param {Object} args
 * @param {string} args.relay
 * @param {string} args.thread_id
 * @param {string} args.invite_code
 */
export async function joinRoom({ relay, thread_id, invite_code }) {
  const url = new URL(`${relay || DEFAULT_RELAY_URL}/threads/${thread_id}/invite`);
  url.searchParams.set('code', invite_code);
  const res = await fetch(url.toString(), { headers: { accept: 'application/json' } });
  const text = await res.text();
  let body;
  try { body = JSON.parse(text); } catch { body = { raw: text }; }
  if (!res.ok) {
    throw new RelayError(
      `joinRoom ${res.status}: ${typeof body === 'object' ? JSON.stringify(body).slice(0, 200) : ''}`,
      { status: res.status, ...classifyStatus(res.status), attempts: 1, body }
    );
  }
  return body;
}

export { RelayError };

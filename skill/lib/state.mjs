// skill/lib/state.mjs
// Local state file r/w + resumeRoom + pending_owner_ask lifecycle.
//
// State file: ${STATE_DIR}/<room_id>-<role>.state.json
// Schema is documented in skill/lib/types.mjs (RoomState).
//
// All writes are atomic (write to .tmp then rename). All reads are
// JSON.parse with strict validation; corruption fails closed (invariant 16).

import fs from 'node:fs';
import path from 'node:path';
import { STATE_DIR } from './types.mjs';

/** @typedef {import('./types.mjs').RoomState} RoomState */
/** @typedef {import('./types.mjs').Role} Role */
/** @typedef {import('./types.mjs').PendingOwnerAsk} PendingOwnerAsk */
/** @typedef {import('./types.mjs').OwnerApproval} OwnerApproval */
/** @typedef {import('./types.mjs').CloseDraft} CloseDraft */

function ensureDir() {
  fs.mkdirSync(STATE_DIR, { recursive: true });
}

function statePath(roomId, role) {
  if (!roomId || typeof roomId !== 'string') throw new Error('state: roomId required');
  if (role !== 'host' && role !== 'guest') throw new Error(`state: bad role ${role}`);
  ensureDir();
  return path.join(STATE_DIR, `${roomId}-${role}.state.json`);
}

function nowIso() {
  return new Date().toISOString();
}

/**
 * Validate a RoomState object loosely. Returns the input on success,
 * throws on bad shape. Designed to fail closed (Codex Q5 hostile test).
 *
 * @param {unknown} raw
 * @returns {RoomState}
 */
function validateRoomState(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('state: not an object');
  const s = /** @type {Record<string, unknown>} */ (raw);
  if (typeof s.room_id !== 'string' || !s.room_id) throw new Error('state: bad room_id');
  if (s.role !== 'host' && s.role !== 'guest') throw new Error(`state: bad role ${s.role}`);
  if (typeof s.last_event_cursor !== 'number') throw new Error('state: bad last_event_cursor');
  if (s.pending_owner_ask !== null && (typeof s.pending_owner_ask !== 'object' || !s.pending_owner_ask)) {
    throw new Error('state: bad pending_owner_ask');
  }
  if (!Array.isArray(s.owner_approvals)) throw new Error('state: bad owner_approvals');
  if (s.draft_close !== null && (typeof s.draft_close !== 'object' || !s.draft_close)) {
    throw new Error('state: bad draft_close');
  }
  return /** @type {RoomState} */ (raw);
}

/**
 * Initialize a local state file. Per invariant 17 (role custody non-transferable),
 * host state stores ONLY host_token; guest state stores ONLY guest_token. The
 * peer's token is never persisted in our state file. The invite URL embeds the
 * guest token for handoff and that's the only place the host needs to see it.
 *
 * @param {Object} args
 * @param {string} args.room_id
 * @param {Role}   args.role
 * @param {string} [args.host_token]   - REQUIRED when role==='host'; rejected when role==='guest'
 * @param {string} [args.guest_token]  - REQUIRED when role==='guest'; rejected when role==='host'
 * @param {string} [args.topic]
 * @param {string} [args.goal]
 * @returns {RoomState}
 */
export function initState({ room_id, role, host_token, guest_token, topic, goal }) {
  if (role === 'host' && guest_token) {
    throw new Error('initState: host state cannot persist guest_token (invariant 17 — role custody non-transferable)');
  }
  if (role === 'guest' && host_token) {
    throw new Error('initState: guest state cannot persist host_token (invariant 17 — role custody non-transferable)');
  }
  if (role === 'host' && !host_token) {
    throw new Error('initState: host state requires host_token');
  }
  if (role === 'guest' && !guest_token) {
    throw new Error('initState: guest state requires guest_token');
  }
  /** @type {RoomState} */
  const state = {
    room_id,
    role,
    last_event_cursor: -1,
    pending_owner_ask: null,
    owner_approvals: [],
    draft_close: null,
    started_at: nowIso(),
    last_seen_at: nowIso(),
    topic,
    goal,
    ...(role === 'host' ? { host_token } : { guest_token }),
  };
  writeState(state);
  return state;
}

/**
 * Atomic write of a state file.
 * @param {RoomState} state
 */
export function writeState(state) {
  validateRoomState(state);
  state.last_seen_at = nowIso();
  const p = statePath(state.room_id, state.role);
  const tmp = `${p}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), { mode: 0o600 });
  fs.renameSync(tmp, p);
}

/**
 * @param {string} room_id
 * @param {Role} role
 * @returns {RoomState | null}
 */
export function readState(room_id, role) {
  const p = statePath(room_id, role);
  if (!fs.existsSync(p)) return null;
  try {
    const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
    return validateRoomState(raw);
  } catch (err) {
    throw new Error(`state: corrupted file ${p}: ${err.message}`);
  }
}

/**
 * Compute a deterministic fingerprint of the state file content,
 * for Phase 5 cross-session resume artifact (proves the same state
 * file was read by a fresh session).
 *
 * @param {string} room_id
 * @param {Role} role
 * @returns {Promise<string>}
 */
export async function stateFingerprint(room_id, role) {
  const crypto = await import('node:crypto');
  const p = statePath(room_id, role);
  if (!fs.existsSync(p)) return '';
  return crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
}

/**
 * Resume a room from its state file. Hostile-test compliant: new caller
 * passes only room_id + role; we return the full state without any
 * inherited context from the previous session.
 *
 * @param {string} room_id
 * @param {Role} role
 * @returns {RoomState}
 */
export function resumeRoom(room_id, role) {
  const state = readState(room_id, role);
  if (!state) {
    const e = new Error(`state: no resume — ${room_id} ${role} not found`);
    /** @type {any} */ (e).fatal = true;
    throw e;
  }
  return state;
}

// ---- pending_owner_ask lifecycle (invariant 13) ----

/**
 * Set a pending owner ask. Blocks subsequent posts/closes past the
 * mandate boundary until resolved.
 *
 * @param {RoomState} state
 * @param {PendingOwnerAsk} ask
 * @returns {RoomState}
 */
export function setPendingOwnerAsk(state, ask) {
  if (state.pending_owner_ask) {
    throw new Error(`state: already has pending_owner_ask (${state.pending_owner_ask.question_id})`);
  }
  state.pending_owner_ask = ask;
  writeState(state);
  return state;
}

/**
 * Resolve a pending owner ask. Records the approval and clears
 * pending state. Returns updated state.
 *
 * @param {RoomState} state
 * @param {OwnerApproval} approval
 * @returns {RoomState}
 */
export function resolveOwnerAsk(state, approval) {
  if (!state.pending_owner_ask) {
    throw new Error('state: no pending_owner_ask to resolve');
  }
  if (state.pending_owner_ask.question_id !== approval.question_id) {
    throw new Error(`state: question_id mismatch (${state.pending_owner_ask.question_id} vs ${approval.question_id})`);
  }
  state.owner_approvals.push(approval);
  state.pending_owner_ask = null;
  writeState(state);
  return state;
}

/**
 * Check if a pending ask is past its timeout. Caller decides whether
 * to fire timeout_close or wait further.
 *
 * @param {RoomState} state
 * @returns {boolean}
 */
export function pendingAskTimedOut(state) {
  if (!state.pending_owner_ask) return false;
  return Date.now() >= Date.parse(state.pending_owner_ask.timeout_at);
}

/**
 * Look up approval for a specific question_id.
 *
 * @param {RoomState} state
 * @param {string} question_id
 * @returns {OwnerApproval | null}
 */
export function findApproval(state, question_id) {
  return state.owner_approvals.find((a) => a.question_id === question_id) || null;
}

// ---- cursor + draft helpers ----

/**
 * @param {RoomState} state
 * @param {number} cursor
 */
export function setCursor(state, cursor) {
  if (typeof cursor !== 'number') throw new Error('state: bad cursor');
  state.last_event_cursor = cursor;
  writeState(state);
}

/**
 * @param {RoomState} state
 * @param {CloseDraft | null} draft
 */
export function setDraftClose(state, draft) {
  state.draft_close = draft;
  writeState(state);
}

export { statePath };

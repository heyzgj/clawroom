// skill/lib/types.mjs
// v4 ClawRoom shared types + constants. Plain JS with JSDoc — portable to
// a Python mirror later. No runtime deps.
//
// Invariant 9 enforcement lives in makeWatchEvent below: the factory
// throws if a `text` (or any other) field appears on a watch event.

/**
 * @typedef {'host' | 'guest'} Role
 * @typedef {'message' | 'close' | 'ask_owner' | 'owner_reply'} MessageKind
 * @typedef {'agreement' | 'no_agreement' | 'partial'} CloseOutcome
 * @typedef {'primary_agent_conversation' | 'owner_url' | 'telegram_inbound'} ApprovalSource
 */

/**
 * @typedef {Object} WatchEvent
 * @property {number} id
 * @property {Role} from
 * @property {MessageKind} kind
 * @property {number} ts
 *
 * INVARIANT 9: no `text` field. Watch sees only event envelopes.
 * Adding `text` here breaks the boundary and must be rejected in code
 * review. evals/invariant9.test.mjs fails the build if it happens.
 */

/**
 * @typedef {Object} OwnerApproval
 * @property {string} question_id
 * @property {'approve' | 'reject'} decision
 * @property {ApprovalSource} source
 * @property {string} ts
 * @property {string} evidence
 */

/**
 * @typedef {Object} CloseDraftAgreedTerm
 * @property {string} term
 * @property {string} value
 * @property {string} provenance  e.g. 'owner_context' | 'peer_message:<id>' | 'owner_reply:<question_id>' | 'assumption'
 */

/**
 * @typedef {Object} CloseDraftCommitment
 * @property {string} commitment
 * @property {string} provenance  e.g. 'peer_message:<id>'
 */

/**
 * @typedef {Object} CloseDraftOwnerConstraint
 * @property {string} constraint
 * @property {'create' | 'join'} source
 * @property {boolean} requires_owner_approval
 */

/**
 * @typedef {Object} CloseDraftNextStep
 * @property {string} step
 * @property {'host' | 'guest' | 'both'} owner
 */

/**
 * @typedef {Object} CloseDraftUnresolved
 * @property {string} item
 * @property {string} reason
 */

/**
 * @typedef {Object} CloseDraft
 * @property {CloseOutcome} outcome
 * @property {CloseDraftAgreedTerm[]} agreed_terms
 * @property {CloseDraftUnresolved[]} unresolved_items
 * @property {CloseDraftOwnerConstraint[]} owner_constraints
 * @property {CloseDraftCommitment[]} peer_commitments
 * @property {OwnerApproval[]} owner_approvals
 * @property {CloseDraftNextStep[]} next_steps
 * @property {string} owner_summary
 */

/**
 * @typedef {Object} PendingOwnerAsk
 * @property {string} question_id
 * @property {string} question_text
 * @property {string} asked_at
 * @property {string} timeout_at
 * @property {'answered' | 'timeout' | 'no_deal_close'} blocks_until
 * @property {Record<string, unknown>} context_snapshot
 */

/**
 * @typedef {Object} RoomState
 * @property {string} room_id
 * @property {Role} role
 * @property {string} [host_token]
 * @property {string} [guest_token]
 * @property {number} last_event_cursor
 * @property {PendingOwnerAsk | null} pending_owner_ask
 * @property {OwnerApproval[]} owner_approvals
 * @property {CloseDraft | null} draft_close
 * @property {string} started_at
 * @property {string} last_seen_at
 * @property {string} [topic]
 * @property {string} [goal]
 * @property {number} [last_wakeup_event_id] - heartbeat wake-lease: the peer
 *   event id that most recently triggered a wake_agent. Absent ⇒ 0. Owned by
 *   `setWakeLease`; never touched by post/poll/close. Dedupe key so a second
 *   heartbeat for the same event returns noop/wake_inflight instead of
 *   re-waking the agent.
 * @property {string | null} [wakeup_inflight_until] - heartbeat wake-lease:
 *   ISO timestamp until which the most recent wake is considered in-flight.
 *   Absent/null ⇒ no lease. After this time the same event can wake again.
 * @property {{ question_id: string, decision: 'approve' | 'reject', answered_at: string } | null} [owner_answered_wake]
 *   - unattended owner-approval loop wake signal. Set by `resolveOwnerAsk` when
 *   the owner answers (approve OR reject); the heartbeat reads this flag and
 *   wakes the agent to ACT on the answer (the peer is just waiting for our
 *   reply, so no NEW peer event would otherwise wake it). The agent clears it
 *   (clearOwnerAnsweredWake) after its next post/close. Absent/null ⇒ no
 *   pending owner-answer to act on. Backward-compatible: old state files lack
 *   it and an absent field means "no signal".
 */

// ---------- constants ----------

export const MESSAGE_KINDS = Object.freeze(['message', 'close', 'ask_owner', 'owner_reply']);
export const ROLES = Object.freeze(['host', 'guest']);
export const CLOSE_OUTCOMES = Object.freeze(['agreement', 'no_agreement', 'partial']);
export const APPROVAL_SOURCES = Object.freeze(['primary_agent_conversation', 'owner_url', 'telegram_inbound']);

// Relay error classification per reflection-sync agreement.
// Fatal = exit immediately, do not retry. Retriable = exponential backoff.
export const FATAL_RELAY_STATUSES = Object.freeze(new Set([401, 403, 404, 410]));
export const RETRIABLE_RELAY_STATUSES = Object.freeze(new Set([408, 425, 429, 500, 502, 503, 504]));

// Client-side caps. Probed against api.clawroom.cc on 2026-05-11:
// relay's DEFAULT_MAX_TEXT_CHARS = 8000 (worker.ts:72), shared by
// `messages` and `close` kinds (worker.ts:826). Relay env var
// CLAWROOM_MAX_TEXT_CHARS can raise this up to 50_000 server-side.
// We cap client at the production default. If a custom relay raises
// it, the client will still reject conservatively; relay-side check
// is the authority.
export const MAX_MESSAGE_TEXT_CHARS = 8000;
export const MAX_CLOSE_SUMMARY_CHARS = 8000;

export const DEFAULT_RELAY_URL = 'https://api.clawroom.cc';
export const DEFAULT_LONG_POLL_WAIT_SECONDS = 20;
export const DEFAULT_RETRY_ATTEMPTS = 4;
export const DEFAULT_RETRY_BASE_MS = 250;

// Room TTL mirror of the relay's DEFAULT_MAX_THREAD_MS (worker.ts: 72h
// default / 7d ceiling). The relay is the authority — an expired room
// answers 410 thread_expired and the heartbeat treats that as cancel/ttl.
// This client-side value lets `heartbeat` predict expiry from
// state.started_at WITHOUT a relay round-trip when the relay can't be
// reached, and matches the default so the prediction agrees with the relay.
export const DEFAULT_MAX_THREAD_MS = 72 * 60 * 60 * 1000;

// Heartbeat wake-lease TTL. After waking the primary agent for a peer event,
// suppress re-waking for the SAME (or older) event for this long, so a
// scheduler firing every few minutes does not stack duplicate wakes while
// the agent is still working that turn. Overridable per-call via --lease-ttl.
export const DEFAULT_WAKE_LEASE_TTL_SECONDS = 600;

// State file location. Override via CLAWROOM_STATE_DIR for tests.
export const STATE_DIR = process.env.CLAWROOM_STATE_DIR
  || `${process.env.HOME || process.env.USERPROFILE || '/tmp'}/.clawroom-v4`;

// ---------- factories with runtime enforcement ----------

/**
 * Strict WatchEvent factory. Throws on any field other than
 * {id, from, kind, ts}. This is the runtime arm of invariant 9.
 *
 * @param {unknown} raw
 * @returns {Readonly<WatchEvent>}
 */
export function makeWatchEvent(raw) {
  if (!raw || typeof raw !== 'object') {
    throw new Error('makeWatchEvent: input is not an object');
  }
  const obj = /** @type {Record<string, unknown>} */ (raw);
  const allowedKeys = new Set(['id', 'from', 'kind', 'ts']);
  for (const key of Object.keys(obj)) {
    if (!allowedKeys.has(key)) {
      throw new Error(
        `makeWatchEvent: invariant 9 violation — disallowed field "${key}" on event. ` +
        `Watch sees only metadata; any text/body field is a regression.`
      );
    }
  }
  const id = Number(obj.id);
  const from = String(obj.from || '');
  const kind = String(obj.kind || '');
  const ts = Number(obj.ts);
  if (!Number.isFinite(id) || id < 0) throw new Error(`makeWatchEvent: bad id ${obj.id}`);
  if (!ROLES.includes(from)) throw new Error(`makeWatchEvent: bad from "${from}"`);
  if (!MESSAGE_KINDS.includes(kind)) throw new Error(`makeWatchEvent: bad kind "${kind}"`);
  if (!Number.isFinite(ts)) throw new Error(`makeWatchEvent: bad ts ${obj.ts}`);
  return Object.freeze({ id, from, kind, ts });
}

// ---------- heartbeat wake-lease sentinels ----------
//
// The wake-lease fields key a wake on an event id. Real /events ids are always
// >= 0 (makeWatchEvent rejects id < 0; the cursor floor is -1). For wakes that
// are NOT tied to a peer event — an owner-ask timeout, or an owner answering an
// ask — we need STABLE RESERVED keys that (a) never collide with a real event
// id and (b) never collide with EACH OTHER for the same question_id, so the two
// kinds of wake can't alias in the lease. We map a question_id to a negative
// integer in two DISJOINT, strictly-negative ranges:
//   timeout  sentinel ∈ [-(2^31),        -1]
//   answered sentinel ∈ [-(2^32), -(2^31)-1]   (offset by -(2^31)-1)
// Adjacent but non-overlapping: the largest answered sentinel (-(2^31)-1) is
// strictly below the smallest timeout sentinel (-(2^31)). Both bounded well
// inside safe-integer range (|value| < 2^32 ≪ 2^53). Deterministic, so a second
// tick for the SAME still-set condition recomputes the same key and dedupes to
// wake_inflight. Lives here (shared, dep-free) so the CLI and tests use ONE
// definition — no drift between the production formula and what tests assert.

/** djb2-ish 32-bit signed hash of a question_id. @param {string} questionId */
function sentinelHash(questionId) {
  let h = 0;
  const s = String(questionId || '');
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return h;
}

/**
 * Reserved wake-lease key for an owner_ask_timeout wake.
 * @param {string} questionId
 * @returns {number} a negative integer in [-(2^31), -1]
 */
export function ownerAskTimeoutSentinel(questionId) {
  return -1 - ((sentinelHash(questionId) >>> 0) % 0x80000000);
}

/**
 * Reserved wake-lease key for an owner_answered wake (unattended owner-approval
 * loop close). Disjoint from — and strictly more negative than — the timeout
 * sentinel, so the two never alias for the same question_id.
 * @param {string} questionId
 * @returns {number} a negative integer in [-(2^32), -(2^31)-1]
 */
export function ownerAnsweredSentinel(questionId) {
  return -0x80000001 - ((sentinelHash(questionId) >>> 0) % 0x80000000);
}

/**
 * Canonical content hash for idempotency key derivation.
 * Lowercase hex sha256 of UTF-8 bytes.
 *
 * @param {string} value
 * @returns {Promise<string>}
 */
export async function canonicalContentHash(value) {
  const crypto = await import('node:crypto');
  return crypto.createHash('sha256').update(String(value || ''), 'utf8').digest('hex');
}

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

// evals/harness/owner-ask-protocol.mjs
//
// Phase 4.5 OWNER_ASK protocol. When a subagent driving the skill hits
// `clawroom ask-owner` (writes pending_owner_ask to state), it MUST
// emit a structured OWNER_ASK packet to its parent (this session) so
// the parent can deliver a natural-language owner reply.
//
// Crucial contract from planning room t_d8681c69-e79:
//   Subagent runs `owner-reply` ITSELF after parent answers.
//   Parent NEVER writes owner-reply directly — that would fake the
//   state transition and defeat the test.
//
// Transport modes (Phase 4.5 review t_f8ce0096-671 amendment):
//   (a) raw stdout streaming — subagent prints OWNER_ASK::<json> line.
//   (b) cooperative stop/resume — subagent's FINAL message contains the
//       OWNER_ASK packet, parent stops the subagent, hands back, then
//       resumes/restarts the subagent which runs owner-reply itself.
//   Both transports use the same packet shape and the same
//   verifyOwnerAskAgainstState check. Codex side currently uses (b).
//
// State-backed verification (Phase 4.5 review P1/P2): a parent could
// fabricate an OWNER_ASK packet. Phase 5 drivers MUST verify the
// packet against the temp state file before honoring it. Anything
// the subagent didn't actually persist via `clawroom ask-owner` is
// invalid evidence.

/**
 * @typedef {Object} OwnerAskPacket
 * @property {"OWNER_ASK"} type
 * @property {string} question_id - matches state.pending_owner_ask.question_id
 * @property {string} question_text - natural-language question for the owner
 * @property {string} [context] - 1-line owner-facing context (why I'm asking)
 * @property {object} blocking_state
 * @property {string} blocking_state.room_id
 * @property {"host"|"guest"} blocking_state.role
 * @property {string} blocking_state.timeout_at - ISO 8601
 */

/**
 * Single canonical line a subagent prints to stdout when it needs owner input.
 * One JSON object per line, prefixed with `OWNER_ASK::` so it's easy to grep
 * out of mixed transcript noise.
 *
 * Example line in subagent stdout:
 *   OWNER_ASK::{"type":"OWNER_ASK","question_id":"q-budget","question_text":"Peer is asking $720 but my budget ceiling is $650 — do you want to approve exceeding it?","blocking_state":{"room_id":"t_x","role":"host","timeout_at":"2026-05-13T11:30:00Z"}}
 */
export const OWNER_ASK_LINE_PREFIX = 'OWNER_ASK::';

/**
 * Parse OWNER_ASK packets out of a subagent transcript.
 *
 * @param {string} transcript - mixed stdout / stderr text
 * @returns {OwnerAskPacket[]}
 */
export function extractOwnerAsks(transcript) {
  const lines = String(transcript || '').split('\n');
  const packets = [];
  for (const line of lines) {
    const idx = line.indexOf(OWNER_ASK_LINE_PREFIX);
    if (idx < 0) continue;
    const json = line.slice(idx + OWNER_ASK_LINE_PREFIX.length).trim();
    let parsed;
    try {
      parsed = JSON.parse(json);
    } catch (e) {
      // Malformed packet — surface as a placeholder with the raw payload
      // so the parent can decide to fail the run.
      packets.push({
        type: 'OWNER_ASK',
        question_id: '__malformed__',
        question_text: `(malformed OWNER_ASK packet: ${e.message}; raw: ${json.slice(0, 200)})`,
        blocking_state: { room_id: 'unknown', role: 'host', timeout_at: '' },
      });
      continue;
    }
    if (validatePacket(parsed)) packets.push(parsed);
  }
  return packets;
}

/**
 * Strict shape check.
 *
 * @param {unknown} p
 * @returns {p is OwnerAskPacket}
 */
function validatePacket(p) {
  if (!p || typeof p !== 'object') return false;
  const o = /** @type {any} */ (p);
  if (o.type !== 'OWNER_ASK') return false;
  if (typeof o.question_id !== 'string' || !o.question_id) return false;
  if (typeof o.question_text !== 'string' || !o.question_text) return false;
  if (!o.blocking_state || typeof o.blocking_state !== 'object') return false;
  if (typeof o.blocking_state.room_id !== 'string') return false;
  if (o.blocking_state.role !== 'host' && o.blocking_state.role !== 'guest') return false;
  return true;
}

/**
 * Build an OWNER_ASK line a subagent would emit. Provided for symmetry
 * (used by the dry-run smoke test to generate synthetic transcripts).
 *
 * @param {OwnerAskPacket} packet
 * @returns {string}
 */
export function makeOwnerAskLine(packet) {
  return `${OWNER_ASK_LINE_PREFIX}${JSON.stringify(packet)}`;
}

/**
 * Owner-reply hand-back template. The parent (this session) drafts a
 * natural-language reply and a structured decision, returns BOTH to the
 * subagent. The subagent then runs `./cli/clawroom owner-reply ...`
 * itself with the structured fields — the parent does NOT write the
 * state transition.
 *
 * @typedef {Object} OwnerReplyHandback
 * @property {string} question_id
 * @property {"approve"|"reject"} decision
 * @property {string} evidence - owner-facing reasoning, will be persisted in state.owner_approvals[].evidence
 * @property {string} natural_language_reply - what the parent would say back to the subagent in the room conversation
 */

/**
 * @param {OwnerAskPacket} ask
 * @param {Omit<OwnerReplyHandback, 'question_id'>} reply
 * @returns {OwnerReplyHandback}
 */
export function buildHandback(ask, reply) {
  return {
    question_id: ask.question_id,
    decision: reply.decision,
    evidence: reply.evidence,
    natural_language_reply: reply.natural_language_reply,
  };
}

import fs from 'node:fs';
import path from 'node:path';

/**
 * State-backed verification of an OWNER_ASK packet. Phase 4.5 review
 * P1/P2 amendment: parent could fabricate an OWNER_ASK; Phase 5
 * drivers MUST check the packet is real before handing back.
 *
 * Honors the standard state file path:
 *   <state_dir>/<room_id>-<role>.state.json
 *
 * Returns { valid, reasons }. valid=true only if:
 *   - state file exists for room_id + role
 *   - state.pending_owner_ask exists
 *   - state.pending_owner_ask.question_id === packet.question_id
 *   - state.pending_owner_ask.timeout_at === packet.blocking_state.timeout_at
 *     (when packet supplies one; loose match if packet omits it)
 *
 * Drivers should treat invalid packets as a FAILED case-3 leak — the
 * subagent didn't actually persist via `ask-owner`, so the protocol
 * has been bypassed.
 *
 * @param {OwnerAskPacket} packet
 * @param {object} args
 * @param {string} args.state_dir - the CLAWROOM_STATE_DIR used by the subagent
 * @returns {{ valid: boolean, reasons: string[] }}
 */
export function verifyOwnerAskAgainstState(packet, { state_dir }) {
  const reasons = [];
  if (!packet || packet.type !== 'OWNER_ASK') {
    return { valid: false, reasons: ['packet missing or wrong type'] };
  }
  const { room_id, role } = packet.blocking_state || {};
  if (typeof room_id !== 'string' || (role !== 'host' && role !== 'guest')) {
    return { valid: false, reasons: ['packet.blocking_state.{room_id,role} invalid'] };
  }
  const file = path.join(state_dir, `${room_id}-${role}.state.json`);
  if (!fs.existsSync(file)) {
    return { valid: false, reasons: [`state file not found: ${file} — subagent never ran ask-owner`] };
  }
  let state;
  try {
    state = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    return { valid: false, reasons: [`state file unreadable: ${e.message}`] };
  }
  if (!state.pending_owner_ask) {
    reasons.push(`state.pending_owner_ask is null — no active ask to back this packet`);
  } else {
    if (state.pending_owner_ask.question_id !== packet.question_id) {
      reasons.push(
        `question_id mismatch: state="${state.pending_owner_ask.question_id}" packet="${packet.question_id}"`
      );
    }
    const packetTimeout = packet.blocking_state.timeout_at;
    if (packetTimeout && state.pending_owner_ask.timeout_at !== packetTimeout) {
      reasons.push(
        `timeout_at mismatch: state="${state.pending_owner_ask.timeout_at}" packet="${packetTimeout}"`
      );
    }
  }
  return { valid: reasons.length === 0, reasons };
}

// skill/lib/watch.mjs
// Non-LLM watch helper. Consumes ONLY /events (metadata-only). Emits one
// stdout line per event in the form:
//
//   event_available {"id":3,"from":"guest","kind":"message","ts":...}
//   close_available {"id":4,"from":"guest","kind":"close","ts":...}
//   mutual_close
//   error <msg>
//
// Never reads /messages. Never logs `text` or any peer content. Never
// makes any semantic decision. The primary agent reads message bodies
// from /messages explicitly when it sees event_available.
//
// Invariant 9 is enforced in three layers:
//   1. Relay /events shape (no text field returned).
//   2. makeWatchEvent() runtime check (throws if any unknown field appears).
//   3. evals/invariant9.test.mjs (asserts no body bytes ever appear in
//      stdout/stderr/state during a full watch cycle).
//
// Per reflection sync `t_bf866856-df0`, after seeing a peer close event,
// watch polls /join (not /events) until `close_state.closed === true`.

import {
  DEFAULT_LONG_POLL_WAIT_SECONDS,
  makeWatchEvent,
} from './types.mjs';
import { RelayClient } from './relay-client.mjs';
import { readState, setCursor } from './state.mjs';

const MUTUAL_CLOSE_POLL_INTERVAL_MS = 2_000;
const MUTUAL_CLOSE_POLL_MAX_ATTEMPTS = 60; // 2 minutes hard cap

/**
 * @param {Object} args
 * @param {string} args.relay
 * @param {string} args.room_id
 * @param {'host' | 'guest'} args.role
 * @param {string} args.token
 * @param {boolean} [args.include_self] - debug only; default false
 * @param {boolean} [args.once] - Pattern B' driver mode. After a poll iteration where AT
 *   LEAST ONE peer event was emitted, exit. If the poll batch contains multiple events
 *   from the peer, ALL of them are emitted in that iteration before exit — the caller
 *   receives the full batch on stdout, not just the first event. This is the right
 *   behavior for per-turn drivers: process the whole batch as one "turn" of input.
 *   Default false (long-running watcher).
 * @param {(line: string) => void} [args.emit] - default: console.log
 * @param {AbortSignal} [args.signal]
 * @returns {Promise<{ closed_mutually: boolean, last_cursor: number, exit_reason: string }>}
 */
export async function watchEvents({ relay, room_id, role, token, include_self = false, once = false, emit, signal }) {
  const out = emit || ((line) => process.stdout.write(line + '\n'));
  const client = new RelayClient({ relay, room_id, role, token });

  // Resume cursor from state file if available (Codex Q5 / N5).
  let cursor = -1;
  try {
    const state = readState(room_id, role);
    if (state) cursor = state.last_event_cursor;
  } catch {
    // Corrupted state should fail closed per invariant 16, but watch is read-only
    // and gives the caller a chance to recover. We surface a structured error and exit.
    out('error state_unreadable');
    return { closed_mutually: false, last_cursor: -1, exit_reason: 'state_unreadable' };
  }

  let exitReason = 'ok';
  let mutuallyClosed = false;

  while (!signal?.aborted) {
    let result;
    try {
      result = await client.pollEvents({ after: cursor, waitSeconds: DEFAULT_LONG_POLL_WAIT_SECONDS });
    } catch (err) {
      // Fatal errors (401/403/404/410) exit; retriable errors should have been
      // exhausted by relay-client. Emit a structured error then exit per invariant 17:
      // we do not synthesize peer events to mask network failure.
      out(`error relay_${err?.status || 'network'}`);
      exitReason = `relay_error_${err?.status || 'network'}`;
      break;
    }

    const rows = Array.isArray(result.body) ? result.body : [];
    let sawPeerClose = false;
    let emittedThisIter = 0;

    for (const raw of rows) {
      let ev;
      try {
        ev = makeWatchEvent(raw); // INVARIANT 9: throws if relay leaked text or any non-allowed field
      } catch (err) {
        out(`error invariant9_violation`);
        return { closed_mutually: false, last_cursor: cursor, exit_reason: 'invariant9_violation' };
      }
      if (ev.id > cursor) cursor = ev.id;

      // Skip echoes of our own posts unless caller explicitly asked for them.
      if (!include_self && ev.from === role) continue;

      // Emit serialized event metadata. NEVER include text. NEVER add any
      // extra field. makeWatchEvent already trims to {id, from, kind, ts}.
      const tag = ev.kind === 'close' ? 'close_available' : 'event_available';
      out(`${tag} ${JSON.stringify(ev)}`);
      emittedThisIter++;

      if (ev.kind === 'close' && ev.from !== role) sawPeerClose = true;
    }

    // Persist cursor so a future resume starts from the right place.
    try {
      const state = readState(room_id, role);
      if (state && cursor !== state.last_event_cursor) {
        setCursor(state, cursor);
      }
    } catch {
      // Don't bring down watch on state-file write failure; emit notice.
      out('error state_write_failed');
    }

    if (sawPeerClose) {
      mutuallyClosed = await pollUntilMutualClose(client, out);
      if (mutuallyClosed) {
        out('mutual_close');
        exitReason = 'mutual_close';
      } else {
        out('error mutual_close_timeout');
        exitReason = 'mutual_close_timeout';
      }
      break;
    }

    // Pattern B' driver: once we've emitted at least one peer event in this
    // iteration, exit. We deliberately drain the ENTIRE poll batch first
    // (all peer events from this one HTTP response) — splitting a batch
    // across two driver turns would risk the second turn seeing a
    // stale-cursor surprise. The caller's per-turn loop should treat one
    // `--once` invocation as "all peer events queued since my last turn".
    if (once && emittedThisIter > 0) {
      exitReason = 'once_event_emitted';
      break;
    }
  }

  return { closed_mutually: mutuallyClosed, last_cursor: cursor, exit_reason: exitReason };
}

/**
 * After a peer close event, switch from /events long-poll to /join short-poll
 * until close_state.closed === true (or timeout). This is the bug fix from
 * the reflection-sync room — /events keeps long-polling on a closed room
 * and never returns. /join is the authoritative state.
 *
 * @param {RelayClient} client
 * @param {(line: string) => void} emit
 * @returns {Promise<boolean>}
 */
async function pollUntilMutualClose(client, emit) {
  for (let attempt = 0; attempt < MUTUAL_CLOSE_POLL_MAX_ATTEMPTS; attempt++) {
    try {
      const join = await client.join();
      const state = /** @type {any} */ (join.body)?.close_state;
      if (state?.closed === true) return true;
    } catch (err) {
      emit(`error join_${err?.status || 'network'}`);
      // Continue polling on retriable errors; relay-client already retried internally.
    }
    await new Promise((r) => setTimeout(r, MUTUAL_CLOSE_POLL_INTERVAL_MS));
  }
  return false;
}

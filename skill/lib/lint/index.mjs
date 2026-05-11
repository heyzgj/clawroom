// skill/lib/lint/index.mjs
// Deterministic pre-send + pre-close advisory lint (invariant 7 + 13).
// Returns warnings; does NOT block. The primary agent decides whether to
// revise. `clawroom close` is the hard wall (skill/lib/close.mjs); lint is
// the preview.

import { validateCloseDraft, validateCloseAgainstState } from '../close.mjs';

/** @typedef {import('../types.mjs').CloseDraft} CloseDraft */
/** @typedef {import('../types.mjs').RoomState} RoomState */

const SENSITIVE_PATTERNS = [
  /\b[a-fA-F0-9]{32,}\b/g,                       // hex tokens / hashes
  /(host_|guest_)[a-fA-F0-9]{16,}/g,             // explicit clawroom tokens
  /\bsk-[A-Za-z0-9_-]{16,}\b/g,                  // API keys
  /\/Users\/[^\s'"]+/g,                          // macOS file paths
  /\/home\/[^\s'"]+/g,                           // linux file paths
  /\bpid\s*[:=]\s*\d+/gi,                        // PID leakage
];

/** @typedef {{ code: string, severity: 'info' | 'warn' | 'error', message: string, path?: string }} LintFinding */

/**
 * Pre-send lint. Checks: sensitive leakage in outbound text.
 *
 * @param {Object} args
 * @param {string} args.text
 * @param {RoomState} args.state
 * @returns {{ findings: LintFinding[] }}
 */
export function lintBeforeSend({ text, state }) {
  const findings = /** @type {LintFinding[]} */ ([]);
  if (typeof text !== 'string') {
    findings.push({ code: 'send_text_not_string', severity: 'error', message: 'text must be a string' });
    return { findings };
  }
  if (!text.trim()) {
    findings.push({ code: 'send_text_empty', severity: 'error', message: 'text is empty' });
  }
  findings.push(...findSensitiveLeaks(text, 'text'));

  // Invariant 13: posting past a pending_owner_ask mandate boundary is a warn.
  if (state.pending_owner_ask) {
    findings.push({
      code: 'send_with_pending_ask',
      severity: 'warn',
      message: `pending_owner_ask "${state.pending_owner_ask.question_id}" is open; sending may cross mandate boundary`,
    });
  }
  return { findings };
}

/**
 * Pre-close lint. Runs close.mjs validators, marks any issue as error,
 * plus additional advisory checks not enforced by close hard wall:
 *  - owner_summary contains sensitive content (tokens/paths/PIDs)
 *  - very short owner_summary (< 40 chars often unhelpful)
 *  - agreed_terms with provenance='assumption' (allowed but noisy)
 *
 * @param {Object} args
 * @param {CloseDraft} args.draft
 * @param {RoomState} args.state
 * @returns {{ findings: LintFinding[] }}
 */
export function lintBeforeClose({ draft, state }) {
  const findings = /** @type {LintFinding[]} */ ([]);

  const schema = validateCloseDraft(draft);
  for (const it of schema.issues) {
    findings.push({ code: it.code, severity: 'error', message: it.message, path: it.path });
  }
  if (!schema.ok) return { findings };

  const semantic = validateCloseAgainstState(draft, state);
  for (const it of semantic.issues) {
    findings.push({ code: it.code, severity: 'error', message: it.message });
  }

  if (draft.owner_summary) {
    findings.push(...findSensitiveLeaks(draft.owner_summary, 'owner_summary'));
    if (draft.owner_summary.length < 40) {
      findings.push({
        code: 'owner_summary_short',
        severity: 'warn',
        message: `owner_summary is only ${draft.owner_summary.length} chars; usually owners want more context`,
        path: 'owner_summary',
      });
    }
  }

  for (let i = 0; i < (draft.agreed_terms || []).length; i++) {
    const t = draft.agreed_terms[i];
    if (t && t.provenance === 'assumption') {
      findings.push({
        code: 'agreed_term_provenance_assumption',
        severity: 'warn',
        message: `agreed_terms[${i}] uses provenance="assumption"; prefer owner_context / peer_message / owner_reply when available`,
        path: `agreed_terms[${i}].provenance`,
      });
    }
  }

  return { findings };
}

/**
 * @param {string} text
 * @param {string} path
 * @returns {LintFinding[]}
 */
function findSensitiveLeaks(text, path) {
  const out = /** @type {LintFinding[]} */ ([]);
  for (const re of SENSITIVE_PATTERNS) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      out.push({
        code: 'sensitive_leak',
        severity: 'warn',
        message: `possible sensitive content matched ${re} near offset ${m.index}`,
        path,
      });
      if (out.filter((f) => f.code === 'sensitive_leak').length >= 5) return out; // cap noise
    }
  }
  return out;
}

// evals/harness/artifact-writer.mjs
//
// Phase 4.5 artifact writer. Per planning room t_d8681c69-e79 close,
// every Phase 5 run produces an artifact at:
//   docs/progress/v4_p5_case<N>_<timestamp>.json
//
// MANDATORY for BOTH pass and fail (Codex round 2 amendment: passing
// artifacts enable regression compare). No auto-retry on failure; the
// prior failed artifact stays canonical if a retry happens.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), '..', '..');
const ARTIFACT_DIR = path.join(REPO_ROOT, 'docs', 'progress');

/**
 * @typedef {Object} RunArtifact
 * @property {number} case_number - 1, 2, 3, or 4
 * @property {"subagent"|"main"|"mixed"} driver_type - who drove host / guest
 * @property {string[]} owner_prompts - exact prompts the parent gave to subagent(s)
 * @property {object[]} transcript - room events (redacted: no tokens, no chat ids)
 * @property {object} state_snapshot - {host?:..., guest?:...} state files at close
 * @property {object} close_output - validator output (issues etc) from either side
 * @property {"MAGICAL"|"GOOD"|"OK"|"FAILED"} score
 * @property {object} score_detail - reasons, owner-question count, schema errors, etc
 * @property {"product_bug"|"runtime_limitation"|"e2e_flake"|"pass"} classification
 * @property {object} owner_facing_quality - subjective ask quality + owner_summary quality
 * @property {string} allowed_context - typically "temp_skill_copy_only"
 * @property {object[]} [context_leak_check] - violations if any
 * @property {number} retry_index - 0 for first, 1 for first retry, etc
 * @property {string|null} retry_reason - why we retried (or null)
 * @property {string} room_id
 * @property {string} started_at - ISO 8601
 * @property {string} finished_at - ISO 8601
 */

/**
 * Write a run artifact. Returns the absolute path written.
 *
 * @param {RunArtifact} artifact
 * @returns {string}
 */
export function writeRunArtifact(artifact) {
  if (!Number.isInteger(artifact.case_number) || artifact.case_number < 1 || artifact.case_number > 4) {
    throw new Error(`writeRunArtifact: case_number must be 1-4, got ${artifact.case_number}`);
  }
  if (!['subagent', 'main', 'mixed'].includes(artifact.driver_type)) {
    throw new Error(`writeRunArtifact: driver_type must be subagent|main|mixed, got ${artifact.driver_type}`);
  }
  if (!['MAGICAL', 'GOOD', 'OK', 'FAILED'].includes(artifact.score)) {
    throw new Error(`writeRunArtifact: score must be MAGICAL|GOOD|OK|FAILED, got ${artifact.score}`);
  }
  if (!['product_bug', 'runtime_limitation', 'e2e_flake', 'pass'].includes(artifact.classification)) {
    throw new Error(`writeRunArtifact: classification must be product_bug|runtime_limitation|e2e_flake|pass, got ${artifact.classification}`);
  }

  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });

  const stamp = artifact.started_at.replace(/[:.]/g, '-').replace('T', '_').replace('Z', '');
  const filename = `v4_p5_case${artifact.case_number}_${stamp}${
    artifact.retry_index > 0 ? `_retry${artifact.retry_index}` : ''
  }.json`;
  const outPath = path.join(ARTIFACT_DIR, filename);

  // Redact protection: scan for known-shape tokens and replace with placeholder.
  // The redacted-transcript discipline is documented in CLAUDE.md but the
  // harness defends in depth.
  const redacted = redactTokens(artifact);

  fs.writeFileSync(outPath, JSON.stringify(redacted, null, 2));
  return outPath;
}

/**
 * Defensive token redaction. Walks the whole artifact and replaces
 * leak-shaped substrings in EVERY string value (not just JSON-shaped
 * object keys). Phase 4.5 review t_f8ce0096-671 P1: transcript strings
 * contain freeform `host_token: "..."` / `create_key = "..."` patterns
 * that the v1 regex missed.
 *
 * Patterns covered:
 *   - Bearer <token>
 *   - CR-<invite-code> (quoted or bare)
 *   - host_token / guest_token / create_key with `:` or `=` separator,
 *     quoted or bare value
 *   - chat_id / chatId (numeric, telegram-style)
 *
 * @param {RunArtifact} artifact
 * @returns {RunArtifact}
 */
function redactTokens(artifact) {
  return /** @type {RunArtifact} */ (deepRedact(artifact));
}

const SENSITIVE_KEYS = new Set([
  'host_token', 'guest_token', 'create_key', 'createKey',
  'chat_id', 'chatId', 'invite_code', 'inviteCode',
]);

/** Recurse, redact every string AND every value under a sensitive key. */
function deepRedact(v) {
  if (typeof v === 'string') return redactString(v);
  if (Array.isArray(v)) return v.map(deepRedact);
  if (v && typeof v === 'object') {
    const out = {};
    for (const k of Object.keys(v)) {
      if (SENSITIVE_KEYS.has(k) && v[k] != null) {
        // Wipe value regardless of shape (string, number, etc).
        out[k] = 'REDACTED';
      } else {
        out[k] = deepRedact(v[k]);
      }
    }
    return out;
  }
  return v;
}

const TOKEN_KEY_RE = /\b(host_token|guest_token|create[_-]?key)\b\s*[:=]\s*("[^"]+"|'[^']+'|[A-Za-z0-9_.-]+)/g;
const CHAT_ID_RE = /\b(chat[_-]?id|chatId)\s*[:=]\s*(-?\d{4,})/g;
const CR_INVITE_RE = /\bCR-[A-Za-z0-9]{6,}\b/g;
const BEARER_RE = /Bearer\s+[A-Za-z0-9_.-]{10,}/g;
// Phase 4.5 review t_f8ce0096-671 P1 round 2: subagent transcripts often
// echo provider API keys (OpenAI/Anthropic/Codex/Slack/GitHub/xAI). Even
// though ClawRoom doesn't need them itself, the artifact contract is
// "no tokens in persisted output" — extend the regex to cover them.
// Match a recognized prefix + an entropic tail. Whitelist of providers
// rather than a generic-high-entropy heuristic to keep false positives
// low.
const API_KEY_RE = /\b(sk-ant-[A-Za-z0-9_-]{20,}|sk-cp-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9_-]{10,}|xai-[A-Za-z0-9_-]{20,}|gh[ps]_[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_-]{20,})\b/g;

function redactString(s) {
  return s
    .replace(TOKEN_KEY_RE, (_m, key) => `${key}: "REDACTED"`)
    .replace(CHAT_ID_RE, (_m, key) => `${key}: "REDACTED"`)
    .replace(CR_INVITE_RE, 'CR-REDACTED')
    .replace(BEARER_RE, 'Bearer REDACTED')
    .replace(API_KEY_RE, 'API_KEY_REDACTED');
}

/**
 * Convenience: read the previous artifact for a case (if any), so a retry
 * knows what came before. Returns null if none.
 *
 * @param {number} case_number
 * @returns {RunArtifact | null}
 */
export function readMostRecentArtifact(case_number) {
  if (!fs.existsSync(ARTIFACT_DIR)) return null;
  const prefix = `v4_p5_case${case_number}_`;
  const files = fs
    .readdirSync(ARTIFACT_DIR)
    .filter((f) => f.startsWith(prefix) && f.endsWith('.json'))
    .sort();
  if (files.length === 0) return null;
  const last = files[files.length - 1];
  try {
    return JSON.parse(fs.readFileSync(path.join(ARTIFACT_DIR, last), 'utf8'));
  } catch {
    return null;
  }
}

export { ARTIFACT_DIR };

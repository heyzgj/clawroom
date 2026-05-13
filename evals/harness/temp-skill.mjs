// evals/harness/temp-skill.mjs
//
// Phase 4.5 subagent harness — temp skill copy + state isolation.
//
// Per planning room t_d8681c69-e79 close: subagent gets a temp copy
// of `skill/` as cwd and a temp CLAWROOM_STATE_DIR. The isolation is
// cwd + env scoping, NOT OS-level sandbox. Subagent prompt names only
// the owner task + installed skill cwd. Any subagent access to
// docs/evals/legacy/session transcripts invalidates the run.

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), '..', '..');
const SKILL_SRC = path.join(REPO_ROOT, 'skill');

/**
 * @typedef {Object} TempSkillEnv
 * @property {string} skill_dir - temp path with copy of skill/
 * @property {string} state_dir - temp path for CLAWROOM_STATE_DIR
 * @property {string} home - temp HOME (for completeness; some agent runtimes pin to HOME)
 * @property {Record<string,string>} child_env - env vars to pass to subagent shell
 * @property {string} allowed_context - canonical isolation label for artifact records
 * @property {() => void} cleanup - rm -rf the temp tree
 */

/**
 * Build a temp skill payload + state dir + child env.
 *
 * @param {object} [opts]
 * @param {string} [opts.label] - case label for the temp dir suffix
 * @returns {TempSkillEnv}
 */
export function makeTempSkillEnv(opts = {}) {
  const label = (opts.label || 'p5').replace(/[^a-z0-9_-]/gi, '_');
  const stamp = `${label}-${Date.now()}-${process.pid}`;
  const root = fs.mkdtempSync(path.join(os.tmpdir(), `clawroom-harness-${stamp}-`));
  const skill_dir = path.join(root, 'skill');
  const state_dir = path.join(root, 'clawroom-state');
  const home = path.join(root, 'home');

  // Recursive copy of skill/.
  fs.cpSync(SKILL_SRC, skill_dir, { recursive: true });
  fs.mkdirSync(state_dir, { recursive: true });
  fs.mkdirSync(home, { recursive: true });

  // Verify the copy: the executable + key lib files must exist.
  const cli = path.join(skill_dir, 'cli', 'clawroom');
  if (!fs.existsSync(cli)) {
    throw new Error(`makeTempSkillEnv: ${cli} missing after copy`);
  }
  for (const lib of ['types.mjs', 'state.mjs', 'close.mjs', 'relay-client.mjs', 'watch.mjs']) {
    const p = path.join(skill_dir, 'lib', lib);
    if (!fs.existsSync(p)) throw new Error(`makeTempSkillEnv: skill/lib/${lib} missing after copy`);
  }

  const child_env = {
    ...process.env,
    HOME: home,
    CLAWROOM_STATE_DIR: state_dir,
    // Subagent should NOT inherit our session-specific paths.
    CLAUDE_PROJECT_DIR: skill_dir,
  };

  return {
    skill_dir,
    state_dir,
    home,
    child_env,
    allowed_context: 'temp_skill_copy_only',
    cleanup: () => {
      try { fs.rmSync(root, { recursive: true, force: true }); } catch {}
    },
  };
}

/**
 * Assert the subagent did NOT peek at repo paths it should not have.
 * Returns true if isolation held; false (with reasons) if violated.
 *
 * Phase 5 case 3 release-green requires this to return true.
 *
 * @param {object} args
 * @param {string} args.subagent_stdout - full subagent transcript
 * @param {string} args.subagent_stderr - full subagent stderr
 * @returns {{ held: boolean, violations: string[] }}
 */
export function checkContextLeak({ subagent_stdout = '', subagent_stderr = '' }) {
  const haystack = `${subagent_stdout}\n${subagent_stderr}`;
  const FORBIDDEN_PATH_FRAGMENTS = [
    // Repo-root maintainer artifacts
    'docs/LESSONS_LEARNED',
    'docs/decisions/',
    'docs/progress/',
    'docs/v4-',
    'evals/fixtures/',
    'evals/harness/',
    'evals/lib/',
    'legacy/v3-bridge/',
    '.claude/',
    // Build session artifacts
    'CLAUDE.md',
    'MIGRATION.md',
  ];
  const violations = [];
  for (const fragment of FORBIDDEN_PATH_FRAGMENTS) {
    if (haystack.includes(fragment)) {
      violations.push(`subagent referenced "${fragment}" — outside allowed_context=temp_skill_copy_only`);
    }
  }
  return { held: violations.length === 0, violations };
}

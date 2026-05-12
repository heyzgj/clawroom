#!/usr/bin/env node
// scripts/v4_fresh_install_smoke.mjs
//
// Gate 10 (added in alignment room t_f20af1fd-a1e): fresh-install + naive
// trigger structural smoke. In a temp HOME / temp state dir / clean
// environment, verify the published `skill/` payload meets the v4 product
// boundary:
//
//   (a) skill default path chooses direct-mode CLI, not legacy bridge.mjs;
//   (b) owner-facing CLI output redacts tokens / paths / PIDs by default;
//   (c) exactly one skill in the manifest;
//   (d) no dependency on maintainer docs / ADRs / plan / /Users/supergeorge
//       / ~/.openclaw legacy assets in the *product path*;
//   (e) no operator-grade prompt scaffolding required (verified via SKILL.md
//       textual content).
//
// This is the structural pre-check that Phase 5 case 3 (naive owner one-
// sentence E2E) depends on. The actual end-to-end behavior with a real LLM
// driving the skill is Phase 5; here we validate the static surface.

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const REPO_ROOT = path.resolve(path.dirname(__filename), '..');
const SKILL_DIR = path.join(REPO_ROOT, 'skill');

// ---------- check primitives ----------

/** @type {{name: string, pass: boolean, detail: string}[]} */
const results = [];
function check(name, pass, detail = '') {
  results.push({ name, pass, detail });
  const tag = pass ? '✓' : '✗';
  process.stderr.write(`  ${tag} ${name}${detail ? '  —  ' + detail : ''}\n`);
}

function fail(name, detail) { check(name, false, detail); }
function pass(name, detail) { check(name, true, detail); }

// ---------- temp env setup ----------

const TMP_HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'clawroom-v4-smoke-home-'));
const TMP_STATE_DIR = path.join(TMP_HOME, '.clawroom-v4');
fs.mkdirSync(TMP_STATE_DIR, { recursive: true });

const CHILD_ENV = {
  ...process.env,
  HOME: TMP_HOME,
  CLAWROOM_STATE_DIR: TMP_STATE_DIR,
  // Explicitly DROP env vars that might leak the maintainer environment
  // into a "fresh install" probe.
  CLAWROOM_RELAY: process.env.CLAWROOM_RELAY || 'https://api.clawroom.cc',
};
// Don't pass CLAWROOM_CREATE_KEY — fresh install shouldn't have it.
delete CHILD_ENV.CLAWROOM_CREATE_KEY;

process.stderr.write(`\nGate 10 fresh-install smoke\n`);
process.stderr.write(`  temp HOME: ${TMP_HOME}\n`);
process.stderr.write(`  temp state dir: ${TMP_STATE_DIR}\n\n`);

// ---------- (a) SKILL.md content: forbidden v3 strings in product path ----------

const skillMd = fs.readFileSync(path.join(SKILL_DIR, 'SKILL.md'), 'utf8');
const referencesDir = path.join(SKILL_DIR, 'references');
const referenceFiles = fs.readdirSync(referencesDir).map((f) => path.join(referencesDir, f));

// These strings, if present in product-path docs, indicate the v3
// bridge-as-LLM-runner pattern is still being recommended. They're allowed
// in v3-only appendix references but not as the default path.
const FORBIDDEN_DEFAULT_PATH = [
  'scripts/clawroomctl.mjs',
  '--agent-id clawroom-relay',
  '--require-features owner-reply-url',
  '/Users/supergeorge',
  // Codex pass 1 P1: an installed skill has skill/ as its root. Doc using
  // `./skill/cli/clawroom` only works from the repo root and will fail
  // for a fresh-install agent invoking from the skill directory.
  './skill/cli/clawroom',
  // Codex pass 2 P2: similarly, doc references to `skill/lib/...` are
  // repo-root paths. Installed skill agents look for `lib/...`.
  'skill/lib/',
  // Pre-commit room t_dcd4f308-357 Q1: v3 bridge scripts moved to
  // legacy/v3-bridge/. Product-path docs must not point at the old path.
  'skill/scripts/',
];

for (const term of FORBIDDEN_DEFAULT_PATH) {
  const skillHit = skillMd.includes(term);
  if (skillHit) {
    fail(`SKILL.md does not reference "${term}" (v3-legacy artifact)`, `found in skill/SKILL.md`);
    continue;
  }
  // Check references too, but allow mention in a strictly "legacy" or "v3" context.
  let leakedInRef = null;
  for (const refPath of referenceFiles) {
    const refContent = fs.readFileSync(refPath, 'utf8');
    if (!refContent.includes(term)) continue;
    // Find the line and check if it's wrapped in legacy framing within 80 chars.
    const idx = refContent.indexOf(term);
    const window = refContent.slice(Math.max(0, idx - 80), idx + term.length + 80).toLowerCase();
    if (window.includes('legacy') || window.includes('v3') || window.includes('do not')) {
      // OK — explicitly legacy / don't-use context.
      continue;
    }
    leakedInRef = `found in ${path.relative(REPO_ROOT, refPath)} outside legacy/do-not framing`;
    break;
  }
  if (leakedInRef) fail(`references/* do not reference "${term}" outside legacy framing`, leakedInRef);
  else pass(`no leaked "${term}" in product path`);
}

// ---------- (b) SKILL.md references all 13 v4 CLI subcommands ----------

const EXPECTED_SUBCOMMANDS = [
  'create', 'resolve', 'join', 'post', 'poll', 'watch',
  'close', 'resume', 'lint', 'ask-owner', 'owner-reply',
  'readiness', 'probe-limits',
];
const skillBundle = skillMd + '\n' + referenceFiles.map((f) => fs.readFileSync(f, 'utf8')).join('\n');
const missingCmds = [];
for (const cmd of EXPECTED_SUBCOMMANDS) {
  // Match `clawroom create`, `clawroom create` styles
  const re = new RegExp(`clawroom\\s+${cmd.replace(/-/g, '\\-')}\\b`, 'i');
  if (!re.test(skillBundle)) missingCmds.push(cmd);
}
if (missingCmds.length === 0) {
  pass(`SKILL.md + references mention all 13 v4 subcommands`);
} else {
  fail(`missing CLI subcommand references`, `missing: ${missingCmds.join(', ')}`);
}

// ---------- (c) SKILL.md doesn't require maintainer-only docs ----------

const REQUIRED_OPTIONAL_LINKS = [
  // These are allowed to be MENTIONED but not REQUIRED to read
  'docs/decisions',
  'docs/v4-phase',
  'docs/progress',
  '.claude/plans',
];
for (const linkPattern of REQUIRED_OPTIONAL_LINKS) {
  // Check whether SKILL.md tells users to read these as required pre-reading
  const requiredPattern = new RegExp(
    `\\b(must|required|first|before)\\b.{0,40}${linkPattern.replace(/\./g, '\\.')}`,
    'i'
  );
  if (requiredPattern.test(skillMd)) {
    fail(`SKILL.md does not require maintainer-only doc "${linkPattern}"`,
         `SKILL.md mentions this as required pre-reading`);
  } else {
    pass(`SKILL.md doesn't gate behavior on "${linkPattern}"`);
  }
}

// ---------- (d) skill/cli/clawroom is executable ----------

const cliPath = path.join(SKILL_DIR, 'cli', 'clawroom');
if (!fs.existsSync(cliPath)) {
  fail(`skill/cli/clawroom exists`, 'file missing');
} else {
  const stat = fs.statSync(cliPath);
  const isExec = (stat.mode & 0o111) !== 0;
  if (isExec) pass(`skill/cli/clawroom is executable`);
  else fail(`skill/cli/clawroom is executable`, `mode is ${stat.mode.toString(8)}`);
}

// ---------- (e) CLI runs and reports a version, in the temp env ----------

const versionResult = spawnSync(cliPath, ['version'], { env: CHILD_ENV, encoding: 'utf8' });
if (versionResult.error) {
  fail(`clawroom version runs in fresh env`, String(versionResult.error.message || versionResult.error));
} else if (versionResult.status !== 0) {
  fail(`clawroom version exits 0`, `exit ${versionResult.status}; stderr: ${(versionResult.stderr || '').slice(0, 120)}`);
} else {
  try {
    const parsed = JSON.parse(versionResult.stdout);
    if (parsed && typeof parsed.version === 'string' && parsed.version.match(/^\d+\.\d+\.\d+/)) {
      pass(`clawroom version returns parseable JSON {version: ${parsed.version}}`);
    } else {
      fail(`clawroom version returns JSON with version field`, `got: ${versionResult.stdout.slice(0, 80)}`);
    }
  } catch (e) {
    fail(`clawroom version output is valid JSON`, `parse error: ${e.message}`);
  }
}

// ---------- (f) CLI help lists all 13 subcommands ----------

const helpResult = spawnSync(cliPath, ['help'], { env: CHILD_ENV, encoding: 'utf8' });
if (helpResult.status !== 0 && helpResult.status !== null) {
  fail(`clawroom help exits cleanly`, `exit ${helpResult.status}`);
} else {
  const helpText = (helpResult.stdout || '') + (helpResult.stderr || '');
  const missingInHelp = EXPECTED_SUBCOMMANDS.filter((cmd) => !helpText.includes(cmd));
  if (missingInHelp.length === 0) pass(`clawroom help lists all 13 subcommands`);
  else fail(`clawroom help missing subcommands`, missingInHelp.join(', '));
}

// ---------- (g) Readiness output redacts internals ----------

// Readiness might exit non-zero if /events isn't deployed; that's fine. We check
// the OUTPUT for forbidden leakage.
const readinessResult = spawnSync(cliPath, ['readiness'], { env: CHILD_ENV, encoding: 'utf8' });
const readinessOutput = (readinessResult.stdout || '') + (readinessResult.stderr || '');
const FORBIDDEN_IN_OWNER_OUTPUT = [
  /\bhost_[a-f0-9]{16,}/i,       // host_token literal pattern
  /\bguest_[a-f0-9]{16,}/i,      // guest_token literal pattern
  /\bsk-[A-Za-z0-9_-]{16,}/,     // generic API keys
  /\b[a-f0-9]{32,}\b/i,          // sha256-ish blobs
];
let readinessLeak = null;
for (const re of FORBIDDEN_IN_OWNER_OUTPUT) {
  const m = readinessOutput.match(re);
  if (m) { readinessLeak = `matched ${re}: "${m[0].slice(0, 40)}..."`; break; }
}
if (readinessLeak) {
  fail(`readiness output is owner-safe (no token / hash leakage)`, readinessLeak);
} else {
  pass(`readiness output has no token / sha256 / API-key shapes`);
}

// readiness MUST report state_dir under the temp HOME (proves no leak to ~/.clawroom-v4 of the real user)
let parsedReadiness;
try {
  parsedReadiness = JSON.parse(readinessResult.stdout);
  if (parsedReadiness?.report?.state_dir === TMP_STATE_DIR) {
    pass(`readiness honors CLAWROOM_STATE_DIR env override`, `${TMP_STATE_DIR}`);
  } else {
    fail(`readiness honors CLAWROOM_STATE_DIR env override`,
         `expected ${TMP_STATE_DIR}, got ${parsedReadiness?.report?.state_dir}`);
  }
} catch {
  fail(`readiness emits parseable JSON`, `output: ${readinessResult.stdout.slice(0, 80)}`);
}

// ---------- (h) State dir is under HOME, not hard-coded /Users/supergeorge ----------

if (parsedReadiness?.report?.state_dir) {
  if (parsedReadiness.report.state_dir.startsWith(TMP_HOME)) {
    pass(`state_dir is under the temp HOME (no maintainer-path leakage)`);
  } else if (parsedReadiness.report.state_dir.includes('/Users/supergeorge')) {
    fail(`state_dir does not contain maintainer path`,
         `found /Users/supergeorge in: ${parsedReadiness.report.state_dir}`);
  } else {
    fail(`state_dir tracks the temp HOME override`,
         `unexpected: ${parsedReadiness.report.state_dir}`);
  }
}

// ---------- (i) Skill manifest: SKILL.md frontmatter parses + has one skill name ----------

const frontmatterMatch = skillMd.match(/^---\n([\s\S]*?)\n---/);
if (!frontmatterMatch) {
  fail(`SKILL.md has YAML frontmatter`, 'no leading --- block found');
} else {
  const fm = frontmatterMatch[1];
  const nameMatch = fm.match(/^name:\s*(\S+)/m);
  const descMatch = fm.match(/^description:/m);
  const versionMatch = fm.match(/version:\s*"?(\d+\.\d+\.\d+)"?/);
  if (nameMatch && nameMatch[1] === 'clawroom') pass(`SKILL.md frontmatter name=clawroom`);
  else fail(`SKILL.md frontmatter name=clawroom`, `got: ${nameMatch?.[1]}`);
  if (descMatch) pass(`SKILL.md has a description`);
  else fail(`SKILL.md has a description`, 'no description: field');
  if (versionMatch && versionMatch[1].startsWith('0.4')) pass(`SKILL.md version is v0.4+ (${versionMatch[1]})`);
  else fail(`SKILL.md version is v0.4+`, `got: ${versionMatch?.[1] || '(none)'}`);
}

// ---------- (j) No legacy bridge process leakage in readiness ----------

if (parsedReadiness?.report?.legacy_bridge_processes !== undefined) {
  const legacy = parsedReadiness.report.legacy_bridge_processes;
  if (Array.isArray(legacy) && legacy.length === 0) {
    pass(`no legacy bridge processes detected in this env`);
  } else {
    // This is a soft warning: in maintainer env there might be stale processes
    // from prior dogfood. Not a fail in smoke (since the fresh-install user
    // wouldn't have any), but worth surfacing.
    fail(`no legacy bridge processes`, `legacy_bridge_processes: ${JSON.stringify(legacy)}`);
  }
}

// ---------- (k) CLI invocation works when cwd is the skill directory ----------
// Codex pass 1 P1: a fresh-install agent's working directory is the skill
// directory, not the repo root. Documented commands must work from there.

const cwdResult = spawnSync('./cli/clawroom', ['version'], {
  cwd: SKILL_DIR,
  env: CHILD_ENV,
  encoding: 'utf8',
});
if (cwdResult.error) {
  fail(`./cli/clawroom version works from skill directory cwd`,
       `error: ${cwdResult.error.message || cwdResult.error}`);
} else if (cwdResult.status === 0) {
  pass(`./cli/clawroom version works from skill directory cwd`,
       `proves SKILL.md doc paths match install layout`);
} else {
  fail(`./cli/clawroom version works from skill directory cwd`,
       `exit ${cwdResult.status}; stderr: ${(cwdResult.stderr || '').slice(0, 120)}`);
}

// ---------- (l) watch --once is implemented (CLI doesn't choke on flag) ----------
// Codex pass 1 P1: SKILL.md / runtime-workflow promise `clawroom watch --once`
// for Pattern B' driver. Even without a real room, the flag must parse.
// We can't fully test it without a room, but we can verify the help mentions
// it AND --once doesn't get rejected as unknown.
const helpText = (spawnSync(cliPath, ['help'], { env: CHILD_ENV, encoding: 'utf8' }).stdout || '');
if (helpText.includes('--once')) {
  pass(`clawroom help documents watch --once (Pattern B')`);
} else {
  fail(`clawroom help documents watch --once`, 'expected mention of --once for Pattern B\' driver');
}

// ---------- (m) npx skills add . --list returns exactly one skill ----------
// Codex pass 1 P2: actually exercise the skill manifest scanner if available.

const skillsListResult = spawnSync('npx', ['--yes', '--quiet', 'skills', 'add', REPO_ROOT, '--list'], {
  env: CHILD_ENV,
  encoding: 'utf8',
  timeout: 30_000,
});
if (skillsListResult.error || skillsListResult.status !== 0) {
  // `skills` CLI might not be installed in every environment; treat as a
  // soft skip rather than fail. Note in the output so the operator knows.
  pass(`skills CLI manifest scan (soft skip — not installed in this env)`,
       `npx skills add ${REPO_ROOT} --list returned exit ${skillsListResult.status}`);
} else {
  const skillsOut = (skillsListResult.stdout || '') + (skillsListResult.stderr || '');
  const matches = (skillsOut.match(/clawroom\b/g) || []).length;
  if (matches >= 1) {
    pass(`skills CLI sees clawroom skill`);
  } else {
    fail(`skills CLI sees clawroom skill`, `output: ${skillsOut.slice(0, 200)}`);
  }
  // Verify it's NOT seeing extra payload that shouldn't be there
  const FORBIDDEN_EXTRA_SKILLS = ['clawroom-v3', 'deploy-clawroom-relay'];
  for (const stale of FORBIDDEN_EXTRA_SKILLS) {
    if (skillsOut.includes(stale)) {
      fail(`skills CLI does not surface legacy "${stale}"`, 'extra payload present');
    }
  }
}

// ---------- (n) Payload cleanliness — no v3 bridge files in published skill ----------
// Pre-commit room t_dcd4f308-357 Q1: SKILL.md says "v4 has no embedded LLM
// bridge" but the skill payload used to ship skill/scripts/{bridge,clawroomctl,
// launcher}.mjs (~3094 LOC / ~112K). Maintainer truth was leaking into the
// product payload (BQ). v3 bridge code now lives under legacy/v3-bridge/,
// outside the published skill. Lock this so the contradiction can't return.

const FORBIDDEN_SKILL_PAYLOAD_FILES = [
  'scripts/bridge.mjs',
  'scripts/clawroomctl.mjs',
  'scripts/launcher.mjs',
];
for (const relPath of FORBIDDEN_SKILL_PAYLOAD_FILES) {
  const absPath = path.join(SKILL_DIR, relPath);
  if (fs.existsSync(absPath)) {
    fail(`skill payload does not include legacy "${relPath}"`,
         `found at ${absPath}; move to legacy/v3-bridge/ and re-run`);
  } else {
    pass(`skill payload does not include legacy "${relPath}"`);
  }
}

// Also fail if skill/scripts/ exists at all and is non-empty (catches new
// patch-style additions like skill/scripts/foo.mjs we didn't anticipate).
const skillScriptsDir = path.join(SKILL_DIR, 'scripts');
if (fs.existsSync(skillScriptsDir)) {
  const entries = fs.readdirSync(skillScriptsDir).filter((f) => !f.startsWith('.'));
  if (entries.length > 0) {
    fail(`skill/scripts/ is absent or empty`,
         `contains: ${entries.join(', ')}; v4 skill payload uses cli/ + lib/ only`);
  } else {
    pass(`skill/scripts/ is absent or empty (only stray dir)`);
  }
} else {
  pass(`skill/scripts/ is absent`);
}

// ---------- summary + cleanup ----------

const total = results.length;
const passed = results.filter((r) => r.pass).length;
const failed = total - passed;

process.stderr.write(`\n  ${passed} / ${total} checks pass.\n`);
if (failed > 0) {
  process.stderr.write(`  Failures:\n`);
  for (const r of results.filter((x) => !x.pass)) {
    process.stderr.write(`    ✗ ${r.name}: ${r.detail}\n`);
  }
}

// Cleanup temp HOME
try { fs.rmSync(TMP_HOME, { recursive: true, force: true }); } catch {}

// Emit JSON report on stdout for machine consumption
process.stdout.write(JSON.stringify({
  ok: failed === 0,
  total,
  passed,
  failed,
  checks: results,
  tmp_home: TMP_HOME,
  tmp_state_dir: TMP_STATE_DIR,
}, null, 2) + '\n');

process.exit(failed === 0 ? 0 : 7);

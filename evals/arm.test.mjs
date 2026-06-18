// evals/arm.test.mjs
// `clawroom arm` / `clawroom disarm` — a one-command, self-verifying way for the
// AGENT to auto-start continuous monitoring of a room right after create/join,
// so the room self-drives and the owner never relays. macOS launchd only.
//
// SAFETY: these tests exercise REAL launchctl. To avoid leaving any launchd
// cruft on the developer's machine, every test that registers a job ALWAYS runs
// `disarm` in a finally, and a final after() hook sweeps any cc.clawroom.wake.*
// label this file could have created. Every job uses a pid+random-suffixed room
// id so a crashed run can't collide with a real room or another test process.
//
// HOW arm resolves the skill dir: arm derives SKILL_DIR from the CLI's own
// location (two dirs up from cli/clawroom). The repo checkout commonly lives
// under ~/Desktop (TCC-protected), where arm MUST refuse. So to test the
// success path with real launchctl we STAGE a copy of skill/ into a non-TCC
// temp dir (os.tmpdir()) and run that staged CLI — exactly how a real install
// under ~/.agents/skills behaves.

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const darwin = process.platform === 'darwin';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');
const REPO_SKILL = path.join(REPO_ROOT, 'skill');

// ---------- staging: a non-TCC copy of the skill so arm's success path runs ----------

const STAGE_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'clawroom-arm-test-'));
const STAGE_SKILL = path.join(STAGE_ROOT, 'skill');
const STAGE_STATE = path.join(STAGE_ROOT, 'state');
const STAGED_CLI = path.join(STAGE_SKILL, 'cli', 'clawroom');

// A per-file label fingerprint so the sweep only touches OUR jobs.
const LABEL_TAG = `armtest${process.pid}`;

before(() => {
  fs.cpSync(REPO_SKILL, STAGE_SKILL, { recursive: true });
  fs.chmodSync(STAGED_CLI, 0o755);
  fs.chmodSync(path.join(STAGE_SKILL, 'lib', 'wakeup-tick.sh'), 0o755);
  fs.mkdirSync(STAGE_STATE, { recursive: true });
});

// Final safety net: boot out anything we might have created, then nuke the
// stage + any per-room base dirs + plists this file could have produced.
const createdLabels = new Set();
after(() => {
  for (const label of createdLabels) {
    if (darwin) {
      spawnSync('launchctl', ['bootout', `gui/${process.getuid()}/${label}`], { encoding: 'utf8' });
    }
    try { fs.unlinkSync(path.join(os.homedir(), 'Library', 'LaunchAgents', `${label}.plist`)); } catch {}
  }
  // Belt-and-suspenders: sweep any leftover label carrying our file tag.
  if (darwin) {
    const sweepRe = new RegExp(`(cc\\.clawroom\\.wake\\.\\S*${LABEL_TAG}\\S*)`);
    const list = spawnSync('launchctl', ['list'], { encoding: 'utf8' }).stdout || '';
    for (const line of list.split('\n')) {
      const m = line.match(sweepRe);
      if (m) spawnSync('launchctl', ['bootout', `gui/${process.getuid()}/${m[1]}`], { encoding: 'utf8' });
    }
  }
  try { fs.rmSync(STAGE_ROOT, { recursive: true, force: true }); } catch {}
});

// ---------- helpers ----------

/** A mock relay that returns no new /events (so heartbeat self-verify → noop). */
function startMockRelay() {
  const server = http.createServer((req, res) => {
    const { pathname } = new URL(req.url, 'http://x');
    if (!req.headers['authorization']?.startsWith('Bearer ')) { res.writeHead(401); res.end('{}'); return; }
    if (/\/events$/.test(pathname)) { res.writeHead(200, { 'content-type': 'application/json' }); res.end('[]'); return; }
    if (/\/join$/.test(pathname)) { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ close_state: null })); return; }
    res.writeHead(404); res.end('{}');
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({ server, url: `http://127.0.0.1:${server.address().port}` });
    });
  });
}

/** A mock relay whose /events ALWAYS errors 500 → heartbeat exits non-zero. */
function startBrokenRelay() {
  const server = http.createServer((req, res) => {
    if (!req.headers['authorization']?.startsWith('Bearer ')) { res.writeHead(401); res.end('{}'); return; }
    res.writeHead(500, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: 'boom' }));
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({ server, url: `http://127.0.0.1:${server.address().port}` }));
  });
}

function runStaged(args, env = {}) {
  return new Promise((resolve) => {
    const child = spawn(STAGED_CLI, args, {
      cwd: STAGE_SKILL,
      env: { ...process.env, CLAWROOM_STATE_DIR: STAGE_STATE, ...env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '', stderr = '';
    child.stdout.on('data', (d) => (stdout += d));
    child.stderr.on('data', (d) => (stderr += d));
    child.on('exit', (code) => resolve({ code, stdout, stderr }));
  });
}

/**
 * Seed a room state file directly in the stage state dir, pinned to a relay.
 * We write the JSON file by hand (matching initState's schema) rather than
 * importing the staged module — this sidesteps ESM module-caching of the
 * STATE_DIR constant and keeps the seed independent of arm's own resolution.
 */
function seedRoom(room, relay) {
  fs.mkdirSync(STAGE_STATE, { recursive: true });
  const now = new Date().toISOString();
  const state = {
    room_id: room,
    role: 'host',
    last_event_cursor: -1,
    pending_owner_ask: null,
    owner_approvals: [],
    draft_close: null,
    started_at: now,
    last_seen_at: now,
    last_wakeup_event_id: 0,
    wakeup_inflight_until: null,
    relay,
    host_token: `host_${room}_tok`,
  };
  fs.writeFileSync(path.join(STAGE_STATE, `${room}-host.state.json`), JSON.stringify(state, null, 2), { mode: 0o600 });
}

function roomId(name) {
  return `t_${LABEL_TAG}_${name}`;
}

function labelFor(room, role = 'host') {
  return `cc.clawroom.wake.${room}-${role}`;
}

function launchdLoaded(label) {
  if (!darwin) return false;
  return spawnSync('launchctl', ['list', label], { encoding: 'utf8' }).status === 0;
}

function plistFor(label) {
  return path.join(os.homedir(), 'Library', 'LaunchAgents', `${label}.plist`);
}

function baseDirFor(room, role = 'host') {
  return path.join(os.homedir(), '.clawroom', `${room}-${role}`);
}

// ---------- tests ----------

test('arm registers a launchd job, writes per-room files, self-verifies, JSON has no token', { skip: !darwin ? 'macOS launchd only' : false }, async () => {
  const room = roomId('register');
  const label = labelFor(room);
  createdLabels.add(label);
  const { server, url } = await startMockRelay();
  seedRoom(room, url);
  try {
    const r = await runStaged(['arm', '--room', room, '--role', 'host', '--interval', '3600'], { CLAWROOM_RELAY: url });
    assert.equal(r.code, 0, `arm should succeed; stderr=${r.stderr}`);
    const j = JSON.parse(r.stdout);
    assert.equal(j.ok, true);
    assert.equal(j.armed, true);
    assert.equal(j.label, label);
    assert.equal(j.interval, 3600);
    assert.equal(j.room, room);
    assert.equal(j.role, 'host');
    assert.equal(typeof j.wake_log, 'string');

    // No token field anywhere in the arm JSON.
    const blob = JSON.stringify(j);
    assert.ok(!/host_token|guest_token|_tok|Bearer/.test(blob), 'arm JSON must not carry tokens');
    // Exact key set — no surprise fields that could leak internals.
    assert.deepEqual(Object.keys(j).sort(), ['armed', 'interval', 'label', 'ok', 'role', 'room', 'wake_log']);

    // The job is actually loaded.
    assert.ok(launchdLoaded(label), 'launchctl list must show the armed label');

    // Per-room files exist with the validated shape.
    const base = baseDirFor(room);
    const prompt = fs.readFileSync(path.join(base, 'wake-prompt.txt'), 'utf8');
    assert.ok(/ask-owner to RECORD it in state FIRST/.test(prompt), 'wake prompt carries the ask-owner-first rule');
    // Cold pickup: the woken agent has NO session to resume, so the prompt must
    // carry the room id + role and tell it to reconstruct context from state +
    // full room history before acting.
    assert.ok(prompt.includes(room), 'wake prompt carries the room id (cold pickup)');
    assert.ok(/clawroom resume --room/.test(prompt), 'wake prompt instructs resume');
    assert.ok(/poll .*--after -1 --no-state/.test(prompt), 'wake prompt instructs full-history poll (--after -1 --no-state)');
    const agent = fs.readFileSync(path.join(base, 'wake-agent.sh'), 'utf8');
    assert.ok(/claude -p "\$\(cat "\$\{CLAWROOM_WAKE_PROMPT_FILE/.test(agent), 'wake-agent reads the prompt from a FILE (no inline quoting)');
    assert.ok(!/--continue/.test(agent), 'cold pickup: wake-agent must NOT use --continue (session resume is cwd-fragile + session-bound)');
    // No `eval` COMMAND (match at a command position, not the word in a comment).
    assert.ok(!/(^|\n|;|&&|\|\|)\s*eval\b/.test(agent), 'no eval command in wake-agent.sh');

    // Plist points at the CANONICAL bundled tick + has node on PATH + no token.
    const plistXml = fs.readFileSync(plistFor(label), 'utf8');
    assert.ok(plistXml.includes(path.join(STAGE_SKILL, 'lib', 'wakeup-tick.sh')), 'plist ProgramArguments points at the bundled tick');
    assert.ok(plistXml.includes(path.dirname(process.execPath)), 'plist PATH includes the node dir');
    assert.ok(!/host_.*_tok|Bearer/.test(plistXml), 'plist carries no token');
    assert.ok(!/CLAWROOM_AGENT_CWD<\/key>\s*<string>[^<]*\/work</.test(plistXml), 'cold pickup: wake-agent cwd is the skill dir, not an empty work subdir');
  } finally {
    await runStaged(['disarm', '--room', room, '--role', 'host']);
    server.close();
  }
});

test('arm is idempotent — arming twice leaves exactly one job', { skip: !darwin ? 'macOS launchd only' : false }, async () => {
  const room = roomId('idem');
  const label = labelFor(room);
  createdLabels.add(label);
  const { server, url } = await startMockRelay();
  seedRoom(room, url);
  try {
    const a = await runStaged(['arm', '--room', room, '--role', 'host', '--interval', '3600'], { CLAWROOM_RELAY: url });
    assert.equal(a.code, 0, `first arm; stderr=${a.stderr}`);
    const b = await runStaged(['arm', '--room', room, '--role', 'host', '--interval', '3600'], { CLAWROOM_RELAY: url });
    assert.equal(b.code, 0, `second arm; stderr=${b.stderr}`);
    assert.ok(launchdLoaded(label), 'still loaded after re-arm');
    // Exactly one entry for the label.
    const list = spawnSync('launchctl', ['list'], { encoding: 'utf8' }).stdout || '';
    const count = list.split('\n').filter((l) => l.includes(label)).length;
    assert.equal(count, 1, `exactly one launchd entry for the label, got ${count}`);
  } finally {
    await runStaged(['disarm', '--room', room, '--role', 'host']);
    server.close();
  }
});

test('disarm removes the job AND the per-room dir, and is idempotent', { skip: !darwin ? 'macOS launchd only' : false }, async () => {
  const room = roomId('disarm');
  const label = labelFor(room);
  createdLabels.add(label);
  const { server, url } = await startMockRelay();
  seedRoom(room, url);
  try {
    const a = await runStaged(['arm', '--room', room, '--role', 'host', '--interval', '3600'], { CLAWROOM_RELAY: url });
    assert.equal(a.code, 0, `arm; stderr=${a.stderr}`);
    assert.ok(launchdLoaded(label));
    assert.ok(fs.existsSync(baseDirFor(room)), 'per-room base exists after arm');

    const d = await runStaged(['disarm', '--room', room, '--role', 'host']);
    assert.equal(d.code, 0, `disarm; stderr=${d.stderr}`);
    assert.equal(JSON.parse(d.stdout).disarmed, true);
    assert.ok(!launchdLoaded(label), 'job gone after disarm');
    assert.ok(!fs.existsSync(baseDirFor(room)), 'per-room base removed after disarm');
    assert.ok(!fs.existsSync(plistFor(label)), 'plist removed after disarm');

    // Idempotent: disarming again must not error.
    const d2 = await runStaged(['disarm', '--room', room, '--role', 'host']);
    assert.equal(d2.code, 0, 'second disarm is a no-op, not an error');
    assert.equal(JSON.parse(d2.stdout).disarmed, true);
  } finally {
    await runStaged(['disarm', '--room', room, '--role', 'host']);
    server.close();
  }
});

test('self-verify fails loud and leaves NO job when the heartbeat tick errors', { skip: !darwin ? 'macOS launchd only' : false }, async () => {
  const room = roomId('selfverifyfail');
  const label = labelFor(room);
  createdLabels.add(label);
  const { server, url } = await startBrokenRelay();
  seedRoom(room, url);
  try {
    const r = await runStaged(['arm', '--room', room, '--role', 'host', '--interval', '3600'], { CLAWROOM_RELAY: url });
    assert.notEqual(r.code, 0, `arm must fail when the heartbeat self-verify tick errors; stdout=${r.stdout}`);
    assert.ok(/self-verify failed/.test(r.stderr), 'error explains the self-verify failure');
    // The half-armed job must have been booted out — nothing left loaded.
    assert.ok(!launchdLoaded(label), 'a failed self-verify must leave NO loaded job');
    assert.ok(!fs.existsSync(plistFor(label)), 'a failed self-verify must leave NO plist');
  } finally {
    await runStaged(['disarm', '--room', room, '--role', 'host']);
    server.close();
  }
});

test('arm fails loud when the room state is missing (arm runs AFTER create/join)', async () => {
  const room = roomId('nostate'); // never seeded
  const { server, url } = await startMockRelay();
  try {
    const r = await runStaged(['arm', '--room', room, '--role', 'host', '--interval', '3600'], { CLAWROOM_RELAY: url });
    assert.notEqual(r.code, 0, 'missing room state is a loud failure');
    assert.ok(/no state for/.test(r.stderr), 'error names the missing state');
    if (darwin) assert.ok(!launchdLoaded(labelFor(room)), 'no job registered when state is missing');
  } finally {
    server.close();
  }
});

test('TCC relocate: arming from a skill under ~/Desktop relocates out + arms durably (no degrade)', async () => {
  // Run the REPO checkout's CLI (it lives under ~/Desktop in this dev env). arm
  // derives SKILL_DIR from the CLI's own path, so this exercises the real TCC
  // path. NEW behavior: instead of refusing (which silently degraded users to a
  // session-bound fallback), arm RELOCATES a copy of the skill to a non-Desktop
  // runtime dir and launchd's from there. Assert: success, a real job, and the
  // wake runs from ~/.clawroom/skill-runtime — never under ~/Desktop.
  const home = os.homedir();
  const underTcc =
    REPO_SKILL === path.join(home, 'Desktop') ||
    REPO_SKILL.startsWith(path.join(home, 'Desktop') + path.sep) ||
    REPO_SKILL.startsWith(path.join(home, 'Documents') + path.sep) ||
    REPO_SKILL.startsWith(path.join(home, 'Downloads') + path.sep);
  if (!underTcc) return; // can't exercise the relocate against this checkout
  const room = roomId('tcc');
  const label = labelFor(room);
  createdLabels.add(label);
  const tccStateDir = path.join(STAGE_ROOT, 'tcc-state');
  fs.mkdirSync(tccStateDir, { recursive: true });
  const { server, url } = await startMockRelay();
  const now = new Date().toISOString();
  // relay:url in state so arm's self-verify heartbeat hits the mock (returns []),
  // not the real relay (the fake room would 404 + retry for minutes).
  fs.writeFileSync(
    path.join(tccStateDir, `${room}-host.state.json`),
    JSON.stringify({
      room_id: room, role: 'host', relay: url, last_event_cursor: -1, pending_owner_ask: null,
      owner_approvals: [], draft_close: null, started_at: now, last_seen_at: now,
      last_wakeup_event_id: 0, wakeup_inflight_until: null, host_token: `host_${room}_tok`,
    }, null, 2),
    { mode: 0o600 }
  );

  const repoCli = path.join(REPO_SKILL, 'cli', 'clawroom');
  const armEnv = { ...process.env, CLAWROOM_STATE_DIR: tccStateDir, CLAWROOM_RELAY: url };
  // Async spawn (NOT spawnSync) so the in-process mock relay keeps answering
  // while arm's self-verify heartbeat polls it — spawnSync blocks this process's
  // event loop, so the mock could never respond and the heartbeat would hang.
  const runRepo = (args) => new Promise((resolve) => {
    const child = spawn(repoCli, args, { cwd: REPO_SKILL, env: armEnv, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', (d) => (stdout += d));
    child.stderr.on('data', (d) => (stderr += d));
    child.on('exit', (code) => resolve({ code, stdout, stderr }));
  });
  const r = await runRepo(['arm', '--room', room, '--role', 'host']);
  try {
    assert.equal(r.code, 0, 'arm under a TCC dir must RELOCATE + succeed, not refuse: ' + r.stderr);
    const j = JSON.parse(r.stdout);
    assert.equal(j.armed, true, 'armed:true after relocate');
    if (darwin) assert.ok(launchdLoaded(label), 'a real launchd job is registered after relocate');
    const runtime = path.join(home, '.clawroom', 'skill-runtime');
    assert.ok(fs.existsSync(path.join(runtime, 'cli', 'clawroom')), 'skill relocated to ~/.clawroom/skill-runtime');
    assert.ok(fs.existsSync(path.join(runtime, 'lib', 'wakeup-tick.sh')), 'relocated copy carries the tick');
    if (darwin) {
      const plistXml = fs.readFileSync(plistFor(label), 'utf8');
      assert.ok(plistXml.includes(runtime), 'plist runs the wake from the relocated non-Desktop copy');
      assert.ok(!/CLAWROOM_AGENT_CWD<\/key>\s*<string>[^<]*\/Desktop\//.test(plistXml), 'wake cwd is NOT under ~/Desktop');
    }
  } finally {
    await runRepo(['disarm', '--room', room, '--role', 'host']);
    server.close();
  }
});

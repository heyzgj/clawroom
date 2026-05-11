// evals/poll-cursor.test.mjs
// Codex pass 2 P2: cmdPoll must update state.last_event_cursor to the max id
// returned, so subsequent post / resume / watch see the correct parent.
// Exercises the CLI as a subprocess against a mock relay so the test reflects
// real behavior, not just lib internals.

process.env.CLAWROOM_STATE_DIR = `/tmp/clawroom-v4-poll-cursor-test-${process.pid}`;

import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CLI = path.resolve(__dirname, '../skill/cli/clawroom');

const { initState } = await import('../skill/lib/state.mjs');
const { STATE_DIR } = await import('../skill/lib/types.mjs');

const ROOM = `t_pollcursor_${process.pid}`;
const TOKEN = 'host_pollcursor_token';

function startMockRelay(messages) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const auth = req.headers['authorization'] || '';
    if (!auth.startsWith('Bearer ')) { res.writeHead(401); res.end('{}'); return; }
    if (/\/messages$/.test(url.pathname)) {
      const after = Number(url.searchParams.get('after') || '-1');
      const due = messages.filter((m) => m.id > after);
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(due));
      return;
    }
    res.writeHead(404); res.end('{}');
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const port = /** @type {any} */ (server.address()).port;
      resolve({ server, url: `http://127.0.0.1:${port}` });
    });
  });
}

function runCli(args, env = {}) {
  return new Promise((resolve) => {
    const child = spawn(CLI, args, {
      env: { ...process.env, ...env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '', stderr = '';
    child.stdout.on('data', (d) => stdout += d);
    child.stderr.on('data', (d) => stderr += d);
    child.on('exit', (code) => resolve({ code, stdout, stderr }));
  });
}

test('cmdPoll updates state.last_event_cursor after a successful poll', async () => {
  const room = `${ROOM}_default`;
  fs.mkdirSync(STATE_DIR, { recursive: true });
  // Clear any leftover state from prior runs.
  try { fs.unlinkSync(path.join(STATE_DIR, `${room}-host.state.json`)); } catch {}
  initState({ room_id: room, role: 'host', host_token: TOKEN });

  const { server, url } = await startMockRelay([
    { id: 0, from: 'host', kind: 'message', ts: 1, text: 'opening' },
    { id: 1, from: 'guest', kind: 'message', ts: 2, text: 'reply' },
    { id: 2, from: 'host', kind: 'message', ts: 3, text: 'follow-up' },
  ]);

  try {
    const result = await runCli(
      ['poll', '--room', room, '--role', 'host'],
      { CLAWROOM_RELAY: url }
    );
    assert.equal(result.code, 0, `poll should succeed; stderr=${result.stderr}`);
    const raw = JSON.parse(fs.readFileSync(path.join(STATE_DIR, `${room}-host.state.json`), 'utf8'));
    assert.equal(raw.last_event_cursor, 2,
      `expected cursor=2 after poll returning id 0..2, got ${raw.last_event_cursor}`);
  } finally {
    server.close();
  }
});

test('cmdPoll --no-state does NOT update cursor', async () => {
  const room = `${ROOM}_nostate`;
  try { fs.unlinkSync(path.join(STATE_DIR, `${room}-host.state.json`)); } catch {}
  initState({ room_id: room, role: 'host', host_token: TOKEN });

  const { server, url } = await startMockRelay([
    { id: 5, from: 'guest', kind: 'message', ts: 1, text: 'msg' },
  ]);

  try {
    const result = await runCli(
      ['poll', '--room', room, '--role', 'host', '--no-state'],
      { CLAWROOM_RELAY: url }
    );
    assert.equal(result.code, 0, `poll should succeed; stderr=${result.stderr}`);
    const raw = JSON.parse(fs.readFileSync(path.join(STATE_DIR, `${room}-host.state.json`), 'utf8'));
    assert.equal(raw.last_event_cursor, -1,
      `with --no-state the cursor should remain at the initial -1, got ${raw.last_event_cursor}`);
  } finally {
    server.close();
  }
});

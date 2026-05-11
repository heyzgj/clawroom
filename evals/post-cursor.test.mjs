// evals/post-cursor.test.mjs
// Codex pass 3 P2: cmdPost must advance state.last_event_cursor to the
// returned message id, so a subsequent post sees the right parent_id
// (idempotency key safety) and resume points to the right place.

process.env.CLAWROOM_STATE_DIR = `/tmp/clawroom-v4-post-cursor-test-${process.pid}`;

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

const TOKEN = 'host_postcursor_token';

function startMockRelay(nextId) {
  let counter = nextId;
  const server = http.createServer(async (req, res) => {
    if (!req.headers['authorization']?.startsWith('Bearer ')) {
      res.writeHead(401); res.end('{}'); return;
    }
    if (/\/messages$/.test(new URL(req.url, 'http://x').pathname) && req.method === 'POST') {
      let body = '';
      for await (const c of req) body += c;
      const id = counter++;
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ id, from: 'host', kind: 'message', ts: Date.now(), text: 'echo' }));
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

test('cmdPost advances last_event_cursor to the returned id', async () => {
  const room = `t_postcursor_${process.pid}`;
  try { fs.unlinkSync(path.join(STATE_DIR, `${room}-host.state.json`)); } catch {}
  initState({ room_id: room, role: 'host', host_token: TOKEN });

  const { server, url } = await startMockRelay(7);
  try {
    const first = await runCli(
      ['post', '--room', room, '--role', 'host', '--text', 'hello'],
      { CLAWROOM_RELAY: url }
    );
    assert.equal(first.code, 0, `first post should succeed; stderr=${first.stderr}`);
    const stateAfterFirst = JSON.parse(fs.readFileSync(path.join(STATE_DIR, `${room}-host.state.json`), 'utf8'));
    assert.equal(stateAfterFirst.last_event_cursor, 7,
      `expected cursor=7 after post returning id=7, got ${stateAfterFirst.last_event_cursor}`);

    // A second post should use the new cursor as parent_id, so its
    // idempotency key is distinct from the first even if the text were
    // identical.
    const second = await runCli(
      ['post', '--room', room, '--role', 'host', '--text', 'world'],
      { CLAWROOM_RELAY: url }
    );
    assert.equal(second.code, 0, `second post should succeed; stderr=${second.stderr}`);
    const stateAfterSecond = JSON.parse(fs.readFileSync(path.join(STATE_DIR, `${room}-host.state.json`), 'utf8'));
    assert.equal(stateAfterSecond.last_event_cursor, 8, 'cursor should advance again');
  } finally {
    server.close();
  }
});

// evals/invariant17.test.mjs
// Codex pass 3 P2: lock down invariant 17 (role custody non-transferable).
// initState must reject any attempt to persist the peer's token. Without this
// regression test, a future refactor to cmdCreate / cmdJoin could quietly
// reintroduce the bypass we fixed in pass 2.

process.env.CLAWROOM_STATE_DIR = `/tmp/clawroom-v4-invariant17-test-${process.pid}`;

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const { initState, readState } = await import('../skill/lib/state.mjs');
const { STATE_DIR } = await import('../skill/lib/types.mjs');

function cleanState(room_id, role) {
  try { fs.unlinkSync(path.join(STATE_DIR, `${room_id}-${role}.state.json`)); } catch {}
}

test('invariant 17: host state rejects guest_token at write time', () => {
  const room_id = `t_inv17_host_${process.pid}`;
  cleanState(room_id, 'host');
  assert.throws(
    () => initState({ room_id, role: 'host', host_token: 'host_x', guest_token: 'guest_x' }),
    /role custody non-transferable/,
    'expected initState to reject host+guest_token combo'
  );
  // Verify NO file was written.
  assert.equal(fs.existsSync(path.join(STATE_DIR, `${room_id}-host.state.json`)), false,
    'failed init must not leave a partial state file');
});

test('invariant 17: guest state rejects host_token at write time', () => {
  const room_id = `t_inv17_guest_${process.pid}`;
  cleanState(room_id, 'guest');
  assert.throws(
    () => initState({ room_id, role: 'guest', guest_token: 'guest_x', host_token: 'host_x' }),
    /role custody non-transferable/,
  );
  assert.equal(fs.existsSync(path.join(STATE_DIR, `${room_id}-guest.state.json`)), false);
});

test('invariant 17: own role without own token rejected', () => {
  const room_id = `t_inv17_owntoken_${process.pid}`;
  cleanState(room_id, 'host');
  assert.throws(
    () => initState({ room_id, role: 'host' }),
    /requires host_token/,
  );
  cleanState(room_id, 'guest');
  assert.throws(
    () => initState({ room_id, role: 'guest' }),
    /requires guest_token/,
  );
});

test('invariant 17: legitimate init succeeds and state file has only own token', () => {
  const room_id = `t_inv17_ok_${process.pid}`;
  cleanState(room_id, 'host');
  initState({ room_id, role: 'host', host_token: 'host_real' });
  const raw = JSON.parse(fs.readFileSync(path.join(STATE_DIR, `${room_id}-host.state.json`), 'utf8'));
  assert.equal(raw.host_token, 'host_real');
  assert.equal(raw.guest_token, undefined, 'host state must not have a guest_token field');
});

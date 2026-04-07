#!/usr/bin/env python3
"""Minimal OpenClaw Gateway WebSocket client for room poller LLM calls.

Bypasses `openclaw agent` CLI to avoid concurrent session contamination.
Connects directly to the Gateway WS control plane (protocol v3).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


def _detect_platform() -> str:
    """Detect platform to match what OpenClaw gateway pinned during device pairing."""
    import sys
    return "darwin" if sys.platform == "darwin" else "linux"


def _read_config() -> dict[str, Any]:
    """Read OpenClaw config to find gateway URL and auth."""
    config_path = os.environ.get("OPENCLAW_CONFIG_PATH", "")
    if not config_path:
        config_path = str(Path.home() / ".openclaw" / "openclaw.json")
    try:
        return json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_gateway_url() -> str:
    env = os.environ.get("OPENCLAW_GATEWAY_URL", "").strip()
    if env:
        return env
    config = _read_config()
    remote_url = (config.get("gateway") or {}).get("remote", {}).get("url", "").strip()
    if remote_url:
        return remote_url
    return "ws://127.0.0.1:18789"


def _read_device_auth() -> dict[str, Any]:
    """Read device-auth.json for pre-authenticated operator token."""
    path = Path.home() / ".openclaw" / "identity" / "device-auth.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_device_identity() -> dict[str, Any]:
    """Read device.json for device ID, public key, and private key."""
    path = Path.home() / ".openclaw" / "identity" / "device.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sign_device_connect(
    private_key_pem: str,
    device_id: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    token: str,
    nonce: str,
    platform: str = "",
    device_family: str = "",
) -> tuple[str, int]:
    """Build v3 canonical payload and sign with Ed25519. Returns (sig_base64url, signed_at_ms)."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(private_key_pem.encode(), password=None)
    signed_at_ms = int(time.time() * 1000)
    scopes_csv = ",".join(scopes)
    payload = "|".join([
        "v3", device_id, client_id, client_mode, role, scopes_csv,
        str(signed_at_ms), token, nonce, platform, device_family,
    ])
    signature = key.sign(payload.encode("utf-8"))  # type: ignore[union-attr]
    sig_b64url = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return sig_b64url, signed_at_ms


def _resolve_auth() -> tuple[dict[str, str], str, list[str]]:
    """Returns (auth_dict, role, scopes) using device-auth operator token."""
    # Try device-auth first (pre-authenticated, has proper scopes)
    device_auth = _read_device_auth()
    tokens = device_auth.get("tokens", {})
    operator = tokens.get("operator", {})
    if operator.get("token"):
        return (
            {"token": operator["token"]},
            operator.get("role", "operator"),
            operator.get("scopes", ["operator.read", "operator.write"]),
        )

    # Fallback to env/config
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
    password = os.environ.get("OPENCLAW_GATEWAY_PASSWORD", "").strip()
    if token or password:
        auth: dict[str, str] = {}
        if token:
            auth["token"] = token
        if password:
            auth["password"] = password
        return auth, "operator", ["operator.read", "operator.write"]

    config = _read_config()
    gw = config.get("gateway") or {}
    auth_block = gw.get("auth") or {}
    t = str(auth_block.get("token") or "").strip()
    return ({"token": t} if t else {}, "operator", ["operator.read", "operator.write"])


_THINKING_MAP = {
    "concise": "low", "verbose": "high", "none": "off",
    "off": "off", "minimal": "minimal", "low": "low",
    "medium": "medium", "high": "high", "adaptive": "adaptive",
}


async def _ws_agent_call(
    message: str,
    session_key: str = "agent:main:main",
    timeout_seconds: int = 90,
    agent_id: str = "main",
    thinking: str = "low",
    deliver: bool = False,
) -> dict[str, Any]:
    """Connect to Gateway WS, send an agent request, wait for final response, disconnect."""
    try:
        import websockets  # type: ignore
    except ImportError:
        raise RuntimeError("websockets package required: pip install websockets")

    url = _resolve_gateway_url()
    auth, role, scopes = _resolve_auth()
    device = _read_device_identity()

    async with websockets.connect(url, max_size=25 * 1024 * 1024, open_timeout=10) as ws:
        # 1. Build connect params
        instance_id = str(uuid.uuid4())

        client_id = "gateway-client"
        client_mode = "backend"
        auth_token = auth.get("token", "")

        def _build_connect_params(nonce: str | None = None) -> dict[str, Any]:
            params: dict[str, Any] = {
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": client_id,
                    "version": "dev",
                    "platform": _detect_platform(),
                    "mode": client_mode,
                    "instanceId": instance_id,
                },
                "role": role,
                "scopes": scopes,
                "caps": [],
            }
            if auth:
                params["auth"] = auth
            # Add signed device identity using v3 canonical payload
            private_key = device.get("privateKeyPem", "")
            device_id = device.get("deviceId", "")
            if device_id and private_key:
                actual_nonce = nonce or str(uuid.uuid4())
                sig, signed_at_ms = _sign_device_connect(
                    private_key_pem=private_key,
                    device_id=device_id,
                    client_id=client_id,
                    client_mode=client_mode,
                    role=role,
                    scopes=scopes,
                    token=auth_token,
                    nonce=actual_nonce,
                    platform=_detect_platform(),
                )
                # Use raw base64url public key (strip PEM header, decode DER, take last 32 bytes)
                pub_pem = device.get("publicKeyPem", "")
                raw_pub_b64url = pub_pem  # fallback
                try:
                    from cryptography.hazmat.primitives.serialization import load_pem_public_key, Encoding, PublicFormat
                    pub_key = load_pem_public_key(pub_pem.encode())
                    raw_bytes = pub_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
                    raw_pub_b64url = base64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode()
                except Exception:
                    pass
                params["device"] = {
                    "id": device_id,
                    "publicKey": raw_pub_b64url,
                    "signature": sig,
                    "signedAt": signed_at_ms,
                    "nonce": actual_nonce,
                }
            return params

        # 2. Wait for connect.challenge event first (server sends it on WS open)
        challenge_nonce: str | None = None
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                if msg.get("type") == "event" and msg.get("event") == "connect.challenge":
                    challenge_nonce = msg.get("payload", {}).get("nonce", "")
                    break
            except asyncio.TimeoutError:
                break

        # 3. Send connect with the server's challenge nonce (or our own if no challenge)
        connect_id = str(uuid.uuid4())
        await ws.send(json.dumps({
            "type": "req", "id": connect_id,
            "method": "connect", "params": _build_connect_params(challenge_nonce),
        }))

        # 4. Wait for connect response
        connected = False
        deadline = asyncio.get_event_loop().time() + 10
        while not connected and asyncio.get_event_loop().time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if msg.get("type") == "res" and msg.get("id") == connect_id:
                if not msg.get("ok"):
                    raise RuntimeError(f"connect failed: {msg.get('error', {})}")
                connected = True

        if not connected:
            raise RuntimeError("gateway connect timeout")

        # 2. Send agent request
        req_id = str(uuid.uuid4())
        idem = str(uuid.uuid4())
        agent_params: dict[str, Any] = {
            "message": message,
            "idempotencyKey": idem,
            "sessionKey": session_key,
            "agentId": agent_id,
            "thinking": _THINKING_MAP.get(thinking, "low"),
        }
        if deliver:
            agent_params["deliver"] = True
        await ws.send(json.dumps({
            "type": "req", "id": req_id,
            "method": "agent",
            "params": agent_params,
        }))

        # 3. Wait for final response (skip the initial "accepted" ack)
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 30))
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)

            if msg.get("type") == "res" and msg.get("id") == req_id:
                payload = msg.get("payload", {})
                status = payload.get("status", "")
                if status == "accepted":
                    continue  # Wait for the real final response
                if msg.get("ok"):
                    return payload
                raise RuntimeError(f"agent error: {msg.get('error', {})}")

            # Ignore events (ticks, agent streaming, etc.)

        raise RuntimeError(f"agent call timeout after {timeout_seconds}s")


def gateway_agent_call(
    message: str,
    session_key: str = "agent:main:main",
    timeout_seconds: int = 90,
    agent_id: str = "main",
    thinking: str = "low",
    deliver: bool = False,
) -> dict[str, Any]:
    """Synchronous wrapper for the async WS agent call."""
    coro = _ws_agent_call(
        message, session_key, timeout_seconds,
        agent_id=agent_id, thinking=thinking, deliver=deliver,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=timeout_seconds + 10)
    else:
        return asyncio.run(coro)


def extract_text_from_result(result: dict[str, Any]) -> str:
    """Extract the text response from an agent result payload."""
    # Try result.payloads[0].text first (standard agent response)
    inner = result.get("result")
    if isinstance(inner, dict):
        payloads = inner.get("payloads")
        if isinstance(payloads, list) and payloads:
            text = str(payloads[0].get("text") or "").strip()
            if text:
                return text
        text = str(inner.get("text") or inner.get("content") or "").strip()
        if text:
            return text
    if isinstance(inner, str) and inner.strip():
        return inner.strip()
    # Fallback to top-level summary (but only if no result content)
    summary = str(result.get("summary") or "").strip()
    if summary and summary != "completed":
        return summary
    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    import sys
    msg = sys.argv[1] if len(sys.argv) > 1 else "Reply with exactly: WS_OK"
    session = sys.argv[2] if len(sys.argv) > 2 else "agent:main:ws-test"
    print(f"Calling gateway WS: session={session}")
    try:
        result = gateway_agent_call(msg, session)
        text = extract_text_from_result(result)
        print(f"Result: {text[:200]}")
        print(f"Full: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

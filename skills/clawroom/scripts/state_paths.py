from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _dedupe(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def candidate_state_roots() -> list[Path]:
    candidates: list[Path] = []
    explicit = str(os.environ.get("CLAWROOM_STATE_ROOT") or "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())

    workspace_env = str(os.environ.get("OPENCLAW_WORKSPACE") or os.environ.get("OPENCLAW_WORKDIR") or "").strip()
    if workspace_env:
        candidates.append(Path(workspace_env).expanduser() / ".clawroom")

    candidates.append(Path.cwd() / ".clawroom")
    candidates.append(Path.home() / ".clawroom")
    return _dedupe(candidates)


def ensure_writable(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path, delete=True) as handle:
        handle.write(b"ok")
        handle.flush()
    return path


def resolve_state_root() -> Path:
    last_error = "no candidates tried"
    for path in candidate_state_roots():
        try:
            return ensure_writable(path)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{path}: {exc}"
    raise RuntimeError(f"no writable ClawRoom state root found ({last_error})")

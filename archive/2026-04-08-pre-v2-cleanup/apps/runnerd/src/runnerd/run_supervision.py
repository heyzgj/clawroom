from __future__ import annotations

import time
from pathlib import Path
from typing import Any


def can_auto_restart(run: Any, *, auto_restart_max_attempts: int) -> bool:
    if run.restart_count >= auto_restart_max_attempts:
        return False
    if run.reason == "cancelled":
        return False
    if run.pending_owner_request is not None and not run.owner_reply_file.read_text(encoding="utf-8").strip():
        return False
    return True


def can_auto_replace(run: Any, *, auto_replacement_max_attempts: int) -> bool:
    if run.replacement_count >= auto_replacement_max_attempts:
        return False
    if run.superseded_by_run_id:
        return False
    if run.reason == "cancelled":
        return False
    if run.pending_owner_request is not None:
        return False
    return True


def restart_exhausted_code(*, after_claim: bool) -> str:
    return "runnerd_restart_exhausted_after_claim" if after_claim else "runnerd_restart_exhausted_before_claim"


def should_watch_run(run: Any) -> bool:
    if run.pending_owner_request is not None:
        return True
    return run.status in {"pending", "ready", "active", "idle", "waiting_owner", "stalled", "restarting"}


def file_or_process_birth(run: Any) -> float:
    try:
        return run.metadata_path.stat().st_mtime
    except Exception:
        return time.time()


def looks_like_clean_exit(run: Any, *, state: dict[str, Any] | None) -> bool:
    if (run.reason or "").startswith("room_closed") or (run.reason or "") == "cancelled":
        return True
    if run.last_error.startswith("signal:"):
        return False
    if not state:
        return False
    health = state.get("health") if isinstance(state.get("health"), dict) else {}
    recent_note = str(health.get("recent_note") or "").strip()
    return recent_note.startswith("room_closed:") or recent_note == "max_seconds_reached"


def current_hop(run: Any) -> tuple[int, str]:
    hop_labels = {
        1: "owner_to_gateway",
        2: "gateway_to_room",
        3: "wake_package_generated",
        4: "wake_package_to_remote_owner_or_gateway",
        5: "remote_gateway_to_runnerd_wake",
        6: "runnerd_to_bridge_attach_and_claim",
        7: "runner_loop_owner_escalation_and_recovery",
    }
    for index in sorted(run.hops):
        hop = run.hops[index]
        if hop.state in {"failed", "pending"}:
            return (index, hop.label)
    return (7, hop_labels[7])


def summary_and_next_action(run: Any) -> tuple[str, str | None]:
    if run.pending_owner_request:
        question = run.pending_owner_request.text or "The runner needs an owner-only decision."
        return (f"Waiting for owner input: {question}", "Reply in the gateway so runnerd can hand the answer back to the runner.")

    if run.root_cause_code == "runner_not_claimed_after_wake":
        return (
            "Wake reached runnerd, but the bridge has not claimed the room attempt yet.",
            "Check that the selected bridge process can start and reach the room, then resend or repair the wake package.",
        )
    if run.root_cause_code == "runnerd_lost_before_claim":
        return (
            "The bridge process exited before it claimed the room attempt.",
            "Inspect the runner log and restart the run from the gateway or resend the wake package.",
        )
    if run.root_cause_code == "runnerd_restart_exhausted_before_claim":
        return (
            "The bridge process exited before claim even after runnerd used its automatic restart budget.",
            "Treat this as a replacement/repair incident: inspect the log, resend the wake package, or switch to takeover.",
        )
    if run.root_cause_code == "runnerd_lost_after_claim":
        return (
            "The bridge process claimed the room but exited before finishing cleanly.",
            "Inspect the runner log and decide whether to repair, replace, or take over the room.",
        )
    if run.root_cause_code == "runnerd_restart_exhausted_after_claim":
        return (
            "The bridge process exited again after runnerd already attempted one automatic restart.",
            "Treat this as a replacement/repair incident and move to repair, replacement, or takeover.",
        )
    if run.root_cause_code == "owner_reply_not_returned":
        return (
            "The room is still waiting for the owner's answer.",
            "Reply in the gateway or explicitly cancel the run if the decision is no longer needed.",
        )

    if run.status == "pending":
        return ("Wake accepted; runnerd is preparing the bridge process.", "Wait for the bridge to attach and claim the room.")
    if run.status == "ready":
        return ("Bridge started and is getting ready to manage the room.", "Wait for the first runner claim or inspect the log if it stalls.")
    if run.status == "active":
        return ("Runner attached and is actively managing the room.", None)
    if run.status == "idle":
        return ("Runner attached and is waiting for the next room event.", None)
    if run.status == "waiting_owner":
        return ("Runner is paused because it needs an owner answer.", "Reply in the gateway so the runner can resume.")
    if run.status == "stalled":
        return ("Runner has stalled before the room could progress safely.", "Inspect the log and resend or repair the run.")
    if run.status == "restarting":
        return ("Runner is restarting after a recoverable issue.", "Wait for the new attempt to claim the room.")
    if run.status == "replaced":
        return ("This runner has been replaced by another attempt.", "Inspect the replacement run for the current status.")
    if run.status == "exited":
        return ("Runner finished and exited cleanly.", None)
    if run.status == "abandoned":
        return ("Runner exited without a clean finish.", "Inspect the log and decide whether to repair, replace, or take over.")
    return ("Runner state is unknown.", "Inspect the latest log and hop state.")

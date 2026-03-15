from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class TelegramSendStep:
    text: str
    double_enter: bool
    delay_after_seconds: float


def normalize_bot_target(value: str) -> str:
    cleaned = str(value or "").strip()
    if cleaned.startswith("https://t.me/"):
        cleaned = cleaned.removeprefix("https://t.me/")
    if cleaned.startswith("t.me/"):
        cleaned = cleaned.removeprefix("t.me/")
    return cleaned.lstrip("@").strip()


def build_resolve_url(bot_target: str) -> str:
    target = normalize_bot_target(bot_target)
    if not target:
        raise ValueError("bot target is required")
    return f"tg://resolve?domain={target}"


def build_message_plan(*, text: str, reset_session: bool, wait_after_new: float) -> list[TelegramSendStep]:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise ValueError("message text is required")
    plan: list[TelegramSendStep] = []
    if reset_session:
        # Telegram's slash-command picker can eat the first Return; a second
        # Return safely sends /new once the picker inserts the command.
        plan.append(TelegramSendStep(text="/new", double_enter=True, delay_after_seconds=max(0.3, wait_after_new)))
    plan.append(TelegramSendStep(text=cleaned, double_enter=False, delay_after_seconds=0.0))
    return plan


def _run_checked(cmd: list[str], *, text_input: str | None = None) -> str:
    proc = subprocess.run(
        cmd,
        input=text_input,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout or ""


def _read_clipboard() -> str:
    return _run_checked(["pbpaste"]).rstrip("\n")


def _write_clipboard(text: str) -> None:
    _run_checked(["pbcopy"], text_input=text)


def run_applescript(lines: list[str]) -> None:
    cmd: list[str] = ["osascript"]
    for line in lines:
        cmd.extend(["-e", line])
    _run_checked(cmd)


def build_paste_and_send_script(*, double_enter: bool) -> list[str]:
    lines = [
        'tell application "Telegram" to activate',
        "delay 0.2",
        'tell application "System Events"',
        '  keystroke "v" using {command down}',
        "  delay 0.12",
        "  key code 36",
        "  delay 0.12",
    ]
    if double_enter:
        lines.append("  key code 36")
        lines.append("  delay 0.12")
    lines.append("end tell")
    return lines


def open_chat(bot_target: str, *, wait_after_open: float) -> None:
    _run_checked(["open", build_resolve_url(bot_target)])
    time.sleep(max(0.3, wait_after_open))


def paste_and_send(text: str, *, double_enter: bool) -> None:
    previous_clipboard = _read_clipboard()
    try:
        _write_clipboard(text)
        run_applescript(build_paste_and_send_script(double_enter=double_enter))
    finally:
        _write_clipboard(previous_clipboard)


def send_sequence(
    *,
    bot_target: str,
    text: str,
    reset_session: bool,
    wait_after_open: float,
    wait_after_new: float,
) -> list[TelegramSendStep]:
    plan = build_message_plan(text=text, reset_session=reset_session, wait_after_new=wait_after_new)
    open_chat(bot_target, wait_after_open=wait_after_open)
    for step in plan:
        paste_and_send(step.text, double_enter=step.double_enter)
        if step.delay_after_seconds > 0:
            time.sleep(step.delay_after_seconds)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a message to a Telegram bot via the Telegram desktop app.")
    parser.add_argument("--bot", required=True, help="Telegram username such as @singularitygz_bot")
    parser.add_argument("--text", default="", help="Message text to send")
    parser.add_argument("--text-file", default="", help="Optional file containing the message text")
    parser.add_argument("--reset-session", action="store_true", help="Send /new first with a hardened double-Enter sequence")
    parser.add_argument("--wait-after-open", type=float, default=1.2)
    parser.add_argument("--wait-after-new", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true", help="Print the planned sequence as JSON without sending")
    args = parser.parse_args()

    text = str(args.text or "").strip()
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8").strip()
    plan = build_message_plan(text=text, reset_session=args.reset_session, wait_after_new=args.wait_after_new)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "bot": normalize_bot_target(args.bot),
                    "resolve_url": build_resolve_url(args.bot),
                    "plan": [asdict(step) for step in plan],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    send_sequence(
        bot_target=args.bot,
        text=text,
        reset_session=args.reset_session,
        wait_after_open=args.wait_after_open,
        wait_after_new=args.wait_after_new,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "bot": normalize_bot_target(args.bot),
                "steps": [asdict(step) for step in plan],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

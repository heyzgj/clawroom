#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from telegram_desktop import (
    build_paste_and_send_script,
    normalize_bot_target,
    send_sequence,
)


def normalize_username(value: str) -> str:
    username = normalize_bot_target(value)
    if not username:
        raise ValueError("username required")
    return username


def build_paste_and_send_applescript(*, extra_enter: bool, paste_delay: float, between_enter_delay: float) -> str:
    del paste_delay, between_enter_delay
    return "\n".join(build_paste_and_send_script(double_enter=extra_enter))


def read_message(args: argparse.Namespace) -> str:
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8").strip()
    if args.message:
        return args.message.strip()
    raise ValueError("provide --message or --message-file")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a Telegram Desktop message to an OpenClaw bot.")
    parser.add_argument("--username", required=True, help="Telegram username, with or without @")
    parser.add_argument("--message", default="", help="Message text to send")
    parser.add_argument("--message-file", default="", help="Path to a UTF-8 text file to send")
    parser.add_argument("--new-session", action="store_true", help="Send /new first with a hardened double-enter sequence")
    parser.add_argument("--open-delay", type=float, default=1.2, help="Seconds to wait after opening the chat")
    parser.add_argument(
        "--new-delay",
        type=float,
        default=30.0,
        help="Seconds to wait after sending /new before posting the real request",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the send plan but do not interact with Telegram")
    args = parser.parse_args()

    username = normalize_username(args.username)
    message = read_message(args)

    if args.dry_run:
        print(
            f"[telegram-send] dry_run username=@{username} new_session={args.new_session} "
            f"new_delay={max(0.3, args.new_delay):.1f}s message_chars={len(message)}"
        )
        return

    plan = send_sequence(
        bot_target=username,
        text=message,
        reset_session=args.new_session,
        wait_after_open=args.open_delay,
        wait_after_new=args.new_delay,
    )
    print(
        f"[telegram-send] sent username=@{username} new_session={args.new_session} "
        f"steps={len(plan)} chars={len(message)}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram-send] error: {exc}", file=sys.stderr)
        raise

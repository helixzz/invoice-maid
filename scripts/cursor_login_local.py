#!/usr/bin/env python3
"""Mode-B helper: capture a Cursor Playwright storage_state on your LOCAL
machine so the production invoice-maid instance can reuse the session
without seeing your password or your 2FA seed.

Usage (run this on your laptop, NOT on the prod host):

    python scripts/cursor_login_local.py --email you@example.com
    # OR, for a dry-run (no browser, just prints the banner):
    python scripts/cursor_login_local.py --dry-run

Workflow:
    1. A non-headless Chromium window opens on https://cursor.com/login.
    2. You log in interactively (password + 2FA + device trust).
    3. Once the Cursor dashboard is visible, return to this terminal and
       press ENTER. The script then serialises cookies + localStorage to
       JSON and prints it to stdout.
    4. Paste that JSON into invoice-maid Settings → Cursor account →
       "Storage state" field and click Save. Re-run every ~7–30 days
       when the WorkOS session expires.

This script NEVER uploads anything anywhere; the JSON only leaves your
machine when you paste it into your own invoice-maid instance.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys


CURSOR_LOGIN_URL = "https://cursor.com/login"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a Cursor Playwright storage_state for invoice-maid Mode B.",
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Pre-fill this email into the login form (optional, cosmetic only).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not launch a browser; print a usage banner and exit 0.",
    )
    return parser.parse_args(argv)


def _banner() -> str:
    return (
        "cursor_login_local.py — capture Cursor Playwright storage_state for Mode B.\n"
        "  1. A Chromium window will open at cursor.com/login.\n"
        "  2. Complete login + 2FA + device trust by hand.\n"
        "  3. Return here and press ENTER; the JSON is printed to stdout.\n"
        "  4. Paste the JSON into invoice-maid Settings → Cursor account.\n"
    )


async def _capture_storage_state(email: str | None) -> str:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise SystemExit(
            "playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from exc

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(CURSOR_LOGIN_URL)
        if email:
            try:
                await page.fill('input[type="email"]', email)
            except Exception:
                pass
        print(
            "Browser is open. Log in + complete 2FA, then press ENTER here to save state.",
            file=sys.stderr,
        )
        await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
        state = await context.storage_state()
        await context.close()
        await browser.close()
        return json.dumps(state)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    print(_banner(), file=sys.stderr)
    if args.dry_run:
        return 0
    state_json = asyncio.run(_capture_storage_state(args.email))
    print(state_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""mail-filter — Gmail inbox filter for the hub ecosystem.

Polls Gmail inbox, applies filter rules (archive or tag-for-delete).
Writes state to ~/.cache/gmail/. On actions taken, fires desktop notifications.

Usage:
    mail-filter              Run poll (designed for cron every 15min)
    mail-filter --status     Print current state without polling
    mail-filter --help
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home()))
sys.path.insert(0, str(Path.home() / 'projects' / 'open-interpreter' / 'tools' / 'hub' / 'webui'))

from hub_common import hub_notify


def main():
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__.strip())
        return

    # Lazy import to avoid loading Gmail libs when not needed
    from mail_api import get_auth_status, load_rules, poll_and_apply, get_recent_actions

    if '--status' in sys.argv:
        auth = get_auth_status()
        rules = load_rules()
        enabled = sum(1 for r in rules if r.get('enabled', True))
        recent = get_recent_actions(5)

        print(f"Authenticated: {auth['authenticated']}")
        if auth.get('email'):
            print(f"Account: {auth['email']}")
        print(f"Rules: {enabled}/{len(rules)} enabled")
        print(f"Token expiry: {auth.get('token_expiry', 'unknown')}")

        if recent:
            print(f"\nLast {len(recent)} actions:")
            for a in recent:
                ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(a['ts']))
                print(f"  {ts}  {a['action']:8s}  {a.get('from', '')[:40]}  {a.get('subject', '')[:40]}")
        return

    # Check prerequisites
    auth = get_auth_status()
    if not auth['has_credentials']:
        print("No Gmail credentials configured. Set up via OI-web Mail tab.")
        return
    if not auth['authenticated']:
        print("Not authenticated. Complete OAuth via OI-web Mail tab.")
        return

    # Run poll (LLM-driven + fast-filter rules)
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] Scanning inbox...")

    try:
        result = poll_and_apply()
        archived = result.get('archived', 0)
        tagged = result.get('tagged_delete', 0)
        processed = result.get('processed', 0)
        errors = result.get('errors', [])

        kept = result.get('kept', 0)
        print(f"[{ts}] Done: {processed} scanned, {archived} archived, {tagged} tagged, {kept} kept")

        if errors:
            for e in errors[:5]:
                print(f"  ERROR: {e}")

        total = archived + tagged
        if total > 0:
            hub_notify(
                f"Mail: {total} action(s)",
                f"Archived: {archived}, Tagged: {tagged}",
                source='mail-filter',
            )
    except Exception as e:
        print(f"[{ts}] ERROR: {e}")
        hub_notify("Mail Filter Error", str(e), icon='dialog-error', source='mail-filter')


if __name__ == '__main__':
    main()

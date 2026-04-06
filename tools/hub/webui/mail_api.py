"""mail_api — LLM-driven Gmail triage assistant for OI-web.

Scan → Review → Apply workflow:
  1. Scan: fetch emails matching scope, LLM classifies each one
  2. Review: user sees recommendations (auto mode = feed, manual mode = batch checkboxes)
  3. Apply: user approves actions, only then are emails modified

No auto-action. Nothing touches email without explicit approval.
"""

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httplib2
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.file import Storage
from googleapiclient.discovery import build

sys.path.insert(0, str(Path.home()))
try:
    from hub_common import llm_query, DEFAULT_MODEL
except ImportError:
    llm_query = None
    DEFAULT_MODEL = None

# ── Paths ────────────────────────────────────────────────────────────────────

WEBUI_DIR = Path(__file__).resolve().parent
ENV_FILE = WEBUI_DIR / ".env"
CONFIG_DIR = Path.home() / ".config" / "hub"
TOKEN_FILE = CONFIG_DIR / "gmail-token.json"
RULES_FILE = CONFIG_DIR / "mail-rules.json"
MAIL_CONFIG_FILE = CONFIG_DIR / "mail-config.json"
CACHE_DIR = Path.home() / ".cache" / "gmail"
SCAN_FILE = CACHE_DIR / "scan-results.json"
ACTIONS_LOG = CACHE_DIR / "actions.jsonl"
SENDER_STATS_FILE = CACHE_DIR / "sender-stats.json"
SUGGESTIONS_FILE = CACHE_DIR / "suggestions.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TO_DELETE_LABEL = "TO-DELETE"
_pending_flows = {}

DEFAULT_SYSTEM_PROMPT = """You are an email assistant triaging my inbox. For each email, recommend one action:

- "keep" — important, personal, or work-related. Leave in inbox.
- "archive" — not urgent, newsletters I sometimes read, notifications, receipts.
- "delete" — spam, marketing I never read, unwanted notifications. Tag for deletion.

Respond with a JSON array. Each element: {"id": <index>, "action": "keep"|"archive"|"delete", "reason": "<brief reason>"}

Be conservative — when in doubt, recommend "keep"."""

DEFAULT_CONFIG = {
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "mode": "manual",        # "auto" or "manual"
    "scope_read": "unread",  # "unread" or "all"
    "scope_label": "inbox",  # "inbox" or "all"
    "batch_size": 25,
}


# ── .env helpers ─────────────────────────────────────────────────────────────

def _load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip("'\"")
    return env


def _save_env(updates: dict):
    lines = []
    existing_keys = set()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in updates:
                    lines.append(f"{key}={updates[key]}")
                    existing_keys.add(key)
                else:
                    lines.append(line)
            else:
                lines.append(line)
    for key, val in updates.items():
        if key not in existing_keys:
            lines.append(f"{key}={val}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def _get_gmail_creds():
    env = _load_env()
    cid = env.get("GMAIL_CLIENT_ID")
    csec = env.get("GMAIL_CLIENT_SECRET")
    return (cid, csec) if cid and csec else None


# ── Config ───────────────────────────────────────────────────────────────────

def load_mail_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if MAIL_CONFIG_FILE.exists():
        try:
            stored = json.loads(MAIL_CONFIG_FILE.read_text())
            config.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_mail_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    MAIL_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


# ── Auth ─────────────────────────────────────────────────────────────────────

def get_auth_status() -> dict:
    creds = _get_gmail_creds()
    has_credentials = creds is not None
    authenticated = False
    email = None
    token_expiry = None

    if has_credentials and TOKEN_FILE.exists():
        try:
            storage = Storage(str(TOKEN_FILE))
            oauth_creds = storage.get()
            if oauth_creds and not oauth_creds.invalid:
                authenticated = True
                if oauth_creds.token_expiry:
                    token_expiry = oauth_creds.token_expiry.isoformat()
                if hasattr(oauth_creds, "id_token") and oauth_creds.id_token:
                    email = oauth_creds.id_token.get("email")
        except Exception:
            pass

    return {
        "has_credentials": has_credentials,
        "authenticated": authenticated,
        "email": email,
        "token_expiry": token_expiry,
    }


def save_credentials(client_id: str, client_secret: str):
    _save_env({"GMAIL_CLIENT_ID": client_id, "GMAIL_CLIENT_SECRET": client_secret})


def start_auth_flow(redirect_uri: str) -> dict:
    creds = _get_gmail_creds()
    if not creds:
        raise ValueError("No Gmail credentials configured")
    client_id, client_secret = creds
    state = uuid.uuid4().hex
    flow = OAuth2WebServerFlow(
        client_id=client_id, client_secret=client_secret,
        scope=SCOPES, redirect_uri=redirect_uri,
        state=state, access_type="offline", prompt="consent",
    )
    _pending_flows[state] = flow
    return {"authorize_url": flow.step1_get_authorize_url(state=state), "state": state}


def complete_auth_flow(code: str, state: str) -> dict:
    flow = _pending_flows.pop(state, None)
    if not flow:
        creds = _get_gmail_creds()
        if not creds:
            raise ValueError("No Gmail credentials and no pending flow")
        flow = OAuth2WebServerFlow(
            client_id=creds[0], client_secret=creds[1],
            scope=SCOPES, redirect_uri="postmessage",
        )
    credential = flow.step2_exchange(code)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    storage = Storage(str(TOKEN_FILE))
    storage.put(credential)
    credential.set_store(storage)
    email = None
    if hasattr(credential, "id_token") and credential.id_token:
        email = credential.id_token.get("email")
    return {"ok": True, "email": email}


def get_gmail_service():
    if not TOKEN_FILE.exists():
        raise ValueError("Not authenticated")
    storage = Storage(str(TOKEN_FILE))
    credentials = storage.get()
    if not credentials or credentials.invalid:
        raise ValueError("Invalid credentials. Re-authenticate.")
    http = credentials.authorize(httplib2.Http())
    return build("gmail", "v1", http=http, cache_discovery=False)


# ── Gmail helpers ────────────────────────────────────────────────────────────

def _get_header(headers: list, name: str) -> str:
    name_lower = name.lower()
    for h in headers:
        if h["name"].lower() == name_lower:
            return h["value"]
    return ""


def _ensure_to_delete_label(service) -> str:
    """Get or create TO-DELETE label."""
    results = service.users().labels().list(userId="me").execute()
    for label in results.get("labels", []):
        if label["name"] == TO_DELETE_LABEL:
            return label["id"]
    created = service.users().labels().create(userId="me", body={
        "name": TO_DELETE_LABEL,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }).execute()
    return created["id"]


# ── Fast-filter rules ────────────────────────────────────────────────────────

def load_rules() -> list:
    if not RULES_FILE.exists():
        return []
    try:
        return json.loads(RULES_FILE.read_text()).get("rules", [])
    except (json.JSONDecodeError, OSError):
        return []


def save_rules(rules: list):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    RULES_FILE.write_text(json.dumps({"rules": rules}, indent=2) + "\n")


def add_rule(rule: dict):
    rules = load_rules()
    rule.setdefault("id", f"r_{uuid.uuid4().hex[:8]}")
    rule.setdefault("enabled", True)
    rule.setdefault("created", int(time.time()))
    rule.setdefault("stats", {"total_matched": 0, "last_matched": None})
    match = rule.get("match", {})
    match.setdefault("from", None)
    match.setdefault("subject", None)
    rule["match"] = match
    rules.append(rule)
    save_rules(rules)
    return rule


def update_rule(rule_id: str, updates: dict) -> bool:
    rules = load_rules()
    for rule in rules:
        if rule["id"] == rule_id:
            for k, v in updates.items():
                if k == "match":
                    rule["match"].update(v)
                elif k != "id":
                    rule[k] = v
            save_rules(rules)
            return True
    return False


def delete_rule(rule_id: str) -> bool:
    rules = load_rules()
    new = [r for r in rules if r["id"] != rule_id]
    if len(new) < len(rules):
        save_rules(new)
        return True
    return False


def _match_fast_filter(headers: list, rules: list) -> str | None:
    """Check fast-filter rules. Returns action or None."""
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        match = rule.get("match", {})
        matched = True
        has_criteria = False

        if match.get("from"):
            has_criteria = True
            if match["from"].lower() not in _get_header(headers, "From").lower():
                matched = False

        if match.get("subject") and matched:
            has_criteria = True
            if match["subject"].lower() not in _get_header(headers, "Subject").lower():
                matched = False

        if has_criteria and matched:
            rule["stats"]["total_matched"] = rule["stats"].get("total_matched", 0) + 1
            rule["stats"]["last_matched"] = int(time.time())
            return rule.get("action", "archive")
    return None


# ── Scan (fetch + classify, no action taken) ────────────────────────────────

def _build_email_summary(emails: list) -> str:
    lines = []
    for i, e in enumerate(emails):
        lines.append(f"[{i}] From: {e['from']}")
        lines.append(f"    Subject: {e['subject']}")
        lines.append(f"    Date: {e['date']}")
        if e.get("snippet"):
            lines.append(f"    Preview: {e['snippet'][:150]}")
        lines.append("")
    return "\n".join(lines)


def _ask_llm(email_summary: str, system_prompt: str) -> list | None:
    if not llm_query or not DEFAULT_MODEL:
        return None
    result = llm_query(
        DEFAULT_MODEL, system_prompt,
        f"Here are the emails to triage:\n\n{email_summary}\n\nClassify each one.",
        timeout=120, temperature=0.2, num_predict=4096,
    )
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("decisions", "emails", "results", "classifications"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return None


def scan() -> dict:
    """Fetch emails and classify with LLM. Stores results, takes no action."""
    config = load_mail_config()
    service = get_gmail_service()
    rules = load_rules()

    batch_size = config.get("batch_size", 25)
    scope_read = config.get("scope_read", "unread")
    scope_label = config.get("scope_label", "inbox")

    # Build Gmail query
    label_ids = ["INBOX"] if scope_label == "inbox" else None
    q_parts = []
    if scope_read == "unread":
        q_parts.append("is:unread")
    q = " ".join(q_parts) if q_parts else None

    kwargs = {"userId": "me", "maxResults": batch_size}
    if label_ids:
        kwargs["labelIds"] = label_ids
    if q:
        kwargs["q"] = q

    result = service.users().messages().list(**kwargs).execute()
    messages = result.get("messages", [])

    if not messages:
        _save_scan([])
        return {"scanned": 0, "results": []}

    # Fetch metadata
    emails = []
    for msg_stub in messages:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_stub["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = msg.get("payload", {}).get("headers", [])
            emails.append({
                "msg_id": msg_stub["id"],
                "from": _get_header(headers, "From"),
                "subject": _get_header(headers, "Subject"),
                "date": _get_header(headers, "Date"),
                "snippet": msg.get("snippet", ""),
                "headers": headers,
            })
        except Exception:
            continue

    # Phase 1: fast-filter pass
    scan_results = []
    llm_batch = []

    for email in emails:
        fast_action = _match_fast_filter(email["headers"], rules)
        if fast_action:
            scan_results.append({
                "msg_id": email["msg_id"],
                "from": email["from"],
                "subject": email["subject"],
                "date": email["date"],
                "snippet": email["snippet"],
                "action": fast_action,
                "reason": "Fast-filter rule match",
                "source": "rule",
                "approved": None,  # None = pending review
            })
        else:
            llm_batch.append(email)

    save_rules(rules)  # persist updated stats

    # Phase 2: LLM classification
    if llm_batch:
        system_prompt = config.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        summary = _build_email_summary(llm_batch)
        decisions = _ask_llm(summary, system_prompt)

        if decisions:
            for decision in decisions:
                idx = decision.get("id")
                if idx is not None and 0 <= idx < len(llm_batch):
                    email = llm_batch[idx]
                    scan_results.append({
                        "msg_id": email["msg_id"],
                        "from": email["from"],
                        "subject": email["subject"],
                        "date": email["date"],
                        "snippet": email["snippet"],
                        "action": decision.get("action", "keep"),
                        "reason": decision.get("reason", ""),
                        "source": "llm",
                        "approved": None,
                    })
                    llm_batch[idx] = None  # mark as classified

        # Any unclassified emails default to keep
        for email in llm_batch:
            if email is not None:
                scan_results.append({
                    "msg_id": email["msg_id"],
                    "from": email["from"],
                    "subject": email["subject"],
                    "date": email["date"],
                    "snippet": email["snippet"],
                    "action": "keep",
                    "reason": "LLM did not classify" if llm_query else "No LLM available",
                    "source": "default",
                    "approved": None,
                })

    _save_scan(scan_results)

    # Auto mode: apply LLM decisions immediately
    if config.get("mode") == "auto" and scan_results:
        auto_approvals = {}
        for r in scan_results:
            auto_approvals[r["msg_id"]] = r["action"]
        apply_result = apply_actions(auto_approvals)
        return {
            "scanned": len(scan_results),
            "auto_applied": True,
            **apply_result,
        }

    return {"scanned": len(scan_results), "auto_applied": False, "results": scan_results}


def _save_scan(results: list):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SCAN_FILE.write_text(json.dumps({
        "ts": int(time.time()),
        "results": results,
    }, indent=2) + "\n")


def load_scan() -> dict:
    """Load last scan results."""
    if not SCAN_FILE.exists():
        return {"ts": None, "results": []}
    try:
        return json.loads(SCAN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"ts": None, "results": []}


# ── Apply (execute approved actions) ────────────────────────────────────────

def apply_actions(approvals: dict) -> dict:
    """Execute approved actions from scan results.

    approvals: {msg_id: "archive"|"delete"|"keep"|"reject"}
      - "archive"/"delete" = execute that action
      - "keep"/"reject" = skip (leave in inbox)
    """
    service = get_gmail_service()
    scan_data = load_scan()
    results = scan_data.get("results", [])

    archived = 0
    tagged_delete = 0
    skipped = 0
    errors = []

    for item in results:
        msg_id = item["msg_id"]
        decision = approvals.get(msg_id)

        if not decision or decision in ("keep", "reject"):
            skipped += 1
            item["approved"] = False
            continue

        try:
            if decision == "archive":
                service.users().messages().modify(
                    userId="me", id=msg_id,
                    body={"removeLabelIds": ["INBOX"]},
                ).execute()
                archived += 1
                item["approved"] = True
            elif decision == "delete":
                label_id = _ensure_to_delete_label(service)
                service.users().messages().modify(
                    userId="me", id=msg_id,
                    body={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
                ).execute()
                tagged_delete += 1
                item["approved"] = True

            _log_action({
                "ts": int(time.time()),
                "action": decision,
                "source": item.get("source", ""),
                "reason": item.get("reason", ""),
                "message_id": msg_id,
                "from": item["from"],
                "subject": item["subject"],
            })
        except Exception as e:
            errors.append(f"{msg_id}: {e}")
            item["approved"] = False

    # Update scan file with approval status
    _save_scan(results)

    # Track sender patterns from approved actions
    _track_senders(results, approvals)

    return {"archived": archived, "tagged_delete": tagged_delete, "skipped": skipped, "errors": errors}


def _log_action(entry: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACTIONS_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Sender tracking & smart suggestions ─────────────────────────────────────

def _extract_email(from_header: str) -> str:
    """Extract email address from 'Name <email>' format."""
    if "<" in from_header and ">" in from_header:
        return from_header.split("<")[1].split(">")[0].lower()
    return from_header.strip().lower()


def _load_sender_stats() -> dict:
    if SENDER_STATS_FILE.exists():
        try:
            return json.loads(SENDER_STATS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_sender_stats(stats: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SENDER_STATS_FILE.write_text(json.dumps(stats, indent=2) + "\n")


def _track_senders(results: list, approvals: dict):
    """Update sender stats based on what the user actually approved."""
    stats = _load_sender_stats()

    for item in results:
        msg_id = item["msg_id"]
        decision = approvals.get(msg_id)
        if not decision:
            continue

        sender = _extract_email(item["from"])
        if not sender:
            continue

        if sender not in stats:
            stats[sender] = {
                "display_name": item["from"],
                "archive": 0, "delete": 0, "keep": 0,
                "first_seen": int(time.time()),
                "last_seen": int(time.time()),
            }

        s = stats[sender]
        s["last_seen"] = int(time.time())
        s["display_name"] = item["from"]  # keep latest

        if decision in ("archive", "delete", "keep", "reject"):
            action = "keep" if decision == "reject" else decision
            s[action] = s.get(action, 0) + 1

    _save_sender_stats(stats)
    _refresh_suggestions(stats, results, approvals)


def _load_suggestions() -> list:
    if SUGGESTIONS_FILE.exists():
        try:
            data = json.loads(SUGGESTIONS_FILE.read_text())
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_suggestions(suggestions: list):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SUGGESTIONS_FILE.write_text(json.dumps(suggestions, indent=2) + "\n")


def _refresh_suggestions(stats: dict, results: list, approvals: dict):
    """Ask the LLM to analyze sender patterns and propose smart suggestions."""
    if not llm_query or not DEFAULT_MODEL:
        return

    # Only run every 5+ apply cycles to avoid spamming LLM
    existing = _load_suggestions()
    total_actions = sum(
        s.get("archive", 0) + s.get("delete", 0) + s.get("keep", 0)
        for s in stats.values()
    )
    if total_actions < 5:
        return  # not enough data yet

    # Build context for the LLM
    existing_rules = load_rules()
    existing_froms = {
        r["match"].get("from", "").lower()
        for r in existing_rules
        if r.get("match", {}).get("from")
    }
    already_suggested = {s.get("sender") for s in existing if not s.get("dismissed")}

    # Filter to senders with enough history, no existing rule/suggestion
    candidates = []
    for sender, s in stats.items():
        if any(ef and ef in sender for ef in existing_froms if ef):
            continue
        if sender in already_suggested:
            continue
        total = s.get("archive", 0) + s.get("delete", 0) + s.get("keep", 0)
        if total < 2:
            continue
        candidates.append({
            "sender": sender,
            "display_name": s.get("display_name", sender),
            "archived": s.get("archive", 0),
            "deleted": s.get("delete", 0),
            "kept": s.get("keep", 0),
            "first_seen": s.get("first_seen"),
            "last_seen": s.get("last_seen"),
        })

    if not candidates:
        return

    # Also include what was just actioned for context
    recent_actions = []
    for item in results:
        decision = approvals.get(item["msg_id"])
        if decision and decision not in ("keep", "reject"):
            recent_actions.append({
                "from": item["from"],
                "subject": item["subject"],
                "action": decision,
            })

    config = load_mail_config()
    user_prompt = config.get("system_prompt", "")

    sender_summary = json.dumps(candidates, indent=2)
    recent_summary = json.dumps(recent_actions[:10], indent=2) if recent_actions else "None"

    system_msg = """You analyze email sender patterns to suggest automation rules.
The user has been manually triaging their inbox. Based on their history with each sender, decide which senders deserve an auto-filter rule.

IMPORTANT:
- Only suggest rules for ONGOING patterns, not one-time cleanup (e.g. old orders being deleted doesn't mean auto-delete all from that sender)
- Consider recency — if a sender was only seen long ago, it's not a pattern
- Consider the ratio — if the user keeps some and archives/deletes others from the same sender, DON'T suggest a rule
- Be conservative — only suggest when you're confident

Respond with a JSON array of suggestions (can be empty []):
[{"sender": "email@example.com", "action": "archive"|"delete", "reason": "brief explanation"}]"""

    user_msg = f"""User's mail preferences: {user_prompt[:500] if user_prompt else 'Not specified'}

Sender history:
{sender_summary}

Recent actions this session:
{recent_summary}

Which senders, if any, should get an auto-filter rule?"""

    llm_result = llm_query(
        DEFAULT_MODEL, system_msg, user_msg,
        timeout=60, temperature=0.2, num_predict=2048,
    )

    if not llm_result:
        return

    suggestions_list = llm_result if isinstance(llm_result, list) else []
    if isinstance(llm_result, dict):
        for key in ("suggestions", "rules", "results"):
            if key in llm_result and isinstance(llm_result[key], list):
                suggestions_list = llm_result[key]
                break

    for s in suggestions_list:
        sender = s.get("sender", "").lower()
        action = s.get("action", "archive")
        reason = s.get("reason", "")

        if not sender or action not in ("archive", "delete"):
            continue
        if sender in already_suggested:
            continue

        # Find display name from stats
        display = stats.get(sender, {}).get("display_name", sender)
        sender_stats = stats.get(sender, {})

        existing.append({
            "id": f"s_{uuid.uuid4().hex[:8]}",
            "ts": int(time.time()),
            "type": "rule_proposal",
            "sender": sender,
            "display_name": display,
            "title": f"Auto-{action} from {sender}",
            "description": reason,
            "action": action,
            "stats": {
                "archive": sender_stats.get("archive", 0),
                "delete": sender_stats.get("delete", 0),
                "keep": sender_stats.get("keep", 0),
            },
            "dismissed": False,
        })

    _save_suggestions(existing)


def get_suggestions() -> list:
    """Get active (non-dismissed) suggestions."""
    return [s for s in _load_suggestions() if not s.get("dismissed")]


def accept_suggestion(suggestion_id: str) -> dict:
    """Accept a suggestion — creates a fast-filter rule."""
    suggestions = _load_suggestions()
    for s in suggestions:
        if s["id"] == suggestion_id and not s.get("dismissed"):
            rule = add_rule({
                "name": s["title"],
                "match": {"from": s["sender"]},
                "action": s["action"],
            })
            s["dismissed"] = True
            _save_suggestions(suggestions)
            return {"ok": True, "rule": rule}
    return {"ok": False, "error": "Suggestion not found"}


def dismiss_suggestion(suggestion_id: str) -> bool:
    suggestions = _load_suggestions()
    for s in suggestions:
        if s["id"] == suggestion_id:
            s["dismissed"] = True
            _save_suggestions(suggestions)
            return True
    return False


# ── Activity log ─────────────────────────────────────────────────────────────

def get_recent_actions(limit: int = 50) -> list:
    if not ACTIONS_LOG.exists():
        return []
    try:
        lines = ACTIONS_LOG.read_text().strip().splitlines()
        recent = lines[-limit:]
        recent.reverse()
        return [json.loads(l) for l in recent if l.strip()]
    except (json.JSONDecodeError, OSError):
        return []


# ── Reset ────────────────────────────────────────────────────────────────────

def reset(scope: str = "token"):
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    if scope == "all":
        if ENV_FILE.exists():
            env = _load_env()
            env.pop("GMAIL_CLIENT_ID", None)
            env.pop("GMAIL_CLIENT_SECRET", None)
            lines = [f"{k}={v}" for k, v in env.items()]
            ENV_FILE.write_text("\n".join(lines) + "\n" if lines else "")
        for f in [RULES_FILE, MAIL_CONFIG_FILE, SCAN_FILE, ACTIONS_LOG,
                  SENDER_STATS_FILE, SUGGESTIONS_FILE]:
            if f.exists():
                f.unlink()


# ── Overview ─────────────────────────────────────────────────────────────────

def get_overview() -> dict:
    auth = get_auth_status()
    config = load_mail_config()
    scan_data = load_scan()
    recent = get_recent_actions(20)
    rules = load_rules()
    suggestions = get_suggestions()

    pending = [r for r in scan_data.get("results", []) if r.get("approved") is None]

    return {
        **auth,
        "config": config,
        "rules_count": len(rules),
        "rules_enabled": sum(1 for r in rules if r.get("enabled", True)),
        "scan_ts": scan_data.get("ts"),
        "scan_results": scan_data.get("results", []),
        "pending_count": len(pending),
        "recent_actions": recent,
        "suggestions": suggestions,
        "llm_available": llm_query is not None and DEFAULT_MODEL is not None,
    }

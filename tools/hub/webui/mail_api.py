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
    from hub_common import llm_query, llm_query_text, DEFAULT_MODEL
except ImportError:
    llm_query = None
    llm_query_text = None
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

DEFAULT_SUGGESTION_PROMPT = """You're an email assistant reviewing the user's triage patterns. Based on their history — which senders they archive, delete, or keep — give brief, actionable advice.

You also see the age of emails currently in the inbox. Pay attention to stale mail that piles up.

Available filter rule types:
1. **Immediate**: `from: "sender@example.com"` → archive/delete (for senders that are always noise)
2. **Time-based**: `from: "sender@example.com", older_than: 7` → archive (for emails that are useful short-term but become clutter — e.g. order confirmations, newsletters you read within a week, notifications)

When you see emails from a sender sitting in the inbox for weeks/months, recommend a time-based rule \
with a specific `older_than` value in days. For example:
- "GitHub notifications: useful when fresh, but you have 27 sitting there for months. \
Suggest rule: `from: notifications@github.com, older_than: 7` → archive"
- "PayPal receipts: 4 emails from 4 months ago still in inbox. \
Suggest rule: `from: service@paypal.nl, older_than: 14` → archive"

Always reference the specific email address. Use markdown. Keep it short and practical."""

DEFAULT_CONFIG = {
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "suggestion_prompt": DEFAULT_SUGGESTION_PROMPT,
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

    # Apply retroactively to current scan results
    scan_data = load_scan()
    results = scan_data.get("results", [])
    retroactive = 0
    for item in results:
        if item.get("source") == "rule":
            continue  # already classified by a rule
        headers = item.get("headers", [])
        if not headers:
            continue
        action = _match_fast_filter(headers, [rule])
        if action:
            item["action"] = action
            item["reason"] = f"Rule: {rule.get('name', rule['id'])}"
            item["source"] = "rule"
            item["approved"] = None
            retroactive += 1
    if retroactive:
        _save_scan(results)
    rule["_retroactive"] = retroactive

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

        if match.get("older_than") and matched:
            age = _parse_email_age_days(_get_header(headers, "Date"))
            if age is None or age < match["older_than"]:
                matched = False

        if has_criteria and matched:
            rule["stats"]["total_matched"] = rule["stats"].get("total_matched", 0) + 1
            rule["stats"]["last_matched"] = int(time.time())
            return rule.get("action", "archive")
    return None


# ── Age helpers ───────────────────────────────────────────────────────────────

def _parse_email_age_days(date_str: str) -> int | None:
    """Parse email Date header into age in days. Returns None on failure."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0, delta.days)
    except Exception:
        return None


def _age_label(days: int | None) -> str:
    """Human-readable age string."""
    if days is None:
        return ""
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    if days < 7:
        return f"{days} days ago"
    if days < 30:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    months = days // 30
    return f"{months} month{'s' if months > 1 else ''} ago"


# ── Scan (fetch + classify, no action taken) ────────────────────────────────

def _build_email_summary(emails: list) -> str:
    lines = []
    for i, e in enumerate(emails):
        age_days = _parse_email_age_days(e.get("date", ""))
        age_str = f" ({_age_label(age_days)})" if age_days is not None else ""
        lines.append(f"[{i}] From: {e['from']}")
        lines.append(f"    Subject: {e['subject']}")
        lines.append(f"    Date: {e['date']}{age_str}")
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


def _save_advice(text: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SUGGESTIONS_FILE.write_text(json.dumps({
        "advice": text, "ts": int(time.time()),
    }, indent=2) + "\n")


def get_advice() -> dict:
    """Get latest LLM advice on triage patterns."""
    if SUGGESTIONS_FILE.exists():
        try:
            data = json.loads(SUGGESTIONS_FILE.read_text())
            if isinstance(data, dict) and "advice" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"advice": "", "ts": None}


def _refresh_suggestions(stats: dict, results: list, approvals: dict):
    """Ask the LLM to review sender patterns and give plain-text advice."""
    if not llm_query_text or not DEFAULT_MODEL:
        _save_advice("No LLM available.")
        return

    total_actions = sum(
        s.get("archive", 0) + s.get("delete", 0) + s.get("keep", 0)
        for s in stats.values()
    )
    if total_actions < 3:
        _save_advice(f"Need more data — {total_actions} action(s) so far.")
        return

    # Build age info per sender from current scan results
    sender_ages = {}  # sender_lower → list of age_days
    for item in results:
        from_addr = item.get("from", "")
        age = _parse_email_age_days(item.get("date", ""))
        if age is not None:
            key = from_addr.lower().split("<")[-1].rstrip(">").strip() if "<" in from_addr else from_addr.lower()
            sender_ages.setdefault(key, []).append(age)

    # Build sender summary
    senders = []
    for sender, s in stats.items():
        total = s.get("archive", 0) + s.get("delete", 0) + s.get("keep", 0)
        if total < 2:
            continue
        display = s.get('display_name', sender)
        # Ensure email address is always visible
        addr_part = f" ({sender})" if sender not in display.lower() else ""
        # Add age context
        ages = sender_ages.get(sender.lower(), [])
        age_ctx = ""
        if ages:
            oldest = max(ages)
            newest = min(ages)
            count = len(ages)
            age_ctx = f", {count} emails in inbox (oldest: {_age_label(oldest)}, newest: {_age_label(newest)})"
        senders.append(
            f"- {display}{addr_part}: "
            f"archived {s.get('archive', 0)}, deleted {s.get('delete', 0)}, kept {s.get('keep', 0)}"
            f"{age_ctx}"
        )

    if not senders:
        _save_advice("Not enough repeat senders yet to spot patterns.")
        return

    # Include existing rules for context
    existing_rules = load_rules()
    rules_ctx = ""
    if existing_rules:
        rule_lines = [f"- {r.get('name', r['id'])}: {r['match']} → {r.get('action', 'archive')}"
                      for r in existing_rules if r.get("enabled", True)]
        if rule_lines:
            rules_ctx = f"\n\nExisting filter rules:\n" + "\n".join(rule_lines)

    config = load_mail_config()
    system_msg = config.get("suggestion_prompt") or DEFAULT_SUGGESTION_PROMPT

    user_msg = f"Sender patterns:\n" + "\n".join(senders) + rules_ctx

    advice = llm_query_text(
        DEFAULT_MODEL, system_msg, user_msg,
        timeout=300, temperature=0.3, num_predict=2048,
    )

    _save_advice(advice or "LLM returned an empty response.")


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


# ── Unsubscribe ─────────────────────────────────────────────────────────────

def get_unsubscribe_link(sender_email: str) -> dict:
    """Find the List-Unsubscribe header from the most recent message by this sender."""
    try:
        service = get_gmail_service()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Find most recent message from this sender
    result = service.users().messages().list(
        userId="me", q=f"from:{sender_email}", maxResults=1,
    ).execute()
    messages = result.get("messages", [])
    if not messages:
        return {"ok": False, "error": "No messages found from this sender"}

    msg = service.users().messages().get(
        userId="me", id=messages[0]["id"], format="metadata",
        metadataHeaders=["List-Unsubscribe", "List-Unsubscribe-Post"],
    ).execute()
    headers = msg.get("payload", {}).get("headers", [])
    unsub = _get_header(headers, "List-Unsubscribe")

    if not unsub:
        return {"ok": False, "error": "No unsubscribe link found for this sender"}

    # Extract URL (prefer https over mailto)
    import re
    urls = re.findall(r'<(https?://[^>]+)>', unsub)
    mailtos = re.findall(r'<(mailto:[^>]+)>', unsub)

    link = urls[0] if urls else (mailtos[0] if mailtos else unsub.strip('<> '))
    return {"ok": True, "link": link, "type": "url" if urls else "mailto"}


# ── Sender stats for UI ────────────────────────────────────────────────────

def get_sender_summary(email: str) -> dict:
    """Get triage stats for a specific sender."""
    stats = _load_sender_stats()
    s = stats.get(email.lower(), {})
    return {
        "archive": s.get("archive", 0),
        "delete": s.get("delete", 0),
        "keep": s.get("keep", 0),
    }


# ── Overview ─────────────────────────────────────────────────────────────────

def get_overview() -> dict:
    auth = get_auth_status()
    config = load_mail_config()
    scan_data = load_scan()
    recent = get_recent_actions(20)
    rules = load_rules()
    advice = get_advice()

    pending = [r for r in scan_data.get("results", []) if r.get("approved") is None]

    return {
        **auth,
        "config": config,
        "rules": rules,
        "rules_count": len(rules),
        "rules_enabled": sum(1 for r in rules if r.get("enabled", True)),
        "scan_ts": scan_data.get("ts"),
        "scan_results": scan_data.get("results", []),
        "pending_count": len(pending),
        "recent_actions": recent,
        "advice": advice,
        "llm_available": llm_query is not None and DEFAULT_MODEL is not None,
    }

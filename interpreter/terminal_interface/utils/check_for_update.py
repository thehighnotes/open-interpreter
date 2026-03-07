"""check_for_update — Git-based update checker for Open Interpreter.

Compares the local git HEAD against the remote origin. Supports auto-update
via config.json setting. Replaces the original PyPI-based checker.

Config (in ~/.config/hub/config.json):
    "oi_auto_update": true    — auto-pull on startup if behind
    "oi_auto_update": false   — just notify (default)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Cache: only check once per 6 hours
_CHECK_INTERVAL = 21600  # 6 hours
_CACHE_FILE = Path.home() / '.cache' / 'oi-update-check.json'


def _get_repo_dir():
    """Find the OI repo root (works for editable installs)."""
    # Walk up from this file to find .git
    d = Path(__file__).resolve().parent
    for _ in range(10):
        if (d / '.git').exists():
            return d
        d = d.parent
    return None


def _git(repo_dir, *args, timeout=10):
    """Run a git command in the repo. Returns (success, stdout)."""
    try:
        result = subprocess.run(
            ['git', '-C', str(repo_dir)] + list(args),
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, ''


def _load_cache():
    """Load cached check result. Returns dict or None."""
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text())
            import time
            if time.time() - data.get('ts', 0) < _CHECK_INTERVAL:
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _save_cache(behind, local_sha, remote_sha, commits_behind=0):
    """Cache the check result."""
    import time
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({
            'ts': time.time(),
            'behind': behind,
            'local': local_sha[:8] if local_sha else '',
            'remote': remote_sha[:8] if remote_sha else '',
            'commits_behind': commits_behind,
        }))
    except OSError:
        pass


def _load_oi_config():
    """Load hub config for auto-update setting."""
    config_file = Path.home() / '.config' / 'hub' / 'config.json'
    try:
        if config_file.exists():
            return json.loads(config_file.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def check_for_update():
    """Check if the local OI repo is behind origin.

    Returns True if an update is available, False otherwise.
    Side effect: if oi_auto_update is enabled, pulls automatically.
    """
    # Use cached result if fresh
    cached = _load_cache()
    if cached is not None:
        return cached.get('behind', False)

    repo_dir = _get_repo_dir()
    if not repo_dir:
        _save_cache(False, '', '')
        return False

    # Fetch latest from origin (quiet, no merge)
    ok, _ = _git(repo_dir, 'fetch', 'origin', '--quiet', timeout=15)
    if not ok:
        _save_cache(False, '', '')
        return False

    # Get current branch
    ok, branch = _git(repo_dir, 'rev-parse', '--abbrev-ref', 'HEAD')
    if not ok:
        branch = 'main'

    # Compare local vs remote
    ok, local_sha = _git(repo_dir, 'rev-parse', 'HEAD')
    ok2, remote_sha = _git(repo_dir, 'rev-parse', f'origin/{branch}')
    if not ok or not ok2:
        _save_cache(False, '', '')
        return False

    if local_sha == remote_sha:
        _save_cache(False, local_sha, remote_sha)
        return False

    # Count commits behind
    ok, count_str = _git(repo_dir, 'rev-list', '--count', f'HEAD..origin/{branch}')
    commits_behind = int(count_str) if ok and count_str.isdigit() else 0

    if commits_behind == 0:
        # Local is ahead or diverged, not behind
        _save_cache(False, local_sha, remote_sha)
        return False

    _save_cache(True, local_sha, remote_sha, commits_behind)

    # Auto-update if configured
    config = _load_oi_config()
    if config.get('oi_auto_update', False):
        return _do_update(repo_dir, branch, commits_behind)

    return True


def _do_update(repo_dir, branch, commits_behind):
    """Pull latest changes. Returns True if update succeeded (caller should notify)."""
    # Check for local modifications that would conflict
    ok, status = _git(repo_dir, 'status', '--porcelain')
    if ok and status:
        # Uncommitted changes — don't auto-update
        return True  # still report as "update available"

    ok, _ = _git(repo_dir, 'pull', '--ff-only', 'origin', branch, timeout=30)
    if ok:
        # Write a marker so the startup message can say "updated" instead of "available"
        try:
            marker = Path.home() / '.cache' / 'oi-just-updated'
            marker.write_text(str(commits_behind))
        except OSError:
            pass
        # Invalidate cache so next check is fresh
        try:
            _CACHE_FILE.unlink()
        except OSError:
            pass
        return False  # no longer behind
    return True  # update failed, still behind


def get_update_status():
    """Get human-readable update status. Returns (message, is_update) or (None, False)."""
    # Check if we just auto-updated
    marker = Path.home() / '.cache' / 'oi-just-updated'
    if marker.exists():
        try:
            count = marker.read_text().strip()
            marker.unlink()
            return f"Auto-updated ({count} commit{'s' if count != '1' else ''})", False
        except OSError:
            pass

    cached = _load_cache()
    if cached and cached.get('behind'):
        n = cached.get('commits_behind', 0)
        local = cached.get('local', '?')
        remote = cached.get('remote', '?')
        return (
            f"Update available: {n} commit{'s' if n != 1 else ''} behind "
            f"({local} -> {remote}). Run: cd {_get_repo_dir()} && git pull",
            True
        )

    return None, False

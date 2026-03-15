#!/usr/bin/env python3
"""install.py — Setup wizard for the hub tools ecosystem.

Single entry point for both hub and node installations. Handles SSH key
setup, config generation, OI installation, symlinks, and shell aliases.

Usage:
    python3 tools/hub/install.py          Full hub setup (default)
    python3 tools/hub/install.py --node   Node setup (connects to existing hub)
    python3 tools/hub/install.py --update Pull latest changes from origin
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / '.config' / 'hub'
CONFIG_FILE = CONFIG_DIR / 'config.json'
PROJECTS_FILE = CONFIG_DIR / 'projects.json'
DIAGRAMS_FILE = CONFIG_DIR / 'diagrams.json'

# All hub tools (files to symlink) — full hub install
HUB_TOOLS = [
    'hub', 'git', 'overview', 'research', 'backup', 'prepare',
    'begin', 'work', 'autosummary', 'notify', 'health-probe',
    'hubgrep', 'edit', 'search', 'code',
]

# Node tools — only tools that work with local files or are needed for session flow
NODE_TOOLS = ['edit', 'search', 'begin', 'work']

HUB_MODULE = 'hub_common.py'

# Detect where this script lives (tools/hub/ directory)
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent.parent  # open-interpreter root


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def print_header(title):
    w = max(len(title) + 6, 40)
    print(f"\n  {'=' * w}")
    print(f"  {'':>2}{title}")
    print(f"  {'=' * w}\n")


def print_step(n, total, msg):
    print(f"  [{n}/{total}] {msg}")


def print_ok(msg):
    print(f"  \033[32m+\033[0m {msg}")


def print_warn(msg):
    print(f"  \033[33m!\033[0m {msg}")


def print_fail(msg):
    print(f"  \033[31mx\033[0m {msg}")


def ask(prompt, default=''):
    """Prompt user for input with optional default."""
    if default:
        val = input(f"  {prompt} [{default}]: ").strip()
        return val or default
    return input(f"  {prompt}: ").strip()


def ask_yn(prompt, default=True):
    """Yes/no question."""
    suffix = '[Y/n]' if default else '[y/N]'
    val = input(f"  {prompt} {suffix}: ").strip().lower()
    if not val:
        return default
    return val in ('y', 'yes')


# ─────────────────────────────────────────────────────────────────────────────
# Detection helpers
# ─────────────────────────────────────────────────────────────────────────────

def detect_user():
    return os.environ.get('USER', 'user')


def detect_hostname():
    try:
        return subprocess.run(
            ['hostname', '-s'], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return 'hub'


def detect_shell():
    """Detect the user's login shell."""
    shell = os.environ.get('SHELL', '/bin/bash')
    return 'zsh' if 'zsh' in shell else 'bash'


def check_command(cmd):
    """Check if a command exists on PATH."""
    return shutil.which(cmd) is not None


def check_ssh_host(alias_or_ip):
    """Check if an SSH host is reachable via key-based auth."""
    try:
        result = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=3', '-o', 'BatchMode=yes',
             '-o', 'StrictHostKeyChecking=no', alias_or_ip, 'echo ok'],
            capture_output=True, text=True, timeout=8
        )
        return result.returncode == 0
    except Exception:
        return False


def check_ollama_local():
    """Check if Ollama is running locally."""
    try:
        result = subprocess.run(
            ['curl', '-s', '--max-time', '2', 'http://localhost:11434/api/tags'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            models = [m['name'] for m in data.get('models', [])]
            return True, models
    except Exception:
        pass
    return False, []


def git_cmd(*args, timeout=15):
    """Run a git command in the OI repo. Returns (success, stdout)."""
    try:
        result = subprocess.run(
            ['git', '-C', str(REPO_DIR)] + list(args),
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip()
    except Exception:
        return False, ''


# ─────────────────────────────────────────────────────────────────────────────
# SSH key setup
# ─────────────────────────────────────────────────────────────────────────────

def setup_ssh_key():
    """Ensure an SSH key exists. Generate one if not."""
    ssh_dir = HOME / '.ssh'
    key_file = ssh_dir / 'id_ed25519'

    if key_file.exists():
        print_ok(f"SSH key exists: {key_file}")
        return key_file

    print("  No SSH key found. Generating one...")
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    result = subprocess.run(
        ['ssh-keygen', '-t', 'ed25519', '-f', str(key_file), '-N', '',
         '-C', f'{detect_user()}@{detect_hostname()}'],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print_ok(f"SSH key generated: {key_file}")
        return key_file
    else:
        print_fail("Failed to generate SSH key")
        return None


def setup_ssh_connection(alias, ip, user):
    """Set up SSH config entry and copy key to remote host.
    Returns True if connection works."""
    ssh_config = HOME / '.ssh' / 'config'
    key_file = HOME / '.ssh' / 'id_ed25519'

    # Check if alias already exists in SSH config
    alias_exists = False
    if ssh_config.exists():
        content = ssh_config.read_text()
        # Match "Host <alias>" as a standalone entry
        for line in content.split('\n'):
            if line.strip().lower() == f'host {alias}':
                alias_exists = True
                break

    if not alias_exists:
        # Add SSH config entry
        entry = (
            f'\nHost {alias}\n'
            f'    HostName {ip}\n'
            f'    User {user}\n'
            f'    IdentityFile ~/.ssh/id_ed25519\n'
        )
        ssh_config.parent.mkdir(mode=0o700, exist_ok=True)
        with open(ssh_config, 'a') as f:
            f.write(entry)
        # Ensure proper permissions
        ssh_config.chmod(0o600)
        print_ok(f"Added '{alias}' to ~/.ssh/config")
    else:
        print_ok(f"SSH config entry '{alias}' already exists")

    # Test connection with key-based auth
    print(f"  Testing SSH to {alias}...", end='', flush=True)
    if check_ssh_host(alias):
        print(" connected")
        return True

    # Connection failed — try ssh-copy-id
    print(" key not authorized")
    print(f"  Copying SSH key to {alias}...")
    print(f"  (you may be prompted for {user}@{ip}'s password)")
    result = subprocess.run(
        ['ssh-copy-id', '-i', str(key_file), f'{user}@{ip}'],
        timeout=30
    )
    if result.returncode == 0:
        # Verify
        if check_ssh_host(alias):
            print_ok(f"SSH key authorized on {alias}")
            return True
    print_fail(f"Could not establish SSH to {alias}")
    print(f"  You can set this up manually later:")
    print(f"    ssh-copy-id -i {key_file} {user}@{ip}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Symlinks & shell integration
# ─────────────────────────────────────────────────────────────────────────────

def create_symlinks(tools=None):
    """Create ~/tool -> tools/hub/tool symlinks."""
    if tools is None:
        tools = HUB_TOOLS

    created = 0
    skipped = 0

    # Module symlink
    target = SCRIPT_DIR / HUB_MODULE
    link = HOME / HUB_MODULE
    if link.exists() or link.is_symlink():
        if link.is_symlink() and link.resolve() == target.resolve():
            skipped += 1
        else:
            skipped += 1
    else:
        link.symlink_to(target)
        created += 1

    # Tool symlinks
    for tool in tools:
        target = SCRIPT_DIR / tool
        link = HOME / tool
        if not target.exists():
            skipped += 1
            continue
        if link.exists() or link.is_symlink():
            if link.is_symlink() and link.resolve() == target.resolve():
                skipped += 1
            else:
                skipped += 1
        else:
            link.symlink_to(target)
            created += 1

    print_ok(f"{created + skipped} tools linked ({created} new)")
    return created


def setup_shell_aliases(tools=None):
    """Add shell aliases to .bashrc or .zshrc."""
    if tools is None:
        tools = HUB_TOOLS
    shell = detect_shell()
    rc_file = HOME / (f'.{shell}rc')
    marker = '# --- Hub Tools ---'
    end_marker = '# --- End Hub Tools ---'

    # Build alias block
    # Skip 'git' — would shadow system git; aliased as 'repo' instead
    skip_alias = {'git'}
    alias_lines = [
        f'\n{marker}',
        'export PATH="$HOME/.local/bin:$PATH"',
    ]
    for tool in tools:
        if tool in skip_alias:
            continue
        tool_path = HOME / tool
        if tool_path.exists() or tool_path.is_symlink():
            # Use underscore alias for tools with hyphens
            alias_name = tool.replace('-', '_')
            alias_lines.append(f"alias {alias_name}='~/{tool}'")
    alias_lines.append("alias repo='~/git'")
    alias_lines.append("alias status='~/hub --status'")
    alias_lines.append(end_marker)
    alias_block = '\n'.join(alias_lines) + '\n'

    if rc_file.exists():
        content = rc_file.read_text()
        if marker in content:
            start = content.index(marker)
            if end_marker in content:
                # New format — replace between markers
                end = content.index(end_marker) + len(end_marker)
            else:
                # Old format without end marker — find end of alias block
                # Scan forward from marker: skip lines that are aliases/exports/blank
                lines = content[start:].split('\n')
                block_len = 0
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if i == 0:  # the marker line itself
                        block_len += len(line) + 1
                        continue
                    if stripped.startswith('alias ') or stripped.startswith('export ') or stripped == '':
                        block_len += len(line) + 1
                    else:
                        break
                end = start + block_len
            # Include trailing newline if present
            if end < len(content) and content[end] == '\n':
                end += 1
            new_content = content[:start] + alias_block
            if end < len(content):
                new_content += content[end:]
            rc_file.write_text(new_content)
            print_ok(f"Shell aliases updated in ~/{rc_file.name}")
            return

    # Append new block
    with open(rc_file, 'a') as f:
        f.write(alias_block)
    print_ok(f"Aliases added to ~/{rc_file.name}")


def create_ssh_stubs(hub_alias, tools=None):
    """Create SSH stub scripts for hub-only tools on a node."""
    if tools is None:
        tools = [t for t in HUB_TOOLS if t not in NODE_TOOLS]

    created = 0
    for tool in tools:
        stub_path = HOME / tool
        if stub_path.exists() or stub_path.is_symlink():
            continue
        stub_path.write_text(
            f'#!/bin/bash\n'
            f'# Stub: delegates to hub ({hub_alias})\n'
            f'exec ssh -t -o ConnectTimeout=5 {hub_alias} "~/{tool} $*"\n'
        )
        stub_path.chmod(0o755)
        created += 1
    print_ok(f"{created} SSH stubs created for hub-only tools")


# ─────────────────────────────────────────────────────────────────────────────
# OI installation
# ─────────────────────────────────────────────────────────────────────────────

def check_oi_installed():
    """Check if Open Interpreter is installed."""
    return check_command('interpreter')


def check_pyte_installed():
    """Check if pyte is installed (required for terminal output processing)."""
    try:
        result = subprocess.run(
            ['python3', '-c', 'import pyte'],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def install_pyte():
    """Install pyte if missing."""
    if check_pyte_installed():
        print_ok("pyte already installed")
        return True
    print("  Installing pyte (terminal emulator, required by OI)...")
    result = subprocess.run(
        ['pip', 'install', 'pyte'],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode == 0:
        print_ok("pyte installed")
        return True
    print_fail("Failed to install pyte — run manually: pip install pyte")
    return False


def install_oi():
    """Install OI in editable mode from the repo."""
    print("  Installing Open Interpreter (editable mode)...")
    print("  This requires the following command to be run:")
    print(f"    cd {REPO_DIR} && pip install -e .")
    print()
    if ask_yn("Run this now?", default=True):
        result = subprocess.run(
            ['pip', 'install', '-e', '.'],
            cwd=str(REPO_DIR),
            timeout=300
        )
        if result.returncode == 0:
            print_ok("Open Interpreter installed")
            install_pyte()
            return True
        else:
            print_fail("Installation failed — run manually: pip install -e .")
            return False
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Update
# ─────────────────────────────────────────────────────────────────────────────

def _refresh_tools():
    """Re-run symlinks, SSH stubs, and aliases for the current role."""
    role = 'hub'
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            role = cfg.get('hub', {}).get('role', 'hub')
        except Exception:
            pass

    symlink_tools = NODE_TOOLS if role == 'node' else HUB_TOOLS
    create_symlinks(tools=symlink_tools)
    if role == 'node':
        hub_host = ''
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            hub_host = cfg.get('hub', {}).get('hub_host', '')
        except Exception:
            pass
        if hub_host:
            create_ssh_stubs(hub_host, tools=[t for t in HUB_TOOLS if t not in NODE_TOOLS])
    # Aliases for all tools — on nodes, hub-only tools are SSH stubs
    setup_shell_aliases(tools=HUB_TOOLS)


def do_update():
    """Pull latest changes from origin and report what changed."""
    print_header("Open Interpreter — Update")

    ok, branch = git_cmd('rev-parse', '--abbrev-ref', 'HEAD')
    if not ok:
        print_fail("Not in a git repository")
        return False

    ok, local_before = git_cmd('rev-parse', '--short', 'HEAD')

    # Check for dirty working tree
    ok, status = git_cmd('status', '--porcelain')
    if ok and status:
        print_warn("Uncommitted changes detected:")
        for line in status.split('\n')[:5]:
            print(f"    {line}")
        if not ask_yn("Stash changes and continue?", default=False):
            print("  Update cancelled")
            return False
        git_cmd('stash', 'push', '-m', 'auto-stash before update')
        stashed = True
    else:
        stashed = False

    # Fetch
    print("  Fetching from origin...", end='', flush=True)
    ok, _ = git_cmd('fetch', 'origin', timeout=20)
    if not ok:
        print(" failed")
        print_fail("Could not reach origin — check your network")
        return False
    print(" done")

    # Check if behind
    ok, count = git_cmd('rev-list', '--count', f'HEAD..origin/{branch}')
    commits_behind = int(count) if ok and count.isdigit() else 0

    if commits_behind == 0:
        print_ok(f"Already up to date on {branch} ({local_before})")
        if stashed:
            git_cmd('stash', 'pop')
        _refresh_tools()
        return True

    # Show what's incoming
    ok, log = git_cmd('log', '--oneline', f'HEAD..origin/{branch}')
    if ok and log:
        print(f"\n  {commits_behind} new commit(s):")
        for line in log.split('\n')[:10]:
            print(f"    {line}")
        if commits_behind > 10:
            print(f"    ... and {commits_behind - 10} more")
        print()

    # Pull
    ok, _ = git_cmd('pull', '--ff-only', 'origin', branch, timeout=30)
    if ok:
        ok, local_after = git_cmd('rev-parse', '--short', 'HEAD')
        print_ok(f"Updated: {local_before} -> {local_after} ({commits_behind} commits)")
    else:
        print_fail("Pull failed — may need manual merge")
        if stashed:
            git_cmd('stash', 'pop')
        return False

    if stashed:
        ok, _ = git_cmd('stash', 'pop')
        if ok:
            print_ok("Stashed changes restored")
        else:
            print_warn("Could not restore stash — run: git stash pop")

    _refresh_tools()
    print_ok("Update complete")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Hub setup (full)
# ─────────────────────────────────────────────────────────────────────────────

def setup_hosts():
    """Interactive host configuration. Returns hosts dict."""
    user = detect_user()
    hostname = detect_hostname()

    print(f"  Detected: user={user}, hostname={hostname}")
    print()

    local_key = ask("Local host alias (used in config)", hostname)
    hosts = {
        local_key: {
            'name': ask("Local host display name", hostname.capitalize()),
            'ip': '127.0.0.1',
            'user': user,
            'roles': ['local'],
        }
    }

    print()
    while ask_yn("Add a remote host?", default=False):
        print()
        key = ask("Host alias (e.g. 'agx', 'gpu', 'server')")
        if not key:
            continue
        ip = ask("IP address")
        remote_user = ask("SSH user")
        hosts[key] = {
            'name': ask("Display name", key.upper()),
            'ip': ip,
            'user': remote_user,
            'roles': [],
        }

        # Set up SSH connection
        setup_ssh_connection(key, ip, remote_user)

        # Role detection
        roles = []
        if ask_yn(f"  Does {key} run Ollama?", default=False):
            roles.append('ollama')
        if ask_yn(f"  Does {key} run Code Assistant?", default=False):
            roles.append('code_assistant')
        if ask_yn(f"  Use {key} as backup target?", default=False):
            roles.append('backup_target')
        if ask_yn(f"  Is {key} wakeable via WoL?", default=False):
            roles.append('wakeable')
            mac = ask("  WoL MAC address")
            if mac:
                hosts[key]['wol_mac'] = mac
        hosts[key]['roles'] = roles
        print()

    return hosts, local_key


def setup_ollama(hosts, local_key):
    """Configure Ollama settings."""
    ollama_hosts = [k for k, h in hosts.items() if 'ollama' in h.get('roles', [])]

    if not ollama_hosts:
        local_ok, models = check_ollama_local()
        if local_ok:
            print_ok(f"Ollama running locally: {', '.join(models[:5]) or 'none'}")
            hosts[local_key]['roles'].append('ollama')
            ollama_host = local_key
        else:
            print_warn("No Ollama host — LLM features disabled. Edit config.json later.")
            return {'host': local_key, 'port': 11434, 'default_model': 'llama3.2:3b'}
    elif len(ollama_hosts) == 1:
        ollama_host = ollama_hosts[0]
    else:
        ollama_host = ask(f"Which host runs Ollama? ({', '.join(ollama_hosts)})", ollama_hosts[0])

    port = int(ask("Ollama port", "11434"))
    model = ask("Default model", "llama3.2:3b")
    return {'host': ollama_host, 'port': port, 'default_model': model}


def setup_git_config():
    """Configure git settings."""
    username = ask("GitHub username", detect_user())
    email = ask("Git email", f"{username}@users.noreply.github.com")
    return {'github_username': username, 'email': email}


def setup_backup(hosts, local_key):
    backup_hosts = [k for k, h in hosts.items() if 'backup_target' in h.get('roles', [])]
    if backup_hosts:
        dest = ask("Backup destination", f"{backup_hosts[0]}:~/hub-backup")
    else:
        dest = ask("Backup destination", "~/hub-backup")
    return {'destination': dest}


def setup_code_assistant(hosts, local_key):
    ca_hosts = [k for k, h in hosts.items() if 'code_assistant' in h.get('roles', [])]
    ca_host = ca_hosts[0] if ca_hosts else local_key
    return {'host': ca_host, 'port': 5002}


def setup_hub():
    """Full hub installation wizard."""
    print_header("Hub Tools — Setup")
    print(f"  Config: {CONFIG_FILE}")
    print()

    total_steps = 7

    if CONFIG_FILE.exists():
        if not ask_yn("config.json already exists. Overwrite?", default=False):
            print("\n  Keeping existing config. Running link setup only.")
            create_symlinks()
            return

    # Step 1: SSH key
    print_step(1, total_steps, "SSH key")
    setup_ssh_key()

    # Step 2: Hosts
    print()
    print_step(2, total_steps, "Host configuration")
    hosts, local_key = setup_hosts()

    # Step 3: Ollama
    print()
    print_step(3, total_steps, "Ollama")
    ollama = setup_ollama(hosts, local_key)

    # Step 4: Git
    print()
    print_step(4, total_steps, "Git")
    git = setup_git_config()

    # Step 5: Write config
    print()
    print_step(5, total_steps, "Writing config")
    backup = setup_backup(hosts, local_key)
    code_assistant = setup_code_assistant(hosts, local_key)

    config = {
        'hub': {
            'name': ask("Hub name", "My Dev Hub"),
            'local_host': local_key,
            'role': 'hub',
        },
        'hosts': hosts,
        'ollama': ollama,
        'code_assistant': code_assistant,
        'backup': backup,
        'git': git,
    }

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print_ok(f"Config written to {CONFIG_FILE}")

    if not PROJECTS_FILE.exists():
        with open(PROJECTS_FILE, 'w') as f:
            json.dump({'projects': {}, 'order': []}, f, indent=2)
            f.write('\n')
    if not DIAGRAMS_FILE.exists():
        with open(DIAGRAMS_FILE, 'w') as f:
            f.write('{}\n')

    # Step 6: Symlinks + shell
    print()
    print_step(6, total_steps, "Tools & shell")
    create_symlinks()
    setup_shell_aliases()

    # Step 7: OI installation check
    print()
    print_step(7, total_steps, "Open Interpreter")
    if check_oi_installed():
        print_ok("Open Interpreter already installed")
        install_pyte()
    else:
        install_oi()

    # Done
    shell = detect_shell()
    print_header("Setup Complete")
    print("  Next steps:")
    print(f"    source ~/.{shell}rc")
    print("    hub --status")
    print("    hub --scan <host>")
    print("    work <project>")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Node setup
# ─────────────────────────────────────────────────────────────────────────────

def setup_node():
    """Node installation wizard — connects to an existing hub."""
    print_header("Hub Tools — Node Setup")
    print("  A node runs OI locally for native file access.")
    print("  State (overview, research, timeline) stays on the hub.")
    print()

    total_steps = 6
    user = detect_user()
    hostname = detect_hostname()

    # Step 1: SSH key
    print_step(1, total_steps, "SSH key")
    key_file = setup_ssh_key()

    # Step 2: Hub connection
    print()
    print_step(2, total_steps, "Connect to hub")
    hub_ip = ask("Hub IP address")
    hub_user = ask("Hub SSH user")
    hub_alias = ask("Hub SSH alias", "nano")

    connected = setup_ssh_connection(hub_alias, hub_ip, hub_user)
    if not connected:
        if not ask_yn("Continue without hub connection?", default=False):
            sys.exit(1)

    # Step 3: Fetch config from hub and identify this node
    print()
    print_step(3, total_steps, "Sync from hub")

    hub_config = None
    hub_projects = None
    if connected:
        try:
            result = subprocess.run(
                ['ssh', '-o', 'BatchMode=yes', hub_alias,
                 'cat ~/.config/hub/config.json'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                hub_config = json.loads(result.stdout)
                print_ok("Hub config fetched")
        except Exception:
            pass

        try:
            result = subprocess.run(
                ['ssh', '-o', 'BatchMode=yes', hub_alias,
                 'cat ~/.config/hub/projects.json'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                hub_projects = json.loads(result.stdout)
                n = len(hub_projects.get('projects', {}))
                print_ok(f"{n} project(s) synced from hub")
        except Exception:
            pass

    if not hub_config:
        print_warn("Could not fetch hub config — using defaults")
        hub_config = {}

    # Try to auto-detect this node from the hub's hosts list
    hosts = hub_config.get('hosts', {})
    local_key = None
    local_name = None

    if hosts:
        # Skip the hub's own local_host entry — we're looking for *this* machine
        hub_local = hub_config.get('hub', {}).get('local_host', '')
        candidates = {k: v for k, v in hosts.items() if k != hub_local}

        # Match by username first (most reliable), then hostname
        for key, info in candidates.items():
            if info.get('user') == user:
                local_key = key
                local_name = info.get('name', key)
                break
        if not local_key:
            for key, info in candidates.items():
                if key == hostname or info.get('name', '').lower() == hostname.lower():
                    local_key = key
                    local_name = info.get('name', key)
                    break

    if local_key:
        print_ok(f"Matched hub host: {local_key} — {local_name} ({hosts[local_key].get('ip', '?')}, {user})")
        # Preserve the hub's entry but mark as local
        hub_entry = hosts[local_key]
        hub_entry['roles'] = list(set(hub_entry.get('roles', [])) | {'local'})
        hub_entry['ip'] = '127.0.0.1'
        hub_entry['user'] = user
    else:
        print_warn("Could not auto-detect this machine in hub's hosts")
        local_key = ask("This node's alias", hostname)
        local_name = ask("Display name", hostname.capitalize())
        hosts[local_key] = {
            'name': local_name, 'ip': '127.0.0.1',
            'user': user, 'roles': ['local'],
        }

    config = {
        'hub': {
            'name': hub_config.get('hub', {}).get('name', 'Dev Hub'),
            'local_host': local_key,
            'role': 'node',
            'hub_host': hub_alias,
        },
        'hosts': hosts,
        'ollama': hub_config.get('ollama', {
            'host': hub_alias, 'port': 11434, 'default_model': 'llama3.2:3b'
        }),
        'code_assistant': hub_config.get('code_assistant', {
            'host': hub_alias, 'port': 5002
        }),
        'backup': hub_config.get('backup', {'destination': '~/hub-backup'}),
        'git': hub_config.get('git', {'github_username': '', 'email': ''}),
        'oi_auto_update': hub_config.get('oi_auto_update', True),
    }

    # Step 4: Write config
    print()
    print_step(4, total_steps, "Writing config")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print_ok(f"Config written to {CONFIG_FILE}")

    if hub_projects:
        with open(PROJECTS_FILE, 'w') as f:
            json.dump(hub_projects, f, indent=2)
            f.write('\n')
    elif not PROJECTS_FILE.exists():
        with open(PROJECTS_FILE, 'w') as f:
            json.dump({'projects': {}, 'order': []}, f, indent=2)
            f.write('\n')

    # Step 5: Tools
    print()
    print_step(5, total_steps, "Tools & shell")
    create_symlinks(tools=NODE_TOOLS)
    create_ssh_stubs(hub_alias)
    # Aliases for all tools — node tools are symlinks, hub-only tools are SSH stubs
    setup_shell_aliases(tools=HUB_TOOLS)

    # OI profile
    profile_src = SCRIPT_DIR / 'profiles' / 'hub-profile.example.py'
    profile_dst = HOME / '.config' / 'open-interpreter' / 'profiles' / 'linux-admin.py'
    if not profile_dst.exists() and profile_src.exists():
        profile_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(profile_src, profile_dst)
        print_ok("OI profile installed")
    elif profile_dst.exists():
        print_ok("OI profile already exists")

    # Step 6: OI installation
    print()
    print_step(6, total_steps, "Open Interpreter")
    if check_oi_installed():
        print_ok("Open Interpreter already installed")
        install_pyte()
    else:
        install_oi()

    # Done
    shell = detect_shell()
    print_header("Node Setup Complete")
    print("  This machine is now a node. Available:")
    print(f"    work <project> --oi    OI with native file access")
    print(f"    ~/edit <file>          Edit files directly")
    print(f"    ~/hub --status         Status (via hub)")
    print()
    print("  Next steps:")
    print(f"    source ~/.{shell}rc")
    if not connected:
        print(f"    ssh-copy-id {hub_user}@{hub_ip}")
    print(f"    work <project> --oi")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap script generator (for curl | bash install)
# ─────────────────────────────────────────────────────────────────────────────

def print_bootstrap_script():
    """Output a bootstrap script that can be piped from curl."""
    # This is for reference — the actual script would be hosted
    script = '''#!/bin/bash
set -e

REPO_URL="${OI_REPO:-https://github.com/thehighnotes/open-interpreter.git}"
INSTALL_DIR="${OI_DIR:-$HOME/projects/open-interpreter}"
MODE="${1:-hub}"  # hub or node

echo ""
echo "  Open Interpreter — Hub Tools Installer"
echo ""

# Check prerequisites
command -v git >/dev/null 2>&1 || { echo "  git is required. Install it first."; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "  python3 is required. Install it first."; exit 1; }
command -v pip >/dev/null 2>&1 && PIP=pip || PIP=pip3

# Clone or update repo
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Updating existing installation..."
    git -C "$INSTALL_DIR" pull --ff-only origin main 2>/dev/null || true
else
    echo "  Cloning repository..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# Install OI
echo "  Installing Open Interpreter..."
cd "$INSTALL_DIR"
$PIP install -e . --quiet

# Run setup wizard
if [ "$MODE" = "node" ]; then
    python3 tools/hub/install.py --node
else
    python3 tools/hub/install.py
fi
'''
    print(script)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if '--bootstrap-script' in args:
        print_bootstrap_script()
        return

    if '--update' in args:
        success = do_update()
        sys.exit(0 if success else 1)

    if '--node' in args:
        setup_node()
    else:
        setup_hub()


if __name__ == '__main__':
    main()

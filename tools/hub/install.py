#!/usr/bin/env python3
"""install.py — Interactive setup wizard for the hub tools ecosystem.

Configures hosts, Ollama, GitHub, and backup settings, then creates
symlinks so hub tools are available from ~/. Run once after cloning.

Usage:
    python3 tools/hub/install.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / '.config' / 'hub'
CONFIG_FILE = CONFIG_DIR / 'config.json'
PROJECTS_FILE = CONFIG_DIR / 'projects.json'
DIAGRAMS_FILE = CONFIG_DIR / 'diagrams.json'

# All hub tools (files to symlink)
HUB_TOOLS = [
    'hub', 'git', 'overview', 'research', 'backup', 'prepare',
    'begin', 'work', 'autosummary', 'notify', 'health-probe',
    'hubgrep', 'edit', 'search', 'code',
]
HUB_MODULE = 'hub_common.py'

# Detect where this script lives (tools/hub/ directory)
SCRIPT_DIR = Path(__file__).resolve().parent


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


def detect_user():
    """Detect current username."""
    return os.environ.get('USER', 'user')


def detect_hostname():
    """Detect current hostname."""
    try:
        return subprocess.run(['hostname', '-s'], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return 'hub'


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


def check_ssh_host(alias_or_ip):
    """Check if an SSH host is reachable."""
    try:
        result = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=3', '-o', 'BatchMode=yes',
             '-o', 'StrictHostKeyChecking=no', alias_or_ip, 'echo ok'],
            capture_output=True, text=True, timeout=8
        )
        return result.returncode == 0
    except Exception:
        return False


def setup_hosts():
    """Interactive host configuration. Returns hosts dict."""
    user = detect_user()
    hostname = detect_hostname()

    print()
    print("  === Host Configuration ===")
    print()
    print(f"  Detected: user={user}, hostname={hostname}")
    print()

    # Local host is always present
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
        hosts[key] = {
            'name': ask("Display name", key.upper()),
            'ip': ask("IP address"),
            'user': ask("SSH user"),
            'roles': [],
        }

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

        # Test connectivity
        print(f"  Testing SSH to {key}...", end='', flush=True)
        if check_ssh_host(key):
            print(" OK")
        else:
            print(f" FAILED (ensure SSH alias '{key}' is configured in ~/.ssh/config)")

        print()

    return hosts, local_key


def setup_ollama(hosts, local_key):
    """Configure Ollama settings. Returns ollama config dict."""
    print()
    print("  === Ollama Configuration ===")
    print()

    # Find hosts with Ollama role
    ollama_hosts = [k for k, h in hosts.items() if 'ollama' in h.get('roles', [])]

    if not ollama_hosts:
        # Check if running locally
        local_ok, models = check_ollama_local()
        if local_ok:
            print(f"  Found Ollama running locally with models: {', '.join(models[:5]) or 'none'}")
            hosts[local_key]['roles'].append('ollama')
            ollama_host = local_key
        else:
            print("  No Ollama host configured. Hub tools will work without LLM features.")
            print("  You can add an Ollama host later by editing config.json.")
            return {'host': local_key, 'port': 11434, 'default_model': 'llama3.2:3b'}
    elif len(ollama_hosts) == 1:
        ollama_host = ollama_hosts[0]
        print(f"  Ollama host: {ollama_host}")
    else:
        ollama_host = ask(f"Which host runs Ollama? ({', '.join(ollama_hosts)})", ollama_hosts[0])

    port = int(ask("Ollama port", "11434"))
    model = ask("Default model", "llama3.2:3b")

    return {'host': ollama_host, 'port': port, 'default_model': model}


def setup_git():
    """Configure git settings. Returns git config dict."""
    print()
    print("  === Git Configuration ===")
    print()

    username = ask("GitHub username", detect_user())
    email = ask("Git email", f"{username}@users.noreply.github.com")

    return {'github_username': username, 'email': email}


def setup_backup(hosts, local_key):
    """Configure backup settings. Returns backup config dict."""
    print()
    print("  === Backup Configuration ===")
    print()

    backup_hosts = [k for k, h in hosts.items() if 'backup_target' in h.get('roles', [])]

    if backup_hosts:
        bhost = backup_hosts[0]
        dest = ask("Backup destination", f"{bhost}:~/hub-backup")
    else:
        dest = ask("Backup destination (host:path or local path)", "~/hub-backup")

    return {'destination': dest}


def setup_code_assistant(hosts, local_key):
    """Configure Code Assistant. Returns config dict."""
    ca_hosts = [k for k, h in hosts.items() if 'code_assistant' in h.get('roles', [])]

    if ca_hosts:
        ca_host = ca_hosts[0]
    else:
        ca_host = local_key

    port = 5002
    return {'host': ca_host, 'port': port}


def create_symlinks():
    """Create ~/tool → tools/hub/tool symlinks."""
    print()
    print("  === Creating Symlinks ===")
    print()

    created = 0
    skipped = 0

    # Module symlink
    target = SCRIPT_DIR / HUB_MODULE
    link = HOME / HUB_MODULE
    if link.exists() or link.is_symlink():
        if link.is_symlink() and link.resolve() == target.resolve():
            print(f"  {HUB_MODULE}: already linked")
            skipped += 1
        else:
            print(f"  {HUB_MODULE}: exists (not overwriting)")
            skipped += 1
    else:
        link.symlink_to(target)
        print(f"  {HUB_MODULE}: linked")
        created += 1

    # Tool symlinks
    for tool in HUB_TOOLS:
        target = SCRIPT_DIR / tool
        link = HOME / tool
        if not target.exists():
            print(f"  {tool}: source missing, skipped")
            skipped += 1
            continue
        if link.exists() or link.is_symlink():
            if link.is_symlink() and link.resolve() == target.resolve():
                print(f"  {tool}: already linked")
                skipped += 1
            else:
                print(f"  {tool}: exists (not overwriting)")
                skipped += 1
        else:
            link.symlink_to(target)
            print(f"  {tool}: linked")
            created += 1

    print(f"\n  {created} symlinks created, {skipped} skipped")
    return created


def setup_bash_aliases():
    """Offer to add bash aliases."""
    print()
    if not ask_yn("Add bash aliases (repo, hub, status) to .bashrc?", default=True):
        return

    bashrc = HOME / '.bashrc'
    marker = '# --- Hub Tools ---'

    if bashrc.exists() and marker in bashrc.read_text():
        print("  Aliases already present in .bashrc")
        return

    aliases = f"""
{marker}
alias repo='~/git'
alias hub='~/hub'
alias status='~/hub --status'
"""

    with open(bashrc, 'a') as f:
        f.write(aliases)
    print("  Added aliases to .bashrc (source it or open a new terminal)")


def main():
    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║     Hub Tools — Setup Wizard         ║")
    print("  ╚══════════════════════════════════════╝")
    print()
    print(f"  This wizard configures the hub ecosystem for your setup.")
    print(f"  Config will be written to: {CONFIG_FILE}")
    print()

    if CONFIG_FILE.exists():
        if not ask_yn("config.json already exists. Overwrite?", default=False):
            print("\n  Keeping existing config. Running symlink setup only.")
            create_symlinks()
            return

    # Interactive setup
    hosts, local_key = setup_hosts()
    ollama = setup_ollama(hosts, local_key)
    git = setup_git()
    backup = setup_backup(hosts, local_key)
    code_assistant = setup_code_assistant(hosts, local_key)

    # Build config
    config = {
        'hub': {
            'name': ask("\nHub name (for display)", "My Dev Hub"),
            'local_host': local_key,
        },
        'hosts': hosts,
        'ollama': ollama,
        'code_assistant': code_assistant,
        'backup': backup,
        'git': git,
    }

    # Write config
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')
    print(f"\n  Config written to {CONFIG_FILE}")

    # Create empty projects.json if not exists
    if not PROJECTS_FILE.exists():
        with open(PROJECTS_FILE, 'w') as f:
            json.dump({'projects': {}, 'order': []}, f, indent=2)
            f.write('\n')
        print(f"  Empty projects.json created at {PROJECTS_FILE}")
        print("  Use 'hub --scan <host>' to discover projects after setup.")

    # Create empty diagrams.json if not exists
    if not DIAGRAMS_FILE.exists():
        with open(DIAGRAMS_FILE, 'w') as f:
            f.write('{}\n')
        print(f"  Empty diagrams.json created at {DIAGRAMS_FILE}")

    # Symlinks
    create_symlinks()

    # Bash aliases
    setup_bash_aliases()

    # Summary
    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║     Setup Complete                   ║")
    print("  ╚══════════════════════════════════════╝")
    print()
    print("  Next steps:")
    print("    1. Source your shell:  source ~/.bashrc")
    print("    2. Check status:      hub --status")
    print("    3. Discover projects: hub --scan <host>")
    print("    4. Start working:     work <project>")
    print()


if __name__ == '__main__':
    main()

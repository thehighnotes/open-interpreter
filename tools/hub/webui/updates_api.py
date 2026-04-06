"""updates_api — LLM-driven update management for OI-web.

Clustered pipeline:
  Stage 1: Gather — apt/pip scanners with source tags and dependency data
  Stage 2: Cluster — group by influence (static definitions + dynamic dep graph)
  Stage 3: Context — enrich clusters with project/service/host info
  Stage 4: Blocklist/Rules — filter known items
  Stage 5: Save — persist scan + clusters
  Stage 6: LLM Analysis — separate endpoint, per-cluster with full context
  Stage 7: Intelligence — on-demand per cluster (changelogs, CVEs)

Nothing runs sudo. Nothing auto-applies without explicit approval.
"""

import fnmatch
import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path.home()))
try:
    from hub_common import (
        ssh_cmd, run_parallel, llm_query, llm_query_text,
        DEFAULT_MODEL, HOSTS, LOCAL_HOST, PROJECTS,
    )
except ImportError:
    ssh_cmd = run_parallel = llm_query = llm_query_text = None
    DEFAULT_MODEL = None
    HOSTS = {}
    LOCAL_HOST = "nano"
    PROJECTS = {}


# ── Paths ────────────────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "hub"
CACHE_DIR = Path.home() / ".cache" / "updates"
UPDATES_CONFIG_FILE = CONFIG_DIR / "updates-config.json"
UPDATES_RULES_FILE = CONFIG_DIR / "updates-rules.json"
SCAN_FILE = CACHE_DIR / "scan-results.json"
ACTIONS_LOG = CACHE_DIR / "actions.jsonl"
INSIGHTS_FILE = CACHE_DIR / "insights.json"


# ── Default config ───────────────────────────────────────────────────────────

DEFAULT_CLUSTER_PROMPT = """\
You are analyzing the "{cluster_name}" update cluster on {host_names}.
Host roles: {host_roles}. Affected projects: {projects}.
Risk score: {risk_score} (total reverse dependencies across this cluster).

For each package you are given: release notes AND actual import usage from the projects on this host. \
If a package is NOT IMPORTED by any project, it is safe to update — classify as "noise" and recommend "apply". \
Only use "defer" when there is a specific reason to wait (breaking API change that affects imported code, \
known incompatibility, or a major version bump on a package that IS actively used). \
If a package IS imported, check whether the specific APIs/classes used are affected by the changes. \
Reference actual API changes, removed features, new capabilities, or migration requirements.

Respond with ONLY a JSON object — no markdown, no explanation, no code fences:
{{"risk_level": "critical"|"moderate"|"low", \
"recommendation": "apply"|"defer"|"investigate", \
"update_order": ["pkg1", "pkg2"], \
"reasoning": "specific explanation citing actual changes from the release notes", \
"breaking_changes": ["specific breaking change 1", "specific breaking change 2"], \
"new_features": ["relevant new feature 1"], \
"items": {{"package_name": {{"classification": "urgent"|"review"|"noise", "reason": "what THIS package's update specifically changes"}}}}}}

Context: Jetson hosts have custom CUDA/torch (excluded). vLLM on AGX serves all LLM traffic. \
FastAPI/starlette runs the hub control plane and LLM proxy."""

DEFAULT_CROSSREF_PROMPT = """\
You are reviewing pending updates across a multi-host dev environment. \
Identify any conflicts, coordination needs, or risks:
1. Package X on host A depends on package Y on host B
2. Major version bumps that break downstream consumers
3. Updates that should be applied together or in a specific order

Be specific. Reference package names and hosts. Keep it brief. Use markdown."""

DEFAULT_CLUSTER_DEFS = {
    "vllm_core": {
        "name": "vLLM Core",
        "packages": ["vllm", "compressed-tensors", "xgrammar", "outlines*",
                      "vllm-flash-attn", "partial-json-parser"],
        "hosts": ["agx"],
        "description": "Monolithic inference engine — never update independently",
    },
    "hf_layer": {
        "name": "HuggingFace Layer",
        "packages": ["huggingface-hub", "tokenizers", "transformers",
                      "safetensors", "datasets"],
        "hosts": ["agx", "nano", "ws"],
        "description": "HF stack — update hub first, then tokenizers, then transformers",
    },
    "web_stack": {
        "name": "Web Stack",
        "packages": ["starlette", "fastapi", "uvicorn", "pydantic",
                      "pydantic-core", "pydantic-settings", "httpx",
                      "anyio", "sse-starlette"],
        "hosts": ["agx", "nano"],
        "description": "FastAPI stack — LLM proxy and hub webui depend on this",
    },
    "langchain": {
        "name": "LangChain",
        "packages": ["langchain*", "langgraph*", "langsmith"],
        "hosts": ["agx", "nano", "ws"],
        "description": "LangChain — lockstep versioning required",
    },
    "torch_training": {
        "name": "Torch Training",
        "packages": ["accelerate", "peft", "trl"],
        "hosts": ["agx"],
        "description": "Training trio — accelerate is the foundation",
    },
    "apt_security": {
        "name": "Security Updates",
        "dimension": "apt",
        "match_security": True,
        "description": "APT packages from security sources — typically safe",
    },
    "apt_gstreamer": {
        "name": "GStreamer",
        "dimension": "apt",
        "packages": ["gstreamer*", "libgstreamer*"],
        "description": "GStreamer media framework — travels together",
    },
    "apt_util_linux": {
        "name": "util-linux",
        "dimension": "apt",
        "packages": ["bsdutils", "bsdextrautils", "fdisk", "util-linux*",
                      "mount", "libblkid*", "libfdisk*", "libmount*",
                      "libsmartcols*", "libuuid*", "eject"],
        "description": "util-linux family — same upstream",
    },
    "npm_azure_sdk": {
        "name": "Azure SDK",
        "dimension": "npm",
        "packages": ["@azure/*", "msal-browser", "@azure/msal-browser",
                      "msal-react", "@azure/msal-react", "@azure/cosmos",
                      "@azure/static-web-apps-cli", "azure-functions-core-tools"],
        "hosts": ["ws"],
        "description": "Azure SDK + auth — coordinate MSAL + Cosmos updates",
    },
    "npm_atproto": {
        "name": "AT Protocol",
        "dimension": "npm",
        "packages": ["@atproto/*"],
        "hosts": ["ws"],
        "description": "Bluesky AT Protocol SDK — lockstep versioning",
    },
    "npm_react": {
        "name": "React Ecosystem",
        "dimension": "npm",
        "packages": ["react", "react-dom", "react-scripts", "react-router*",
                      "@testing-library/react", "@testing-library/jest-dom",
                      "@testing-library/user-event", "@tanstack/react-query"],
        "hosts": ["ws"],
        "description": "React core + testing + routing — update together",
    },
    "infra_certs": {
        "name": "TLS Certificates",
        "dimension": "infra",
        "packages": ["cert:*"],
        "hosts": ["vps"],
        "description": "Let's Encrypt certs — expired or expiring soon",
    },
    "infra_node": {
        "name": "Node.js Runtime",
        "dimension": "infra",
        "packages": ["node"],
        "description": "Node.js version across hosts — coordinate major upgrades",
    },
    "devtools_azure_cli": {
        "name": "Azure CLI Stack",
        "dimension": "devtools",
        "packages": ["azure-cli", "az-ext:*"],
        "description": "Azure CLI + extensions — update CLI first, then extensions",
    },
    "azure_swa": {
        "name": "Static Web Apps",
        "dimension": "azure",
        "packages": ["swa:*"],
        "hosts": ["ws"],
        "description": "Azure Static Web Apps — 5 deployed apps",
    },
    "azure_backend": {
        "name": "Azure Backend Services",
        "dimension": "azure",
        "packages": ["func:*", "cosmos:*"],
        "hosts": ["ws"],
        "description": "Function Apps + Cosmos DB — API backends",
    },
}

DEFAULT_CONFIG = {
    "cluster_prompt": None,
    "cross_ref_prompt": None,
    "enabled_dimensions": ["apt", "pip"],
    "enabled_hosts": ["nano", "agx", "ws", "vps"],
    "clusters": DEFAULT_CLUSTER_DEFS,
    "blocklists": {
        "apt": {
            "nano": ["nvidia-*", "cuda-*", "libcudnn*", "libnvinfer*",
                      "tensorrt*", "nvidia-l4t-*", "jetpack-*"],
            "agx": ["nvidia-*", "cuda-*", "libcudnn*", "libnvinfer*",
                      "tensorrt*", "nvidia-l4t-*", "jetpack-*"],
            "ws": [],
            "vps": [],
        },
        "pip": {
            "nano": ["torch", "torchvision", "torchaudio", "tensorrt*",
                      "vllm", "flashinfer*", "bitsandbytes"],
            "agx": ["torch", "torchvision", "torchaudio", "tensorrt*",
                      "vllm", "triton", "flashinfer*", "bitsandbytes"],
            "ws": [],
            "vps": [],
        },
    },
    "batch_size": 50,
}


# ── Config management ────────────────────────────────────────────────────────

def load_updates_config():
    """Load config, merging stored values over defaults."""
    config = {k: v for k, v in DEFAULT_CONFIG.items()}
    if UPDATES_CONFIG_FILE.exists():
        try:
            stored = json.loads(UPDATES_CONFIG_FILE.read_text())
            if "blocklists" in stored:
                merged_bl = dict(config.get("blocklists", {}))
                for dim, hosts in stored["blocklists"].items():
                    if dim not in merged_bl:
                        merged_bl[dim] = {}
                    merged_bl[dim].update(hosts)
                stored["blocklists"] = merged_bl
            if "clusters" in stored:
                merged_cl = dict(config.get("clusters", {}))
                merged_cl.update(stored["clusters"])
                stored["clusters"] = merged_cl
            config.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_updates_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    UPDATES_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


# ── Rules management ─────────────────────────────────────────────────────────

def load_rules():
    if UPDATES_RULES_FILE.exists():
        try:
            return json.loads(UPDATES_RULES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_rules(rules):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    UPDATES_RULES_FILE.write_text(json.dumps(rules, indent=2) + "\n")


def add_rule(rule):
    rules = load_rules()
    rule["id"] = f"r_{uuid.uuid4().hex[:8]}"
    rule.setdefault("enabled", True)
    rule.setdefault("stats", {"total_matched": 0, "last_matched": None})
    rule["created"] = int(time.time())
    rules.append(rule)
    save_rules(rules)
    return rule


def update_rule(rule_id, updates):
    rules = load_rules()
    for r in rules:
        if r.get("id") == rule_id:
            r.update(updates)
            save_rules(rules)
            return True
    return False


def delete_rule(rule_id):
    rules = load_rules()
    new_rules = [r for r in rules if r.get("id") != rule_id]
    if len(new_rules) < len(rules):
        save_rules(new_rules)
        return True
    return False


def _match_rules(item, rules):
    for rule in rules:
        if not rule.get("enabled"):
            continue
        match = rule.get("match", {})
        matched = True
        if match.get("dimension") and match["dimension"] != item.get("dimension"):
            matched = False
        if matched and match.get("host") and match["host"] != item.get("host"):
            matched = False
        if matched and match.get("project") and match["project"] != item.get("project"):
            matched = False
        if matched and match.get("package_pattern"):
            if not fnmatch.fnmatch(item.get("package", "").lower(),
                                   match["package_pattern"].lower()):
                matched = False
        if matched:
            rule["stats"]["total_matched"] = rule["stats"].get("total_matched", 0) + 1
            rule["stats"]["last_matched"] = int(time.time())
            return rule.get("action", "skip"), rule["id"]
    return None, None


# ── Blocklist ────────────────────────────────────────────────────────────────

def _is_blocklisted(item, config):
    blocklists = config.get("blocklists", {})
    dim_bl = blocklists.get(item.get("dimension"), {})
    host_patterns = dim_bl.get(item.get("host"), [])
    pkg = item.get("package", "").lower()
    for pattern in host_patterns:
        if fnmatch.fnmatch(pkg, pattern.lower()):
            return True
    return False


# ── Stage 1: Scanners (enhanced) ────────────────────────────────────────────

def _parse_apt_line(line, host):
    """Parse apt list --upgradable line. Captures source tag for security detection."""
    # Format: "package/source1,source2 version arch [upgradable from: old]"
    m = re.match(
        r'^(\S+?)(?:/(\S+))?\s+(\S+)\s+\S+\s+\[upgradable from:\s*(\S+?)\]',
        line
    )
    if not m:
        return None
    pkg, source_tag, available, current = m.group(1), m.group(2) or "", m.group(3), m.group(4)
    is_security = "security" in source_tag.lower()
    return {
        "id": f"apt:{host}:{pkg}",
        "dimension": "apt",
        "host": host,
        "project": None,
        "package": pkg,
        "current": current,
        "available": available,
        "source_tag": source_tag,
        "is_security": is_security,
        "deps": [],
        "required_by": [],
        "dep_count": 0,
        "required_by_count": 0,
        "cluster_id": None,
        "classification": None,
        "reason": None,
        "source": None,
        "rule_matched": None,
        "approved": None,
        "apply_cmd": f"sudo apt-get install --only-upgrade {pkg}",
        "cross_refs": [],
        "intel": None,
        "scanned_at": int(time.time()),
    }


def _scan_apt_host(host):
    ok, output = ssh_cmd(host, "apt list --upgradable 2>/dev/null", timeout=30)
    if not ok:
        return []
    items = []
    for line in output.strip().split("\n"):
        if line.startswith("Listing") or not line.strip():
            continue
        item = _parse_apt_line(line, host)
        if item:
            items.append(item)
    return items


def _scan_apt(hosts):
    if not hosts:
        return []
    tasks = {h: (lambda h=h: _scan_apt_host(h)) for h in hosts}
    results = run_parallel(tasks)
    items = []
    for h in hosts:
        items.extend(results.get(h, []))
    return items


def _parse_pip_show_output(output):
    """Parse bulk 'pip show pkg1 pkg2 ...' output into dependency map."""
    dep_map = {}
    current_name = None
    for line in output.split("\n"):
        if line.startswith("Name: "):
            current_name = line[6:].strip()
            dep_map[current_name.lower()] = {"deps": [], "required_by": []}
        elif line.startswith("Requires: ") and current_name:
            raw = line[10:].strip()
            if raw:
                dep_map[current_name.lower()]["deps"] = [
                    d.strip().lower() for d in raw.split(",") if d.strip()
                ]
        elif line.startswith("Required-by: ") and current_name:
            raw = line[13:].strip()
            if raw:
                dep_map[current_name.lower()]["required_by"] = [
                    d.strip().lower() for d in raw.split(",") if d.strip()
                ]
    return dep_map


def _gather_pip_deps(host, package_names):
    """Bulk fetch dependency data for packages on a host."""
    if not package_names:
        return {}
    # Batch in chunks to avoid command line length limits
    dep_map = {}
    chunk_size = 80
    for i in range(0, len(package_names), chunk_size):
        chunk = package_names[i:i + chunk_size]
        cmd = f"pip show {' '.join(chunk)} 2>/dev/null | grep -E '^Name:|^Requires:|^Required-by:'"
        ok, output = ssh_cmd(host, cmd, timeout=30)
        if ok and output.strip():
            dep_map.update(_parse_pip_show_output(output))
    return dep_map


def _scan_pip_host(host):
    """Scan pip outdated + gather dependency data."""
    ok, output = ssh_cmd(host, "pip list --outdated --format=json 2>/dev/null", timeout=60)
    if not ok:
        return []
    try:
        pkgs = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return []

    # Gather deps for all outdated packages
    pkg_names = [p.get("name", "") for p in pkgs if p.get("name")]
    dep_map = _gather_pip_deps(host, pkg_names)

    items = []
    for p in pkgs:
        name = p.get("name", "")
        name_lower = name.lower()
        current = p.get("version", "")
        available = p.get("latest_version", "")
        deps_info = dep_map.get(name_lower, {})
        deps = deps_info.get("deps", [])
        required_by = deps_info.get("required_by", [])
        items.append({
            "id": f"pip:{host}:{name}",
            "dimension": "pip",
            "host": host,
            "project": None,
            "package": name,
            "current": current,
            "available": available,
            "source_tag": None,
            "is_security": False,
            "deps": deps,
            "required_by": required_by,
            "dep_count": len(deps),
            "required_by_count": len(required_by),
            "cluster_id": None,
            "classification": None,
            "reason": None,
            "source": None,
            "rule_matched": None,
            "approved": None,
            "apply_cmd": f"pip install --upgrade {name}=={available}",
            "cross_refs": [],
            "intel": None,
            "scanned_at": int(time.time()),
        })
    return items


def _scan_pip(hosts):
    if not hosts:
        return []
    results = {}
    def _run(h):
        results[h] = _scan_pip_host(h)
    threads = [threading.Thread(target=_run, args=(h,), daemon=True) for h in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    items = []
    for h in hosts:
        items.extend(results.get(h, []))
    return items


# ── npm scanner ──────────────────────────────────────────────────────────────

def _npm_projects_on_host(host):
    """Return list of (project_key, path) for projects with package.json on a host."""
    results = []
    for pkey, pdata in PROJECTS.items():
        if pdata.get("host") != host:
            continue
        path = pdata.get("path", "")
        if not path:
            continue
        results.append((pkey, path))
    return results


def _parse_npm_outdated(output, host, project_key=None, project_path=None):
    """Parse npm outdated --json output into item list.
    project_key=None means global packages."""
    try:
        pkgs = json.loads(output) if output.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return []

    items = []
    for name, info in pkgs.items():
        current = info.get("current", "")
        available = info.get("latest", "")
        if not current or not available or current == available:
            continue
        # Skip linked/missing packages
        if current == "linked" or current == "MISSING":
            continue

        if project_key:
            item_id = f"npm:{host}:{name}:{project_key}"
            apply_cmd = f"cd {project_path} && npm install {name}@{available}"
        else:
            item_id = f"npm:{host}:{name}"
            apply_cmd = f"npm -g install {name}@{available}"

        items.append({
            "id": item_id,
            "dimension": "npm",
            "host": host,
            "project": project_key,
            "package": name,
            "current": current,
            "available": available,
            "source_tag": "global" if not project_key else project_key,
            "is_security": False,
            "deps": [],
            "required_by": [],
            "dep_count": 0,
            "required_by_count": 0,
            "cluster_id": None,
            "classification": None,
            "reason": None,
            "source": None,
            "rule_matched": None,
            "approved": None,
            "apply_cmd": apply_cmd,
            "cross_refs": [],
            "intel": None,
            "scanned_at": int(time.time()),
        })
    return items


def _scan_npm_host(host):
    """Scan npm global + per-project outdated on a host.
    Note: npm outdated exits 1 when packages ARE outdated — so we parse output
    regardless of exit code, as long as it looks like JSON."""
    items = []

    # Global packages
    ok, output = ssh_cmd(host, "npm -g outdated --json 2>/dev/null", timeout=30)
    if output and output.strip().startswith("{"):
        items.extend(_parse_npm_outdated(output, host))

    # Per-project packages
    projects = _npm_projects_on_host(host)
    for pkey, path in projects:
        # Check if package.json exists and run npm outdated
        ok, output = ssh_cmd(
            host,
            f"test -f {path}/package.json && cd {path} && npm outdated --json 2>/dev/null",
            timeout=30,
        )
        if output and output.strip().startswith("{"):
            items.extend(_parse_npm_outdated(output, host, pkey, path))

    return items


def _scan_npm(hosts):
    if not hosts:
        return []
    results = {}
    def _run(h):
        results[h] = _scan_npm_host(h)
    threads = [threading.Thread(target=_run, args=(h,), daemon=True) for h in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    items = []
    for h in hosts:
        items.extend(results.get(h, []))
    return items


# ── Infra scanner ────────────────────────────────────────────────────────────

def _github_latest_version(owner_repo):
    """Get latest release tag from GitHub. Returns version string or None."""
    import urllib.request
    import urllib.error
    try:
        url = f"https://api.github.com/repos/{owner_repo}/releases/latest"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "oi-webui",
        })
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        tag = data.get("tag_name", "")
        return tag.lstrip("v")
    except Exception:
        return None


# Tools to check per host: (tool_name, version_cmd, parse_fn, github_repo, hosts, apply_hint)
# parse_fn extracts version string from command output
INFRA_TOOLS = [
    {
        "name": "nginx",
        "cmd": "nginx -v 2>&1",
        "parse": lambda out: re.search(r"nginx/(\S+)", out).group(1) if "nginx/" in out else None,
        "repo": "nginx/nginx",
        "hosts": ["vps"],
        "apply": "Add nginx mainline PPA or build from source",
    },
    {
        "name": "certbot",
        "cmd": "certbot --version 2>&1",
        "parse": lambda out: re.search(r"certbot (\S+)", out).group(1) if "certbot" in out else None,
        "repo": "certbot/certbot",
        "hosts": ["vps"],
        "apply": "pip install --upgrade certbot certbot-nginx",
    },
    {
        "name": "cloudflared",
        "cmd": "cloudflared --version 2>&1",
        "parse": lambda out: re.search(r"version (\S+)", out).group(1) if "version" in out else None,
        "repo": "cloudflare/cloudflared",
        "hosts": ["nano"],
        "apply": "cloudflared update",
    },
    {
        "name": "gh",
        "cmd": "gh --version 2>&1 | head -1",
        "parse": lambda out: re.search(r"version (\S+)", out).group(1).split("+")[0] if "version" in out else None,
        "repo": "cli/cli",
        "hosts": ["ws", "vps", "agx", "nano"],
        "apply": "See https://github.com/cli/cli/blob/trunk/docs/install_linux.md",
    },
    {
        "name": "node",
        "cmd": "node --version 2>&1",
        "parse": lambda out: out.strip().lstrip("v") if out.strip().startswith("v") else None,
        "repo": "nodejs/node",
        "hosts": ["nano", "ws", "vps", "agx"],
        "apply": "Use nvm or nodesource to update Node.js",
    },
]


def _scan_infra_host(host):
    """Check infrastructure tools on a host against latest GitHub releases."""
    items = []
    for tool in INFRA_TOOLS:
        if host not in tool["hosts"]:
            continue
        ok, output = ssh_cmd(host, tool["cmd"], timeout=10)
        if not output:
            continue
        try:
            current = tool["parse"](output)
        except Exception:
            continue
        if not current:
            continue

        items.append({
            "_tool": tool,
            "host": host,
            "package": tool["name"],
            "current": current,
        })
    return items


def _scan_infra(hosts):
    if not hosts:
        return []

    # Step 1: Gather installed versions from all hosts in parallel
    host_results = {}
    def _run(h):
        host_results[h] = _scan_infra_host(h)
    threads = [threading.Thread(target=_run, args=(h,), daemon=True) for h in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    # Step 2: Fetch latest versions from GitHub (deduplicate by repo)
    repos_needed = {}
    for h in hosts:
        for entry in host_results.get(h, []):
            repo = entry["_tool"]["repo"]
            if repo not in repos_needed:
                repos_needed[repo] = None

    latest_results = {}
    def _fetch_latest(repo):
        v = _github_latest_version(repo)
        # Strip common tag prefixes: release-, v
        if v and v.startswith("release-"):
            v = v[8:]
        latest_results[repo] = v
    lt = [threading.Thread(target=_fetch_latest, args=(r,), daemon=True) for r in repos_needed]
    for t in lt:
        t.start()
    for t in lt:
        t.join(timeout=15)

    # Step 3: Build items for tools that are outdated
    items = []
    now = int(time.time())
    for h in hosts:
        for entry in host_results.get(h, []):
            tool = entry["_tool"]
            current = entry["current"]
            latest = latest_results.get(tool["repo"])
            if not latest:
                continue
            # Normalize for comparison: strip pre-release suffixes for rough compare
            cur_clean = current.split("-")[0].split("+")[0]
            lat_clean = latest.split("-")[0].split("+")[0]
            if cur_clean == lat_clean:
                continue
            items.append({
                "id": f"infra:{h}:{tool['name']}",
                "dimension": "infra",
                "host": h,
                "project": None,
                "package": tool["name"],
                "current": current,
                "available": latest,
                "source_tag": tool["repo"],
                "is_security": False,
                "deps": [],
                "required_by": [],
                "dep_count": 0,
                "required_by_count": 0,
                "cluster_id": None,
                "classification": None,
                "reason": None,
                "source": None,
                "rule_matched": None,
                "approved": None,
                "apply_cmd": tool["apply"],
                "cross_refs": [],
                "intel": {"repo": tool["repo"]},
                "scanned_at": now,
            })

    # Step 4: Check certbot expired certs on VPS
    if "vps" in hosts:
        ok, output = ssh_cmd("vps", "certbot certificates 2>&1", timeout=15)
        if output:
            # Parse cert blocks
            cert_name = None
            for line in output.split("\n"):
                line = line.strip()
                m = re.match(r"Certificate Name:\s+(.+)", line)
                if m:
                    cert_name = m.group(1)
                if "EXPIRED" in line and cert_name:
                    m2 = re.search(r"Expiry Date:\s+(\S+)", line)
                    expired_date = m2.group(1) if m2 else "unknown"
                    items.append({
                        "id": f"infra:vps:cert-expired:{cert_name}",
                        "dimension": "infra",
                        "host": "vps",
                        "project": None,
                        "package": f"cert:{cert_name}",
                        "current": f"expired {expired_date}",
                        "available": "renew",
                        "source_tag": "certbot",
                        "is_security": True,
                        "deps": [],
                        "required_by": [],
                        "dep_count": 0,
                        "required_by_count": 0,
                        "cluster_id": None,
                        "classification": "urgent",
                        "reason": "Certificate expired",
                        "source": "scanner",
                        "rule_matched": None,
                        "approved": None,
                        "apply_cmd": f"certbot renew --cert-name {cert_name}",
                        "cross_refs": [],
                        "intel": None,
                        "scanned_at": now,
                    })
                    cert_name = None

    return items


# ── Devtools scanner ─────────────────────────────────────────────────────────

def _scan_devtools_host(host):
    """Check dev tools on a host."""
    items = []
    now = int(time.time())

    # VS Code extensions (WS only — desktop IDE)
    if host == "ws":
        ok, output = ssh_cmd(host, "code --list-extensions --show-versions 2>/dev/null", timeout=15)
        if ok and output and output.strip():
            extensions = []
            for line in output.strip().split("\n"):
                line = line.strip()
                if "@" in line:
                    parts = line.rsplit("@", 1)
                    if len(parts) == 2:
                        extensions.append((parts[0], parts[1]))
            # VS Code itself
            ok2, vs_output = ssh_cmd(host, "code --version 2>/dev/null | head -1", timeout=10)
            if ok2 and vs_output and vs_output.strip():
                items.append({
                    "id": f"devtools:{host}:vscode",
                    "dimension": "devtools",
                    "host": host,
                    "project": None,
                    "package": "vscode",
                    "current": vs_output.strip(),
                    "available": "check",
                    "source_tag": "vscode",
                    "is_security": False,
                    "deps": [],
                    "required_by": [],
                    "dep_count": 0,
                    "required_by_count": len(extensions),
                    "cluster_id": None,
                    "classification": None,
                    "reason": None,
                    "source": None,
                    "rule_matched": None,
                    "approved": None,
                    "apply_cmd": "VS Code updates itself",
                    "cross_refs": [],
                    "intel": {"extension_count": len(extensions)},
                    "scanned_at": now,
                })

    # .NET SDK
    ok, output = ssh_cmd(host, "dotnet --list-sdks 2>/dev/null | tail -1", timeout=10)
    if ok and output and output.strip():
        m = re.match(r"(\S+)", output.strip())
        if m:
            items.append({
                "id": f"devtools:{host}:dotnet-sdk",
                "dimension": "devtools",
                "host": host,
                "project": None,
                "package": "dotnet-sdk",
                "current": m.group(1),
                "available": "check",
                "source_tag": "dotnet",
                "is_security": False,
                "deps": [], "required_by": [],
                "dep_count": 0, "required_by_count": 0,
                "cluster_id": None,
                "classification": None, "reason": None, "source": None,
                "rule_matched": None, "approved": None,
                "apply_cmd": "See https://dotnet.microsoft.com/download",
                "cross_refs": [], "intel": None,
                "scanned_at": now,
            })

    # Azure CLI
    ok, output = ssh_cmd(host, "az version --output json 2>/dev/null", timeout=15)
    if ok and output and output.strip().startswith("{"):
        try:
            az_data = json.loads(output)
            az_ver = az_data.get("azure-cli", "")
            extensions = az_data.get("extensions", {})
            if az_ver:
                items.append({
                    "id": f"devtools:{host}:azure-cli",
                    "dimension": "devtools",
                    "host": host,
                    "project": None,
                    "package": "azure-cli",
                    "current": az_ver,
                    "available": "check",
                    "source_tag": "azure",
                    "is_security": False,
                    "deps": [], "required_by": list(extensions.keys()),
                    "dep_count": 0, "required_by_count": len(extensions),
                    "cluster_id": None,
                    "classification": None, "reason": None, "source": None,
                    "rule_matched": None, "approved": None,
                    "apply_cmd": "az upgrade",
                    "cross_refs": [], "intel": {"extensions": extensions},
                    "scanned_at": now,
                })
                # Each extension as its own item
                for ext_name, ext_ver in extensions.items():
                    items.append({
                        "id": f"devtools:{host}:az-ext:{ext_name}",
                        "dimension": "devtools",
                        "host": host,
                        "project": None,
                        "package": f"az-ext:{ext_name}",
                        "current": ext_ver,
                        "available": "check",
                        "source_tag": "azure",
                        "is_security": False,
                        "deps": ["azure-cli"], "required_by": [],
                        "dep_count": 1, "required_by_count": 0,
                        "cluster_id": None,
                        "classification": None, "reason": None, "source": None,
                        "rule_matched": None, "approved": None,
                        "apply_cmd": f"az extension update --name {ext_name}",
                        "cross_refs": [], "intel": None,
                        "scanned_at": now,
                    })
        except (json.JSONDecodeError, ValueError):
            pass

    return items


def _scan_devtools(hosts):
    if not hosts:
        return []
    results = {}
    def _run(h):
        results[h] = _scan_devtools_host(h)
    threads = [threading.Thread(target=_run, args=(h,), daemon=True) for h in hosts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=45)
    items = []
    for h in hosts:
        items.extend(results.get(h, []))
    return items


# ── Azure services scanner ──────────────────────────────────────────────────

def _scan_azure():
    """Scan Azure service configurations: SWA runtimes, Function App bundles."""
    items = []
    now = int(time.time())

    # Only WS has az CLI configured for our subscriptions
    hosts_with_az = ["ws"]
    for host in hosts_with_az:
        # Check az login status
        ok, output = ssh_cmd(host, "az account show --output json 2>/dev/null", timeout=10)
        if not ok or not output or not output.strip().startswith("{"):
            continue

        # Static Web Apps
        ok, swa_out = ssh_cmd(
            host,
            "az staticwebapp list --output json 2>/dev/null",
            timeout=20,
        )
        if ok and swa_out and swa_out.strip().startswith("["):
            try:
                swas = json.loads(swa_out)
                for swa in swas:
                    name = swa.get("name", "")
                    sku = swa.get("sku", {}).get("name", "")
                    # SWA API version / build config
                    build = swa.get("buildProperties", {}) or {}
                    api_runtime = build.get("apiRuntime", "")
                    app_runtime = build.get("appArtifactLocation", "")

                    items.append({
                        "id": f"azure:ws:swa:{name}",
                        "dimension": "azure",
                        "host": "ws",
                        "project": None,
                        "package": f"swa:{name}",
                        "current": f"sku={sku}" + (f", api={api_runtime}" if api_runtime else ""),
                        "available": "check",
                        "source_tag": "azure-swa",
                        "is_security": False,
                        "deps": [], "required_by": [],
                        "dep_count": 0, "required_by_count": 0,
                        "cluster_id": None,
                        "classification": None, "reason": None, "source": None,
                        "rule_matched": None, "approved": None,
                        "apply_cmd": f"az staticwebapp update -n {name}",
                        "cross_refs": [], "intel": {"sku": sku, "api_runtime": api_runtime},
                        "scanned_at": now,
                    })
            except (json.JSONDecodeError, ValueError):
                pass

        # Function Apps
        ok, func_out = ssh_cmd(
            host,
            "az functionapp list --output json 2>/dev/null",
            timeout=20,
        )
        if ok and func_out and func_out.strip().startswith("["):
            try:
                funcs = json.loads(func_out)
                for fa in funcs:
                    name = fa.get("name", "")
                    runtime = fa.get("siteConfig", {}).get("linuxFxVersion", "") or ""
                    node_ver = fa.get("siteConfig", {}).get("nodeVersion", "") or ""
                    rg = fa.get("resourceGroup", "")

                    items.append({
                        "id": f"azure:ws:func:{name}",
                        "dimension": "azure",
                        "host": "ws",
                        "project": None,
                        "package": f"func:{name}",
                        "current": runtime or node_ver or "unknown",
                        "available": "check",
                        "source_tag": "azure-func",
                        "is_security": False,
                        "deps": [], "required_by": [],
                        "dep_count": 0, "required_by_count": 0,
                        "cluster_id": None,
                        "classification": None, "reason": None, "source": None,
                        "rule_matched": None, "approved": None,
                        "apply_cmd": f"az functionapp config set -n {name} -g {rg}",
                        "cross_refs": [],
                        "intel": {"runtime": runtime, "resource_group": rg},
                        "scanned_at": now,
                    })
            except (json.JSONDecodeError, ValueError):
                pass

        # Cosmos DB accounts
        ok, cosmos_out = ssh_cmd(
            host,
            "az cosmosdb list --output json 2>/dev/null",
            timeout=20,
        )
        if ok and cosmos_out and cosmos_out.strip().startswith("["):
            try:
                dbs = json.loads(cosmos_out)
                for db in dbs:
                    name = db.get("name", "")
                    kind = db.get("kind", "")
                    offer = db.get("databaseAccountOfferType", "")
                    cap = db.get("capabilities", [])
                    cap_names = [c.get("name", "") for c in cap] if cap else []

                    items.append({
                        "id": f"azure:ws:cosmos:{name}",
                        "dimension": "azure",
                        "host": "ws",
                        "project": None,
                        "package": f"cosmos:{name}",
                        "current": f"{kind}, {offer}" + (f", caps={','.join(cap_names)}" if cap_names else ""),
                        "available": "check",
                        "source_tag": "azure-cosmos",
                        "is_security": False,
                        "deps": [], "required_by": [],
                        "dep_count": 0, "required_by_count": 0,
                        "cluster_id": None,
                        "classification": None, "reason": None, "source": None,
                        "rule_matched": None, "approved": None,
                        "apply_cmd": f"Review in Azure Portal",
                        "cross_refs": [],
                        "intel": {"kind": kind, "capabilities": cap_names},
                        "scanned_at": now,
                    })
            except (json.JSONDecodeError, ValueError):
                pass

    return items


# ── Stub scanners ────────────────────────────────────────────────────────────

def _scan_cargo(hosts): return []
def _scan_github_actions(): return []
def _scan_docker(hosts): return []


# ── Stage 2: Clustering ─────────────────────────────────────────────────────

def _match_static_cluster(item, cluster_defs):
    """Check if item matches any static cluster definition. Returns cluster key or None."""
    pkg = item.get("package", "").lower()
    host = item.get("host", "")
    dim = item.get("dimension", "")

    for ckey, cdef in cluster_defs.items():
        # Dimension filter
        if cdef.get("dimension") and cdef["dimension"] != dim:
            continue

        # Security match (apt only)
        if cdef.get("match_security"):
            if item.get("is_security"):
                return ckey
            continue

        # Host filter
        if cdef.get("hosts") and host not in cdef["hosts"]:
            continue

        # Package pattern matching
        for pattern in cdef.get("packages", []):
            if fnmatch.fnmatch(pkg, pattern.lower()):
                return ckey

    return None


HUB_PACKAGE_THRESHOLD = 8  # packages with >= this many dependents become hub packages


def _identify_hub_packages(pip_items):
    """Find high-fanout packages that should get their own cluster.
    These are dependency hubs — updating them impacts many consumers."""
    hub_pkgs = {}  # (host, pkg_lower) → item
    for item in pip_items:
        if item.get("required_by_count", 0) >= HUB_PACKAGE_THRESHOLD:
            key = (item["host"], item["package"].lower())
            hub_pkgs[key] = item
    return hub_pkgs


MAX_CLUSTER_SIZE = 30  # clusters above this get split by promoting internal hubs


def _connected_components(group, excluded_keys):
    """Run union-find on a group of pip items, skipping excluded package keys.
    Returns list of lists (each sub-list is a connected component)."""
    parent = {}
    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Build lookup for this group only
    pkg_to_id = {}
    for item in group:
        key = (item["host"], item["package"].lower())
        if key not in excluded_keys:
            pkg_to_id[key] = item["id"]

    for item in group:
        key = (item["host"], item["package"].lower())
        if key in excluded_keys:
            continue
        for dep in item.get("deps", []):
            dep_key = (item["host"], dep.lower())
            if dep_key in pkg_to_id:
                union(item["id"], pkg_to_id[dep_key])
        for rb in item.get("required_by", []):
            rb_key = (item["host"], rb.lower())
            if rb_key in pkg_to_id:
                union(item["id"], pkg_to_id[rb_key])

    comps = {}
    for item in group:
        key = (item["host"], item["package"].lower())
        if key in excluded_keys:
            continue
        root = find(item["id"])
        if root not in comps:
            comps[root] = []
        comps[root].append(item)
    return list(comps.values())


def _split_oversized(group, hub_pkg_keys):
    """If a component exceeds MAX_CLUSTER_SIZE, iteratively promote the
    highest-fanout internal package as a local hub and re-split.
    Returns (sub_groups, promoted_items) where promoted_items get their own cluster."""
    promoted = []
    local_hubs = set(hub_pkg_keys)  # start with global hubs

    remaining = list(group)
    for _ in range(20):  # safety cap on iterations
        # Re-run connected components with current exclusions
        comps = _connected_components(remaining, local_hubs)
        # Find any oversized component
        oversized = [c for c in comps if len(c) > MAX_CLUSTER_SIZE]
        if not oversized:
            return comps, promoted

        # In the largest oversized component, find the top bridge node
        biggest = max(oversized, key=len)
        # Score by required_by_count (how many items in THIS group depend on it)
        pkg_set = {(i["host"], i["package"].lower()) for i in biggest}
        scores = []
        for item in biggest:
            key = (item["host"], item["package"].lower())
            if key in local_hubs:
                continue
            # Count edges within this component
            edges = 0
            for dep in item.get("deps", []):
                if (item["host"], dep.lower()) in pkg_set:
                    edges += 1
            for rb in item.get("required_by", []):
                if (item["host"], rb.lower()) in pkg_set:
                    edges += 1
            scores.append((edges, item.get("required_by_count", 0), item))
        if not scores:
            break
        scores.sort(key=lambda x: (-x[0], -x[1]))
        # Promote the top connector
        promote_item = scores[0][2]
        promote_key = (promote_item["host"], promote_item["package"].lower())
        local_hubs.add(promote_key)
        promoted.append(promote_item)
        remaining = [i for i in remaining if (i["host"], i["package"].lower()) != promote_key]

    # Final pass
    comps = _connected_components(remaining, local_hubs)
    return comps, promoted


def _build_dynamic_clusters(items, hub_pkg_keys):
    """Group unmatched pip items by connected components, skipping hub packages
    as edge nodes (they get their own cluster). This prevents mega-clusters.
    Oversized components are split by promoting internal bridge nodes."""
    if not items:
        return {}, []

    pip_items = [i for i in items if i.get("dimension") == "pip"]
    non_pip = [i for i in items if i.get("dimension") != "pip"]

    # Build lookup of items being updated (excluding hub packages)
    pkg_to_item = {}
    for item in pip_items:
        key = (item["host"], item["package"].lower())
        if key not in hub_pkg_keys:
            pkg_to_item[key] = item

    # Initial connected components
    initial_comps = _connected_components(
        [pkg_to_item[k] for k in pkg_to_item], hub_pkg_keys
    )

    # Split any oversized components
    all_groups = []
    promoted_items = []
    for comp in initial_comps:
        if len(comp) > MAX_CLUSTER_SIZE:
            sub_groups, promoted = _split_oversized(comp, hub_pkg_keys)
            all_groups.extend(sub_groups)
            promoted_items.extend(promoted)
        else:
            all_groups.append(comp)

    # Create clusters for multi-item components
    clusters = {}
    solo_items = list(non_pip)
    for group in all_groups:
        if len(group) >= 2:
            host = group[0]["host"]
            names = sorted(i["package"] for i in group)
            cid = f"dynamic__{host}__{names[0].lower().replace(' ', '_')}"
            clusters[cid] = {
                "id": cid,
                "name": ", ".join(names[:3]) + ("..." if len(names) > 3 else ""),
                "hosts": list(set(i["host"] for i in group)),
                "dimension": "pip",
                "item_ids": [i["id"] for i in group],
                "item_count": len(group),
                "static": False,
            }
            for item in group:
                item["cluster_id"] = cid
        else:
            solo_items.extend(group)

    # Promoted items become their own single-item clusters (local hubs)
    for item in promoted_items:
        host = item["host"]
        pkg = item["package"].lower().replace(" ", "_")
        cid = f"promoted__{host}__{pkg}"
        clusters[cid] = {
            "id": cid,
            "name": f"{item['package']} (bridge)",
            "hosts": [host],
            "dimension": "pip",
            "item_ids": [item["id"]],
            "item_count": 1,
            "static": False,
            "tier": "bridge",
        }
        item["cluster_id"] = cid

    return clusters, solo_items


def _build_hub_clusters(hub_pkgs, all_items):
    """Create 'Core Dependencies' clusters for high-fanout packages.
    Each hub package gets listed with which domain clusters it influences."""
    # Group hub packages by host
    by_host = {}
    for (host, pkg_lower), item in hub_pkgs.items():
        if host not in by_host:
            by_host[host] = []
        by_host[host].append(item)

    clusters = {}
    for host, items in by_host.items():
        host_name = HOSTS.get(host, {}).get("name", host)
        cid = f"core_deps__{host}"
        # Sort by dependent count descending
        items.sort(key=lambda i: -i.get("required_by_count", 0))

        # Compute which other clusters these hub packages influence
        influenced = set()
        for item in items:
            for rb in item.get("required_by", []):
                # Check if any required_by package is in a static cluster
                for other_item in all_items:
                    if (other_item.get("host") == host and
                            other_item.get("package", "").lower() == rb.lower() and
                            other_item.get("cluster_id") and
                            not other_item["cluster_id"].startswith("core_deps")):
                        influenced.add(other_item["cluster_id"])

        clusters[cid] = {
            "id": cid,
            "name": f"Core Dependencies — {host_name}",
            "hosts": [host],
            "dimension": "pip",
            "item_ids": [i["id"] for i in items],
            "item_count": len(items),
            "static": False,
            "tier": "hub",
            "influenced_clusters": list(influenced),
            "description": f"High-impact packages with {HUB_PACKAGE_THRESHOLD}+ dependents. "
                           f"Updating these affects multiple clusters.",
        }
        for item in items:
            item["cluster_id"] = cid

    return clusters


def _build_clusters(all_items, config):
    """Tiered clustering:
    1. Static cluster definitions (vLLM Core, Web Stack, etc.)
    2. Hub packages (numpy, packaging, requests — high fanout, own cluster)
    3. Dynamic clusters (connected components, hub packages excluded from edges)
    4. Catch-all (solo items grouped by host+dimension)
    """
    cluster_defs = config.get("clusters", DEFAULT_CLUSTER_DEFS)
    clusters = {}
    unmatched = []

    # Tier 1: Static cluster matching
    for item in all_items:
        if item.get("classification") == "blocked":
            continue
        ckey = _match_static_cluster(item, cluster_defs)
        if ckey:
            host = item.get("host", "unknown")
            cluster_id = f"{ckey}__{host}"
            if cluster_id not in clusters:
                cdef = cluster_defs[ckey]
                clusters[cluster_id] = {
                    "id": cluster_id,
                    "name": cdef.get("name", ckey),
                    "hosts": [host],
                    "dimension": cdef.get("dimension") or item.get("dimension", "mixed"),
                    "item_ids": [],
                    "item_count": 0,
                    "static": True,
                    "description": cdef.get("description", ""),
                }
            if host not in clusters[cluster_id]["hosts"]:
                clusters[cluster_id]["hosts"].append(host)
            clusters[cluster_id]["item_ids"].append(item["id"])
            clusters[cluster_id]["item_count"] += 1
            item["cluster_id"] = cluster_id
        else:
            unmatched.append(item)

    # Tier 2: Identify hub packages among unmatched pip items
    pip_unmatched = [i for i in unmatched if i.get("dimension") == "pip"]
    hub_pkgs = _identify_hub_packages(pip_unmatched)
    hub_pkg_keys = set(hub_pkgs.keys())

    # Tier 3: Dynamic clustering (skipping hub packages as edges)
    dynamic_clusters, solo_items = _build_dynamic_clusters(unmatched, hub_pkg_keys)
    clusters.update(dynamic_clusters)

    # Now create hub clusters (after dynamic so we can see influenced_clusters)
    hub_clusters = _build_hub_clusters(hub_pkgs, all_items)
    clusters.update(hub_clusters)

    # Tier 4: Catch-all for remaining solo items
    catchall_groups = {}
    for item in solo_items:
        key = f"other__{item.get('dimension', 'misc')}__{item.get('host', 'unknown')}"
        if key not in catchall_groups:
            host = item.get("host", "unknown")
            dim = item.get("dimension", "misc")
            host_name = HOSTS.get(host, {}).get("name", host)
            catchall_groups[key] = {
                "id": key,
                "name": f"{host_name} — Other {dim}",
                "hosts": [host],
                "dimension": dim,
                "item_ids": [],
                "item_count": 0,
                "static": False,
                "description": f"Unclustered {dim} packages on {host_name}",
            }
        catchall_groups[key]["item_ids"].append(item["id"])
        catchall_groups[key]["item_count"] += 1
        item["cluster_id"] = key
    clusters.update(catchall_groups)

    return clusters


# ── Stage 3: Context Enrichment ──────────────────────────────────────────────

def _enrich_clusters(clusters, all_items):
    """Add project/service context and risk scores to clusters."""
    # Build item lookup
    item_map = {i["id"]: i for i in all_items}

    # Map host → projects
    host_projects = {}
    for pkey, proj in PROJECTS.items():
        h = proj.get("host", "")
        if h not in host_projects:
            host_projects[h] = []
        host_projects[h].append({
            "key": pkey,
            "name": proj.get("name", pkey),
            "tagline": proj.get("tagline", ""),
        })

    for cid, cluster in clusters.items():
        # Compute risk score
        risk_score = 0
        has_security = False
        for item_id in cluster.get("item_ids", []):
            item = item_map.get(item_id, {})
            risk_score += item.get("required_by_count", 0)
            if item.get("is_security"):
                has_security = True

        cluster["risk_score"] = risk_score
        cluster["is_security"] = has_security

        # Gather affected projects from all cluster hosts
        projects = []
        seen_projects = set()
        for h in cluster.get("hosts", []):
            for proj in host_projects.get(h, []):
                if proj["key"] not in seen_projects:
                    projects.append(proj)
                    seen_projects.add(proj["key"])
        cluster["projects"] = projects

        # Host roles
        host_roles = []
        for h in cluster.get("hosts", []):
            host_roles.extend(HOSTS.get(h, {}).get("roles", []))
        cluster["host_roles"] = list(set(host_roles))

        # Analysis placeholders
        cluster.setdefault("analysis", None)
        cluster.setdefault("analyzed_at", None)
        cluster.setdefault("intel", None)

    return clusters


# ── Release notes fetching ────────────────────────────────────────────────────


def _get_github_repo_from_pypi(package_name):
    """Get GitHub owner/repo from PyPI metadata. Returns 'owner/repo' or None."""
    import urllib.request
    import urllib.error
    try:
        url = f"https://pypi.org/pypi/{package_name}/json"
        req = urllib.request.Request(url, headers={"User-Agent": "oi-webui"})
        data = json.loads(urllib.request.urlopen(req, timeout=8).read())
        urls = data.get("info", {}).get("project_urls") or {}
        # Also check home_page
        all_urls = list(urls.values())
        home = data.get("info", {}).get("home_page")
        if home:
            all_urls.append(home)
        for u in all_urls:
            if not u:
                continue
            m = re.match(r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?(?:/.*)?$", u)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _get_github_repo_from_npm(package_name):
    """Get GitHub owner/repo from npm registry metadata. Returns 'owner/repo' or None."""
    import urllib.request
    import urllib.error
    try:
        url = f"https://registry.npmjs.org/{package_name}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "oi-webui",
            "Accept": "application/vnd.npm.install-v1+json",
        })
        data = json.loads(urllib.request.urlopen(req, timeout=8).read())
        repo = data.get("repository") or {}
        repo_url = repo.get("url", "") if isinstance(repo, dict) else str(repo)
        m = re.match(r"(?:git\+)?https?://github\.com/([^/]+/[^/]+?)(?:\.git)?(?:/.*)?$", repo_url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _fetch_release_notes(github_repo, current_version, available_version):
    """Fetch release notes for all versions between current and available.
    Returns list of {version, date, notes} dicts, newest first."""
    import urllib.request
    import urllib.error
    try:
        from packaging.version import Version, InvalidVersion
    except ImportError:
        return []

    try:
        current_v = Version(current_version)
        available_v = Version(available_version)
    except InvalidVersion:
        return []

    try:
        url = f"https://api.github.com/repos/{github_repo}/releases?per_page=100"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "oi-webui",
        })
        releases = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception:
        return []

    relevant = []
    for r in releases:
        tag = r.get("tag_name", "").lstrip("v")
        try:
            v = Version(tag)
        except InvalidVersion:
            continue
        if not (current_v < v <= available_v):
            continue

        body = r.get("body") or ""
        # Clean: strip images, code blocks, HTML tags
        lines = []
        in_code = False
        for line in body.split("\n"):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            if "<img" in line or "![" in line:
                continue
            lines.append(line)
        clean = "\n".join(lines).strip()

        relevant.append({
            "version": tag,
            "date": r.get("published_at", "")[:10],
            "notes": clean[:800],  # cap per release
        })

    return relevant


def _gather_cluster_release_notes(items):
    """For each pip/npm item in a cluster, fetch release notes for intermediate versions.
    Returns {package_name: [release_notes]} dict. Runs in parallel threads."""
    eligible = [i for i in items if i.get("dimension") in ("pip", "npm")]
    if not eligible:
        return {}

    # Deduplicate by package name (same package may appear in multiple projects)
    seen = set()
    unique = []
    for i in eligible:
        if i["package"] not in seen:
            seen.add(i["package"])
            unique.append(i)

    results = {}

    def _fetch_one(item):
        pkg = item["package"]
        dim = item.get("dimension")
        if dim == "pip":
            repo = _get_github_repo_from_pypi(pkg)
        else:
            repo = _get_github_repo_from_npm(pkg)
        if not repo:
            return
        notes = _fetch_release_notes(repo, item.get("current", ""), item.get("available", ""))
        if notes:
            results[pkg] = {
                "repo": repo,
                "releases": notes,
                "releases_skipped": len(notes),
            }

    threads = [threading.Thread(target=_fetch_one, args=(i,), daemon=True) for i in unique]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    return results


# ── Stage 6: LLM Cluster Analysis ───────────────────────────────────────────

def _parse_cluster_analysis(raw_text):
    """Parse LLM markdown response into structured analysis dict.
    The model returns markdown with embedded JSON for the items array.
    We extract: risk_level, recommendation, update_order, reasoning, items."""
    analysis = {"raw": raw_text}

    # Extract quoted values from markdown like **1. risk_level**: "moderate"
    for field in ("risk_level", "recommendation"):
        m = re.search(rf'{field}["\s:*]*["\s]*(\w+)', raw_text, re.IGNORECASE)
        if m:
            analysis[field] = m.group(1).lower()

    # Extract reasoning (everything after "reasoning" header until next numbered section or JSON)
    m = re.search(r'reasoning["\s:*]*\n(.*?)(?:\n\*?\*?\d\.|```)', raw_text, re.DOTALL | re.IGNORECASE)
    if m:
        analysis["reasoning"] = m.group(1).strip()

    # Extract update_order from array-like content
    m = re.search(r'update_order["\s:*]*\[([^\]]+)\]', raw_text, re.IGNORECASE)
    if m:
        analysis["update_order"] = [
            s.strip().strip('"').strip("'") for s in m.group(1).split(",")
        ]

    # Extract items JSON array (may be in a code fence or inline)
    items = []
    # Try code-fenced JSON first
    json_match = re.search(r'```json?\s*\n(\[.*?\])\s*\n```', raw_text, re.DOTALL)
    if json_match:
        try:
            items = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    # Fallback: find any JSON array in the text
    if not items:
        for m in re.finditer(r'\[[\s\S]*?\{[\s\S]*?"id"[\s\S]*?\}[\s\S]*?\]', raw_text):
            try:
                items = json.loads(m.group(0))
                break
            except json.JSONDecodeError:
                continue

    if items:
        analysis["items"] = items

    return analysis


def _scan_actual_usage(cluster, items):
    """Grep affected projects for actual imports of each package in the cluster.
    Returns {package_name: {project_key: [import_lines]}} dict.
    Handles pip (Python imports) and npm (JS/TS imports) items."""
    usage = {}

    # ── pip items: grep Python imports ──
    pip_items = [i for i in items if i.get("dimension") == "pip"]
    if pip_items:
        pkg_names = [i["package"].lower().replace("-", "_").replace(".", "_") for i in pip_items]
        pkg_display = {i["package"].lower().replace("-", "_").replace(".", "_"): i["package"] for i in pip_items}
        patterns = "|".join(f"from {p}|import {p}" for p in pkg_names)

        for proj in cluster.get("projects", []):
            pkey = proj.get("key", "")
            pdata = PROJECTS.get(pkey, {})
            host = pdata.get("host", "")
            path = pdata.get("path", "")
            if not host or not path:
                continue

            ok, output = ssh_cmd(
                host,
                f"grep -rh --include='*.py' -E '{patterns}' {path}/ 2>/dev/null | sort -u | head -30",
                timeout=10,
            )
            if not ok or not output.strip():
                continue

            for line in output.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                for pkg_norm, pkg_orig in pkg_display.items():
                    if f"from {pkg_norm}" in line or f"import {pkg_norm}" in line:
                        if pkg_orig not in usage:
                            usage[pkg_orig] = {}
                        if pkey not in usage[pkg_orig]:
                            usage[pkg_orig][pkey] = []
                        usage[pkg_orig][pkey].append(line)
                        break

    # ── npm items: grep JS/TS require/import ──
    npm_items = [i for i in items if i.get("dimension") == "npm"]
    if npm_items:
        # Build package names — npm names used as-is (e.g., @azure/cosmos, react-router-dom)
        npm_pkgs = list({i["package"] for i in npm_items})
        # Grep pattern: require('pkg') or from 'pkg' or from "pkg"
        # Escape @ and / for grep
        escaped = [p.replace("/", "\\/").replace("@", "\\@") for p in npm_pkgs]
        patterns = "|".join(
            f"require\\(['\"]({e})" + f"|from ['\"]({e})" for e in escaped
        )

        for proj in cluster.get("projects", []):
            pkey = proj.get("key", "")
            pdata = PROJECTS.get(pkey, {})
            host = pdata.get("host", "")
            path = pdata.get("path", "")
            if not host or not path:
                continue

            ok, output = ssh_cmd(
                host,
                f"grep -rh --include='*.js' --include='*.ts' --include='*.tsx' --include='*.jsx' "
                f"-E '{patterns}' {path}/src/ {path}/lib/ {path}/backend/ 2>/dev/null | sort -u | head -30",
                timeout=10,
            )
            if not ok or not output.strip():
                continue

            for line in output.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                for pkg in npm_pkgs:
                    if pkg in line:
                        if pkg not in usage:
                            usage[pkg] = {}
                        if pkey not in usage[pkg]:
                            usage[pkg][pkey] = []
                        usage[pkg][pkey].append(line)
                        break

    return usage


_analysis_lock = threading.Lock()
_analysis_state = {}  # {cluster_id: "pending"|"running"|"done"|"error"}


REGROUP_PROMPT = """\
You are grouping {count} Python/npm packages into logical clusters for update analysis.
Each group should contain packages that belong together — same ecosystem, same purpose, \
or packages that depend on each other. Aim for groups of 5-25 packages.

Package list (name: current → available, deps):
{package_list}

Respond with ONLY a JSON object — no markdown, no explanation:
{{"groups": [{{"name": "short group name", "packages": ["pkg1", "pkg2"]}}, ...]}}

Every package must appear in exactly one group. Use descriptive names like \
"NVIDIA CUDA", "Google Cloud", "LLM Clients", "Web Framework", "Testing", etc."""


def _llm_regroup_cluster(items):
    """Ask LLM to group a large set of packages into logical sub-clusters.
    Returns list of {"name": str, "packages": [str]} or None on failure."""
    pkg_lines = []
    for item in items:
        deps = item.get("required_by", [])[:5]
        dep_str = f" (needed by: {', '.join(deps)})" if deps else ""
        pkg_lines.append(
            f"  {item['package']}: {item.get('current', '?')} → {item.get('available', '?')}{dep_str}"
        )

    user_msg = REGROUP_PROMPT.format(
        count=len(items),
        package_list="\n".join(pkg_lines),
    )

    result = llm_query(
        DEFAULT_MODEL,
        "You are a package management expert. Respond with ONLY JSON.",
        user_msg,
        timeout=60, temperature=0.3, num_predict=4096,
    )

    if result and isinstance(result, dict) and "groups" in result:
        return result["groups"]
    return None


def _analyze_single_cluster(cluster, items, config):
    """Run LLM analysis for one cluster with release notes + actual usage context."""
    cluster_id = cluster["id"]
    with _analysis_lock:
        _analysis_state[cluster_id] = "running"

    try:
        # Step 0: Scan actual usage of packages in affected projects
        usage_data = _scan_actual_usage(cluster, items)

        # Step 1: Fetch release notes for all packages in parallel
        release_data = _gather_cluster_release_notes(items)

        # Store intel on items
        for item in items:
            pkg = item.get("package", "")
            if pkg in release_data:
                item["intel"] = {
                    "repo": release_data[pkg]["repo"],
                    "releases_skipped": release_data[pkg]["releases_skipped"],
                    "semver_change": _compute_semver_change(
                        item.get("current", ""), item.get("available", "")
                    ),
                }

        # Step 2: Build prompt with release notes context
        prompt_template = config.get("cluster_prompt") or DEFAULT_CLUSTER_PROMPT
        host_names = ", ".join(
            HOSTS.get(h, {}).get("name", h) for h in cluster.get("hosts", [])
        )
        projects_str = ", ".join(
            f"{p['name']} ({p['tagline']})" for p in cluster.get("projects", [])[:5]
        ) or "none identified"

        system_prompt = prompt_template.format(
            cluster_name=cluster.get("name", "Unknown"),
            host_names=host_names,
            host_roles=", ".join(cluster.get("host_roles", [])) or "general",
            projects=projects_str,
            risk_score=cluster.get("risk_score", 0),
        )

        # Build per-package sections with inline release notes
        pkg_sections = []
        for item in items:
            pkg = item["package"]
            rb_count = item.get("required_by_count", 0)
            rb_str = f" ({rb_count} packages depend on this)" if rb_count else ""
            deps_str = ""
            if item.get("required_by"):
                deps_str = f"\n  Required by: {', '.join(item['required_by'][:5])}"
            semver = ""
            intel = item.get("intel") or {}
            if intel.get("semver_change"):
                semver = f" [{intel['semver_change']}]"

            section = (
                f"=== {pkg} {item.get('current', '?')} → "
                f"{item.get('available', '?')}{rb_str}{semver} ==="
                f"{deps_str}"
            )

            # Actual usage in projects (from grep)
            pkg_usage = usage_data.get(pkg, {})
            if pkg_usage:
                section += "\n  ACTUAL USAGE IN PROJECTS:"
                for proj_key, imports in pkg_usage.items():
                    section += f"\n    {proj_key}: {'; '.join(imports[:5])}"
            else:
                section += "\n  NOT IMPORTED by any project on this host"

            # Inline release notes for this package
            if pkg in release_data:
                releases = release_data[pkg].get("releases", [])
                if releases:
                    section += f"\n  Release notes ({len(releases)} releases):"
                    for r in releases[:6]:
                        section += f"\n  v{r['version']} ({r['date']}): {r['notes'][:300]}"

            pkg_sections.append(section)

        user_msg = "\n\n".join(pkg_sections)

        # Try llm_query (JSON mode) first, fall back to text + parse
        result = llm_query(
            DEFAULT_MODEL, system_prompt, user_msg,
            timeout=120, temperature=0.2, num_predict=4096,
        )

        if not result:
            # Fallback: model returned markdown instead of JSON — parse it
            raw = llm_query_text(
                DEFAULT_MODEL, system_prompt, user_msg,
                timeout=120, temperature=0.2, num_predict=4096,
            )
            if raw:
                result = _parse_cluster_analysis(raw)

        if not result:
            cluster["analysis"] = {"error": "LLM returned no result"}
            with _analysis_lock:
                _analysis_state[cluster_id] = "error"
            return

        cluster["analysis"] = result
        cluster["analyzed_at"] = int(time.time())

        # Apply per-item classifications (tag-based: keyed by package name)
        item_decisions = result.get("items", {}) if isinstance(result, dict) else {}
        if isinstance(item_decisions, dict):
            for item in items:
                pkg = item.get("package", "")
                d = item_decisions.get(pkg) or item_decisions.get(pkg.lower())
                if isinstance(d, dict):
                    item["classification"] = d.get("classification", "review")
                    item["reason"] = d.get("reason", "")
                    item["source"] = "llm"

        with _analysis_lock:
            _analysis_state[cluster_id] = "done"

    except Exception as e:
        cluster["analysis"] = {"error": str(e)}
        with _analysis_lock:
            _analysis_state[cluster_id] = "error"


def _regroup_oversized(cluster_id, clusters, item_map, results):
    """Split an oversized cluster into LLM-determined sub-clusters.
    Modifies clusters dict in place: removes parent, adds children.
    Returns list of new cluster IDs, or empty list on failure."""
    cluster = clusters.get(cluster_id)
    if not cluster:
        return []

    cluster_items = [item_map[iid] for iid in cluster.get("item_ids", []) if iid in item_map]
    if len(cluster_items) <= MAX_CLUSTER_SIZE:
        return []

    with _analysis_lock:
        _analysis_state[cluster_id] = "regrouping"

    groups = _llm_regroup_cluster(cluster_items)
    if not groups:
        return []

    # Build package→item lookup
    pkg_map = {i["package"].lower(): i for i in cluster_items}
    # Also try exact case
    pkg_map_exact = {i["package"]: i for i in cluster_items}

    new_cids = []
    assigned = set()

    for g in groups:
        gname = g.get("name", "Unnamed")
        gpkgs = g.get("packages", [])
        if not gpkgs:
            continue

        gitems = []
        for p in gpkgs:
            item = pkg_map_exact.get(p) or pkg_map.get(p.lower())
            if item and item["id"] not in assigned:
                gitems.append(item)
                assigned.add(item["id"])

        if not gitems:
            continue

        host = cluster.get("hosts", ["unknown"])[0]
        safe = re.sub(r'[^a-z0-9_]', '_', gname.lower())[:30].strip("_")
        safe = re.sub(r'_+', '_', safe)
        cid = f"regrouped__{host}__{safe}"
        # Ensure unique
        if cid in clusters:
            cid = f"{cid}_{len(new_cids)}"

        clusters[cid] = {
            "id": cid,
            "name": gname,
            "hosts": cluster.get("hosts", []),
            "dimension": cluster.get("dimension", "pip"),
            "item_ids": [i["id"] for i in gitems],
            "item_count": len(gitems),
            "static": False,
            "tier": "regrouped",
            "parent_cluster": cluster_id,
            "projects": cluster.get("projects", []),
            "host_roles": cluster.get("host_roles", []),
            "risk_score": sum(i.get("required_by_count", 0) for i in gitems),
        }
        for item in gitems:
            item["cluster_id"] = cid
        new_cids.append(cid)

    # Any unassigned items stay in a remainder cluster
    unassigned = [i for i in cluster_items if i["id"] not in assigned]
    if unassigned:
        host = cluster.get("hosts", ["unknown"])[0]
        rem_cid = f"regrouped__{host}__other"
        if rem_cid in clusters:
            rem_cid = f"{rem_cid}_{len(new_cids)}"
        clusters[rem_cid] = {
            "id": rem_cid,
            "name": f"Other ({host.upper()})",
            "hosts": cluster.get("hosts", []),
            "dimension": cluster.get("dimension", "pip"),
            "item_ids": [i["id"] for i in unassigned],
            "item_count": len(unassigned),
            "static": False,
            "tier": "regrouped",
            "parent_cluster": cluster_id,
            "projects": cluster.get("projects", []),
            "host_roles": cluster.get("host_roles", []),
            "risk_score": sum(i.get("required_by_count", 0) for i in unassigned),
        }
        for item in unassigned:
            item["cluster_id"] = rem_cid
        new_cids.append(rem_cid)

    # Remove the parent cluster
    if new_cids:
        del clusters[cluster_id]
        with _analysis_lock:
            _analysis_state[cluster_id] = "regrouped"

    return new_cids


def analyze_clusters(cluster_ids=None):
    """Run LLM analysis on clusters. Runs in background threads (max 3 concurrent).
    Oversized clusters get LLM-regrouped first, then each sub-cluster is analyzed."""
    scan_data = load_scan()
    results = scan_data.get("results", [])
    clusters = scan_data.get("clusters", {})
    item_map = {i["id"]: i for i in results}
    config = load_updates_config()

    targets = cluster_ids or list(clusters.keys())
    # Filter: explicit cluster_ids always re-analyze; bulk analyze skips already-done
    to_analyze = []
    for cid in targets:
        cluster = clusters.get(cid)
        if cluster and cluster.get("item_count", 0) > 0:
            if cluster_ids or not cluster.get("analysis"):
                to_analyze.append(cid)

    if not to_analyze:
        return {"status": "nothing_to_analyze", "clusters": 0}

    # Phase 1: Regroup oversized clusters (sequential, modifies clusters dict)
    regrouped = []
    still_to_analyze = []
    for cid in to_analyze:
        cluster = clusters.get(cid)
        if not cluster:
            continue
        if cluster.get("item_count", 0) > MAX_CLUSTER_SIZE:
            new_cids = _regroup_oversized(cid, clusters, item_map, results)
            if new_cids:
                regrouped.extend(new_cids)
                _save_scan(results, clusters)
                continue
        still_to_analyze.append(cid)

    # Add regrouped sub-clusters to the analysis queue
    still_to_analyze.extend(regrouped)

    if not still_to_analyze:
        return {"status": "nothing_to_analyze", "clusters": 0, "regrouped": len(regrouped)}

    # Phase 2: Initialize state and run analysis
    with _analysis_lock:
        for cid in still_to_analyze:
            _analysis_state[cid] = "pending"

    def _worker(cid):
        cluster = clusters.get(cid)
        if not cluster:
            return
        cluster_items = [item_map[iid] for iid in cluster.get("item_ids", []) if iid in item_map]
        _analyze_single_cluster(cluster, cluster_items, config)
        # Save after each cluster completes
        _save_scan(results, clusters)

    # Run with max 3 concurrent threads
    semaphore = threading.Semaphore(3)
    def _throttled(cid):
        with semaphore:
            _worker(cid)

    threads = []
    for cid in still_to_analyze:
        t = threading.Thread(target=_throttled, args=(cid,), daemon=True)
        t.start()
        threads.append(t)

    # Don't block — return immediately, frontend polls status
    return {
        "status": "started",
        "clusters": len(still_to_analyze),
        "cluster_ids": still_to_analyze,
        "regrouped": len(regrouped),
    }


def get_analyze_status():
    """Return analysis progress."""
    with _analysis_lock:
        state = dict(_analysis_state)
    done = sum(1 for v in state.values() if v == "done")
    running = sum(1 for v in state.values() if v == "running")
    pending = sum(1 for v in state.values() if v == "pending")
    errors = sum(1 for v in state.values() if v == "error")
    return {
        "total": len(state),
        "done": done,
        "running": running,
        "pending": pending,
        "errors": errors,
        "complete": pending == 0 and running == 0,
        "clusters": state,
    }


# ── Stage 7: Intelligence (on-demand) ───────────────────────────────────────

def _compute_semver_change(current, available):
    """Determine if version change is major, minor, or patch."""
    def parse_ver(v):
        parts = re.split(r'[.\-+]', re.sub(r'[^0-9.]', '.', v.split('+')[0]))
        nums = []
        for p in parts:
            try:
                nums.append(int(p))
            except ValueError:
                break
        while len(nums) < 3:
            nums.append(0)
        return nums[:3]

    try:
        c = parse_ver(current)
        a = parse_ver(available)
        if a[0] != c[0]:
            return "major"
        if a[1] != c[1]:
            return "minor"
        return "patch"
    except Exception:
        return "unknown"


def enrich_cluster(cluster_id):
    """Fetch intelligence for a cluster's items. Returns enriched items."""
    scan_data = load_scan()
    results = scan_data.get("results", [])
    clusters = scan_data.get("clusters", {})
    cluster = clusters.get(cluster_id)
    if not cluster:
        return {"error": "Cluster not found"}

    item_map = {i["id"]: i for i in results}
    enriched = 0

    for item_id in cluster.get("item_ids", []):
        item = item_map.get(item_id)
        if not item or item.get("intel"):
            continue

        intel = {
            "semver_change": _compute_semver_change(
                item.get("current", ""), item.get("available", "")
            ),
        }

        # APT: fetch changelog for security packages
        if item.get("dimension") == "apt" and item.get("is_security"):
            host = item.get("host", LOCAL_HOST)
            ok, output = ssh_cmd(
                host,
                f"apt-get changelog {item['package']} 2>/dev/null | head -30",
                timeout=15,
            )
            if ok and output:
                cves = re.findall(r'CVE-\d{4}-\d+', output)
                urgency_m = re.search(r'urgency=(\w+)', output)
                intel["cves"] = list(set(cves))
                intel["urgency"] = urgency_m.group(1) if urgency_m else None
                intel["changelog_snippet"] = output[:500]

        item["intel"] = intel
        enriched += 1

    _save_scan(results, clusters)
    return {"enriched": enriched, "cluster_id": cluster_id}


# ── Master scan ──────────────────────────────────────────────────────────────

DIMENSION_SCANNERS = {
    "apt": lambda hosts: _scan_apt(hosts),
    "pip": lambda hosts: _scan_pip(hosts),
    "npm": lambda hosts: _scan_npm(hosts),
    "cargo": lambda hosts: _scan_cargo(hosts),
    "gha": lambda _: _scan_github_actions(),
    "docker": lambda hosts: _scan_docker(hosts),
    "azure": lambda _: _scan_azure(),
    "infra": lambda hosts: _scan_infra(hosts),
    "devtools": lambda hosts: _scan_devtools(hosts),
}


def scan(dimensions=None, hosts=None):
    """
    Clustered scan pipeline:
      1. Gather raw updates (apt + pip with deps)
      2. Cluster by influence
      3. Enrich clusters with project context
      4. Apply blocklist + rules
      5. Save
    LLM analysis is a separate step (analyze_clusters endpoint).
    """
    config = load_updates_config()
    enabled_dims = dimensions or config.get("enabled_dimensions", ["apt", "pip"])
    enabled_hosts = hosts or config.get("enabled_hosts", list(HOSTS.keys()))
    valid_hosts = [h for h in enabled_hosts if h in HOSTS]

    # Stage 1: Gather — dispatch dimension scanners in parallel
    dim_results = {}
    def _run_dim(dim, scanner, h):
        try:
            dim_results[dim] = scanner(h)
        except Exception:
            dim_results[dim] = []

    threads = []
    for dim in enabled_dims:
        scanner = DIMENSION_SCANNERS.get(dim)
        if scanner:
            t = threading.Thread(target=_run_dim, args=(dim, scanner, valid_hosts), daemon=True)
            t.start()
            threads.append(t)
    for t in threads:
        t.join(timeout=120)

    all_items = []
    for dim in enabled_dims:
        all_items.extend(dim_results.get(dim, []))

    # Stage 4 (before clustering): Blocklist
    rules = load_rules()
    for item in all_items:
        if _is_blocklisted(item, config):
            item["classification"] = "blocked"
            item["reason"] = "Blocklisted — unsafe to update"
            item["source"] = "blocklist"

    # Rules fast-filter
    for item in all_items:
        if item["classification"] is not None:
            continue
        action, rule_id = _match_rules(item, rules)
        if action:
            item["rule_matched"] = rule_id
            if action == "skip":
                item["classification"] = "noise"
                item["reason"] = "Matched skip rule"
                item["source"] = "rule"
            elif action == "approve":
                item["classification"] = "review"
                item["reason"] = "Matched auto-approve rule"
                item["source"] = "rule"
                item["approved"] = True
            elif action.startswith("classify:"):
                item["classification"] = action.split(":", 1)[1]
                item["reason"] = "Matched classification rule"
                item["source"] = "rule"
    save_rules(rules)

    # Stage 2: Cluster
    clusters = _build_clusters(all_items, config)

    # Stage 3: Enrich clusters with context
    _enrich_clusters(clusters, all_items)

    # Stage 5: Save
    _save_scan(all_items, clusters)

    # Reset analysis state
    with _analysis_lock:
        _analysis_state.clear()

    # Stats
    counts = {"total": len(all_items), "urgent": 0, "review": 0,
              "noise": 0, "blocked": 0, "pending": 0}
    for item in all_items:
        cls = item.get("classification", "review")
        if cls in counts:
            counts[cls] += 1
        if item.get("approved") is None and item.get("classification") is None:
            counts["pending"] += 1

    return {
        "scanned": len(all_items),
        "dimensions": enabled_dims,
        "hosts": valid_hosts,
        "cluster_count": len(clusters),
        **counts,
        "results": all_items,
        "clusters": clusters,
    }


# ── Scan persistence ─────────────────────────────────────────────────────────

_save_lock = threading.Lock()


def _save_scan(results, clusters=None):
    with _save_lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {"ts": int(time.time()), "results": results}
        if clusters is not None:
            data["clusters"] = clusters
        SCAN_FILE.write_text(json.dumps(data, indent=2) + "\n")


def load_scan():
    if SCAN_FILE.exists():
        try:
            return json.loads(SCAN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"ts": None, "results": [], "clusters": {}}


# ── Apply ────────────────────────────────────────────────────────────────────

def apply_actions(approvals):
    scan_data = load_scan()
    results = scan_data.get("results", [])
    clusters = scan_data.get("clusters", {})
    approved_items = []
    skipped = 0

    for item in results:
        item_id = item.get("id")
        decision = approvals.get(item_id)
        if decision == "approve":
            item["approved"] = True
            approved_items.append(item)
            _log_action({
                "ts": int(time.time()), "action": "approve",
                "id": item_id, "dimension": item.get("dimension"),
                "host": item.get("host"), "package": item.get("package"),
                "current": item.get("current"), "available": item.get("available"),
            })
        elif decision == "skip":
            item["approved"] = False
            skipped += 1
            _log_action({
                "ts": int(time.time()), "action": "skip",
                "id": item_id, "dimension": item.get("dimension"),
                "host": item.get("host"), "package": item.get("package"),
            })

    _save_scan(results, clusters)
    commands = _generate_commands(approved_items)
    return {"approved": len(approved_items), "skipped": skipped, "commands": commands}


def _generate_commands(items):
    by_host = {}
    for item in items:
        host = item.get("host", "unknown")
        if host not in by_host:
            by_host[host] = {"apt": [], "pip": [], "npm": [], "other": []}
        dim = item.get("dimension", "other")
        if dim in by_host[host]:
            by_host[host][dim].append(item)
        else:
            by_host[host]["other"].append(item)

    commands = {}
    for host, groups in by_host.items():
        host_cmds = []
        apt_pkgs = [i["package"] for i in groups.get("apt", [])]
        if apt_pkgs:
            host_cmds.append(f"sudo apt-get install --only-upgrade {' '.join(apt_pkgs)}")
        pip_specs = [f"{i['package']}=={i['available']}" for i in groups.get("pip", [])]
        if pip_specs:
            host_cmds.append(f"pip install --upgrade {' '.join(pip_specs)}")
        # npm: group global installs, per-project installs grouped by project path
        npm_items = groups.get("npm", [])
        npm_global = [i for i in npm_items if not i.get("project")]
        npm_project = [i for i in npm_items if i.get("project")]
        if npm_global:
            specs = [f"{i['package']}@{i['available']}" for i in npm_global]
            host_cmds.append(f"npm -g install {' '.join(specs)}")
        # Group project npm items by project path (from apply_cmd)
        proj_groups = {}
        for i in npm_project:
            pkey = i.get("project", "")
            pdata = PROJECTS.get(pkey, {})
            path = pdata.get("path", "")
            if path not in proj_groups:
                proj_groups[path] = []
            proj_groups[path].append(i)
        for path, pitems in proj_groups.items():
            specs = [f"{i['package']}@{i['available']}" for i in pitems]
            host_cmds.append(f"cd {path} && npm install {' '.join(specs)}")
        for item in groups.get("other", []):
            cmd = item.get("apply_cmd")
            if cmd:
                host_cmds.append(cmd)
        total = len(apt_pkgs) + len(groups.get("pip", [])) + len(npm_items) + len(groups.get("other", []))
        if host_cmds:
            host_name = HOSTS.get(host, {}).get("name", host)
            commands[host] = {
                "host_name": host_name, "commands": host_cmds,
                "count": total,
            }
    return commands


def export_script(host, item_ids=None):
    scan_data = load_scan()
    results = scan_data.get("results", [])
    items = [i for i in results if i.get("host") == host and i.get("approved")]
    if item_ids:
        id_set = set(item_ids)
        items = [i for i in items if i.get("id") in id_set]
    commands = _generate_commands(items)
    host_data = commands.get(host, {"commands": [], "count": 0})
    host_name = HOSTS.get(host, {}).get("name", host)
    lines = [
        "#!/bin/bash",
        f"# Update script for {host_name} — generated {time.strftime('%Y-%m-%d %H:%M')} by OI WebUI",
        "# Review carefully before running!",
        "", "set -euo pipefail", "",
    ]
    for cmd in host_data.get("commands", []):
        lines.append(f"echo '=== {cmd.split()[0]} ==='")
        lines.append(cmd)
        lines.append("")
    lines.append("echo 'Done.'")
    return {"script": "\n".join(lines), "host": host, "host_name": host_name}


def deploy_cluster_script(cluster_id, excluded=None):
    """Generate and deploy update script to target host via SSH.
    Writes to ~/oi-scripts/{cluster-name}/update-{date}.sh on each host.
    excluded: list of item IDs to exclude from the script."""
    scan_data = load_scan()
    results = scan_data.get("results", [])
    clusters = scan_data.get("clusters", {})
    cluster = clusters.get(cluster_id)
    if not cluster:
        return {"ok": False, "error": "Cluster not found"}

    excluded_set = set(excluded or [])
    item_map = {i["id"]: i for i in results}
    cluster_items = [item_map[iid] for iid in cluster.get("item_ids", []) if iid in item_map]

    # Include all non-blocked items except explicitly excluded ones
    approved = [i for i in cluster_items
                if i.get("classification") != "blocked" and i.get("id") not in excluded_set]

    if not approved:
        return {"ok": False, "error": "No items to deploy"}

    # Group by host (a cluster can span multiple hosts)
    by_host = {}
    for item in approved:
        h = item.get("host", "unknown")
        if h not in by_host:
            by_host[h] = []
        by_host[h].append(item)

    # Sanitize cluster name for filesystem
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', cluster.get("name", cluster_id)).strip("_").lower()
    safe_name = re.sub(r'_+', '_', safe_name)[:50]
    date_str = time.strftime("%Y-%m-%d")
    deployed = []

    for host, items in by_host.items():
        commands = _generate_commands(items)
        host_data = commands.get(host, {"commands": []})
        if not host_data.get("commands"):
            continue

        host_name = HOSTS.get(host, {}).get("name", host)
        script_dir = f"~/oi-scripts/{safe_name}"
        script_name = f"update-{date_str}.sh"
        script_path = f"{script_dir}/{script_name}"

        # Build script content
        lines = [
            "#!/bin/bash",
            f"# Cluster: {cluster.get('name', cluster_id)}",
            f"# Host: {host_name}",
            f"# Generated: {time.strftime('%Y-%m-%d %H:%M')} by OI WebUI",
            f"# Items: {len(items)}",
            "",
        ]

        # Add analysis context as comments if available
        a = cluster.get("analysis")
        if isinstance(a, dict):
            if a.get("recommendation"):
                lines.append(f"# Recommendation: {a['recommendation'].upper()}")
            if a.get("reasoning"):
                for rline in a["reasoning"][:200].split(". "):
                    lines.append(f"#   {rline.strip()}")
            if a.get("update_order"):
                lines.append(f"# Update order: {' -> '.join(a['update_order'])}")
            if a.get("breaking_changes"):
                lines.append("#")
                lines.append("# BREAKING CHANGES:")
                for bc in a["breaking_changes"]:
                    lines.append(f"#   - {bc[:120]}")
            lines.append("")

        lines.append("set -euo pipefail")
        lines.append("")

        for cmd in host_data["commands"]:
            lines.append(f'echo "=== {cmd.split()[0]} ==="')
            lines.append(cmd)
            lines.append("")

        lines.append('echo "Done."')
        script_content = "\n".join(lines)

        # Deploy via SSH: mkdir + write file
        # Escape single quotes in script content for the heredoc
        escaped = script_content.replace("'", "'\\''")
        deploy_cmd = (
            f"mkdir -p {script_dir} && "
            f"cat > {script_path} << 'OISCRIPT'\n{script_content}\nOISCRIPT\n"
            f"chmod +x {script_path} && echo 'OK'"
        )

        ok, output = ssh_cmd(host, deploy_cmd, timeout=15)
        if ok and "OK" in output:
            deployed.append({
                "host": host,
                "host_name": host_name,
                "path": script_path,
                "items": len(items),
                "commands": len(host_data["commands"]),
            })
        else:
            deployed.append({
                "host": host,
                "host_name": host_name,
                "error": f"Deploy failed: {output[:200]}",
            })

    # Track deployment
    cluster["deployed_at"] = int(time.time())
    _save_scan(results, clusters)

    return {
        "ok": True,
        "cluster_name": cluster.get("name", cluster_id),
        "deployed": deployed,
    }


def deploy_bulk(cluster_ids, excluded=None):
    """Deploy multiple clusters as one script per host.
    Combines all items from selected clusters, grouped by dimension."""
    scan_data = load_scan()
    results = scan_data.get("results", [])
    clusters = scan_data.get("clusters", {})
    item_map = {i["id"]: i for i in results}
    excluded_set = set(excluded or [])

    # Gather all items from selected clusters
    all_items = []
    deployed_cids = []
    for cid in cluster_ids:
        cluster = clusters.get(cid)
        if not cluster:
            continue
        cluster_items = [item_map[iid] for iid in cluster.get("item_ids", []) if iid in item_map]
        approved = [i for i in cluster_items
                    if i.get("classification") != "blocked" and i["id"] not in excluded_set]
        all_items.extend(approved)
        deployed_cids.append(cid)

    if not all_items:
        return {"ok": False, "error": "No items to deploy"}

    # Group by host
    by_host = {}
    for item in all_items:
        h = item.get("host", "unknown")
        by_host.setdefault(h, []).append(item)

    date_str = time.strftime("%Y-%m-%d")
    deployed = []

    for host, items in by_host.items():
        commands = _generate_commands(items)
        host_data = commands.get(host, {"commands": []})
        if not host_data.get("commands"):
            continue

        host_name = HOSTS.get(host, {}).get("name", host)
        script_dir = "~/oi-scripts/bulk"
        script_path = f"{script_dir}/update-{date_str}.sh"

        # Build script with cluster sections
        lines = [
            "#!/bin/bash",
            f"# Bulk update — {len(deployed_cids)} clusters, {len(items)} items",
            f"# Host: {host_name}",
            f"# Generated: {time.strftime('%Y-%m-%d %H:%M')} by OI WebUI",
            "",
        ]

        # List included clusters
        for cid in deployed_cids:
            c = clusters.get(cid, {})
            if host in c.get("hosts", []):
                a = c.get("analysis", {})
                rec = a.get("recommendation", "?") if isinstance(a, dict) else "?"
                lines.append(f"# - {c.get('name', cid)} ({rec})")
        lines.append("")
        lines.append("set -euo pipefail")
        lines.append("")

        for cmd in host_data["commands"]:
            lines.append(f'echo "=== {cmd.split()[0]} ==="')
            lines.append(cmd)
            lines.append("")

        lines.append('echo "Done."')
        script_content = "\n".join(lines)

        deploy_cmd = (
            f"mkdir -p {script_dir} && "
            f"cat > {script_path} << 'OISCRIPT'\n{script_content}\nOISCRIPT\n"
            f"chmod +x {script_path} && echo 'OK'"
        )

        ok, output = ssh_cmd(host, deploy_cmd, timeout=15)
        if ok and "OK" in output:
            deployed.append({
                "host": host, "host_name": host_name,
                "path": script_path, "items": len(items),
                "commands": len(host_data["commands"]),
            })
        else:
            deployed.append({
                "host": host, "host_name": host_name,
                "error": f"Deploy failed: {output[:200]}",
            })

    # Track deployment on all clusters
    now = int(time.time())
    for cid in deployed_cids:
        if cid in clusters:
            clusters[cid]["deployed_at"] = now
    _save_scan(results, clusters)

    return {
        "ok": True,
        "clusters": len(deployed_cids),
        "deployed": deployed,
    }


# ── Cluster Q&A ──────────────────────────────────────────────────────────────

def ask_cluster(cluster_id, question):
    """Ask a follow-up question about a cluster with full context."""
    if not llm_query_text or not DEFAULT_MODEL:
        return {"ok": False, "error": "LLM not available"}

    scan_data = load_scan()
    results = scan_data.get("results", [])
    clusters = scan_data.get("clusters", {})
    cluster = clusters.get(cluster_id)
    if not cluster:
        return {"ok": False, "error": "Cluster not found"}

    item_map = {i["id"]: i for i in results}
    items = [item_map[iid] for iid in cluster.get("item_ids", []) if iid in item_map]

    # Build rich context
    host_names = ", ".join(HOSTS.get(h, {}).get("name", h) for h in cluster.get("hosts", []))
    projects_str = ", ".join(
        f"{p['name']} ({p['tagline']})" for p in cluster.get("projects", [])[:5]
    ) or "none identified"

    # Package details
    pkg_lines = []
    for item in items:
        rb = item.get("required_by", [])
        rb_str = f" (needed by: {', '.join(rb[:5])})" if rb else ""
        reason = f" — LLM: {item['reason']}" if item.get("reason") else ""
        intel = item.get("intel", {})
        semver = f" [{intel.get('semver_change', '?')}]" if intel else ""
        pkg_lines.append(
            f"  {item['package']} {item.get('current', '?')} → {item.get('available', '?')}"
            f"{semver}{rb_str}{reason}"
        )

    # Include analysis if available
    analysis_ctx = ""
    a = cluster.get("analysis")
    if isinstance(a, dict) and not a.get("error"):
        analysis_ctx = f"\nPrevious analysis:\n"
        analysis_ctx += f"  Recommendation: {a.get('recommendation', '?')}\n"
        analysis_ctx += f"  Reasoning: {a.get('reasoning', '')}\n"
        if a.get("breaking_changes"):
            analysis_ctx += "  Breaking: " + "; ".join(a["breaking_changes"]) + "\n"
        if a.get("new_features"):
            analysis_ctx += "  New: " + "; ".join(a["new_features"]) + "\n"

    system_prompt = (
        f"You are answering questions about the \"{cluster.get('name', '?')}\" update cluster.\n"
        f"Hosts: {host_names}. Projects: {projects_str}.\n"
        f"Packages:\n" + "\n".join(pkg_lines) + "\n"
        f"{analysis_ctx}\n"
        f"Answer concisely and specifically. Reference package names and versions."
    )

    answer = llm_query_text(
        DEFAULT_MODEL, system_prompt, question,
        timeout=60, temperature=0.3, num_predict=1024,
    )

    return {
        "ok": True,
        "answer": answer or "No response from LLM",
        "cluster_name": cluster.get("name", cluster_id),
    }


# ── Action log ───────────────────────────────────────────────────────────────

def _log_action(entry):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(ACTIONS_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_recent_actions(limit=50):
    if not ACTIONS_LOG.exists():
        return []
    try:
        lines = ACTIONS_LOG.read_text().strip().split("\n")
        lines = lines[-limit:]
        lines.reverse()
        return [json.loads(l) for l in lines if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []


# ── Insights ─────────────────────────────────────────────────────────────────

def refresh_insights():
    if not llm_query_text or not DEFAULT_MODEL:
        return {"advice": "LLM not available", "ts": int(time.time())}
    scan_data = load_scan()
    clusters = scan_data.get("clusters", {})
    if not clusters:
        return {"advice": "No scan results to analyze", "ts": int(time.time())}

    summary_lines = []
    for cid, cluster in sorted(clusters.items(), key=lambda x: -x[1].get("risk_score", 0)):
        hosts = ", ".join(cluster.get("hosts", []))
        risk = cluster.get("risk_score", 0)
        count = cluster.get("item_count", 0)
        sec = " [SECURITY]" if cluster.get("is_security") else ""
        summary_lines.append(f"- {cluster['name']} ({hosts}): {count} pkgs, risk={risk}{sec}")

    config = load_updates_config()
    prompt = config.get("cross_ref_prompt") or DEFAULT_CROSSREF_PROMPT
    summary = "\n".join(summary_lines)

    advice = llm_query_text(
        DEFAULT_MODEL, prompt,
        f"Update clusters:\n{summary}",
        timeout=60, temperature=0.3, num_predict=1024,
    )
    result = {"advice": advice or "Analysis returned empty", "ts": int(time.time())}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    INSIGHTS_FILE.write_text(json.dumps(result, indent=2) + "\n")
    return result


def get_insights():
    if INSIGHTS_FILE.exists():
        try:
            return json.loads(INSIGHTS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"advice": None, "ts": None}


# ── Overview ─────────────────────────────────────────────────────────────────

def get_overview():
    config = load_updates_config()
    scan_data = load_scan()
    results = scan_data.get("results", [])
    clusters = scan_data.get("clusters", {})
    rules = load_rules()
    insights = get_insights()

    counts = {"total": 0, "urgent": 0, "review": 0, "noise": 0,
              "blocked": 0, "pending": 0, "unclassified": 0}
    for item in results:
        counts["total"] += 1
        cls = item.get("classification")
        if cls and cls in counts:
            counts[cls] += 1
        elif cls is None:
            counts["unclassified"] += 1
        if item.get("approved") is None and cls not in ("blocked", "noise"):
            counts["pending"] += 1

    # Cluster summary
    cluster_summary = []
    for cid, c in sorted(clusters.items(), key=lambda x: -x[1].get("risk_score", 0)):
        cluster_summary.append({
            "id": cid,
            "name": c.get("name", cid),
            "hosts": c.get("hosts", []),
            "dimension": c.get("dimension", "mixed"),
            "item_ids": c.get("item_ids", []),
            "item_count": c.get("item_count", 0),
            "risk_score": c.get("risk_score", 0),
            "is_security": c.get("is_security", False),
            "projects": c.get("projects", []),
            "host_roles": c.get("host_roles", []),
            "static": c.get("static", False),
            "tier": c.get("tier"),
            "influenced_clusters": c.get("influenced_clusters", []),
            "analysis": {k: v for k, v in c["analysis"].items() if k != "raw"} if isinstance(c.get("analysis"), dict) else c.get("analysis"),
            "analyzed_at": c.get("analyzed_at"),
            "deployed_at": c.get("deployed_at"),
            "description": c.get("description", ""),
        })

    return {
        "config": config,
        "scan_ts": scan_data.get("ts"),
        "scan_results": results,
        "clusters": cluster_summary,
        "counts": counts,
        "rules": rules,
        "rules_count": len(rules),
        "rules_enabled": sum(1 for r in rules if r.get("enabled")),
        "recent_actions": get_recent_actions(20),
        "insights": insights,
        "llm_available": bool(llm_query and DEFAULT_MODEL),
        "hosts": {k: v.get("name", k) for k, v in HOSTS.items()},
        "dimensions": list(DIMENSION_SCANNERS.keys()),
    }

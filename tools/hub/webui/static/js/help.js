/* OI WebUI - Help tab: in-app documentation */

const Help = {

  // ── Documentation sections (prose) ─────────────────────────────────────────
  docs: [
    {
      id: 'overview',
      title: 'What is this?',
      body: `
        <p>This is the <strong>Multi-Machine Dev Hub</strong> - a set of 15 CLI tools that manage
        projects, git, services, backups, and LLM workflows across multiple machines from a single
        terminal. The WebUI gives you browser access to the same capabilities.</p>
        <p>Everything connects through SSH and two config files:</p>
        <ul>
          <li><code>~/.config/hub/config.json</code> - infrastructure (hosts, Ollama, GitHub, backup targets)</li>
          <li><code>~/.config/hub/projects.json</code> - project registry (names, paths, services, git remotes)</li>
        </ul>
        <p>Built on <a href="https://aiquest.info" target="_blank" rel="noopener">AIquest</a>,
        a fork of <a href="https://github.com/OpenInterpreter/open-interpreter" target="_blank" rel="noopener">Open Interpreter</a> v0.4.3.</p>
      `,
    },
    {
      id: 'architecture',
      title: 'Architecture',
      body: `
        <pre class="help-diagram">
+--------------+         SSH          +------------------+
|   Hub        |&lt;--------------------&gt;|   GPU Server     |
|              |                      |                  |
|  hub tools   |    +------------+    |  Ollama (LLM)    |
|  OI / Claude |    | Workstation|    |  Code Assistant  |
|  cron jobs   |&lt;--&gt;|            |    |  project repos   |
|  backups     |SSH | gh CLI     |    |  backup storage  |
+--------------+    | project    |    +------------------+
       ^            | repos      |             ^
       |            +------------+             |
       |              SSH ^                    |
       +------------------+--------------------+
                    all linked via
                  ~/.config/hub/config.json</pre>
        <p>The <strong>Hub</strong> is the always-on machine that runs the tools.
        It reaches other machines over SSH. Ollama runs wherever your GPU is.
        Projects can live on any host.</p>
        <p><strong>Host roles</strong> in config.json determine behavior:</p>
        <ul>
          <li><code>local</code> - the machine running hub tools</li>
          <li><code>ollama</code> - runs the Ollama LLM server</li>
          <li><code>code_assistant</code> - runs semantic search / RAG</li>
          <li><code>backup_target</code> - receives rsync backups</li>
          <li><code>wakeable</code> - supports Wake-on-LAN</li>
        </ul>
      `,
    },
    {
      id: 'getting-started',
      title: 'Getting Started',
      body: `
        <h4>Install</h4>
        <pre class="help-code">git clone https://github.com/thehighnotes/open-interpreter.git
cd open-interpreter
pip install -e .
python3 tools/hub/install.py</pre>
        <p>The install wizard detects your hostname, asks about remote hosts, configures Ollama
        and GitHub, writes <code>config.json</code>, and creates symlinks so tools are available from <code>~/</code>.</p>

        <h4>First steps</h4>
        <ol>
          <li><code>hub --status</code> - see your hosts, services, and caches</li>
          <li><code>hub --scan &lt;host&gt;</code> - discover projects on a machine</li>
          <li><code>repo</code> - git dashboard across all projects</li>
          <li><code>work &lt;project&gt;</code> - start a full work session</li>
        </ol>

        <h4>WebUI</h4>
        <p>Run <code>oi-web</code> to start this server on port 8585. Open
        <code>http://&lt;hub-ip&gt;:8585</code> from any device on the network.</p>
      `,
    },
    {
      id: 'session-flow',
      title: 'Session Flow',
      body: `
        <p>These four tools chain together into a session lifecycle.
        Run <code>work &lt;project&gt;</code> to trigger the whole flow, or use each tool standalone.</p>
        <table class="help-table">
          <tr><th>Step</th><th>Tool</th><th>What it does</th></tr>
          <tr><td>1</td><td><code>work</code></td><td>Pick project, chain prepare + overview + begin</td></tr>
          <tr><td>2</td><td><code>prepare</code></td><td>Wake hosts, warm Ollama, start services, refresh caches</td></tr>
          <tr><td>3</td><td><code>begin</code></td><td>Build context preamble, launch Claude Code or OI</td></tr>
          <tr><td>4</td><td><em>your work</em></td><td>Code, commit, test - use repo, code, edit, search</td></tr>
          <tr><td>5</td><td><code>autosummary</code></td><td>AI summary, stop dev services, log activity</td></tr>
          <tr><td>6</td><td><code>backup</code></td><td>Rsync hub ecosystem to backup target (optional)</td></tr>
        </table>
        <p><code>autosummary</code> runs as a background daemon. It polls for activity and auto-triggers
        when your session goes idle.</p>
      `,
    },
    {
      id: 'tools',
      title: 'Hub Tools',
      body: `
        <table class="help-table">
          <tr><th>Tool</th><th>Alias</th><th>Purpose</th></tr>
          <tr><td><code>hub</code></td><td>hub, status</td><td>Meta-tool: dashboard, priorities, services, scanning, config</td></tr>
          <tr><td><code>git</code></td><td>repo</td><td>Git management: dashboard, commit, push, checkpoint, deploy</td></tr>
          <tr><td><code>work</code></td><td></td><td>One-command session: prepare + overview + begin</td></tr>
          <tr><td><code>prepare</code></td><td></td><td>Wake hosts, warm Ollama, start services</td></tr>
          <tr><td><code>begin</code></td><td></td><td>Build context preamble, launch editor</td></tr>
          <tr><td><code>autosummary</code></td><td></td><td>Post-session AI summary daemon</td></tr>
          <tr><td><code>overview</code></td><td></td><td>LLM-powered project briefings</td></tr>
          <tr><td><code>research</code></td><td></td><td>Arxiv + GitHub release monitor, scored by relevance</td></tr>
          <tr><td><code>backup</code></td><td></td><td>Rsync hub ecosystem to remote host</td></tr>
          <tr><td><code>code</code></td><td></td><td>Code Assistant: semantic search, RAG, dependency graphs</td></tr>
          <tr><td><code>edit</code></td><td></td><td>Structured file editing (local and remote)</td></tr>
          <tr><td><code>notify</code></td><td></td><td>Notification history viewer</td></tr>
          <tr><td><code>health-probe</code></td><td></td><td>Host and service health checker (cron)</td></tr>
          <tr><td><code>hubgrep</code></td><td></td><td>Search across all hub files</td></tr>
          <tr><td><code>search</code></td><td></td><td>DuckDuckGo web search</td></tr>
          <tr><td><code>oi-web</code></td><td></td><td>This WebUI</td></tr>
        </table>
        <p>Every tool responds to <code>--help</code> for full usage details.</p>
      `,
    },
    {
      id: 'webui',
      title: 'Using the WebUI',
      body: `
        <h4>Tabs</h4>
        <table class="help-table">
          <tr><th>Tab</th><th>Content</th></tr>
          <tr><td><strong>Chat</strong></td><td>Full OI conversation with streaming, code approval, image upload</td></tr>
          <tr><td><strong>Status</strong></td><td>Hub dashboard (hosts, services, caches)</td></tr>
          <tr><td><strong>Projects</strong></td><td>Project list with switch buttons</td></tr>
          <tr><td><strong>Repo</strong></td><td>Git dashboard across all projects</td></tr>
          <tr><td><strong>Research</strong></td><td>Scored arxiv papers and GitHub releases</td></tr>
          <tr><td><strong>Notify</strong></td><td>Notification history</td></tr>
          <tr><td><strong>Help</strong></td><td>This page</td></tr>
          <tr><td><strong>Settings</strong></td><td>Model, context window, session reset</td></tr>
        </table>

        <h4>Chat features</h4>
        <ul>
          <li><strong>Streaming</strong> - responses stream token-by-token, rendered as markdown with syntax highlighting</li>
          <li><strong>Code approval</strong> - unsafe commands show Run/Skip buttons; safe commands auto-run</li>
          <li><strong>Magic commands</strong> - type <code>%status</code>, <code>%repo</code>, etc. directly in chat</li>
          <li><strong>Image upload</strong> - click the image button or drag and drop (requires vision-capable model)</li>
        </ul>

        <h4>Keyboard</h4>
        <table class="help-table">
          <tr><td><kbd>Enter</kbd></td><td>Send message</td></tr>
          <tr><td><kbd>Shift + Enter</kbd></td><td>New line</td></tr>
        </table>
      `,
    },
    {
      id: 'config',
      title: 'Configuration',
      body: `
        <h4>config.json</h4>
        <p>Infrastructure config created by <code>install.py</code>. Defines hosts, Ollama location,
        GitHub username, and backup target.</p>
        <pre class="help-code">{
  "hub": { "name": "My Dev Hub", "local_host": "nano" },
  "hosts": {
    "nano": { "name": "Hub", "ip": "127.0.0.1", "roles": ["local"] },
    "gpu":  { "name": "GPU", "ip": "192.168.1.100", "roles": ["ollama", "backup_target"] }
  },
  "ollama": { "host": "gpu", "port": 11434, "default_model": "llama3:8b" },
  "backup": { "destination": "gpu:~/hub-backup" },
  "git": { "github_username": "myuser", "email": "me@example.com" }
}</pre>

        <h4>projects.json</h4>
        <p>Project registry managed by <code>hub --scan</code> and <code>hub --manage</code>.
        Each project has a name, host, path, and optional services/dev_services.</p>

        <h4>Cron jobs</h4>
        <p>Recommended crontab entries:</p>
        <pre class="help-code"># Health check every 15 minutes
*/15 * * * * ~/health-probe

# Research fetch every 6 hours
0 */6 * * * ~/research --fetch

# Daily backup at 4am
0 4 * * * ~/backup

# Start autosummary daemon on boot
@reboot ~/autosummary &</pre>
      `,
    },
    {
      id: 'troubleshooting',
      title: 'Troubleshooting',
      body: `
        <h4>Common issues</h4>
        <table class="help-table">
          <tr><th>Problem</th><th>Fix</th></tr>
          <tr><td>Hub tools not found</td><td>Run <code>python3 tools/hub/install.py</code> to create symlinks, then <code>source ~/.bashrc</code></td></tr>
          <tr><td>SSH connection refused</td><td>Check <code>~/.ssh/config</code> has the host alias. Test with <code>ssh &lt;alias&gt; echo ok</code></td></tr>
          <tr><td>Ollama not responding</td><td>Verify Ollama is running on the host defined in config.json. Check with <code>curl http://&lt;host&gt;:11434/api/tags</code></td></tr>
          <tr><td>LLM returns empty response</td><td>If using a thinking model (qwen3/3.5), ensure <code>think: false</code> is set in the API payload</td></tr>
          <tr><td>WebUI won't connect</td><td>Check the interpreter is loaded. Look at the connection dot in the sidebar footer</td></tr>
          <tr><td>Projects not showing up</td><td>Run <code>hub --scan &lt;host&gt;</code> to discover projects, or <code>hub --manage</code> to add manually</td></tr>
          <tr><td>Git push fails</td><td>Run <code>repo fix &lt;project&gt;</code> to audit remotes, auth, and .gitignore</td></tr>
          <tr><td>Code Assistant down</td><td>Check if the service is running: <code>hub --services</code>. Restart via tmux on the host</td></tr>
          <tr><td>TUI menus broken</td><td>Non-TTY environments (pipes, OI) fall back to numbered input. This is expected</td></tr>
          <tr><td>Backup fails</td><td>Test SSH to backup target: <code>ssh &lt;host&gt; ls ~/</code>. Check destination path in config.json</td></tr>
        </table>

        <h4>Logs and state</h4>
        <ul>
          <li><code>~/.cache/overview/</code> - project overview cache</li>
          <li><code>~/.cache/research/</code> - research state and scores</li>
          <li><code>~/.cache/health/</code> - health probe results</li>
          <li><code>~/.cache/hub/notifications.jsonl</code> - notification history</li>
          <li><code>~/.cache/hub/timeline/</code> - session journals</li>
        </ul>
      `,
    },
  ],

  // ── Command quick-reference data ───────────────────────────────────────────
  quickRef: [
    {
      title: 'Hub Commands',
      entries: [
        { cmd: 'hub --status', desc: 'Live dashboard of hosts, services, and caches' },
        { cmd: 'hub --next', desc: 'AI-suggested next tasks based on project state' },
        { cmd: 'hub --explain', desc: 'AI explanation of hub status and topology' },
        { cmd: 'hub --scan <host>', desc: 'Discover projects, services, and dev scripts' },
        { cmd: 'hub --manage', desc: 'Interactive project editor (TUI)' },
        { cmd: 'hub --services', desc: 'Live service status across all projects' },
        { cmd: 'hub --dev', desc: 'Toggle dev services on/off (TUI)' },
      ],
    },
    {
      title: 'Git Commands',
      entries: [
        { cmd: 'repo', desc: 'Git dashboard - branch, log, remotes, dirty state' },
        { cmd: 'repo commit <proj> "msg"', desc: 'Stage all + commit (LLM message if omitted)' },
        { cmd: 'repo push <proj>', desc: 'Push with pre-flight checks' },
        { cmd: 'repo checkpoint', desc: 'Batch commit+push all dirty projects' },
        { cmd: 'repo deploy <proj>', desc: 'Commit + push + restart services + health check' },
        { cmd: 'repo create <proj>', desc: 'Create private GitHub repo + configure SSH remote' },
        { cmd: 'repo fix <proj>', desc: 'Audit and fix git issues' },
        { cmd: 'repo init <proj>', desc: 'git init + .gitignore + first commit' },
      ],
    },
    {
      title: 'Session Commands',
      entries: [
        { cmd: 'work <project>', desc: 'Full session: prepare + overview + begin' },
        { cmd: 'work <project> --oi', desc: 'Launch with Open Interpreter instead of Claude' },
        { cmd: 'prepare <project>', desc: 'Wake hosts, warm Ollama, start services' },
        { cmd: 'begin <project>', desc: 'Build context preamble, launch editor' },
        { cmd: 'begin <project> --dry-run', desc: 'Print preamble without launching' },
        { cmd: 'autosummary', desc: 'Start post-session polling daemon' },
      ],
    },
    {
      title: 'Other Tools',
      entries: [
        { cmd: 'overview', desc: 'AI overview of all projects' },
        { cmd: 'overview <project>', desc: 'Deep view of a single project' },
        { cmd: 'research', desc: 'Research digest (arxiv + GitHub releases)' },
        { cmd: 'research --fetch', desc: 'Fetch and score new items (cron mode)' },
        { cmd: 'backup', desc: 'Rsync hub ecosystem to backup target' },
        { cmd: 'code search <proj> "query"', desc: 'Semantic code search' },
        { cmd: 'code ask <proj> "question"', desc: 'RAG-powered architecture question' },
        { cmd: 'edit <host>:<file> --show', desc: 'View remote file with line numbers' },
        { cmd: 'notify', desc: 'View unread notifications' },
        { cmd: 'notify --all', desc: 'View all notifications (7 days)' },
        { cmd: 'health-probe', desc: 'Probe hosts and services' },
        { cmd: 'hubgrep <pattern>', desc: 'Search across all hub files' },
        { cmd: 'search "query"', desc: 'DuckDuckGo web search' },
      ],
    },
    {
      title: 'Magic Commands (OI / WebUI Chat)',
      entries: [
        { cmd: '%status', desc: 'Hub status dashboard' },
        { cmd: '%next', desc: 'AI-suggested next tasks' },
        { cmd: '%projects', desc: 'List registered projects' },
        { cmd: '%switch <name>', desc: 'Switch active project (fuzzy match)' },
        { cmd: '%repo', desc: 'Git dashboard' },
        { cmd: '%checkpoint', desc: 'Batch commit+push all dirty projects' },
        { cmd: '%backup', desc: 'Run backup' },
        { cmd: '%wake', desc: 'Wake workstation via WoL' },
        { cmd: '%research', desc: 'Research digest' },
        { cmd: '%health', desc: 'Health probe results' },
        { cmd: '%services', desc: 'Service status' },
        { cmd: '%overview', desc: 'AI project overview' },
        { cmd: '%notify', desc: 'Unread notifications' },
        { cmd: '%image', desc: 'Send clipboard image to LLM' },
        { cmd: '%image <path>', desc: 'Send image file to LLM' },
        { cmd: '%auto-edit', desc: 'Auto-apply edit blocks' },
        { cmd: '%confirm-edit', desc: 'Require confirmation for edits' },
        { cmd: '%allow <pattern>', desc: 'Auto-run matching commands' },
        { cmd: '%deny <pattern>', desc: 'Remove auto-run pattern' },
        { cmd: '%permissions', desc: 'Show auto-run rules' },
        { cmd: '%reset', desc: 'Clear conversation' },
        { cmd: '%undo', desc: 'Undo last exchange' },
        { cmd: '%help', desc: 'List available commands' },
        { cmd: '%map', desc: 'Show project file tree' },
      ],
    },
  ],

  // ── Rendering ──────────────────────────────────────────────────────────────
  activeSection: null,

  render() {
    const container = document.getElementById('help-content');
    if (!container) return;

    let html = '<div class="help-nav">';
    for (const doc of this.docs) {
      html += `<button class="help-nav-btn" data-section="${doc.id}">${this.escapeHtml(doc.title)}</button>`;
    }
    html += `<button class="help-nav-btn" data-section="quick-ref">Quick Reference</button>`;
    html += '</div>';

    html += '<div id="help-body"></div>';
    container.innerHTML = html;

    // Wire up nav buttons
    container.querySelectorAll('.help-nav-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        this.showSection(btn.dataset.section);
        container.querySelectorAll('.help-nav-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      });
    });

    // Show first section
    this.showSection(this.docs[0].id);
    container.querySelector('.help-nav-btn').classList.add('active');
  },

  showSection(id) {
    this.activeSection = id;
    const body = document.getElementById('help-body');
    if (!body) return;

    if (id === 'quick-ref') {
      this.renderQuickRef(body);
      return;
    }

    const doc = this.docs.find(d => d.id === id);
    if (!doc) return;

    body.innerHTML = `
      <div class="help-doc">
        <h2 class="help-doc-title">${this.escapeHtml(doc.title)}</h2>
        <div class="help-doc-body">${doc.body}</div>
      </div>
    `;
  },

  renderQuickRef(body) {
    let html = `
      <div class="help-doc">
        <h2 class="help-doc-title">Quick Reference</h2>
        <div class="help-search-box">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input type="text" class="help-search-input" id="help-search"
                 placeholder="Search commands..." oninput="Help.filter(this.value)">
        </div>
        <div id="help-sections">
    `;

    for (const section of this.quickRef) {
      html += `
        <div class="help-section" data-section="${section.title}">
          <div class="help-section-title">${this.escapeHtml(section.title)}</div>
          <div class="help-entries">
      `;
      for (const entry of section.entries) {
        html += `
            <div class="help-entry" data-search="${this.escapeHtml((entry.cmd + ' ' + entry.desc).toLowerCase())}">
              <code class="help-cmd">${this.escapeHtml(entry.cmd)}</code>
              <span class="help-desc">${this.escapeHtml(entry.desc)}</span>
            </div>
        `;
      }
      html += '</div></div>';
    }

    html += '</div></div>';
    body.innerHTML = html;
  },

  filter(query) {
    const q = query.toLowerCase().trim();
    const sections = document.querySelectorAll('.help-section');

    for (const section of sections) {
      const entries = section.querySelectorAll('.help-entry');
      let visibleCount = 0;

      for (const entry of entries) {
        const text = entry.getAttribute('data-search') || '';
        const match = !q || text.includes(q);
        entry.style.display = match ? '' : 'none';
        if (match) visibleCount++;
      }

      section.style.display = visibleCount > 0 ? '' : 'none';
    }
  },

  escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },
};

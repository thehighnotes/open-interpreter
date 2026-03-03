/* OI WebUI — Settings portal: full hub configuration interface */

const Settings = {
  _hubConfig: null,
  _oiConfig: null,
  _sessionData: null,
  _ragData: null,
  _ragPage: 0,
  _ragPageSize: 20,
  _ragSearch: '',
  _ragCategory: '',

  // ── Main entry point ─────────────────────────────────────────────────────
  async load() {
    const container = document.getElementById('settings-content');
    container.innerHTML = '<div class="tab-loading"><div class="spinner"></div>Loading settings...</div>';
    try {
      const [hubRes, settingsRes, oiRes] = await Promise.all([
        fetch('/api/settings/hub'),
        fetch('/api/settings'),
        fetch('/api/settings/oi'),
      ]);
      this._hubConfig = await hubRes.json();
      this._sessionData = await settingsRes.json();
      this._oiConfig = await oiRes.json();
      this._render();
    } catch (e) {
      container.innerHTML = `<div class="text-muted">Failed to load settings: ${e.message}</div>`;
    }
  },

  _render() {
    const container = document.getElementById('settings-content');
    const config = this._hubConfig;
    let html = '';
    html += this.renderHubSection(config);
    html += this.renderGitSection(config);
    html += this.renderBackupSection(config);
    html += this.renderHostsSection(config);
    html += this.renderLLMSection(config);
    html += this.renderRagSection();
    // Conditional: Code Assistant
    const hasCA = Object.values(config.hosts || {}).some(h => (h.roles || []).includes('code_assistant'));
    if (hasCA) html += this.renderCodeAssistantSection(config);
    html += this.renderResearchSection(config);
    html += this.renderWebuiSection();
    html += this.renderSessionSection();
    container.innerHTML = html;
    // Load RAG data async
    this._loadRagData();
  },

  _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },

  // ── Generic save ─────────────────────────────────────────────────────────
  async saveSection(section, data, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
    try {
      const res = await fetch('/api/settings/hub/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ section, data }),
      });
      const result = await res.json();
      if (result.ok) {
        App.toast(`Saved ${section} settings`, 'success');
        // Refresh config
        const r = await fetch('/api/settings/hub');
        this._hubConfig = await r.json();
      } else {
        App.toast(result.error || 'Save failed', 'error');
      }
    } catch (e) {
      App.toast('Save failed: ' + e.message, 'error');
    }
    if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  },

  // ── Hub Name Section ─────────────────────────────────────────────────────
  renderHubSection(config) {
    const name = config.hub?.name || 'Dev Hub';
    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">Hub Identity</div>
        </div>
        <div class="settings-group">
          <div class="settings-label">Hub Name</div>
          <div class="settings-row">
            <input type="text" class="input" id="s-hub-name" value="${this._esc(name)}" style="max-width:300px;">
            <button class="btn btn-sm" onclick="Settings.saveHub()">Save</button>
          </div>
          <div class="text-xs text-muted">Displayed in sidebar, status, and tools.</div>
        </div>
      </div>`;
  },

  async saveHub() {
    const name = document.getElementById('s-hub-name').value.trim();
    if (!name) { App.toast('Hub name cannot be empty', 'error'); return; }
    await this.saveSection('hub', { name, local_host: this._hubConfig.hub?.local_host || 'local' });
    // Update sidebar
    const el = document.getElementById('hub-name');
    if (el) el.textContent = name;
  },

  // ── Git Section ──────────────────────────────────────────────────────────
  renderGitSection(config) {
    const git = config.git || {};
    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">Git</div>
        </div>
        <div class="settings-group">
          <div class="settings-label">GitHub Username</div>
          <input type="text" class="input" id="s-git-user" value="${this._esc(git.github_username || '')}" style="max-width:300px;">
        </div>
        <div class="settings-group">
          <div class="settings-label">Git Email</div>
          <input type="text" class="input" id="s-git-email" value="${this._esc(git.email || '')}" style="max-width:300px;">
        </div>
        <div class="settings-group">
          <button class="btn btn-sm" onclick="Settings.saveGit(this)">Save</button>
        </div>
      </div>`;
  },

  async saveGit(btn) {
    const username = document.getElementById('s-git-user').value.trim();
    const email = document.getElementById('s-git-email').value.trim();
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      App.toast('Invalid email format', 'error'); return;
    }
    await this.saveSection('git', { github_username: username, email }, btn);
  },

  // ── Backup Section ───────────────────────────────────────────────────────
  renderBackupSection(config) {
    const dest = config.backup?.destination || '';
    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">Backup</div>
        </div>
        <div class="settings-group">
          <div class="settings-label">Backup Destination</div>
          <div class="settings-row">
            <input type="text" class="input" id="s-backup-dest" value="${this._esc(dest)}" style="max-width:400px;" placeholder="host:~/path or ~/local-path">
            <button class="btn btn-sm" onclick="Settings.probeBackup()">Test</button>
            <span id="s-backup-probe" class="probe-result"></span>
          </div>
        </div>
        <div class="settings-group">
          <button class="btn btn-sm" onclick="Settings.saveBackup(this)">Save</button>
        </div>
      </div>`;
  },

  async probeBackup() {
    const dest = document.getElementById('s-backup-dest').value.trim();
    const badge = document.getElementById('s-backup-probe');
    badge.innerHTML = '<span class="spinner-sm"></span>';
    try {
      const res = await fetch('/api/settings/backup/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ destination: dest }),
      });
      const data = await res.json();
      if (data.reachable) {
        badge.innerHTML = '<span class="badge badge-success">Reachable</span>';
      } else {
        badge.innerHTML = '<span class="badge badge-error">Unreachable</span>';
      }
    } catch (e) {
      badge.innerHTML = '<span class="badge badge-error">Error</span>';
    }
  },

  async saveBackup(btn) {
    const dest = document.getElementById('s-backup-dest').value.trim();
    if (!dest) { App.toast('Backup destination cannot be empty', 'error'); return; }
    await this.saveSection('backup', { destination: dest }, btn);
  },

  // ── Hosts Section ────────────────────────────────────────────────────────
  renderHostsSection(config) {
    const hosts = config.hosts || {};
    const allRoles = ['local', 'ollama', 'code_assistant', 'backup_target', 'wakeable'];

    let cards = '';
    for (const [alias, host] of Object.entries(hosts)) {
      const roles = host.roles || [];
      const isWakeable = roles.includes('wakeable');

      let roleChips = '';
      for (const role of allRoles) {
        const active = roles.includes(role);
        roleChips += `<span class="tag-chip ${active ? 'active' : ''}" data-role="${role}" data-host="${alias}" onclick="Settings.toggleRole(this)">${role}</span>`;
      }

      cards += `
        <div class="data-card host-card" data-alias="${this._esc(alias)}">
          <div class="settings-row" style="margin-bottom:var(--space-sm);justify-content:space-between;">
            <strong style="font-family:var(--font-mono);color:var(--accent-primary);">${this._esc(alias)}</strong>
            <div style="display:flex;gap:var(--space-xs);">
              <button class="btn btn-sm" onclick="Settings.probeHost('${this._esc(alias)}', this)" title="Test SSH">Test SSH</button>
              <button class="btn btn-sm btn-danger" onclick="Settings.removeHost('${this._esc(alias)}')" title="Delete">Delete</button>
            </div>
          </div>
          <div class="settings-group">
            <div class="settings-label">Display Name</div>
            <input type="text" class="input host-name" value="${this._esc(host.name || alias)}" style="max-width:200px;" data-field="name">
          </div>
          <div class="settings-group">
            <div class="settings-label">IP Address</div>
            <input type="text" class="input host-ip" value="${this._esc(host.ip || '127.0.0.1')}" style="max-width:200px;" data-field="ip">
          </div>
          <div class="settings-group">
            <div class="settings-label">User</div>
            <input type="text" class="input host-user" value="${this._esc(host.user || 'user')}" style="max-width:200px;" data-field="user">
          </div>
          <div class="settings-group">
            <div class="settings-label">Roles</div>
            <div class="role-chips">${roleChips}</div>
          </div>
          <div class="settings-group host-wol-group" style="display:${isWakeable ? 'block' : 'none'};">
            <div class="settings-label">WoL MAC Address</div>
            <input type="text" class="input host-wol" value="${this._esc(host.wol_mac || '')}" style="max-width:240px;" placeholder="00:11:22:33:44:55" data-field="wol_mac">
          </div>
          <div class="probe-result host-probe" id="probe-${this._esc(alias)}"></div>
        </div>`;
    }

    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">Hosts</div>
          <button class="btn btn-sm" onclick="Settings.addHost()">+ Add Host</button>
        </div>
        <div id="hosts-list">${cards}</div>
        <div class="settings-group" style="margin-top:var(--space-md);">
          <button class="btn btn-sm" onclick="Settings.saveHosts(this)">Save All Hosts</button>
        </div>
      </div>`;
  },

  toggleRole(chip) {
    chip.classList.toggle('active');
    const alias = chip.dataset.host;
    const role = chip.dataset.role;
    // Show/hide WoL field
    if (role === 'wakeable') {
      const card = chip.closest('.host-card');
      const wolGroup = card.querySelector('.host-wol-group');
      if (wolGroup) wolGroup.style.display = chip.classList.contains('active') ? 'block' : 'none';
    }
  },

  async probeHost(alias, btn) {
    const badge = document.getElementById(`probe-${alias}`);
    if (btn) btn.disabled = true;
    badge.innerHTML = '<span class="spinner-sm"></span> Testing...';
    try {
      const res = await fetch('/api/settings/hosts/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ alias }),
      });
      const data = await res.json();
      if (data.reachable) {
        badge.innerHTML = '<span class="badge badge-success">Connected</span>';
      } else {
        badge.innerHTML = `
          <span class="badge badge-error">Unreachable</span>
          ${data.guide ? `<details class="guide-block"><summary>SSH Setup Guide</summary><pre>${this._esc(data.guide)}</pre></details>` : ''}`;
      }
    } catch (e) {
      badge.innerHTML = '<span class="badge badge-error">Error</span>';
    }
    if (btn) btn.disabled = false;
  },

  addHost() {
    const alias = prompt('Enter alias for new host (e.g., "server").\nOnly lowercase letters, numbers, hyphens, underscores.');
    if (!alias || !alias.trim()) return;
    const key = alias.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '');
    if (!key) { App.toast('Invalid alias — use only a-z, 0-9, -, _', 'error'); return; }
    // Add to current config
    if (!this._hubConfig.hosts) this._hubConfig.hosts = {};
    if (this._hubConfig.hosts[key]) {
      App.toast(`Host "${key}" already exists`, 'error');
      return;
    }
    this._hubConfig.hosts[key] = {
      name: key.charAt(0).toUpperCase() + key.slice(1),
      ip: '192.168.1.100',
      user: 'user',
      roles: [],
    };
    this._render();
    App.toast(`Added host "${key}" — configure and save`, 'success');
  },

  removeHost(alias) {
    if (!confirm(`Delete host "${alias}"? This cannot be undone.`)) return;
    delete this._hubConfig.hosts[alias];
    this._render();
    App.toast(`Removed host "${alias}" — save to persist`, 'success');
  },

  _collectHosts() {
    const hosts = {};
    document.querySelectorAll('.host-card').forEach(card => {
      const alias = card.dataset.alias;
      const name = card.querySelector('.host-name').value.trim();
      const ip = card.querySelector('.host-ip').value.trim();
      const user = card.querySelector('.host-user').value.trim();
      const roles = [];
      card.querySelectorAll('.tag-chip.active').forEach(c => roles.push(c.dataset.role));
      const entry = { name: name || alias, ip: ip || '127.0.0.1', user: user || 'user', roles };
      if (roles.includes('wakeable')) {
        const wol = card.querySelector('.host-wol');
        if (wol && wol.value.trim()) entry.wol_mac = wol.value.trim();
      }
      hosts[alias] = entry;
    });
    return hosts;
  },

  async saveHosts(btn) {
    const hosts = this._collectHosts();
    // Validate: at least one host, each with a valid IP
    const keys = Object.keys(hosts);
    if (!keys.length) { App.toast('Must have at least one host', 'error'); return; }
    for (const [alias, h] of Object.entries(hosts)) {
      if (!h.ip || !/^\d{1,3}(\.\d{1,3}){3}$/.test(h.ip)) {
        App.toast(`Host "${alias}": invalid IP address`, 'error'); return;
      }
      if (!h.user) { App.toast(`Host "${alias}": user cannot be empty`, 'error'); return; }
    }
    if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
    try {
      const res = await fetch('/api/settings/hosts/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hosts }),
      });
      const result = await res.json();
      if (result.ok) {
        App.toast('Hosts saved', 'success');
        const r = await fetch('/api/settings/hub');
        this._hubConfig = await r.json();
      } else {
        App.toast(result.error || 'Save failed', 'error');
      }
    } catch (e) {
      App.toast('Save failed: ' + e.message, 'error');
    }
    if (btn) { btn.disabled = false; btn.textContent = 'Save All Hosts'; }
  },

  // ── LLM Section ──────────────────────────────────────────────────────────
  renderLLMSection(config) {
    const ollama = config.ollama || {};
    const ollamaHosts = Object.entries(config.hosts || {}).filter(([_, h]) => (h.roles || []).includes('ollama'));

    let hostOptions = '';
    for (const [key, h] of ollamaHosts) {
      const sel = key === ollama.host ? 'selected' : '';
      hostOptions += `<option value="${this._esc(key)}" ${sel}>${this._esc(h.name || key)} (${this._esc(h.ip || '?')})</option>`;
    }
    // If no ollama host found, show text input fallback
    if (!ollamaHosts.length) {
      hostOptions = `<option value="${this._esc(ollama.host || 'local')}">${this._esc(ollama.host || 'local')}</option>`;
    }

    const sessionModel = this._sessionData?.model || ollama.default_model || '';

    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">LLM / Ollama</div>
          <span class="probe-result" id="s-ollama-probe"></span>
        </div>

        <div class="settings-group">
          <div class="settings-label">Ollama Host</div>
          <div class="settings-row">
            <select class="input" id="s-ollama-host" style="max-width:300px;">${hostOptions}</select>
          </div>
        </div>
        <div class="settings-group">
          <div class="settings-label">Port</div>
          <input type="number" class="input" id="s-ollama-port" value="${ollama.port || 11434}" style="max-width:120px;">
        </div>
        <div class="settings-group">
          <div class="settings-label">Default Model</div>
          <div class="settings-row">
            <select class="input" id="s-ollama-model" style="max-width:300px;">
              <option value="${this._esc(ollama.default_model || '')}">${this._esc(ollama.default_model || 'loading...')}</option>
            </select>
            <button class="btn btn-sm" onclick="Settings.refreshModels()">Refresh</button>
          </div>
          <div class="text-xs text-muted">This sets the hub-wide default. Session model can differ.</div>
        </div>
        <div class="settings-group">
          <div class="settings-label">Context Window</div>
          <input type="number" class="input" id="s-llm-ctx" value="${this._sessionData?.context_window || 16000}" style="max-width:160px;" min="1000" max="128000" step="1000">
        </div>
        <div class="settings-group">
          <div class="settings-label">Max Tokens (per response)</div>
          <input type="number" class="input" id="s-llm-maxtok" value="${this._sessionData?.max_tokens || 1200}" style="max-width:160px;" min="100" max="8000" step="100">
        </div>
        <div class="settings-group">
          <div class="settings-row">
            <button class="btn btn-sm" onclick="Settings.saveLLM(this)">Save</button>
            <button class="btn btn-sm" onclick="Settings.probeOllama()">Test Connection</button>
          </div>
        </div>
      </div>`;
  },

  async refreshModels() {
    const sel = document.getElementById('s-ollama-model');
    const current = sel.value;
    sel.innerHTML = '<option>Loading...</option>';
    try {
      const res = await fetch('/api/settings/ollama/models');
      const data = await res.json();
      if (data.models && data.models.length) {
        sel.innerHTML = data.models.map(m =>
          `<option value="${this._esc(m)}" ${m === current ? 'selected' : ''}>${this._esc(m)}</option>`
        ).join('');
      } else {
        sel.innerHTML = `<option value="${this._esc(current)}">${this._esc(current)} (fetch failed)</option>`;
      }
    } catch (e) {
      sel.innerHTML = `<option value="${this._esc(current)}">${this._esc(current)}</option>`;
    }
  },

  async probeOllama() {
    const badge = document.getElementById('s-ollama-probe');
    badge.innerHTML = '<span class="spinner-sm"></span>';
    try {
      const res = await fetch('/api/settings/ollama/probe', { method: 'POST' });
      const data = await res.json();
      if (data.reachable) {
        badge.innerHTML = `<span class="badge badge-success">Connected (${data.model_count} models)</span>`;
      } else {
        badge.innerHTML = `<span class="badge badge-error">Unreachable</span>`;
      }
    } catch (e) {
      badge.innerHTML = '<span class="badge badge-error">Error</span>';
    }
  },

  async saveLLM(btn) {
    const host = document.getElementById('s-ollama-host').value;
    const port = parseInt(document.getElementById('s-ollama-port').value) || 11434;
    const model = document.getElementById('s-ollama-model').value;
    const ctx = parseInt(document.getElementById('s-llm-ctx').value) || 16000;
    const maxTok = parseInt(document.getElementById('s-llm-maxtok').value) || 1200;

    if (!model) { App.toast('Model name cannot be empty', 'error'); return; }
    if (port < 1 || port > 65535) { App.toast('Port must be 1-65535', 'error'); return; }
    if (ctx < 1000 || ctx > 128000) { App.toast('Context window must be 1000-128000', 'error'); return; }
    if (maxTok < 100 || maxTok > 8000) { App.toast('Max tokens must be 100-8000', 'error'); return; }

    if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }

    // Save to hub config
    await this.saveSection('ollama', { host, port, default_model: model }, null);

    // Save runtime OI settings
    try {
      await fetch('/api/settings/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, context_window: ctx, max_tokens: maxTok }),
      });
    } catch (e) { /* best effort */ }

    if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  },

  // ── RAG Section ──────────────────────────────────────────────────────────
  renderRagSection() {
    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">Mini-RAG Entries</div>
          <span class="badge badge-info" id="s-rag-count">...</span>
        </div>
        <div class="settings-row" style="margin-bottom:var(--space-md);flex-wrap:wrap;">
          <input type="text" class="input" id="s-rag-search" placeholder="Search entries..." style="max-width:250px;" oninput="Settings.filterRag()">
          <select class="input" id="s-rag-cat-filter" style="max-width:180px;" onchange="Settings.filterRag()">
            <option value="">All categories</option>
          </select>
          <button class="btn btn-sm" onclick="Settings.addRagEntry()">+ Add Entry</button>
        </div>
        <div id="s-rag-list"></div>
        <div id="s-rag-pagination" class="settings-row" style="justify-content:space-between;margin-top:var(--space-sm);"></div>
      </div>`;
  },

  async _loadRagData() {
    try {
      const res = await fetch('/api/settings/rag');
      this._ragData = await res.json();
      this._ragPage = 0;
      this._ragSearch = '';
      this._ragCategory = '';
      // Populate category filter
      const catSel = document.getElementById('s-rag-cat-filter');
      if (catSel && this._ragData.categories) {
        let opts = '<option value="">All categories</option>';
        for (const c of this._ragData.categories) {
          opts += `<option value="${this._esc(c)}">${this._esc(c)}</option>`;
        }
        catSel.innerHTML = opts;
      }
      document.getElementById('s-rag-count').textContent = this._ragData.count || 0;
      this._renderRagList();
    } catch (e) {
      document.getElementById('s-rag-list').innerHTML = '<div class="text-muted">Failed to load RAG entries</div>';
    }
    // Auto-load model list
    this.refreshModels();
  },

  filterRag() {
    this._ragSearch = (document.getElementById('s-rag-search')?.value || '').toLowerCase();
    this._ragCategory = document.getElementById('s-rag-cat-filter')?.value || '';
    this._ragPage = 0;
    this._renderRagList();
  },

  _getFilteredRag() {
    if (!this._ragData?.entries) return [];
    return this._ragData.entries.filter((e, i) => {
      e._origIndex = i; // track original index for API calls
      if (this._ragCategory && e.category !== this._ragCategory) return false;
      if (this._ragSearch) {
        const hay = `${e.topic || ''} ${e.description || ''} ${e.content || ''}`.toLowerCase();
        if (!hay.includes(this._ragSearch)) return false;
      }
      return true;
    });
  },

  _renderRagList() {
    const listEl = document.getElementById('s-rag-list');
    const pagEl = document.getElementById('s-rag-pagination');
    if (!listEl) return;

    const filtered = this._getFilteredRag();
    const total = filtered.length;
    const start = this._ragPage * this._ragPageSize;
    const end = Math.min(start + this._ragPageSize, total);
    const page = filtered.slice(start, end);

    if (!page.length) {
      listEl.innerHTML = '<div class="text-muted">No entries found</div>';
      pagEl.innerHTML = '';
      return;
    }

    let html = '';
    for (const entry of page) {
      const desc = (entry.description || '').length > 120
        ? entry.description.substring(0, 120) + '...'
        : entry.description || '';
      html += `
        <div class="data-card rag-card" data-index="${entry._origIndex}">
          <div class="settings-row" style="justify-content:space-between;margin-bottom:var(--space-xs);">
            <strong>${this._esc(entry.topic || 'Untitled')}</strong>
            <div style="display:flex;gap:var(--space-xs);align-items:center;">
              ${entry.category ? `<span class="badge badge-info">${this._esc(entry.category)}</span>` : ''}
              <button class="btn btn-sm" onclick="Settings.editRagEntry(${entry._origIndex})">Edit</button>
              <button class="btn btn-sm btn-danger" onclick="Settings.deleteRagEntry(${entry._origIndex})">Delete</button>
            </div>
          </div>
          <div class="text-xs text-muted">${this._esc(desc)}</div>
          <div id="rag-edit-${entry._origIndex}"></div>
        </div>`;
    }
    listEl.innerHTML = html;

    // Pagination
    const totalPages = Math.ceil(total / this._ragPageSize);
    if (totalPages > 1) {
      pagEl.innerHTML = `
        <button class="btn btn-sm" onclick="Settings.ragPrev()" ${this._ragPage === 0 ? 'disabled' : ''}>Prev</button>
        <span class="text-xs text-muted">Showing ${start + 1}-${end} of ${total}</span>
        <button class="btn btn-sm" onclick="Settings.ragNext()" ${this._ragPage >= totalPages - 1 ? 'disabled' : ''}>Next</button>`;
    } else {
      pagEl.innerHTML = total ? `<span class="text-xs text-muted">${total} entries</span>` : '';
    }
  },

  ragPrev() { if (this._ragPage > 0) { this._ragPage--; this._renderRagList(); } },
  ragNext() {
    const total = this._getFilteredRag().length;
    if ((this._ragPage + 1) * this._ragPageSize < total) { this._ragPage++; this._renderRagList(); }
  },

  editRagEntry(index) {
    const el = document.getElementById(`rag-edit-${index}`);
    if (!el) return;
    // Toggle
    if (el.innerHTML) { el.innerHTML = ''; return; }
    const entry = this._ragData.entries[index] || {};
    el.innerHTML = `
      <div style="margin-top:var(--space-sm);padding:var(--space-sm);border:1px solid var(--border-secondary);border-radius:var(--radius-md);background:var(--bg-primary);">
        <div class="settings-group">
          <div class="settings-label">Topic</div>
          <input type="text" class="input rag-topic" value="${this._esc(entry.topic || '')}" style="width:100%;">
        </div>
        <div class="settings-group">
          <div class="settings-label">Description</div>
          <textarea class="input rag-desc" rows="2" style="width:100%;resize:vertical;">${this._esc(entry.description || '')}</textarea>
        </div>
        <div class="settings-group">
          <div class="settings-label">Content</div>
          <textarea class="input rag-content" rows="3" style="width:100%;resize:vertical;">${this._esc(entry.content || '')}</textarea>
        </div>
        <div class="settings-row" style="gap:var(--space-sm);">
          <div class="settings-group" style="flex:1;">
            <div class="settings-label">Source</div>
            <input type="text" class="input rag-source" value="${this._esc(entry.source || 'hub')}" style="width:100%;">
          </div>
          <div class="settings-group" style="flex:1;">
            <div class="settings-label">Category</div>
            <input type="text" class="input rag-category" value="${this._esc(entry.category || '')}" style="width:100%;" list="rag-categories">
            <datalist id="rag-categories">${(this._ragData.categories || []).map(c => `<option value="${this._esc(c)}">`).join('')}</datalist>
          </div>
        </div>
        <div class="settings-row" style="margin-top:var(--space-sm);">
          <button class="btn btn-sm" onclick="Settings.saveRagEntry(${index}, this)">Save</button>
          <button class="btn btn-sm" onclick="document.getElementById('rag-edit-${index}').innerHTML=''">Cancel</button>
        </div>
      </div>`;
  },

  async saveRagEntry(index, btn) {
    const el = document.getElementById(`rag-edit-${index}`);
    if (!el) return;
    const entry = {
      topic: el.querySelector('.rag-topic').value.trim(),
      description: el.querySelector('.rag-desc').value.trim(),
      content: el.querySelector('.rag-content').value.trim(),
      source: el.querySelector('.rag-source').value.trim() || 'hub',
      category: el.querySelector('.rag-category').value.trim(),
    };
    if (!entry.topic) { App.toast('Topic is required', 'error'); return; }
    if (btn) btn.disabled = true;
    try {
      const res = await fetch('/api/settings/rag/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index, entry }),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast('RAG entry updated', 'success');
        await this._loadRagData();
      } else {
        App.toast(data.error || 'Update failed', 'error');
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
    if (btn) btn.disabled = false;
  },

  async addRagEntry() {
    const entry = {
      topic: '',
      description: '',
      content: '',
      source: 'hub',
      category: '',
    };
    // Pre-prompt for topic
    const topic = prompt('Topic for new RAG entry:');
    if (!topic || !topic.trim()) return;
    entry.topic = topic.trim();

    try {
      const res = await fetch('/api/settings/rag/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry }),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast('RAG entry added — edit to fill in details', 'success');
        await this._loadRagData();
        // Auto-open editor for the new entry
        const newIndex = (this._ragData.entries?.length || 1) - 1;
        this._ragPage = Math.floor(newIndex / this._ragPageSize);
        this._renderRagList();
        setTimeout(() => this.editRagEntry(newIndex), 100);
      } else {
        App.toast(data.error || 'Add failed', 'error');
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  async deleteRagEntry(index) {
    const entry = this._ragData.entries[index];
    if (!confirm(`Delete RAG entry "${entry?.topic || 'Untitled'}"?`)) return;
    try {
      const res = await fetch('/api/settings/rag/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index }),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast('RAG entry deleted', 'success');
        await this._loadRagData();
      } else {
        App.toast(data.error || 'Delete failed', 'error');
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  // ── Code Assistant Section (conditional) ─────────────────────────────────
  renderCodeAssistantSection(config) {
    const ca = config.code_assistant || {};
    const caHosts = Object.entries(config.hosts || {}).filter(([_, h]) => (h.roles || []).includes('code_assistant'));

    let hostOptions = '';
    for (const [key, h] of caHosts) {
      const sel = key === ca.host ? 'selected' : '';
      hostOptions += `<option value="${this._esc(key)}" ${sel}>${this._esc(h.name || key)}</option>`;
    }

    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">Code Assistant</div>
          <span class="probe-result" id="s-ca-probe"></span>
        </div>
        <div class="settings-group">
          <div class="settings-label">Host</div>
          <select class="input" id="s-ca-host" style="max-width:200px;">${hostOptions}</select>
        </div>
        <div class="settings-group">
          <div class="settings-label">Port</div>
          <input type="number" class="input" id="s-ca-port" value="${ca.port || 5002}" style="max-width:120px;">
        </div>
        <div class="settings-group">
          <div class="settings-row">
            <button class="btn btn-sm" onclick="Settings.saveCA(this)">Save</button>
            <button class="btn btn-sm" onclick="Settings.probeCA()">Test Connection</button>
          </div>
        </div>
      </div>`;
  },

  async probeCA() {
    const badge = document.getElementById('s-ca-probe');
    badge.innerHTML = '<span class="spinner-sm"></span>';
    try {
      const res = await fetch('/api/settings/ca/probe', { method: 'POST' });
      const data = await res.json();
      badge.innerHTML = data.healthy
        ? '<span class="badge badge-success">Healthy</span>'
        : '<span class="badge badge-error">Unreachable</span>';
    } catch (e) {
      badge.innerHTML = '<span class="badge badge-error">Error</span>';
    }
  },

  async saveCA(btn) {
    const host = document.getElementById('s-ca-host').value;
    const port = parseInt(document.getElementById('s-ca-port').value) || 5002;
    if (port < 1 || port > 65535) { App.toast('Port must be 1-65535', 'error'); return; }
    await this.saveSection('code_assistant', { host, port }, btn);
  },

  // ── Research Section ─────────────────────────────────────────────────────
  renderResearchSection(config) {
    const r = config.research || {};
    const threshold = r.threshold ?? 7;
    const cats = r.arxiv_categories || [];
    const keywords = r.arxiv_keywords || [];
    const repos = r.github_repos || [];
    const maxResults = r.arxiv_max_results ?? 30;

    const catChips = cats.map((c, i) =>
      `<span class="tag-chip active" data-value="${this._esc(c)}">${this._esc(c)}<span class="tag-remove" onclick="this.parentElement.remove()">x</span></span>`
    ).join('');

    const kwChips = keywords.map((k, i) =>
      `<span class="tag-chip active" data-value="${this._esc(k)}">${this._esc(k)}<span class="tag-remove" onclick="this.parentElement.remove()">x</span></span>`
    ).join('');

    const repoList = repos.map((r, i) =>
      `<div class="settings-row" style="margin-bottom:var(--space-xs);">
        <input type="text" class="input research-repo" value="${this._esc(r)}" style="max-width:300px;">
        <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">x</button>
      </div>`
    ).join('');

    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">Research</div>
        </div>
        <div class="settings-group">
          <div class="settings-label">Relevance Threshold (1-10)</div>
          <input type="number" class="input" id="s-research-threshold" value="${threshold}" style="max-width:100px;" min="1" max="10">
        </div>
        <div class="settings-group">
          <div class="settings-label">Max arXiv Results</div>
          <input type="number" class="input" id="s-research-maxresults" value="${maxResults}" style="max-width:100px;" min="5" max="100">
        </div>
        <div class="settings-group">
          <div class="settings-label">arXiv Categories</div>
          <div class="tag-chips-container" id="s-research-cats">${catChips}</div>
          <div class="settings-row" style="margin-top:var(--space-xs);">
            <input type="text" class="input" id="s-research-cat-input" placeholder="e.g. cs.CV" style="max-width:150px;">
            <button class="btn btn-sm" onclick="Settings.addResearchTag('s-research-cats', 's-research-cat-input')">Add</button>
          </div>
        </div>
        <div class="settings-group">
          <div class="settings-label">arXiv Keywords</div>
          <div class="tag-chips-container" id="s-research-kw">${kwChips}</div>
          <div class="settings-row" style="margin-top:var(--space-xs);">
            <input type="text" class="input" id="s-research-kw-input" placeholder="e.g. diffusion" style="max-width:200px;">
            <button class="btn btn-sm" onclick="Settings.addResearchTag('s-research-kw', 's-research-kw-input')">Add</button>
          </div>
        </div>
        <div class="settings-group">
          <div class="settings-label">GitHub Repos</div>
          <div id="s-research-repos">${repoList}</div>
          <button class="btn btn-sm" onclick="Settings.addResearchRepo()" style="margin-top:var(--space-xs);">+ Add Repo</button>
        </div>
        <div class="settings-group" style="margin-top:var(--space-md);">
          <button class="btn btn-sm" onclick="Settings.saveResearch(this)">Save</button>
        </div>
      </div>`;
  },

  addResearchTag(containerId, inputId) {
    const input = document.getElementById(inputId);
    const value = input?.value.trim();
    if (!value) return;
    const container = document.getElementById(containerId);
    const chip = document.createElement('span');
    chip.className = 'tag-chip active';
    chip.dataset.value = value;
    chip.innerHTML = `${this._esc(value)}<span class="tag-remove" onclick="this.parentElement.remove()">x</span>`;
    container.appendChild(chip);
    input.value = '';
  },

  addResearchRepo() {
    const container = document.getElementById('s-research-repos');
    const row = document.createElement('div');
    row.className = 'settings-row';
    row.style.marginBottom = 'var(--space-xs)';
    row.innerHTML = `
      <input type="text" class="input research-repo" value="" style="max-width:300px;" placeholder="owner/repo">
      <button class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">x</button>`;
    container.appendChild(row);
  },

  async saveResearch(btn) {
    const threshold = parseInt(document.getElementById('s-research-threshold').value) || 7;
    const maxResults = parseInt(document.getElementById('s-research-maxresults').value) || 30;

    if (threshold < 1 || threshold > 10) { App.toast('Threshold must be 1-10', 'error'); return; }
    if (maxResults < 5 || maxResults > 100) { App.toast('Max results must be 5-100', 'error'); return; }

    const cats = [];
    document.querySelectorAll('#s-research-cats .tag-chip').forEach(c => {
      if (c.dataset.value) cats.push(c.dataset.value);
    });
    const kw = [];
    document.querySelectorAll('#s-research-kw .tag-chip').forEach(c => {
      if (c.dataset.value) kw.push(c.dataset.value);
    });
    const repos = [];
    document.querySelectorAll('.research-repo').forEach(input => {
      const v = input.value.trim();
      if (v) repos.push(v);
    });

    await this.saveSection('research', {
      threshold,
      arxiv_categories: cats,
      arxiv_keywords: kw,
      arxiv_max_results: maxResults,
      github_repos: repos,
    }, btn);
  },

  // ── WebUI Section ────────────────────────────────────────────────────────
  renderWebuiSection() {
    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">WebUI</div>
        </div>
        <div class="settings-group">
          <div class="settings-label">Interpreter Config</div>
          ${this._oiConfig && Object.keys(this._oiConfig).length > 0 ? `
          <div class="settings-row"><span class="settings-label" style="min-width:130px;">API Base</span><span class="settings-value">${this._esc(this._oiConfig.api_base || '')}</span></div>
          <div class="settings-row"><span class="settings-label" style="min-width:130px;">Vision</span><span class="badge ${this._oiConfig.supports_vision ? 'badge-success' : 'badge-warning'}">${this._oiConfig.supports_vision ? 'Enabled' : 'Disabled'}</span></div>
          <div class="settings-row"><span class="settings-label" style="min-width:130px;">Auto-run</span><span class="badge badge-info">${this._oiConfig.auto_run === 'callable' ? 'Smart' : this._oiConfig.auto_run ? 'All' : 'Off'}</span></div>
          <div class="settings-row"><span class="settings-label" style="min-width:130px;">Custom Instructions</span><span class="badge badge-info">${this._oiConfig.custom_instructions === 'callable' ? 'Dynamic (RAG)' : this._oiConfig.custom_instructions === 'set' ? 'Static' : 'None'}</span></div>
          ` : '<div class="text-muted">Interpreter not initialized yet</div>'}
        </div>
      </div>`;
  },

  // ── Session Section ──────────────────────────────────────────────────────
  renderSessionSection() {
    const msgCount = this._sessionData?.message_count || 0;
    return `
      <div class="settings-section">
        <div class="settings-section-header">
          <div class="settings-section-title">Session</div>
        </div>
        <div class="settings-group">
          <div class="settings-label">Messages in conversation</div>
          <div class="settings-row">
            <span class="settings-value">${msgCount}</span>
          </div>
        </div>
        <div class="settings-group">
          <button class="btn btn-danger btn-sm" onclick="Tabs.resetSession()">Reset Session</button>
        </div>
      </div>`;
  },
};

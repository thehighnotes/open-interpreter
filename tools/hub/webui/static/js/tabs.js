/* OI WebUI — Non-chat tabs: status, projects, repo, research, notify, settings */

const Tabs = {
  loaded: {},

  onTabActivated(tab) {
    if (tab === 'chat') return;
    if (!this.loaded[tab]) {
      this.loaded[tab] = true;
      this.loadTab(tab);
    }
  },

  async loadTab(tab) {
    const loaders = {
      status: () => this.loadStatus(),
      projects: () => this.loadProjects(),
      repo: () => this.loadRepo(),
      research: () => this.loadResearch(),
      notify: () => this.loadNotifications(),
      help: () => Help.render(),
      settings: () => this.loadSettings(),
    };
    if (loaders[tab]) await loaders[tab]();
  },

  // ── Status ────────────────────────────────────────────────────────────────
  async loadStatus() {
    const container = document.getElementById('status-content');
    const cols = this.getColumns('status-content');
    container.innerHTML = '<div class="tab-loading"><div class="spinner"></div>Loading status...</div>';
    try {
      const url = cols ? `/api/status?columns=${cols}` : '/api/status';
      const res = await fetch(url);
      const data = await res.json();
      container.innerHTML = `<div class="terminal-block">${data.output}</div>`;
    } catch (e) {
      container.innerHTML = `<div class="terminal-block" style="color: var(--status-error);">Failed to load status: ${e.message}</div>`;
    }
  },

  // ── Projects ──────────────────────────────────────────────────────────────
  _projectDevExpanded: {},

  async loadProjects() {
    const container = document.getElementById('projects-content');
    container.innerHTML = '<div class="tab-loading"><div class="spinner"></div>Loading projects...</div>';
    try {
      const res = await fetch('/api/projects');
      const data = await res.json();
      const currentProject = this.getCurrentProject();

      let html = '';
      for (const p of data.projects) {
        const isCurrent = p.key === currentProject;
        const services = (p.services || []).length;
        const devServices = p.dev_services || [];
        const enabledDev = devServices.filter(s => s.enabled).length;
        const hasDevServices = devServices.length > 0;

        // Dev services expandable section
        let devHtml = '';
        if (hasDevServices) {
          const expanded = this._projectDevExpanded[p.key];
          const chevron = expanded ? '&#9660;' : '&#9654;';
          let svcRows = '';
          for (const svc of devServices) {
            const name = this.escapeHtml(svc.name || 'unnamed');
            const port = svc.port || '';
            const cmd = this.escapeHtml((svc.cmd || '').length > 40 ? svc.cmd.substring(0, 40) + '...' : svc.cmd || '');
            const btnClass = svc.enabled ? 'badge-success' : 'badge-error';
            const btnText = svc.enabled ? 'ON' : 'OFF';
            svcRows += `
              <div class="dev-svc-row">
                <span class="dev-svc-name">${name}</span>
                ${port ? `<span class="text-xs text-muted">:${port}</span>` : ''}
                <span class="text-xs text-muted" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${cmd}</span>
                <span class="badge ${btnClass}" style="cursor:pointer;" onclick="Tabs.toggleDevService('${p.key}', '${this.escapeHtml(svc.name)}', ${!svc.enabled})">${btnText}</span>
              </div>`;
          }
          devHtml = `
            <div class="dev-services-toggle" onclick="Tabs.toggleDevSection('${p.key}')">
              <span>${chevron}</span> Dev Services (${enabledDev}/${devServices.length})
            </div>
            <div class="dev-services-list" style="display:${expanded ? 'block' : 'none'};" id="dev-list-${p.key}">
              ${svcRows}
            </div>`;
        }

        // Edit form placeholder
        const editId = `proj-edit-${p.key}`;

        html += `
          <div class="project-card ${isCurrent ? 'current' : ''}" id="proj-card-${p.key}">
            <div class="project-info" style="flex:1;">
              <div class="project-name" style="display:flex;align-items:center;gap:var(--space-sm);">
                ${this.escapeHtml(p.name)}
                ${isCurrent ? '<span class="badge badge-info">Current</span>' : ''}
              </div>
              ${p.tagline ? `<div class="project-tagline">${this.escapeHtml(p.tagline)}</div>` : ''}
              <div class="project-meta">
                <span>${this.escapeHtml(p.host)}</span>
                <span>${this.escapeHtml(p.path)}</span>
                ${services > 0 ? `<span>${services} service${services > 1 ? 's' : ''}</span>` : ''}
                ${enabledDev > 0 ? `<span>${enabledDev} dev</span>` : ''}
              </div>
              ${devHtml}
              <div id="${editId}"></div>
              <div id="proj-output-${p.key}"></div>
            </div>
            <div style="display:flex;flex-direction:column;gap:var(--space-xs);align-items:flex-end;">
              ${!isCurrent ? `<button class="btn btn-sm" onclick="Tabs.switchProject('${p.key}')">Switch</button>` : ''}
              <div style="display:flex;gap:var(--space-xs);">
                <button class="btn btn-sm" onclick="Tabs.runProjectAction('${p.key}', 'prepare')" title="Prepare">Prepare</button>
                <button class="btn btn-sm" onclick="Tabs.runProjectAction('${p.key}', 'work')" title="Work">Work</button>
              </div>
              <div style="display:flex;gap:var(--space-xs);">
                <button class="btn btn-sm" onclick="Tabs.editProject('${p.key}')" title="Edit">Edit</button>
                <button class="btn btn-sm btn-danger" onclick="Tabs.deleteProject('${p.key}', '${this.escapeHtml(p.name)}')" title="Delete">Del</button>
              </div>
            </div>
          </div>
        `;
      }
      container.innerHTML = html || '<div class="text-muted">No projects registered</div>';
    } catch (e) {
      container.innerHTML = `<div class="text-muted">Failed to load projects: ${e.message}</div>`;
    }
  },

  toggleDevSection(key) {
    this._projectDevExpanded[key] = !this._projectDevExpanded[key];
    const list = document.getElementById(`dev-list-${key}`);
    if (list) {
      list.style.display = this._projectDevExpanded[key] ? 'block' : 'none';
    }
    // Update chevron (reload is cleaner but preserving state)
    this.loaded.projects = false;
    this.loadProjects();
  },

  async toggleDevService(key, name, enabled) {
    try {
      const res = await fetch('/api/projects/dev-toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: key, service: name, enabled }),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast(`${name}: ${enabled ? 'enabled' : 'disabled'}`, 'success');
        this._projectDevExpanded[key] = true;
        this.loaded.projects = false;
        this.loadProjects();
      } else {
        App.toast(data.error || 'Toggle failed', 'error');
      }
    } catch (e) {
      App.toast('Toggle failed: ' + e.message, 'error');
    }
  },

  async runProjectAction(key, action) {
    const outEl = document.getElementById(`proj-output-${key}`);
    if (outEl) outEl.innerHTML = `<div class="tab-loading" style="padding:var(--space-sm);"><div class="spinner"></div>Running ${action}...</div>`;
    try {
      const res = await fetch('/api/magic', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: `%${action} ${key}` }),
      });
      const data = await res.json();
      if (outEl) outEl.innerHTML = `<div class="terminal-block" style="margin-top:var(--space-sm);max-height:200px;overflow-y:auto;">${data.output || 'Done'}</div>`;
    } catch (e) {
      if (outEl) outEl.innerHTML = `<div class="text-muted">Failed: ${e.message}</div>`;
    }
  },

  editProject(key) {
    const el = document.getElementById(`proj-edit-${key}`);
    if (!el) return;
    if (el.innerHTML) { el.innerHTML = ''; return; }

    // Find current project data
    const card = document.getElementById(`proj-card-${key}`);
    const nameEl = card?.querySelector('.project-name');
    const taglineEl = card?.querySelector('.project-tagline');
    const currentName = nameEl ? nameEl.textContent.trim() : key;
    const currentTagline = taglineEl ? taglineEl.textContent.trim() : '';

    el.innerHTML = `
      <div style="margin-top:var(--space-sm);padding:var(--space-sm);border:1px solid var(--border-secondary);border-radius:var(--radius-md);background:var(--bg-primary);">
        <div style="margin-bottom:var(--space-xs);">
          <label class="settings-label">Name</label>
          <input type="text" class="input proj-edit-name" value="${this.escapeHtml(currentName)}" style="width:100%;max-width:300px;">
        </div>
        <div style="margin-bottom:var(--space-xs);">
          <label class="settings-label">Tagline</label>
          <input type="text" class="input proj-edit-tagline" value="${this.escapeHtml(currentTagline)}" style="width:100%;max-width:400px;">
        </div>
        <div style="display:flex;gap:var(--space-sm);">
          <button class="btn btn-sm" onclick="Tabs.saveProjectEdit('${key}')">Save</button>
          <button class="btn btn-sm" onclick="document.getElementById('proj-edit-${key}').innerHTML=''">Cancel</button>
        </div>
      </div>`;
  },

  async saveProjectEdit(key) {
    const el = document.getElementById(`proj-edit-${key}`);
    if (!el) return;
    const name = el.querySelector('.proj-edit-name')?.value.trim();
    const tagline = el.querySelector('.proj-edit-tagline')?.value.trim() || '';
    if (!name) { App.toast('Name cannot be empty', 'error'); return; }

    try {
      const res = await fetch('/api/projects/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, data: { name, tagline } }),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast('Project updated', 'success');
        this.loaded.projects = false;
        this.loadProjects();
      } else {
        App.toast(data.error || 'Update failed', 'error');
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  async deleteProject(key, name) {
    if (!confirm(`Delete project "${name}"? This removes it from the registry (files are not deleted).`)) return;
    try {
      const res = await fetch('/api/projects/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key }),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast(`Deleted ${name}`, 'success');
        this.loaded.projects = false;
        this.loadProjects();
      } else {
        App.toast(data.error || 'Delete failed', 'error');
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  async switchProject(key) {
    try {
      const res = await fetch('/api/projects/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: key }),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast(`Switched to ${data.name}`, 'success');
        this.loaded.projects = false;
        this.loadProjects();
      } else {
        App.toast(data.error || 'Switch failed', 'error');
      }
    } catch (e) {
      App.toast('Switch failed: ' + e.message, 'error');
    }
  },

  getCurrentProject() {
    // From env vars set by switch
    return window._currentProject || '';
  },

  // ── Repo ──────────────────────────────────────────────────────────────────
  async loadRepo() {
    const container = document.getElementById('repo-content');
    const cols = this.getColumns('repo-content');
    container.innerHTML = '<div class="tab-loading"><div class="spinner"></div>Loading repo info...</div>';
    try {
      const url = cols ? `/api/repo?columns=${cols}` : '/api/repo';
      const res = await fetch(url);
      const data = await res.json();
      container.innerHTML = `<div class="terminal-block">${data.output}</div>`;
    } catch (e) {
      container.innerHTML = `<div class="terminal-block" style="color: var(--status-error);">Failed: ${e.message}</div>`;
    }
  },

  // ── Research ──────────────────────────────────────────────────────────────
  async loadResearch() {
    const container = document.getElementById('research-content');
    const cols = this.getColumns('research-content');
    container.innerHTML = '<div class="tab-loading"><div class="spinner"></div>Loading research digest...</div>';
    try {
      const url = cols ? `/api/research?columns=${cols}` : '/api/research';
      const res = await fetch(url);
      const data = await res.json();
      container.innerHTML = `
        <div class="flex justify-between items-center mb-md">
          <button class="btn btn-sm" onclick="Tabs.fetchResearch()">Fetch Now</button>
          <button class="btn btn-sm btn-icon" onclick="Tabs.refreshResearch()">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
        </div>
        <div class="terminal-block">${data.output}</div>
      `;
    } catch (e) {
      container.innerHTML = `<div class="terminal-block" style="color: var(--status-error);">Failed: ${e.message}</div>`;
    }
  },

  async fetchResearch() {
    const container = document.getElementById('research-content');
    const existing = container.querySelector('.terminal-block');
    const fetchMsg = document.createElement('div');
    fetchMsg.className = 'tab-loading';
    fetchMsg.style.padding = 'var(--space-md)';
    fetchMsg.innerHTML = '<div class="spinner"></div>Fetching research (this may take a few minutes)...';
    if (existing) existing.before(fetchMsg);
    else container.appendChild(fetchMsg);

    try {
      const res = await fetch('/api/research/fetch', { method: 'POST' });
      const data = await res.json();
      fetchMsg.remove();
      App.toast('Research fetch complete', 'success');
      this.loaded.research = false;
      this.loadResearch();
    } catch (e) {
      fetchMsg.innerHTML = `<div class="text-muted">Fetch failed: ${e.message}</div>`;
    }
  },

  refreshResearch() {
    this.loaded.research = false;
    this.loadResearch();
  },

  // ── Notifications ─────────────────────────────────────────────────────────
  async loadNotifications() {
    const container = document.getElementById('notify-content');
    const cols = this.getColumns('notify-content');
    container.innerHTML = '<div class="tab-loading"><div class="spinner"></div>Loading notifications...</div>';
    try {
      const url = cols ? `/api/notifications?columns=${cols}` : '/api/notifications';
      const res = await fetch(url);
      const data = await res.json();
      container.innerHTML = `
        <div class="flex justify-between items-center mb-md">
          <button class="btn btn-sm" onclick="Tabs.clearNotifications()">Mark all read</button>
          <button class="btn btn-sm btn-icon" onclick="Tabs.refreshNotifications()">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
        </div>
        <div class="terminal-block">${data.output}</div>
      `;
    } catch (e) {
      container.innerHTML = `<div class="text-muted">Failed: ${e.message}</div>`;
    }
  },

  async clearNotifications() {
    try {
      await fetch('/api/notifications/clear', { method: 'POST' });
      App.toast('Notifications cleared', 'success');
      this.loaded.notify = false;
      this.loadNotifications();
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  refreshNotifications() {
    this.loaded.notify = false;
    this.loadNotifications();
  },

  // ── Settings ──────────────────────────────────────────────────────────────
  async loadSettings() {
    await Settings.load();
  },

  async resetSession() {
    if (!confirm('Reset the conversation? This clears all messages.')) return;
    try {
      await fetch('/api/session/reset', { method: 'POST' });
      App.toast('Session reset', 'success');
      // Clear chat UI
      const msgs = document.getElementById('chat-messages');
      msgs.innerHTML = '';
      const welcome = document.getElementById('welcome-screen');
      if (welcome) welcome.classList.remove('hidden');
      msgs.classList.add('hidden');
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  // ── Refresh tab data ─────────────────────────────────────────────────────
  refreshCurrentTab() {
    const tab = App.activeTab;
    if (tab !== 'chat') {
      this.loaded[tab] = false;
      this.loadTab(tab);
    }
  },

  // ── Column width measurement ─────────────────────────────────────────────
  getColumns(containerId) {
    const el = document.getElementById(containerId);
    if (!el) return null;
    // Measure character width using a monospace probe
    const probe = document.createElement('span');
    probe.style.cssText = 'font-family:var(--font-mono);font-size:var(--font-size-sm);visibility:hidden;position:absolute;white-space:pre;';
    probe.textContent = 'X'.repeat(10);
    el.appendChild(probe);
    const charWidth = probe.offsetWidth / 10;
    el.removeChild(probe);
    if (!charWidth) return null;
    // Account for terminal-block padding (--space-md = 16px typically, both sides)
    const style = getComputedStyle(el);
    const padding = (parseFloat(style.paddingLeft) || 16) + (parseFloat(style.paddingRight) || 16);
    const availWidth = el.clientWidth - padding;
    return Math.floor(availWidth / charWidth) - 1;
  },

  escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },
};

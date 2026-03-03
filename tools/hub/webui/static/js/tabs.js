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
        const devServices = (p.dev_services || []).filter(s => s.enabled).length;

        html += `
          <div class="project-card ${isCurrent ? 'current' : ''}">
            <div class="project-info">
              <div class="project-name">
                ${this.escapeHtml(p.name)}
                ${isCurrent ? '<span class="badge badge-info" style="margin-left: 8px;">Current</span>' : ''}
              </div>
              ${p.tagline ? `<div class="project-tagline">${this.escapeHtml(p.tagline)}</div>` : ''}
              <div class="project-meta">
                <span>${this.escapeHtml(p.host)}</span>
                <span>${this.escapeHtml(p.path)}</span>
                ${services > 0 ? `<span>${services} service${services > 1 ? 's' : ''}</span>` : ''}
                ${devServices > 0 ? `<span>${devServices} dev</span>` : ''}
              </div>
            </div>
            ${!isCurrent ? `<button class="btn btn-sm" onclick="Tabs.switchProject('${p.key}')">Switch</button>` : ''}
          </div>
        `;
      }
      container.innerHTML = html || '<div class="text-muted">No projects registered</div>';
    } catch (e) {
      container.innerHTML = `<div class="text-muted">Failed to load projects: ${e.message}</div>`;
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
      container.innerHTML = `<div class="terminal-block">${data.output}</div>`;
    } catch (e) {
      container.innerHTML = `<div class="terminal-block" style="color: var(--status-error);">Failed: ${e.message}</div>`;
    }
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

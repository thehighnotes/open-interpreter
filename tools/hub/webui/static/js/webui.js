/* OI WebUI — App core: init, tab switching, toast, config */

const App = {
  config: null,
  activeTab: 'chat',
  initialized: false,

  async init() {
    if (this.initialized) return;
    this.initialized = true;

    // Fetch config
    try {
      const res = await fetch('/api/config');
      this.config = await res.json();
      this.updateHubName();
      this.updateConnectionStatus();
    } catch (e) {
      console.error('Config fetch failed:', e);
    }

    // Tab switching
    document.querySelectorAll('[data-tab]').forEach(el => {
      el.addEventListener('click', () => this.switchTab(el.dataset.tab));
    });

    // URL-based tab routing — check path for /status, /settings, etc.
    const path = window.location.pathname.replace(/^\/+|\/+$/g, '');
    const validTabs = ['chat','status','projects','repo','research','notify','help','settings'];
    if (path && validTabs.includes(path)) {
      this.switchTab(path, true);
    }

    // Handle browser back/forward
    window.addEventListener('popstate', () => {
      const p = window.location.pathname.replace(/^\/+|\/+$/g, '');
      if (p && validTabs.includes(p)) {
        this.switchTab(p, true);
      } else {
        this.switchTab('chat', true);
      }
    });

    // Initialize chat
    Chat.init();

    // Load session info
    this.loadSessionInfo();
  },

  switchTab(tab, skipPush) {
    if (tab === this.activeTab && !skipPush) return;
    this.activeTab = tab;

    // Update sidebar
    document.querySelectorAll('.sidebar-tab').forEach(el => {
      el.classList.toggle('active', el.dataset.tab === tab);
    });
    // Update mobile tabs
    document.querySelectorAll('.mobile-tab').forEach(el => {
      el.classList.toggle('active', el.dataset.tab === tab);
    });
    // Update content
    document.querySelectorAll('.tab-content').forEach(el => {
      el.classList.toggle('active', el.id === `tab-${tab}`);
    });

    // Update URL without reload
    if (!skipPush) {
      const url = tab === 'chat' ? '/' : `/${tab}`;
      history.pushState({ tab }, '', url);
    }

    // Load data for tab on first visit
    Tabs.onTabActivated(tab);
  },

  updateHubName() {
    const el = document.getElementById('hub-name');
    if (el && this.config) {
      el.textContent = this.config.hub_name || 'Dev Hub';
    }
  },

  async updateConnectionStatus() {
    try {
      const res = await fetch('/api/session');
      const info = await res.json();
      const dot = document.getElementById('connection-dot');
      const label = document.getElementById('connection-label');
      if (dot) dot.classList.toggle('offline', !info.connected);
      if (label) label.textContent = info.connected ? `${info.model}` : 'Disconnected';
    } catch {
      const dot = document.getElementById('connection-dot');
      if (dot) dot.classList.add('offline');
    }
  },

  async loadSessionInfo() {
    try {
      const res = await fetch('/api/session/messages');
      const data = await res.json();
      if (data.messages && data.messages.length > 0) {
        Chat.restoreMessages(data.messages);
      }
    } catch (e) {
      console.log('No previous session to restore');
    }
  },

  toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type === 'error' ? 'toast-error' : type === 'success' ? 'toast-success' : ''}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity 0.3s';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  },
};

// Init on DOM ready
document.addEventListener('DOMContentLoaded', () => App.init());

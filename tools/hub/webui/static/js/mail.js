/* OI WebUI — Mail tab: LLM-driven scan → review → apply */

const Mail = {
  _data: null,

  _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },

  // ── Main entry ────────────────────────────────────────────────────────────

  async load() {
    const container = document.getElementById('mail-content');
    container.innerHTML = '<div class="tab-loading"><div class="spinner"></div>Loading...</div>';
    try {
      const res = await fetch('/api/mail');
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      this._data = data;
      this._render();
    } catch (e) {
      container.innerHTML = `<div class="text-muted">Failed to load: ${this._esc(e.message)}</div>`;
    }
  },

  _render() {
    const d = this._data;
    const container = document.getElementById('mail-content');

    if (!d.authenticated) {
      container.innerHTML = `
        <div style="padding:var(--space-xl);text-align:center;color:var(--text-tertiary)">
          <p style="margin-bottom:var(--space-md)">Gmail is not connected yet.</p>
          <p>Set up credentials in <a href="/settings" style="color:var(--accent-primary)" onclick="event.preventDefault();App.switchTab('settings')">Settings &gt; Gmail</a>.</p>
        </div>`;
      return;
    }

    let html = '';

    // Stats bar
    const config = d.config || {};
    const mode = config.mode === 'auto' ? 'Auto' : 'Manual';
    const scope = `${config.scope_read === 'unread' ? 'Unread' : 'All'} / ${config.scope_label === 'inbox' ? 'Inbox' : 'All labels'}`;
    const lastScan = d.scan_ts ? new Date(d.scan_ts * 1000).toLocaleString() : 'Never';
    const llmBadge = d.llm_available
      ? '<span class="badge badge-success" style="font-size:var(--font-size-xs)">LLM</span>'
      : '<span class="badge badge-warning" style="font-size:var(--font-size-xs)">No LLM</span>';

    html += `
      <div style="display:flex;gap:var(--space-lg);margin-bottom:var(--space-lg);flex-wrap:wrap;align-items:center">
        <div style="color:var(--text-tertiary)">Mode: <strong style="color:var(--text-primary)">${mode}</strong></div>
        <div style="color:var(--text-tertiary)">Scope: <strong style="color:var(--text-primary)">${scope}</strong></div>
        <div style="color:var(--text-tertiary)">Batch: <strong style="color:var(--text-primary)">${config.batch_size || 25}</strong></div>
        <div style="color:var(--text-tertiary)">Last scan: <strong style="color:var(--text-primary)">${lastScan}</strong></div>
        ${llmBadge}
      </div>`;

    // Smart suggestions (from accumulated patterns)
    const suggestions = d.suggestions || [];
    if (suggestions.length > 0) {
      html += this._foldable('Smart Suggestions', this._renderSuggestions(suggestions), {
        open: true, count: suggestions.length, accent: true,
      });
    }

    // Scan results (pending review)
    const results = d.scan_results || [];
    const pending = results.filter(r => r.approved === null);

    if (pending.length > 0) {
      html += this._foldable('Scan Results', this._renderScanResults(pending, config.mode), {
        open: true, count: pending.length,
      });
    } else if (results.length > 0) {
      html += `<div style="padding:var(--space-md);color:var(--text-tertiary);text-align:center;margin-bottom:var(--space-lg)">
        Last scan fully reviewed. Click <strong>Scan</strong> to check for new emails.
      </div>`;
    }

    // Recent activity
    const actions = d.recent_actions || [];
    if (actions.length > 0) {
      html += this._foldable('Recent Activity', this._renderActivity(actions), {
        open: false, count: actions.length,
      });
    }

    container.innerHTML = html;
  },

  _foldable(title, body, {open = true, count = null, accent = false} = {}) {
    const border = accent ? 'border-color:var(--accent-primary);' : '';
    const badge = count !== null ? ` <span class="badge badge-info" style="font-size:var(--font-size-xs);margin-left:var(--space-xs)">${count}</span>` : '';
    return `<details class="settings-section" style="${border}"${open ? ' open' : ''}>
      <summary>
        <div class="settings-section-title">${title}${badge}</div>
      </summary>
      ${body}
    </details>`;
  },

  // ── Scan results ──────────────────────────────────────────────────────────

  _renderScanResults(results, mode) {
    let html = '';

    if (mode === 'manual') {
      // Manual mode: batch review with checkboxes
      html += `<div style="margin-bottom:var(--space-sm);display:flex;justify-content:space-between;align-items:center">
        <span style="font-weight:600;color:var(--text-primary)">${results.length} email(s) to review</span>
        <div style="display:flex;gap:var(--space-xs)">
          <button class="btn btn-sm" onclick="Mail.approveAll()">Approve All</button>
          <button class="btn btn-sm" onclick="Mail.applySelected()">Apply Selected</button>
        </div>
      </div>`;

      for (const r of results) {
        const actionColor = r.action === 'archive' ? 'var(--status-success)'
                          : r.action === 'delete' ? 'var(--status-error)'
                          : 'var(--text-secondary)';
        const actionLabel = r.action === 'archive' ? 'Archive'
                          : r.action === 'delete' ? 'Delete'
                          : 'Keep';

        html += `
          <div class="mail-review-item" data-msgid="${this._esc(r.msg_id)}" data-action="${r.action}"
               style="display:flex;align-items:flex-start;gap:var(--space-sm);padding:var(--space-sm);border-bottom:1px solid var(--border-secondary)">
            <div style="flex-shrink:0;padding-top:2px">
              <select class="input mail-action-select" data-msgid="${this._esc(r.msg_id)}"
                      style="font-size:var(--font-size-sm);padding:2px 4px;width:80px">
                <option value="archive" ${r.action === 'archive' ? 'selected' : ''}>Archive</option>
                <option value="delete" ${r.action === 'delete' ? 'selected' : ''}>Delete</option>
                <option value="keep" ${r.action === 'keep' ? 'selected' : ''}>Keep</option>
                <option value="reject">Skip</option>
              </select>
            </div>
            <div style="flex:1;min-width:0">
              <div style="font-size:var(--font-size-sm);color:var(--text-primary)">${this._esc(r.from)}</div>
              <div style="font-size:var(--font-size-sm);color:var(--text-secondary)">${this._esc(r.subject)}</div>
              <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:2px">
                <span style="color:${actionColor}">${actionLabel}</span>
                ${r.source === 'llm' ? '<span style="color:var(--accent-primary)"> LLM</span>' : ''}
                ${r.reason ? ` &mdash; ${this._esc(r.reason)}` : ''}
              </div>
            </div>
          </div>`;
      }

      html += `<div style="margin-top:var(--space-md);display:flex;gap:var(--space-xs)">
        <button class="btn btn-sm" onclick="Mail.applySelected()">Apply Selected</button>
      </div>`;

    } else {
      // Auto mode: suggestion cards
      html += `<div style="margin-bottom:var(--space-sm);font-weight:600;color:var(--text-primary)">${results.length} suggestion(s)</div>`;

      for (const r of results) {
        if (r.action === 'keep') continue; // Only show actionable suggestions

        const actionColor = r.action === 'archive' ? 'var(--status-success)' : 'var(--status-error)';
        const actionLabel = r.action === 'archive' ? 'Archive' : 'Tag for Delete';
        const icon = r.action === 'archive' ? '&#x1f4e5;' : '&#x1f5d1;';

        html += `
          <div style="display:flex;align-items:flex-start;gap:var(--space-md);padding:var(--space-sm);border-bottom:1px solid var(--border-secondary)">
            <div style="flex-shrink:0;font-size:1.1em">${icon}</div>
            <div style="flex:1;min-width:0">
              <div style="font-size:var(--font-size-sm);color:var(--text-primary)">${this._esc(r.from)}</div>
              <div style="font-size:var(--font-size-sm);color:var(--text-secondary)">${this._esc(r.subject)}</div>
              <div style="font-size:var(--font-size-xs);color:var(--text-tertiary)">
                <span style="color:${actionColor}">${actionLabel}</span>
                ${r.reason ? ` &mdash; ${this._esc(r.reason)}` : ''}
              </div>
            </div>
            <div style="display:flex;gap:var(--space-xs);flex-shrink:0">
              <button class="btn btn-sm" onclick="Mail.applyOne('${this._esc(r.msg_id)}', '${r.action}')">
                ${actionLabel}
              </button>
              <button class="btn btn-sm" onclick="Mail.applyOne('${this._esc(r.msg_id)}', 'reject')">Skip</button>
            </div>
          </div>`;
      }
    }

    return html;
  },

  // ── Smart suggestions ─────────────────────────────────────────────────────

  _renderSuggestions(suggestions) {
    let html = '';
    for (const s of suggestions) {
      const icon = s.action === 'archive' ? '&#x1f4cb;' : '&#x1f5d1;';
      const stats = s.stats || {};
      const statsText = `archived ${stats.archive || 0}, deleted ${stats.delete || 0}, kept ${stats.keep || 0}`;

      html += `
        <div style="display:flex;align-items:flex-start;gap:var(--space-md);padding:var(--space-sm);border-bottom:1px solid var(--border-secondary)">
          <div style="flex-shrink:0;font-size:1.2em">${icon}</div>
          <div style="flex:1;min-width:0">
            <div style="font-weight:600;color:var(--text-primary)">${this._esc(s.title)}</div>
            <div style="font-size:var(--font-size-sm);color:var(--text-tertiary)">${this._esc(s.description)}</div>
            <div style="font-size:var(--font-size-xs);color:var(--text-tertiary);margin-top:2px">Pattern: ${statsText}</div>
          </div>
          <div style="display:flex;gap:var(--space-xs);flex-shrink:0">
            <button class="btn btn-sm" onclick="Mail.acceptSuggestion('${s.id}')">Add Rule</button>
            <button class="btn btn-sm" onclick="Mail.dismissSuggestion('${s.id}')">Dismiss</button>
          </div>
        </div>`;
    }
    return html;
  },

  async acceptSuggestion(id) {
    try {
      const res = await fetch('/api/mail/suggestions/accept', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id}),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast('Fast-filter rule added!', 'success');
        Tabs.loaded.mail = false;
        Mail.load();
      } else {
        App.toast(data.error || 'Failed', 'error');
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  async dismissSuggestion(id) {
    try {
      await fetch('/api/mail/suggestions/dismiss', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id}),
      });
      Tabs.loaded.mail = false;
      Mail.load();
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  // ── Actions ───────────────────────────────────────────────────────────────

  async scan() {
    if (!this._data?.authenticated) {
      App.toast('Not authenticated', 'error');
      return;
    }
    const container = document.getElementById('mail-content');
    container.innerHTML = '<div class="tab-loading"><div class="spinner"></div>Scanning inbox with LLM...</div>';
    try {
      const res = await fetch('/api/mail/scan', { method: 'POST' });
      const data = await res.json();
      if (data.ok) {
        if (data.auto_applied) {
          App.toast(`Auto: ${data.scanned} scanned, ${data.archived || 0} archived, ${data.tagged_delete || 0} tagged`, 'success');
        } else {
          App.toast(`Scanned ${data.scanned} emails — review below`, 'success');
        }
        Tabs.loaded.mail = false;
        Mail.load();
      } else {
        App.toast(data.error || 'Scan failed', 'error');
        Mail.load();
      }
    } catch (e) {
      App.toast('Scan failed: ' + e.message, 'error');
      Mail.load();
    }
  },

  approveAll() {
    document.querySelectorAll('.mail-action-select').forEach(sel => {
      const item = sel.closest('.mail-review-item');
      const suggested = item?.dataset.action;
      if (suggested && suggested !== 'keep') {
        sel.value = suggested;
      }
    });
    App.toast('All suggestions approved. Click Apply Selected.', 'info');
  },

  _collectApprovals() {
    const approvals = {};
    document.querySelectorAll('.mail-action-select').forEach(sel => {
      const msgId = sel.dataset.msgid;
      if (msgId) approvals[msgId] = sel.value;
    });
    return approvals;
  },

  async applySelected() {
    const approvals = this._collectApprovals();
    const actionCount = Object.values(approvals).filter(v => v !== 'keep' && v !== 'reject').length;
    if (!actionCount) {
      App.toast('Nothing to apply — all set to Keep/Skip', 'info');
      return;
    }
    App.toast(`Applying ${actionCount} action(s)...`, 'info');
    try {
      const res = await fetch('/api/mail/apply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({approvals}),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast(`Done: ${data.archived} archived, ${data.tagged_delete} tagged`, 'success');
        Tabs.loaded.mail = false;
        Mail.load();
      } else {
        App.toast(data.error || 'Apply failed', 'error');
      }
    } catch (e) {
      App.toast('Apply failed: ' + e.message, 'error');
    }
  },

  async applyOne(msgId, action) {
    try {
      const res = await fetch('/api/mail/apply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({approvals: {[msgId]: action}}),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast(action === 'reject' ? 'Skipped' : `${action}d`, 'success');
        Tabs.loaded.mail = false;
        Mail.load();
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  // ── Activity log ──────────────────────────────────────────────────────────

  _renderActivity(actions) {
    if (!actions || actions.length === 0) return '';

    let html = '<div style="font-family:var(--font-mono);font-size:var(--font-size-sm)">';

    for (const a of actions) {
      const time = new Date(a.ts * 1000).toLocaleString();
      const color = a.action === 'archive' ? 'var(--status-success)'
                  : a.action === 'delete' ? 'var(--status-error)'
                  : 'var(--text-secondary)';
      const label = a.action === 'archive' ? 'Archived'
                  : a.action === 'delete' ? 'Tagged'
                  : 'Kept';
      const source = a.source === 'llm' ? ' <span style="color:var(--accent-primary);font-size:var(--font-size-xs)">LLM</span>'
                   : a.source === 'rule' ? ' <span style="color:var(--text-tertiary);font-size:var(--font-size-xs)">rule</span>'
                   : '';

      html += `
        <div style="padding:4px 0;border-bottom:1px solid var(--border-secondary)">
          <span style="color:var(--text-tertiary);font-size:var(--font-size-xs)">${time}</span>
          <strong style="color:${color}">${label}</strong>${source}
          <span style="color:var(--text-secondary)"> ${this._esc(a.from)}</span>
          <span style="color:var(--text-tertiary)"> &mdash; ${this._esc(a.subject)}</span>
        </div>`;
    }

    html += '</div>';
    return html;
  },
};

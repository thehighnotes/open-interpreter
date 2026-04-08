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

    // LLM advice (from accumulated triage patterns)
    const advice = d.advice || {};
    if (advice.advice) {
      const age = advice.ts ? new Date(advice.ts * 1000).toLocaleString() : '';
      let rendered = typeof marked !== 'undefined'
        ? marked.parse(advice.advice, {breaks: true, gfm: true})
        : `<pre style="white-space:pre-wrap">${this._esc(advice.advice)}</pre>`;
      // Make email addresses in inline code clickable
      rendered = rendered.replace(/<code>([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})<\/code>/g,
        '<code class="mail-email-action" data-email="$1" style="cursor:pointer;text-decoration:underline;text-decoration-style:dotted" title="Click for actions">$1</code>');
      html += this._foldable('Insights', `
        <div class="msg-bubble markdown-content" style="padding:var(--space-sm);color:var(--text-secondary);font-size:var(--font-size-sm);line-height:1.6;background:transparent;border:none;max-width:none">${rendered}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;padding:0 var(--space-sm) var(--space-sm)">
          ${age ? `<span style="font-size:var(--font-size-xs);color:var(--text-tertiary)">Updated ${age}</span>` : '<span></span>'}
          <button class="btn btn-sm" onclick="Mail.refreshInsights()">Refresh</button>
        </div>
      `, {open: true});
    }

    // Filter rules
    const rules = d.rules || [];
    if (rules.length > 0) {
      html += this._foldable('Filter Rules', this._renderRules(rules), {
        open: false, count: rules.length,
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
    this._initEmailActions();
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

  // ── Insights actions ──────────────────────────────────────────────────────

  async refreshInsights() {
    App.toast('Refreshing insights...', 'info');
    try {
      const res = await fetch('/api/mail/advice/refresh', {method: 'POST'});
      const data = await res.json();
      if (data.ok) {
        this._data.advice = {advice: data.advice, ts: data.ts};
        this._render();
        App.toast('Insights updated', 'success');
      } else {
        App.toast(data.error || 'Failed', 'error');
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  _initEmailActions() {
    const container = document.getElementById('mail-content');
    if (!container) return;
    container.addEventListener('click', (e) => {
      const el = e.target.closest('.mail-email-action');
      if (!el) return;
      e.preventDefault();
      const email = el.dataset.email;
      if (email) this._showEmailPopup(el, email);
    });
  },

  _showEmailPopup(anchor, email) {
    // Remove any existing popup
    document.querySelectorAll('.mail-email-popup').forEach(p => p.remove());

    const popup = document.createElement('div');
    popup.className = 'mail-email-popup';
    popup.style.cssText = 'position:absolute;z-index:100;background:var(--bg-elevated);border:1px solid var(--border-primary);border-radius:var(--radius-sm);padding:var(--space-sm);box-shadow:0 4px 12px rgba(0,0,0,0.3);min-width:200px;font-size:var(--font-size-sm)';

    popup.innerHTML = `
      <div style="font-weight:600;color:var(--text-primary);margin-bottom:var(--space-xs);word-break:break-all">${this._esc(email)}</div>
      <div style="display:flex;flex-direction:column;gap:4px">
        <button class="btn btn-sm" onclick="Mail.createRuleFor('${this._esc(email)}','archive')">Auto-archive</button>
        <button class="btn btn-sm" onclick="Mail.createRuleFor('${this._esc(email)}','delete')">Auto-delete</button>
        <button class="btn btn-sm" onclick="Mail.unsubscribe('${this._esc(email)}')">Unsubscribe</button>
      </div>`;

    // Position near anchor
    const rect = anchor.getBoundingClientRect();
    const container = document.getElementById('mail-content');
    const cRect = container.getBoundingClientRect();
    popup.style.left = (rect.left - cRect.left) + 'px';
    popup.style.top = (rect.bottom - cRect.top + 4) + 'px';
    container.style.position = 'relative';
    container.appendChild(popup);

    // Close on click outside
    const close = (e) => { if (!popup.contains(e.target) && e.target !== anchor) { popup.remove(); document.removeEventListener('click', close); } };
    setTimeout(() => document.addEventListener('click', close), 0);
  },

  async createRuleFor(email, action) {
    document.querySelectorAll('.mail-email-popup').forEach(p => p.remove());
    // Ask if this should be time-based
    const days = prompt(`Auto-${action} emails from ${email}.\n\nOptional: only apply to emails older than N days.\nLeave blank for immediate (all emails).`, '');
    const match = { from: email };
    let name = `Auto-${action} ${email}`;
    if (days && parseInt(days) > 0) {
      match.older_than = parseInt(days);
      name += ` (after ${days}d)`;
    }
    try {
      const res = await fetch('/api/mail/rules/add', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({rule: {name, match, action}}),
      });
      const data = await res.json();
      if (data.ok) {
        const retro = data.rule?._retroactive || 0;
        App.toast(`Rule added: ${name}${retro ? ` (matched ${retro} existing)` : ''}`, 'success');
        Tabs.loaded.mail = false;
        Mail.load();
      } else {
        App.toast(data.error || 'Failed', 'error');
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  async unsubscribe(email) {
    document.querySelectorAll('.mail-email-popup').forEach(p => p.remove());
    App.toast('Looking up unsubscribe link...', 'info');
    try {
      const res = await fetch('/api/mail/unsubscribe', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email}),
      });
      const data = await res.json();
      if (data.ok) {
        if (data.type === 'url') {
          window.open(data.link, '_blank');
          App.toast('Unsubscribe page opened', 'success');
        } else {
          // mailto link
          window.location.href = data.link;
          App.toast('Unsubscribe email opened', 'success');
        }
      } else {
        App.toast(data.error || 'No unsubscribe link found', 'error');
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  async deleteRule(ruleId) {
    try {
      const res = await fetch('/api/mail/rules/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: ruleId}),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast('Rule deleted', 'success');
        Tabs.loaded.mail = false;
        Mail.load();
      }
    } catch (e) {
      App.toast('Failed: ' + e.message, 'error');
    }
  },

  // ── Filter rules ─────────────────────────────────────────────────────────

  _renderRules(rules) {
    let html = '';
    for (const r of rules) {
      const enabled = r.enabled !== false;
      const from = r.match?.from || '';
      const subject = r.match?.subject || '';
      const olderThan = r.match?.older_than;
      const matched = r.stats?.total_matched || 0;
      const actionColor = r.action === 'delete' ? 'var(--status-error)' : 'var(--status-success)';
      const ageBadge = olderThan ? `<span style="color:var(--accent-primary);font-size:var(--font-size-xs)">after ${olderThan}d</span>` : '';

      html += `
        <div style="display:flex;align-items:center;gap:var(--space-sm);padding:var(--space-xs) var(--space-sm);border-bottom:1px solid var(--border-secondary);${enabled ? '' : 'opacity:0.5'}">
          <div style="flex:1;min-width:0">
            <span style="color:var(--text-primary)">${this._esc(r.name || from)}</span>
            ${from ? `<span style="color:var(--text-tertiary);font-size:var(--font-size-xs)"> ${this._esc(from)}</span>` : ''}
            ${subject ? `<span style="color:var(--text-tertiary);font-size:var(--font-size-xs)"> subj: ${this._esc(subject)}</span>` : ''}
            ${ageBadge}
          </div>
          <span style="color:${actionColor};font-size:var(--font-size-xs);font-weight:600">${r.action || 'archive'}</span>
          <span style="color:var(--text-tertiary);font-size:var(--font-size-xs)">${matched}x</span>
          <button class="btn btn-sm" style="color:var(--status-error);padding:2px 6px" onclick="Mail.deleteRule('${r.id}')">x</button>
        </div>`;
    }
    return html;
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

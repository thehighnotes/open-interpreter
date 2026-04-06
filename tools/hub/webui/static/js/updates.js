/* OI WebUI — Updates tab: clustered scan/review/apply for system & package updates */

const Updates = {
  _data: null,
  _approvals: {},
  _filter: { host: null, dimension: null, text: '', analyzed: false, hideDeployed: true },
  _expandedClusters: {},
  _selectedClusters: {},
  _analyzeTimer: null,

  _esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  },

  // ── Load & Render ───────────────────────────────────────────────────────

  async load() {
    const container = document.getElementById('updates-content');
    container.innerHTML = '<div class="tab-loading"><div class="spinner"></div>Loading updates...</div>';
    try {
      const res = await fetch('/api/updates');
      const data = await res.json();
      this._data = data;
      this._approvals = {};
      this._render();
    } catch (e) {
      container.innerHTML = `<div class="text-muted">Failed to load: ${this._esc(e.message)}</div>`;
    }
  },

  _render() {
    const container = document.getElementById('updates-content');
    if (!this._data) { container.innerHTML = '<div class="text-muted">No data</div>'; return; }
    const d = this._data;
    const clusters = d.clusters || [];
    const counts = d.counts || {};
    const results = d.scan_results || [];

    let html = '';

    // ── Dashboard bar ──────────────────────────────────────────────────
    const lastScan = d.scan_ts ? new Date(d.scan_ts * 1000).toLocaleString() : 'Never';
    const llmBadge = d.llm_available
      ? '<span class="badge badge-success">LLM</span>'
      : '<span class="badge badge-warning">No LLM</span>';

    const hubCount = clusters.filter(c => c.tier === 'hub').length;
    const secCount = clusters.filter(c => c.is_security).length;
    const analyzedCount = clusters.filter(c => c.analysis).length;

    html += `<div style="display:flex;gap:var(--space-lg);margin-bottom:var(--space-md);flex-wrap:wrap;align-items:center">
      <div style="color:var(--text-tertiary)">Items: <strong style="color:var(--text-primary)">${counts.total || 0}</strong></div>
      <div style="color:var(--text-tertiary)">Clusters: <strong style="color:var(--text-primary)">${clusters.length}</strong></div>
      <div style="color:var(--status-error)">Blocked: <strong>${counts.blocked || 0}</strong></div>
      <div style="color:var(--text-tertiary)">Analyzed: <strong style="color:var(--accent-primary)">${analyzedCount}/${clusters.length}</strong></div>
      <div style="color:var(--text-tertiary)">Last scan: <strong style="color:var(--text-primary)">${lastScan}</strong></div>
      ${llmBadge}
    </div>`;

    // ── Filter bar ─────────────────────────────────────────────────────
    if (clusters.length > 0) {
      const f = this._filter;
      const hosts = d.hosts || {};
      const filterBtn = (label, key, value) => {
        const active = f[key] === value ? 'badge-info' : '';
        return `<span class="badge ${active}" style="cursor:pointer" onclick="Updates.setFilter('${key}',${value === null ? 'null' : `'${value}'`})">${label}</span>`;
      };
      const dims = [...new Set(clusters.map(c => c.dimension).filter(Boolean))].sort();
      const analyzedActive = f.analyzed ? 'badge-info' : '';
      html += `<div style="display:flex;gap:var(--space-xs);margin-bottom:var(--space-md);flex-wrap:wrap;align-items:center">
        <input type="text" class="input" placeholder="Search clusters..." value="${this._esc(f.text || '')}"
          style="width:160px;font-size:var(--font-size-xs);padding:2px 8px;height:24px"
          oninput="Updates.setFilter('text', this.value)">
        <span style="color:var(--border-secondary);margin:0 2px">|</span>
        ${filterBtn('All Hosts', 'host', null)}
        ${Object.entries(hosts).map(([k, name]) => filterBtn(name, 'host', k)).join('')}
        <span style="color:var(--border-secondary);margin:0 2px">|</span>
        ${filterBtn('All Types', 'dimension', null)}
        ${dims.map(d => filterBtn(d, 'dimension', d)).join('')}
        <span style="color:var(--border-secondary);margin:0 2px">|</span>
        <span class="badge ${analyzedActive}" style="cursor:pointer" onclick="Updates.toggleAnalyzed()">Analyzed</span>
        <span class="badge ${f.hideDeployed ? 'badge-info' : ''}" style="cursor:pointer" onclick="Updates.toggleHideDeployed()">Hide Deployed</span>
      </div>`;
    }

    // ── Empty state ────────────────────────────────────────────────────
    if (!clusters.length) {
      html += `<div class="text-muted" style="padding:var(--space-lg);text-align:center;">
        No scan results yet. Click <strong>Scan</strong> to check for updates across all hosts.
      </div>`;
      container.innerHTML = html;
      return;
    }

    // ── Insights ───────────────────────────────────────────────────────
    const insights = d.insights || {};
    if (insights.advice) {
      const rendered = typeof marked !== 'undefined' ? marked.parse(insights.advice) : `<pre>${this._esc(insights.advice)}</pre>`;
      html += this._foldable('Insights', `
        <div class="msg-bubble markdown-content" style="margin-bottom:var(--space-sm)">${rendered}</div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span class="text-xs text-muted">${insights.ts ? new Date(insights.ts * 1000).toLocaleString() : ''}</span>
          <button class="btn btn-sm" onclick="Updates.refreshInsights()">Refresh</button>
        </div>
      `, { open: false });
    }

    // ── Selection action bar ──────────────────────────────────────────
    const selectedCount = Object.values(this._selectedClusters).filter(Boolean).length;
    const analyzedClusters = clusters.filter(c => c.analysis && !c.analysis.error);
    if (analyzedClusters.length > 0) {
      html += `<div style="display:flex;gap:var(--space-xs);margin-bottom:var(--space-sm);align-items:center;flex-wrap:wrap">
        <button class="btn btn-sm" onclick="Updates.selectAllAnalyzed()">Select All Analyzed</button>
        ${selectedCount > 0 ? `
          <button class="btn btn-sm" onclick="Updates.clearSelection()">Clear (${selectedCount})</button>
          <button class="btn btn-sm" style="background:var(--accent-primary);color:#fff" onclick="Updates.deploySelected()">Deploy ${selectedCount} Selected</button>
        ` : ''}
      </div>`;
    }

    // ── Render clusters by tier ────────────────────────────────────────
    const filtered = this._filterClusters(clusters);

    // Hub clusters (Core Dependencies)
    const hubs = filtered.filter(c => c.tier === 'hub');
    if (hubs.length) {
      html += '<div style="margin-bottom:var(--space-xs);color:var(--text-tertiary);font-size:var(--font-size-xs);text-transform:uppercase;letter-spacing:0.05em;margin-top:var(--space-md)">Core Dependencies</div>';
      for (const c of hubs) html += this._renderCluster(c, results);
    }

    // Security clusters
    const security = filtered.filter(c => c.is_security && c.tier !== 'hub');
    if (security.length) {
      html += '<div style="margin-bottom:var(--space-xs);color:var(--text-tertiary);font-size:var(--font-size-xs);text-transform:uppercase;letter-spacing:0.05em;margin-top:var(--space-md)">Security Updates</div>';
      for (const c of security) html += this._renderCluster(c, results);
    }

    // Static clusters (defined, non-security)
    const statics = filtered.filter(c => c.static && !c.is_security && c.tier !== 'hub');
    if (statics.length) {
      html += '<div style="margin-bottom:var(--space-xs);color:var(--text-tertiary);font-size:var(--font-size-xs);text-transform:uppercase;letter-spacing:0.05em;margin-top:var(--space-md)">Domain Clusters</div>';
      for (const c of statics) html += this._renderCluster(c, results);
    }

    // Dynamic clusters
    const dynamics = filtered.filter(c => !c.static && !c.is_security && c.tier !== 'hub' && !c.id.startsWith('other__'));
    if (dynamics.length) {
      html += '<div style="margin-bottom:var(--space-xs);color:var(--text-tertiary);font-size:var(--font-size-xs);text-transform:uppercase;letter-spacing:0.05em;margin-top:var(--space-md)">Detected Clusters</div>';
      for (const c of dynamics) html += this._renderCluster(c, results);
    }

    // Catch-all
    const catchalls = filtered.filter(c => c.id.startsWith('other__'));
    if (catchalls.length) {
      html += '<div style="margin-bottom:var(--space-xs);color:var(--text-tertiary);font-size:var(--font-size-xs);text-transform:uppercase;letter-spacing:0.05em;margin-top:var(--space-md)">Other</div>';
      for (const c of catchalls) html += this._renderCluster(c, results);
    }



    // ── Rules ──────────────────────────────────────────────────────────
    const rules = d.rules || [];
    if (rules.length) {
      html += this._foldable('Rules', this._renderRules(rules), { open: false, count: rules.length });
    }

    // ── Activity ───────────────────────────────────────────────────────
    const actions = d.recent_actions || [];
    if (actions.length) {
      html += this._foldable('Recent Activity', this._renderActivity(actions), { open: false, count: actions.length });
    }

    container.innerHTML = html;

    // Restore search input focus if it was active
    if (this._filter.text) {
      const input = container.querySelector('input[placeholder="Search clusters..."]');
      if (input) { input.focus(); input.selectionStart = input.selectionEnd = input.value.length; }
    }
  },

  // ── Cluster rendering ───────────────────────────────────────────────────

  _renderCluster(cluster, results) {
    const cid = cluster.id;
    const expanded = this._expandedClusters[cid];
    const hostNames = {'nano': 'Nano', 'agx': 'AGX', 'ws': 'WS', 'vps': 'VPS'};
    const hosts = cluster.hosts ? cluster.hosts.map(h => hostNames[h] || h).join(', ') : '';
    const a = (cluster.analysis && typeof cluster.analysis === 'object' && !cluster.analysis.error) ? cluster.analysis : null;

    // ── Traffic light badge ──────────────────────────────────────────
    let trafficColor, trafficLabel;
    if (a) {
      const rec = (a.recommendation || '').toLowerCase();
      trafficColor = rec === 'apply' ? 'var(--status-success)' : rec === 'defer' ? 'var(--status-error)' : 'var(--status-warning)';
      trafficLabel = rec.toUpperCase();
    } else {
      trafficColor = 'var(--text-muted)';
      trafficLabel = '?';
    }

    // ── One-line summary for collapsed state ─────────────────────────
    const riskColor = cluster.risk_score >= 20 ? 'var(--status-error)' : cluster.risk_score >= 5 ? 'var(--status-warning)' : 'var(--text-tertiary)';
    const breakingCount = a && a.breaking_changes ? a.breaking_changes.length : 0;
    const newCount = a && a.new_features ? a.new_features.length : 0;
    let summaryChips = '';
    if (breakingCount) summaryChips += `<span style="color:var(--status-error);font-size:var(--font-size-xs)">${breakingCount} breaking</span>`;
    if (newCount) summaryChips += `<span style="color:var(--status-success);font-size:var(--font-size-xs)">${newCount} new</span>`;

    const deployedBadge = cluster.deployed_at
      ? `<span style="font-size:0.6rem;font-weight:600;color:var(--status-success);background:rgba(50,200,50,0.12);padding:1px 6px;border-radius:3px">DEPLOYED</span>`
      : '';
    const typeBadges = [
      cluster.tier === 'hub' ? '<span class="badge badge-info" style="font-size:0.6rem">HUB</span>' : '',
      cluster.is_security ? '<span class="badge badge-warning" style="font-size:0.6rem">SEC</span>' : '',
      deployedBadge,
    ].filter(Boolean).join('');

    // ── Influenced clusters (hub tier, collapsed) ────────────────────
    let influenceChip = '';
    if (cluster.influenced_clusters && cluster.influenced_clusters.length) {
      influenceChip = `<span class="text-xs text-muted">influences ${cluster.influenced_clusters.length} clusters</span>`;
    }

    // ── Expanded content ─────────────────────────────────────────────
    let expandedHtml = '';
    if (expanded) {
      // Analysis card
      let analysisCard = '';
      if (a) {
        // Recommendation bar
        analysisCard += `<div style="padding:var(--space-sm);border-radius:var(--radius-sm);border:1px solid ${trafficColor};margin-bottom:var(--space-sm)">`;
        analysisCard += `<div style="display:flex;align-items:center;gap:var(--space-sm);margin-bottom:var(--space-xs)">
          <span style="background:${trafficColor};color:var(--bg-primary);padding:2px 8px;border-radius:var(--radius-sm);font-size:var(--font-size-xs);font-weight:700;letter-spacing:0.05em">${trafficLabel}</span>
          ${a.update_order ? `<span class="text-xs text-muted">Order: ${a.update_order.join(' \u2192 ')}</span>` : ''}
        </div>`;

        // Reasoning
        if (a.reasoning) {
          analysisCard += `<div style="color:var(--text-secondary);font-size:var(--font-size-sm);line-height:1.5;margin-bottom:var(--space-sm)">${this._esc(a.reasoning)}</div>`;
        }

        // Breaking changes
        if (a.breaking_changes && a.breaking_changes.length) {
          analysisCard += `<div style="border-left:3px solid var(--status-error);padding:var(--space-xs) var(--space-sm);margin-bottom:var(--space-xs);background:rgba(255,50,50,0.05);border-radius:0 var(--radius-sm) var(--radius-sm) 0">
            <div style="color:var(--status-error);font-weight:600;font-size:var(--font-size-xs);margin-bottom:4px">BREAKING CHANGES</div>
            ${a.breaking_changes.map(c => `<div style="font-size:var(--font-size-sm);color:var(--text-secondary);padding:2px 0">&bull; ${this._esc(c)}</div>`).join('')}
          </div>`;
        }

        // New features
        if (a.new_features && a.new_features.length) {
          analysisCard += `<div style="border-left:3px solid var(--status-success);padding:var(--space-xs) var(--space-sm);margin-bottom:var(--space-xs);background:rgba(50,200,50,0.05);border-radius:0 var(--radius-sm) var(--radius-sm) 0">
            <div style="color:var(--status-success);font-weight:600;font-size:var(--font-size-xs);margin-bottom:4px">NEW FEATURES</div>
            ${a.new_features.map(f => `<div style="font-size:var(--font-size-sm);color:var(--text-secondary);padding:2px 0">&bull; ${this._esc(f)}</div>`).join('')}
          </div>`;
        }

        analysisCard += '</div>';
      }

      // Influenced clusters detail (hub tier)
      let influenceDetail = '';
      if (cluster.influenced_clusters && cluster.influenced_clusters.length) {
        const allClusters = (this._data && this._data.clusters) || [];
        const names = cluster.influenced_clusters
          .map(id => { const c = allClusters.find(x => x.id === id); return c ? c.name : null; })
          .filter(Boolean);
        if (names.length) {
          influenceDetail = `<div class="text-xs text-muted" style="margin-bottom:var(--space-sm)">Influences: ${names.join(', ')}</div>`;
        }
      }

      // Projects
      const projDetail = (cluster.projects || []).length
        ? `<div class="text-xs text-muted" style="margin-bottom:var(--space-sm)">Projects: ${cluster.projects.map(p => this._esc(p.name || p.key)).join(', ')}</div>`
        : '';

      // Items
      const itemMap = {};
      for (const r of results) itemMap[r.id] = r;
      const clusterItems = (cluster.item_ids || []).map(id => itemMap[id]).filter(Boolean);
      clusterItems.sort((a, b) => (b.required_by_count || 0) - (a.required_by_count || 0));
      const itemsHtml = this._renderClusterItems(clusterItems, cid);

      // Q&A input
      const qaHtml = `<div style="margin-top:var(--space-sm);padding-top:var(--space-sm);border-top:1px solid var(--border-primary)">
        <div style="display:flex;gap:var(--space-xs);align-items:center">
          <input type="text" class="input" id="qa-input-${this._esc(cid)}" placeholder="Ask about this cluster..." style="flex:1;font-size:var(--font-size-sm)" onkeydown="if(event.key==='Enter')Updates.askCluster('${this._esc(cid)}')">
          <button class="btn btn-sm" onclick="Updates.askCluster('${this._esc(cid)}')">Ask</button>
          <button class="btn btn-sm" onclick="Updates.deployCluster('${this._esc(cid)}')" title="Deploy script to host">Deploy</button>
        </div>
        <div id="qa-result-${this._esc(cid)}"></div>
        <div id="deploy-result-${this._esc(cid)}"></div>
      </div>`;

      expandedHtml = `<div style="padding:var(--space-sm);border-top:1px solid var(--border-secondary)">
        ${projDetail}${influenceDetail}${analysisCard}${itemsHtml}${qaHtml}
      </div>`;
    }

    // ── Border color by type ─────────────────────────────────────────
    const borderColor = cluster.tier === 'hub' ? 'var(--accent-primary)' : cluster.is_security ? 'var(--status-warning)' : cluster.static ? 'var(--border-secondary)' : 'transparent';

    // ── Assemble ─────────────────────────────────────────────────────
    const isSelected = this._selectedClusters[cid];
    const selectedBorder = isSelected ? 'outline:2px solid var(--accent-primary);outline-offset:-2px;' : '';
    const checkbox = a ? `<input type="checkbox" ${isSelected ? 'checked' : ''} onclick="event.stopPropagation();Updates.toggleSelect('${this._esc(cid)}')" style="flex-shrink:0;cursor:pointer;accent-color:var(--accent-primary)">` : '';

    return `<div style="margin-bottom:var(--space-xs);border:1px solid var(--border-secondary);border-left:3px solid ${borderColor};border-radius:var(--radius-md);background:var(--bg-primary);${selectedBorder}">
      <div style="display:flex;align-items:center;gap:var(--space-sm);padding:var(--space-sm) var(--space-sm);cursor:pointer;user-select:none" onclick="Updates.toggleCluster('${this._esc(cid)}')">
        ${checkbox}
        <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${trafficColor};flex-shrink:0" title="${trafficLabel}"></span>
        <span style="color:var(--text-tertiary);font-size:0.7em;flex-shrink:0">${expanded ? '\u25BC' : '\u25B6'}</span>
        <div style="flex:1;min-width:0;display:flex;align-items:center;gap:var(--space-xs);flex-wrap:wrap">
          <strong style="color:var(--text-primary);font-size:var(--font-size-sm)">${this._esc(cluster.name)}</strong>
          ${typeBadges}
          ${hosts ? `<span style="font-size:0.65rem;font-weight:600;color:var(--accent-primary);background:rgba(100,149,237,0.12);padding:1px 6px;border-radius:3px">${hosts}</span>` : ''}
          <span style="font-size:var(--font-size-xs);color:${riskColor};font-weight:600">${cluster.risk_score}</span>
          <span class="text-xs text-muted">${cluster.item_count} pkg${cluster.item_count !== 1 ? 's' : ''}</span>
          ${summaryChips}
          ${influenceChip}
        </div>
        <div style="display:flex;gap:var(--space-xs);flex-shrink:0" onclick="event.stopPropagation()">
          <button class="btn btn-sm" onclick="Updates.analyzeCluster('${this._esc(cid)}')" title="${a ? 'Re-analyze' : 'Analyze'}">${a ? 'Re-analyze' : 'Analyze'}</button>
          <button class="btn btn-sm" onclick="Updates.deployCluster('${this._esc(cid)}')" title="Deploy update script to host">Deploy</button>
        </div>
      </div>
      ${expandedHtml}
    </div>`;
  },

  _renderClusterItems(items, clusterId) {
    if (!items.length) return '';
    const hosts = (this._data && this._data.hosts) || {};
    let html = '<div style="margin-top:var(--space-sm)">';
    for (const item of items) {
      const id = item.id;
      const current = this._approvals[id] || (item.approved === true ? 'approve' : item.approved === false ? 'skip' : '');
      const rb = item.required_by_count || 0;
      const rbStr = rb > 0 ? `<span class="text-xs" style="color:var(--status-warning)">${rb} deps</span>` : '';
      const clsColor = item.classification === 'urgent' ? 'var(--status-error)' : item.classification === 'review' ? 'var(--status-warning)' : item.classification === 'noise' ? 'var(--text-muted)' : 'var(--text-tertiary)';
      const clsBadge = item.classification ? `<span style="color:${clsColor};font-size:var(--font-size-xs);font-weight:600">${item.classification}</span>` : '';
      const srcBadge = item.source === 'llm' ? '<span style="color:var(--accent-primary);font-size:var(--font-size-xs)">LLM</span>' : '';
      const rbNames = (item.required_by || []).slice(0, 4);
      const rbDetail = rbNames.length ? `<div class="text-xs text-muted">needed by: ${rbNames.join(', ')}${item.required_by.length > 4 ? '...' : ''}</div>` : '';

      // Intel badges
      let intelHtml = '';
      if (item.intel) {
        const sv = item.intel.semver_change;
        if (sv && sv !== 'unknown') {
          const svColor = sv === 'major' ? 'var(--status-error)' : sv === 'minor' ? 'var(--status-warning)' : 'var(--text-muted)';
          intelHtml += `<span class="badge" style="font-size:0.6rem;color:${svColor}">${sv}</span>`;
        }
        if (item.intel.releases_skipped) {
          intelHtml += `<span class="text-xs text-muted">${item.intel.releases_skipped} releases behind</span>`;
        }
        if (item.intel.cves && item.intel.cves.length) {
          intelHtml += `<span class="badge badge-warning" style="font-size:0.6rem">${item.intel.cves.length} CVE${item.intel.cves.length > 1 ? 's' : ''}</span>`;
        }
        if (item.intel.repo) {
          intelHtml += `<a href="https://github.com/${this._esc(item.intel.repo)}/releases" target="_blank" class="text-xs" style="color:var(--accent-primary);text-decoration:none" title="View releases">changelog</a>`;
        }
      }

      html += `<div style="display:flex;align-items:flex-start;gap:var(--space-sm);padding:var(--space-xs) 0;border-bottom:1px solid var(--border-primary)">
        <select class="input update-action-select" data-id="${this._esc(id)}" style="width:80px;flex-shrink:0;font-size:var(--font-size-xs)" onchange="Updates.onSelect('${this._esc(id)}', this.value)">
          <option value="" ${!current ? 'selected' : ''}>Include</option>
          <option value="skip" ${current === 'skip' ? 'selected' : ''}>Exclude</option>
        </select>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:var(--space-xs);flex-wrap:wrap">
            <strong style="color:var(--text-primary);font-size:var(--font-size-sm)">${this._esc(item.package)}</strong>
            <span class="text-xs text-muted">${this._esc(item.current)} &rarr; ${this._esc(item.available)}</span>
            ${rbStr}${clsBadge}${srcBadge}${intelHtml}
            ${item.project ? `<span class="text-xs" style="color:var(--accent-secondary)">${this._esc(item.project)}</span>` : ''}
          </div>
          ${rbDetail}
          ${item.reason ? `<div class="text-xs text-muted">${this._esc(item.reason)}</div>` : ''}
        </div>
      </div>`;
    }
    html += `<div style="display:flex;gap:var(--space-sm);margin-top:var(--space-sm)">
      <button class="btn btn-sm" onclick="Updates.includeAllInCluster('${this._esc(clusterId)}')">Include All</button>
      <button class="btn btn-sm" onclick="Updates.excludeAllInCluster('${this._esc(clusterId)}')">Exclude All</button>
      <button class="btn btn-sm" onclick="Updates.enrichCluster('${this._esc(clusterId)}')">Load Intelligence</button>
    </div></div>`;
    return html;
  },

  // ── Foldable ────────────────────────────────────────────────────────────

  _foldable(title, body, opts = {}) {
    const open = opts.open ? ' open' : '';
    const badge = opts.count != null ? ` <span class="badge badge-info">${opts.count}</span>` : '';
    return `<details class="settings-section" style="margin-bottom:var(--space-sm)"${open}>
      <summary><div class="settings-section-title">${title}${badge}</div></summary>
      <div style="padding:var(--space-sm)">${body}</div>
    </details>`;
  },

  // ── Cluster expand/collapse ─────────────────────────────────────────────

  toggleCluster(cid) {
    this._expandedClusters[cid] = !this._expandedClusters[cid];
    this._render();
  },

  // ── Selection ──────────────────────────────────────────────────────────

  toggleSelect(cid) {
    this._selectedClusters[cid] = !this._selectedClusters[cid];
    this._render();
  },

  selectAllAnalyzed() {
    const clusters = (this._data && this._data.clusters) || [];
    const filtered = this._filterClusters(clusters);
    for (const c of filtered) {
      if (c.analysis && !c.analysis.error && !c.deployed_at) {
        this._selectedClusters[c.id] = true;
      }
    }
    this._render();
  },

  clearSelection() {
    this._selectedClusters = {};
    this._render();
  },

  async deploySelected() {
    const ids = Object.entries(this._selectedClusters).filter(([_, v]) => v).map(([k]) => k);
    if (!ids.length) return;
    if (!confirm(`Deploy ${ids.length} cluster(s) to host? This generates update scripts via SSH.`)) return;

    this._showBanner(`Deploying ${ids.length} clusters...`);
    try {
      const res = await fetch('/api/updates/deploy/bulk', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ cluster_ids: ids }),
      });
      const data = await res.json();
      this._hideBanner();
      if (data.ok) {
        const items = data.deployed || [];
        const ok = items.filter(d => !d.error);
        const paths = ok.map(d => `${d.host_name}: ${d.path} (${d.items} items)`).join('\n');
        App.toast(`Deployed to ${ok.length} host(s)`, 'success');
        if (paths) alert(`Scripts deployed:\n\n${paths}`);
        this._selectedClusters = {};
        this.load();
      } else {
        App.toast(data.error || 'Deploy failed', 'error');
      }
    } catch (e) {
      this._hideBanner();
      App.toast('Deploy failed: ' + e.message, 'error');
    }
  },

  // ── Filtering ───────────────────────────────────────────────────────────

  setFilter(key, value) {
    if (key === 'text') {
      this._filter.text = value;
      clearTimeout(this._textTimer);
      this._textTimer = setTimeout(() => this._render(), 200);
      return;
    }
    if (this._filter[key] === value) value = null;
    this._filter[key] = value;
    this._render();
  },

  toggleAnalyzed() {
    this._filter.analyzed = !this._filter.analyzed;
    this._render();
  },

  toggleHideDeployed() {
    this._filter.hideDeployed = !this._filter.hideDeployed;
    this._render();
  },

  _filterClusters(clusters) {
    const f = this._filter;
    const q = (f.text || '').toLowerCase();
    const results = (this._data && this._data.scan_results) || [];
    const itemMap = {};
    if (q) for (const r of results) itemMap[r.id] = r;

    return clusters.filter(c => {
      if (f.host && !(c.hosts || []).includes(f.host)) return false;
      if (f.dimension && c.dimension !== f.dimension && c.dimension !== 'mixed') return false;
      if (f.analyzed && !c.analysis) return false;
      if (f.hideDeployed && c.deployed_at) return false;
      if (q) {
        // Match cluster name, host names, or any package name in the cluster
        const nameMatch = (c.name || '').toLowerCase().includes(q);
        const hostMatch = (c.hosts || []).some(h => h.includes(q));
        const dimMatch = (c.dimension || '').includes(q);
        const pkgMatch = (c.item_ids || []).some(id => {
          const item = itemMap[id];
          return item && (item.package || '').toLowerCase().includes(q);
        });
        if (!nameMatch && !hostMatch && !dimMatch && !pkgMatch) return false;
      }
      return true;
    });
  },

  // ── Activity banner ──────────────────────────────────────────────────

  _showBanner(msg) {
    this._hideBanner();
    const container = document.getElementById('updates-content');
    if (!container) return;
    const banner = document.createElement('div');
    banner.id = 'updates-banner';
    banner.style.cssText = 'position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:var(--space-sm);padding:var(--space-sm);margin-bottom:var(--space-sm);background:var(--bg-secondary);border:1px solid var(--accent-primary);border-radius:var(--radius-md);font-size:var(--font-size-sm)';
    banner.innerHTML = `<div class="spinner" style="width:16px;height:16px;flex-shrink:0"></div><span id="updates-banner-text">${msg}</span>`;
    container.prepend(banner);
  },

  _updateBanner(msg) {
    const el = document.getElementById('updates-banner-text');
    if (el) el.innerHTML = msg;
  },

  _hideBanner() {
    const el = document.getElementById('updates-banner');
    if (el) el.remove();
  },

  // ── Scan ────────────────────────────────────────────────────────────────

  async scan(options) {
    this._showBanner('Scanning all hosts for updates... <span class="text-xs text-muted">(~50s)</span>');
    try {
      const res = await fetch('/api/updates/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(options || {}),
      });
      const data = await res.json();
      this._hideBanner();
      if (data.ok) {
        App.toast(`Scan complete: ${data.scanned} items in ${data.cluster_count} clusters`, 'success');
        Tabs.loaded.updates = false;
        this.load();
      } else {
        App.toast(data.error || 'Scan failed', 'error');
      }
    } catch (e) {
      this._hideBanner();
      App.toast('Scan failed: ' + e.message, 'error');
    }
  },

  // ── Analysis ────────────────────────────────────────────────────────────

  async analyze(clusterIds) {
    this._showBanner('Running LLM analysis on clusters... <span class="text-xs text-muted">0 done</span>');
    try {
      const body = clusterIds ? { cluster_ids: clusterIds } : {};
      const res = await fetch('/api/updates/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (data.ok) {
        this._pollAnalysis(data.clusters);
      } else {
        this._hideBanner();
        App.toast(data.error || 'Analysis failed', 'error');
      }
    } catch (e) {
      this._hideBanner();
      App.toast('Analysis failed: ' + e.message, 'error');
    }
  },

  async analyzeCluster(cid) {
    this.analyze([cid]);
  },

  _pollAnalysis(total) {
    if (this._analyzeTimer) clearInterval(this._analyzeTimer);
    this._analyzeTimer = setInterval(async () => {
      try {
        const res = await fetch('/api/updates/analyze/status');
        const data = await res.json();
        this._updateBanner(`Running LLM analysis... <span class="text-xs text-muted">${data.done}/${data.total} clusters done</span>`);
        if (data.complete) {
          clearInterval(this._analyzeTimer);
          this._analyzeTimer = null;
          this._hideBanner();
          App.toast(`Analysis complete: ${data.done} clusters analyzed`, 'success');
          Tabs.loaded.updates = false;
          this.load();
        } else if (data.done > 0) {
          Tabs.loaded.updates = false;
          this.load();
        }
      } catch (e) {
        clearInterval(this._analyzeTimer);
        this._analyzeTimer = null;
        this._hideBanner();
      }
    }, 5000);
  },

  // ── Intelligence ────────────────────────────────────────────────────────

  async enrichCluster(cid) {
    App.toast('Loading intelligence...', 'info');
    try {
      const res = await fetch('/api/updates/enrich', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cluster_id: cid }),
      });
      const data = await res.json();
      if (data.ok) {
        App.toast(`Enriched ${data.enriched} items`, 'success');
        Tabs.loaded.updates = false;
        this.load();
      } else {
        App.toast(data.error || 'Enrich failed', 'error');
      }
    } catch (e) {
      App.toast('Enrich failed: ' + e.message, 'error');
    }
  },

  // ── Deploy & Q&A ────────────────────────────────────────────────────────

  async deployCluster(cid) {
    const resultEl = document.getElementById(`deploy-result-${cid}`);
    if (resultEl) resultEl.innerHTML = '<div class="text-xs text-muted" style="padding:var(--space-xs)">Deploying script to host...</div>';
    try {
      const res = await fetch('/api/updates/deploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cluster_id: cid, excluded: this._getExcluded(cid) }),
      });
      const data = await res.json();
      if (data.ok && resultEl) {
        const items = data.deployed || [];
        let html = '';
        for (const d of items) {
          if (d.error) {
            html += `<div style="padding:var(--space-xs);color:var(--status-error);font-size:var(--font-size-sm)">${this._esc(d.host_name)}: ${this._esc(d.error)}</div>`;
          } else {
            html += `<div style="padding:var(--space-xs);font-size:var(--font-size-sm)">
              <span style="color:var(--status-success)">&#10003;</span>
              <strong>${this._esc(d.host_name)}</strong>:
              <code style="color:var(--accent-primary);font-size:var(--font-size-xs)">${this._esc(d.path)}</code>
              <span class="text-xs text-muted">(${d.items} items, ${d.commands} commands)</span>
            </div>`;
          }
        }
        resultEl.innerHTML = html;
        App.toast(`Script deployed to ${items.filter(d => !d.error).length} host(s)`, 'success');
      } else if (resultEl) {
        resultEl.innerHTML = `<div class="text-xs" style="color:var(--status-error);padding:var(--space-xs)">${this._esc(data.error || 'Deploy failed')}</div>`;
      }
    } catch (e) {
      if (resultEl) resultEl.innerHTML = `<div class="text-xs" style="color:var(--status-error);padding:var(--space-xs)">Failed: ${this._esc(e.message)}</div>`;
    }
  },

  async askCluster(cid) {
    const input = document.getElementById(`qa-input-${cid}`);
    const resultEl = document.getElementById(`qa-result-${cid}`);
    if (!input || !input.value.trim()) return;
    const question = input.value.trim();
    if (resultEl) resultEl.innerHTML = '<div class="text-xs text-muted" style="padding:var(--space-xs)">Thinking...</div>';
    try {
      const res = await fetch('/api/updates/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cluster_id: cid, question }),
      });
      const data = await res.json();
      if (data.ok && resultEl) {
        const rendered = typeof marked !== 'undefined' ? marked.parse(data.answer) : `<pre>${this._esc(data.answer)}</pre>`;
        resultEl.innerHTML = `<div style="margin-top:var(--space-xs);padding:var(--space-sm);background:var(--bg-secondary);border-radius:var(--radius-sm);border-left:3px solid var(--accent-primary)">
          <div class="text-xs text-muted" style="margin-bottom:4px">Q: ${this._esc(question)}</div>
          <div class="msg-bubble markdown-content" style="font-size:var(--font-size-sm)">${rendered}</div>
        </div>`;
      } else if (resultEl) {
        resultEl.innerHTML = `<div class="text-xs" style="color:var(--status-error);padding:var(--space-xs)">${this._esc(data.error || 'No answer')}</div>`;
      }
    } catch (e) {
      if (resultEl) resultEl.innerHTML = `<div class="text-xs" style="color:var(--status-error);padding:var(--space-xs)">Failed: ${this._esc(e.message)}</div>`;
    }
    input.value = '';
  },

  // ── Item selection (Include/Exclude for deploy) ─────────────────────────

  onSelect(id, value) {
    if (value === 'skip') this._approvals[id] = 'skip';
    else delete this._approvals[id];  // default = included
  },

  _getExcluded(cid) {
    // Get item IDs explicitly excluded for this cluster
    const clusters = (this._data && this._data.clusters) || [];
    const cluster = clusters.find(c => c.id === cid);
    if (!cluster) return [];
    return (cluster.item_ids || []).filter(id => this._approvals[id] === 'skip');
  },

  includeAllInCluster(cid) {
    const clusters = (this._data && this._data.clusters) || [];
    const cluster = clusters.find(c => c.id === cid);
    if (!cluster) return;
    for (const id of (cluster.item_ids || [])) {
      delete this._approvals[id];
    }
    document.querySelectorAll('.update-action-select').forEach(sel => {
      if ((cluster.item_ids || []).includes(sel.dataset.id)) sel.value = '';
    });
    App.toast(`All ${cluster.item_count} items included`, 'info');
  },

  excludeAllInCluster(cid) {
    const clusters = (this._data && this._data.clusters) || [];
    const cluster = clusters.find(c => c.id === cid);
    if (!cluster) return;
    for (const id of (cluster.item_ids || [])) {
      this._approvals[id] = 'skip';
    }
    document.querySelectorAll('.update-action-select').forEach(sel => {
      if ((cluster.item_ids || []).includes(sel.dataset.id)) sel.value = 'skip';
    });
    App.toast(`All ${cluster.item_count} items excluded`, 'info');
  },

  // ── Rules ───────────────────────────────────────────────────────────────

  _renderRules(rules) {
    return rules.map(r => {
      const match = r.match || {};
      const parts = [];
      if (match.dimension) parts.push(match.dimension);
      if (match.host) parts.push(match.host);
      if (match.package_pattern) parts.push(match.package_pattern);
      const matchStr = parts.join(' / ') || 'any';
      const actionColor = r.action === 'approve' ? 'var(--status-success)' : 'var(--status-error)';
      return `<div style="display:flex;align-items:center;gap:var(--space-sm);padding:var(--space-xs) var(--space-sm);border-bottom:1px solid var(--border-secondary);${r.enabled ? '' : 'opacity:0.5'}">
        <div style="flex:1"><span>${this._esc(r.name || 'Unnamed')}</span> <span class="text-xs text-muted">${this._esc(matchStr)}</span></div>
        <span style="color:${actionColor};font-size:var(--font-size-xs);font-weight:600">${r.action}</span>
        <span class="text-xs text-muted">${r.stats?.total_matched || 0}x</span>
        <button class="btn btn-sm" style="color:var(--status-error);padding:2px 6px" onclick="Updates.deleteRule('${r.id}')">x</button>
      </div>`;
    }).join('');
  },

  async deleteRule(ruleId) {
    try {
      const res = await fetch('/api/updates/rules/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: ruleId }),
      });
      const data = await res.json();
      if (data.ok) { App.toast('Rule deleted', 'success'); Tabs.loaded.updates = false; this.load(); }
    } catch (e) { App.toast('Delete failed: ' + e.message, 'error'); }
  },

  // ── Activity ────────────────────────────────────────────────────────────

  _renderActivity(actions) {
    return actions.map(a => {
      const ts = a.ts ? new Date(a.ts * 1000).toLocaleString() : '';
      const color = a.action === 'approve' ? 'var(--status-success)' : 'var(--status-error)';
      return `<div style="padding:4px 0;border-bottom:1px solid var(--border-secondary);font-family:var(--font-mono);font-size:var(--font-size-sm)">
        <span class="text-xs text-muted">${ts}</span>
        <strong style="color:${color}">${a.action === 'approve' ? 'APPROVED' : 'SKIPPED'}</strong>
        <span style="color:var(--accent-primary);font-size:var(--font-size-xs)">${this._esc(a.dimension)}</span>
        <span class="text-muted">${this._esc(a.host)}</span>
        <span>${this._esc(a.package)}</span>
      </div>`;
    }).join('');
  },

  // ── Insights ────────────────────────────────────────────────────────────

  async refreshInsights() {
    App.toast('Running cross-reference analysis...', 'info');
    try {
      const res = await fetch('/api/updates/insights/refresh', { method: 'POST' });
      const data = await res.json();
      if (data.ok) { App.toast('Analysis complete', 'success'); Tabs.loaded.updates = false; this.load(); }
      else App.toast(data.error || 'Failed', 'error');
    } catch (e) { App.toast('Failed: ' + e.message, 'error'); }
  },
};

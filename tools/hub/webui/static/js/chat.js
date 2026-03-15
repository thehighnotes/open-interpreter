/* OI WebUI — Chat: SSE streaming, message rendering, approval UI */

const Chat = {
  streaming: false,
  abortController: null,
  pendingApproval: false,
  imageFile: null,
  _acIndex: -1,

  // Magic command definitions for autocomplete
  _magicCommands: [
    { cmd: '%status', desc: 'Hub status dashboard' },
    { cmd: '%next', desc: 'AI-suggested next tasks' },
    { cmd: '%projects', desc: 'List registered projects' },
    { cmd: '%repo', desc: 'Git dashboard' },
    { cmd: '%services', desc: 'Service status' },
    { cmd: '%research', desc: 'Research digest' },
    { cmd: '%overview', desc: 'AI project overview' },
    { cmd: '%notify', desc: 'Unread notifications' },
    { cmd: '%health', desc: 'Health probe results' },
    { cmd: '%backup', desc: 'Run backup' },
    { cmd: '%vllm', args: '[status|start|stop|restart]', desc: 'vLLM server management' },
    { cmd: '%prepare', args: '<project>', desc: 'Prepare project session' },
    { cmd: '%begin', args: '<project>', desc: 'Build preamble, launch editor' },
    { cmd: '%work', args: '<project>', desc: 'Full session: prepare + overview + begin' },
    { cmd: '%dev', desc: 'Toggle dev services' },
    { cmd: '%reset', desc: 'Clear conversation' },
    { cmd: '%model', args: '[name]', desc: 'Show or switch model' },
  ],

  init() {
    this.textarea = document.getElementById('chat-input');
    this.messagesEl = document.getElementById('chat-messages');
    this.welcomeEl = document.getElementById('welcome-screen');
    this.sendBtn = document.getElementById('send-btn');
    this.stopBtn = document.getElementById('stop-btn');
    this.imageBtn = document.getElementById('image-btn');
    this.imageInput = document.getElementById('image-input');

    // Send on enter (unless autocomplete is open)
    this.textarea.addEventListener('keydown', (e) => {
      if (this._acHandleKey(e)) return;
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.send();
      }
    });

    // Auto-resize + autocomplete
    this.textarea.addEventListener('input', () => {
      this.autoResize();
      this._acUpdate();
    });

    // Close autocomplete on blur (with delay for click)
    this.textarea.addEventListener('blur', () => {
      setTimeout(() => this._acHide(), 150);
    });

    // Send button
    this.sendBtn.addEventListener('click', () => this.send());

    // Stop button
    this.stopBtn.addEventListener('click', () => this.stop());

    // Image upload
    this.imageBtn.addEventListener('click', () => this.imageInput.click());
    this.imageInput.addEventListener('change', (e) => this.handleImageSelect(e));

    // Welcome chips
    document.querySelectorAll('.welcome-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        this.textarea.value = chip.dataset.prompt;
        this.send();
      });
    });
  },

  autoResize() {
    this.textarea.style.height = 'auto';
    this.textarea.style.height = Math.min(this.textarea.scrollHeight, 150) + 'px';
  },

  // ── Magic command autocomplete ──────────────────────────────────────────

  _acGetEl() {
    let el = document.getElementById('magic-autocomplete');
    if (!el) {
      el = document.createElement('div');
      el.id = 'magic-autocomplete';
      el.className = 'magic-autocomplete';
      const box = document.querySelector('.chat-input-box');
      if (box) box.parentElement.appendChild(el);
    }
    return el;
  },

  _acUpdate() {
    const val = this.textarea.value;
    // Only trigger when line starts with % and cursor is on that line
    if (!val.startsWith('%')) { this._acHide(); return; }

    const query = val.split(/\s/)[0].toLowerCase();
    const matches = this._magicCommands.filter(m =>
      m.cmd.startsWith(query)
    );

    if (!matches.length || (matches.length === 1 && matches[0].cmd === query && !val.includes(' '))) {
      // Exact match with no args typed yet — keep showing if it has args
      if (matches.length === 1 && matches[0].args && matches[0].cmd === query) {
        // Show hint only
      } else {
        this._acHide();
        return;
      }
    }

    this._acIndex = -1;
    const el = this._acGetEl();
    el.innerHTML = matches.map((m, i) =>
      `<div class="magic-ac-item" data-index="${i}" onclick="Chat._acSelect(${i})">` +
      `<span class="magic-ac-cmd">${this._acEsc(m.cmd)}${m.args ? ' <span class="magic-ac-args">' + this._acEsc(m.args) + '</span>' : ''}</span>` +
      `<span class="magic-ac-desc">${this._acEsc(m.desc)}</span>` +
      `</div>`
    ).join('');
    el.style.display = 'block';
    el._matches = matches;
  },

  _acEsc(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  },

  _acHide() {
    const el = document.getElementById('magic-autocomplete');
    if (el) el.style.display = 'none';
  },

  _acIsVisible() {
    const el = document.getElementById('magic-autocomplete');
    return el && el.style.display === 'block';
  },

  _acHandleKey(e) {
    if (!this._acIsVisible()) return false;
    const el = document.getElementById('magic-autocomplete');
    const matches = el._matches || [];
    if (!matches.length) return false;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      this._acIndex = Math.min(this._acIndex + 1, matches.length - 1);
      this._acHighlight(el);
      return true;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      this._acIndex = Math.max(this._acIndex - 1, 0);
      this._acHighlight(el);
      return true;
    }
    if (e.key === 'Tab' || (e.key === 'Enter' && this._acIndex >= 0)) {
      e.preventDefault();
      const idx = this._acIndex >= 0 ? this._acIndex : 0;
      this._acSelect(idx);
      return true;
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      this._acHide();
      return true;
    }
    return false;
  },

  _acHighlight(el) {
    el.querySelectorAll('.magic-ac-item').forEach((item, i) => {
      item.classList.toggle('active', i === this._acIndex);
    });
  },

  _acSelect(index) {
    const el = document.getElementById('magic-autocomplete');
    const matches = el?._matches || [];
    if (index < 0 || index >= matches.length) return;
    const m = matches[index];
    // Replace the command portion, keep any existing args after space
    const parts = this.textarea.value.split(/\s+/);
    parts[0] = m.cmd;
    this.textarea.value = parts.join(' ') + (parts.length === 1 && m.args ? ' ' : '');
    this.textarea.focus();
    this._acHide();
  },

  async send() {
    const message = this.textarea.value.trim();
    if (!message || this.streaming) return;

    // Hide welcome, show messages
    if (this.welcomeEl) this.welcomeEl.classList.add('hidden');
    this.messagesEl.classList.remove('hidden');

    // Check for magic command
    if (message.startsWith('%')) {
      this.textarea.value = '';
      this.autoResize();
      await this.handleMagicCommand(message);
      return;
    }

    // Add user message
    this.addMessage('user', message);
    this.textarea.value = '';
    this.autoResize();

    // Stream response
    await this.streamChat(message);
  },

  async handleMagicCommand(cmd) {
    this.addMagicCommand(cmd);
    const loadingEl = this.addMagicLoading(cmd);

    // Measure column width from the magic output body
    const body = loadingEl.querySelector('.magic-output-body');
    let columns = null;
    if (body) {
      const probe = document.createElement('span');
      probe.style.cssText = 'font-family:var(--font-mono);font-size:var(--font-size-sm);visibility:hidden;position:absolute;white-space:pre;';
      probe.textContent = 'X'.repeat(10);
      body.appendChild(probe);
      const charWidth = probe.offsetWidth / 10;
      body.removeChild(probe);
      if (charWidth) {
        const style = getComputedStyle(body);
        const padding = (parseFloat(style.paddingLeft) || 16) + (parseFloat(style.paddingRight) || 16);
        columns = Math.floor((body.clientWidth - padding) / charWidth) - 1;
      }
    }

    try {
      const payload = { command: cmd };
      if (columns) payload.columns = columns;
      const res = await fetch('/api/magic', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      this.replaceMagicLoading(loadingEl, cmd, data.output || 'No output');
    } catch (e) {
      this.replaceMagicLoading(loadingEl, cmd, `Error: ${e.message}`);
    }
  },

  addMagicCommand(cmd) {
    const div = document.createElement('div');
    div.className = 'chat-message user';
    div.innerHTML = `
      <div class="msg-avatar">U</div>
      <div class="msg-body">
        <div class="msg-bubble" style="font-family: var(--font-mono); font-size: var(--font-size-sm);">${this.escapeHtml(cmd)}</div>
      </div>
    `;
    this.messagesEl.appendChild(div);
    this.scrollToBottom();
  },

  addMagicLoading(cmd) {
    const div = document.createElement('div');
    div.className = 'chat-message assistant';
    div.innerHTML = `
      <div class="msg-avatar">OI</div>
      <div class="msg-body">
        <div class="magic-output">
          <div class="magic-output-header">${this.escapeHtml(cmd)}</div>
          <div class="magic-output-body"><div class="typing-indicator"><span></span><span></span><span></span></div></div>
        </div>
      </div>
    `;
    this.messagesEl.appendChild(div);
    this.scrollToBottom();
    return div;
  },

  replaceMagicLoading(el, cmd, output) {
    const body = el.querySelector('.magic-output-body');
    if (body) body.innerHTML = output;
    this.scrollToBottom();
  },

  async streamChat(message) {
    this.streaming = true;
    this.updateInputState();

    this.abortController = new AbortController();

    // Create assistant message container
    const msgDiv = this.createAssistantMessage();
    let currentBubble = null;
    let currentCodeBlock = null;
    let currentOutput = null;
    let textBuffer = '';
    let fullTextBuffer = '';  // accumulates across multiple text_start/text_end pairs

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
        signal: this.abortController.signal,
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop(); // keep incomplete line

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const jsonStr = line.slice(6);
          let event;
          try {
            event = JSON.parse(jsonStr);
          } catch { continue; }

          switch (event.type) {
            case 'text_start':
              // Reuse existing bubble if last child is a text bubble (merges fragmented text blocks)
              if (!currentBubble) {
                const body = msgDiv.querySelector('.msg-body');
                const last = body.lastElementChild;
                if (last && last.classList.contains('msg-bubble') && last.classList.contains('markdown-content')) {
                  currentBubble = last;
                  // Continue from previous text
                  textBuffer = fullTextBuffer;
                } else {
                  currentBubble = this.addBubbleToMsg(msgDiv);
                  textBuffer = '';
                  fullTextBuffer = '';
                }
              }
              break;

            case 'text':
              textBuffer += event.content;
              fullTextBuffer = textBuffer;
              this.renderMarkdown(currentBubble, textBuffer);
              this.scrollToBottom();
              break;

            case 'text_end':
              if (currentBubble && textBuffer) {
                this.renderMarkdown(currentBubble, textBuffer);
                // DEBUG: log final text_end render
                if (currentBubble._lastDebug) {
                  const d = currentBubble._lastDebug;
                  console.log('[MD-FINAL] input (first 500):', JSON.stringify(d.text.slice(0, 500)));
                  console.log('[MD-FINAL] html (first 500):', d.html.slice(0, 500));
                  console.log('[MD-FINAL] <p> count:', currentBubble.querySelectorAll('p').length);
                }
              }
              fullTextBuffer = textBuffer;
              currentBubble = null;
              textBuffer = '';
              break;

            case 'code_start':
              fullTextBuffer = '';  // break text merge across code blocks
              currentCodeBlock = this.addCodeBlockToMsg(msgDiv, event.language || 'bash');
              break;

            case 'code':
              if (currentCodeBlock) {
                const body = currentCodeBlock.querySelector('.code-block-body');
                body.textContent += event.content;
                this.scrollToBottom();
              }
              break;

            case 'code_end':
              if (currentCodeBlock) {
                // Syntax highlight
                const body = currentCodeBlock.querySelector('.code-block-body');
                if (window.hljs) {
                  body.innerHTML = hljs.highlightAuto(body.textContent).value;
                }
              }
              currentCodeBlock = null;
              break;

            case 'confirmation':
              fullTextBuffer = '';  // break text merge across confirmations
              currentCodeBlock = this.addCodeBlockWithApproval(msgDiv, event.language || 'bash', event.code || '');
              this.pendingApproval = true;
              this.scrollToBottom();
              break;

            case 'output_start':
              fullTextBuffer = '';  // break text merge across output blocks
              currentOutput = this.addOutputToMsg(msgDiv);
              break;

            case 'output':
              if (currentOutput) {
                currentOutput._rawBuffer = (currentOutput._rawBuffer || '') + event.content;
                currentOutput.innerHTML = this.ansiToHtml(currentOutput._rawBuffer);
                this.scrollToBottom();
              }
              break;

            case 'output_end':
              if (currentOutput && currentOutput._rawBuffer) {
                currentOutput.innerHTML = this.ansiToHtml(currentOutput._rawBuffer);
                // Auto-expand short output (≤5 lines), keep long output collapsed
                const lines = currentOutput._rawBuffer.split('\n').length;
                const wrap = currentOutput.closest('.console-output-wrap');
                if (wrap) {
                  if (lines <= 5) {
                    wrap.open = true;
                  }
                  // Update summary with line count
                  const summary = wrap.querySelector('.console-output-toggle');
                  if (summary) summary.textContent = `Output (${lines} line${lines !== 1 ? 's' : ''})`;
                }
              }
              currentOutput = null;
              break;

            case 'queue_status':
              this.updateQueueStatus(msgDiv, event.content);
              break;

            case 'error':
              this.addErrorToMsg(msgDiv, event.content);
              break;

            case 'done':
              this.removeQueueStatus(msgDiv);
              if (event.stats) {
                const pt = event.stats.prompt_tokens;
                const cw = event.stats.context_window;
                if (pt > 0 && cw > 0) {
                  const pct = Math.min(Math.round(pt / cw * 100), 100);
                  const fmtK = n => n >= 1000 ? (n < 10000 ? (n/1000).toFixed(1)+'K' : Math.round(n/1000)+'K') : n;
                  const statsEl = document.createElement('div');
                  statsEl.className = 'msg-stats';
                  statsEl.textContent = `ctx ${fmtK(pt)} / ${fmtK(cw)} (${pct}%)`;
                  msgDiv.querySelector('.msg-body').appendChild(statsEl);
                }
              }
              break;
          }
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        this.addErrorToMsg(msgDiv, `Stream error: ${e.message}`);
      }
    }

    this.streaming = false;
    this.pendingApproval = false;
    this.updateInputState();
    this.scrollToBottom();
  },

  createAssistantMessage() {
    const div = document.createElement('div');
    div.className = 'chat-message assistant';
    div.innerHTML = `
      <div class="msg-avatar">OI</div>
      <div class="msg-body"></div>
    `;
    this.messagesEl.appendChild(div);
    return div;
  },

  addBubbleToMsg(msgDiv) {
    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble markdown-content';
    msgDiv.querySelector('.msg-body').appendChild(bubble);
    return bubble;
  },

  updateQueueStatus(msgDiv, info) {
    const body = msgDiv.querySelector('.msg-body');
    let indicator = body.querySelector('.queue-indicator');
    if (!indicator) {
      indicator = document.createElement('details');
      indicator.className = 'queue-indicator';
      indicator.innerHTML = `
        <summary class="queue-summary"><span class="spinner-sm"></span> Queued</summary>
        <div class="queue-details"></div>
      `;
      body.appendChild(indicator);
    }
    indicator.querySelector('.queue-details').textContent = info;
    this.scrollToBottom();
  },

  removeQueueStatus(msgDiv) {
    const indicator = msgDiv.querySelector('.queue-indicator');
    if (indicator) indicator.remove();
  },

  addCodeBlockToMsg(msgDiv, language) {
    const block = document.createElement('div');
    block.className = 'code-block';
    block.innerHTML = `
      <div class="code-block-header">
        <span class="code-block-lang">${this.escapeHtml(language)}</span>
      </div>
      <div class="code-block-body"></div>
    `;
    msgDiv.querySelector('.msg-body').appendChild(block);
    return block;
  },

  addCodeBlockWithApproval(msgDiv, language, code) {
    const block = document.createElement('div');
    block.className = 'code-block';
    const highlighted = window.hljs ? hljs.highlightAuto(code).value : this.escapeHtml(code);
    block.innerHTML = `
      <div class="code-block-header">
        <span class="code-block-lang">${this.escapeHtml(language)}</span>
        <span style="color: var(--status-warning);">Awaiting approval</span>
      </div>
      <div class="code-block-body">${highlighted}</div>
      <div class="approval-bar">
        <button class="btn btn-success btn-sm" onclick="Chat.approve(true, this)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
          Run
        </button>
        <button class="btn btn-danger btn-sm" onclick="Chat.approve(false, this)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          Skip
        </button>
      </div>
    `;
    msgDiv.querySelector('.msg-body').appendChild(block);
    return block;
  },

  addOutputToMsg(msgDiv) {
    const details = document.createElement('details');
    details.className = 'console-output-wrap';
    details.innerHTML = `<summary class="console-output-toggle">Output</summary>`;
    const output = document.createElement('div');
    output.className = 'console-output';
    output._rawBuffer = '';
    details.appendChild(output);
    msgDiv.querySelector('.msg-body').appendChild(details);
    return output;
  },

  // Convert ANSI SGR codes to HTML spans
  ansiToHtml(text) {
    const SGR = {
      '1': 'ansi-bold', '2': 'ansi-dim',
      '30': 'ansi-black', '31': 'ansi-red', '32': 'ansi-green', '33': 'ansi-yellow',
      '34': 'ansi-blue', '35': 'ansi-magenta', '36': 'ansi-cyan', '37': 'ansi-white',
      '90': 'ansi-gray', '91': 'ansi-bright-red', '92': 'ansi-bright-green',
      '93': 'ansi-bright-yellow', '94': 'ansi-bright-blue', '95': 'ansi-bright-magenta',
      '96': 'ansi-bright-cyan', '97': 'ansi-white',
    };
    // Strip non-SGR escapes first
    text = text.replace(/\x1b\[[0-9;]*[A-HJKSTfhln]/g, '');
    text = text.replace(/\x1b\[\?[0-9;]*[a-zA-Z]/g, '');

    const parts = text.split(/\x1b\[([0-9;]*)m/);
    let out = '';
    let open = false;
    for (let i = 0; i < parts.length; i++) {
      if (i % 2 === 0) {
        out += this.escapeHtml(parts[i]);
      } else {
        const codes = parts[i];
        if (!codes || codes === '0' || codes === '00' || codes === '39') {
          if (open) { out += '</span>'; open = false; }
        } else {
          const classes = codes.split(';').map(c => SGR[c]).filter(Boolean);
          if (classes.length) {
            if (open) out += '</span>';
            out += `<span class="${classes.join(' ')}">`;
            open = true;
          }
        }
      }
    }
    if (open) out += '</span>';
    return out;
  },

  addErrorToMsg(msgDiv, text) {
    const err = document.createElement('div');
    err.className = 'msg-bubble';
    err.style.color = 'var(--status-error)';
    err.style.background = '#1a0a0a';
    err.style.borderRadius = 'var(--radius-md)';
    err.textContent = text;
    msgDiv.querySelector('.msg-body').appendChild(err);
  },

  addMessage(role, text) {
    const div = document.createElement('div');
    div.className = `chat-message ${role}`;
    const avatar = role === 'user' ? 'U' : 'OI';
    const content = role === 'user' ? this.escapeHtml(text) : text;
    div.innerHTML = `
      <div class="msg-avatar">${avatar}</div>
      <div class="msg-body">
        <div class="msg-bubble ${role === 'assistant' ? 'markdown-content' : ''}">${content}</div>
      </div>
    `;
    this.messagesEl.appendChild(div);
    this.scrollToBottom();
  },

  async approve(approved, btn) {
    // Remove approval bar
    const bar = btn.closest('.approval-bar');
    if (bar) {
      const statusEl = bar.closest('.code-block').querySelector('.code-block-header span:last-child');
      if (statusEl) {
        statusEl.textContent = approved ? 'Running...' : 'Skipped';
        statusEl.style.color = approved ? 'var(--status-success)' : 'var(--text-tertiary)';
      }
      bar.remove();
    }

    this.pendingApproval = false;
    try {
      await fetch('/api/chat/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved }),
      });
    } catch (e) {
      App.toast('Approval failed: ' + e.message, 'error');
    }
  },

  async stop() {
    if (this.abortController) {
      this.abortController.abort();
    }
    try {
      await fetch('/api/chat/stop', { method: 'POST' });
    } catch {}
    this.streaming = false;
    this.pendingApproval = false;
    this.updateInputState();
  },

  async handleImageSelect(e) {
    const file = e.target.files[0];
    if (!file) return;

    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch('/api/image', { method: 'POST', body: formData });
      const data = await res.json();

      if (data.path) {
        // Prompt user for description
        const prompt = this.textarea.value.trim();
        const msg = prompt ? `%image ${data.path} ${prompt}` : `%image ${data.path}`;
        this.textarea.value = msg;
        App.toast('Image uploaded — press Enter to send', 'success');
      }
    } catch (e) {
      App.toast('Upload failed: ' + e.message, 'error');
    }
    // Reset input
    this.imageInput.value = '';
  },

  restoreMessages(messages) {
    if (!messages || messages.length === 0) return;

    if (this.welcomeEl) this.welcomeEl.classList.add('hidden');
    this.messagesEl.classList.remove('hidden');

    let currentAssistantDiv = null;
    let mergedText = '';
    let currentBubble = null;

    const flushText = () => {
      if (currentBubble && mergedText) {
        this.renderMarkdown(currentBubble, mergedText);
      }
    };

    for (const msg of messages) {
      if (msg.role === 'user' && msg.type === 'message') {
        flushText();
        mergedText = '';
        currentBubble = null;
        this.addMessage('user', msg.content);
        currentAssistantDiv = null;
      } else if (msg.role === 'assistant' && msg.type === 'message') {
        if (!currentAssistantDiv) currentAssistantDiv = this.createAssistantMessage();
        // Merge consecutive text messages into one bubble
        if (!currentBubble) {
          currentBubble = this.addBubbleToMsg(currentAssistantDiv);
          mergedText = msg.content;
        } else {
          mergedText += '\n\n' + msg.content;
        }
        this.renderMarkdown(currentBubble, mergedText);
      } else if (msg.role === 'assistant' && msg.type === 'code') {
        flushText();
        mergedText = '';
        currentBubble = null;
        if (!currentAssistantDiv) currentAssistantDiv = this.createAssistantMessage();
        const block = this.addCodeBlockToMsg(currentAssistantDiv, msg.format || 'bash');
        const body = block.querySelector('.code-block-body');
        body.textContent = msg.content;
        if (window.hljs) body.innerHTML = hljs.highlightAuto(msg.content).value;
      } else if (msg.role === 'computer' && msg.type === 'console') {
        flushText();
        mergedText = '';
        currentBubble = null;
        if (!currentAssistantDiv) currentAssistantDiv = this.createAssistantMessage();
        const output = this.addOutputToMsg(currentAssistantDiv);
        output.innerHTML = this.ansiToHtml(msg.content || '');
        // Show short output expanded, collapse long output
        const lineCount = (msg.content || '').split('\n').length;
        if (lineCount <= 5) output.closest('.console-output-wrap').open = true;
      }
    }
    flushText();
    this.scrollToBottom();
  },

  updateInputState() {
    this.sendBtn.classList.toggle('hidden', this.streaming);
    this.stopBtn.classList.toggle('hidden', !this.streaming);
    this.textarea.disabled = this.streaming || this.pendingApproval;
    if (!this.streaming && !this.pendingApproval) {
      this.textarea.focus();
    }
  },

  renderMarkdown(el, text) {
    if (window.marked) {
      const html = marked.parse(text, { breaks: true, gfm: true });
      el.innerHTML = html;
      // DEBUG: log final render (store on element, print on text_end)
      el._lastDebug = { text, html };
      // Highlight code blocks
      if (window.hljs) {
        el.querySelectorAll('pre code').forEach(block => {
          hljs.highlightElement(block);
        });
      }
    } else {
      el.textContent = text;
    }
  },

  scrollToBottom() {
    requestAnimationFrame(() => {
      this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
    });
  },

  escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },
};

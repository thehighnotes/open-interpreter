/* OI WebUI — Chat: SSE streaming, message rendering, approval UI */

const Chat = {
  streaming: false,
  abortController: null,
  pendingApproval: false,
  imageFile: null,

  init() {
    this.textarea = document.getElementById('chat-input');
    this.messagesEl = document.getElementById('chat-messages');
    this.welcomeEl = document.getElementById('welcome-screen');
    this.sendBtn = document.getElementById('send-btn');
    this.stopBtn = document.getElementById('stop-btn');
    this.imageBtn = document.getElementById('image-btn');
    this.imageInput = document.getElementById('image-input');

    // Send on enter
    this.textarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.send();
      }
    });

    // Auto-resize textarea
    this.textarea.addEventListener('input', () => this.autoResize());

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
              currentBubble = this.addBubbleToMsg(msgDiv);
              textBuffer = '';
              break;

            case 'text':
              textBuffer += event.content;
              this.renderMarkdown(currentBubble, textBuffer);
              this.scrollToBottom();
              break;

            case 'text_end':
              if (currentBubble && textBuffer) {
                this.renderMarkdown(currentBubble, textBuffer);
              }
              currentBubble = null;
              textBuffer = '';
              break;

            case 'code_start':
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
              currentCodeBlock = this.addCodeBlockWithApproval(msgDiv, event.language || 'bash', event.code || '');
              this.pendingApproval = true;
              this.scrollToBottom();
              break;

            case 'output_start':
              currentOutput = this.addOutputToMsg(msgDiv);
              break;

            case 'output':
              if (currentOutput) {
                currentOutput.textContent += event.content;
                this.scrollToBottom();
              }
              break;

            case 'output_end':
              currentOutput = null;
              break;

            case 'error':
              this.addErrorToMsg(msgDiv, event.content);
              break;

            case 'done':
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
    const output = document.createElement('div');
    output.className = 'console-output';
    msgDiv.querySelector('.msg-body').appendChild(output);
    return output;
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
    for (const msg of messages) {
      if (msg.role === 'user' && msg.type === 'message') {
        this.addMessage('user', msg.content);
        currentAssistantDiv = null;
      } else if (msg.role === 'assistant' && msg.type === 'message') {
        currentAssistantDiv = this.createAssistantMessage();
        const bubble = this.addBubbleToMsg(currentAssistantDiv);
        this.renderMarkdown(bubble, msg.content);
      } else if (msg.role === 'assistant' && msg.type === 'code') {
        if (!currentAssistantDiv) currentAssistantDiv = this.createAssistantMessage();
        const block = this.addCodeBlockToMsg(currentAssistantDiv, msg.format || 'bash');
        const body = block.querySelector('.code-block-body');
        body.textContent = msg.content;
        if (window.hljs) body.innerHTML = hljs.highlightAuto(msg.content).value;
      } else if (msg.role === 'computer' && msg.type === 'console') {
        if (!currentAssistantDiv) currentAssistantDiv = this.createAssistantMessage();
        const output = this.addOutputToMsg(currentAssistantDiv);
        output.textContent = msg.content;
      }
    }
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
      el.innerHTML = marked.parse(text, { breaks: true });
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

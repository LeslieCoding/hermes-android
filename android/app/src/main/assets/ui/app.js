// Hermes for Android — mobile shell.
// Talks to the on-device Hermes dashboard over the same JSON-RPC WebSocket the
// official desktop app uses (/api/ws), and embeds the dashboard for settings.
(function () {
  'use strict';

  const $ = (sel, root) => (root || document).querySelector(sel);
  const bridge = window.HermesAndroid || null;
  const MD = window.HermesMarkdown;
  const LAST_SESSION_KEY = 'hermes.android.lastSession';

  // ───────────────────────────── helpers ─────────────────────────────

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function toast(text) {
    if (bridge) bridge.toast(String(text));
    else console.log('[toast]', text);
  }

  function copy(text) {
    if (bridge) { bridge.copyText(text); return; }
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => toast('已复制'));
  }

  function textOf(value) {
    if (value == null) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      return value.map(part => (typeof part === 'string' ? part : (part && (part.text || part.content)) || '')).join('');
    }
    if (typeof value === 'object') return value.text || value.content || JSON.stringify(value);
    return String(value);
  }

  function clip(s, n) {
    s = String(s || '');
    return s.length > n ? s.slice(0, n) + '\n…（已截断）' : s;
  }

  function relTime(sec) {
    if (!sec) return '';
    const d = Date.now() / 1000 - sec;
    if (d < 60) return '刚刚';
    if (d < 3600) return Math.floor(d / 60) + ' 分钟前';
    if (d < 86400) return Math.floor(d / 3600) + ' 小时前';
    if (d < 86400 * 7) return Math.floor(d / 86400) + ' 天前';
    const t = new Date(sec * 1000);
    return (t.getMonth() + 1) + '月' + t.getDate() + '日';
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const url = String(reader.result);
        resolve({ dataUrl: url, base64: url.slice(url.indexOf(',') + 1) });
      };
      reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
      reader.readAsDataURL(file);
    });
  }

  // ───────────────────────────── runtime status ─────────────────────────────

  let status = { phase: 'idle', port: 0, token: '' };

  async function fetchStatus() {
    const res = await fetch('/__android/status.json', { cache: 'no-store' });
    return res.json();
  }

  // ───────────────────────────── gateway client ─────────────────────────────

  class Gateway {
    constructor() {
      this.ws = null;
      this.nextId = 0;
      this.pending = new Map();
      this.eventListeners = new Set();
      this.stateListeners = new Set();
      this.requestHandler = null;
      this.state = 'closed';
      this.pingTimer = null;
    }

    setState(state) {
      this.state = state;
      this.stateListeners.forEach(fn => fn(state));
    }

    connect(url) {
      return new Promise((resolve, reject) => {
        const ws = new WebSocket(url);
        this.ws = ws;
        this.setState('connecting');
        let settled = false;
        const timer = setTimeout(() => {
          if (settled) return;
          settled = true;
          try { ws.close(); } catch (_) { /* ignore */ }
          reject(new Error('连接超时'));
        }, 15000);
        ws.onopen = () => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          this.setState('open');
          resolve();
        };
        ws.onerror = () => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          reject(new Error('无法连接到 Hermes'));
        };
        ws.onclose = ev => {
          if (this.ws !== ws) return;
          this.ws = null;
          this.stopPing();
          for (const call of this.pending.values()) {
            clearTimeout(call.timer);
            call.reject(new Error('与 Hermes 的连接已断开'));
          }
          this.pending.clear();
          if (!settled) {
            settled = true;
            clearTimeout(timer);
            reject(new Error('连接被关闭（' + ev.code + '）'));
          }
          this.setState('closed');
        };
        ws.onmessage = ev => this.onFrame(ev.data);
      });
    }

    close() {
      const ws = this.ws;
      this.ws = null;
      this.stopPing();
      if (ws) { try { ws.close(); } catch (_) { /* ignore */ } }
      this.setState('closed');
    }

    get open() {
      return !!this.ws && this.ws.readyState === 1;
    }

    send(frame) {
      if (!this.open) throw new Error('未连接到 Hermes');
      this.ws.send(JSON.stringify(frame));
    }

    request(method, params, timeoutMs) {
      const timeout = timeoutMs == null ? 120000 : timeoutMs;
      return new Promise((resolve, reject) => {
        if (!this.open) { reject(new Error('未连接到 Hermes')); return; }
        const id = 'm' + (++this.nextId);
        const timer = timeout > 0 ? setTimeout(() => {
          this.pending.delete(id);
          reject(new Error('请求超时：' + method));
        }, timeout) : null;
        this.pending.set(id, { resolve, reject, timer });
        try {
          this.send({ jsonrpc: '2.0', id, method, params: params || {} });
        } catch (e) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(e);
        }
      });
    }

    respond(id, result) {
      try { this.send({ jsonrpc: '2.0', id, result }); } catch (_) { /* socket gone */ }
    }

    fail(id, code, message) {
      try { this.send({ jsonrpc: '2.0', id, error: { code, message } }); } catch (_) { /* socket gone */ }
    }

    onFrame(data) {
      let frame;
      try { frame = JSON.parse(data); } catch (_) { return; }
      if (!frame || typeof frame !== 'object') return;
      if (frame.method === 'event' && frame.params) {
        const ev = frame.params;
        if (ev.type === 'gateway.ready' && ev.payload && ev.payload.heartbeat) this.startPing();
        this.eventListeners.forEach(fn => {
          try { fn(ev); } catch (e) { console.error(e); }
        });
        return;
      }
      if (frame.method && frame.id != null) {
        const req = { id: frame.id, method: frame.method, params: frame.params || {} };
        let handled = false;
        try {
          handled = !!(this.requestHandler && this.requestHandler(req) !== false);
        } catch (e) {
          console.error(e);
          this.fail(frame.id, -32603, String(e && e.message || e));
          return;
        }
        if (!handled) this.fail(frame.id, -32601, 'not supported by the Android client');
        return;
      }
      if (frame.id != null && this.pending.has(frame.id)) {
        const call = this.pending.get(frame.id);
        this.pending.delete(frame.id);
        clearTimeout(call.timer);
        if (frame.error) {
          const err = new Error(frame.error.message || 'Hermes 请求失败');
          err.code = frame.error.code;
          err.data = frame.error.data;
          call.reject(err);
        } else {
          call.resolve(frame.result);
        }
      }
    }

    startPing() {
      this.stopPing();
      this.pingTimer = setInterval(() => {
        try {
          this.send({ jsonrpc: '2.0', id: 'hb' + Date.now(), method: 'gateway.ping', params: {} });
        } catch (_) { /* reconnect logic handles it */ }
      }, 15000);
    }

    stopPing() {
      if (this.pingTimer) clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  const gw = new Gateway();

  // ───────────────────────────── chat state & rendering ─────────────────────────────

  const chat = {
    sid: null,        // live (runtime) session id
    storedId: null,   // stored session id (survives restarts)
    title: '',
    info: null,
    running: false,
    turn: null,
    attachments: [],
  };

  const ui = {
    messages: $('#messages'),
    empty: $('#empty'),
    input: $('#input'),
    send: $('#btn-send'),
    title: $('#title'),
    connDot: $('#conn-dot'),
    connText: $('#conn-text'),
    banner: $('#banner'),
    attachments: $('#attachments'),
    file: $('#file'),
  };

  function nearBottom() {
    const m = ui.messages;
    return m.scrollHeight - m.scrollTop - m.clientHeight < 120;
  }

  function scrollToBottom(force) {
    if (force || stickToBottom) ui.messages.scrollTop = ui.messages.scrollHeight;
  }

  let stickToBottom = true;
  ui.messages.addEventListener('scroll', () => { stickToBottom = nearBottom(); }, { passive: true });

  function append(node) {
    ui.empty.hidden = true;
    ui.messages.appendChild(node);
    scrollToBottom();
    return node;
  }

  function clearMessages() {
    for (const child of Array.from(ui.messages.children)) {
      if (child !== ui.empty) child.remove();
    }
    ui.empty.hidden = false;
    chat.turn = null;
  }

  function setTitle(title) {
    chat.title = title || '';
    ui.title.textContent = chat.title || 'Hermes';
  }

  function updateSubtitle() {
    const connected = gw.state === 'open';
    ui.connDot.className = 'dot ' + (connected ? (chat.running ? 'busy' : 'ok') : (status.phase === 'error' ? 'err' : 'busy'));
    if (!connected) {
      ui.connText.textContent = status.phase === 'running' ? '正在连接…' : phaseLabel(status.phase);
    } else {
      const model = chat.info && chat.info.model ? chat.info.model : '';
      ui.connText.textContent = chat.running ? '正在思考…' : (model || '已连接');
    }
  }

  function updateSendButton() {
    const hasText = ui.input.value.trim().length > 0 || chat.attachments.length > 0;
    const stopMode = chat.running && !hasText;
    ui.send.classList.toggle('stop', stopMode);
    ui.send.setAttribute('aria-label', stopMode ? '停止' : '发送');
    ui.send.disabled = !gw.open || (!hasText && !chat.running);
  }

  function setRunning(running) {
    chat.running = running;
    updateSendButton();
    updateSubtitle();
  }

  function addUser(text) {
    const wrap = el('div', 'msg user');
    wrap.appendChild(el('div', 'bubble', text));
    stickToBottom = true;
    return append(wrap);
  }

  function addSystem(text) {
    return append(el('div', 'msg system', text));
  }

  function addError(text) {
    const wrap = el('div', 'msg error');
    wrap.appendChild(el('div', 'body', text));
    return append(wrap);
  }

  function renderMarkdownInto(node, text, streaming) {
    node.innerHTML = MD.render(text);
    node.classList.toggle('cursor', !!streaming);
  }

  // A turn is one assistant reply: reasoning blocks, text segments and tool cards in order.
  function beginTurn() {
    if (chat.turn) return chat.turn;
    const wrap = el('div', 'msg assistant');
    append(wrap);
    chat.turn = {
      wrap,
      seg: null,
      segText: '',
      allText: [],
      reason: null,
      reasonText: '',
      tools: new Map(),
      statusEl: null,
      renderQueued: false,
    };
    return chat.turn;
  }

  function clearStatus(turn) {
    if (turn && turn.statusEl) {
      turn.statusEl.remove();
      turn.statusEl = null;
    }
  }

  function scheduleRender(turn) {
    if (turn.renderQueued) return;
    turn.renderQueued = true;
    requestAnimationFrame(() => {
      turn.renderQueued = false;
      if (turn.seg) renderMarkdownInto(turn.seg, turn.segText, true);
      scrollToBottom();
    });
  }

  function sealSegment(turn) {
    if (turn.seg) {
      renderMarkdownInto(turn.seg, turn.segText, false);
      if (turn.segText.trim()) turn.allText.push(turn.segText);
    }
    turn.seg = null;
    turn.segText = '';
    turn.reason = null;
    turn.reasonText = '';
  }

  function appendText(text) {
    const turn = beginTurn();
    clearStatus(turn);
    if (!turn.seg) {
      turn.seg = el('div', 'body');
      turn.wrap.appendChild(turn.seg);
      turn.reason = null;
    }
    turn.segText += text;
    scheduleRender(turn);
  }

  function appendReasoning(text) {
    const turn = beginTurn();
    if (!turn.reason) {
      const det = document.createElement('details');
      det.className = 'reasoning';
      det.appendChild(el('summary', null, '思考过程'));
      det.appendChild(el('div', 'rtext'));
      if (turn.seg) turn.wrap.insertBefore(det, turn.seg); else turn.wrap.appendChild(det);
      turn.reason = det;
      turn.reasonText = '';
    }
    turn.reasonText += text;
    turn.reason.querySelector('.rtext').textContent = turn.reasonText;
    scrollToBottom();
  }

  function toolCard(name, context, running) {
    const det = document.createElement('details');
    det.className = 'tool' + (running ? ' running' : '');
    const sum = el('summary');
    sum.appendChild(el('span', 'tname', name || '工具'));
    sum.appendChild(el('span', 'tctx', context || ''));
    sum.appendChild(el('span', 'tstate', running ? ' ' : '完成'));
    det.appendChild(sum);
    return det;
  }

  function setToolResult(card, text, failed) {
    card.classList.remove('running');
    if (failed) card.classList.add('failed');
    card.querySelector('.tstate').textContent = failed ? '失败' : '完成';
    let pre = card.querySelector('pre');
    if (text) {
      if (!pre) { pre = el('pre'); card.appendChild(pre); }
      pre.textContent = clip(text, 6000);
    }
  }

  function argsPreview(p) {
    if (p.context) return p.context;
    if (p.preview) return p.preview;
    if (p.args_text) return p.args_text;
    if (p.args && typeof p.args === 'object') {
      const vals = Object.values(p.args).filter(v => typeof v === 'string');
      if (vals.length) return vals[0];
    }
    return '';
  }

  function onToolStart(p) {
    const turn = beginTurn();
    clearStatus(turn);
    sealSegment(turn);
    const card = toolCard(p.name, argsPreview(p), true);
    turn.wrap.appendChild(card);
    turn.tools.set(p.tool_id, card);
    scrollToBottom();
  }

  function onToolComplete(p) {
    const turn = beginTurn();
    let card = turn.tools.get(p.tool_id);
    if (!card) {
      card = toolCard(p.name, argsPreview(p), false);
      turn.wrap.appendChild(card);
    }
    const result = p.summary || p.result_text || (p.result != null ? textOf(p.result) : '');
    const failed = p.result && typeof p.result === 'object' && (p.result.error || p.result.success === false);
    let body = result;
    if (p.inline_diff) body = (body ? body + '\n\n' : '') + p.inline_diff;
    setToolResult(card, body, !!failed);
  }

  function onStatus(p) {
    if (!chat.running) return;
    const turn = beginTurn();
    if (!turn.statusEl) {
      turn.statusEl = el('div', 'status-line');
      turn.wrap.appendChild(turn.statusEl);
    }
    turn.statusEl.textContent = p.text || '';
    scrollToBottom();
  }

  function onComplete(p) {
    const turn = beginTurn();
    clearStatus(turn);
    if (!turn.segText && !turn.allText.length && p) {
      const finalText = textOf(p.text);
      if (finalText) appendText(finalText);
    }
    if (p && p.reasoning && !turn.wrap.querySelector('details.reasoning')) {
      appendReasoning(p.reasoning);
    }
    sealSegment(turn);
    if (p && p.status === 'error') {
      const why = p.error || p.failure_reason || p.warning || '本轮对话出错';
      const box = el('div', 'body');
      box.style.color = 'var(--err)';
      box.textContent = '⚠ ' + why;
      turn.wrap.appendChild(box);
    } else if (p && p.status === 'interrupted') {
      turn.wrap.appendChild(el('div', 'meta', '已中断'));
    } else if (p && p.warning) {
      turn.wrap.appendChild(el('div', 'meta', p.warning));
    }
    const meta = el('div', 'meta');
    const full = turn.allText.join('\n\n');
    if (full) {
      const copyBtn = el('button', null, '复制');
      copyBtn.type = 'button';
      copyBtn.onclick = () => copy(full);
      meta.appendChild(copyBtn);
      const shareBtn = el('button', null, '分享');
      shareBtn.type = 'button';
      shareBtn.onclick = () => bridge && bridge.shareText(full);
      if (bridge) meta.appendChild(shareBtn);
    }
    const usage = p && p.usage;
    if (usage && (usage.total_tokens || usage.output_tokens)) {
      meta.appendChild(el('span', null, (usage.total_tokens || ((usage.input_tokens || 0) + (usage.output_tokens || 0))) + ' tokens'));
    }
    if (meta.childNodes.length) turn.wrap.appendChild(meta);
    chat.turn = null;
    setRunning(false);
    scrollToBottom();
  }

  // Rebuild a transcript returned by session.resume / session.create.
  function renderHistory(messages) {
    clearMessages();
    let turn = null;
    for (const m of messages || []) {
      if (!m || m.display_kind === 'hidden') continue;
      const text = m.text != null ? m.text : textOf(m.content);
      if (m.role === 'user') {
        chat.turn = null;
        turn = null;
        if (text) addUser(text);
      } else if (m.role === 'assistant') {
        turn = beginTurn();
        if (m.reasoning) {
          appendReasoning(m.reasoning);
        }
        if (text) {
          appendText(text);
          sealSegment(turn);
        }
      } else if (m.role === 'tool') {
        turn = beginTurn();
        sealSegment(turn);
        const card = toolCard(m.name, m.context || '', false);
        turn.wrap.appendChild(card);
        setToolResult(card, clip(text, 4000), false);
      }
    }
    if (chat.turn) {
      sealSegment(chat.turn);
      chat.turn = null;
    }
    requestAnimationFrame(() => scrollToBottom(true));
  }

  // ───────────────────────────── gateway events ─────────────────────────────

  const GLOBAL_EVENTS = new Set(['gateway.ready', 'skin.changed', 'setup.ready', 'sessions.changed', 'notification.show']);

  gw.eventListeners.add(ev => {
    const type = ev.type;
    const p = ev.payload || {};
    if (!GLOBAL_EVENTS.has(type) && ev.session_id && ev.session_id !== chat.sid) {
      if (type === 'session.title' && p.session_id === chat.storedId) setTitle(p.title);
      return;
    }
    switch (type) {
      case 'message.start':
        setRunning(true);
        beginTurn();
        break;
      case 'message.delta':
        if (!chat.running) setRunning(true);
        appendText(p.text || '');
        break;
      case 'reasoning.delta':
      case 'thinking.delta':
      case 'reasoning.available':
        appendReasoning(p.text || '');
        break;
      case 'message.interim':
        if (!p.already_streamed && p.text) appendText(p.text);
        if (chat.turn) sealSegment(chat.turn);
        break;
      case 'message.complete':
        onComplete(p);
        break;
      case 'tool.start':
        onToolStart(p);
        break;
      case 'tool.complete':
        onToolComplete(p);
        break;
      case 'tool.generating':
        onStatus({ text: '正在准备调用 ' + (p.name || '工具') + '…' });
        break;
      case 'status.update':
        onStatus(p);
        break;
      case 'error':
        addError(p.message || '发生错误');
        break;
      case 'notice':
        if (p.message) addSystem(p.message);
        break;
      case 'notification.show':
        if (p.text && (p.level === 'error' || p.level === 'warn')) toast(p.text);
        break;
      case 'session.title':
        if (!p.session_id || p.session_id === chat.storedId) setTitle(p.title);
        break;
      case 'session.info':
        chat.info = Object.assign({}, chat.info || {}, p);
        if (p.title) setTitle(p.title);
        if (typeof p.running === 'boolean' && !p.running && chat.running && !chat.turn) setRunning(false);
        updateSubtitle();
        break;
      case 'request.cancel':
        cancelAsk(p.id);
        break;
      case 'sessions.changed':
        if (!$('#drawer').hidden) loadSessions();
        break;
      default:
        break;
    }
  });

  // ───────────────────────────── server → client questions ─────────────────────────────

  const asks = new Map();

  function cancelAsk(id) {
    const card = asks.get(id);
    if (card) {
      card.classList.add('done');
      asks.delete(id);
    }
  }

  // Questions belong to the reply that asked them, so later tool cards and text follow them.
  function placeAsk(card) {
    if (chat.turn) {
      clearStatus(chat.turn);
      sealSegment(chat.turn);
      chat.turn.wrap.appendChild(card);
    } else {
      append(card);
    }
    scrollToBottom(true);
  }

  function askCard(req, title) {
    const card = el('div', 'ask');
    card.appendChild(el('div', 'ask-title', title));
    asks.set(req.id, card);
    return card;
  }

  function finishAsk(req, card, result, label) {
    gw.respond(req.id, result);
    card.classList.add('done');
    if (label) card.appendChild(el('div', 'muted', label));
    asks.delete(req.id);
  }

  const APPROVAL_LABELS = { once: '允许一次', session: '本会话允许', always: '始终允许', deny: '拒绝' };

  function showApproval(req) {
    const p = req.params;
    const card = askCard(req, '需要你的确认');
    if (p.description) card.appendChild(el('div', null, p.description));
    if (p.command) card.appendChild(el('pre', null, p.command));
    const choices = el('div', 'choices');
    const offered = (p.choices && p.choices.length) ? p.choices : ['once', 'deny'];
    for (const choice of offered) {
      const b = el('button', choice === 'once' ? 'primary' : '', APPROVAL_LABELS[choice] || choice);
      b.type = 'button';
      b.onclick = () => finishAsk(req, card, { choice }, '已选择：' + (APPROVAL_LABELS[choice] || choice));
      choices.appendChild(b);
    }
    card.appendChild(choices);
    placeAsk(card);
  }

  function questionBlock(q, onAnswer) {
    const box = el('div');
    box.appendChild(el('div', null, q.question));
    const input = document.createElement('textarea');
    input.rows = 2;
    input.placeholder = '输入回答…';
    const choices = el('div', 'choices');
    for (const c of q.choices || []) {
      const b = el('button', null, c);
      b.type = 'button';
      b.onclick = () => {
        if (q.multi_select) {
          b.classList.toggle('primary');
        } else {
          onAnswer(c);
        }
      };
      choices.appendChild(b);
    }
    box.appendChild(choices);
    box.appendChild(input);
    box.value = () => {
      if (q.multi_select) {
        const picked = Array.from(choices.querySelectorAll('button.primary')).map(b => b.textContent);
        if (picked.length) return picked.join(', ');
      }
      return input.value.trim();
    };
    return box;
  }

  function showClarify(req) {
    const p = req.params;
    const card = askCard(req, 'Hermes 想确认一下');
    const actions = el('div', 'choices');
    if (Array.isArray(p.questions) && p.questions.length) {
      const blocks = p.questions.map(q => {
        const block = questionBlock(q, () => {});
        card.appendChild(block);
        return { q, block };
      });
      const submit = el('button', 'primary', '提交');
      submit.type = 'button';
      submit.onclick = () => {
        const answers = {};
        for (const { q, block } of blocks) answers[q.qid] = block.value();
        finishAsk(req, card, { answers }, '已回答');
      };
      actions.appendChild(submit);
    } else {
      const block = questionBlock({ question: p.question || '', choices: p.choices, multi_select: p.multi_select },
        answer => finishAsk(req, card, { answer }, '已回答：' + answer));
      card.appendChild(block);
      const submit = el('button', 'primary', '回答');
      submit.type = 'button';
      submit.onclick = () => finishAsk(req, card, { answer: block.value() }, '已回答');
      actions.appendChild(submit);
    }
    const skip = el('button', null, '跳过');
    skip.type = 'button';
    skip.onclick = () => finishAsk(req, card, Array.isArray(p.questions) ? {} : { answer: '' }, '已跳过');
    actions.appendChild(skip);
    card.appendChild(actions);
    placeAsk(card);
  }

  function showValuePrompt(req, title, hint, secret) {
    const card = askCard(req, title);
    if (hint) card.appendChild(el('div', null, hint));
    const input = document.createElement('input');
    input.type = secret ? 'password' : 'text';
    card.appendChild(input);
    const actions = el('div', 'choices');
    const ok = el('button', 'primary', '提交');
    ok.type = 'button';
    ok.onclick = () => finishAsk(req, card, { value: input.value }, '已提交');
    const skip = el('button', null, '跳过');
    skip.type = 'button';
    skip.onclick = () => finishAsk(req, card, { value: '' }, '已跳过');
    actions.appendChild(ok);
    actions.appendChild(skip);
    card.appendChild(actions);
    placeAsk(card);
  }

  gw.requestHandler = req => {
    const p = req.params || {};
    switch (req.method) {
      case 'approval':
        showApproval(req);
        return true;
      case 'clarify':
        showClarify(req);
        return true;
      case 'secret':
        showValuePrompt(req, '需要填写 ' + (p.env_var || '密钥'), p.prompt, true);
        return true;
      case 'vault.unlock_prompt':
        showValuePrompt(req, '解锁 ' + (p.display_name || p.backend || '密码库'), '', true);
        return true;
      case 'vault.code':
        showValuePrompt(req, '需要验证码', p.prompt || '', false);
        return true;
      case 'vault.save_login':
        gw.respond(req.id, { value: '' });
        return true;
      case 'sudo':
        addSystem('Android 上没有 sudo，已跳过需要管理员权限的命令。');
        gw.respond(req.id, { value: '' });
        return true;
      // Desktop-only bridges (terminal pane, preview pane, window capture, guided tour).
      case 'terminal.read':
      case 'preview.read':
      case 'window.read':
      case 'preview.act':
      case 'tour':
        gw.respond(req.id, { value: '' });
        return true;
      default:
        return false;
    }
  };

  // ───────────────────────────── sessions ─────────────────────────────

  function rememberSession() {
    try {
      if (chat.storedId) localStorage.setItem(LAST_SESSION_KEY, chat.storedId);
      else localStorage.removeItem(LAST_SESSION_KEY);
    } catch (_) { /* storage may be unavailable */ }
  }

  function lastSession() {
    try { return localStorage.getItem(LAST_SESSION_KEY); } catch (_) { return null; }
  }

  function adoptSnapshot(r, fallbackStoredId) {
    chat.sid = r.session_id;
    chat.storedId = r.stored_session_id || (r.info && r.info.stored_session_id) || fallbackStoredId || null;
    chat.info = r.info || null;
    setTitle((r.info && r.info.title) || '');
    rememberSession();
    updateSubtitle();
  }

  async function ensureSession() {
    if (chat.sid) return chat.sid;
    const r = await gw.request('session.create', {}, 60000);
    adoptSnapshot(r);
    return chat.sid;
  }

  async function openSession(storedId, quiet) {
    try {
      const r = await gw.request('session.resume', { session_id: storedId }, 90000);
      adoptSnapshot(r, storedId);
      renderHistory(r.messages);
      setRunning(!!r.running);
      for (const open of r.open_requests || []) {
        gw.requestHandler({ id: open.id, method: open.method, params: open.params || {} });
      }
      return true;
    } catch (e) {
      if (!quiet) toast('打开会话失败：' + e.message);
      return false;
    }
  }

  function newChat(focus) {
    const old = chat.sid;
    if (old && !chat.running) {
      gw.request('session.close', { session_id: old }, 10000).catch(() => {});
    }
    chat.sid = null;
    chat.storedId = null;
    chat.info = null;
    setTitle('');
    rememberSession();
    clearMessages();
    setRunning(false);
    closeDrawer();
    if (focus) ui.input.focus();
  }

  async function loadSessions() {
    const list = $('#session-list');
    if (!gw.open) {
      list.innerHTML = '';
      list.appendChild(el('div', 'sess-empty', 'Hermes 未连接'));
      return;
    }
    try {
      const r = await gw.request('session.list', { limit: 80 }, 20000);
      list.innerHTML = '';
      const rows = (r && r.sessions) || [];
      if (!rows.length) list.appendChild(el('div', 'sess-empty', '还没有会话'));
      for (const row of rows) {
        const b = el('button', 'sess' + (row.id === chat.storedId ? ' active' : ''));
        b.type = 'button';
        b.appendChild(el('div', 'st', row.title || row.preview || '未命名会话'));
        b.appendChild(el('div', 'sp', [relTime(row.last_active || row.started_at), row.title ? row.preview : ''].filter(Boolean).join(' · ')));
        b.onclick = async () => {
          closeDrawer();
          await openSession(row.id);
        };
        list.appendChild(b);
      }
    } catch (e) {
      list.innerHTML = '';
      list.appendChild(el('div', 'sess-empty', '加载失败：' + e.message));
    }
  }

  function openDrawer() {
    $('#drawer').hidden = false;
    loadSessions();
  }

  function closeDrawer() {
    $('#drawer').hidden = true;
  }

  // ───────────────────────────── composer ─────────────────────────────

  function renderAttachments() {
    ui.attachments.innerHTML = '';
    chat.attachments.forEach((file, i) => {
      const chip = el('div', 'chip');
      chip.appendChild(el('span', null, file.name));
      const x = el('button', null, '×');
      x.type = 'button';
      x.onclick = () => {
        chat.attachments.splice(i, 1);
        renderAttachments();
        updateSendButton();
      };
      chip.appendChild(x);
      ui.attachments.appendChild(chip);
    });
  }

  async function uploadAttachments(sid) {
    const refs = [];
    for (const file of chat.attachments) {
      const data = await fileToBase64(file);
      const type = file.type || '';
      if (type.startsWith('image/')) {
        await gw.request('image.attach_bytes', { session_id: sid, content_base64: data.base64, filename: file.name }, 60000);
      } else if (type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
        await gw.request('pdf.attach', { session_id: sid, content_base64: data.base64, filename: file.name }, 120000);
      } else {
        const r = await gw.request('file.attach', { session_id: sid, data_url: data.dataUrl, name: file.name }, 60000);
        if (r && r.ref_text) refs.push(r.ref_text);
      }
    }
    return refs;
  }

  async function submit(textOverride) {
    const text = (textOverride != null ? textOverride : ui.input.value).trim();
    const files = chat.attachments.slice();
    if (!text && !files.length) {
      if (chat.running && chat.sid) {
        gw.request('session.interrupt', { session_id: chat.sid }, 15000).catch(e => toast(e.message));
      }
      return;
    }
    if (!gw.open) {
      toast('Hermes 还没有连接');
      return;
    }
    ui.input.value = '';
    autosize();
    chat.attachments = [];
    renderAttachments();
    const shown = files.length ? (text + (text ? '\n' : '') + files.map(f => '📎 ' + f.name).join('\n')) : text;
    addUser(shown);
    try {
      const sid = await ensureSession();
      const refs = files.length ? await uploadAttachments(sid) : [];
      const finalText = [text].concat(refs).filter(Boolean).join('\n\n') || '请查看附件';
      setRunning(true);
      const r = await gw.request('prompt.submit', { session_id: sid, text: finalText }, 60000);
      if (r && r.status === 'queued') addSystem('已加入队列，当前回复结束后处理');
      else if (r && (r.status === 'steered' || r.status === 'redirected')) addSystem('已把新消息交给正在运行的任务');
    } catch (e) {
      setRunning(false);
      addError('发送失败：' + e.message);
    }
  }

  function autosize() {
    ui.input.style.height = 'auto';
    ui.input.style.height = Math.min(ui.input.scrollHeight, window.innerHeight * 0.4) + 'px';
    updateSendButton();
  }

  $('#composer').addEventListener('submit', ev => {
    ev.preventDefault();
    submit();
  });
  ui.input.addEventListener('input', autosize);
  ui.input.addEventListener('focus', () => document.body.classList.add('typing'));
  ui.input.addEventListener('blur', () => setTimeout(() => document.body.classList.remove('typing'), 150));
  $('#btn-attach').onclick = () => ui.file.click();
  ui.file.addEventListener('change', () => {
    for (const f of Array.from(ui.file.files || [])) {
      if (f.size > 25 * 1024 * 1024) { toast(f.name + ' 超过 25 MB'); continue; }
      chat.attachments.push(f);
    }
    ui.file.value = '';
    renderAttachments();
    updateSendButton();
  });
  document.querySelectorAll('#empty [data-prompt]').forEach(b => {
    b.addEventListener('click', () => submit(b.getAttribute('data-prompt')));
  });
  ui.messages.addEventListener('click', ev => {
    const btn = ev.target.closest && ev.target.closest('.code .copy');
    if (btn) {
      const code = btn.closest('.code').querySelector('code');
      copy(code ? code.textContent : '');
    }
  });

  // ───────────────────────────── connection management ─────────────────────────────

  let reconnectTimer = null;
  let reconnectDelay = 800;
  let connectedToken = '';
  let setupChecked = false;

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      ensureConnected();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 8000);
  }

  async function ensureConnected() {
    if (status.phase !== 'running' || !status.token) return;
    if (gw.open && connectedToken === status.token) return;
    if (gw.state === 'connecting') return;
    if (gw.open) gw.close();
    const url = 'ws://' + location.host + '/api/ws?token=' + encodeURIComponent(status.token);
    try {
      await gw.connect(url);
      connectedToken = status.token;
      reconnectDelay = 800;
      await onConnected();
    } catch (e) {
      console.warn('gateway connect failed', e);
      scheduleReconnect();
    }
  }

  async function onConnected() {
    console.log('HERMES_GATEWAY_CONNECTED');
    updateSubtitle();
    updateSendButton();
    const target = chat.storedId || lastSession();
    if (target) {
      const ok = await openSession(target, true);
      if (!ok) newChat(false);
    }
    if (!setupChecked) {
      setupChecked = true;
      checkSetup();
    }
  }

  async function checkSetup() {
    try {
      const r = await gw.request('setup.status', {}, 20000);
      console.log('HERMES_SETUP_STATUS provider_configured=' + (r && r.provider_configured));
      if (r && r.provider_configured === false) {
        ui.banner.innerHTML = '';
        ui.banner.appendChild(el('div', null, '还没有配置 AI 模型。请在控制台的「Models / Keys」里添加模型服务商和 API Key，然后回来开始对话。'));
        const go = el('button', null, '去配置');
        go.type = 'button';
        go.onclick = () => { showView('console', '/models'); ui.banner.hidden = true; };
        ui.banner.appendChild(go);
        ui.banner.hidden = false;
      } else {
        ui.banner.hidden = true;
      }
    } catch (_) { /* older backends: skip the hint */ }
  }

  gw.stateListeners.add(state => {
    updateSubtitle();
    updateSendButton();
    if (state === 'closed' && status.phase === 'running') scheduleReconnect();
  });

  // ───────────────────────────── boot overlay & status page ─────────────────────────────

  function phaseLabel(phase) {
    return ({
      idle: '未启动',
      installing: '正在安装',
      starting: '正在启动',
      running: '运行中',
      stopping: '正在停止',
      stopped: '已停止',
      error: '出错了',
    })[phase] || phase || '—';
  }

  const boot = {
    root: $('#boot'),
    title: $('#boot-title'),
    text: $('#boot-text'),
    progress: $('#boot-progress'),
    action: $('#boot-action'),
  };

  function setProgress(node, value) {
    if (value >= 0) {
      node.classList.remove('indeterminate');
      node.firstElementChild.style.width = Math.round(value * 100) + '%';
    } else {
      node.classList.add('indeterminate');
      node.firstElementChild.style.width = '';
    }
  }

  function renderBoot() {
    const phase = status.phase;
    if (phase === 'running') {
      boot.root.hidden = true;
      return;
    }
    boot.root.hidden = false;
    boot.action.hidden = true;
    $('#boot-logs').hidden = phase !== 'error';
    boot.progress.hidden = false;
    if (phase === 'installing') {
      boot.title.textContent = '首次运行：正在安装运行环境';
      boot.text.textContent = (status.message || '') + '（约需 1–3 分钟，只需一次）';
      setProgress(boot.progress, status.progress);
    } else if (phase === 'starting') {
      boot.title.textContent = '正在启动 Hermes';
      boot.text.textContent = '首次启动需要预热 Python 环境，可能要一两分钟';
      setProgress(boot.progress, -1);
    } else if (phase === 'error') {
      boot.title.textContent = 'Hermes 没有正常运行';
      boot.text.textContent = status.message || '';
      boot.progress.hidden = true;
      boot.action.hidden = false;
      boot.action.textContent = '重新启动';
      boot.action.onclick = () => bridge && bridge.restart();
    } else {
      boot.title.textContent = phase === 'stopping' ? '正在停止…' : 'Hermes 未运行';
      boot.text.textContent = '智能体服务在这台手机上本地运行';
      boot.progress.hidden = phase !== 'stopping';
      setProgress(boot.progress, -1);
      if (phase !== 'stopping') {
        boot.action.hidden = false;
        boot.action.textContent = '启动 Hermes';
        boot.action.onclick = () => bridge && bridge.start();
      }
    }
  }

  function renderStatusPage() {
    $('#st-phase').textContent = phaseLabel(status.phase);
    const dot = $('#st-dot');
    dot.className = 'dot big-dot ' + (status.phase === 'running' ? 'ok' : status.phase === 'error' ? 'err' : 'busy');
    $('#st-message').textContent = status.message || '';
    const prog = $('#st-progress');
    prog.hidden = status.phase !== 'installing';
    if (!prog.hidden) setProgress(prog, status.progress);
    $('#st-addr').textContent = status.port ? '127.0.0.1:' + status.port : '—';
    $('#st-pid').textContent = status.pid ? String(status.pid) : '—';
    $('#st-payload').textContent = status.payloadId || '—';
    $('#st-app').textContent = status.appVersion || '—';
    $('#st-device').textContent = bridge ? bridge.deviceInfo() : navigator.userAgent;
    const running = status.phase === 'running' || status.phase === 'starting' || status.phase === 'installing';
    $('#btn-start').disabled = running;
    $('#btn-stop').disabled = !running;
    if (bridge) {
      $('#opt-awake').checked = bridge.keepAwake();
      $('#opt-autostart').checked = bridge.autoStart();
      const optimized = bridge.batteryOptimized();
      $('#battery-text').textContent = optimized ? '受系统限制，后台可能被暂停' : '已关闭，后台运行更稳定';
      $('#btn-battery').hidden = !optimized;
    }
  }

  async function refreshLog() {
    try {
      const r = await (await fetch('/__android/log.json?n=500', { cache: 'no-store' })).json();
      const pre = $('#log');
      const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
      pre.textContent = (r.lines || []).join('\n');
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    } catch (_) { /* ignore */ }
  }

  let lastPhase = '';

  async function pollStatus() {
    try {
      status = await fetchStatus();
    } catch (_) {
      status = { phase: 'idle' };
    }
    if (status.phase !== lastPhase) {
      lastPhase = status.phase;
      if (status.phase !== 'running' && gw.open) gw.close();
    }
    renderBoot();
    if (currentView === 'status') renderStatusPage();
    if (currentView === 'console') syncConsole();
    updateSubtitle();
    if (status.phase === 'running') ensureConnected();
    const delay = status.phase === 'running' ? 3000 : 800;
    clearTimeout(pollStatus.timer);
    pollStatus.timer = setTimeout(pollStatus, delay);
  }

  // ───────────────────────────── views ─────────────────────────────

  let currentView = 'chat';
  let logTimer = null;

  function syncConsole(path) {
    const frame = $('#console-frame');
    const offline = $('#console-offline');
    const running = status.phase === 'running';
    offline.classList.toggle('show', !running);
    if (running && (path || !frame.getAttribute('src'))) {
      frame.setAttribute('src', path || '/');
    }
  }

  function showView(name, consolePath) {
    currentView = name;
    document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
    document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    clearInterval(logTimer);
    if (name === 'console') syncConsole(consolePath);
    if (name === 'status') {
      renderStatusPage();
      refreshLog();
      logTimer = setInterval(refreshLog, 2000);
    }
  }

  document.querySelectorAll('#tabs button').forEach(b => {
    b.addEventListener('click', () => showView(b.dataset.view));
  });
  $('#btn-sessions').onclick = openDrawer;
  $('#btn-new').onclick = () => newChat(true);
  $('#btn-drawer-new').onclick = () => newChat(true);
  $('#drawer [data-close]').onclick = closeDrawer;

  $('#btn-start').onclick = () => bridge && bridge.start();
  $('#btn-stop').onclick = () => bridge && bridge.stop();
  $('#btn-restart').onclick = () => bridge && bridge.restart();
  $('#btn-battery').onclick = () => bridge && bridge.requestBatteryExemption();
  $('#opt-awake').onchange = ev => bridge && bridge.setKeepAwake(ev.target.checked);
  $('#opt-autostart').onchange = ev => bridge && bridge.setAutoStart(ev.target.checked);
  $('#btn-log-refresh').onclick = refreshLog;
  $('#boot-logs').onclick = () => showView('status');
  $('#btn-log-copy').onclick = () => copy($('#log').textContent);

  // Hooks called by MainActivity.
  window.hermesBack = function () {
    if (!$('#drawer').hidden) { closeDrawer(); return true; }
    if (currentView === 'console') {
      const frame = $('#console-frame');
      try {
        const loc = frame.contentWindow.location;
        if (loc.pathname && loc.pathname !== '/') { frame.contentWindow.history.back(); return true; }
      } catch (_) { /* cross-origin: fall through */ }
    }
    if (currentView !== 'chat') { showView('chat'); return true; }
    return false;
  };

  window.hermesShared = function () {
    if (!bridge) return;
    const text = bridge.takeSharedText();
    if (text) {
      showView('chat');
      ui.input.value = ui.input.value ? ui.input.value + '\n' + text : text;
      autosize();
      ui.input.focus();
    }
  };

  window.hermesResume = function () {
    pollStatus();
  };

  // ───────────────────────────── start ─────────────────────────────

  updateSendButton();
  pollStatus();
  window.hermesShared();
})();

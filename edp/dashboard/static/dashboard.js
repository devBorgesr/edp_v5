/**
 * EDP v3.3 Dashboard
 *
 * Estratégia:
 *   - IIFE para isolar escopo
 *   - WebSocket único reutilizável (sem duplicação)
 *   - Polling reduzido (15s em vez de 5s)
 *   - Fallback HTTP automático
 *   - window.onerror para diagnóstico
 *
 * Arquivo separado do Python → sem problemas de escape \n, ${}, aspas, etc.
 */

window.onerror = function(msg, src, line, col, err) {
  console.error('[GLOBAL ERROR]', msg, 'line:', line, 'col:', col, err);
  return false;
};

console.log('[dashboard] script carregado');

(function() {
  'use strict';

  const SESSION = "default";
  const POLL_INTERVAL_MS = 15000;  // reduzido de 5s para 15s

  let ws = null;
  let wsReady = false;
  let wsConnecting = false;

  // ── DOM helpers ───────────────────────────────────────────────────────────
  function el(id) { return document.getElementById(id); }

  function setVal(id, val, cls) {
    const e = el(id);
    if (!e) return;
    e.textContent = val;
    e.className = 'value' + (cls ? ' ' + cls : '');
  }

  // ── Helpers da v3.5 ───────────────────────────────────────────────────────
  //
  // REGRA: campo ausente vira '—'. Nunca 0, nunca 'ok', nunca valor
  // inventado — o backend responde parcial em degradação, e um zero
  // fabricado ali seria indistinguivel de um zero medido.

  const NA = '\u2014';  // travessão

  function setText(id, val) {
    const e = el(id);
    if (e) e.textContent = (val === null || val === undefined || val === '') ? NA : val;
  }

  function num(v) {
    if (v === null || v === undefined || isNaN(v)) return null;
    return Number(v).toLocaleString('pt-BR');
  }

  function setBar(id, part, total) {
    const e = el(id);
    if (!e) return;
    const pct = (total > 0 && part >= 0) ? Math.min(100, (part / total) * 100) : 0;
    e.style.width = pct.toFixed(1) + '%';
  }

  // Runtime flow — dirigido pelos eventos REAIS do WebSocket.
  // Não é o pipeline do agent_runtime (outro repositório, sem fonte aqui).
  const FLOW = ['request', 'pipeline', 'model', 'stream', 'response'];
  const MARK = { waiting: '\u25CB', active: '\u25CF', done: '\u2713',
                 blocked: '!', error: '\u00D7' };

  function setFlow(node, state) {
    const e = document.querySelector('[data-node="' + node + '"]');
    if (!e) return;
    e.setAttribute('data-state', state);
    const m = e.querySelector('.flow-mark');
    if (m) m.textContent = MARK[state] || MARK.waiting;
  }

  function resetFlow(state) {
    FLOW.forEach(function(n) { setFlow(n, state || 'waiting'); });
  }

  // Avança: tudo antes de `node` vira done, `node` vira active.
  function advanceFlow(node) {
    const i = FLOW.indexOf(node);
    if (i < 0) return;
    FLOW.forEach(function(n, j) {
      setFlow(n, j < i ? 'done' : (j === i ? 'active' : 'waiting'));
    });
  }

  function addEvent(what, meta, cls) {
    const list = el('event-list');
    if (!list) return;
    const vazio = list.querySelector('.empty');
    if (vazio) vazio.remove();

    const row = document.createElement('div');
    row.className = 'event new';

    const t = document.createElement('span');
    t.className = 'ev-time';
    t.textContent = new Date().toLocaleTimeString('pt-BR', { hour12: false });

    const w = document.createElement('span');
    w.className = 'ev-what';
    w.textContent = what;

    const m = document.createElement('span');
    m.className = 'ev-meta' + (cls ? ' ' + cls : '');
    m.textContent = meta || '';

    row.appendChild(t); row.appendChild(w); row.appendChild(m);
    list.insertBefore(row, list.firstChild);

    while (list.children.length > 60) list.removeChild(list.lastChild);
  }

  // Bloco técnico no console: decisão do RUNTIME, não raciocínio do modelo.
  function addOpBlock(titulo, linhas) {
    const box = el('chat-box');
    if (!box) return;
    const b = document.createElement('div');
    b.className = 'op-block';
    const t = document.createElement('div');
    t.className = 'op-title';
    t.textContent = titulo;
    b.appendChild(t);
    (linhas || []).forEach(function(par) {
      const r = document.createElement('div');
      r.className = 'op-row';
      const k = document.createElement('span'); k.textContent = par[0] + ':';
      const v = document.createElement('b');    v.textContent = par[1];
      r.appendChild(k); r.appendChild(v);
      b.appendChild(r);
    });
    box.appendChild(b);
    box.scrollTop = box.scrollHeight;
  }

  function setConnDot(estado) {
    const d = el('conn-dot');
    if (d) d.className = 'dot ' + (estado || 'w');
  }

  function fmtDur(seg) {
    if (seg == null || isNaN(seg)) return null;
    const s2 = Math.floor(seg);
    if (s2 < 60)   return s2 + 's';
    if (s2 < 3600) return Math.floor(s2 / 60) + 'min';
    if (s2 < 86400) return Math.floor(s2 / 3600) + 'h';
    return Math.floor(s2 / 86400) + 'd';
  }

  // runtime_state.components — mapa nome -> {healthy, last_check, error}.
  // Existia no payload desde a v3.4 e nunca chegou na tela.
  function renderComponents(comps) {
    const box = el('components');
    if (!box) return;
    const nomes = comps ? Object.keys(comps) : [];
    if (!nomes.length) {
      box.innerHTML = '';
      const v = document.createElement('div');
      v.className = 'empty';
      v.textContent = 'Nenhum componente reportado.';
      box.appendChild(v);
      return;
    }
    box.innerHTML = '';
    nomes.sort().forEach(function(nome) {
      const c = comps[nome] || {};
      const row = document.createElement('div');
      row.className = 'comp';

      const d = document.createElement('i');
      d.className = 'dot ' + (c.healthy === true ? 'd' : c.healthy === false ? 'e' : 'w');

      const n = document.createElement('span');
      n.className = 'comp-name';
      n.textContent = nome;

      row.appendChild(d); row.appendChild(n);

      if (c.healthy === false && c.error) {
        const e2 = document.createElement('span');
        e2.className = 'comp-err';
        e2.textContent = String(c.error);
        row.appendChild(e2);
      }
      box.appendChild(row);
    });
  }

  function setStage(text) {
    const stage = el('stage-info');
    if (stage) stage.textContent = text;
  }

  function addMsg(role, text) {
    const box = el('chat-box');
    if (!box) return;
    const msg = document.createElement('div');
    msg.className = 'msg ' + role;
    const bub = document.createElement('div');
    bub.className = 'bubble';
    bub.textContent = text;
    msg.appendChild(bub);
    box.appendChild(msg);
    box.scrollTop = box.scrollHeight;
  }

  // ── Fetch helper ──────────────────────────────────────────────────────────
  async function fetchJSON(url) {
    try {
      const r = await fetch(url);
      if (!r.ok) return null;
      return r.json();
    } catch {
      return null;
    }
  }

  // ── Refresh consolidado (1 chamada em vez de 4) ──────────────────────────
  async function refresh() {
    const data = await fetchJSON('/dashboard/state?session_id=' + SESSION);
    if (!data) {
      setVal('h-status', 'OFFLINE', 'err');
      const d0 = el('status-dot');
      if (d0) { d0.style.background = 'var(--err)'; d0.classList.remove('live'); }
      return;
    }

    // Health
    if (data.health) {
      setVal('h-status', data.health.status, data.health.status === 'ready' || data.health.status === 'ok' ? 'ok' : 'err');
      setVal('h-version', data.health.version, '');
      setVal('h-sessions', (data.health.sessions || []).length, '');
    }

    // v3.4 - Runtime state + pressure + queue (log no console + warning visual)
    if (data.runtime_state && data.runtime_state.state) {
      const state = data.runtime_state.state;
      const isHealthy = state === 'ready';
      const isDegraded = state === 'degraded';
      if (isDegraded) {
        console.warn('[runtime] DEGRADED', data.runtime_state);
      }
      // Sobrescreve status com state real
      setVal('h-status', state, isHealthy ? 'ok' : (isDegraded ? 'warn' : 'err'));
    }
    // uptime + componentes (runtime_state ja vinha; nunca fora exibido)
    if (data.runtime_state) {
      const rs = data.runtime_state;
      setText('rt-uptime', rs.uptime_s != null
        ? 'up ' + fmtDur(rs.uptime_s) : null);
      renderComponents(rs.components);
    }

    // PRESSAO: chegava ao navegador e so ia para console.log.
    if (data.pressure) {
      const lvl = data.pressure.level;
      setVal('p-level', lvl || NA,
             lvl === 'ok' || lvl === 'normal' ? 'ok'
             : lvl === 'warning' ? 'warn'
             : lvl === 'critical' ? 'err' : '');
      setText('p-ram', data.pressure.available_gb != null
        ? Number(data.pressure.available_gb).toFixed(1) + ' GB' : null);
      if (lvl === 'critical') console.error('[pressure] CRITICAL', data.pressure);
      else if (lvl === 'warning') console.warn('[pressure] WARNING', data.pressure);
    }

    // FILA: idem — active/queued eram so console.log.
    if (data.queue && !data.queue.error) {
      const q = data.queue;
      setVal('q-active', q.active != null ? q.active : NA,
             q.active > 0 ? 'ok' : '');
      setText('q-capacity', q.max_concurrent != null
        ? 'max ' + q.max_concurrent + ' simultaneas' : null);
      setVal('q-queued', q.queued != null ? q.queued : NA,
             q.queued > 0 ? 'warn' : '');
      setText('q-wait', q.avg_wait_ms != null
        ? 'espera media ' + Math.round(q.avg_wait_ms) + 'ms' : null);
    }

    // RETRIEVAL QUALITY: o backend devolvia e o front nunca lia.
    if (data.retrieval_quality && !data.retrieval_quality.error) {
      const rq = data.retrieval_quality;
      setText('r-trend', rq.trend);
      setText('r-turns', num(rq.total_turns));
    }

    // CONTRADICOES: idem.
    if (data.contradictions && data.contradictions.stats) {
      const cs = data.contradictions.stats;
      setVal('c-flags', cs.total_flags != null ? cs.total_flags : NA,
             cs.total_flags > 0 ? 'warn' : '');
    }

    // Memory
    if (data.memory && !data.memory.error) {
      setVal('m-episodic', data.memory.episodic != null ? data.memory.episodic : '?', '');
      setVal('m-semantic', data.memory.semantic != null ? data.memory.semantic : '?', '');
      setVal('m-working',  data.memory.working  != null ? data.memory.working  : '?', '');
      setVal('m-total',    data.memory.total    != null ? data.memory.total    : '?', '');

      const mm = data.memory, tot = mm.total || 0;
      setBar('bar-episodic', mm.episodic, tot);
      setBar('bar-semantic', mm.semantic, tot);
      setBar('bar-working',  mm.working,  tot);
      setText('m-sub', tot > 0
        ? (mm.episodic || 0) + ' ep \u00B7 ' + (mm.semantic || 0) + ' sem'
        : null);
    }

    // LLM metrics
    if (data.llm_metrics) {
      const m = data.llm_metrics;
      if (m.model)    setVal('llm-model',    m.model, '');
      if (m.provider) setVal('llm-provider', m.provider, '');
      if (m.avg_latency_ms != null) {
        setVal('llm-lat', Math.round(m.avg_latency_ms) + 'ms', '');
      }
      if (m.total_requests != null) setVal('llm-req', m.total_requests, '');
      if (m.avg_memory_hits != null) {
        setVal('llm-hits', m.avg_memory_hits.toFixed(1), '');
      }
      if (m.total_errors != null) {
        setVal('llm-err', m.total_errors, m.total_errors > 0 ? 'err' : 'ok');
      }
      if (m.avg_first_token_ms != null && m.avg_first_token_ms > 0) {
        setText('llm-ttft', Math.round(m.avg_first_token_ms) + 'ms');
      }
    }

    // System metrics
    if (data.system_metrics) {
      const sm = data.system_metrics;
      const raw = el('raw-metrics');
      if (raw) raw.textContent = JSON.stringify(sm, null, 2).slice(0, 1200);

      if (sm.retrieval_score) {
        setVal('r-score', (sm.retrieval_score.mean || 0).toFixed(3), '');
      }
      if (sm.compression_ratio) {
        setVal('r-comp', ((sm.compression_ratio.mean || 0) * 100).toFixed(1) + '%', '');
      }
      if (sm.cache_hit) {
        setVal('r-cache', ((sm.cache_hit.mean || 0) * 100).toFixed(0) + '%', '');
      }
    }

    // Snapshot raw (mostra estado consolidado)
    const snap = el('snapshot-data');
    if (snap) {
      snap.textContent = JSON.stringify({
        session_id: data.session_id,
        memory:     data.memory,
        metrics:    data.llm_metrics,
      }, null, 2).slice(0, 800);
    }

    // Last update timestamp
    const ts = el('last-update');
    if (ts) ts.textContent = new Date().toLocaleTimeString();
    const dot = el('status-dot');
    if (dot) {
      dot.style.background = 'var(--ok)';
      dot.classList.add('live');
    }
  }

  // ── LLM connection ────────────────────────────────────────────────────────

  // Adapta formulário conforme provider escolhido
  function updateProviderForm() {
    const provEl = el('provider');
    const urlInput = el('url-input');
    const keyInput = el('apikey-input');
    const modelInput = el('model-input');

    // Sanity check: se algum elemento não existir, aborta silenciosamente
    if (!provEl || !urlInput || !keyInput || !modelInput) {
      console.warn('[connect] elementos faltando', {
        provEl: !!provEl, urlInput: !!urlInput,
        keyInput: !!keyInput, modelInput: !!modelInput,
      });
      return;
    }

    const provider = provEl.value;

    if (provider === 'anthropic') {
      urlInput.style.display = 'none';
      keyInput.style.display = '';
      keyInput.placeholder = 'API key Anthropic (sk-ant-...)';
      if (!modelInput.value || modelInput.value.includes('phi3') || modelInput.value.includes('qwen') || modelInput.value.startsWith('gpt')) {
        modelInput.value = 'claude-haiku-4-5';
      }
    } else if (provider === 'openai') {
      urlInput.style.display = 'none';
      keyInput.style.display = '';
      keyInput.placeholder = 'API key OpenAI (sk-...)';
      if (modelInput.value.startsWith('claude') || modelInput.value.includes('phi3') || modelInput.value.includes('qwen')) {
        modelInput.value = 'gpt-4o-mini';
      }
    } else {
      // Local: ollama / lm_studio
      urlInput.style.display = '';
      keyInput.style.display = 'none';
      if (provider === 'ollama') {
        if (!urlInput.value) urlInput.value = 'http://localhost:11434';
        if (modelInput.value.startsWith('claude') || modelInput.value.startsWith('gpt')) {
          modelInput.value = 'qwen2.5:0.5b';
        }
      } else {
        if (!urlInput.value) urlInput.value = 'http://localhost:1234';
      }
    }
  }

  async function connectLLM() {
    console.log('[connect] iniciando...');
    const provEl   = el('provider');
    const modelEl  = el('model-input');
    const urlEl    = el('url-input');
    const keyEl    = el('apikey-input');
    const statusEl = el('conn-status');

    if (!provEl || !modelEl || !statusEl) {
      console.error('[connect] elementos faltando');
      return;
    }

    const provider = provEl.value;
    const model    = (modelEl.value || '').trim();
    const url      = urlEl ? (urlEl.value || '').trim() : '';
    const apiKey   = keyEl ? (keyEl.value || '').trim() : '';

    console.log('[connect] provider=' + provider + ' model=' + model +
                ' has_key=' + (apiKey ? 'yes' : 'no'));

    // Validações conforme provider
    if ((provider === 'anthropic' || provider === 'openai') && !apiKey) {
      statusEl.textContent = 'Informe API key';
      statusEl.className = 'err';
      console.warn('[connect] api key obrigatória para ' + provider);
      return;
    }
    if (!model) {
      statusEl.textContent = 'Informe modelo';
      statusEl.className = 'err';
      return;
    }

    statusEl.textContent = 'Conectando...';
    statusEl.className = '';
    setConnDot('a');

    try {
      const payload = {
        provider: provider,
        model: model,
        base_url: url || '',
        api_key: apiKey,
        session_id: SESSION,
      };
      console.log('[connect] POST /connect', {
        provider: provider, model: model, has_key: !!apiKey,
      });
      const r = await fetch('/connect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });

      console.log('[connect] resposta status=' + r.status);

      if (r.ok) {
        const d = await r.json();
        console.log('[connect] OK', d);
        statusEl.textContent = 'Conectado: ' + d.model;
        statusEl.className = 'ok';
        setConnDot('d');
        setVal('llm-model', d.model, '');
        setVal('llm-provider', provider, '');

        // Limpa a key do DOM por segurança (já enviada ao servidor)
        if (apiKey && keyEl) keyEl.value = '';

        // Reconecta WS para garantir runtime atualizado
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.close();
        }
        setTimeout(connectWS, 300);
      } else {
        let errMsg = 'falhou';
        try {
          const err = await r.json();
          errMsg = err.detail || JSON.stringify(err);
        } catch (e) {
          errMsg = await r.text();
        }
        console.error('[connect] erro HTTP ' + r.status + ': ' + errMsg);
        statusEl.textContent = 'Erro: ' + errMsg.substring(0, 100);
        statusEl.className = 'err';
        setConnDot('e');
      }
    } catch (e) {
      console.error('[connect] exception', e);
      statusEl.textContent = 'Erro: ' + e.message;
      statusEl.className = 'err';
    }
  }

  // ── WebSocket ─────────────────────────────────────────────────────────────
  function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN ||
               ws.readyState === WebSocket.CONNECTING)) {
      console.log('[WS] já existe (state=' + ws.readyState + ')');
      return;
    }
    if (wsConnecting) return;

    wsConnecting = true;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = proto + '://' + location.host + '/ws/chat/' + SESSION;
    console.log('[WS] conectando:', url);
    ws = new WebSocket(url);

    ws.onopen = function() {
      console.log('[WS] aberto');
      wsReady = true;
      wsConnecting = false;
    };

    ws.onmessage = function(ev) {
      let d;
      try { d = JSON.parse(ev.data); }
      catch (e) { console.error('[WS] JSON inválido:', ev.data); return; }
      console.log('[WS] msg:', d.type);

      if (d.type === 'heartbeat') {
        // v3.4: heartbeat para manter WS vivo — não loga
        return;
      } else if (d.type === 'start') {
        addMsg('assistant', '');
        setStage('Processando pipeline...');
        advanceFlow('pipeline');
        setFlow('request', 'done');
        setText('flow-sub', '\u2014 turno em curso');
        addEvent('turn.start', '', '');
      } else if (d.type === 'pipeline_done') {
        const ok = d.pipeline_ok ? '[OK]' : '[WARN]';
        setStage(ok + ' Pipeline | ' + d.compression_pct + '% | ' +
                 d.memory_hits + ' memorias');
        setFlow('pipeline', d.pipeline_ok ? 'done' : 'blocked');
        addEvent('pipeline', (d.memory_hits != null ? d.memory_hits + ' hits' : ''),
                 d.pipeline_ok ? 'ok' : 'warn');
        if (d.compression_pct != null || d.memory_hits != null) {
          addOpBlock('PIPELINE', [
            ['compressao', (d.compression_pct != null ? d.compression_pct + '%' : NA)],
            ['memorias',   (d.memory_hits != null ? String(d.memory_hits) : NA)],
          ]);
        }
      } else if (d.type === 'llm_start') {
        setStage('LLM ' + d.model + ' gerando...');
        advanceFlow('model');
        addEvent('llm.start', d.model || '', '');
        if (d.model) addOpBlock('MODEL', [['selecionado', d.model]]);
      } else if (d.type === 'chunk') {
        const last = el('chat-box').lastElementChild;
        if (last && last.classList.contains('assistant')) {
          last.querySelector('.bubble').textContent += d.text;
        } else {
          addMsg('assistant', d.text);
        }
        el('chat-box').scrollTop = el('chat-box').scrollHeight;
        setFlow('model', 'done');
        setFlow('stream', 'active');
      } else if (d.type === 'done') {
        el('send-btn').disabled = false;
        setStage(d.llm_used ? '[OK] Resposta gerada' :
                              '[OK] Resposta cognitiva (sem LLM)');
        if (d.metrics) {
          const m = d.metrics;
          if (m.avg_latency_ms != null) {
            setVal('llm-lat', Math.round(m.avg_latency_ms) + 'ms', '');
          }
          if (m.total_requests != null) setVal('llm-req', m.total_requests, '');
          if (m.avg_memory_hits != null) {
            setVal('llm-hits', m.avg_memory_hits.toFixed(1), '');
          }
          if (m.total_errors != null) {
            setVal('llm-err', m.total_errors, m.total_errors > 0 ? 'err' : 'ok');
          }
          // Novos campos da v3.3 com instrumentação completa
          if (m.avg_first_token_ms != null && m.avg_first_token_ms > 0) {
            console.log('[metrics] first_token=' + Math.round(m.avg_first_token_ms) + 'ms throughput=' + (m.avg_throughput_tps || 0).toFixed(2) + 'tps');
          }
        }
        setFlow('stream', 'done');
        setFlow('response', 'done');
        setText('flow-sub', '\u2014 turno concluido');
        addEvent('turn.done', (d.llm_used ? 'com LLM' : 'sem LLM'), 'ok');
        setTimeout(function() { setStage(''); }, 4000);
      } else if (d.type === 'warn') {
        const last = el('chat-box').lastElementChild;
        if (last && last.classList.contains('assistant')) {
          last.querySelector('.bubble').textContent +=
            String.fromCharCode(10) + '[!] ' + d.error + String.fromCharCode(10);
        } else {
          addMsg('assistant', '[!] ' + d.error);
        }
        addEvent('warn', String(d.error || '').slice(0, 60), 'warn');
      } else if (d.type === 'error') {
        addMsg('assistant', '[Erro]: ' + d.error);
        el('send-btn').disabled = false;
        setStage('[Erro]');
        const ativo = document.querySelector('.flow-node[data-state="active"]');
        if (ativo) setFlow(ativo.getAttribute('data-node'), 'error');
        setText('flow-sub', '\u2014 falha no turno');
        addEvent('error', String(d.error || '').slice(0, 60), 'err');
      }
    };

    ws.onerror = function(e) {
      console.error('[WS] erro', e);
      wsReady = false;
      wsConnecting = false;
    };

    ws.onclose = function() {
      console.log('[WS] fechado');
      wsReady = false;
      wsConnecting = false;
      el('send-btn').disabled = false;
    };
  }

  // ── HTTP fallback ─────────────────────────────────────────────────────────
  async function sendViaHTTP(text) {
    console.log('[HTTP] fallback');
    setStage('Enviando via HTTP (fallback)...');
    addMsg('assistant', '');
    try {
      const r = await fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: text, session_id: SESSION}),
      });
      const d = await r.json();
      if (r.ok) {
        const last = el('chat-box').lastElementChild;
        if (last && last.classList.contains('assistant')) {
          last.querySelector('.bubble').textContent = d.text || '[sem resposta]';
        }
        setStage('HTTP OK | ' + d.memory_hits + ' memorias | ' +
                 d.latency_ms + 'ms');
        if (d.latency_ms) setVal('llm-lat', Math.round(d.latency_ms) + 'ms', '');
      } else {
        addMsg('assistant', 'ERROR ' + (d.detail || 'erro HTTP ' + r.status));
        setStage('Erro HTTP');
      }
    } catch (e) {
      addMsg('assistant', 'ERROR Falha de rede: ' + e.message);
      setStage('Falha de rede');
    } finally {
      el('send-btn').disabled = false;
    }
  }

  // ── Send ──────────────────────────────────────────────────────────────────
  function sendMsg() {
    const input = el('chat-input');
    const text = input.value.trim();
    if (!text) return;

    addMsg('user', text);
    input.value = '';
    el('send-btn').disabled = true;
    resetFlow('waiting');
    setFlow('request', 'active');
    setText('flow-sub', '\u2014 enviando');
    setStage('Enviando...');

    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({message: text}));
        console.log('[WS] enviado');
      } catch (e) {
        console.error('[WS] send falhou', e);
        sendViaHTTP(text);
      }
      return;
    }

    if (ws && ws.readyState === WebSocket.CONNECTING) {
      setTimeout(function() {
        if (ws.readyState === WebSocket.OPEN) {
          try { ws.send(JSON.stringify({message: text})); }
          catch (e) { sendViaHTTP(text); }
        } else {
          sendViaHTTP(text);
        }
      }, 1000);
      return;
    }

    connectWS();
    setTimeout(function() {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({message: text})); }
        catch (e) { sendViaHTTP(text); }
      } else {
        sendViaHTTP(text);
      }
    }, 1500);
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function() {
    el('connect-btn').addEventListener('click', connectLLM);
    el('provider').addEventListener('change', updateProviderForm);
    // Inicializa form para o provider default (anthropic)
    updateProviderForm();
    el('send-btn').addEventListener('click', sendMsg);
    el('chat-input').addEventListener('keydown', function(ev) {
      if (ev.key === 'Enter') sendMsg();
    });

    refresh();
    setInterval(refresh, POLL_INTERVAL_MS);

    if (!window.__edpWsInit) {
      window.__edpWsInit = true;
      setTimeout(connectWS, 500);
    }

    console.log('[dashboard] inicializado | session=' + SESSION);
  });

})();

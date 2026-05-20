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
    if (data.pressure && data.pressure.level) {
      const level = data.pressure.level;
      if (level === 'critical') {
        console.error('[pressure] CRITICAL ram=' + data.pressure.available_gb + 'GB');
      } else if (level === 'warning') {
        console.warn('[pressure] WARNING ram=' + data.pressure.available_gb + 'GB');
      }
    }
    if (data.queue) {
      if (data.queue.queued > 0) {
        console.log('[queue] active=' + data.queue.active + ' queued=' + data.queue.queued);
      }
    }

    // Memory
    if (data.memory && !data.memory.error) {
      setVal('m-episodic', data.memory.episodic != null ? data.memory.episodic : '?', '');
      setVal('m-semantic', data.memory.semantic != null ? data.memory.semantic : '?', '');
      setVal('m-working',  data.memory.working  != null ? data.memory.working  : '?', '');
      setVal('m-total',    data.memory.total    != null ? data.memory.total    : '?', '');
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
    if (dot) dot.style.background = '#22c55e';
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
      } else if (d.type === 'pipeline_done') {
        const ok = d.pipeline_ok ? '[OK]' : '[WARN]';
        setStage(ok + ' Pipeline | ' + d.compression_pct + '% | ' +
                 d.memory_hits + ' memorias');
      } else if (d.type === 'llm_start') {
        setStage('LLM ' + d.model + ' gerando...');
      } else if (d.type === 'chunk') {
        const last = el('chat-box').lastElementChild;
        if (last && last.classList.contains('assistant')) {
          last.querySelector('.bubble').textContent += d.text;
        } else {
          addMsg('assistant', d.text);
        }
        el('chat-box').scrollTop = el('chat-box').scrollHeight;
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
        setTimeout(function() { setStage(''); }, 4000);
      } else if (d.type === 'warn') {
        const last = el('chat-box').lastElementChild;
        if (last && last.classList.contains('assistant')) {
          last.querySelector('.bubble').textContent +=
            String.fromCharCode(10) + '[!] ' + d.error + String.fromCharCode(10);
        } else {
          addMsg('assistant', '[!] ' + d.error);
        }
      } else if (d.type === 'error') {
        addMsg('assistant', '[Erro]: ' + d.error);
        el('send-btn').disabled = false;
        setStage('[Erro]');
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

"""
edp.api.routes.flags - Endpoints de review de meta-memoria.

Principio: instrumentacao de observacao, nao governanca.

Endpoints:
  GET  /flags                   - lista flags com filtro/paginacao
  GET  /flags/{flag_id}         - detalhes de 1 flag
  POST /flags/{flag_id}/feedback - registra veredicto humano
  GET  /flags/aggregates        - agregados (% util, % FP, etc.)
  GET  /flags/review            - UI HTML minima para review
"""
from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ._sidebar import SIDEBAR_CSS, SIDEBAR_JS, render_sidebar

logger = logging.getLogger("edp.api.flags")

router = APIRouter(tags=["flags"])


class FeedbackRequest(BaseModel):
    feedback: str = Field(
        ...,
        description="useful | false_positive | ambiguous | unreviewed",
    )
    note: Optional[str] = Field(
        default="",
        description="Nota opcional do operador (ate 500 chars)",
    )


@router.get("/flags")
async def list_flags(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    filter_feedback: Optional[str] = Query(
        None,
        description="useful | false_positive | ambiguous | unreviewed",
    ),
):
    """Lista contradiction flags ordenadas por detected_at desc."""
    try:
        from ...runtime.contradiction_flagger import get_flagger
        flags = get_flagger().list_flags(
            limit=limit, offset=offset, filter_feedback=filter_feedback,
        )
        return {
            "flags": flags,
            "count": len(flags),
            "limit": limit,
            "offset": offset,
            "filter": filter_feedback,
        }
    except Exception as e:
        raise HTTPException(500, f"erro listando flags: {e}")


@router.get("/flags/aggregates")
async def get_aggregates():
    """
    Agregados meta-cognitivos.

    Interpretacao:
      - useful_rate > 0.7  -> heuristica funcionando bem
      - useful_rate < 0.3  -> heuristica ruidosa, subir threshold
      - fp_avg_sim alto    -> ajustar pattern de negacao
    """
    try:
        from ...runtime.contradiction_flagger import get_flagger
        return get_flagger().aggregates()
    except Exception as e:
        raise HTTPException(500, f"erro: {e}")


# IMPORTANTE: /flags/review precisa vir ANTES de /flags/{flag_id}
# Senao "review" seria capturado como flag_id e daria 404.
@router.get("/flags/review", response_class=HTMLResponse)
async def flags_review_ui():
    """UI HTML minima para review."""
    html = (
        REVIEW_HTML
        .replace("<!--SIDEBAR_CSS-->", SIDEBAR_CSS)
        .replace("<!--SIDEBAR_HTML-->", render_sidebar(current_page="flags"))
        .replace("<!--SIDEBAR_JS-->", SIDEBAR_JS)
    )
    return HTMLResponse(content=html)


@router.get("/flags/{flag_id}")
async def get_flag(flag_id: str):
    """Detalhes de 1 flag."""
    try:
        from ...runtime.contradiction_flagger import get_flagger
        flag = get_flagger().get_flag(flag_id)
        if flag is None:
            raise HTTPException(404, f"flag '{flag_id}' nao encontrado")
        return flag
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"erro: {e}")


@router.post("/flags/{flag_id}/feedback")
async def post_feedback(flag_id: str, req: FeedbackRequest):
    """
    Registra veredicto humano.

    Valores validos: useful | false_positive | ambiguous | unreviewed
    """
    try:
        from ...runtime.contradiction_flagger import get_flagger, FlagFeedback

        if req.feedback not in FlagFeedback.ALL:
            raise HTTPException(
                400,
                f"feedback invalido. Valores: {FlagFeedback.ALL}",
            )

        ok = get_flagger().set_feedback(
            flag_id=flag_id,
            feedback=req.feedback,
            note=req.note or "",
        )
        if not ok:
            raise HTTPException(404, f"flag '{flag_id}' nao encontrado")

        return {
            "flag_id": flag_id,
            "feedback": req.feedback,
            "note": req.note or "",
            "ok": True,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"erro: {e}")


REVIEW_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EDP - Review de Flags</title>
<!--SIDEBAR_CSS-->
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background:#0f172a; color:#e2e8f0; margin:0; padding:0; }
.page-content { padding:20px; padding-top:64px; max-width:1200px; margin:0 auto; }
h1 { color:#f1f5f9; border-bottom:2px solid #334155; padding-bottom:10px; margin-top:0; }
.aggregates { background:#1e293b; padding:16px; border-radius:8px; margin-bottom:20px;
              display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
              gap:12px; }
.metric { background:#0f172a; padding:10px; border-radius:6px; }
.metric-label { font-size:11px; color:#94a3b8; text-transform:uppercase; }
.metric-value { font-size:22px; font-weight:bold; color:#22d3ee; margin-top:4px; }
.filters { margin-bottom:20px; display:flex; gap:8px; flex-wrap:wrap; }
.filters button { background:#1e293b; color:#e2e8f0; border:1px solid #334155;
                  padding:8px 14px; border-radius:6px; cursor:pointer; font-size:13px; }
.filters button.active { background:#0e7490; border-color:#06b6d4; }
.filters button:hover { background:#334155; }
.flag { background:#1e293b; padding:14px; border-radius:8px; margin-bottom:12px;
        border-left:3px solid #64748b; }
.flag.useful { border-left-color:#dc2626; }
.flag.false_positive { border-left-color:#16a34a; }
.flag.ambiguous { border-left-color:#ca8a04; }
.flag-head { display:flex; justify-content:space-between; font-size:12px;
             color:#94a3b8; margin-bottom:8px; }
.text-pair { display:grid; grid-template-columns: 1fr 1fr; gap:10px; margin-bottom:10px; }
.text-block { background:#0f172a; padding:8px; border-radius:4px; font-size:13px;
              color:#cbd5e1; white-space:pre-wrap; overflow-wrap:break-word; }
.label { color:#64748b; font-size:10px; margin-bottom:4px; text-transform:uppercase; }
.actions { display:flex; gap:6px; margin-top:10px; flex-wrap:wrap; }
.actions button { background:#334155; color:#e2e8f0; border:none; padding:6px 12px;
                  border-radius:4px; cursor:pointer; font-size:12px; }
.actions button:hover { background:#475569; }
.actions button.useful { background:#7f1d1d; }
.actions button.fp { background:#14532d; }
.actions button.ambig { background:#713f12; }
.actions button.current { outline:2px solid #06b6d4; }
textarea { width:100%; background:#0f172a; color:#e2e8f0; border:1px solid #334155;
           padding:6px; border-radius:4px; resize:vertical; min-height:40px;
           font-size:12px; margin-top:6px; }
.empty { text-align:center; padding:40px; color:#64748b; }
.feedback-status { font-size:11px; color:#94a3b8; }
.feedback-status.useful { color:#fca5a5; }
.feedback-status.false_positive { color:#86efac; }
.feedback-status.ambiguous { color:#fde68a; }
</style>
</head>
<body>
<!--SIDEBAR_HTML-->

<div class="page-content">
<h1>Review de Contradiction Flags</h1>

<div class="aggregates" id="aggregates">
  <div class="metric"><div class="metric-label">Carregando...</div></div>
</div>

<div class="filters">
  <button onclick="setFilter('all')" id="f-all" class="active">Todos</button>
  <button onclick="setFilter('unreviewed')" id="f-unreviewed">Nao revisados</button>
  <button onclick="setFilter('useful')" id="f-useful">Uteis</button>
  <button onclick="setFilter('false_positive')" id="f-false_positive">Falsos positivos</button>
  <button onclick="setFilter('ambiguous')" id="f-ambiguous">Ambiguos</button>
  <button onclick="loadAll()" style="margin-left:auto;background:#075985;">Atualizar</button>
</div>

<div id="flags-list">Carregando...</div>

<script>
var currentFilter = 'all';

function setFilter(f) {
  currentFilter = f;
  var btns = document.querySelectorAll('.filters button');
  for (var i = 0; i < btns.length; i++) btns[i].classList.remove('active');
  var el = document.getElementById('f-' + f);
  if (el) el.classList.add('active');
  loadFlags();
}

function escapeHtml(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function metricBox(label, val) {
  return '<div class="metric"><div class="metric-label">' + label +
         '</div><div class="metric-value">' + val + '</div></div>';
}

async function loadAggregates() {
  try {
    var r = await fetch('/flags/aggregates');
    var data = await r.json();
    var el = document.getElementById('aggregates');
    var html = '';
    html += metricBox('Total flags', data.total || 0);
    if (data.by_feedback) {
      html += metricBox('Nao revisados', data.by_feedback.unreviewed || 0);
      html += metricBox('Uteis', data.by_feedback.useful || 0);
      html += metricBox('Falsos positivos', data.by_feedback.false_positive || 0);
      html += metricBox('Ambiguos', data.by_feedback.ambiguous || 0);
    }
    html += metricBox('% Util', data.useful_rate != null ?
                       (data.useful_rate * 100).toFixed(1) + '%' : '--');
    html += metricBox('% Revisado', data.review_rate != null ?
                       (data.review_rate * 100).toFixed(1) + '%' : '--');
    html += metricBox('Sim media', data.avg_similarity != null ? data.avg_similarity : '--');
    html += metricBox('Sim uteis', data.useful_avg_sim != null ? data.useful_avg_sim : '--');
    html += metricBox('Sim FPs', data.fp_avg_sim != null ? data.fp_avg_sim : '--');
    if (data.oldest_unreviewed_age_days != null) {
      html += metricBox('Mais antigo (d)', data.oldest_unreviewed_age_days.toFixed(1));
    }
    el.innerHTML = html;
  } catch(e) { console.error('aggregates falhou', e); }
}

async function loadFlags() {
  try {
    var url = '/flags?limit=100';
    if (currentFilter !== 'all') url += '&filter_feedback=' + currentFilter;
    var r = await fetch(url);
    var data = await r.json();
    var list = document.getElementById('flags-list');
    if (!data.flags || data.flags.length === 0) {
      list.innerHTML = '<div class="empty">Nenhum flag para este filtro.</div>';
      return;
    }
    var html = '';
    for (var i = 0; i < data.flags.length; i++) {
      var f = data.flags[i];
      var fbClass = f.feedback || 'unreviewed';
      var date = new Date(f.detected_at * 1000).toLocaleString('pt-BR');
      var fbDate = f.feedback_at ? new Date(f.feedback_at * 1000).toLocaleString('pt-BR') : '';
      var noteId = 'note_' + f.flag_id;
      var actions = '';
      actions += '<button class="useful' + (f.feedback === 'useful' ? ' current' : '') +
                 '" onclick="setFeedback(\\'' + f.flag_id + '\\', \\'useful\\')">[OK] Util (real)</button>';
      actions += '<button class="fp' + (f.feedback === 'false_positive' ? ' current' : '') +
                 '" onclick="setFeedback(\\'' + f.flag_id + '\\', \\'false_positive\\')">[X] Falso positivo</button>';
      actions += '<button class="ambig' + (f.feedback === 'ambiguous' ? ' current' : '') +
                 '" onclick="setFeedback(\\'' + f.flag_id + '\\', \\'ambiguous\\')">[?] Ambiguo</button>';
      if (f.feedback !== 'unreviewed') {
        actions += '<button onclick="setFeedback(\\'' + f.flag_id + '\\', \\'unreviewed\\')">Reverter</button>';
      }
      html += '<div class="flag ' + fbClass + '" id="flag_' + f.flag_id + '">' +
              '<div class="flag-head">' +
                '<span><b>' + f.flag_id + '</b> | sim=' + f.similarity.toFixed(3) +
                ' | ' + date + '</span>' +
                '<span class="feedback-status ' + fbClass + '">' + fbClass +
                (fbDate ? ' | ' + fbDate : '') + '</span>' +
              '</div>' +
              '<div class="text-pair">' +
                '<div><div class="label">Memoria A | ' + f.id_a.slice(0,8) + '</div>' +
                '<div class="text-block">' + escapeHtml(f.text_a) + '</div></div>' +
                '<div><div class="label">Memoria B | ' + f.id_b.slice(0,8) + '</div>' +
                '<div class="text-block">' + escapeHtml(f.text_b) + '</div></div>' +
              '</div>' +
              (f.feedback_note ? '<div class="text-block" style="font-style:italic;margin-bottom:8px;">Nota: ' +
                escapeHtml(f.feedback_note) + '</div>' : '') +
              '<textarea id="' + noteId + '" placeholder="Nota opcional...">' +
                escapeHtml(f.feedback_note || '') + '</textarea>' +
              '<div class="actions">' + actions + '</div>' +
              '</div>';
    }
    list.innerHTML = html;
  } catch(e) {
    console.error('flags falhou', e);
    document.getElementById('flags-list').innerHTML = '<div class="empty">Erro: ' + e + '</div>';
  }
}

async function setFeedback(flagId, feedback) {
  var noteEl = document.getElementById('note_' + flagId);
  var note = noteEl ? noteEl.value : '';
  try {
    var r = await fetch('/flags/' + flagId + '/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feedback: feedback, note: note }),
    });
    if (!r.ok) throw new Error(await r.text());
    await loadAggregates();
    await loadFlags();
  } catch(e) {
    alert('erro: ' + e);
  }
}

function loadAll() {
  loadAggregates();
  loadFlags();
}

loadAll();
setInterval(loadAggregates, 30000);
</script>
</div>
<!--SIDEBAR_JS-->
</body>
</html>
"""


# (handler de /flags/review foi movido para antes de /flags/{flag_id})

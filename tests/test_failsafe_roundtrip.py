"""
tests/test_failsafe_roundtrip.py — ciclo backup → corrupção → restore
(Hardening Fase 3, T1).

Contrato REAL de edp/failsafe.py (os nomes usados no escopo original da
tarefa — backup_ep/restore_ep/detect_corruption/validate_ep — não batem
1:1 com o módulo; os nomes reais são):

    incremental_backup(memory_store) -> dict
        Copia episodic.json + semantic.json (via shutil.copy2) para
        <MEMORY_DIR>/<session_id>_backups/{session}_{layer}_{ts}.json.
        Poda para as 5 últimas (_prune_backups). NÃO valida o conteúdo
        antes de copiar (backup de arquivo corrompido é fielmente
        replicado — a validação acontece só na hora de RESTAURAR).

    restore_backup(memory_store, backup_timestamp=None) -> dict
        Acha o backup mais recente (ou o timestamp pedido) via
        glob("*_episodic_*.json"), valida cada arquivo de backup com
        validate_memory_json() ANTES de promovê-lo (guarda contra
        restaurar um backup também corrompido), copia por cima do
        arquivo live e recarrega (episodic._load() / semantic._load()).

    validate_memory_json(path) -> (bool, list[str])
        Valida UM arquivo específico no disco: JSON parseável + campos
        obrigatórios presentes em cada entry. É a função que de fato
        classifica um arquivo como corrompido/inválido — usada
        internamente por restore_backup() como guarda.

    detect_corruption(memory_store) -> dict
        Inspeciona memory_store.episodic/semantic.entries JÁ CARREGADOS
        EM MEMÓRIA (não relê o disco): flags anomalias de CONTEÚDO
        (sem_id, embedding_vazio, timestamp_futuro, texto_vazio). Não é
        um detector de corrupção estrutural de JSON — se o load do
        arquivo falhar, não há entries para inspecionar.

Bug real encontrado escrevendo este teste (corrigido nesta mesma tarefa,
edp/failsafe.py): incremental_backup() gravava os arquivos de backup
SEM o prefixo de sessão (f"{src.stem}_{ts}{src.suffix}" →
"episodic_<ts>.json"), mas restore_backup() só reconhece
"{session}_{layer}_{ts}.json" (glob "*_episodic_*.json", que não casa
com um nome que não tem "_episodic_" no meio — "episodic_123.json" não
contém esse substring). Resultado: restore_backup() SEMPRE retornava
{"ok": False, "reason": "Backup vazio"} mesmo logo após um backup bem
sucedido — restore estava silenciosamente não funcional. Fix: prefixar
o nome do arquivo de backup com o session_id.

Gap fechado pela Dívida #53 (docs/preregistro_fix_corrupcao_json.md,
04/08/2026) — histórico do que este módulo documentava antes:
_safe_load_json (edp/memory.py) só recuperava corrupção do tipo
"Extra data" (lixo IGUAL/DEPOIS de um array já fechado corretamente —
o cenário coberto por repair_episodic.py, tipicamente Ctrl+C durante
save PRÉ write-atômico). Truncamento GENUÍNO no meio de um objeto
(array nunca fecha) NÃO era recuperável por esse algoritmo — o
JSONDecodeError original propagava sem ser capturado por _load(), o que
quebrava a CONSTRUÇÃO INTEIRA do MemoryStore (EpisodicMemory.__init__
chama _load() sem try/except). Esse comportamento (crash) era
documentado por test_reload_apos_truncamento_no_meio_do_objeto_propaga_erro,
que ESTE COMMIT reescreve com o contrato novo — ver
test_reload_apos_truncamento_no_meio_do_objeto_quarentena_e_degrada
abaixo: o boot agora sobrevive, quarentena o arquivo original
(byte-idêntico, movido via os.replace) e degrada para vazio de forma
observável (evento Pareto "store_degraded" + logger.critical). Detalhe
completo do desenho e do critério de decisão em
docs/preregistro_fix_corrupcao_json.md.
"""
from __future__ import annotations

import json

import pytest

import edp.failsafe as failsafe


def _read_entries(path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _truncate_mid_object(path) -> None:
    """
    Corrompe o arquivo truncando genuinamente no meio de um objeto: o
    array JSON nunca fecha. Diferente de "Extra data" (lixo depois de um
    documento válido já fechado) — este é o modo de falha de um write
    interrompido a meio de uma entry (ex.: processo morto, disco cheio),
    o cenário que motivou o write atômico (tmp + fsync + rename) em
    _atomic_write_json.
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Corta dentro do valor de "text" da ÚLTIMA entry — garante que o
    # array nunca fecha e a última entry fica com string não-terminada.
    cut = content.rfind('"text"')
    assert cut > 0, "fixture não tem o formato esperado (campo 'text' ausente)"
    cut += len('"text": "')
    truncated = content[:cut] + "entry parcial sem fechar"
    with open(path, "w", encoding="utf-8") as f:
        f.write(truncated)


# ── Ciclo completo: backup → corrupção → validação → restore ──────────────────

def test_backup_corrupcao_restore_roundtrip(synthetic_store):
    store = synthetic_store
    store.add("Q: pergunta um\nA: resposta um", 0.6)
    store.add("Q: pergunta dois\nA: resposta dois", 0.7)
    store.save()

    original_entries = _read_entries(store.episodic.path)
    assert len(original_entries) == 2

    backup_result = failsafe.incremental_backup(store)
    assert backup_result["errors"] == []
    assert len(backup_result["backed_files"]) == 2

    _truncate_mid_object(store.episodic.path)

    # validate_memory_json é a função que de fato flagra o arquivo
    # corrompido no disco (detect_corruption não relê o disco — ver
    # docstring do módulo acima e o teste de gap abaixo).
    ok, errors = failsafe.validate_memory_json(str(store.episodic.path))
    assert ok is False
    assert any("inválido" in e for e in errors)

    restore_result = failsafe.restore_backup(store)
    assert restore_result["ok"] is True
    assert restore_result["errors"] == []
    assert len(restore_result["restored_files"]) == 2

    restored_entries = _read_entries(store.episodic.path)
    assert [e["id"] for e in restored_entries] == [e["id"] for e in original_entries]
    assert [e["text"] for e in restored_entries] == [e["text"] for e in original_entries]

    # restore_backup já recarrega o store em memória (_load()) — confere
    # que o objeto vivo reflete o conteúdo restaurado, não só o disco.
    assert [e["id"] for e in store.episodic.entries] == [e["id"] for e in original_entries]


def test_restore_backup_recusa_quando_nao_ha_backup(synthetic_store):
    """Sem incremental_backup() prévio, restore_backup() reporta o motivo
    em vez de tentar restaurar do nada."""
    result = failsafe.restore_backup(synthetic_store)
    assert result["ok"] is False
    assert "reason" in result


def test_restore_backup_recusa_backup_tambem_corrompido(synthetic_store):
    """Guarda interna de restore_backup(): valida o arquivo de BACKUP
    (via validate_memory_json) antes de promovê-lo. Se o próprio backup
    estiver corrompido, restore_backup() não sobrescreve o live file."""
    store = synthetic_store
    store.add("Q: pergunta\nA: resposta", 0.5)
    store.save()

    live_before = _read_entries(store.episodic.path)

    backup_result = failsafe.incremental_backup(store)
    backup_episodic_path = next(
        p for p in backup_result["backed_files"] if "episodic" in p
    )
    _truncate_mid_object(backup_episodic_path)

    restore_result = failsafe.restore_backup(store)
    assert restore_result["ok"] is False
    assert len(restore_result["errors"]) >= 1

    # Live file intocado — restore não promoveu o backup ruim.
    live_after = _read_entries(store.episodic.path)
    assert live_after == live_before


# ── Dívida #53: truncamento genuíno agora é quarentenado, não crasha ──────────

def test_reload_apos_truncamento_no_meio_do_objeto_quarentena_e_degrada(synthetic_store):
    """
    Reescrita de test_reload_apos_truncamento_no_meio_do_objeto_propaga_erro
    (renomeado — contrato antigo documentava um crash; ver
    docs/preregistro_fix_corrupcao_json.md, critério de decisão (a)/(b)/(c)).

    Contrato NOVO: truncamento genuíno no meio de um objeto (array nunca
    fecha, irrecuperável por _safe_load_json mesmo após a otimização do
    Passo 0.5) não derruba mais o reload. EpisodicMemory._load() agora
    passa por _load_json_or_quarantine (edp/memory/atomic_io.py):
      (a) não propaga JSONDecodeError — o reload completa;
      (b) o arquivo corrompido original é preservado, byte-idêntico, em
          "<path>.corrompido-<timestamp>" (os.replace atômico, nunca
          apagado);
      (c) store.episodic.entries fica vazio — degradação EXPLÍCITA
          (logger.critical + evento Pareto "store_degraded"), nunca
          confundida com sucesso silencioso.
    """
    store = synthetic_store
    store.add("Q: pergunta\nA: resposta", 0.5)
    store.save()

    original_entries_on_disk = _read_entries(store.episodic.path)
    assert len(original_entries_on_disk) == 1

    _truncate_mid_object(store.episodic.path)
    corrupted_bytes = store.episodic.path.read_bytes()

    # Simula o boot real (o cenário do bug original): uma CONSTRUÇÃO NOVA
    # de EpisodicMemory apontando pro mesmo path, não um _load() reaplicado
    # sobre um objeto já populado em memória (esse caminho tem uma
    # propriedade diferente e igualmente correta: data=None não sobrescreve
    # entries já carregadas — nunca piora um estado bom com um reload ruim;
    # não é o que este teste documenta). Antes do fix: isto propagava
    # json.JSONDecodeError na CONSTRUÇÃO (ver histórico do git deste
    # arquivo) — EpisodicMemory.__init__ chama _load() sem try/except.
    fresh = type(store.episodic)(store.episodic.session_id, scope=store.episodic.scope)

    assert fresh.entries == [], (
        "degradação esperada: entries vazio no boot, não uma reconstrução parcial"
    )

    quarantine_candidates = sorted(
        store.episodic.path.parent.glob(f"{store.episodic.path.name}.corrompido-*")
    )
    assert len(quarantine_candidates) == 1, (
        f"esperava exatamente 1 arquivo de quarentena, achei {quarantine_candidates}"
    )
    assert quarantine_candidates[0].read_bytes() == corrupted_bytes, (
        "arquivo de quarentena não é byte-idêntico ao conteúdo corrompido original"
    )

    from edp.runtime.pareto_store import get_pareto_store
    events = [
        e for e in get_pareto_store().query(event_type="store_degraded")
        if e.get("store_label") == "episodic"
    ]
    assert len(events) == 1, "evento store_degraded não emitido para o reload corrompido"


# ── detect_corruption(): anomalias de CONTEÚDO em entries já carregados ───────

def test_detect_corruption_flags_entries_com_problema(synthetic_store):
    """
    detect_corruption() não lê o disco — opera sobre entries já em
    memória. Testa o contrato real: injeta entries com problemas
    conhecidos (sem id, embedding vazio, texto vazio, timestamp futuro)
    diretamente na lista em memória e confere que cada um é flagrado.
    """
    import time

    store = synthetic_store
    future_ts = time.time() + 999_999

    store.episodic.entries = [
        {"id": "", "text": "ok mas sem id", "embedding": [0.1], "timestamp": 100},
        {"id": "b", "text": "ok mas sem embedding", "embedding": [], "timestamp": 100},
        {"id": "c", "text": "", "embedding": [0.1], "timestamp": 100},
        {"id": "d", "text": "timestamp no futuro", "embedding": [0.1], "timestamp": future_ts},
        {"id": "e", "text": "entry saudável", "embedding": [0.1], "timestamp": 100},
    ]

    result = failsafe.detect_corruption(store)

    assert result["is_healthy"] is False
    issue_types = {(i["id"], i["issue"]) for i in result["issues"]}
    assert ("b", "embedding_vazio") in issue_types
    assert ("c", "texto_vazio") in issue_types
    assert ("d", "timestamp_futuro") in issue_types
    assert not any(i["id"] == "e" for i in result["issues"])


def test_detect_corruption_saudavel_sem_issues(synthetic_store):
    store = synthetic_store
    store.add("Q: pergunta saudável\nA: resposta saudável", 0.5)
    store.save()

    result = failsafe.detect_corruption(store)
    assert result["is_healthy"] is True
    assert result["issues"] == []

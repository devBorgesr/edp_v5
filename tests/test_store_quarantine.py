"""
tests/test_store_quarantine.py — Dívida #53
(docs/preregistro_fix_corrupcao_json.md).

Testa o comportamento uniforme de
edp.memory.atomic_io::_load_json_or_quarantine nos 6 call sites migrados.
Ordem obrigatória do pré-registro: EpisodicMemory (store.py) primeiro,
sozinho, com testes completos — as classes abaixo desta seção só entram
depois que a primeira passa (ver histórico do commit).

Critério de decisão (pré-registro):
  (a) boot não crasha com JSON truncado no meio, por store, individualmente;
  (b) arquivo de quarentena byte-idêntico ao corrompido original;
  (c) sinal de observabilidade (evento Pareto "store_degraded") verificável
      por asserção, não por log visual;
  arquivo válido permanece intocado — teste negativo, prova de que não há
  falso positivo de quarentena.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from edp.memory.atomic_io import (
    MAX_RECOVERY_CANDIDATES,
    _load_json_or_quarantine,
    _safe_load_json,
)

# _reset_pareto_store_singleton (tests/conftest.py) é autouse — garante que
# get_pareto_store() neste arquivo sempre vê um singleton fresco, vinculado
# ao EDP_BASE_DIR isolado do teste atual (isolated_base_dir).


def _truncate_mid_structure(path: Path, ratio: float = 0.6) -> bytes:
    """
    Trunca genuinamente no meio da estrutura: corta em `ratio` do arquivo,
    SEM alcançar o(s) colchete(s)/chave(s) que fecham o container mais
    externo (que ficam nos últimos bytes de um dump com indent=2). Nenhum
    prefixo do resultado parseia como JSON válido — o container externo
    nunca fecha, então mesmo cortar logo após uma entry completa deixa o
    array/dict externo aberto. Diferente de "lixo depois de JSON válido"
    (o caso que _safe_load_json já resolvia antes desta dívida).

    Retorna os bytes truncados (para comparação byte-a-byte com o arquivo
    de quarentena depois do boot).
    """
    content = path.read_bytes()
    cut = int(len(content) * ratio)
    truncated = content[:cut]
    path.write_bytes(truncated)
    return truncated


def _quarantine_siblings(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.corrompido-*"))


# ═══════════════════════════════════════════════════════════════════════════
# 1) store.py :: EpisodicMemory — migrado sozinho primeiro (ordem do
#    pré-registro). Cobertura completa: boot, quarentena byte-idêntica,
#    observabilidade, arquivo válido intocado.
# ═══════════════════════════════════════════════════════════════════════════

class TestEpisodicMemoryQuarantine:
    def test_boot_sobrevive_a_truncamento_no_meio(self, isolated_base_dir):
        import edp.memory.store as store_mod

        em = store_mod.EpisodicMemory("sess-a")
        em.entries = [
            {"id": "1", "text": "entry um", "embedding": [0.1, 0.2],
             "timestamp": 1.0, "answer_class": None},
            {"id": "2", "text": "entry dois", "embedding": [0.3, 0.4],
             "timestamp": 2.0, "answer_class": None},
        ]
        em.save()

        _truncate_mid_structure(em.path)

        # Antes do fix: EpisodicMemory("sess-a") aqui derrubava o processo
        # inteiro (JSONDecodeError propagado por _load(), chamado sem
        # try/except em __init__). Critério (a): não crasha.
        em2 = store_mod.EpisodicMemory("sess-a")
        assert em2.entries == []  # degradação explícita, não sucesso

    def test_arquivo_de_quarentena_byte_identico(self, isolated_base_dir):
        import edp.memory.store as store_mod

        em = store_mod.EpisodicMemory("sess-b")
        em.entries = [
            {"id": "1", "text": "entry um", "embedding": [0.1],
             "timestamp": 1.0, "answer_class": None},
            {"id": "2", "text": "entry dois", "embedding": [0.2],
             "timestamp": 2.0, "answer_class": None},
        ]
        em.save()

        corrupted_bytes = _truncate_mid_structure(em.path)

        store_mod.EpisodicMemory("sess-b")  # dispara a quarentena no boot

        siblings = _quarantine_siblings(em.path)
        assert len(siblings) == 1, f"esperava 1 arquivo de quarentena, achei {siblings}"
        assert siblings[0].read_bytes() == corrupted_bytes, (
            "arquivo de quarentena não é byte-idêntico ao conteúdo corrompido original"
        )

    def test_sinal_de_observabilidade_verificavel_por_assercao(self, isolated_base_dir):
        import edp.memory.store as store_mod
        from edp.runtime.pareto_store import get_pareto_store

        em = store_mod.EpisodicMemory("sess-c")
        em.entries = [{"id": "1", "text": "x", "embedding": [0.1],
                       "timestamp": 1.0, "answer_class": None}]
        em.save()
        _truncate_mid_structure(em.path)

        store_mod.EpisodicMemory("sess-c")

        events = list(get_pareto_store().query(event_type="store_degraded"))
        assert len(events) == 1, "evento store_degraded não emitido (ou emitido mais de uma vez)"
        evt = events[0]
        assert evt["store_label"] == "episodic"
        assert evt["path"].endswith("episodic.json")
        assert evt["quarantine_path"] is not None
        assert evt["error_type"] == "JSONDecodeError"

    def test_arquivo_valido_permanece_intocado(self, isolated_base_dir):
        """Teste negativo: sem corrupção, nenhuma quarentena é criada —
        prova de que não há falso positivo."""
        import edp.memory.store as store_mod
        from edp.runtime.pareto_store import get_pareto_store

        em = store_mod.EpisodicMemory("sess-d")
        em.entries = [{"id": "1", "text": "entry saudável", "embedding": [0.1],
                       "timestamp": 1.0, "answer_class": None}]
        em.save()
        original_bytes = em.path.read_bytes()

        em2 = store_mod.EpisodicMemory("sess-d")

        # Compara campos estáveis (não "embedding": _deserialize converte
        # list->np.ndarray no reload, então == direto entre entries falha
        # por tipo, não por conteúdo — irrelevante para este teste).
        assert [e["id"] for e in em2.entries] == [e["id"] for e in em.entries]
        assert [e["text"] for e in em2.entries] == [e["text"] for e in em.entries]
        assert em.path.read_bytes() == original_bytes
        assert _quarantine_siblings(em.path) == []
        assert list(get_pareto_store().query(event_type="store_degraded")) == []

    def test_flag_off_restaura_comportamento_pre_fix(self, isolated_base_dir, monkeypatch):
        """EDP_STORE_QUARANTINE=0 é a válvula de rollback: repropaga a
        exceção original, sem quarentena — comportamento idêntico ao que
        existia antes desta dívida."""
        import edp.config as edp_config
        monkeypatch.setattr(edp_config, "EDP_STORE_QUARANTINE", False, raising=False)

        import edp.memory.store as store_mod

        em = store_mod.EpisodicMemory("sess-e")
        em.entries = [{"id": "1", "text": "x", "embedding": [0.1],
                       "timestamp": 1.0, "answer_class": None}]
        em.save()
        _truncate_mid_structure(em.path)

        with pytest.raises(json.JSONDecodeError):
            store_mod.EpisodicMemory("sess-e")

        assert _quarantine_siblings(em.path) == []


# ═══════════════════════════════════════════════════════════════════════════
# 2) _safe_load_json — Passo 0.5: cap de candidatos muda o que é
#    recuperável (risco a priori #4 do pré-registro) + performance.
# ═══════════════════════════════════════════════════════════════════════════

class TestCapDeCandidatosPasso05:
    def test_cap_de_candidatos_muda_o_que_e_recuperavel(self, tmp_path):
        """
        Documenta o risco a priori #4 do pré-registro: um arquivo com MAIS
        candidatos soltos na cauda do que MAX_RECOVERY_CANDIDATES é
        recuperável em princípio (o prefixo correto PARSEIA) mas o cap faz
        _safe_load_json desistir antes de alcançá-lo — degrada para
        quarentena em vez de recuperar. Mudança de comportamento
        deliberada (ver pré-registro, seção "N — número de candidatos"),
        não bug: nenhum cenário realista deste codebase (write path de
        _atomic_write_json) produz essa cauda.

        Cada repetição de `{"lixo": "x"}` é plana (1 único '}' cada, sem
        aninhamento) — contribui exatamente 1 posição de candidato. A
        varredura anda de trás pra frente; todas as `n_stray` repetições
        falham (concatenação de múltiplos valores JSON = "Extra data"),
        e só a posição bem no fim do array válido (ANTES de toda a cauda)
        parseia sozinha.
        """
        valid = json.dumps([{"a": 1}, {"a": 2}])
        n_stray = MAX_RECOVERY_CANDIDATES + 5  # empurra o candidato certo p/ além do cap
        content = valid + ('{"lixo": "x"}' * n_stray)

        # Prova de que É recuperável em princípio (não é lixo aleatório
        # sem solução — o candidato correto existe e parseia).
        assert json.loads(content[:len(valid)]) == [{"a": 1}, {"a": 2}]
        # Prova de que esse candidato está além do orçamento de tentativas:
        # entre o fim do arquivo e o corte correto há exatamente n_stray
        # candidatos (um '}' por repetição de lixo), todos ANTES dele na
        # ordem de varredura (de trás pra frente).
        assert n_stray > MAX_RECOVERY_CANDIDATES

        p = tmp_path / "cauda_longa.json"
        p.write_text(content, encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            _safe_load_json(p)

    def test_cap_de_candidatos_nao_afeta_caso_feliz_lixo_pequeno(self, tmp_path):
        """Controle: cauda de lixo PEQUENA (dentro do cap) continua
        recuperável exatamente como antes — nenhuma regressão no caso
        original que _safe_load_json já resolvia (Extra data)."""
        valid_list = [{"a": 1}, {"a": 2}]
        content = json.dumps(valid_list) + "\ngarbage-nao-json-sem-chaves"
        p = tmp_path / "trailing_garbage.json"
        p.write_text(content, encoding="utf-8")

        data = _safe_load_json(p)
        assert data == valid_list

    def test_performance_recuperacao_dentro_do_limite(self, tmp_path):
        """
        Passo 0.5: recuperação sobre arquivo GRANDE e genuinamente
        irrecuperável fica dentro do limite X declarado no pré-registro
        (20s em 10MB). Usa arquivo menor (~1MB) com o MESMO
        MAX_RECOVERY_CANDIDATES (o fator limitante é o número de
        tentativas, não o tamanho do arquivo, uma vez que o cap está
        ativo — ver pré-registro, seção de testes) e um limite escalado
        para o tamanho menor, para não deixar a suíte lenta.
        """
        # ~1MB de entries com embedding 384-dim — mesmo formato usado na
        # medição do pré-registro, escala menor.
        entries = [
            {
                "id": f"e{i}", "text": "texto de exemplo " * 5,
                "embedding": [0.123456] * 384, "timestamp": float(i),
                "answer_class": None,
            }
            for i in range(230)
        ]
        full = json.dumps(entries, indent=2)
        cut = int(len(full) * 0.7)
        # Garante que o corte cai dentro do array de embedding (nunca em
        # ']'/'}' por acidente) — pior caso para a recuperação.
        while full[cut] in "]},\n ":
            cut += 1
        truncated = full[:cut]

        p = tmp_path / "big_truncated.json"
        p.write_text(truncated, encoding="utf-8")
        assert p.stat().st_size > 0.8 * 1024 * 1024, "fixture menor que o esperado"

        t0 = time.perf_counter()
        with pytest.raises(json.JSONDecodeError):
            _safe_load_json(p)
        elapsed = time.perf_counter() - t0

        # Limite escalado: X=20s foi medido/declarado para 10MB no
        # pré-registro; este arquivo é ~1/10 do tamanho, então cada uma
        # das MAX_RECOVERY_CANDIDATES tentativas de parse é ~1/10 mais
        # barata — orçamento generoso de 5s deixa margem sem deixar a
        # suíte lenta se algo regredir.
        assert elapsed < 5.0, f"recuperação levou {elapsed:.2f}s, esperado < 5s (arquivo ~1MB, N={MAX_RECOVERY_CANDIDATES})"

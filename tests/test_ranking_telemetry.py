"""
test_ranking_telemetry.py — telemetria da seleção de memórias (13/08/2026).

Quatro cortes decidem o que chega ao prompt e três eram invisíveis. O que o
sistema reportava — `memory_hits` — é o ÚLTIMO dos quatro, e o único que não
explica nada.

Segue o padrão de `test_exp017_dedup_ranked.py`: testa o emissor puro em vez de
dirigir `retrieve()`, que exigiria modelo de embedding e tornaria a suíte lenta.
O que o caminho pesado provaria — que a emissão está ligada — é coberto por
guarda de fonte, mesma técnica de `test_validate_passa_telemetria_false`.
"""
from __future__ import annotations

import inspect

import pytest

import edp.config as edp_config
from edp.runtime.pareto_store import (
    EVENT_TYPES,
    emit_ranking_decision,
    get_pareto_store,
)

# Os DEZ fatores multiplicativos do rank_score (store.py:611-616).
# `nf_floor` entrou no dict em 13/08 — sempre esteve no produto e era o único
# que não ficava registrado.
FATORES = (
    "sim", "decay", "prio", "access_boost", "epi_mult",
    "src_weight", "dom_penalty", "anchor_boost", "session_boost", "nf_floor",
)


@pytest.fixture
def flag(monkeypatch):
    def _set(ligada: bool):
        monkeypatch.setattr(edp_config, "EDP_RANKING_TELEMETRY", ligada,
                            raising=False)
    return _set


def eventos():
    return list(get_pareto_store().query(event_type="ranking_decision"))


def emite(**kw):
    base = dict(n_avaliadas=594, n_acima_do_piso=42, n_apos_filtro_sessao=18,
                n_apos_filtro_recusa=15, n_entregues=10,
                min_score=0.20, top_k=10,
                detalhe=[{"rank": 1, "score": 0.83,
                          "fatores": {f: 1.0 for f in FATORES}}])
    base.update(kw)
    emit_ranking_decision(**base)


# ── Contrato com o event store ───────────────────────────────────────────────

def test_tipo_de_evento_registrado():
    """Sem registro, `emit()` descarta com warning e a telemetria fica muda."""
    assert "ranking_decision" in EVENT_TYPES


# ── A cascata ────────────────────────────────────────────────────────────────

def test_cascata_completa_e_gravada(isolated_base_dir):
    """
    Os cinco números, não só o último. `memory_hits` sozinho responde "quantas
    vieram" e nada sobre "de quantas" — que é a pergunta que explica a escolha.
    """
    emite()
    e = eventos()[0]
    assert e["n_avaliadas"] == 594
    assert e["n_acima_do_piso"] == 42
    assert e["n_apos_filtro_sessao"] == 18
    assert e["n_apos_filtro_recusa"] == 15
    assert e["n_entregues"] == 10


def test_cascata_e_monotonica_decrescente(isolated_base_dir):
    """
    Invariante estrutural: cada corte só pode remover. Se algum estágio
    reportar mais que o anterior, a instrumentação está lendo o contador
    errado — e o número pareceria plausível.
    """
    emite()
    e = eventos()[0]
    seq = [e["n_avaliadas"], e["n_acima_do_piso"], e["n_apos_filtro_sessao"],
           e["n_apos_filtro_recusa"], e["n_entregues"]]
    assert seq == sorted(seq, reverse=True)


# ── Os dez fatores ───────────────────────────────────────────────────────────

def test_os_dez_fatores_chegam_no_detalhe(isolated_base_dir):
    emite()
    assert set(eventos()[0]["detalhe"][0]["fatores"]) == set(FATORES)


def test_nf_floor_esta_no_dict_do_ranking():
    """
    REGRESSÃO 13/08: `nf_floor` SEMPRE esteve no produto do `rank_score`
    (store.py:614) e NÃO estava no dict guardado — era o único dos dez fora do
    registro, e justamente o que implementa a governança epistêmica do
    exp012/exp016 (`NOT_FOUND_FLOOR=0.05`, derruba o score em 20× ao disparar).

    Este teste trava os dez juntos: quem adicionar um fator ao produto e
    esquecer o dict quebra o build, em vez de criar outro fator invisível.
    """
    from edp.memory import store as _store
    fonte = inspect.getsource(_store.EpisodicMemory.retrieve)
    trecho = fonte[fonte.index("rank_score = round("):fonte.index("scored.sort")]
    for f in FATORES:
        assert f'"{f}"' in trecho, f"fator {f} saiu do dict do ranking"
        assert f in trecho, f"fator {f} saiu do produto do rank_score"


# ── Flag OFF ─────────────────────────────────────────────────────────────────

def test_flag_off_nao_emite(flag, isolated_base_dir):
    flag(False)
    from edp.memory import store as _store
    fonte = inspect.getsource(_store.EpisodicMemory.retrieve)
    # o gate lê a flag ANTES de montar o detalhe — se lesse depois, o custo de
    # serializar 20 breakdowns seria pago em todo turno com a coleta desligada
    i_gate = fonte.index("EDP_RANKING_TELEMETRY")
    i_detalhe = fonte.index("emit_ranking_decision")
    assert i_gate < i_detalhe


def test_flag_off_no_emissor_nao_grava(flag, isolated_base_dir):
    """
    O emissor não checa a flag — quem checa é o call site, para não pagar a
    montagem do detalhe. Este teste documenta essa divisão em vez de deixá-la
    implícita: chamar o emissor direto SEMPRE grava.
    """
    flag(False)
    emite()
    assert len(eventos()) == 1


# ── Volume ───────────────────────────────────────────────────────────────────

def test_detalhe_e_limitado_no_call_site():
    """
    594 episódicas × ~200 bytes por turno encheria a rotação de 10MB em poucos
    dias e afogaria o sinal. A resposta de "por que esta e não aquela" mora na
    fronteira do corte, não na cauda.
    """
    from edp.memory import store as _store
    fonte = inspect.getsource(_store.EpisodicMemory.retrieve)
    assert "scored[:20]" in fonte


# ── Telemetria nunca derruba caminho vivo ───────────────────────────────────

def test_falha_do_store_nao_propaga(isolated_base_dir, monkeypatch):
    import edp.runtime.pareto_store as ps

    def explode():
        raise OSError("disco cheio")

    monkeypatch.setattr(ps, "get_pareto_store", explode)
    emite()  # não levanta


def test_detalhe_nao_serializavel_nao_propaga(isolated_base_dir):
    """Objeto que não vira JSON não pode derrubar um retrieve."""
    emite(detalhe=[{"rank": 1, "fatores": object()}])


# ── Não contamina a coleta da Fase 2 ────────────────────────────────────────

def test_flag_e_classificada_como_nao_afetando_o_prompt():
    """
    `EDP_RANKING_TELEMETRY` não pode entrar no `format_hash`: é leitura da
    seleção, não mudança de composição. Se entrasse, ligá-la no meio da coleta
    trocaria o regime de todas as amostras seguintes e partiria o dataset em
    dois estratos por um motivo que não existe.
    """
    assert "EDP_RANKING_TELEMETRY" in edp_config.FORMAT_STATE_FLAGS_IGNORADAS
    assert "EDP_RANKING_TELEMETRY" not in edp_config.FORMAT_STATE_FLAGS


def test_ranking_decision_nao_entra_na_populacao_de_tokens(isolated_base_dir):
    """Evento de outro tipo no mesmo arquivo não pode virar amostra da Fase 2."""
    from edp.runtime.pareto_store import amostra_valida_fase2
    emite()
    assert not amostra_valida_fase2(eventos()[0])

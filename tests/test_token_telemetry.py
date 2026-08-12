"""
test_token_telemetry.py — Fase 1 da calibração de tokens (12/08/2026).

Especificação congelada: `AUDITORIA_FASE1_TOKENS.md §5`.

A Fase 1 grava o par (chars enviados, tokens REAIS cobrados) para substituir o
`4 chars ≈ 1 token` de `runtime/context_window_manager.py:12-13`, que nunca foi
medido. Ela NÃO muda prompt, resposta, ranking nem custo — e é exatamente isso
que a maior parte destes testes prova.

Nenhum teste toca a rede. A suíte do projeto é 100% sintética (`README.md §4`);
um teste que chamasse a API real custaria dinheiro por execução, seria
não-determinístico e quebraria no CI, que não tem chave.

`_reset_pareto_store_singleton` (conftest.py) é autouse e `isolated_base_dir`
vincula o store a `tmp_path` — nenhum evento destes testes cai em produção.
"""
from __future__ import annotations

import types

import pytest

import edp.config as edp_config
from edp.llm.providers.anthropic import AnthropicProvider
from edp.runtime.pareto_store import (
    EVENT_TYPES,
    classificar_conteudo,
    emit_token_usage,
    get_pareto_store,
)

USAGE_OK = {"input_tokens": 1200, "output_tokens": 340}


@pytest.fixture
def flag(monkeypatch):
    """Liga/desliga EDP_TOKEN_TELEMETRY.

    `monkeypatch.setattr` no módulo basta (não precisa de reload): tanto
    `emit_token_usage` quanto `_telemetria_tokens` importam a flag DENTRO da
    função, então cada chamada relê o atributo do módulo.
    """
    def _set(ligada: bool):
        monkeypatch.setattr(edp_config, "EDP_TOKEN_TELEMETRY", ligada,
                            raising=False)
    return _set


def eventos():
    return list(get_pareto_store().query(event_type="token_usage"))


def emite(**kw):
    base = dict(model="claude-sonnet-4-6", modo="complete", usage=dict(USAGE_OK),
                text_chars=4800, system_chars=1200, payload_bytes=5100,
                n_messages=3, amostra_texto="texto de teste")
    base.update(kw)
    emit_token_usage(**base)


# ── Contrato com o event store ───────────────────────────────────────────────

def test_tipo_de_evento_registrado():
    """
    REGRESSÃO: `FileParetoStore.emit` valida contra `EVENT_TYPES` e DESCARTA
    tipo desconhecido — com warning, sem erro. Se alguém adicionar um emissor e
    esquecer de registrar o tipo, a telemetria fica muda e os testes de emissão
    falhariam por um motivo que não é o real. Este teste nomeia a causa.
    """
    assert "token_usage" in EVENT_TYPES


# ── Flag OFF: nada acontece ──────────────────────────────────────────────────

def test_flag_off_nao_emite_nada(flag, isolated_base_dir):
    flag(False)
    emite()
    assert eventos() == []


def test_flag_off_nao_percorre_o_prompt(flag):
    """
    O gate da flag tem de vir ANTES da medição, não depois — senão o custo de
    percorrer o prompt seria pago em toda chamada mesmo com a coleta desligada.

    Prova: sabota `_medir_prompt` para explodir. Com a flag OFF nada explode,
    porque a medição nunca é alcançada.
    """
    flag(False)

    class _Prov:
        @staticmethod
        def _medir_prompt(payload):
            raise AssertionError("mediu o prompt com a flag OFF")

    req = types.SimpleNamespace(data=b"{}")
    AnthropicProvider._telemetria_tokens(
        _Prov(), {"messages": []}, req, dict(USAGE_OK), "complete", "m",
    )


# ── Flag ON: grava o par, e grava o que a Fase 2 vai precisar ────────────────

def test_flag_on_emite_o_par(flag, isolated_base_dir):
    flag(True)
    emite()
    evs = eventos()
    assert len(evs) == 1
    e = evs[0]
    assert e["usage"]["input_tokens"] == 1200
    assert e["text_chars"] == 4800
    assert e["payload_bytes"] == 5100
    assert e["n_messages"] == 3
    assert e["model"] == "claude-sonnet-4-6"
    assert e["modo"] == "complete"


def test_grava_clock_verified(flag, isolated_base_dir):
    """
    `ts` vem de `edp.clock`, não de `datetime.utcnow()` — a Peça 0.1 existe
    para isso. Mas relógio em fallback grava timestamp que pode ter desvio, e
    sem este campo não haveria como separar amostra confiável de amostra em
    fallback depois.
    """
    flag(True)
    emite()
    assert isinstance(eventos()[0]["clock_verified"], bool)


def test_usage_vai_verbatim_com_campos_de_cache(flag, isolated_base_dir):
    """
    O motivo de gravar `usage` inteiro em vez de dois inteiros: se prompt
    caching for ligado um dia, chamada cacheada tem relação chars→tokens
    COMPLETAMENTE diferente da sem cache. Gravando só `input_tokens`, toda
    amostra posterior ficaria contaminada e indistinguível das limpas — o
    dataset inteiro viraria suspeito retroativamente.
    """
    flag(True)
    emite(usage={**USAGE_OK,
                 "cache_read_input_tokens": 900,
                 "cache_creation_input_tokens": 300})
    e = eventos()[0]
    assert e["usage"]["cache_read_input_tokens"] == 900
    assert e["usage"]["cache_creation_input_tokens"] == 300


def test_duas_medidas_de_chars_coexistem(flag, isolated_base_dir):
    """
    `text_chars` (o que a API cobra) e `payload_bytes` (o que vai no fio, com
    andaime JSON) são numeradores diferentes e ambos defensáveis. Escolher um
    às cegas produziria uma razão com aparência de medida; a Fase 2 calcula das
    duas formas e decide com dado. `n_messages` vai junto porque o andaime
    escala com ele.
    """
    flag(True)
    emite()
    e = eventos()[0]
    assert e["text_chars"] != e["payload_bytes"]
    assert e["n_messages"] == 3


# ── Amostra incompleta é DESCARTADA, não completada com zero ────────────────

@pytest.mark.parametrize("usage", [
    {"output_tokens": 10},                              # input ausente
    {"input_tokens": 10},                               # output ausente
    {"input_tokens": None, "output_tokens": 10},        # explicitamente None
    {"input_tokens": 10, "output_tokens": None},
    None,                                               # sem usage
    "nao-e-dict",
])
def test_amostra_incompleta_e_descartada(flag, isolated_base_dir, usage):
    """
    No streaming os tokens chegam em eventos SSE distintos (`message_start` e
    `message_delta`) e qualquer um pode faltar. Gravar ausência como 0
    injetaria par falso — uma amostra dizendo "4800 chars custaram 0 tokens"
    envenenaria a razão. Uma amostra a menos é o custo certo.
    """
    flag(True)
    emite(usage=usage)
    assert eventos() == []


# ── Telemetria nunca derruba caminho vivo ───────────────────────────────────

def test_falha_do_store_nao_propaga(flag, isolated_base_dir, monkeypatch):
    """
    Contrato de todo emissor do projeto (`emit_task_started` etc.): disco
    cheio ou permissão negada não pode derrubar uma resposta ao usuário só
    para gravar telemetria.
    """
    flag(True)
    import edp.runtime.pareto_store as ps

    def explode():
        raise OSError("disco cheio")

    monkeypatch.setattr(ps, "get_pareto_store", explode)
    emite()  # não levanta


def test_falha_na_telemetria_nao_propaga_no_provider(flag, monkeypatch):
    """Mesma garantia, um nível acima — do lado do provider."""
    flag(True)
    import edp.runtime.pareto_store as ps

    def explode(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(ps, "emit_token_usage", explode)
    req = types.SimpleNamespace(data=b"{}")
    AnthropicProvider._telemetria_tokens(
        AnthropicProvider.__new__(AnthropicProvider),
        {"messages": [{"role": "user", "content": "oi"}]},
        req, dict(USAGE_OK), "complete", "m",
    )


# ── Classificação de conteúdo ───────────────────────────────────────────────

def test_classifica_codigo_por_cerca():
    r = classificar_conteudo("veja:\n```python\nx = 1\n```\nfim")
    assert r["classe"] == "codigo"


def test_classifica_prosa_acentuada():
    r = classificar_conteudo("A âncora de tarefa é injetada na camada de "
                             "contexto antes da janela imediata.")
    assert r["classe"] == "acentuado"


def test_classifica_prosa_ascii():
    r = classificar_conteudo("the quick brown fox jumps over the lazy dog")
    assert r["classe"] == "ascii"


def test_sinais_crus_permitem_reparticionar():
    """
    Os limiares da classificação são escolha minha, sem medição por trás. Isso
    só é inofensivo porque os sinais CRUS vão gravados junto: a Fase 2
    re-particiona com outros limiares sem recoletar. O que seria irreversível é
    não gravar os sinais.
    """
    r = classificar_conteudo("função: cálculo de índice {x}")
    assert set(r["sinais"]) == {"n", "nao_ascii", "cercas", "simbolos"}
    assert r["sinais"]["nao_ascii"] > 0


def test_classificacao_e_deterministica():
    txt = "misto: função `f(x)` com acentuação e símbolos {}[]"
    assert classificar_conteudo(txt) == classificar_conteudo(txt)


def test_texto_vazio_nao_quebra():
    r = classificar_conteudo("")
    assert r["classe"] == "vazio"
    assert r["sinais"]["n"] == 0


# ── _medir_prompt: mede o payload, não o request ────────────────────────────

def test_medir_prompt_conta_system_separado():
    payload = {"system": "S" * 100,
               "messages": [{"role": "user", "content": "U" * 50}]}
    text_chars, system_chars, n_msgs, texto = AnthropicProvider._medir_prompt(payload)
    assert system_chars == 100
    assert n_msgs == 1
    # system + "\n" + user
    assert text_chars == 151
    assert texto.startswith("S") and texto.endswith("U")


def test_medir_prompt_nao_conta_andaime_json():
    """
    O numerador honesto é o texto que a API tokeniza, não a serialização.
    `"model"`, `"role"`, chaves e aspas vão no fio (e entram em
    `payload_bytes`), mas não são cobrados como conteúdo.
    """
    payload = {"model": "claude-sonnet-4-6", "max_tokens": 4096,
               "messages": [{"role": "user", "content": "abc"}]}
    text_chars, system_chars, _, _ = AnthropicProvider._medir_prompt(payload)
    assert text_chars == 3
    assert system_chars == 0


def test_medir_prompt_aceita_conteudo_multimodal():
    payload = {"messages": [
        {"role": "user", "content": [{"type": "text", "text": "abcd"},
                                     {"type": "image", "source": {}}]},
    ]}
    text_chars, _, n_msgs, _ = AnthropicProvider._medir_prompt(payload)
    assert text_chars == 4
    assert n_msgs == 1


def test_medir_prompt_payload_vazio_nao_quebra():
    assert AnthropicProvider._medir_prompt({}) == (0, 0, 0, "")

"""
test_token_telemetry.py — Fase 1 da calibração de tokens (12/08/2026).

Especificação congelada: `lab_edp_novo/docs/sujeito_edp/AUDITORIA_FASE1_TOKENS.md §5`.

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


# ═══ Regime de formato (12/08/2026) ═══════════════════════════════════════════
#
# O congelamento de formato da Fase 1 era só convenção. Estes testes existem
# porque "confie que a configuração era a mesma" e "prove qual configuração
# produziu esta amostra" são garantias diferentes, e só a segunda sobrevive a
# alguém rodar `/mode sprint` no meio da coleta.

from edp.llm_adapter import CAPS_POR_MODO, EDPRuntime, snapshot_formato  # noqa: E402
from edp.runtime.pareto_store import (  # noqa: E402
    hash_format_state,
    set_current_format_state,
    clear_current_format_state,
)


@pytest.fixture(autouse=True)
def _limpa_formato():
    """Thread-local não pode vazar entre testes."""
    clear_current_format_state()
    yield
    clear_current_format_state()


# ── O gate: flag nova obriga classificação explícita ─────────────────────────

def test_toda_flag_do_config_esta_classificada():
    """
    ESTE É O TESTE QUE CONVERTE O PRINCÍPIO EM MECANISMO.

    O princípio — "tudo que altera a composição do prompt entra no
    format_state" — é inútil se depender de o próximo desenvolvedor lembrar
    dele. Aqui, adicionar uma `EDP_*` booleana ao config.py e não classificá-la
    QUEBRA O BUILD, com uma mensagem dizendo o que decidir.

    Falhou pra você? Decida: a flag muda o que entra no prompt? Vai em
    FORMAT_STATE_FLAGS. Não muda? Vai em FORMAT_STATE_FLAGS_IGNORADAS, **com o
    motivo no comentário** — "não importa" sem motivo é exatamente a suposição
    que esta lista existe para impedir.
    """
    classificadas = set(edp_config.FORMAT_STATE_FLAGS) | set(
        edp_config.FORMAT_STATE_FLAGS_IGNORADAS
    )
    existentes = {
        nome for nome in dir(edp_config)
        if nome.startswith("EDP_") and isinstance(getattr(edp_config, nome), bool)
    }
    faltando = existentes - classificadas
    assert not faltando, (
        f"flag(s) EDP_* sem classificação de formato: {sorted(faltando)} — "
        f"decida se mudam o prompt e adicione a FORMAT_STATE_FLAGS ou a "
        f"FORMAT_STATE_FLAGS_IGNORADAS em config.py"
    )


def test_listas_de_classificacao_nao_se_sobrepoem():
    assert not (set(edp_config.FORMAT_STATE_FLAGS)
                & set(edp_config.FORMAT_STATE_FLAGS_IGNORADAS))


# ── O snapshot grava o estado EFETIVO, não a causa dele ─────────────────────

def test_snapshot_grava_caps_efetivos_nao_so_o_modo():
    """
    Gravar `mode="sprint"` obriga quem analisa a reconstruir "logo, o cap era
    12000". Gravar os caps responde direto. Configuração é causa indireta; o
    que entrou no prompt é o que interessa.
    """
    assert snapshot_formato("sprint")["caps"][0] == 12000
    assert snapshot_formato("cognitive")["caps"][0] == 4000


def test_caps_por_modo_e_fonte_unica():
    """
    REGRESSÃO: o mapa mode->caps era literal DENTRO de `_retrieve_context`.
    Foi içado para módulo em 12/08 porque a telemetria precisa do mesmo mapa —
    duplicá-lo é o antipadrão que a auditoria de constantes catalogou
    (`SESSION_GAP_THRESHOLD_SEC` definido duas vezes, concordando por
    coincidência). Se alguém re-inlinar os literais, o snapshot e o prompt
    passam a discordar em silêncio.
    """
    assert CAPS_POR_MODO["sprint"][0] == 12000
    assert CAPS_POR_MODO["cognitive"][0] == 4000
    assert CAPS_POR_MODO["sprint"][1:] == CAPS_POR_MODO["cognitive"][1:]


def test_modo_desconhecido_cai_em_cognitive():
    assert snapshot_formato("modo-que-nao-existe")["caps"] == list(
        CAPS_POR_MODO["cognitive"]
    )


def test_snapshot_inclui_flag_lida_fora_do_config(monkeypatch):
    """
    `EDP_USE_CTX_MGR` é lida de os.environ direto no llm_adapter, NÃO do
    config.py — o teste de completude acima varre config.py e não a pegaria.
    E ela troca `_build_enriched_context` pelo fallback: monta o prompt de
    outro jeito. É a flag mais disruptiva do conjunto e a única fora do módulo.
    """
    monkeypatch.setenv("EDP_USE_CTX_MGR", "0")
    assert snapshot_formato("cognitive")["flags"]["EDP_USE_CTX_MGR"] is False
    monkeypatch.setenv("EDP_USE_CTX_MGR", "1")
    assert snapshot_formato("cognitive")["flags"]["EDP_USE_CTX_MGR"] is True


def test_snapshot_e_foto_nao_ponteiro():
    """
    A pergunta que a Fase 2 faz é "como o EDP estava quando esta observação
    nasceu", não "como está agora". Um ponteiro para estrutura viva responderia
    a segunda por acidente, e a amostra teria sido gravada com o valor errado
    sem ninguém notar.
    """
    a = snapshot_formato("cognitive")
    a["mode"] = "adulterado"
    a["flags"]["EDP_ANCHOR_COMPACT"] = "lixo"
    b = snapshot_formato("cognitive")
    assert b["mode"] == "cognitive"
    assert isinstance(b["flags"]["EDP_ANCHOR_COMPACT"], bool)


# ── O hash: identidade verificável, não confiança ───────────────────────────

def test_hash_muda_quando_o_modo_muda():
    """
    `/mode sprint` no meio da coleta é um comando normal, não um cenário
    exótico — e troca o cap do turno-1 por 3×. Sem hash distinto, as duas
    populações ficam indistinguíveis no dataset.
    """
    h_cog = hash_format_state(snapshot_formato("cognitive"))
    h_spr = hash_format_state(snapshot_formato("sprint"))
    assert h_cog and h_spr and h_cog != h_spr


def test_hash_muda_quando_uma_flag_muda(monkeypatch):
    antes = hash_format_state(snapshot_formato("cognitive"))
    monkeypatch.setattr(edp_config, "EDP_ANCHOR_COMPACT",
                        not edp_config.EDP_ANCHOR_COMPACT, raising=False)
    depois = hash_format_state(snapshot_formato("cognitive"))
    assert antes != depois


def test_hash_e_estavel_para_o_mesmo_regime():
    """Sem isto, cada amostra viraria seu próprio estrato e a divisão seria inútil."""
    assert hash_format_state(snapshot_formato("cognitive")) == \
           hash_format_state(snapshot_formato("cognitive"))


def test_hash_ignora_ordem_de_insercao_das_chaves():
    """
    `sort_keys=True` não é cosmético: sem ele, dois dicts com o MESMO conteúdo
    e ordem de inserção diferente dariam hashes diferentes, e a Fase 2 veria
    dois regimes onde só há um.
    """
    a = {"mode": "cognitive", "caps": [1, 2], "flags": {"X": True, "Y": False}}
    b = {"flags": {"Y": False, "X": True}, "caps": [1, 2], "mode": "cognitive"}
    assert hash_format_state(a) == hash_format_state(b)


def test_hash_de_none_e_none():
    assert hash_format_state(None) is None
    assert hash_format_state("nao-e-dict") is None


# ── Chega na amostra ─────────────────────────────────────────────────────────

def test_amostra_carrega_o_regime_e_o_hash(flag, isolated_base_dir):
    flag(True)
    estado = snapshot_formato("sprint")
    set_current_format_state(estado)
    emite()
    e = eventos()[0]
    assert e["format_state"]["mode"] == "sprint"
    assert e["format_state"]["caps"][0] == 12000
    assert e["format_hash"] == hash_format_state(estado)


def test_amostras_de_regimes_diferentes_sao_separaveis(flag, isolated_base_dir):
    """
    O ponto inteiro da mudança: mistura de regime deixa de ser contaminação
    invisível e vira variável observável. A Fase 2 agrupa por `format_hash` e
    analisa cada estrato no seu próprio regime, em vez de jogar fora tudo o que
    veio depois da troca (ou pior: não perceber a troca).
    """
    flag(True)
    set_current_format_state(snapshot_formato("cognitive"))
    emite()
    set_current_format_state(snapshot_formato("sprint"))
    emite()
    hashes = {e["format_hash"] for e in eventos()}
    assert len(hashes) == 2


def test_sem_regime_registrado_a_amostra_ainda_e_gravada(flag, isolated_base_dir):
    """
    Falta de regime NÃO descarta a amostra — diferente de tokens ausentes.
    Motivo: token ausente produziria um par falso que envenena a razão; regime
    ausente só produz uma amostra que a Fase 2 não consegue estratificar, e é
    ela quem decide o que fazer com isso. Descartar aqui seria decidir por ela.
    """
    flag(True)
    clear_current_format_state()
    emite()
    e = eventos()[0]
    assert e["format_state"] is None
    assert e["format_hash"] is None


# ── Cobertura dos caminhos de emissão (auditoria de 12/08) ───────────────────
#
# A pergunta que originou estes testes: "mostre TODOS os caminhos que produzem
# uma amostra token_usage e prove que todos passam pelo mesmo construtor de
# format_state". A resposta era não — três não passavam, e um deles rotulava
# errado, o que é pior que não rotular.

def test_validate_nao_emite_amostra(flag, isolated_base_dir, monkeypatch):
    """
    `AnthropicProvider.validate()` chama `_do_complete` direto para testar
    credencial (prompt "1", max_tokens=1). Sem `telemetria=False` isso vira uma
    amostra de ~1 token no dataset — e em prompt minúsculo o andaime JSON
    domina, então ela puxaria a razão do estrato inteiro.
    """
    flag(True)
    prov = AnthropicProvider.__new__(AnthropicProvider)
    payload = {"messages": [{"role": "user", "content": "1"}]}
    req = types.SimpleNamespace(data=b"{}")

    # simula o corpo de _do_complete: com telemetria=False, nada é emitido
    telemetria = False
    if telemetria:
        prov._telemetria_tokens(payload, req, dict(USAGE_OK), "complete", "m")
    assert eventos() == []

    # e com telemetria=True (o caminho normal), emite
    prov._telemetria_tokens(payload, req, dict(USAGE_OK), "complete", "m")
    assert len(eventos()) == 1


def test_validate_passa_telemetria_false():
    """Trava o argumento — se alguém remover, o teste acima vira teatro."""
    import inspect
    fonte = inspect.getsource(AnthropicProvider.validate)
    assert "telemetria=False" in fonte
    assinatura = inspect.signature(AnthropicProvider._do_complete)
    assert assinatura.parameters["telemetria"].default is True


def test_camara_nao_herda_o_regime_do_turno(flag, isolated_base_dir):
    """
    A câmara roda DENTRO do turno, na MESMA thread — herdaria o format_state
    por acidente. Seria pior que não rotular: o prompt da câmara tem composição
    própria (system de refutação, sem retrieval nem âncora), e carimbá-lo com o
    regime do prompt principal afirmaria algo falso sobre a amostra.
    """
    flag(True)
    from edp.runtime.pareto_store import get_current_format_state

    set_current_format_state(snapshot_formato("sprint"))
    # o que _llm_call_for_chamber faz ao redor da chamada
    guardado = get_current_format_state()
    set_current_format_state(None)
    emite()                                     # amostra da câmara
    set_current_format_state(guardado)          # devolve ao turno
    emite()                                     # amostra do turno

    evs = eventos()
    assert evs[0]["format_state"] is None, "câmara não pode herdar o regime"
    assert evs[1]["format_state"]["mode"] == "sprint", "turno perdeu o regime"


def test_camara_restaura_o_regime_mesmo_com_erro():
    """O restore está em `finally` — exceção da câmara não pode comer o regime."""
    import inspect
    fonte = inspect.getsource(EDPRuntime._llm_call_for_chamber)
    pos_finally = fonte.rindex("finally:")
    assert "_set_fmt(_fmt_turno)" in fonte[pos_finally:], \
        "restauração do format_state precisa estar no finally"


# ── População da Fase 2 como predicado, não como convenção ──────────────────

from edp.runtime.pareto_store import amostra_valida_fase2  # noqa: E402


def test_amostra_do_turno_entra_na_populacao(flag, isolated_base_dir):
    flag(True)
    set_current_format_state(snapshot_formato("cognitive"))
    emite()
    assert amostra_valida_fase2(eventos()[0])


def test_amostra_sem_regime_fica_fora(flag, isolated_base_dir):
    """
    Câmara e cognitive_decisions. Não são inválidas — são OUTRA população.
    Ficam gravadas (podem servir a outra pergunta) e fora do estrato.
    """
    flag(True)
    clear_current_format_state()
    emite()
    assert not amostra_valida_fase2(eventos()[0])


def test_evento_de_outro_tipo_fica_fora():
    assert not amostra_valida_fase2({"event": "task_started", "format_state": {}})
    assert not amostra_valida_fase2(None)
    assert not amostra_valida_fase2({})


def test_provider_e_gravado_e_exigido(flag, isolated_base_dir):
    """
    "anthropic" é redundante hoje (só ele emite) — e é por isso que vai
    gravado: no dia em que o Ollama for instrumentado, amostra antiga sem o
    campo vira ambígua retroativamente, sem como desambiguar depois.
    """
    flag(True)
    set_current_format_state(snapshot_formato("cognitive"))
    emite()
    e = eventos()[0]
    assert e["provider"] == "anthropic"
    assert not amostra_valida_fase2({**e, "provider": "ollama"})


def test_amostra_com_tokens_incompletos_fica_fora():
    """Defesa contra log editado à mão — o emissor já descarta na origem."""
    base = {"event": "token_usage", "format_state": {"mode": "x"},
            "provider": "anthropic", "usage": {"input_tokens": 10}}
    assert not amostra_valida_fase2(base)

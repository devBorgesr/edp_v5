"""
Dívida #56 — o probe de rede no caminho do turno.

O QUE ESTES TESTES PROTEGEM

`websocket.py:765` chama `is_connected()` logo antes de iniciar o streaming.
Para Anthropic isso descia até `AnthropicProvider.validate()`: chamada REAL ao
provider, prompt "1", max_tokens=1. Medido no log de produção de 03/09/2026:
**21,782 s, ~44% de um turno de 49,6 s**.

O cache não remove a verificação. Ele para de fazê-la a cada turno.

O que falha se alguém desfizer isto:
  * probe volta ao caminho quente          -> `test_segunda_chamada_nao_bate_na_rede`
  * cache guarda um "falhou"               -> `test_negativo_nunca_e_cacheado`
  * `/connect` aceita credencial do cache  -> `test_connect_sempre_forca`
  * flag-off deixa de reproduzir o antigo  -> `test_ttl_zero_reproduz_comportamento_antigo`

Pré-registro: `docs/preregistro_divida_56_probe.md`.
"""
from __future__ import annotations

import time

import pytest

from edp import config
from edp.llm_adapter import LLMClient, LLMConfig, LLMProvider


class ProviderFalso:
    """
    Conta chamadas. A LOGICA DE CACHE testada e a do codigo de producao — este
    fake so substitui a ida a rede, que e exatamente o que nao se quer no
    teste nem no turno.
    """

    def __init__(self, resposta=True, erro=None):
        self.resposta = resposta
        self.erro = erro
        self.chamadas = 0

    def validate(self):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return self.resposta


@pytest.fixture
def cliente(monkeypatch):
    def _monta(resposta=True, erro=None, ttl=300.0):
        monkeypatch.setattr(config, "EDP_LLM_VALIDATE_TTL", ttl)
        c = LLMClient(LLMConfig(provider=LLMProvider.ANTHROPIC,
                                model="claude-haiku-4-5", api_key="k"))
        p = ProviderFalso(resposta, erro)
        c._anthropic_provider = p
        return c, p
    return _monta


# ── o ganho ─────────────────────────────────────────────────────────────────

def test_segunda_chamada_nao_bate_na_rede(cliente):
    """
    O turno seguinte nao pode pagar o round-trip de novo. Era isto que custava
    21,782s por turno.
    """
    c, p = cliente()
    assert c.is_available() is True
    assert p.chamadas == 1
    for _ in range(20):
        assert c.is_available() is True
    assert p.chamadas == 1, f"{p.chamadas} idas a rede; esperado 1"


def test_is_connected_do_runtime_tambem_para_de_bater(cliente, monkeypatch):
    """
    O caminho real e `is_connected() -> is_available()`. Se o cache so
    valesse na chamada direta, o turno continuaria pagando.
    """
    from edp.llm_adapter import EDPRuntime
    c, p = cliente()
    rt = EDPRuntime.__new__(EDPRuntime)
    rt._client = c
    for _ in range(10):
        assert rt.is_connected() is True
    assert p.chamadas == 1


def test_cache_expira_e_verifica_de_novo(cliente):
    """TTL curto: a verificacao volta. O cache adia, nao elimina."""
    c, p = cliente(ttl=0.15)
    assert c.is_available() is True
    assert c.is_available() is True
    assert p.chamadas == 1
    time.sleep(0.25)
    assert c.is_available() is True
    assert p.chamadas == 2


# ── o controle negativo do §3 ───────────────────────────────────────────────

def test_negativo_nunca_e_cacheado(cliente):
    """
    Guardar um "falhou" manteria o sistema fora do ar depois de o operador
    corrigir a chave: o erro se curaria so quando o TTL expirasse, e ninguem
    entenderia por que.
    """
    c, p = cliente(resposta=False)
    for _ in range(5):
        assert c.is_available() is False
    assert p.chamadas == 5, "um resultado negativo foi cacheado"


def test_excecao_tambem_nao_e_cacheada(cliente):
    c, p = cliente(erro=RuntimeError("rede fora"))
    for _ in range(4):
        assert c.is_available() is False
    assert p.chamadas == 4


def test_credencial_que_passa_a_falhar_e_pega_quando_o_ttl_expira(cliente):
    """
    Chave revogada no meio da sessao: o cache atrasa a deteccao ate o TTL, e
    NAO mais que isso.
    """
    c, p = cliente(ttl=0.15)
    assert c.is_available() is True
    p.resposta = False
    assert c.is_available() is True          # dentro do TTL, ainda cacheado
    time.sleep(0.25)
    assert c.is_available() is False         # expirou: pega
    assert p.chamadas == 2


# ── /connect nao usa cache ──────────────────────────────────────────────────

def test_connect_sempre_forca(cliente):
    """
    `_connect` e o momento em que a resposta importa. Aceitar um "sim"
    guardado seria aceitar uma chave que ja pode nao valer.
    """
    c, p = cliente()
    c._validado_ate = time.time() + 9999     # cache "quente"
    assert c.is_available() is True
    assert p.chamadas == 0                   # confirma que o cache valeria
    assert c.is_available(forcar=True) is True
    assert p.chamadas == 1, "forcar=True usou o cache"


# ── flag-off ────────────────────────────────────────────────────────────────

def test_ttl_zero_reproduz_comportamento_antigo(cliente):
    c, p = cliente(ttl=0.0)
    for _ in range(6):
        c.is_available()
    assert p.chamadas == 6, "TTL=0 deveria bater na rede toda vez"


def test_default_do_config_e_300(monkeypatch):
    monkeypatch.delenv("EDP_LLM_VALIDATE_TTL", raising=False)
    import importlib
    from edp import config as c
    importlib.reload(c)
    assert c.EDP_LLM_VALIDATE_TTL == 300.0


# ── o que NAO pode ter mudado ───────────────────────────────────────────────

def test_ollama_e_openai_nao_passam_pelo_cache(monkeypatch):
    """
    So o caminho Anthropic pagava rede externa. O GET local de 3s continua
    como estava — o cache nao o cobre, e nao deve.
    """
    monkeypatch.setattr(config, "EDP_LLM_VALIDATE_TTL", 300.0)
    for prov in (LLMProvider.OLLAMA, LLMProvider.OPENAI):
        c = LLMClient(LLMConfig(provider=prov, model="x",
                                base_url="http://127.0.0.1:1"))
        assert c.is_available() is False     # porta fechada
        assert c._validado_ate == 0.0, f"{prov} tocou no cache"


def test_assinatura_continua_compativel():
    """
    `is_available()` sem argumento tem de continuar valendo: ha 4 chamadores
    fora deste modulo que nao foram tocados.
    """
    import inspect
    sig = inspect.signature(LLMClient.is_available)
    assert list(sig.parameters) == ["self", "forcar"]
    assert sig.parameters["forcar"].default is False

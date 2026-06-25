# EDP v5 — Sessão de Análise + Implementação
## Documento completo para revisão externa

**Data**: 2026-06-25  
**Repositório**: devborgesr/edp_v5  
**Branch de trabalho**: `fase4-dead-branch-cleanup` (parte da série `auditoria-curadoria`)  
**Commits desta sessão**: `1e0a38a` → `b32728b` → `40b117c` → `c4afc1f`

---

## 1. Contexto geral

O EDP v5 é um sistema de memória conversacional com arquitetura em camadas:

```
run.py:serve()
  → api/main.py:lifespan()
    → websocket.py:629 → run_pipeline(message, message)
      → memory.retrieve()  ← contexto recuperado
      → LLM stream
      → memory.add()       ← episódio salvo
      → lineage.persist()
```

Um bloco de 11 arquivos v3.2 foi incorporado em um único commit (`14179eb`) e nunca conectado ao caminho vivo. Em `f99835c`, esse bloco foi removido do codebase — mas `MemoryBridgeV32` em `pipeline.py` ficou com código morto referenciando o v3.2 removido. Esta sessão fez duas coisas:

1. **Analisou os 7 satélites v3.2** para decidir o que (se algo) reaproveitaria no EDP vivo e no lab experimental.
2. **Implementou as mudanças aprovadas pela análise**: extraiu o fragmento de eviction do `PressureMonitor`, conectou ao `EpisodicMemory`, e removeu o ramo morto do `MemoryBridgeV32`.

**Regra metodológica aplicada**: nenhuma alteração sem evidência em `file:line`. Ônus de prova no satélite. Mexer pouco foi o objetivo.

---

## 2. Análise dos 7 satélites v3.2

### 2.1 Problema original

O `EpisodicMemory` tem `max_size=200` (`EPISODIC_MEM_SIZE`) mas o store de produção tinha **594 entradas** — overflow silencioso por 36 dias. Nenhum alerta, nenhum log. A questão era: algum satélite v3.2 resolve isso (ou outra necessidade real)?

### 2.2 Dados de referência medidos

| Métrica | Valor |
|---|---|
| Entradas no store | 594 |
| Limite padrão | 200 |
| Usuários simultâneos | 1 (single-user) |
| Chamadas/turno ao retrieve | 1 (`websocket.py:629`) |
| Storm threshold (StormGuard) | 10 q/s |

### 2.3 Resultado por satélite

| Satélite | Arquivo | EDP Vivo | Lab | Decisão |
|---|---|---|---|---|
| SemanticBiodiversityEngine | `biodiversity.py` | TALVEZ (fragmento MRD, condicionado) | NÃO | Medir MRD real antes de decidir |
| CognitiveEconomyEngine | `economy.py` | NÃO | NÃO | **Descartar** |
| MetaStabilityController | `meta_stability.py` | NÃO | NÃO | **Descartar** |
| FractalPressureRegulator | `pressure_regulator.py` | NÃO | NÃO | **Descartar** |
| RetrievalStormGuard | `storm_guard.py` | TALVEZ (fragmento 15 linhas) | NÃO | Aguardar confirmação de echo chamber |
| AsyncDecisionGraph | `decision_graph_v32.py` | NÃO | NÃO | **Descartar** |
| **PressureMonitor** | `pressure_monitor.py` | **VALE** (fragmento scoped) | NÃO | **Extrair fragmento** ✓ |

**Por que PressureMonitor passou**: único satélite com necessidade confirmada por dado real (594 > 200) e zero dependências externas ao stdlib. Os outros 6 foram projetados para cenário multi-tenant/alta-frequência que não existe no sistema.

**Por que o Lab não precisa de nenhum**: o `edp/lab/` é auto-suficiente com 23 necessidades cobertas internamente (isolation, sampler, prontuário, rodízio, scorer). Nenhum satélite resolve necessidade real do lab.

> Documento completo da análise: `REAPROVEITAMENTO_SATELITES.md` (302 linhas, `commit 37cdb8f`, branch `auditoria-curadoria`)

---

## 3. Implementação — 4 fases

### FASE 1 — Novo arquivo `edp/pressure.py`
**Commit**: `1e0a38a`  
**Fonte**: `feb0db9:pressure_monitor.py` (commit git com a versão original)

Extração cirúrgica das duas dimensões com sinal real no EDP vivo:
- **eviction**: `len(entries) / max_size` — sinal imediato disponível
- **consolidation**: placeholder em `0.0` — sem fila no EDP vivo hoje

O satélite original tinha 6 dimensões. As outras 4 (entropy, retrieval, embedding, graph) dependem de outros satélites não integrados e foram omitidas.

```python
# edp/pressure.py — 107 linhas, zero deps fora de stdlib

from __future__ import annotations
import threading
import time
from dataclasses import dataclass


@dataclass
class StorePressureConfig:
    # Fonte: PressureMonitorConfig — feb0db9:pressure_monitor.py:77-79
    alert_threshold: float = 0.65
    critical_threshold: float = 0.82
    hysteresis: float = 0.10
    # Fonte: feb0db9:pressure_monitor.py:82
    ema_alpha: float = 0.20


@dataclass(frozen=True)
class StorePressureSnapshot:
    eviction: float           # EMA-smoothed em [0,1]
    consolidation: float      # EMA-smoothed em [0,1]
    eviction_alert: bool
    eviction_critical: bool
    consolidation_alert: bool
    ts: float


class _DimState:
    __slots__ = ("smoothed", "is_alert", "is_critical")

    def __init__(self) -> None:
        self.smoothed: float = 0.0
        self.is_alert: bool = False
        self.is_critical: bool = False


class StorePressureMonitor:
    """
    Call update(eviction_ratio) após cada memory.add() / _prune().
    eviction_ratio = len(entries) / max_size.
    snapshot() retorna estado imutável. Thread-safe.
    """

    def __init__(self, config: StorePressureConfig | None = None) -> None:
        self._cfg = config or StorePressureConfig()
        self._lock = threading.Lock()
        self._eviction = _DimState()
        self._consolidation = _DimState()

    def update(self, eviction_ratio: float, consolidation_ratio: float = 0.0) -> None:
        ev = max(0.0, min(eviction_ratio, 1.0))
        co = max(0.0, min(consolidation_ratio, 1.0))
        with self._lock:
            self._apply(self._eviction, ev)
            self._apply(self._consolidation, co)

    def snapshot(self) -> StorePressureSnapshot:
        with self._lock:
            return StorePressureSnapshot(
                eviction=self._eviction.smoothed,
                consolidation=self._consolidation.smoothed,
                eviction_alert=self._eviction.is_alert,
                eviction_critical=self._eviction.is_critical,
                consolidation_alert=self._consolidation.is_alert,
                ts=time.time(),
            )

    def _apply(self, state: _DimState, raw: float) -> None:
        # EMA — fonte: feb0db9:pressure_monitor.py:192-193
        alpha = self._cfg.ema_alpha
        state.smoothed = alpha * raw + (1.0 - alpha) * state.smoothed

        # Hysteresis — fonte: feb0db9:pressure_monitor.py:197-206
        cfg = self._cfg
        if not state.is_alert and state.smoothed >= cfg.alert_threshold:
            state.is_alert = True
        elif state.is_alert and state.smoothed < (cfg.alert_threshold - cfg.hysteresis):
            state.is_alert = False
            state.is_critical = False

        if not state.is_critical and state.smoothed >= cfg.critical_threshold:
            state.is_critical = True
        elif state.is_critical and state.smoothed < (cfg.critical_threshold - cfg.hysteresis):
            state.is_critical = False
```

**Decisões de design**:
- EMA `alpha=0.20`: previne oscilação em bursts curtos (10 updates para convergir ~87% do sinal)
- Hysteresis `0.10`: alert limpa em `0.55`, critical limpa em `0.72` — previne flapping
- `frozen=True` no snapshot: imutabilidade garante que caller não modifica estado por referência
- `threading.Lock` por instância: thread-safe sem lock global

---

### FASE 2 — Conexão em `edp/memory.py`
**Commit**: `b32728b`  
**+16 linhas** em dois pontos cirúrgicos

#### Ponto 1 — `EpisodicMemory.__init__`, após `self._load()` (linha 472)

```python
        # Store pressure monitor — extracted from feb0db9:pressure_monitor.py
        from .pressure import StorePressureMonitor
        self._pressure = StorePressureMonitor()
        self._pressure.update(len(self.entries) / max(self.max_size, 1))
        if len(self.entries) > self.max_size:
            logger.warning(
                "[memory] store carregado acima do limite: %d/%d entradas",
                len(self.entries), self.max_size,
            )
```

**Por que depois de `_load()`**: o monitor precisa do count real pós-carregamento, não zero. Se o store foi carregado com 594 entradas, o WARNING aparece imediatamente na inicialização — captura o caso silencioso que existia antes.

#### Ponto 2 — `EpisodicMemory.add()`, após o bloco `_prune()` (linha 587)

```python
        _prev_alert = self._pressure.snapshot().eviction_alert
        self._pressure.update(len(self.entries) / max(self.max_size, 1))
        _snap = self._pressure.snapshot()
        if _snap.eviction_alert and not _prev_alert:
            logger.warning("[memory] pressão de eviction em ALERTA: %d/%d (ema=%.2f)",
                           len(self.entries), self.max_size, _snap.eviction)
```

**Por que `_prev_alert` antes do update**: log só na transição `False→True`. Sem isso, o WARNING spamaria em cada `add()` enquanto o store estiver acima do threshold — uma entrada nova por turno = um WARNING por turno.

**Comportamento inalterado**: `retrieve()`, `_prune()`, `consolidate()` não foram tocados. Teste determinístico (seed=42, 20 entries, max=10) confirmou IDs e scores idênticos antes/depois.

---

### FASE 3 — Validação determinística

Testes rodados antes de cada commit nas FASEs 1 e 2:

**Smoke test `pressure.py`** (manual, sem pytest):
```python
m = StorePressureMonitor()
m.update(0.0)   # smoothed ≈ 0.0, no alert
m.update(1.0)   # smoothed sobe via EMA
for _ in range(50): m.update(1.0)  # converge para ~1.0
snap = m.snapshot()
assert snap.eviction_alert    # True após convergência
assert snap.eviction_critical # True após convergência

# Clearance
for _ in range(50): m.update(0.0)
snap = m.snapshot()
assert not snap.eviction_alert  # limpa após hysteresis
```

**Before/after test `memory.py`** (seed=42):
- 20 entradas inseridas, max_size=10 → prune acontece
- IDs retornados por `retrieve("pergunta de teste", top_k=3)` **antes** das mudanças: `['14','11','15']`
- IDs retornados **depois** das mudanças: `['14','11','15']` — idêntico
- Verificação: `snapshot().eviction_alert == True` quando store saturado

---

### FASE 4 — Limpeza do ramo morto em `pipeline.py`
**Commit**: `40b117c`  
**-45 linhas, +5 linhas** (net -40)

#### Antes (com ramo morto)

```python
class MemoryBridgeV32:
    def __init__(self, session_id: str = "default"):
        self.session_id       = session_id
        self._semantic_memory = get_pipeline_memory(session_id)
        self._v32_store       = None  # NUNCA populado após f99835c
        self._lock            = threading.Lock()

    def register_v32_store(self, store) -> None:
        """Injeta VectorStoreProtocol do orchestrator v3.2."""  # SEM CALLERS
        with self._lock:
            self._v32_store = store

    def consolidate(self, episodes: list[dict]) -> None:
        # v3.1 path — sempre executa
        self._semantic_memory.consolidate_from_episodes(episodes)

        # v3.2 path — SEMPRE FALSE (self._v32_store is always None)
        if self._v32_store is not None:
            with self._lock:
                store = self._v32_store
            for ep in episodes:
                try:
                    store.upsert_raw(
                        id=ep.get("id", ""),
                        text=ep.get("text", ""),
                        embedding=ep.get("embedding"),
                    )
                except Exception:
                    pass  # best-effort

    def retrieve(self, question: str, top_k: int = 3) -> list[dict]:
        """Retrieval via SemanticMemory (v3.1). Bridge transparente."""
        return self._semantic_memory.retrieve(question, top_k=top_k)
```

#### Depois (limpo)

```python
class MemoryBridgeV32:
    """
    [P15] Envolve SemanticMemory expondo consolidate() e retrieve().
    Detectada via isinstance em pipeline.py:646 para rotear consolidação.
    """

    def __init__(self, session_id: str = "default"):
        self.session_id       = session_id
        self._semantic_memory = get_pipeline_memory(session_id)

    def consolidate(self, episodes: list[dict]) -> None:
        self._semantic_memory.consolidate_from_episodes(episodes)

    def retrieve(self, question: str, top_k: int = 3) -> list[dict]:
        return self._semantic_memory.retrieve(question, top_k=top_k)
```

**O que foi removido e por quê**:

| Elemento removido | Razão |
|---|---|
| `self._v32_store = None` | Nunca populado após `f99835c` (grep confirmou zero callers de `register_v32_store`) |
| `self._lock = threading.Lock()` | Só usado pelo ramo v3.2; `threading` ainda importado em `pipeline.py:48` para `_memory_lock` |
| `register_v32_store()` | Zero callers — grep em todo o codebase: nenhum arquivo chama este método |
| `if self._v32_store is not None:` | Branch sempre False → código morto |
| `store.upsert_raw(...)` | Nunca executado |

**O que foi preservado** (confirmado em `pipeline.py:646`):
- Classe em si (usada em `isinstance` check para rotear consolidação)
- `__init__` com `_semantic_memory`
- `consolidate()` v3.1 path (o único que sempre executava)
- `retrieve()` (comportamento inalterado)

**Verificação**: retrieve seed=42 antes/depois: `['14','11','15']` — idêntico.

---

### Chore — `.gitignore`
**Commit**: `c4afc1f`

Adicionado `data/` ao `.gitignore` para excluir `data/pareto/events.jsonl` — telemetria de runtime gerada pelos testes de validação da FASE 3. O `.gitignore` tinha `edp_data/` mas não `data/`.

```diff
 # Dados do EDP (ficam fora do repo, mas garantia)
 edp_data/
+data/
 *.json.broken
```

---

## 4. Histórico de commits desta sessão

```
c4afc1f  chore: ignora data/ (telemetria de runtime gerada pelos testes)
40b117c  refactor(pipeline): remove ramo morto v3.2 do MemoryBridgeV32
b32728b  feat(memory): conecta StorePressureMonitor ao EpisodicMemory (leitura)
1e0a38a  feat(pressure): extrai fragmento de eviction do PressureMonitor v3.2
37cdb8f  análise: mapa de reaproveitamento dos 7 satélites v3.2  ← base (auditoria-curadoria)
```

---

## 5. Estado atual do codebase (pós-implementação)

### Arquivos modificados/criados

| Arquivo | Status | Mudança |
|---|---|---|
| `edp/pressure.py` | **NOVO** | 107 linhas — StorePressureMonitor standalone |
| `edp/memory.py` | **+16 linhas** | Monitor conectado em `__init__` e `add()` |
| `edp/pipeline.py` | **-40 linhas net** | MemoryBridgeV32 sem ramo morto |
| `.gitignore` | **+1 linha** | `data/` excluído |

### Arquivos inalterados no caminho vivo

`websocket.py`, `run.py`, `api/main.py`, `lineage.py`, `consolidation.py`, `semantic_memory.py`, `vector_store.py`, `cognitive_scheduler.py` — nenhum destes foi tocado.

---

## 6. Itens pendentes (não implementados, listados para revisão humana)

### 6.1 Arquivos órfãos (candidatos a deleção, não deletados)

Por regra metodológica ("se houver dúvida, NÃO remova — liste para revisão humana"):

**`edp/types.py`**
- Define: `CognitiveTick`, `DecisionEvent`, `DecisionEventType`
- Importadores no codebase vivo: **nenhum** (grep confirmado)
- Contexto: tipos v3.2, nunca conectados ao caminho vivo
- Decisão pendente: deletar ou manter como referência?

**`edp/exceptions.py`**
- Define: `PressureSaturationError`, `StormDetected`, `EconomyBudgetExceeded`
- Importadores: apenas `edp/types.py` (que já é órfão)
- Contexto: exceções de satélites v3.2 sem handler em nenhum arquivo vivo
- Decisão pendente: deletar junto com `types.py`?

### 6.2 Comentários v3.2 em arquivos vivos

Referências textuais que sobraram após a limpeza de código (não afetam comportamento, mas podem confundir):

| Local | Linha | Texto |
|---|---|---|
| `edp/vector_store.py` | 107 | referência a v3.2 |
| `edp/health_index.py` | 21 | referência a v3.2 |
| `edp/semantic_memory.py` | 18 | referência a v3.2 |
| `edp/pipeline.py` | 74 | atualizado nesta sessão, mas verificar se restam outros |

### 6.3 Satélites em estado "aguardar dado real"

**SemanticBiodiversityEngine** (`biodiversity.py`)
- Fragmento viável: `_compute_mrd(embeddings)` (~15 linhas, matemática pura sobre matriz de embeddings)
- Pré-condição: medir MRD real do store de produção
- Script estimado: ~5 linhas (extrair embeddings de `memory.entries`, calcular distâncias)
- Se MRD < 0.15 → colapso semântico confirmado → extrair fragmento
- Se MRD normal → descartar

**RetrievalStormGuard** (`storm_guard.py`)
- Fragmento viável: `_update_similarity_saturation()` (~15 linhas, opera sobre lista de `float`)
- Pré-condição: confirmar echo chamber nos scores de retrieval históricos
- Se fração de scores ≥ 0.88 for alta → extrair fragmento
- Sem confirmação → descartar

### 6.4 Dimensões de pressão não conectadas

O `StorePressureMonitor` tem `consolidation_ratio` como parâmetro (default 0.0). No EDP vivo, consolidação é feita por `cognitive_scheduler.py:170` de forma síncrona — sem fila para medir. Se uma fila for adicionada no futuro, a conexão é direta: `self._pressure.update(eviction_ratio, len(queue)/max_queue)`.

### 6.5 PR não criado

Decisão do usuário: "NÃO crie o PR agora". Branches aguardam revisão local antes de merge.

---

## 7. Arquitetura do EMA + Hysteresis (referência para a outra IA)

O `StorePressureMonitor` usa dois mecanismos para evitar oscilação:

**EMA (Exponential Moving Average)**
```
smoothed_t = alpha * raw_t + (1 - alpha) * smoothed_{t-1}
```
Com `alpha=0.20`: cada novo valor contribui 20%, o histórico pesa 80%. Para um store saturado (ratio=1.0), a EMA leva ~10 updates para chegar a ~87% do valor real. Isso previne que um burst curto de inserts dispare um alerta falso.

**Hysteresis**
```
Alert SOBE quando: smoothed >= alert_threshold  (0.65)
Alert DESCE quando: smoothed < alert_threshold - hysteresis  (0.65 - 0.10 = 0.55)
```
A zona morta [0.55, 0.65] previne flapping: o sistema só desalerta depois de o store ter reduzido significativamente, não na primeira entrada abaixo do threshold.

**Threshold escolhidos** (preservados do original `feb0db9`):
- `alert_threshold = 0.65`: store a 65% da capacidade → atenção
- `critical_threshold = 0.82`: store a 82% → crítico
- Com `max_size=200`: alert em 130 entradas, critical em 164 entradas

---

## 8. Perguntas que a outra IA pode querer responder

1. **A conexão em `memory.py` está no lugar certo?** O monitor é atualizado *depois* do `_prune()` — então o ratio medido é pós-evicção. Seria melhor medir antes? (Argumento contra: medir antes daria ratio sempre acima do limite quando prune acontece, inflando o alerta.)

2. **O import lazy `from .pressure import StorePressureMonitor` no `__init__` é o melhor padrão?** Alternativa: import no topo do arquivo (mais visível). O lazy foi escolhido para manter a mudança localizada e reversível.

3. **A limpeza do `MemoryBridgeV32` foi completa?** O `isinstance` check em `pipeline.py:646` ainda roteia para `bridge.consolidate()`. Isso é correto — a classe ainda é necessária como wrapper da interface. Mas vale verificar se outros callers existem.

4. **Os órfãos `types.py` e `exceptions.py` podem ser deletados com segurança?** Grep confirmou zero importadores no codebase vivo, mas uma varredura independente seria bem-vinda antes da deleção.

5. **O `consolidation_ratio` deveria ser 0.0 ou calculado de outra forma?** `cognitive_scheduler.py` não tem fila explícita. Se há algum proxy de pressão de consolidação já disponível no código, poderia ser wired.

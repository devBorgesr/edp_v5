# Registro de Dívidas Técnicas — EDP

Lar único e versionado das dívidas técnicas do projeto. Toda dívida vive
aqui, com status, workaround (se houver) e caminho de correção.

---

## Dívida #41 — Threshold de pressão de RAM mal configurado

**Status:** FECHADA (PR #11, `hardening/fase2-mortos-e-divida41`)
**Origem:** descoberta no Commit δ (elevação de logs)

### O problema
O threshold de pressão de RAM estava mal configurado para a máquina real
(notebook 8GB, CPU-only). Os limites default disparavam alarmes de pressão
fora de hora.

### Correção aplicada
Defaults recalibrados no código (`edp/runtime/pressure_governor.py`) para
a realidade API-only: `CRITICAL_GB=0.30` / `WARNING_GB=0.60`, com override
via env var (`EDP_PRESSURE_CRITICAL_GB` / `EDP_PRESSURE_WARNING_GB`)
preservado para rollback aos valores antigos (1.2/2.0) sem mudar código.
Coberto por `tests/test_divida_41.py` (7 checks: defaults novos, env vars
respeitadas, rollback para valores antigos, e os 3 regimes de
classificação NORMAL/WARNING/CRITICAL).

---

## Dívida #46d — Classificador marca turnos técnicos como meta_conversation

**Status:** registrada, não-bloqueante
**Origem:** descoberta durante o arco #46c (16/06/2026)

### O problema
O classificador de turnos rotula turnos de conversa puramente técnicos como
`meta_conversation` por engano. Caso concreto: o turno onde o modelo explicou
o algoritmo de Luhn foi classificado como `meta_conversation`, quando é uma
resposta técnica normal.

### Por que importa (e por que NÃO é bloqueante)
- NÃO bloqueia a janela imediata: o #46c passou a selecionar turno por FORMA
  (form-check Q:/A:), então a janela é imune a este erro de classificação.
- MAS suja a telemetria: qualquer métrica ou consumidor que confie em
  `source_type=meta_conversation` para contar/filtrar conversas vai errar.
- É a causa-raiz A MONTANTE do #46c: o #46c foi a defesa (parar de confiar na
  categoria); o #46d é o defeito real (a categoria está errada na origem).

### Caminho de correção (futuro)
Investigar o critério do classificador que dispara `meta_conversation`.
Uma resposta técnica sobre um tópico externo (Luhn, Avogadro) não é
meta-conversa. Enquanto o #46d não for corrigido, NENHUM código novo deve
confiar em source_type para decidir o que é conversa.

---

## Dívida #53 — Crash ou perda silenciosa em truncamento no meio de JSON nos stores

**Status:** FECHADA (06/08/2026, commit `2524c55`, branch `fix/toxic-guards`
— ver "Correção pós-ressalva" abaixo). 6/6 call sites versionados e
comprovados em clone limpo real (não só working tree). Histórico da
ressalva de 04–05/08 preservado abaixo, não apagado.
**Origem:** já citada como risco em auditoria anterior, sem ID formal
atribuído até este documento. Pré-registro completo (hipóteses, métricas,
critério de decisão congelado antes da implementação) em
[`docs/preregistro_fix_corrupcao_json.md`](preregistro_fix_corrupcao_json.md).
Veredito (congelado vs. provado) em
[`docs/VEREDITO_fix_corrupcao_json.md`](VEREDITO_fix_corrupcao_json.md).

### O problema
`edp/memory/atomic_io.py::_safe_load_json` recupera corrupção do tipo
"lixo depois de um JSON válido" (write parcial que deixou sobra no final),
mas truncamento GENUÍNO no meio da estrutura (nenhum candidato fecha o
container externo) não era recuperável. Isso produzia dois sintomas do
mesmo defeito, em 6 call sites reais (5 do escopo original do prompt +
`edp/ingest/session_index.py` + `edp/profiles/registry.py`, achados na
verificação de premissas desta rodada — ver pré-registro, Passo 0):

- **Crash no boot** (`store.py::EpisodicMemory._load`,
  `semantic.py::SemanticMemory._load`,
  `ingest/session_index.py::SessionIndex._load`,
  `profiles/registry.py::ProfileRegistry._load`): `JSONDecodeError`
  propagava sem try/except ao redor da construção, derrubando o processo
  inteiro.
- **Perda silenciosa** (`echo_chamber.py::EchoChamber._load`,
  `blocks.py::BlockManager._load`): `except Exception: self.x = []`
  engolia a corrupção sem log, sem quarentena, sem rastro do que foi
  perdido.

Adicionalmente (Passo 0.5, achado nesta auditoria): o próprio algoritmo de
recuperação de `_safe_load_json` era O(tentativas × tamanho do parse) —
até O(n²) — e podia travar o boot por minutos em arquivos de alguns MB
antes de decidir que a corrupção era irrecuperável (medido: >300s / ~17min
extrapolado num arquivo de 10MB truncado).

### Correção aplicada
- `_safe_load_json`: loop de recuperação trocado de caractere-a-caractere
  para `str.rfind` + cap `MAX_RECOVERY_CANDIDATES=20`, justificado pelo
  write path de `_atomic_write_json` (tmp→fsync→os.replace — corrupção
  realista é sempre cauda de UMA escrita interrompida). Contrato
  preservado (ainda propaga se irrecuperável dentro do orçamento).
- `_load_json_or_quarantine` (novo, em `atomic_io.py`): choke-point
  usado pelos 6 call sites — nunca crasha, nunca perde o dado bruto em
  silêncio. Preserva o arquivo original byte-idêntico via `os.replace`
  atômico para `<path>.corrompido-<timestamp>`, loga
  `logger.critical(exc_info=True)`, emite evento Pareto "store_degraded"
  (`edp/runtime/pareto_store.py`, reaproveitado — não é subsistema novo),
  e degrada para vazio de forma explícita.
- Feature flag `EDP_STORE_QUARANTINE` (default ON, `edp/config.py`) —
  válvula de rollback para este mecanismo específico, não compartilhada
  com `EDP_TOXIC_GUARDS`/`EDP_WRITE_PROVENANCE` (o projeto já mediu esse
  antipadrão de flag compartilhada — ver histórico do fix de toxic-guards
  — e não repete aqui).

Coberto por `tests/test_store_quarantine.py` (28 testes — boot sobrevive,
quarentena byte-idêntica, arquivo válido intocado, sinal de
observabilidade por asserção, cap de candidatos, performance) e pela
reescrita de
`tests/test_failsafe_roundtrip.py::test_reload_apos_truncamento_no_meio_do_objeto_quarentena_e_degrada`
(contrato antigo documentava o crash; novo contrato documenta a
quarentena).

### Correção pós-ressalva (06/08/2026) — `edp.profiles` versionado por inteiro

Commit `2524c55` (branch `fix/toxic-guards`) versiona o resto do módulo
(`models.py`, `__init__.py`, `selector.py`, `tools.py`, `tracker.py`,
`README.md`, `config/profiles.example.yaml`). Verificado em **clone limpo
de verdade** (`git clone` fresco do remote público, não o working tree
local — é exatamente a checagem que faltou em 05/08): **206 passed, 1
deselected, 0 skipped**. `TestProfileRegistryQuarantine` (4 testes) roda
normalmente, sem `pytest.importorskip` mais acionando. Item (a)/(b)/(c)
do critério original passa de "5/6 versionado" para **6/6**.

Achado adjacente durante esta verificação, não escondido: a diferença
entre 206 e os 220 medidos no working tree local **não fechou** —
`tests/test_profiles_selector.py` e `tests/test_profiles_tracker.py`
continuam untracked (confirmado via `git status` em 06/08). Isso é um
gap separado desta dívida (cobre `ProfileSelector`/`UsageTracker`, não
o mecanismo de quarentena), não reaberto aqui, só registrado para não
virar "achado perdido" — mesmo padrão do WIP não versionado que gerou
esta ressalva originalmente, em escopo menor.

### Ressalva (05/08/2026) — `profiles_registry` não fechou junto (histórico)

`d83503f` versionou `edp/profiles/registry.py` sozinho, sem o resto do
módulo `edp.profiles` (`models.py`, `__init__.py`, `selector.py`,
`tools.py`, `tracker.py` — untracked). Clone limpo desta branch quebrava
em `ModuleNotFoundError` na primeira `import edp`, confirmado com clone
real. Commit `2467020` destrackeia `registry.py` (preserva a mudança de
quarentena no working tree) e guarda
`TestProfileRegistryQuarantine` com `pytest.importorskip`, que pula a
classe inteira em clone limpo em vez de quebrar a coleta do pytest.

Número real em clone limpo: **202 passed, 4 skipped
(`TestProfileRegistryQuarantine`), 1 deselected** — não os "28 testes /
220 passed" citados acima, que foram medidos contra o working tree local
(que tem `edp/profiles` inteiro no disco, só não versionado). Detalhe
completo em
[`docs/VEREDITO_fix_corrupcao_json.md`](VEREDITO_fix_corrupcao_json.md#correção-pós-veredito-05082026--profiles_registry-não-ficou-fechado).
Reabre quando `edp.profiles` for versionado por inteiro: nesse momento os
4 testes voltam a rodar sozinhos, sem exigir mudança neste arquivo.

---

## Dívida #54 — Caminho com LLM real do runtime flow nunca foi exercitado

**Status:** ABERTA
**Origem:** validação visual do Dashboard v3.5 (03/09/2026)

### O problema
A validação em navegador real provou o turno cognitivo sem LLM
(`request → pipeline → stream → response`). O caminho com provider real
(`request → pipeline → model → stream → response`) foi **lido no código**, não
executado: `advanceFlow('model')` no handler de `llm_start`
(`edp/dashboard/static/dashboard.js`) exige chave de provider.

A distinção importa porque a correção de `concluiSeAtivo` mudou justamente
quando um nó pode ser marcado concluído. Com LLM ausente o comportamento está
medido; com LLM presente está deduzido.

### Caminho de correção
Smoke test de um turno com provider real, verificando que `model` chega a
`active` antes de `done`, e que `flow-sub` não usa a redação "sem LLM".

### Workaround
Nenhum necessário — o comportamento sem LLM é o correto e está validado.

### Atualização 03/09/2026 — precondição medida, render ainda não
Log de execução em produção (host Windows, store `C:\edp_data_todo\edp_data`,
`claude-haiku-4-5`) mostra a cadeia completa no servidor: `msg recebida` →
`pipeline ok` → `LLM stream iniciando` → `LLM primeiro chunk` → `LLM done` →
`done llm_used=True`. A ordem é a que o frontend espera e `llm_used=True`
chega ao cliente.

Isso fecha a metade de trás: existe um `llm_start` real para ativar o nó
`model` antes do `chunk`. Não fecha a dívida — o log é do servidor, e o que
falta medir é o **render**. Ver `VEREDITO_dashboard_v3.5.md` §4.2.

---

## Dívida #56 — `is_connected()` faz round-trip de rede no caminho do turno

**Status:** ABERTA
**Origem:** log de execução em produção, 03/09/2026

### O problema
`edp/api/routes/websocket.py:765` chama `runtime.is_connected()` no caminho
quente do turno, logo depois de `pipeline_done` e imediatamente antes de
iniciar o streaming. Para Anthropic essa cadeia é:

```
is_connected() -> LLMClient.is_available()   (llm_adapter.py:1898, :636)
               -> AnthropicProvider.validate()  (anthropic.py:515)
               -> chamada real a api.anthropic.com, prompt "1", max_tokens=1
```

Para Ollama/OpenAI o mesmo `is_available()` é um GET local com timeout de 3 s.
Só o caminho Anthropic paga uma ida e volta à rede.

### A medida
No log, o probe do turno levou **21.782 s**, entre o fim da recuperação de
memória (04:54:16,192) e o `LLM stream iniciando` (04:54:37,999). O turno
inteiro — `msg recebida` a `done` — levou **49,6 s**. O probe foi **~44% do
turno**, para produzir 1 token.

Cinco probes `tok_in=9 tok_out=1` aparecem em ~5 minutos de operação, com
latências de 22.065, 9.836, 21.818, 21.782 e 11.091 ms: **86,6 s somados**.
Além de `websocket.py:765`, alcançam `is_available()` os caminhos de
`session_summary.py:154`, `ingest/consolidator.py:47`, `api/routes/llm.py:59`
e `:89` — ou seja, jobs de fundo também pagam o probe.

O custo em dinheiro é desprezível (`cost=$0.0000`, e `validate()` já passa
`telemetria=False` justamente para não sujar o dataset). O custo é **latência
percebida**: quase metade da espera do usuário é o sistema perguntando ao
provider se ele está lá, antes de perguntar o que o usuário quis saber.

### Caminho de correção
Não é remover a verificação — é não fazê-la por turno. Estado de conexão já é
conhecido: `_connect()` validou na conexão, e um turno que falha por
credencial já levanta `AuthError` no lugar certo. As opções, em ordem de
custo: cachear o resultado de `validate()` com TTL no `LLMClient`; ou trocar
`is_connected()` por uma leitura de estado (`self._client is not None`) no
caminho do turno, deixando o probe para `/connect` e para o endpoint de
providers, que é onde ele responde uma pergunta que alguém fez.

Qualquer das duas muda comportamento do caminho quente do kernel num
repositório público — decisão antes de código, como o resto do projeto.

### Workaround
Nenhum. O sistema funciona; só espera mais do que precisa.

---

## Dívida #55 — `avg_top` não tem definição fechada

**Status:** ABERTA
**Origem:** validação visual do Dashboard v3.5 (03/09/2026)

### O problema
`/dashboard/state` devolve `retrieval_quality` com série diária contendo
`avg_top` e `turns`. O dashboard lê apenas `trend` e `total_turns`; a série é
descartada. Existe aí um gráfico de tendência sem custo de backend.

O bloqueio não é de engenharia: **não está escrito o que `avg_top`
representa** — sobre qual população, com qual ranking, e o que pode ser
afirmado a partir de uma variação dele. Sem isso, plotar a série transforma um
indicador operacional (NÍVEL 1) em alegação de qualidade de recuperação
(NÍVEL 3), que é a fronteira que o resto do projeto sustenta.

### Caminho de correção
1. Documentar a grandeza a partir do código que a produz: definição, unidade,
   população, e o que uma queda pode e não pode significar.
2. Só então decidir se vai ao dashboard, e com qual ressalva na tela.

### Workaround
Nenhum. O dado segue disponível no payload para quem quiser inspecioná-lo
diretamente; nada é exibido, então nada é afirmado.

Referência: [`docs/VEREDITO_dashboard_v3.5.md`](VEREDITO_dashboard_v3.5.md) §5.

---

## Notas de decisão

Retrieval duplo (caminho cosine puro + caminho híbrido) é requisito de
rollback byte-idêntico, contrato pinado por `test_flag_off_byte_identical.py`;
colapso para 1 caminho é decisão futura condicionada a abandonar o rollback
por env var (`EDP_HYBRID_RETRIEVAL`/`EDP_WRITE_PROVENANCE`).
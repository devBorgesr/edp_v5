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

**Status:** FECHADA (04/08/2026, branch `fix/toxic-guards`).
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

---

## Notas de decisão

Retrieval duplo (caminho cosine puro + caminho híbrido) é requisito de
rollback byte-idêntico, contrato pinado por `test_flag_off_byte_identical.py`;
colapso para 1 caminho é decisão futura condicionada a abandonar o rollback
por env var (`EDP_HYBRID_RETRIEVAL`/`EDP_WRITE_PROVENANCE`).
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

## Notas de decisão

Retrieval duplo (caminho cosine puro + caminho híbrido) é requisito de
rollback byte-idêntico, contrato pinado por `test_flag_off_byte_identical.py`;
colapso para 1 caminho é decisão futura condicionada a abandonar o rollback
por env var (`EDP_HYBRID_RETRIEVAL`/`EDP_WRITE_PROVENANCE`).
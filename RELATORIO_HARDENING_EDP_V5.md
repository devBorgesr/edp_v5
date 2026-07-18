# Relatório de Fechamento — Ciclo de Hardening EDP v5

Branch `docs/relatorio-hardening`, a partir de `main` (`cd4ac0d`). Consolida
as 4 fases do ciclo de hardening — PRs #10-#13, tags `v3.16-quarantine-stable`
→ `v3.20-hardening-fase4` — que corrigiram os 3 eixos abertos pelo
diagnóstico Fase 0 (`FASE0_DIAGNOSTICO_HARDENING.md`, baseline `e2b0b2d`,
pós-merge PRs #7/#8/#9).

Fontes: `git log --stat`/`git show --stat` das 4 branches mescladas
(`hardening/fase1-rede-e-aposentadoria` até `hardening/fase4-memoria-e-clock`),
`FASE0_DIAGNOSTICO_HARDENING.md`, `FASE0_5_MEDICAO_SPLITBRAIN.md`,
`RELATORIO_FASE4.md`, `docs/RUNBOOK.md`. Onde um número deste relatório foi
reconferido por mim e diverge do documento-fonte, os dois valores estão
registrados lado a lado, explicitamente — não escondido, seguindo a mesma
Regra 6 (evidência antes de crer em documento) que orientou a Fase 0.

Sem código nesta branch — só este documento.

---

## 1. Placar dos 3 eixos

### Modularidade

| Critério | Estado inicial (Fase 0, `e2b0b2d`) | Estado final (`cd4ac0d`) | Nota |
|---|---|---|---|
| Código morto removido | 0 | **~2977 linhas** em 7 arquivos: `api.py` 410 (`81a9633`) + `api_v2.py` 1110 (`f5f4a41`) + `retrieval_ann.py` 387 (`0e56f2b`) + `belief_graph.py` 362 (`39068e1`) + `scheduler.py` 333 (`44bd82c`) + `semantic_memory.py` 295 (`cb4298f`) + trecho morto de `benchmark_edp.py` 80 (dentro de `39068e1`) | Todas as 7 remoções confirmadas por `git show --stat` no commit específico, não estimadas |
| Superfícies de API vivas | 3 (`edp/api/` modular + `api.py` legacy v3.1 + `api_v2.py` monolito v3.3, os dois últimos só alcançáveis por fallback nunca atingido) | **1** (`edp/api/`) | `run.py:serve()` perdeu os 2 fallbacks mortos nos commits `81a9633`/`f5f4a41` |
| Implementações de retrieval vivas | 3 (`HybridRetriever` viva, `RetrievalEngine` só benchmark, `ANNRetrievalEngine` morta) | **2** (`HybridRetriever`, `RetrievalEngine` — este mantido deliberadamente para o benchmark legado) | `ANNRetrievalEngine` removida em `0e56f2b` por evidência de grep exaustivo, zero uso fora do próprio arquivo |
| `memory.py` | 1 arquivo monolítico, 1991 linhas | Pacote `edp/memory/` — 4 módulos (`atomic_io.py` 164L, `semantic.py` 157L, `store.py` 1707L, `__init__.py` 48L; 2073L totais), **0 ciclos de import** (DAG linear `atomic_io` ← `semantic` ← `store` ← `__init__`) | Fase 4, T3, `a5ab641`/`53703bb`/`2feccf2` |
| `MemoryStore` — edges EXTRACTED (god node) | **75** (Fase 0) | **48** (Fase 4, T4) — trajetória completa: 75 → 47 (T0 da Fase 4, já refletindo as limpezas das Fases 1-3) → 48 (pós-split) | Ver parágrafo abaixo — a leitura ingênua "48 > meta ≤35" precisa da nuance |

**Parágrafo honesto sobre a meta ≤35 de `MemoryStore`:** a meta original (adendo
pré-Fase-0) foi escrita contra as **97 edges totais** (EXTRACTED+INFERRED) que
o grafo mostrava antes de qualquer investigação. A Fase 0 (Item 1) decompôs
essas 97 e mostrou que ~17 das 22 INFERRED eram ruído sistemático do extrator
(edges `uses→MemoryStore` atribuídas por co-ocorrência de arquivo a classes
Pydantic que nunca referenciam `MemoryStore`, âncoradas em linha de `import`
isolada — inclusive em `api.py`/`api_v2.py`, código já morto). Filtrando para
EXTRACTED-apenas, o número real de partida já era 75, não 97 — a meta ≤35
foi recalibrada implicitamente nesse momento, mas nunca reescrita
formalmente contra o número certo.

O split da Fase 4 (MOVE-ONLY, por restrição explícita do adendo) **dissolve o
arquivo, não a classe**: `MemoryStore` continua ~30 métodos e ~8 importadores
externos (`benchmark_edp.py`, `llm_adapter.py`, `exp009.py`, `exp010.py`,
`measure_ss_dominance.py`, `run.py`, `registry.py`, `cleanup_orphans.py`) —
por isso 47→48 (estável, dentro do ruído de medição) em vez de cair. Reduzir
abaixo de 35 exigiria **extração de responsabilidade** (dividir a classe
`MemoryStore` em componentes menores com contrato próprio), não apenas
reposicionar código em arquivos — isso é, por definição, um ciclo de trabalho
diferente do que este hardening escopou. Registrado como item aberto na Fila
Pós-Hardening (§4), não como meta perdida silenciosamente.

### Testes

| Critério | Estado inicial (Fase 0) | Estado final |
|---|---|---|
| Testes automatizados | **0** (`git ls-tree e2b0b2d -- tests/` = vazio) | **63** (62 passed + 1 deselected, marker `windows_only`) — verificado rodando `pytest -q` na branch atual, resultado idêntico ao relatado em `RELATORIO_FASE4.md` |
| CI | Nenhum | `.github/workflows/tests.yml` (Fase 3, `f810cd4`) — `ubuntu-latest` + `windows-latest`, 2 jobs por push/PR |
| Bugs de produção interceptados pelo CI | — | **1 confirmado**: ver estudo de caso abaixo |
| `frozen_clock` — módulos injetados | 0 | `_CLOCK_BOUND_MODULES` em `tests/conftest.py`. **Nota de discrepância**: `RELATORIO_FASE4.md` (T1b/d) relata "26 módulos vivos" no commit de introdução (`d7922bf`); recontagem direta do arquivo nesse mesmo commit dá **24**; recontagem no HEAD atual (`cd4ac0d`, após os 2 módulos do split de memória entrarem na lista) dá **36**. Os dois números do relatório original (26) não batem com a leitura direta da fonte — registrado aqui em vez de repetido sem checar, mesma régua da Fase 0 |
| Testes hermetizados pós-incidente "env fantasma" | — | `EDP_PRESSURE_*` isolado por fixture — evita que env vars persistidas no host (ver §2) vazem para dentro da suite; `faa078d` (Fase 3) |

**Estudo de caso — o único bug de produção pego pelo CI Windows:**
PR #13 (Fase 4). O split de `memory.py` (`a5ab641`) moveu
`_atomic_write_json` byte-idêntico para `edp/memory/atomic_io.py`, mas a
lista de imports do módulo novo não trouxe `import time` — usado em
`time.sleep(delay)` dentro do loop de retry da Dívida técnica #8
(backoff em `PermissionError`/`OSError` de `os.replace`, específico do
Windows). A mensagem do próprio commit de extração já registrava a crença
equivocada: *"`import time` removido do preâmbulo (só era usado por
`time.sleep` dentro de `_atomic_write_json`, que já foi junto pro
`atomic_io.py`)"* — o corpo foi, o import não.

CI Windows do PR #13 falhou:
```
test_divida_8_atomic_write_retry_em_permission_error
NameError: name 'time' is not defined  (edp/memory/atomic_io.py:104)
```
Latente por construção: o caminho só executa sob `PermissionError` real em
`os.replace` — condição que só `test_divida_8_atomic_write_retry_em_permission_error`
(`tests/test_divida_regressions.py:29`, `@pytest.mark.windows_only`)
provoca via monkeypatch. A suite completa passava até no Windows; nenhum
gate Linux toca essa linha. Fix (`7eae9c2`): uma linha, `import time`
restaurado, com verificação de que o original (`git show
b20b2b4:edp/memory.py`) sempre teve o import — restauração, não mudança de
lógica. Varredura de classe (pyflakes) nos 3 módulos do split não achou
outros nomes indefinidos. Esse é exatamente o cenário que o CI de 2 jobs
(Fase 3) foi desenhado para pegar, e pegou — na primeira oportunidade em
que um caminho `windows_only` foi tocado por um refactor.

### Operacional

| Item | Estado inicial | Estado final |
|---|---|---|
| `restore_backup()` | **Nunca funcionou** — bug de naming silencioso | Corrigido (`d551af5`, Fase 3, T1): `incremental_backup()` gravava `"episodic_<ts>.json"` (sem prefixo de sessão); `restore_backup()` só reconhecia o glob `"{session}_{layer}_{ts}.json"` — nunca casava. Resultado real: `restore_backup()` retornava `{"ok": False, "reason": "Backup vazio"}` mesmo logo após um backup bem-sucedido. Fix de 1 linha (prefixar com `session_id`), achado escrevendo o teste hermético do ciclo completo (`tests/test_failsafe_roundtrip.py`, 228 linhas) |
| `/health` | `{"status": "ok"}` fixo, sem relação com o estado real | Checks reais (`9554173`, Fase 3, T2): `boot_state` (COLD/WARMING/SHUTDOWN → HTTP 503), `memory_store` (`is_valid()` na sessão já cacheada, sem I/O extra), `provider` (presença de API key, sem chamada de rede) |
| Runbook operacional | Inexistente | `docs/RUNBOOK.md` (Fase 3, `91c99b9`) — 11 seções: boot, fantasma de env, rollback por env var, semântica de pressure, relógio, stores produção/teste, quarentena, corrupção de `episodic.json`, credenciais, ruídos conhecidos, gates |
| Dívida #41 (threshold de RAM) | `CRITICAL` **permanente** — defaults (`CRITICAL_GB=1.2`/`WARNING_GB=2.0`) dimensionados para inferência local 8GB+, deployment real é API-only com RAM residente 0.28-1.45GB. 100% dos ticks de background pulados por dias | Recalibrada (`1217383`, Fase 2): defaults `0.30`/`0.60` — faixa observada volta a `NORMAL` em uso normal. Bug lateral achado e corrigido no mesmo commit: `websocket.py` hardcodava "1.2GB" na mensagem de erro ao usuário, dessincronizado do valor configurável |
| Primeiro ciclo metabólico completo | Nenhum — `background_loop` em `CRITICAL` constante impedia os 3 jobs (`cognitive_decisions` extractor, `auto_consolidation`, `health_index`/CHI) de rodar | Vivos pela primeira vez em uso normal, como efeito direto do fix da Dívida #41 (nota T2f do commit `1217383`: *"a guarda `blocked_toxic` [Fase 1] vira load-bearing... a tranca foi construída antes de a porta abrir"*) |
| Tags de rollback | 0 | **5**: `v3.16-quarantine-stable` (pré-hardening, `e2b0b2d`) → `v3.17` (Fase 1) → `v3.18` (Fase 2) → `v3.19` (Fase 3) → `v3.20` (Fase 4) |

---

## 2. Incidentes e lições

**Env fantasma (escopo User, 17/07/2026).** `EDP_PRESSURE_CRITICAL_GB=1.0`
e `EDP_PRESSURE_WARNING_GB=1.5` ficaram persistidas no escopo **User** do
Windows e derrotaram silenciosamente os defaults do repo (0.30/0.60) por
semanas — o boot nunca avisou, só o log `[pressure] psutil OK |
critical=...` mostrava o valor efetivo, e ninguém olhou até o sintoma
(`CRITICAL` quase permanente) aparecer nos smokes. Lição registrada em
`docs/RUNBOOK.md` §2: checar `[System.Environment]::GetEnvironmentVariable`
em User/Machine é o primeiro passo de qualquer investigação de "o código
está certo mas o boot não bate" — reconciliar sempre contra a fonte do
repo, nunca contra memória do que "deveria" estar setado.

**Clipboard → latin-1 (17/07/2026).** Um comentário com travessão (`—`)
colado sem querer no campo de API key virou parte da "key", causando 503
por erro de decodificação latin-1 no header HTTP. O sistema degradou
limpo (erro claro, não crash silencioso), mas custou 2 tentativas até
alguém notar que o problema era o clipboard, não a credencial. Lição:
sempre conferir o conteúdo do clipboard antes de colar uma API key — um
`Ctrl+C` de linha errada sobrevive até o paste.

**`unlink`/vboxsf — física de arquivo-aberto-no-Windows (3 exemplares).**
O mesmo modo de falha reapareceu em três contextos distintos ao longo do
ciclo: writes atômicos disputando `os.replace` com um leitor concorrente
(a própria Dívida #8, motivo do retry+backoff em `atomic_io.py`), operações
de arquivo dentro da VM Kali batendo em `vboxsf` (pasta compartilhada
VirtualBox, que tem semântica de lock mais frouxa/lenta que NTFS nativo),
e um `git pull` deixado meio-feito depois que um processo do host segurou
um arquivo aberto no meio da operação. Os três são a mesma física — no
Windows (e em compartilhamentos vboxsf), um arquivo "fechado" do ponto de
vista do processo A pode continuar bloqueado do ponto de vista do processo
B por um intervalo não-determinístico. É o motivo de fundo por trás de
tanto a Dívida #8 quanto da disciplina operacional de nunca rodar
smoke/teste contra `C:\edp_data` (produção) — `docs/RUNBOOK.md` §6.

**Roteiro colado atropelando etapa manual.** Um roteiro de comandos
copiado e colado em sequência pulou uma etapa que exigia confirmação/
inspeção manual entre passos — o tipo de erro que só acontece quando um
procedimento pensado para execução assistida é executado como um bloco
único. Reforça por que `docs/RUNBOOK.md` é deliberadamente "comandos
copiáveis, sem enrolação" mas organizado em seções curtas e não em um
único script: cada comando tem contexto de decisão ao lado, não é
pensado para ser colado inteiro sem pausa.

**Drift ~3h do host — a âncora NTP manda.** O host Windows tem drift
conhecido (~3h em 17/07/2026, provável fuso horário mal configurado). Em
qualquer investigação forense (ordem de eventos, "o que aconteceu antes de
quê"), a âncora temporal verificada (NTP/HTTP, `edp.clock`) é a fonte
confiável — o timestamp do log do SO pode estar simplesmente errado.
`docs/RUNBOOK.md` §5 documenta que `"[modo fallback]"` na âncora (boot sem
verificação online) exige desconfiança extra em qualquer timestamp daquela
sessão. Ver também §3 (gap aceito sobre a interação deste drift com
`detect_corruption`).

---

## 3. Gaps aceitos

- **WAL.** Confirmado na Fase 0 (Item 8) como proposta, não implementação —
  busca exaustiva por `WALEpisodicMemory` no código-fonte deu zero
  resultados; existe só como classe de exemplo em
  `docs/EDP_ARCHITECTURE_v4.md:114`. Mitigação atual, documentada em
  `docs/RUNBOOK.md` §8: write atômico (`_atomic_write_json`: tmp + fsync +
  rename) + failsafe (`incremental_backup`/`restore_backup`, corrigido
  nesta fase) + disciplina de backup manual. Risco conhecido, não
  resolvido — decisão de entrar num ciclo futuro é do pesquisador.

- **Truncamento genuíno mata o boot.** `_safe_load_json` só recupera
  corrupção do tipo "Extra data" (lixo depois de um array já fechado —
  o cenário pré-write-atômico). Truncamento real no meio de um objeto
  (array nunca fecha) propaga `JSONDecodeError` sem tratamento em
  `EpisodicMemory._load()`, o que quebra a construção do `MemoryStore`
  inteiro se acontecer no boot. Não corrigido — **pinado por teste**
  (`test_reload_apos_truncamento_no_meio_do_objeto_propaga_erro`,
  `d551af5`) que documenta o comportamento atual em vez de mascará-lo.
  Caminho de recuperação nesse cenário é `restore_backup()` a partir do
  backup mais recente, não `repair_episodic.py`.

- **Branch protection.** CI existe e roda (Fase 3), mas bloquear merge
  com CI vermelho **não é config de repositório** — é um passo manual no
  GitHub (Settings → Branches → Branch protection rules → `main` →
  "Require status checks to pass before merging" → marcar o job `tests`).
  Documentado em `docs/RUNBOOK.md` §11 como pendente; ninguém fez ainda.
  Tier de risco: baixo enquanto o time for pequeno e disciplinado em olhar
  o CI manualmente, mas é o tipo de gap que só dói quando alguém não olha.

- **`benchmark_edp.py` × `MEMORY_DIR` pós-split (o ALERTA δ, 3ª ocorrência).**
  `benchmark_edp.py` faz `mem_mod.MEMORY_DIR = tmp_dir` (atribuição direta
  de atributo de módulo, não `monkeypatch`) em dois pontos
  (`benchmark_edp.py:~389,~728`). Antes do split isso propagava porque
  `mem_mod` era o único módulo com esse nome vinculado. Depois do split
  (Fase 4), essa atribuição **não propaga mais** para
  `edp.memory.store.MEMORY_DIR`/`edp.memory.semantic.MEMORY_DIR` — cada
  submódulo do pacote tem sua própria cópia do nome importado, a mesma
  classe de bug que exigiu 2 patches em `tests/conftest.py` durante o
  próprio split (`53703bb`, `2feccf2`). **Não corrigido nesta fase**
  (corrigir tocaria a lógica de `benchmark_edp.py`, fora do escopo de
  `memory.py`) — **fix pendente OBRIGATÓRIO antes de rodar o benchmark**,
  ou os números medidos vão silenciosamente vazar/ler do diretório real em
  vez do `tmp_dir` isolado.

- **`AdaptiveController`/`MetaReasoner` vivos-mortos.** Medido, não
  hipotético (`FASE0_5_MEDICAO_SPLITBRAIN.md`, M1): `mem_results` do
  `SemanticMemory` paralelo alimentava `adaptive_decision` e `reflection`
  dentro de `run_pipeline()`, mas `adaptive_decision.to_dict()` só é
  gravado sob `debug=True` (nunca ativo nos 2 chamadores reais) e
  `reflection` é dead store (nunca lida depois de calculada). O código
  roda em todo turno, seu resultado não alcança nada observável. A cadeia
  de morte está documentada; remoção é decisão separada, não executada
  neste ciclo.

- **Eco do `session_summary` (N=2).** Em sessão quase-vazia (família
  `exp009`), o summary reflete o próprio prompt de volta quando não há
  conteúdo suficiente para sumarizar de verdade. Pendência registrada
  (`docs/RUNBOOK.md` §10) como ruído conhecido, não bug de dado.

- **Clock em modo fallback exercitado ao vivo pela 1ª vez (smoke
  18/07/2026, `v3.20`).** A degradação em si funcionou corretamente —
  âncora rotulada como estimativa, comportamento honesto, sem timestamp
  falsamente confiável. Mas em fallback os timestamps herdam o drift
  ~3h do host Windows (ver §2), o que cria interação potencial com
  `detect_corruption()` (`edp/failsafe.py`, migrado para `edp.clock` na
  Fase 4, `23f3d29`) — a função **rejeita timestamp futuro**, checagem
  baseada no clock desde a Fase 4. Se a verificação online falhar
  (fallback) justamente numa janela em que o relógio do host está ~3h
  adiantado, um timestamp gerado nessa sessão pode ler como "futuro" na
  próxima checagem de corrupção com âncora verificada. Não observado em
  produção ainda — é risco identificado no primeiro exercício ao vivo do
  fallback, não um incidente consumado. Conserto real = acertar o fuso do
  Windows (pendente); mitigação de curto prazo seria tolerância de janela
  em `detect_corruption()`, não implementada.

---

## 4. Fila pós-hardening

Ordem sugerida, do que mais bloqueia observação/correção para o que é
mais isolado:

1. **Dedup do retrieve** — `EpisodicMemory.retrieve()` (cosine com todos
   os multiplicadores: epistemic/source_type/dominance/anchor/session/
   piso) e o caminho híbrido (`_retrieve_hybrid`) têm lógica de scoring
   visivelmente duplicada, achado e registrado (não corrigido, MOVE-ONLY
   não permite) na Fase 4. É a interseção de 6 medições acumuladas ao
   longo do ciclo (Fase 0 Item 1, Fase 0 Item 6, Fase 0 Item 8 — o
   choke-point do piso/exclusão-híbrida — e as 3 medições de edges de
   `MemoryStore` em §1) que todas tocam a mesma tensão estrutural sem
   nenhuma resolvê-la. Merece pré-registro próprio (hipótese + critério de
   aceite antes de tocar código, no mesmo espírito da Fase 0) porque o
   scoring duplicado exposto na Fase 4 é exatamente o tipo de dedup que,
   mal feito, quebra o choke-point de defesa exp012/exp016 documentado em
   `store.py:9-21`.
2. **Fix do δ de `benchmark_edp.py`** (§3) — bloqueia confiança em
   qualquer número novo de benchmark até corrigido; baixo esforço (mesma
   classe de fix já aplicada 2x em `conftest.py`).
3. **NEG v2** — próximo ciclo de trabalho em negações (existe branch
   `fase0/memoria-vs-negacoes` no repo); não tocado neste hardening.
4. **Piso da `SemanticMemory`** — one-liner no choke-point já preparado em
   `store.py:9-21` ("onde o one-liner equivalente para `SemanticMemory`
   deve entrar quando o piso for estendido para lá"); a Fase 4 deixou o
   comentário-âncora pronto, falta o one-liner em si.
5. **Backfill de produção** — usar o extractor/consolidação/CHI agora
   vivos (§1, Operacional) para rodar backfill nas sessões de produção que
   ficaram sem extração enquanto o `background_loop` esteve preso em
   `CRITICAL` permanente (pré Dívida #41 fix); mesma máquina de
   `exp016_cognitive_decisions_backfill.py`/`backfill_audit.jsonl`
   (`docs/RUNBOOK.md` §7), aplicada a dados reais em vez de exp de
   laboratório.

---

PARAR.

# Relatório de mudanças — README.md e metodologia, 11/08/2026

Acompanha a reescrita de `README.md` e a criação de
`docs/edp_metodologia_v5.md`. Registra o que o README anterior dizia de
errado, por quê, e o que este processo verificou vs. herdou.

---

## O que o README antigo (v3.3) dizia que não existe mais

O `README.md` até 11/08/2026 descrevia uma arquitetura **v3.3** com um
bloco de 8 módulos "satélite" (`meta_stability.py`, `storm_guard.py`,
`economy.py`, `pressure_regulator.py`, `snapshot_manager.py`,
`decision_graph_v32.py`, `biodiversity.py`, `orchestrator_v32.py`) e
outros dois módulos citados (`semantic_memory.py`, `api_v2.py`,
`retrieval_ann.py`) que **não existem no repositório de hoje**.

Esses módulos foram removidos deliberadamente em 24/06/2026
(`RESULTADO_AUDITORIA_EDP_v5.md §5.2`, commit `f99835c` — 11 arquivos,
~5.500 linhas), depois de uma análise que concluiu que resolviam
governança de recursos (budget, homeostase, circuit-breaking) num
sistema cujo gap real era qualidade de sinal de memória — problemas
diferentes (`REAPROVEITAMENTO_SATELITES.md`: "apenas 1 dos 7 satélites
resolve necessidade confirmada por dado real").

O README nunca foi atualizado depois dessa remoção. Ficou descrevendo,
por 47 dias, um sistema que não existia mais no próprio repositório.

## O benchmark "84/84" era de um sistema anterior

A tabela de benchmark do README antigo listava suítes (`Scoring`,
`Snapshot Manager`, `Economy`, `Meta Stability`, `Storm Guard`,
`Biodiversity`, `Decision Graph`, `Belief Graph`) que correspondem aos
módulos removidos acima — não aos 298 testes reais de hoje (`pytest -q`,
11/08/2026), cujos nomes de arquivo não têm relação com aquela lista.

Não há um "84/84" para reproduzir hoje: o número certo, medido nesta
mesma data, é **298 passed, 1 deselected**.

## Modelo mental errado: "chame `EDPRuntime` direto"

O README antigo descrevia uso via `from edp.llm_adapter import
EDPRuntime; runtime.chat(...)`. O caminho vivo real hoje é
API/WebSocket (`run.py serve` → `edp/api/main.py` → 14 routers →
`edp/api/routes/websocket.py`) — `EDPRuntime` existe e é usado
internamente por esse caminho, não como biblioteca de uso direto
documentado.

## O que este processo verificou de novo vs. herdou sem checar

**Verificado nesta rodada, com comando ou `arquivo:linha` citado:**
suíte de testes completa; censo de dívida técnica; os 4 sinais órfãos
(`cognitive_decisions`, `contradiction_flagger`, `reflection.reweights`,
`RETRIEVAL_BACKEND`); o fechamento do crash de boot (Dívida #53);
conteúdo real de `edp/dashboard/`; dependências declaradas vs. importadas
(achou `ntplib` e `hnswlib` como gaps não catalogados antes); status de
merge das 30 branches remotas; os parâmetros hardcoded citados por
`NORTE.md §4.13`, incluindo dois locais adicionais de `score=0.65`
(`llm_adapter.py`) não citados pelo próprio NORTE.

**Herdado de relatório anterior, não re-executado nesta rodada** —
marcado explicitamente como tal em `docs/edp_metodologia_v5.md`: o
número de queda de dominância do exp009 (~87%→~33%); os vereditos
individuais de exp011 guardas 5-6; a confirmação de branch protection
ainda desligada no GitHub (citada por `docs/RUNBOOK.md`, não
reverificada via API do GitHub nesta sessão); o recálculo completo do
scorecard [P]/[C] de 10 dimensões de `AVALIACAO_ENGENHARIA_EDP.md`.

## Discrepâncias encontradas nos próprios documentos-fonte, registradas sem resolver

- `docs/edp_metodologia.md` assina "Mantenedor: Renato (autodidata)" —
  nenhum outro documento do repositório usa esse nome; todos os outros
  se referem a Daniel/`devBorgesr`. Não investigado a fundo; fica como
  nota na errata do próprio documento.
- `NORTE.md §4.8` fala em "5 dimensões de investigação prévia" e cita
  `docs/edp_metodologia.md` para o checklist completo, mas esse
  documento nomeia formalmente 4 dimensões (mais uma, custo de LLM, que
  aparece no checklist mas não como "Dimensão 5" nomeada da mesma forma
  que as outras quatro). Discrepância de contagem, não de conteúdo.
- `pyproject.toml` declara `version = "3.21.0"` — um número de versão
  real e específico que não aparece em nenhum título de documentação
  (que fala em "v3.3", "v4", "v5" como nomes de fase, não semver). Não
  fica claro se `3.21.0` é mantido manualmente ou está defasado; não
  investigado nesta rodada.

## O que não foi tocado

Por instrução explícita do prompt que originou esta tarefa: nenhuma
reorganização de arquivo, nenhuma remoção de código morto catalogado
(`vector_store.py`, `memory_graph.py`, `retrieval.py`, `RETRIEVAL_BACKEND`),
nenhuma correção do `pyyaml` duplicado em `requirements.txt`. Documentado,
não corrigido — decisão do dono do projeto, não deste processo.

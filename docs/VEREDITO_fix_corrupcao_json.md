# Veredito — Fix de corrupção/truncamento de JSON nos stores do EDP

Referência: [`docs/preregistro_fix_corrupcao_json.md`](preregistro_fix_corrupcao_json.md)
(congelado antes da implementação). Dívida: [#53](DIVIDAS.md).

Este documento é o contraste factual entre o que foi congelado e o que os
testes provaram de fato. Nada do critério de decisão é omitido, mesmo o
que não saiu perfeito.

---

## Correção pós-veredito (05/08/2026) — `profiles_registry` não ficou fechado

O commit que versionou `edp/profiles/registry.py` (`d83503f`, já citado
abaixo como achado do Passo 0) versionou **só esse arquivo**, sem o resto
do módulo `edp.profiles` (`models.py`, `__init__.py`, `selector.py`,
`tools.py`, `tracker.py` — todos permaneceram untracked). Consequência não
percebida na hora: `registry.py:23` importa `.models`, então **clone limpo
desta branch quebrava em `ModuleNotFoundError` na primeira `import edp`**
— confirmado com clone real, não suposição.

Fix (commit `2467020`): `git rm --cached edp/profiles/registry.py`
(destrackeia, preserva a mudança de quarentena no working tree) +
`tests/test_store_quarantine.py::TestProfileRegistryQuarantine` ganhou
`setup_method` com `pytest.importorskip("edp.profiles.models")` — a classe
pula em clone limpo em vez de quebrar a coleta do pytest, e volta a rodar
sozinha quando `edp.profiles` for versionado por inteiro.

**Números reais, medidos em clone limpo após o fix** (substituem os
números abaixo, que foram medidos contra o working tree, não contra um
clone):

```
tests/test_store_quarantine.py: 24 passed, 4 skipped (TestProfileRegistryQuarantine)
suíte completa:                 202 passed, 4 skipped, 1 deselected
```

A diferença para os "220 passed" originais não é só os 4 agora pulados:
14 testes a mais (`test_profiles_selector.py`, `test_profiles_tracker.py`)
existem no working tree local mas são untracked — em clone limpo eles nem
são coletados (o arquivo não existe), então nunca fizeram parte do que
esta dívida de fato fecha. `220 − 14 − 4 = 202` reconcilia exato.

**Item (a)/(b)/(c) do critério, revisado:** 5/6 call sites versionados e
comprovados; o 6º (`profiles_registry`) tem o código e o teste escritos,
mas **pendurado em módulo WIP não versionado** — não roda em clone limpo
até `edp.profiles` ser commitado por inteiro. Isto é **não-alcançado**,
registrado aqui em vez de omitido, seguindo a própria régua deste
documento ("nada do critério de decisão é omitido, mesmo o que não saiu
perfeito"). Ver `docs/DIVIDAS.md` #53 para o status atualizado.

---

## Premissas do Passo 0 — refutadas, registradas, não escondidas

O prompt original assumia `branch main @ 67f2f5b`, 5 call sites, e
`tests/test_health_check.py` falhando na coleta. **As três premissas
falharam** na verificação:

- Checkout real: `fix/toxic-guards @ cf91c96` (main é ancestral — nenhum
  código de main ficou de fora).
- Call sites reais: 7 (5 do prompt + `edp/ingest/session_index.py` +
  `edp/profiles/registry.py`, este último em diretório untracked).
  `_get_edp_lifetime` ficou fora de escopo (já protegido, baixo risco —
  reafirmado, não é uma decisão nova).
- `test_health_check.py` passa limpo (5/5) neste ambiente — a falha de
  coleta relatada no prompt não se reproduz aqui.

Confirmado com o pesquisador via pergunta direta ANTES de escrever o
pré-registro: escopo ampliado para os 6 call sites reais, implementação
em `fix/toxic-guards` (checkout atual). Detalhe completo no pré-registro,
seção "Passo 0".

---

## H1 — CONFIRMADA

> Substituir crash/perda-silenciosa por quarentena + degradação observável
> preserva 100% dos bytes do arquivo corrompido (em local separado) e
> produz pelo menos um sinal de observabilidade verificável por teste,
> sem regressão na suíte.

Provado por `tests/test_store_quarantine.py` (28 testes) — para cada um
dos 6 stores migrados (`episodic`, `semantic`, `echo_chamber`, `blocks`,
`session_index`, `profiles_registry`), individualmente:
- boot sobrevive a truncamento no meio (antes: crash em 4 dos 6, perda
  silenciosa sem log nos outros 2);
- arquivo `.corrompido-<timestamp>` byte-idêntico ao conteúdo corrompido
  original, confirmado por comparação direta de bytes;
- evento Pareto `store_degraded` presente e consultável via
  `get_pareto_store().query(event_type="store_degraded")`, verificado por
  asserção — não por leitura de log;
- controle negativo: arquivo válido não gera quarentena nem evento, em
  nenhum dos 6 stores (nenhum falso positivo).

Suíte completa: **220 passed, 1 deselected** (baseline 192 + 28 novos),
zero regressão, incluindo `test_flag_off_byte_identical.py` (8/8,
protege `EDP_TOXIC_GUARDS` — não relacionado a este fix, mas confirma que
o escopo não vazou). **Medido contra o working tree, não contra clone
limpo — ver "Correção pós-veredito" acima para o número que vale (202
passed, 4 skipped, 1 deselected) e por que os 6/6 stores citados abaixo
são, de fato, 5/6 versionados + 1/6 pendurado em WIP.**

## H2 — CONFIRMADA, com uma nota metodológica registrada

> O caminho de recuperação de `_safe_load_json` é O(n²)-ish em tentativas
> de parse e viola o limite de aceite declarado num arquivo de 10 MB
> truncado no meio.

**Medição exploratória (pré-fix, antes do pré-registro existir — ver nota
metodológica no próprio pré-registro):** arquivo de ~11.33 MB truncado no
meio não terminou em 300s (timeout do processo de medição, abortado).
Curva medida em tamanhos menores (0.435→2.396 MB) super-linear,
extrapolando para ~17 minutos em 10 MB. O texto do prompt original
("pode travar o boot por minutos") era otimista, não exagerado.

**Limite de aceite (X=20s), congelado no pré-registro ANTES de otimizar
qualquer código.** Resultado pós-fix, medido com a implementação REAL (não
protótipo) sobre o MESMO arquivo de ~11.33 MB:

```
MAX_RECOVERY_CANDIDATES = 20
tempo pós-fix (real) em 11.33 MB: 17.927s
```

**PASS, mas com margem apertada** (17.927s contra um limite de 20s — ~10%
de folga, não uma margem confortável). Registro isto explicitamente porque
o critério pedia honestidade sobre alcançado-mas-justo, não só
PASS/FAIL binário: se o tamanho típico de arquivo neste ecossistema
crescer (o pré-registro já cita arquivos de 15 MB observados), o mesmo
N=20 aproximaria ou estouraria X=20s de novo — a mitigação estrutural
(N capado, não o tamanho do arquivo) já existe, mas o limite X em si não
tem folga para 2× o tamanho testado sem revisão. Não é um blocker desta
rodada (o pré-registro não exigia margem, só o número abaixo do limite),
mas é uma dívida observacional que fica registrada aqui, não escondida
atrás de um "PASS" seco.

Redução de ~17 minutos (~1020s extrapolado) para 17.927s medido =
**~57× mais rápido**, e — mais importante que o fator — o comportamento
deixou de ser "sem limite superior conhecido" para "limitado por uma
constante (N), não pelo tamanho do arquivo".

---

## Critério de decisão — resultado item a item

| # | Critério | Resultado |
|---|---|---|
| (a) | Boot não crasha com JSON truncado no meio, em cada store migrado | **PASS 5/6 versionado**, 1/6 (`profiles_registry`) não-alcançado em clone limpo — ver correção acima |
| (b) | Arquivo corrompido original byte-idêntico em `.corrompido-<ts>` | **PASS 5/6 versionado**, mesma ressalva |
| (c) | Sinal de observabilidade verificável por asserção | **PASS 5/6 versionado**, mesma ressalva |
| (d) | Suíte no baseline do Passo 0, incl. `test_flag_off_byte_identical.py` | **PASS** — 202 passed, 4 skipped, 1 deselected em clone limpo (ver correção acima); 8/8 no teste nomeado |
| (e) | Nenhum código novo usa `except Exception: <algo> = []` sem log | **PASS** — os 2 sites que já tinham esse padrão (`echo_chamber.py`, `blocks.py`) ganharam log associado ao tocar o código |
| (f) | Tempo de recuperação em 10 MB abaixo de X=20s | **PASS, margem apertada** (17.927s) — ver nota acima |

**Dois pontos não saíram "limpos", registrados como tal, não como PASS
silencioso:** a margem de (f), e o call site `profiles_registry` de
(a)/(b)/(c), que existe em código e teste mas não roda em clone limpo por
depender de um módulo (`edp.profiles`) que não foi versionado por inteiro
— ver "Correção pós-veredito" no topo deste documento.

---

## Riscos a priori do pré-registro — o que aconteceu com cada um

1. **Quarentena por copiar+apagar falhando pela metade** → usado
   `os.replace` atômico, nunca copy+unlink. O sub-caso "o próprio
   `os.replace` falha" (ex.: permissão) é tratado no código (não bloqueia
   o boot, loga critical adicional) mas **não ganhou teste dedicado**,
   exatamente como o pré-registro já havia declarado antes de implementar
   — não é uma omissão nova, é a decisão registrada se cumprindo.
2. **`except Exception` genérico engolindo bugs não relacionados** →
   `_load_json_or_quarantine` captura especificamente
   `json.JSONDecodeError`/`UnicodeDecodeError`; qualquer outra exceção
   propaga. Confirmado por leitura do código final.
3. **Quebra de contrato do teste antigo** → reescrito para
   `test_reload_apos_truncamento_no_meio_do_objeto_quarentena_e_degrada`,
   docstring referenciando o pré-registro, histórico preservado no
   `git log` (nunca apagado em silêncio).
4. **Cap de candidatos mudando o que é recuperável** → confirmado e
   testado (`test_cap_de_candidatos_muda_o_que_e_recuperavel`): um
   arquivo com mais de `MAX_RECOVERY_CANDIDATES` colchetes/chaves soltos
   na cauda, recuperável pelo algoritmo antigo (tempo ilimitado), agora é
   quarentenado em vez de recuperado. Mudança de comportamento
   deliberada, documentada, testada — não um efeito colateral descoberto
   tarde.

## Achado extra durante a escrita dos testes (não estava no pré-registro)

Ao escrever os testes de `SessionIndex`/`ProfileRegistry` com `tmp_path`
puro (sem `isolated_base_dir`), o singleton `FileParetoStore` vazou para
`<cwd>/data/pareto/events.jsonl` — um diretório real do projeto (não o
tmp isolado do teste), porque `EDP_BASE_DIR` não estava sobrescrito nesse
caminho de teste. Detectado pela própria asserção de observabilidade
(contagem de eventos vinha errada — 3 em vez de 1, sinal de que o arquivo
não estava isolado entre testes), corrigido antes do commit: os 4 testes
passaram a usar `isolated_base_dir`, e um fixture autouse
(`_reset_pareto_store_singleton`, `tests/conftest.py`) foi adicionado para
proteger qualquer teste futuro que use `pareto_store` do mesmo problema.
O arquivo poluído (7 linhas, todas de paths `pytest-of-*` desta sessão de
testes, nada de dado real) foi apagado. Registro isto porque é exatamente
o tipo de achado que este pré-registro pede para não esconder — mesmo
sendo um bug no MEU código de teste, não no fix em si.

---

## Entregáveis

1. [`docs/preregistro_fix_corrupcao_json.md`](preregistro_fix_corrupcao_json.md)
   — commitado antes da implementação (commit `fe08696`).
2. Implementação — `store.py` migrado sozinho primeiro (commit `17beef1`),
   depois os outros 5 call sites no mesmo padrão de uma linha por
   arquivo.
3. `tests/test_store_quarantine.py` (28 testes, novo) +
   `tests/test_failsafe_roundtrip.py` (reescrita do teste de truncamento).
4. [`docs/DIVIDAS.md`](DIVIDAS.md) — Dívida #53, status FECHADA COM RESSALVA
   (ver correção pós-veredito acima).
5. Este documento.
6. Commit `2467020` — destrackeia `edp/profiles/registry.py`, guarda
   `TestProfileRegistryQuarantine` com `importorskip`, corrige este
   veredito e a Dívida #53.

Sem push. Implementação parada aqui para validação do pesquisador.

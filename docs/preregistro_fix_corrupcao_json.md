# Pré-registro — Fix de corrupção/truncamento de JSON nos stores do EDP

**Status:** congelado antes da implementação (este documento é commitado
primeiro; a implementação vem depois, em commits separados).
**Dívida associada:** [Dívida #53](DIVIDAS.md#dívida-53--crash-ou-perda-silenciosa-em-truncamento-no-meio-de-json-nos-stores)
(atribuída por este documento).
**Autor:** Claude (engenharia), a pedido do pesquisador.
**Data:** 04/08/2026.

---

## Passo 0 — verificação de premissas (feita ANTES de qualquer código)

O prompt original desta tarefa assume `branch main` @ `67f2f5b`, 5 call
sites de `_safe_load_json`, e `tests/test_health_check.py` falhando na
coleta por conflito `email-validator`/`pydantic`. **Nenhuma dessas três
premissas se confirmou neste checkout.** Registro o que encontrei, com os
comandos exatos do Passo 0:

### Branch e baseline reais

```
$ git rev-parse --short HEAD
cf91c96
$ git branch --show-current
fix/toxic-guards
```

`main` (tip `100b0c8`) é ancestral de `HEAD` — `fix/toxic-guards` contém
tudo que `main` tem, mais 32 commits (T1-T6 do fix de toxic-guards, exp017
dedup, e o commit mais recente `cf91c96`, que adiciona `edp/ingest/`). A
decisão de prosseguir em `fix/toxic-guards` em vez de trocar para `main`
foi confirmada com o pesquisador (ver seção "Decisões confirmadas com o
pesquisador" abaixo) — nenhum código de `main` fica de fora.

### Call sites reais — 7, não 5

```
$ grep -rn "_safe_load_json(" --include="*.py" edp/
edp/blocks.py:242
edp/echo_chamber.py:645
edp/ingest/session_index.py:40      ← NÃO estava no prompt original
edp/memory/atomic_io.py:117         (definição da função)
edp/memory/store.py:126             (_get_edp_lifetime)
edp/memory/store.py:338             (EpisodicMemory._load)
edp/memory/semantic.py:53           (SemanticMemory._load)
edp/profiles/registry.py:58         ← NÃO estava no prompt original,
                                        diretório untracked (WIP alheio)
```

`edp/ingest/` **existe** neste checkout (adicionado no commit `cf91c96`,
"receptor de eventos do sensor"), contradizendo a premissa do prompt de que
o diretório inteiro não existe. O prompt instrui explicitamente: *"se ele
existir na sua árvore, você está em outra branch — pare e reporte antes de
continuar."* Parei, reportei ao pesquisador via pergunta direta, e recebi
autorização explícita para prosseguir com escopo ampliado (ver abaixo).

Tabela real (linha conferida por leitura direta do arquivo, não só grep):

| Call site | Estado | Linha real (prompt dizia) |
|---|---|---|
| `store.py::_get_edp_lifetime` | protegido (`try/except Exception: pass`) | L126 (prompt: 124) |
| `store.py::EpisodicMemory._load` | **desprotegido (crash)** | L338 (prompt: 336) |
| `semantic.py::SemanticMemory._load` | **desprotegido (crash)** | L53 (bate) |
| `echo_chamber.py::_load` | engole em silêncio | L645 (bate) |
| `blocks.py::_load` | engole em silêncio | L242 (bate) |
| `ingest/session_index.py::_load` | **desprotegido (crash)** — não previsto | L40 |
| `profiles/registry.py::_load` | **desprotegido (crash)** — não previsto, dir. untracked | L58 |

### Baseline real da suíte

```
$ python3 -m pytest tests/ -q
192 passed, 1 deselected in 17.68s
```

`tests/test_health_check.py` **não falha** neste ambiente (`email-validator
2.3.0`, `pydantic 2.13.4` — sem conflito). A premissa do prompt sobre essa
falha de coleta não se confirma. Baseline real usado em todo este documento:
**192 passed, 1 deselected, ~18s.** Não há exclusão de arquivo necessária.

`requirements.txt` tem uma modificação não commitada (`+pyyaml>=6.0`, do
trabalho untracked de `edp/profiles/`) — não mexida aqui, só registrada.

### Decisões confirmadas com o pesquisador (pergunta direta, antes de escrever este documento)

1. **Escopo:** migrar os 7 call sites reais (não os 5 do prompt original).
   `_get_edp_lifetime` continua **fora de escopo** (já protegido, baixo
   risco — decisão original do prompt, reafirmada, não nova). Ficam **6**
   call sites migrados nesta rodada:
   `store.py::EpisodicMemory._load`, `semantic.py::SemanticMemory._load`,
   `echo_chamber.py::_load`, `blocks.py::_load`,
   `ingest/session_index.py::_load`, `profiles/registry.py::_load`.
2. **Branch:** continuar em `fix/toxic-guards` (checkout atual), não trocar
   para `main`.

---

## Hipóteses

**H1** — Substituir crash (nos 3 call sites desprotegidos) e perda
silenciosa (nos 2 que engolem `Exception`) por quarentena + degradação
observável preserva 100% dos bytes do arquivo corrompido original (movido,
nunca apagado, em local separado) e produz pelo menos um sinal de
observabilidade verificável por asserção de teste, sem regressão na suíte
(baseline: 192 passed, 1 deselected).

**H2** — O caminho de recuperação de `_safe_load_json` (loop
`for cut in range(len(content), 0, -1)`, um `json.loads` por candidato) é,
no pior caso, O(tentativas) × O(parse por tentativa) — até O(n²) — e viola
o limite de aceite declarado abaixo (seção "Limite de aceite (X),
congelado antes da otimização") num arquivo de 10 MB genuinamente
truncado no meio (nenhum candidato parseia, o loop se esgota).

### Nota metodológica sobre a ordem da medição (transparência, não omissão)

O texto original do prompt pede, no Passo 0.5, para medir **antes** de
escrever este pré-registro ("meça antes de mexer... registre o número no
pré-registro") — a decisão de sequência é da própria tarefa, porque sem
saber a magnitude do problema eu não saberia se a otimização entra no
escopo desta rodada. Essa medição exploratória rodou antes deste arquivo
existir; ela **não** é o teste que decide H2 — é o que me permitiu
escrever a seção seguinte com um limite justificado em vez de arbitrário.
O número que decide PASS/FAIL do critério (f) é o **limite de aceite (X)**
declarado na próxima seção, **antes** de qualquer otimização de código ou
remedição — esse sim, na ordem certa. Registro esta distinção explicitamente
porque o rigor do resto do documento exige isso, não porque encontrei uma
saída elegante — não encontrei; é o melhor que dá para fazer tendo o texto
da tarefa pedido a medição antes do documento.

### Medição exploratória (pré-otimização, informal quanto a X — ver nota acima)

Arquivo gerado: lista de entries no formato real de `episodic.json`
(`id`, `text`, `embedding` 384-dim, `timestamp`, `scope`, `source_type`,
`answer_class`, `session_id`), truncado em ~70% do tamanho, ponto de corte
ajustado para cair dentro do array de floats do `embedding` (garante que
não sobra `]`/`}` parseável por acidente — truncamento genuíno no meio, o
pior caso).

| Tamanho truncado | Tempo `_safe_load_json` (código atual, não otimizado) |
|---|---|
| 0.435 MB | 2.236 s |
| 0.653 MB | 4.842 s |
| 1.307 MB | 17.915 s |
| 2.396 MB | 58.955 s |
| ~11.33 MB | **> 300 s (timeout do processo de medição, abortado)** |

Curva claramente super-linear (0.435→0.653 MB, ~1.5×, tempo ~2.2×;
1.307→2.396 MB, ~1.8×, tempo ~3.3×) — consistente com O(n²): número de
tentativas cresce com o tamanho do arquivo (mais colchetes/chaves internos
do array de embeddings), e cada tentativa reparseia um prefixo quase do
tamanho inteiro do conteúdo. Extrapolando a partir da curva (fator ~4.2×
entre 2.396 MB e ~10 MB, tempo escalando por ~4.2² ≈ 17.6×): **~17 minutos
em 10 MB** — consistente com o timeout de 300s observado diretamente.
**H2 confirmada** de forma inequívoca: o texto do prompt ("pode travar o
boot por minutos") era, se algo, otimista.

### Limite de aceite (X), congelado antes da otimização

**X = 20 segundos** para recuperação (irrecuperável, pior caso) num
arquivo de 10 MB truncado no meio, **após** a otimização do Passo 0.5.
Este número é declarado agora, antes de tocar em `_safe_load_json`, e é o
que decide o critério (f) abaixo. Justificativa da escolha de 20s: ver
"N — número de candidatos" logo abaixo; 20s é o valor interpolado da curva
de `N × custo_por_tentativa` medida em protótipo (ver próxima seção),
arredondado para cima com folga.

### N — número de candidatos de recuperação, justificado antes da implementação

A otimização troca o loop caractere-a-caractere por
`content.rfind("]"/"}", 0, pos)` (busca em C, não em Python puro) **e**
limita o número de tentativas de `json.loads` a uma constante `N`. `rfind`
sozinho não resolve o problema — o custo dominante medido é o `json.loads`
repetido sobre prefixos quase do tamanho inteiro do arquivo (confirmado em
protótipo: 64 tentativas em ~11 MB = 50.2s, ~0.78s/tentativa, escala
linear em N — ver sweep abaixo). É o **cap em N** que muda o pior caso de
O(tentativas-no-arquivo) para O(N), constante.

Sweep em protótipo (mesmo arquivo de ~11.33 MB truncado no meio):

| N | tentativas usadas | tempo |
|---|---|---|
| 4 | 4 | 3.48 s |
| 8 | 8 | 6.46 s |
| 16 | 16 | 12.65 s |
| 32 | 32 | 25.42 s |
| 64 | 64 | 50.21 s |

**N = 20 escolhido** (não é número mágico): o único write path deste
código é `_atomic_write_json` (`edp/memory/atomic_io.py`) — tmp → fsync →
`os.replace`. Isso estrutura o universo de corrupção possível: como o
`os.replace` só promove o `.tmp` depois que o write inteiro terminou, o
arquivo final nunca pode ser a concatenação de duas gerações de conteúdo
(um arquivo antigo inteiro sobrevivendo atrás de um novo) — a única forma
de corrupção realista é a cauda de UMA escrita interrompida (processo
morto, disco cheio, etc.), no máximo alguns registros incompletos. Cada
registro incompleto contribui no máximo ~2 colchetes/chaves soltos (o
array do próprio `embedding` + o dict da própria entry). N=20 cobre com
folga até ~10 registros incompletos de lixo — nenhum cenário realista
deste codebase chega perto disso — enquanto mantém o pior caso (arquivo
genuinamente irrecuperável, típico do bug desta tarefa) abaixo de X=20s
mesmo em 10 MB. Acima de N tentativas, degrada para quarentena em vez de
tentar para sempre — que é exatamente o comportamento-alvo desta tarefa
(nunca fica pendurado, nunca faz retry-loop).

**Efeito colateral esperado, testado à parte (risco a priori, ver
abaixo):** existe um arquivo teoricamente construível com >20
colchetes/chaves soltos na cauda, recuperável pelo código ATUAL (dado
tempo ilimitado) mas que passa a ser tratado como irrecuperável — e
portanto quarentenado — pelo código otimizado. Isso é uma mudança de
comportamento deliberada, não um bug; ganha teste dedicado
(`test_cap_de_candidatos_muda_o_que_e_recuperavel`, ver seção de testes).

---

## Investigação de observabilidade (antes de propor mecanismo novo)

Investiguei `edp/runtime/health_index.py::CognitiveHealthIndex` primeiro,
como pedido. **Não é reaproveitável para este sinal**: é um calculador de
score composto (Gauss + Bayes + CoOccurrence + cognitive_decisions →
número único 0-1, classificado NASCENT/GROWING/DEVELOPING/MATURE), sem
qualquer noção de "evento degradado" — não tem um `emit()` genérico, não
tem tipos de evento, e sua persistência (`health_history.jsonl`) é
especificamente snapshots de CHI, não um log de eventos arbitrários.

Encontrei o substrato certo em `edp/runtime/pareto_store.py`:
`FileParetoStore` já é exatamente isto — um event store JSONL append-only,
genérico, com `EVENT_TYPES` (frozenset de tipos permitidos), `emit(dict)`,
`query(event_type=..., since_ts=...)` para leitura filtrada, e uma
convenção de helpers `emit_<tipo>()` (`emit_memory_added`,
`emit_camara_outcome`, etc.) — cada um try/except-envolvido, nunca propaga,
sempre loga se falhar. **Reaproveitado em vez de construído do zero**:
adiciono o tipo `"store_degraded"` a `EVENT_TYPES` e um helper
`emit_store_degraded()`, seguindo byte-a-byte o mesmo padrão dos helpers
existentes. Verificável por teste via
`get_pareto_store().query(event_type="store_degraded")` — assinatura já
testável sem tocar em log.

---

## Métricas

1. Nº de call sites migrados: **6** (dos 7 reais; `_get_edp_lifetime`
   fica fora, decisão reafirmada).
2. Presença e integridade byte-a-byte do arquivo de quarentena — testado
   por comparação de bytes (`Path.read_bytes()`) entre o conteúdo
   corrompido original (capturado antes do boot) e o conteúdo do arquivo
   `.corrompido-<timestamp>` depois do boot.
3. Presença do sinal de observabilidade — testado via
   `get_pareto_store().query(event_type="store_degraded")`, por asserção,
   não por captura de log.
4. Contagem da suíte antes/depois — baseline real do Passo 0:
   **192 passed, 1 deselected**.
5. Tempo de recuperação em arquivo de 10 MB, antes (medido acima,
   informalmente: >300s / ~17min extrapolado) e depois (contra o limite
   X=20s congelado acima).

---

## Critério de decisão (congelado antes do primeiro teste novo)

PASS somente se:

**(a)** Boot não crasha com JSON truncado no meio, em cada um dos 6 stores
migrados, individualmente.

**(b)** O arquivo corrompido original existe, byte-idêntico, em
`<path>.corrompido-<timestamp>` depois do boot — nada perdido, só movido.

**(c)** Pelo menos um sinal de observabilidade foi emitido e é verificável
por asserção de teste (`pareto_store.query(event_type="store_degraded")`),
não por inspeção visual de log.

**(d)** A suíte continua no baseline do Passo 0 (192 passed, 1 deselected),
incluindo `test_flag_off_byte_identical.py` (protege `EDP_TOXIC_GUARDS`,
não relacionado a este fix — mas se quebrar, é sinal de vazamento de
escopo) — se esse quebrar, o escopo vazou.

**(e)** Nenhum código novo usa `except Exception: <algo> = []` sem log
associado — proibido nesta rodada.

**(f)** O tempo de recuperação em 10 MB (pior caso, irrecuperável) fica
abaixo de X=20s, declarado acima, antes de qualquer otimização.

---

## Escopo explícito

**Ordem obrigatória, conforme prompt:** migrar `store.py::EpisodicMemory._load`
SOZINHO primeiro, com testes completos rodando e verdes, e só então
replicar o mesmo padrão (edição de uma linha por arquivo — troca da
chamada de `_safe_load_json` por `_load_json_or_quarantine`) para os
outros 5. Se o desenho da quarentena tiver problema, aparece em um
arquivo, não em seis.

**4 call sites do prompt original + 2 achados nesta auditoria = 6 migrados.**
Justificativa de trazer os 2 extras: é o mesmo defeito (crash em
truncamento no meio), mesma causa raiz (`_safe_load_json` sem proteção),
mesmo fix — tratá-los diferente do resto seria inconsistente com a própria
tese desta tarefa ("é o mesmo defeito com sintomas diferentes").
`profiles/registry.py` é diretório untracked (trabalho em andamento de
outra pessoa/sessão) — só a linha da chamada a `_safe_load_json` é tocada,
nada mais nesse módulo.

**Feature flag: `EDP_STORE_QUARANTINE`, default `True` (ON).** Diferente
de `EDP_HYBRID_RETRIEVAL`/`EDP_CTX_SLOTS`/`EDP_WRITE_PROVENANCE`/
`EDP_TOXIC_GUARDS` (todas guardam features em rollout, com o estado
"seguro para reverter" sendo o *antigo* comportamento), aqui o estado
seguro é o *novo* — crash-on-corrupt e perda-silenciosa não têm defensor
óbvio (ver seção "Por que só embrulhar em try/except é a resposta errada"
do prompt original). A flag existe como válvula de emergência para ESTE
mecanismo especificamente (ex.: se a lógica de quarentena tiver um bug
não previsto), não como A/B de feature. **Não compartilha flag com
nenhum rollback de feature existente** — nome próprio, lido só dentro de
`_load_json_or_quarantine`, nunca perto de `EDP_TOXIC_GUARDS`/
`EDP_WRITE_PROVENANCE` (a razão explícita: o projeto já mediu esse
antipadrão — guarda de toxicidade morrendo junto com
`EDP_WRITE_PROVENANCE=0` — e repetir aqui seria regressão conhecida).
Com a flag `False`, `_load_json_or_quarantine` apenas repropaga a exceção
original — comportamento idêntico ao pré-fix, para rollback sem deploy.

**Dívida:** [#53](DIVIDAS.md) — atribuída por este documento, fechada no
commit final da implementação.

---

## Riscos a priori (escritos antes de implementar)

1. **Quarentena por copiar+apagar pode falhar pela metade** → usa
   `os.replace(path, path.with_name(path.name + f".corrompido-{ts}"))`
   — atômico, mesma partição, nunca `shutil.copy` + `unlink`. Se o
   `os.replace` em si falhar (ex.: permissão), o código não trava o boot:
   loga `critical` adicional indicando falha da quarentena em si, e segue
   para a inicialização vazia mesmo assim (nunca bloqueia, nunca faz
   retry-loop) — o arquivo original fica onde estava, não byte-idêntico
   *movido*, mas também não perdido. Este sub-caso (falha do `os.replace`)
   não ganha teste dedicado nesta rodada — é defensivo, não uma parte
   testável do critério de decisão (que assume filesystem saudável).

2. **`except Exception` genérico engole bugs não relacionados** → captura
   especificamente `json.JSONDecodeError` e `UnicodeDecodeError` (o
   arquivo tem bytes inválidos para UTF-8 — hoje isso também propaga sem
   ser pego por `_safe_load_json`, que só trata `json.JSONDecodeError`
   explicitamente; `UnicodeDecodeError` não é subclasse dela). Todo o
   resto (ex.: `PermissionError` ao abrir o arquivo) propaga sem ser
   interceptado — não é "corrupção do dado", é outra classe de falha.

3. **A mudança quebra o contrato de
   `test_reload_apos_truncamento_no_meio_do_objeto_propaga_erro`** — hoje
   espera o crash. Renomeado para
   `test_reload_apos_truncamento_no_meio_do_objeto_quarentena_e_degrada`
   (novo nome, novo contrato, docstring referenciando este documento).
   Nunca apagado nem alterado em silêncio — `git log` preserva o teste
   antigo.

4. **O ganho do Passo 0.5 pode mudar qual corrupção é recuperável** —
   confirmado acima (seção "N — número de candidatos"): um arquivo com
   >20 colchetes/chaves soltos na cauda, recuperável pelo código antigo
   (dado tempo ilimitado), passa a ser irrecuperável (→ quarentena) pelo
   novo. Ganha teste dedicado que constrói esse caso de fronteira
   explicitamente e assere o novo comportamento (quarentena, não crash —
   ainda assim uma melhoria sobre o estado atual, que crasharia de
   qualquer forma nos 3 call sites desprotegidos).

---

## Especificação técnica (uniforme nos 6 call sites)

1. Tenta parse normal (`json.load`) → tenta recuperação de "lixo no final"
   (existente, otimizada pelo Passo 0.5: `rfind` + cap `N=20`) → se as
   duas falharem com `JSONDecodeError`/`UnicodeDecodeError`:
2. Preserva o original via
   `os.replace(path, path.with_name(path.name + f".corrompido-{ts}"))`.
3. `logger.critical(...)` com `exc_info=True` (mesmo padrão de
   `edp/scoring.py:371`), incluindo path original, path de quarentena e
   tipo de erro.
4. Retorna `None` — os 6 call sites já tratam `None` como "inicializa
   vazio" (é o mesmo contrato que já usam para `FileNotFoundError`; nenhum
   deles precisa de lógica nova além de trocar o nome da função chamada).
   Tratado como degradação explícita: logado em `critical`, nunca
   confundido com sucesso.
5. Emite `emit_store_degraded(...)` (novo helper em `pareto_store.py`,
   reaproveitando `FileParetoStore` — ver seção de observabilidade acima).
6. Nunca deleta o arquivo corrompido automaticamente. Nunca faz
   retry-loop. Gated por `EDP_STORE_QUARANTINE` (default `True`) — com a
   flag `False`, repropaga a exceção original (comportamento pré-fix).

---

## Testes obrigatórios (planejados; implementação e resultado no veredito)

- Boot bem-sucedido com truncamento no meio do objeto, para cada um dos 6
  stores migrados, individualmente.
- Arquivo de quarentena presente e byte-idêntico ao conteúdo corrompido
  (comparação de bytes, não de hash — arquivo pequeno o bastante para
  comparação direta).
- Arquivo válido permanece intocado (nenhuma quarentena, nenhum falso
  positivo) — teste negativo por store.
- Sinal de observabilidade verificado por asserção
  (`pareto_store.query(event_type="store_degraded")`).
- Reescrita de `test_reload_apos_truncamento_no_meio_do_objeto_propaga_erro`
  → `..._quarentena_e_degrada`, referenciando este documento no docstring.
- Teste do cap de candidatos mudando o que é recuperável (risco a priori
  #4) — arquivo sintético com >20 colchetes soltos na cauda.
- Teste de performance do Passo 0.5: usa arquivo MENOR que 10 MB
  (justificativa: o teste de perf sintético precisa rodar em toda
  execução da suíte — 10 MB truncado e irrecuperável, mesmo otimizado,
  ainda leva segundos de sobra; um arquivo de ~1 MB com o mesmo N=20
  já exercita o cap por completo — o comportamento limitante é o número
  de tentativas, não o tamanho do arquivo em si, uma vez que o cap está
  ativo) — limite de tempo escalado proporcionalmente.
- Suíte completa no baseline do Passo 0 (192 passed, 1 deselected),
  incluindo `test_flag_off_byte_identical.py`.

---

## Entregáveis desta tarefa

1. Este documento (commitado antes da implementação).
2. Implementação, na ordem definida (`store.py` sozinho primeiro).
3. Testes novos + reescrita do teste existente.
4. Entrada Dívida #53 em `docs/DIVIDAS.md`.
5. `docs/VEREDITO_fix_corrupcao_json.md` — congelado vs. provado, sem
   omissões.

Sem push. Implementação para no fim, para validação do pesquisador.

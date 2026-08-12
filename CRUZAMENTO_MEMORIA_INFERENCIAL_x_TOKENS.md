# Cruzamento — memória inferencial × orçamento de tokens

**Data:** 2026-08-12. Cruza duas conversas coladas: (1) memória que registra
**inferências** com atenção/confiança/evidência e ciclo de vida de hipótese;
(2) destilação "boas/más notícias" da análise de tokens, com uma
especificação de Fase 1 e um pedido de implementação. Feito **antes** de
escrever qualquer código, a pedido.

---

## 1. A conversa 2 é fiel à minha análise — com quatro defeitos na especificação

Os 4 pontos bons e os 4 pontos ruins da destilação batem com o que eu
tinha verificado no código. Não encontrei número inventado nem
inversão de sentido. O problema não está no diagnóstico; está no
**código proposto**, que contradiz padrões que este repositório já
estabeleceu e testou.

### 1.1 — `datetime.utcnow()` reintroduz exatamente o que `edp/clock.py` existe para impedir

`edp/clock.py:1-27` abre com: *"Substitui `time.time()` em todo o código
para garantir tempo confiável independente do relógio do sistema
operacional"* — sincroniza via NTP no boot, mantém contador monotônico,
e expõe `is_verified()` para marcar dados gravados em modo não-confiável.
Toda a Peça 0.1 foi construída para isso.

A especificação usa `datetime.utcnow()`, que é o relógio do SO — a coisa
exata que a peça substitui. Consequência prática: se a máquina tiver
drift (o cenário que motivou o clock), o dado de calibração fica com
timestamp errado e ninguém saberia, porque não haveria o flag
`is_verified()` junto. Correção: `from ..clock import now as _now` e
gravar também `clock_verified`, como `llm_adapter.py:2055` já faz para a
Âncora Temporal.

(Secundário: `datetime.utcnow()` está deprecado a partir do Python 3.12 —
a suíte roda em 3.11 hoje, então não quebra agora, mas já nasce com
dívida.)

### 1.2 — "sem try/except pesado" quebra o contrato de todo emissor do projeto

A especificação diz *"síncrona, append-only, não bloqueante (sem
try/except pesado)"*. São três afirmações e duas se contradizem: uma
escrita síncrona em arquivo, no caminho da resposta do LLM, **é**
bloqueante.

Mais grave: sem try/except, um `PermissionError`/`OSError` (disco cheio,
permissão) propaga para dentro da chamada do LLM e derruba uma resposta
ao usuário — para gravar telemetria. Todo emissor existente do projeto
faz o oposto, deliberadamente:

```python
# edp/runtime/pareto_store.py:459-472 — emit_task_started
try:
    ...
    get_pareto_store().emit(evt)
except Exception as e:
    logger.warning("[pareto] emit_task_started falhou: %s: %s", ...)
```

O mesmo padrão aparece em `emit_task_completed`, `emit_mode_switched`, e
em cada ponto onde `llm_adapter.py` chama Pareto (`try/except` com
`logger.debug`). Telemetria nunca derruba caminho vivo — é regra, não
acaso.

### 1.3 — canal de telemetria paralelo, quando já existe um consumido

A especificação cria `edp/runtime/token_telemetry.py` com log próprio.
Mas `edp/runtime/pareto_store.py` já é o canal de telemetria do projeto,
e — diferente dos 4 sinais mortos do `README.md §4` — os eventos dele
**são consumidos**: `bayes_calibrator.py:72-74` e
`gauss_calibrator.py:76-78` leem `task_started`/`task_completed`.

Um segundo canal significa: dois formatos, duas rotações, dois lugares
para procurar, e nenhum dos calibradores existentes enxergando o dado
novo. Se o objetivo é calibrar, o dado precisa chegar onde os
calibradores já olham.

### 1.4 — caminho relativo quebra a convenção de `BASE_DIR`

`Path("data/token_calibration.log")` é relativo ao CWD — o arquivo muda
de lugar conforme de onde o processo foi lançado (`python run.py serve`
da raiz vs. de outro diretório vs. sob systemd). Todo artefato
persistente do EDP usa `BASE_DIR`, sobrescrevível por env
(`config.py:9-12`): `METRICS_LOG = BASE_DIR / "metrics.jsonl"`,
`LIVE_FEED_LOG`, `MEMORY_DIR`.

Ressalva honesta: `data/` **está** no `.gitignore` (linha 17) e já existe
localmente — então isto **não** é risco de vazamento por commit, só
fragmentação de telemetria. Não inflo o achado.

---

## 2. O defeito que corromperia o resultado em silêncio: `prompt_chars` não está definido

Este é o mais sério, e não aparece na lista de "más notícias" da
conversa 2 porque está escondido num nome de parâmetro.

A razão que se quer calibrar é `chars ÷ tokens`. O denominador é exato —
vem da API. O numerador é uma **escolha de desenho** que a especificação
não faz:

- só o texto do usuário?
- system prompt + todas as mensagens?
- incluindo o andaime JSON do payload?

A API cobra o **payload inteiro** — system prompt, histórico, todos os
blocos de contexto. Se o numerador contar só parte disso, a razão sai
sistematicamente baixa, com aparência de número medido. Trocar o `4.0`
chutado por um `2.7` mal-medido é pior que manter o chute, porque o
segundo carrega autoridade de dado.

Onde isto se decide em código: `_build_payload()`
(`edp/llm/providers/anthropic.py:131`) é quem monta o que de fato vai
para a rede. O numerador honesto é o tamanho do payload que **essa
função produz**, não o de `request.messages` antes dela.

Complicação adicional, no caminho streaming: `_input_tokens_reported` vem
de `message_start` e `_output_tokens_reported` de `message_delta`
(`anthropic.py:370-385`) — chegam em momentos diferentes e **qualquer um
pode ser `None`** (o código já trata isso, imprimindo `"?"` no log da
linha 395). A especificação não diz o que fazer quando é `None`.
Registrar `None` como `0` polui o dataset com pares falsos; a regra certa
é descartar a amostra.

---

## 3. O teste proposto faria a suíte bater na API real

*"Chama a API com e sem a instrumentação, compara as respostas."*

A suíte deste projeto é **100% sintética** e nunca sai para a rede
(`README.md §4`). Um teste assim custaria dinheiro por execução, seria
não-determinístico e quebraria no CI (que não tem chave). Além disso o
projeto já tem o padrão certo para exatamente esta pergunta:
`tests/test_flag_off_byte_identical.py`.

Os dois testes que a Fase 1 realmente precisa:
1. **flag-off byte-idêntico** — com a instrumentação desligada, a saída é
   idêntica byte a byte;
2. **falha de telemetria não propaga** — com o destino de escrita
   inacessível (`tmp_path` sem permissão, ou monkeypatch levantando
   `OSError`), a chamada segue normal e só loga warning. É o teste que
   prova o §1.2.

---

## 4. O cruzamento propriamente dito — as duas conversas não são independentes

### 4.1 — A conversa 1 descreve algo que o EDP já tem pela metade, com a metade construída desconectada

| o que a conversa 1 propõe | o que já existe | estado real |
|---|---|---|
| unidades de inferência com `conteúdo`/`origem`/`contexto` | `CognitiveDecisions` (`key_assertion` + `concepts` + `domain`) | existe, extrai em background — **e nunca é lido pelo ranking** (1 dos 4 sinais mortos, `README.md §4`) |
| ciclo de hipótese: 0.72 → nova evidência → 0.41 → refutada | estados epistêmicos `contestado`/`quarentenado`/`hipótese` com multiplicadores no ranking (`memory/store.py:555-557`) | **vivo** — é o diferencial declarado do projeto |
| "contradiz → Y" como relação registrada | `contradiction_flagger` | existe, roda, e `scan_results()` tem o retorno **descartado** (outro dos 4 mortos) |
| atenção/confiança como peso | `SOURCE_TYPE_WEIGHTS`, `anchor_boost`, `dom_penalty` | vivos, e **todos Tier A/B** — sem calibração (`AUDITORIA_CONSTANTES_NAO_CALIBRADAS.md §2.3`) |

Ou seja: a conversa 1 não é um projeto novo. É, em boa parte, o pedido
de **ligar o que já está construído e desligado** — o que é um trabalho
bem menor, e bem mais honesto, do que "construir memória inferencial".

### 4.2 — A conversa 2 é pré-requisito técnico da conversa 1, não uma tarefa paralela

Cada campo que a conversa 1 propõe (`atenção`, `confiança`, `evidência`,
`origem`, `contexto`, `relações`, `validade`) só tem efeito sobre o LLM
se **entrar no prompt**. E entrar no prompt é gastar token.

O EDP hoje orçamenta prompt em **caracteres**, com uma razão `4 chars ≈ 1
token` que ninguém mediu, num mecanismo (Âncora de Tarefa) que a
auditoria de ontem mostrou ter um bloco **sem teto nenhum**
(`consolidated`, `AUDITORIA_ANCORA_DE_TAREFA.md §3`).

Construir a conversa 1 antes da Fase 1 é adicionar metadados
estruturados e crescentes a um prompt cujo orçamento é medido com um
número não validado — e o repositório **já tem o precedente do que
acontece nesse cenário**: o `consolidated` cresceu sem teto porque
ninguém tinha como ver o custo em token, só em caractere. Não é risco
hipotético; é o mesmo mecanismo, uma volta antes.

### 4.3 — A conversa 1 enuncia a regra que condena o `4.0` da conversa 2

A conversa 1 escreve, com todas as letras:

> "Foi observado" ≠ "foi inferido" ≠ "é verdade"
>
> OBSERVAÇÃO → MEMÓRIA → INFERÊNCIA → HIPÓTESE → CONFIRMAÇÃO/REFUTAÇÃO →
> CONHECIMENTO CONSOLIDADO

Aplicando essa régua ao próprio EDP: `4 chars ≈ 1 token` está no nível
**INFERÊNCIA** (é uma estimativa razoável de alguém), sendo usado como
**CONHECIMENTO CONSOLIDADO** (é o divisor real de todo orçamento de
janela). Nunca passou por CONFIRMAÇÃO/REFUTAÇÃO. A Fase 1 é
literalmente o passo que falta nessa cadeia — e a conversa 1 forneceu o
vocabulário para dizer isso sem apelar a autoridade nenhuma.

Mesma coisa para os pesos que a conversa 1 quer usar (`atenção 0.91`,
`confiança 0.73`): os pesos análogos que já existem no EDP estão todos
em Tier A/B. Adicionar novos pesos não-calibrados a um ranking que já
tem ~90 constantes não-calibradas piora o problema que a auditoria de
constantes mediu.

---

## 5. O que a conversa 1 colide com, e que precisa de atenção antes de virar código

**Existe uma proibição em vigor neste repositório que atinge a conversa 1
em cheio.** `docs/DIVIDAS.md`, Dívida #46d (classificador rotula turnos
técnicos como `meta_conversation` — caso concreto: uma explicação do
algoritmo de Luhn classificada como meta-conversa):

> "Enquanto o #46d não for corrigido, **NENHUM código novo deve confiar
> em `source_type` para decidir o que é conversa.**"

A conversa 1 propõe inferir intenção e raciocínio a partir de sequências
de eventos. É a mesma família de operação — inferir categoria a partir
de comportamento observado — cujo classificador existente o próprio
projeto já sabe estar errado na origem, e sobre o qual há uma proibição
escrita de construir por cima.

E o histórico empírico reforça a cautela: honeypot (H0 venceu,
seletividade invertida), exp015, wiki camada 3 (2/5, critério ≥3), e Gap
Score em 4 implementações. O padrão comum dos quatro é o mesmo:
**o sistema julgando o próprio estado epistêmico falhou toda vez que foi
medido com critério congelado.** A conversa 1 propõe uma versão mais
ambiciosa dessa mesma afirmação.

Isso não é motivo para recusar — é motivo para **pré-registrar antes de
implementar**, exatamente como o projeto faz com tudo o mais. E note que
o único caso do repositório em que a governança epistêmica **passou** foi
o que teve critério congelado antes (exp012/exp016, matriz N=97, DISQ-v1
com zero falsos positivos). O método existe e funciona; o que não pode é
pular ele por a ideia ser boa.

**O que a conversa 1 acerta e vale preservar:** a separação
*LLM = geradora de hipóteses / Memória = preservadora e avaliadora /
Governança = decide o que consolida*. Ela dá um **motivo de princípio**
para `cognitive_decisions` estar fora do ranking — hoje isso é acidente
(sinal morto), não decisão. Reenquadrar acidente como princípio é
legítimo, desde que dito em voz alta: seria decidir que ele fica fora
**até ser validado**, e não fingir que sempre foi de propósito.

---

## 6. Sequenciamento proposto

1. **Fase 1 corrigida** (o que eu implemento quando você mandar): usa
   `edp.clock.now()` + `is_verified()`; try/except no padrão
   `pareto_store`; emissor dentro do canal Pareto existente, não paralelo;
   caminho sob `BASE_DIR`; `prompt_chars` medido sobre o payload real de
   `_build_payload()`, com essa decisão escrita no docstring; amostra
   descartada quando `input_tokens`/`output_tokens` vier `None`; dois
   testes (flag-off byte-idêntico + falha de telemetria não propaga).
2. **Fase 2** (calibrar) — só depois de N amostras reais, com hipótese e
   limiar congelados antes de olhar o dado.
3. **Ligar o que já existe da conversa 1** — `cognitive_decisions` e
   `contradiction_flagger` já produzem sinal e são descartados. Ligar um
   deles ao ranking, com pré-registro, é mais barato e mais mensurável do
   que construir a arquitetura inferencial completa, e responde à mesma
   pergunta de fundo.
4. **Conversa 1 completa** — depois de 1-3, e com pré-registro próprio,
   por causa do §5.

---

## 7. Limites honestos deste cruzamento

- Não medi nada aqui. Tudo é leitura de código e de documento do próprio
  repositório; nenhum número novo foi produzido.
- Não verifiquei se `bayes_calibrator`/`gauss_calibrator`, que consomem
  os eventos Pareto, têm o **output** consumido por alguém — isso é uma
  pergunta em aberto sobre o CHI/`health_index.py`, e ela importa para o
  item 1 do sequenciamento (adianta pouco emitir para um canal cujo
  consumidor final também está desligado).
- A afirmação de que prompt caching resolveria parte do custo
  (`ANALISE_TOKENIZER_MEMORIA.md §6`) continua **não verificada em
  execução** — verifiquei que o EDP não usa (`cache_control`: zero
  ocorrências), não que ligaria sem atrito.

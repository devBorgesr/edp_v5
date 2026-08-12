# Tokenizer adaptado para a memória — cruzamento vídeo × estado real do EDP

**Data:** 2026-08-12. Compila a conversa sobre o vídeo de tokens, corrige um
ponto técnico da análise externa colada, cruza com o estado real do EDP
(auditorias desta mesma sessão: `AUDITORIA_CONSTANTES_NAO_CALIBRADAS.md`,
`AUDITORIA_ANCORA_DE_TAREFA.md`) e responde à pergunta: dá pra construir um
"tokenizer adaptado para a memória" sem ligá-lo a um transformer?

---

## 0. Uma correção pontual na análise que veio colada

A resposta que você colou (de outra sessão) sobre "RNN tem custo constante
por token, Transformer reprocessa tudo do zero" está certa na direção, mas
exagera o Transformer moderno em produção: Claude/GPT não recalculam as
matrizes de atenção do zero a cada token — usam **KV cache** (guardam as
projeções chave/valor já computadas e só processam o token novo contra
elas). O custo por token de geração ainda cresce com o tamanho do contexto
(porque a nova query tem que atender a um cache cada vez maior), mas não é
"reprocessar tudo do zero" — é mais parecido com "consultar um cache que
cresce". A conclusão prática da colagem (RNN é mais barato por passo, pior
em manter contexto longo; Transformer é o oposto) continua correta; só o
mecanismo do "por quê" estava simplificado a mais.

Isso importa aqui porque muda o quadro de negociação: o "problema" que o
vídeo descreve (reprocessamento caro a cada turno) já tem uma mitigação do
lado do provider — **prompt caching** — que ataca exatamente esse ponto.
Volto nisso na seção 4.

---

## 1. O que o vídeo estabelece que já é verificável dentro do próprio EDP

Dois pontos do vídeo não são teoria solta — batem com dado real do
repositório:

- **Preço assimétrico input/output.** O vídeo cita Opus ~$15/$75. A tabela
  real do EDP (`edp/llm/providers/anthropic.py:39-50`, `PRICING`,
  corrigida em 12/06/2026 com nota explícita de dois erros anteriores)
  tem `claude-opus-4-8: {input: 5.00, output: 25.00}` — a proporção 1:5
  input:output do vídeo bate, os valores absolutos mudaram (preço caiu
  desde a gravação do vídeo, ou o vídeo já citava um modelo antigo). O
  princípio — output custa ~5× o input — é o mesmo.
- **Português custa mais tokens que inglês.** O vídeo mede isso direto
  (22→15 tokens GPT-4o, 42→35 Claude Opus, mesma frase). Isso é
  diretamente relevante porque **o corpus real do EDP é majoritariamente
  português** — é a língua em que Daniel escreve e em que a maior parte da
  memória é gravada. Qualquer estimativa de custo/orçamento de tokens do
  EDP que não diferencie idioma está sistematicamente enviesada para o
  lado que mais importa aqui.

---

## 2. Cruzamento com o EDP real — ele já confunde caracteres com tokens, em três lugares

Fui ver, e o EDP **não tem hoje nenhuma medida real de token** — tem três
aproximações diferentes, desalinhadas entre si:

| onde | o que faz | é token de verdade? |
|---|---|---|
| `edp/compression.py:24`, `token_count()` | `len(re.findall(r"\w+", text))` — conta **palavras**, via regex | **Não.** O nome é enganoso — é contagem de palavras, usada pra medir taxa de compressão em `fuse_chunks`/`extractive_summarize`. Nenhuma relação com o tokenizer real de nenhum provider. |
| `edp/runtime/context_window_manager.py:12-13` | `4 chars ≈ 1 token`, comentário admite: "Para precisão real usaria tiktoken, mas evitamos dependência extra" | **Aproximação confessa**, com margem de erro auto-declarada de ~10% (docstring, linha 20) — e essa margem foi medida quando/onde? Não há citação. |
| `edp/llm_adapter.py` — `CAPS_POR_POSICAO`, `BLOCO_CAP_CHARS`, e todos os caps da Âncora de Tarefa (`challenge` 800/2000, `title` 120, `summary` 200, decisões 120/200 — ver `AUDITORIA_ANCORA_DE_TAREFA.md §3`) | caps em **caracteres**, direto | **Não usa nem o char/4 do item acima** — são números de caractere fixos, sem conversão nenhuma pra token. |

O achado que amarra isto com o vídeo: a proporção real chars/token **não é
uma constante universal** — o próprio vídeo mostrou que varia por idioma e
por raridade de palavra (a palavra inventada "Ubazu" gastou mais tokens
que "carro", mesmo comprimento). O EDP usa `4 chars ≈ 1 token` como se
fosse universal, mas o corpus real dele é português (que já tokeniza pior
que inglês, por vocabulário) misturado com código (que tokeniza de um
jeito completamente diferente, cheio de símbolos e identificadores).
**A aproximação mais provável de estar errada é justamente a que orçamenta
os caps da Âncora de Tarefa** — o mecanismo que a auditoria anterior já
achou sem teto num dos campos.

---

## 3. Respondendo direto: dá pra criar um "tokenizer adaptado pra memória" sem ligar a um transformer?

A pergunta mistura duas coisas que valem a pena separar, porque a resposta
é diferente para cada uma:

### 3.1 — Um tokenizer de verdade (decide os IDs que o modelo processa)

**Não é possível, e não faz sentido tentar.** O EDP não treina nem
hospeda o modelo — ele chama a API da Anthropic (`edp/llm/providers/
anthropic.py`) ou o Ollama local via texto puro. Quem decide os token IDs
reais é o tokenizer DO MODELO, do lado de lá — no caso da Anthropic,
nem é público (o próprio vídeo menciona isso). Não existe um jeito de
"plugar" um tokenizer alternativo no meio dessa chamada; você manda texto,
o provider tokeniza como quiser. Isso é arquitetural, não uma limitação
de esforço de implementação.

### 3.2 — Um estimador/orçamentador de tokens (prevê o custo, decide o que cortar)

**Sim, totalmente possível, e desacoplado de qualquer transformer** — é
código local, estatístico, sem chamada de rede nem dependência de modelo
nenhum. É a peça que o EDP já tenta ter (`context_window_manager.py`) mas
com um número chutado (4.0) em vez de medido. Isto é a peça que vale a
pena construir — não um "tokenizer", um **orçamentador calibrado**.

---

## 4. O achado mais valioso desta análise: o dado de calibração já existe e está sendo descartado

Fui verificar se o EDP já recebe o número REAL de tokens de alguma
chamada — e recebe, em **toda** chamada à Anthropic:

```python
# edp/llm/providers/anthropic.py:248-250 (não-streaming)
usage  = data.get("usage", {})
ptoks  = usage.get("input_tokens", 0)
ctoks  = usage.get("output_tokens", 0)
```

E o mesmo para streaming (`message_start`/`message_delta`, linhas
370-385). Esse valor é real, exato, é literalmente o que a Anthropic vai
cobrar — carregado em `CompletionMetrics.prompt_tokens`/
`completion_tokens` (`edp/llm/providers/base.py:81-82`) e devolvido em
todo `CompletionResponse`.

**Isso nunca é comparado com o tamanho em caracteres do que foi enviado.**
O sistema já tem, de graça, em toda chamada, o par (texto enviado, tokens
reais que esse texto custou) — que é exatamente o dado de calibração que
faltaria pra substituir o `4 chars ≈ 1 token` chutado por uma razão medida
no corpus real do Daniel (português + código, não a mistura genérica que
qualquer heurística universal assume). Ninguém precisa inventar telemetria
nova — precisa só parar de descartar a que já existe.

---

## 5. Proposta de implementação — 3 fases, sem tocar produção na fase 1

### Fase 1 — instrumentar (só leitura, risco zero)

Novo módulo `edp/token_budget.py`, ou uma função em
`runtime/pareto_store.py` (já é o canal de telemetria do projeto — mesmo
padrão de `emit_task_started`/`emit_task_completed` já auditado):

```python
def emit_token_usage(session_id, chars_enviados, ptoks, ctoks, bloco=None):
    """Registra (chars, tokens_reais) por chamada, para calibração futura.
    bloco: rótulo opcional (ex: 'task_anchor', 'retrieval', 'system') se o
    call site souber discriminar — a API só devolve total, não por bloco."""
```

Chamado a cada resposta real da Anthropic, ao lado de onde `ptoks`/`ctoks`
já são extraídos hoje. Zero mudança de comportamento — só grava o par pra
análise depois. Ressalva honesta: a API devolve **total** de tokens do
prompt inteiro, não por bloco — não dá pra saber quantos tokens a Âncora
de Tarefa especificamente custou numa chamada com retrieval + histórico +
âncora juntos, só o total. Pra isolar por bloco seria preciso ou (a) medir
o delta entre chamadas variando só um bloco (caro, controlado), ou (b)
aceitar uma razão global chars→token e aplicá-la por bloco (aproximação,
mas melhor que 4.0 fixo).

### Fase 2 — calibrar (depois de N chamadas reais acumuladas)

Calcular a razão real `chars_enviados / ptoks` sobre os dados coletados,
separada por classe de conteúdo se a Fase 1 permitir discriminar (prosa
PT-BR vs. bloco de código vs. bloco estruturado tipo a âncora). Pré-
registro simples: hipótese "a razão real difere de 4.0 em mais de X%",
threshold definido antes de olhar o dado — mesma disciplina do resto do
projeto.

### Fase 3 — aplicar (só depois da Fase 2 confirmar que vale a pena)

Trocar `4 chars ≈ 1 token` em `context_window_manager.py` pela razão
calibrada, e — o ponto que fecha o ciclo com a auditoria anterior — usar
essa razão pra dar um teto real (em tokens estimados, não em chars) ao
bloco `consolidated` da Âncora de Tarefa, que hoje não tem nenhum.

### Opção mais precisa, com trade-off explícito pro Daniel decidir

Um tokenizer de subpalavra local de verdade (`tiktoken`, vocabulário da
OpenAI, não o da Anthropic — mas uma tokenização real bate muito mais
perto do comportamento real do que uma razão fixa, porque captura a
mesma dinâmica que o vídeo mostrou: palavra rara = mais pedaços) é mais
preciso que a Fase 2 sozinha, mas **reabre uma decisão que o projeto já
tomou duas vezes conscientemente** — evitar `tiktoken`
(`context_window_manager.py:13`) e evitar o SDK `anthropic-python`
(`edp/llm/providers/anthropic.py:16`), as duas vezes citando "evitar
dependência extra". Não decido isso por conta própria — é exatamente o
tipo de trade-off (precisão vs. superfície de dependência) que esse
projeto sempre devolveu pro Daniel decidir.

---

## 6. Uma segunda alavanca, provavelmente mais valiosa que "contar melhor"

O problema de fundo que o vídeo descreve — reprocessar contexto a cada
turno é caro — tem uma mitigação do lado da Anthropic que o EDP **não usa
hoje**: prompt caching (`cache_control` no payload da Messages API,
desconto de até 90% em tokens de prefixo repetido entre chamadas). Busquei
por `cache_control`/`ephemeral` no repo inteiro — zero ocorrências.

Isso é relevante aqui especificamente porque a Âncora de Tarefa (Camada
0.5) e a Âncora Temporal (Camada 0) são **quase-estáticas entre turnos
consecutivos da mesma tarefa** — o desafio, o título das seções entregues,
as decisões consolidadas mudam pouco a cada rodada. É exatamente o
padrão que caching foi desenhado para resolver: prefixo repetido,
reprocessado a preço cheio a cada turno hoje. Diferente da Fase 1-3 acima
(que só mede melhor o problema), isto ataca a causa. É um projeto
separado, maior — não estou propondo fazer os dois juntos, só registrando
que "quanto custa" e "por que custa" são perguntas diferentes, e o vídeo
respondeu bem a segunda sem o EDP ainda ter aplicado a mitigação óbvia
que ela sugere.

---

## 7. Prioridade — isto é feature nova, não correção de bug

Nada aqui é dívida técnica registrada nem bug com sintoma em produção — é
uma capacidade que não existe ainda. Pela disciplina do próprio
`NORTE.md §4` ("cada semana de código-sem-cliente é uma semana a mais de
obra"), vale marcar isso: a Fase 1 (instrumentação) é barata e de risco
zero, mas as Fases 2-3 e a alavanca de caching são trabalho real, sem
relação direta com o checkpoint comercial de 02/09. Decisão de prioridade
é sua.

Não escrevi nenhum código de produção ainda — isto é a análise e a
proposta. Se quiser, o próximo passo natural é eu escrever só a Fase 1
(instrumentação, `emit_token_usage`, sem alterar nenhum comportamento) e
um teste que prove que ela não muda nada — mesmo padrão de todas as
features novas deste projeto.

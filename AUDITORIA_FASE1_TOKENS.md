# Auditoria da Fase 1 — instrumentação de tokens

**Data:** 2026-08-12. **Escopo:** apenas a Fase 1 (coletar o par
`chars → tokens reais`). Fases 2-3 e a arquitetura de memória inferencial
ficam fora, e a §5 explica por que essa fronteira é técnica, não
organizacional. Os quatro repositórios indicados foram abertos e
verificados um a um.

---

## 1. Verificação da análise colada dos 4 repositórios

| alegação | veredito |
|---|---|
| `i-have-adhd` — 10 regras (lead with next action, cap lists at 5, no preamble/recap/closers…) | **confere.** Ressalva de proveniência: a análise colada leu `ayghri/i-have-adhd`; o repo que você indicou é `leondebeer/i-have-adhd`. Abri o seu — as 10 regras estão lá, substância idêntica |
| `airllm` — carregamento camada-a-camada; per-expert streaming em MoE; DeepSeek-V3 671B em ~12GB; evita quantização total | **confere, textual.** README: *"sparse MoE models stream one expert at a time rather than a whole layer"* e *"we only need to make the model loading size smaller"* |
| `GLOSSOPETRAE` — módulo `TokenExploiter` de exploração de BPE | **confere.** README lista `TokenExploiter \| BPE tokenizer exploitation + glitch tokens \| YES` |
| `GLOSSOPETRAE` — *"Opacity UP → usability UP"* / *"Human readability hurts model performance"* | **confere, textual**, com números: `L0-hard: Opus 20%, GPT 53%. L3-hard: Opus 97%, GPT 100%` |
| `GLOSSOPETRAE` — "specs de linguagem compactas ~8000 tokens" | **NÃO EXISTE no repositório.** Número não encontrado |
| `GLOSSOPETRAE` — "formato de injeção de system prompt ~500 tokens" | **NÃO EXISTE no repositório.** Número não encontrado |

Os dois números inexistentes são exatamente os dois únicos itens
acionáveis de orçamento na tabela-síntese da análise colada (a linha
*"Use formato compacto (~500 tokens fixos)"*). O resto das linhas são
princípios; essas duas eram números — e são as duas que não têm origem.

**`CL4R1T4S`** — descartado como fonte para esta fase, por critério
evidencial, não por outro motivo: um prompt vazado tem proveniência
não-verificável por construção. Não há como confirmar que corresponde ao
prompt real e atual de qualquer provider. Usá-lo como "referência
empírica de orçamento" importa um número não-verificado para dentro de um
exercício cujo propósito declarado é substituir números não-verificados
por medidos — é o mesmo erro do `4 chars ≈ 1 token`, com outra roupa. Na
prática o ponto é acadêmico: a seção correspondente da análise colada não
extraiu nenhum padrão concreto, só propôs a tarefa de extrair.

---

## 2. Achado central: os quatro repositórios são Fase 3, e usá-los na Fase 1 destrói a Fase 1

Os quatro atacam **redução** de consumo (poda de saída, roteamento
esparso, formato compacto, referência de orçamento). A Fase 1 é
**medição** de consumo. São operações opostas na ordem de execução.

A consequência é concreta e não é estilística: **mudar o formato de
injeção durante a coleta mistura dois regimes no mesmo dataset.** Se
metade das amostras usa `confiança: 0.73` e metade usa `H=0.73`, a razão
`chars ÷ tokens` resultante não descreve nenhum dos dois. E o modo de
falha é o pior possível — não dá erro, dá um número. Um número com cara
de medido, que passa a ser dividido em todo orçamento de janela.

**Decisão: a Fase 1 congela o formato.** Nenhuma otimização de formato,
poda ou compactação entra enquanto a coleta estiver aberta. As ideias dos
repositórios entram na Fase 3, contra a razão já medida — que é onde elas
podem ser avaliadas por quanto economizam, em vez de assumidas.

---

## 3. O que dos repositórios efetivamente pertence à Fase 1 — e é irreversível se pular

Um item, e a análise colada arquivou ele na gaveta errada.

`TokenExploiter` (BPE, verificado) e a demonstração PT/EN do vídeo
apontam para o mesmo fato: **tokenização é dependente de conteúdo, não
uniforme.** A análise colada filou isso em "ajuste caps dinamicamente",
que é Fase 3. A implicação real para a Fase 1 é outra:

> Se a coleta não estratificar por classe de conteúdo, o resultado é uma
> razão global que é a média de regimes distintos (prosa PT-BR / código /
> bloco estruturado / termo técnico em inglês) e não serve para nenhum
> deles.

E isso é **irreversível**: se a classe não for gravada no momento da
coleta, não há como estratificar depois — a informação não está no par
`(chars, tokens)`. Por isso é requisito de Fase 1, não otimização de
Fase 3.

**Decisão: a Fase 1 grava classe de conteúdo por amostra**, com
classificador determinístico e barato (proporção de não-ASCII, presença
de cerca de código, proporção de pontuação/espaço). Sem chamada de LLM,
sem heurística que precise de calibração própria — senão a instrumentação
vira o mesmo problema que veio medir.

---

## 4. Três defeitos verificados no código que bloqueiam a boa versão da Fase 1

### 4.1 — `request_id` é anunciado no docstring e não existe

`edp/llm/providers/anthropic.py:11` promete *"Logs estruturados com
request_id"*. Grep no módulo inteiro: `request_id` aparece **só nessa
linha de docstring**, em nenhum ponto do código. Não há id de correlação.

### 4.2 — provider e adapter sabem metades diferentes, e nada os junta

- `anthropic.py` tem o token **real** (`usage.input_tokens`), e vê o
  payload já achatado — **zero atribuição por bloco**.
- `llm_adapter.py` monta a lista `blocks` (âncora temporal, âncora de
  tarefa, histórico, janela imediata, retrieval) e sabe o tamanho de cada
  um — e **não vê** o payload final que foi para a rede.

Nenhum dos dois lados sozinho consegue responder "quanto a Âncora de
Tarefa custou em token". Sem id de correlação (4.1), também não dá para
juntar depois. Este é o gargalo real da atribuição por bloco, e é o que
inviabilizaria orçar o `consolidated` sem teto que a auditoria da âncora
achou.

**Decisão: a Fase 1 cria o id de correlação** — gerado no adapter,
propagado no `CompletionRequest`, ecoado nas duas emissões. Junção
post-hoc, nenhum lado precisa conhecer o outro. É a única mudança
estrutural da Fase 1, e ela é aditiva.

### 4.3 — `usage` bruto já está disponível e seria descartado

`CompletionResponse.raw = data` (`anthropic.py:277`) já carrega a
resposta inteira, incluindo o dict `usage` completo. A especificação
colada extrai dois inteiros e joga o resto fora.

Isso tem uma consequência específica e séria: **se prompt caching for
ligado um dia**, o `usage` passa a ter `cache_read_input_tokens` /
`cache_creation_input_tokens`, e uma chamada com cache tem relação
`chars → tokens cobrados` completamente diferente de uma sem. Se a Fase 1
gravou só `input_tokens`, toda amostra posterior a esse dia fica
contaminada e **indistinguível** das limpas. O dataset inteiro vira
suspeito retroativamente.

**Decisão: gravar o dict `usage` verbatim**, não campos extraídos. Custo
zero, e imuniza o histórico contra mudança de esquema do provider.

---

## 5. Especificação congelada da Fase 1

Substitui a especificação colada. Cada item é decisão, com o motivo ao
lado.

| # | decisão | motivo |
|---|---|---|
| 1 | emissor dentro de `edp/runtime/pareto_store.py`, padrão `emit_*` | canal único já consumido por `bayes_calibrator`/`gauss_calibrator`; canal paralelo = segunda fonte de verdade |
| 2 | `try/except` envolvendo tudo, `logger.warning` na falha | contrato de todo emissor do projeto (`emit_task_started:459-472`); telemetria nunca derruba caminho vivo |
| 3 | `edp.clock.now()` + gravar `clock_verified` | `datetime.utcnow()` é o relógio do SO — a coisa que a Peça 0.1 existe para substituir |
| 4 | destino sob `BASE_DIR` (env-sobrescrevível) | convenção de `METRICS_LOG`/`LIVE_FEED_LOG`/`MEMORY_DIR`; caminho relativo muda conforme o CWD do lançamento |
| 5 | `usage` gravado verbatim | §4.3 |
| 6 | amostra **descartada** quando `input_tokens`/`output_tokens` vier `None` | no streaming os dois chegam em eventos distintos e podem faltar (`anthropic.py:370-385`); gravar `None` como `0` injeta par falso |
| 7 | `prompt_chars` medido sobre o payload real de `_build_payload()` (`:131`), com essa definição escrita no docstring | é o que a API cobra; contar só `request.messages` produz razão sistematicamente baixa com aparência de medida |
| 8 | classe de conteúdo gravada por amostra, classificador determinístico | §3 — irreversível se omitido |
| 9 | id de correlação adapter↔provider | §4.1/4.2 |
| 10 | `model` gravado por amostra | modelos diferentes podem ter tokenizadores diferentes; sem o campo a pergunta fica sem resposta possível |
| 11 | formato de injeção congelado enquanto a coleta estiver aberta | §2 |
| 12 | dois testes: flag-off byte-idêntico + falha de telemetria não propaga | suíte é 100% sintética (`README.md §4`); o teste colado bateria na API real, custaria dinheiro e quebraria no CI sem chave |

**Fora da Fase 1, explicitamente:** qualquer mudança de formato, qualquer
poda, qualquer cap novo, prompt caching, e a arquitetura de memória
inferencial.

---

## 6. Ideias de melhora que eu registro agora, para não se perderem

Em cumprimento ao pedido de não omitir melhorias de eficiência/sofisticação
da inferência da memória — com a fase de destino marcada, porque nenhuma
delas pode entrar na Fase 1 sem quebrar a §2:

1. **Roteamento esparso de metadados (de `airllm`, re-arquivado).** O
   mecanismo real do airllm é *carregar só o expert para o qual o token
   roteou*. Traduzido para a memória inferencial: não injetar todos os
   metadados de todas as memórias recuperadas — injetar só os top-K que a
   query ativa. É a ideia mais forte dos quatro repositórios para
   "sofisticação da inferência", porque muda a pergunta de recuperação de
   *"o que é parecido?"* para *"o que participa deste raciocínio?"* — que
   é exatamente o que a conversa da memória inferencial pedia.
   **Destino: Fase 3+.** **Gancho na Fase 1:** o item 8 (classe por
   amostra) já registra composição; sem ele não haveria como modelar o
   custo de uma decisão de roteamento depois.

2. **Formato opaco em vez de legível (de `GLOSSOPETRAE`, verificado).**
   O achado `Opacity UP → usability UP` / `human readability hurts model
   performance` é contraintuitivo e está medido no repo deles. Se se
   sustentar no corpus do EDP, `H=0.73|E:A,B|C:X` custaria menos que
   `confiança: 0.73` **e** poderia performar melhor. **Destino: Fase 3,
   como experimento pré-registrado** — é uma alegação externa sobre
   linguagens sintéticas, não sobre blocos de memória em PT-BR; herdar o
   resultado sem re-medir seria o mesmo erro de importar número de fora
   que a §1 acabou de flagrar.

3. **Poda estrutural de saída (de `i-have-adhd`).** As 10 regras atacam
   token de **output**, que é onde o preço é ~5× o de input
   (`anthropic.py:39-50`, `PRICING`). É a maior alavanca de custo dos
   quatro repositórios, e a mais barata de testar. **Destino: Fase 3**,
   ou antes disso como experimento independente — não depende de nada da
   Fase 1, porque não mexe no prompt que está sendo medido.

4. **Prompt caching.** Segue como a maior alavanca isolada
   (`ANALISE_TOKENIZER_MEMORIA.md §6`), e agora tem uma restrição de
   ordem que não estava registrada antes: **não pode ser ligado durante a
   coleta da Fase 1** sem o item 5 desta especificação, pelo motivo da
   §4.3. Com o item 5, pode — as amostras cacheadas ficam separáveis.

---

## 6-bis. NOTA DE EXECUÇÃO (12/08/2026) — implementado, com dois desvios da §5

Suíte: **324 passed, 1 deselected** (eram 299 + 25 novos). Arquivos tocados:
`config.py` (flag), `runtime/pareto_store.py` (tipo de evento + classificador +
emissor), `llm/providers/anthropic.py` (medição + 2 pontos de emissão),
`runtime/__init__.py` (export), `tests/test_token_telemetry.py` (novo).

**Desvio 1 — item 9 estava errado: o correlation_id já existia.** A §5 mandava
*criar* o id de correlação adapter↔provider. Ao abrir o código para
implementar, achei o mecanismo pronto em `pareto_store.py:102-125`
(thread-local: `new_correlation_id`/`set_current_correlation_id`/
`get_current_correlation_id`), já chamado em `llm_adapter.py:1527` (chat) e
`:1607` (stream_chat), e já preenchido automaticamente por `emit()` quando
ausente. O que **não** existe é o `request_id` que o docstring do provider
anuncia (`anthropic.py:11`) — a §4.1 continua correta, mas a conclusão que tirei
dela (criar mecanismo novo) estava errada: o provider roda na mesma thread onde
o id é setado, então o evento já sai correlacionado sem nenhuma linha nova.
Construir um segundo mecanismo teria criado duas verdades sobre "que turno é
este". Item 9 **cancelado por já estar satisfeito**.

**Desvio 2 — item 7 era ambíguo e virou duas medidas.** A §5 dizia
"`prompt_chars` medido sobre o payload real". Ao implementar ficou claro que
"payload real" ainda tem duas leituras defensáveis — o texto que a API tokeniza
(`text_chars`) e os bytes no fio com andaime JSON (`payload_bytes`) — e que
escolher uma às cegas produziria exatamente o defeito que a §2 do documento
anterior denunciou. Gravo **as duas**, mais `n_messages` (o andaime escala com
ele). Custo: dois inteiros por amostra. A Fase 2 decide qual numerador é mais
estável com dado, não por decreto.

**Um bug meu, achado por teste antes de rodar em produção:** a primeira versão
de `_medir_prompt` juntava partes vazias, então um bloco multimodal sem texto
(imagem) somava um `"\n"` fantasma. Um char por bloco não-textual — pequeno e
**sistemático**, o tipo de viés que se esconde dentro de uma razão com cara de
medida. Corrigido, com o motivo no comentário e teste que trava
(`test_medir_prompt_aceita_conteudo_multimodal`).

**O que a flag OFF garante, provado por teste:** com `EDP_TOKEN_TELEMETRY=0`
(default) o caminho é um `if` e mais nada — `test_flag_off_nao_percorre_o_prompt`
sabota `_medir_prompt` para explodir e prova que ela nunca é alcançada.

**Coleta ainda NÃO começou.** Ligar `EDP_TOKEN_TELEMETRY=1` é a decisão
explícita de abrir a janela — e a janela congela o formato de injeção (§2).

---

## 7. Limites desta auditoria

- Os quatro repositórios foram lidos pelo README/página principal. Não li
  o código-fonte de nenhum deles; as alegações verificadas em §1 são
  alegações **dos autores**, verificadas como *presentes*, não como
  *verdadeiras*. Especificamente, os números do `GLOSSOPETRAE`
  (`Opus 20%→97%`) não foram reproduzidos por mim.
- Não medi nada do EDP nesta rodada. As citações `arquivo:linha` são de
  leitura direta e do grep de `request_id`, que rodei agora.
- A pergunta em aberto do documento anterior continua aberta: se o
  **output** de `bayes_calibrator`/`gauss_calibrator` é consumido por
  alguém. Emitir para um canal cujo consumidor final também está
  desligado resolveria metade do problema.

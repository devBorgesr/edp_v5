# Procedência do 15,7% — auditável sem publicar conversa

**30/08/2026.** Este arquivo existe para que o número da página comercial tenha
"arquivo, data e linha" **sem** que texto de conversa real entre num repositório
público.

## O número

```
duplicação de conteúdo intra-query, média .... 15,7%
pior query ...................................  40,0%   (1 de 14, não frequência geral)
duplicação intra-query por ID, média ......... 15,7%
sobreposição cross-query (contínua) ..........   4,6%   (referência aleatória: 7,0%)
sobreposição cross-query (binária) ...........   0,0%
```

## Como reproduzir

| | |
|---|---|
| código | `audit/retrieval_audit.py` (versionado) |
| entrada | `export_fase0.jsonl` — **NÃO versionado** |
| sha256 da entrada | `6a22cc680e1aef067b3dea3da1c80e10025e38aa2bc7df71cc7569b8a8d5019a` |
| tamanho | 14 linhas, 67.062 bytes |
| data | 22/07/2026 |
| N | 14 queries válidas |
| k | **5** |
| relatório gerado | `RELATORIO_DOGFOOD.md` — **NÃO versionado** |

**Por que a entrada não é versionada:** contém queries reais do pesquisador, e
este repositório é público. O sha256 permite provar que uma cópia é a mesma
usada, sem publicar o conteúdo. Quem auditar recebe o arquivo por outro canal e
confere o hash.

**Por que o relatório não é versionado:** ele cita 5 trechos de conversa real
como exemplos de duplicação encontrada.

## Os cinco números, e o que cada um mede

Colisão de valor é real neste projeto — há **três** "15,7%" medindo coisas
diferentes. Nenhum destes deve circular sem referente.

| valor | mede | fonte |
|---|---|---|
| **15,7%** | duplicação intra-query, **k=5** | dogfood, N=14 |
| **12,4%** | duplicação intra-query no `retrieval_kept` (tamanho variável) | exp017 T6 |
| **15,4%** | sobreposição **entre queries** — não é intra-query | exp017 T6 |
| **25,5%** | repetição de **ID** no ranking top-50 | retriever real, 50 queries |
| **24,8%** | conteúdo duplicado entre slots **entregues**, IC [20,5; 29,7] | `ranking_decision`, N=50 turnos |

## Frase permitida, e a proibida

**Permitida:**
> "15,7% de duplicação de conteúdo intra-query — média de 14 queries válidas,
> k=5, sobre `export_fase0.jsonl`."

**Permitida, para o pior caso:**
> "40% na pior query — **1 de 14**."

**Proibida:**
> "25% do seu contexto é inútil."

Todos os cinco medem **repetição de slot**. Que a repetição desperdice contexto é
inferência plausível e **não medida** — nenhum experimento deste projeto comparou
qualidade de resposta com e sem duplicação.

Ver `lab_edp/docs/sujeito_edp/NUMEROS_DE_DUPLICACAO.md` para a reconciliação
completa.

---

## Família de chunking — permitido e proibido (07/09/2026)

Medido sobre o **mesmo** `export_fase0.jsonl`, sha256 conferido
(`6a22cc68…`), N=14, k=5, 70 trechos:

```
comprimento mediano ........ 722 chars    p10/p90 119/1462   min/max 102/3484
sem fim de frase ...........   5,7%
inicio minusculo ...........   0,0%
sobreposicao adjacente .....   0,0%   (mediana sobre 56 pares)
boilerplate ................   0,0%   (0 linhas repetidas)
```

**A leitura correta é que a família não acusa nada aqui — e o motivo importa
mais que o número.** O EDP não tem chunker: ele guarda **turnos de conversa**,
não documentos partidos por um divisor. Sem divisor não há corte no meio de
frase, não há janela deslizante e não há cabeçalho repetido. Os quatro zeros
não são nota boa; são **não se aplica**.

Isso vale como verificação de especificidade do instrumento: rodado contra um
sistema sem a patologia, ele **não dispara**. Uma métrica que acusa sempre é
tão inútil quanto uma que nunca acusa.

**Permitida:**
> "5,7% dos trechos entregues não terminam em pontuação de fim de frase —
> 70 trechos, N=14 queries, k=5, sobre `export_fase0.jsonl`."

**Permitida, e a mais informativa deste conjunto:**
> "O comprimento dos trechos varia de 102 a 3.484 caracteres, mediana 722."

**Proibida:**
> "O EDP tem bom chunking."

Ele não tem chunking. Medir ausência de patologia num sistema que não pode
tê-la não é evidência de qualidade.

**Cuidado com `n_textos_distintos`.** São 37 distintos em 70 trechos, e esse
quociente **não é** uma taxa de duplicação: ele mistura repetição dentro da
query com repetição entre queries, que as famílias 1 e 2 medem separado e com
definições próprias. Usar 37/70 como "47% de duplicação" cria o sexto número
sem referente.

---

## Duas frases proibidas que reapareceram, e por quê

Registradas aqui porque as duas voltaram em material comercial depois de já
terem sido barradas uma vez.

**1. Conversão de repetição de slot em custo de token.**

> ~~"26% do orçamento de token do prompt vai para repetição."~~

É a frase proibida do parágrafo anterior com outra roupa, e **pior**: empilha
duas inferências não medidas. A primeira é que repetição desperdiça contexto —
já barrada acima. A segunda é que todos os slots pesam o mesmo em tokens, e a
própria família de chunking acaba de mostrar que **não pesam**: os trechos vão
de 102 a 3.484 caracteres. Converter fração de slot em fração de token exige
comprimento uniforme, e ele não existe.

Esta frase já custou dois documentos: `comercial/OFERTA.md` e
`comercial/PUBLICO_ALVO.md` foram retidos do repositório público em `5d30b89`
por dizerem "token pago duas vezes ocupando o lugar de".

**Permitida no lugar:**
> "13 dos 50 slots do top-k foram ocupados por IDs já presentes no ranking —
> 26% dos slots, IC [0,24; 0,26], sobre 50 queries."

O leitor técnico completa a implicação de custo sozinho. Deixar que ele
complete é mais forte que afirmar.

**2. Atribuição de causa à duplicação por ID.**

> ~~"Duplicação por ID sem duplicação por texto aponta dedup ausente após o
> merge híbrido ou o RRF — é bug de junção, não de indexação."~~

A auditoria `400f691a3fa6` **não registra** o estado de `EDP_RETRIEVE_DEDUP`,
que está em `edp.config.FORMAT_STATE_FLAGS` com o comentário "muda o conjunto
recuperado" e tem default desligado. A duplicação medida é indistinguível
entre defeito do retriever e flag desligada por padrão. Ver
`auditorias/diagnostico_edp/400f691a3fa6/ERRATA.md` no lab.

A partir de agora `retriever.configuracao_sujeito` é gravado em toda execução,
então esta frase **pode voltar a ser permitida** — mas só sobre uma auditoria
que tenha a captura, e nunca sobre a `400f691a3fa6`.

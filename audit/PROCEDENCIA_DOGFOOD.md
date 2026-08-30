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

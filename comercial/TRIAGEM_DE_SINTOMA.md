# Triagem de sintoma — o que eu meço, o que não meço, e a frase de cada um

**07/09/2026.** Serve para três coisas: filtrar sinais lidos em fórum ou vaga,
escrever a abordagem citando o sintoma que a pessoa escreveu, e recusar o que
o instrumento não alcança **antes** da primeira reunião, não durante.

Cada linha da zona 1 traz a **frase permitida**, no formato da seção
"Frase permitida, e a proibida" de [`audit/PROCEDENCIA_DOGFOOD.md`](../audit/PROCEDENCIA_DOGFOOD.md):
valor + N + k + fonte. Cada linha da zona 3 traz a **recusa formulada** — a
frase que se diz, não um "não".

Coluna `IC`: se a medida sai com intervalo de confiança. As duas
implementações não são a mesma (`audit/retrieval_audit.py` não tem IC; o
protocolo `DIAGNOSTICO` do lab tem, por bootstrap, e não tem chunking).

---

## Zona 1 — mede hoje, sem rótulo e sem corpus

Precisa só do contrato congelado: `{"query": …, "results": [{"id", "text", "score"}]}`.
`text` é obrigatório; sem `id` as métricas por ID são omitidas com nota, sem
`score` a análise de escala é omitida com nota.

| sintoma, como a pessoa escreve | o que é medido | IC | frase permitida |
|---|---|---|---|
| "traz os mesmos documentos repetidas vezes" | `duplicacao_intra_query_por_id`, `duplicacao_por_texto` | sim | "X% dos slots do top-k foram ocupados por IDs já presentes no ranking — N queries, k=K, IC [a; b]" |
| "queries diferentes trazem quase o mesmo conjunto" | `jaccard_cross_query` | sim | "Jaccard mediano entre pares de queries distintas = X, IC [a; b], sobre P pares" |
| "o ranking não separa bom de ruim" / "os scores são todos parecidos" | `razao_score_topo_cauda`, `tie_fraction`, `tied_adjacent` | sim | "razão entre o score mediano das 5 primeiras e das 5 últimas posições = X, IC [a; b]; Y% dos pares adjacentes têm score exatamente igual" |
| "peço 50 e recebo repetição" | `cardinalidade_do_ranking` | sim | "N documentos distintos entregues numa janela top-k de K, mediana entre queries, IC [a; b]" |
| "os trechos vêm cortados no meio da frase" | `frac_sem_fim_de_frase`, `frac_inicio_minusculo` | não | "X% dos trechos entregues não terminam em pontuação de fim de frase — N trechos, N queries, k=K" |
| "os trechos se sobrepõem" | `sobreposicao_adjacente` | não | "sobreposição mediana de X% entre posições consecutivas, sobre P pares" |
| "vem cabeçalho repetido em todo trecho" | `boilerplate` | não | "X% dos caracteres estão em linhas que reaparecem em ≥3 trechos distintos" |
| "os trechos têm tamanhos muito diferentes" | `comprimento` | não | "comprimento mediano X caracteres, p10/p90 A/B, mín/máx C/D" |

**Proibido nesta zona:** converter qualquer uma dessas frações em custo de
token, desperdício de contexto ou dinheiro. Todas medem **repetição de slot** e
**forma do texto**. Que isso degrade a resposta é inferência plausível e não
medida — nenhum experimento aqui comparou resposta com e sem duplicação. E os
trechos vão de 102 a 3.484 caracteres na medição de referência, então fração de
slot não vira fração de token nem por aritmética.

**Sobre sobreposição alta:** não é defeito. Janela deslizante é estratégia
deliberada. O número diz qual estratégia está em uso, não se ela está certa.

---

## Zona 2 — mede com rotulação do conjunto devolvido

Não exige corpus, não exige acesso ao sistema. Exige **julgamento humano sobre
o que veio**: dada a query e o trecho, ele é relevante?

| sintoma | o que seria medido | estado |
|---|---|---|
| "traz documentos irrelevantes" | `Precision@K` | não construído; alcançável black-box |
| "o documento certo vem em posição ruim" | `MRR`, `nDCG sobre o conjunto devolvido` | idem |

**O que já existe para isso:** `rotulador.py` (CLI de rotulação humana, dedup
por hash de conteúdo, anti-circularidade explícita), `avaliador_matriz.py`
(regras congeladas em pré-registro) e `bancada/cobertura.py` (IC por bootstrap).
O maquinário foi construído **e rodado até o fim** — `gt_rotulacao.csv` tem 193
linhas 100% rotuladas por humano.

**O que não se aproveita:** os rótulos. A taxonomia atual é de toxicidade de
resposta (`VENENO_*` / `LEGITIMO_*`), não de relevância documento-query.
Reapontar o instrumento é trabalho real; reusar os rótulos seria erro de
construto.

**`nDCG sobre o conjunto devolvido` precisa desse nome.** Ele normaliza pelo
ideal do que **veio**, não pelo ideal do corpus. É uma quantidade legítima e
mais fraca que o nDCG clássico, e chamá-la de nDCG sem qualificar seria a
sexta forma de dizer "15,7%".

**Frase honesta hoje:** "não meço relevância no diagnóstico. Meço com
rotulação, e o custo é K×N julgamentos documento-query — dá para orçar."

---

## Zona 3 — não mede, e a recusa

| sintoma | por quê | recusa formulada |
|---|---|---|
| "não acha um documento que existe" / "falta documento" | **Recall.** Impossível em caixa-preta, por informação e não por ferramenta: observa-se o que foi devolvido; recall pergunta sobre o que deveria ter vindo e não veio. Não se observa a ausência de um documento que nunca apareceu. | "Recall exige saber o que deveria ter vindo. Sem o seu corpus ou um gold set, ninguém mede isso — nem eu, nem ferramenta nenhuma. O que eu meço é o que o retriever entregou." |
| "alucina mesmo com o contexto certo" | É geração, não recuperação. Fora do escopo do instrumento. | "Isso é da geração. Eu meço o que o retriever entregou, não o que o modelo fez com aquilo. Se o contexto certo chegou, o meu diagnóstico vai dizer que chegou — e o problema está depois de mim." |
| "o RAGAS diz uma coisa e meus usuários dizem outra" | `faithfulness` e `answer relevance` comparam a resposta com uma referência. Não há referência. | "Essas métricas comparam resposta com referência. Eu não tenho referência, e produzi-la é outro trabalho, com outro preço." |
| "queria saber se meu RAG é bom" | Não é pergunta mensurável sem critério declarado. | "'Bom' precisa de um critério antes do dado. Eu posso medir cinco comportamentos do seu retrieval com intervalo de confiança; se algum deles for o seu critério, respondo. Se o critério for qualidade de resposta, é a Zona 2." |

---

## Zona 4 — parcial, e vale dizer que é parcial

| sintoma | o que dá | o que não dá |
|---|---|---|
| "piorou depois que troquei o reranker / o embedding / o chunker" | as grandezas da Zona 1 **antes e depois**, com IC nas quatro que têm | se ficou melhor **para o usuário** |

**Frase:** "consigo dizer se duplicação, dispersão de score, cardinalidade e
forma dos trechos mudaram entre as duas versões, com intervalo de confiança.
Não consigo dizer se ficou melhor — isso precisa de critério de qualidade, e
esse critério é a Zona 2."

Esta linha é a que mais aparece em fórum e a que mais gente confunde com
"medir qualidade". Dizer a diferença em voz alta é a demonstração.

---

## Como usar isto lendo um sinal

```
sintoma escrito pela pessoa
        |
        +-- casa com Zona 1  -> abordagem cita o sintoma DELA e a frase permitida
        |
        +-- casa com Zona 2  -> abordagem diz o que custa, e oferece a Zona 1 antes
        |
        +-- casa com Zona 3  -> NAO abordar com este instrumento.
        |                       Recusar aqui e barato; recusar na reuniao nao e.
        |
        +-- casa com Zona 4  -> abordagem oferece o antes/depois, com o limite dito
```

**A metade que mais aparece nas buscas óbvias cai na Zona 3.** "RAG returning
irrelevant documents", "RAG hallucination despite correct documents", "RAGAS
bad scores", "vector search retrieving wrong documents" — todas pedem
relevância. Responder a elas com este instrumento é o caminho mais curto para
ser desmontado na primeira reunião.

Os sinais que valem procurar são os literais da Zona 1: *"same chunks over and
over"*, *"duplicate results"*, *"different queries return the same docs"*,
*"scores are all similar"*, *"chunks cut mid-sentence"*.

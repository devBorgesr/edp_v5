# `avg_top` — o que a grandeza mede, e o que não se pode afirmar dela

**03/09/2026.** Escrito para fechar a dívida [#55](DIVIDAS.md), que bloqueava
o gráfico de tendência de retrieval no dashboard. O bloqueio nunca foi de
engenharia — o dado já vem no payload de `/dashboard/state`. Era de
definição: sem saber o que a grandeza mede, plotá-la transforma um indicador
operacional (NÍVEL 1) em alegação de qualidade de recuperação (NÍVEL 3).

Tudo abaixo foi lido no código que produz o número, não deduzido do nome.

---

## 1. A conta

`edp/runtime/retrieval_monitor.py`, `DailyBucket`:

```python
avg_top = sum_top / turn_count
```

e em `record_turn`:

```python
bucket.turn_count += 1          # TODO turno, inclusive os vazios
if not top_scores:
    bucket.empty_count += 1
    return                      # sai SEM somar a sum_top
top_score = float(top_scores[0])
bucket.sum_top += top_score
```

**O denominador conta todos os turnos; o numerador só os não-vazios.** Logo:

```
avg_top  =  media(top-1 | turno nao-vazio)  ×  (1 − empty_rate)
```

Isto não é defeito, mas é a primeira coisa que se precisa saber: **`avg_top`
mistura duas grandezas** — quão alto pontua o melhor resultado, e com que
frequência há algum resultado. Uma queda pode vir de qualquer uma das duas, e
o número sozinho não distingue.

`empty_rate` já é publicado no mesmo bucket. Quem quiser a média condicional
divide: `avg_top / (1 − empty_rate)`.

## 2. Qual score é `top_scores[0]`

`edp/memory/store.py:1596` e `:1811`:

```python
top_scores = [r["ranking_score"] for r in final_top]
```

`final_top` sai de `_dedup_ranked(final, top_k, mode)`. Os três modos
preservam a ordem do ranking — `mode="off"` é `candidates[:k]`,
byte-idêntico; `dedup` e `random_pareado` fazem refill "na ordem". Então
`[0]` **é** o primeiro colocado.

**Com uma exceção declarada:** `EDP_RETRIEVE_SHUFFLE` (exp017 Fase 0, default
`OFF`) embaralha antes. Com essa flag ligada, `top_scores[0]` deixa de ser o
máximo e `avg_top` deixa de significar "score do primeiro colocado" — que é
exatamente o ponto de um controle negativo. Séries que atravessem uma
mudança dessa flag não são comparáveis.

## 3. `ranking_score` NÃO é uma escala só — são três

| origem | o que é | faixa típica |
|---|---|---|
| `store.py:1560` | `cosine_similarity` da camada *working* | 0 – 1 |
| `store.py:792` | `rank_score` composto, com `ranking_breakdown` | escala própria |
| `store.py:1750` | `res.scores[pos]`, `breakdown.method = "rrf"` | **~1/(60+rank), máx ≈ 0,016** |

O terceiro é Reciprocal Rank Fusion, `score(d) = Σ 1/(k + rank(d))` com
`k = 60` (`edp/retrieval_hybrid.py:116,244`). O próprio `edp/config.py:124`
já registra a faixa: *"RRF produz scores ~1/(60+rank) (máx ≈0.016)"*.

### A consequência prática, e ela é grande

O dashboard exibe **`Score médio 0.010`**, e o payload real trazia
`avg_top: 0.0123`.

- Lido como **similaridade**, `0,0123` é quase ortogonal — pareceria um
  sistema recuperando lixo.
- Lido como **RRF**, `0,0123` ≈ `1/81`, dentro da faixa e perto do teto de
  `0,016` — comportamento normal.

Os dois números são o mesmo número. **Sem saber qual caminho produziu o
score, o mostrador não tem interpretação** — e um gráfico de tendência dele
teria ainda menos, porque uma mudança de caminho apareceria como "queda de
qualidade" sem que qualidade nenhuma tivesse mudado.

O valor observado é compatível com o caminho híbrido (RRF) e incompatível com
cosseno de uma recuperação funcionando. Mas isto é inferência a partir da
faixa, não medição: **o payload não carrega `ranking_breakdown.method`**, e
sem ele não dá para afirmar qual caminho rodou naquele dia.

## 4. O que se pode e o que não se pode afirmar

**Pode (NÍVEL 1, observação):**

- "o `avg_top` do dia D foi X, sobre N turnos" — com a ressalva de que X
  embute `empty_rate`
- "X caiu Y% em relação à janela anterior" — desde que as duas janelas usem
  o mesmo caminho de retrieval e a mesma configuração de flags
- "houve Z% de turnos sem nenhum resultado"

**Não pode (NÍVEL 3, qualidade):**

- "o retrieval piorou" — a queda pode ser só `empty_rate` subindo, ou
  mudança de caminho, ou mudança de `top_k`
- "o score médio é baixo" — sem dizer a escala, "baixo" não tem referente;
  `0,0123` é ruim em cosseno e bom em RRF
- qualquer comparação com outro sistema, ou com o mesmo sistema antes de uma
  mudança de caminho de retrieval

`RetrievalQualityMonitor` já dispara `WARNING` com queda >20%
(`retrieval_monitor.py:136`). Esse alerta é **operacional** — "olhe isto" — e
não um veredito sobre qualidade. A distinção precisa sobreviver à ida para a
tela.

## 5. O que falta para o gráfico existir honestamente

1. **Publicar a escala junto do número.** `DailyBucket` precisa registrar qual
   `ranking_breakdown.method` dominou o dia, e `to_dict()` devolvê-lo. Sem
   isso o eixo Y não tem unidade.
2. **Separar as duas grandezas.** Plotar `avg_top` e `empty_rate` juntos, ou
   plotar a média condicional. Um número que mistura os dois não é série
   temporal de nada.
3. **Marcar descontinuidade.** Mudança de caminho ou de flag de retrieval tem
   de aparecer como corte no gráfico, não como tendência.
4. **Rotular na tela o que o número é.** "Score médio" sem escala é o rótulo
   que criou este problema.

Nenhum dos quatro é caro. Os quatro são anteriores a desenhar o gráfico.

---

## 6. Correção de um rótulo já em produção

O dashboard chama `r-score` de **"Score médio"**. Pelo que está acima, o rótulo
honesto seria "Score do 1º colocado (média diária)" com a escala ao lado. A
mudança não foi feita aqui: o dashboard está **congelado**
(`VEREDITO_dashboard_v3.5.md` §6), e mexer nele agora contrariaria o
congelamento por uma melhoria que não é urgente. Fica registrado para a
próxima abertura.

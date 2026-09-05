# Pré-registro — dívida #56: o probe de rede no caminho do turno

## Cachear o resultado de `validate()` tira o round-trip do caminho quente sem perder a verificação de credencial?

> **Régua da Bancada:** hipótese, condições, métrica e critério fixados **antes
> de tocar no código**. Congela ao primeiro disparo real. Se um defeito
> invalidar o desenho, é um pré-registro novo — não uma edição deste.

**Data de pré-registro: 2026-09-05** (antes da implementação).

---

## 1. Contexto provado

Medido no log de execução em produção do pesquisador (03/09/2026, host
Windows, store `C:\edp_data_todo\edp_data`, `claude-haiku-4-5`).

**A cadeia, por leitura de código:**

```
websocket.py:765   if runtime_ok and runtime.is_connected():   <- caminho do turno
llm_adapter.py:1898  is_connected -> self._client.is_available()
llm_adapter.py:636   is_available -> _anthropic_provider.validate()   (so ANTHROPIC)
anthropic.py:515     validate -> chamada real, prompt "1", max_tokens=1
```

Para Ollama/OpenAI o mesmo `is_available()` é um `GET` local com timeout de
3 s. **Só o caminho Anthropic paga rede.**

**A medida, pelo relógio do log:**

```
04:54:15,985  [WS] msg recebida
04:54:16,192  [WS] memory | hits=7          <- fim da recuperacao
04:54:37,976  [anthropic] complete lat=21782ms tok_in=9 tok_out=1   <- O PROBE
04:54:37,999  [WS] LLM stream iniciando     <- 23ms depois
04:55:05,545  [WS] done llm_used=True

turno inteiro ......... 49,56 s
probe ................. 21,782 s  = 43,9% do turno
5 probes em ~5 min .... 86,6 s somados (22.065, 9.836, 21.818, 21.782, 11.091)
```

Custo em dinheiro: `cost=$0.0000`, e `validate()` já passa `telemetria=False`
para não sujar o dataset. **O custo é latência percebida.**

---

## 2. Hipótese

**H1 —** Cachear o resultado positivo de `validate()` com TTL remove o
round-trip do caminho do turno. Um turno com LLM conectado passa a não emitir
nenhuma chamada `tok_in=9 tok_out=1` entre o fim da recuperação de memória e o
`LLM stream iniciando`, e a diferença entre esses dois marcos cai para a ordem
de milissegundos.

**H0 —** O probe não é a única causa do intervalo. Depois do cache, o gap
entre `memory hits` e `stream iniciando` continua acima de 1 s, indicando que
havia outra coisa naquele trecho que a leitura do log atribuiu ao probe.

**H0 vencer é achado válido:** significa que o log foi lido rápido demais e
que existe custo não identificado no caminho — o que é mais importante saber
do que o ganho do cache.

---

## 3. Desenho

| condição | descrição |
|---|---|
| `antes` | comportamento atual, probe por turno |
| `depois` | `validate()` cacheado, TTL congelado na §6 |
| `credencial_invalida` | **controle negativo** — chave errada tem de continuar sendo recusada |
| `flag_off` | `EDP_LLM_VALIDATE_TTL=0` reproduz o comportamento antigo |

**Controle negativo de validade:** `credencial_invalida`. Se uma chave inválida
passar a ser aceita porque o cache guardou um "sim" antigo, **o ganho de
latência não é afirmado** — a mudança teria trocado verificação por
velocidade, que não é o que está sendo pedido.

---

## 4. Constantes congeladas

| constante | valor | por quê |
|---|---|---|
| `TTL` padrão | `300 s` | credencial não muda no meio de uma sessão; 5 min mantém a verificação de pé e tira ~11 probes de um turno de 5 min |
| cacheia positivo | **sim** | é o caso comum |
| cacheia negativo | **NÃO** | cachear "falhou" manteria o sistema fora do ar depois de o operador corrigir a chave |
| `_connect()` | **sempre fresco** | é o momento em que a resposta importa; usar cache ali aceitaria uma chave que já não vale |
| env var | `EDP_LLM_VALIDATE_TTL` | `0` desliga o cache e reproduz o comportamento antigo |
| escopo | `LLMClient` | por cliente; trocar de provider ou de chave cria cliente novo, então o cache morre junto |

**Mudou qualquer uma destas ⇒ é outro pré-registro.**

---

## 5. Métrica

Uma só, lida do log de produção, e é a mesma nos dois lados:

```
gap = (timestamp de "[WS] LLM stream iniciando")
    - (timestamp de "[WS] memory | hits=")
```

Agregação: mediana de pelo menos 3 turnos com LLM conectado, no mesmo
provider e na mesma máquina. Não há IC — 3 turnos não sustentam um, e fingir
que sustentam seria pior que não ter.

Métrica secundária, de conferência: **contagem de chamadas
`tok_in=9 tok_out=1`** no log durante os mesmos turnos.

---

## 6. Critério de decisão (PASSA / FALHA)

```
PASSA H1 sse TODAS:

  gap_depois ................. mediana < 1,0 s      (era ~21,8 s)
  probes_no_turno ............ 0 chamadas tok_in=9 tok_out=1 entre
                               "memory hits" e "stream iniciando"
  credencial_invalida ........ chave errada CONTINUA sendo recusada em
                               /connect, com a mensagem de sempre
  flag_off ................... EDP_LLM_VALIDATE_TTL=0 reproduz o
                               comportamento antigo (probe volta)
  suite ...................... pytest tests/ -q verde no edp_v5
```

**Qualquer linha falsa ⇒ FALHA, e H0 é o resultado.** O critério não é
reaberto depois de ver o dado.

**O que PASSA autoriza:** fechar a dívida #56. **Não autoriza** mexer em mais
nada do caminho do turno — os outros chamadores de `is_connected()`
(`session_summary.py:154`, `consolidator.py:47`, `llm.py:59,89`) se beneficiam
do mesmo cache sem alteração própria, e nenhum deles é tocado.

---

## 7. Anti-mock e isolamento

O mecanismo é o real: `LLMClient.is_available` e
`AnthropicProvider.validate`, sem reimplementação. Os testes usam um provider
falso **apenas para contar chamadas** — a lógica de cache testada é a do
código de produção.

**O que esta mudança NÃO pode tocar:**

```
[ ] o caminho Ollama/OpenAI de is_available (GET local, sem rede externa)
[ ] a assinatura de is_connected() / is_available()
[ ] telemetria=False em validate() — a chamada de 1 token continua fora do
    dataset quando acontecer
[ ] o store: nada aqui escreve em edp_data
```

`edp_v5` é repositório **público**: nenhum texto de conversa entra no commit.

---

## 8. Onde o resultado vai

`docs/VEREDITO_divida_56_probe.md`, com o gap medido antes e depois, a
contagem de probes, e o veredito H1/H0. Enquanto ele não existir, #56 continua
**ABERTA** em `DIVIDAS.md`, mesmo que o código já esteja no lugar — código
escrito não é dívida fechada.

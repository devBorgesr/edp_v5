# Decisão pendente — o probe de rede no caminho do turno

**03/09/2026. Nenhuma opção foi escolhida. Nenhum código foi alterado.**

Dívida [#56](DIVIDAS.md). Mesma forma de `docs/agent_runtime/DECISAO_TRANSPORTE.md`:
inspeção primeiro, opções com custo, recomendação que **não é decisão**.

---

## 1. O que foi medido

Log de execução em produção (host Windows, store `C:\edp_data_todo\edp_data`,
`claude-haiku-4-5`):

```
04:54:15,985  [WS] msg recebida
04:54:15,988  [WS] pipeline ok
04:54:16,192  [WS] memory | hits=7          <- fim da recuperacao
04:54:37,976  [anthropic] complete lat=21782ms tok_in=9 tok_out=1   <- PROBE
04:54:37,999  [WS] LLM stream iniciando     <- 23 ms depois
04:55:05,545  [WS] done llm_used=True
```

**Turno inteiro: 49,6 s. Probe: 21,782 s — ~44%, para produzir 1 token.**

Cinco probes `tok_in=9 tok_out=1` em ~5 minutos: 22.065, 9.836, 21.818,
21.782 e 11.091 ms. **86,6 s somados.**

## 2. A cadeia

```
websocket.py:765   runtime.is_connected()
llm_adapter.py:1898  self._client.is_available()
llm_adapter.py:636   provider == ANTHROPIC -> self._anthropic_provider.validate()
anthropic.py:515     CompletionRequest(messages=[Message(USER,"1")], max_tokens=1)
                     -> chamada real a api.anthropic.com
```

Assimetria que importa: para Ollama/OpenAI, `is_available()` é um `GET` local
com timeout de 3 s (`llm_adapter.py:645-650`). **Só o caminho Anthropic paga
uma ida e volta à rede.** O mesmo código, dependendo do provider, custa 3 ms
ou 22 s.

O custo não é dinheiro: `cost=$0.0000`, e `validate()` já passa
`telemetria=False` de propósito, com comentário explicando que uma amostra de
~1 token puxaria a razão chars/token do estrato inteiro. Quem escreveu pensou
no dataset. O que não foi pensado é que a chamada mora no caminho quente.

## 3. O que os chamadores realmente querem — verificado um a um

| onde | uso |
|---|---|
| `websocket.py:765` | guarda: entrar no ramo do LLM ou no fallback cognitivo |
| `websocket.py:1181` | guarda: escrever "Nenhum LLM conectado" no fallback |
| `session_summary.py:154` | guarda: gerar resumo ou desistir |
| `ingest/consolidator.py:47` | guarda: idem, em job de fundo |
| `llm.py:59` | guarda: já conectado? senão, conecta |
| `llm.py:89` | guarda: tentar LLM ou cair no fallback |

**Seis chamadores, seis guardas.** Nenhum pede relatório de saúde do provider;
todos perguntam "existe LLM para eu usar?". E `health.py:25` já registra a
distinção no próprio comentário: *"NÃO chama provider.validate() (isso pinga a
rede)"* — o endpoint de saúde, que seria o lugar legítimo do probe, foi
deliberadamente escrito para não fazê-lo.

## 4. As alternativas

### A — `is_connected()` lê estado; probe fica em `/connect` e `/providers`

```
mudanca ......... llm_adapter.py:1898 -> `self._client is not None`
semantica ....... "ha cliente configurado", que e o que os 6 chamadores querem
credencial morta  ja tratada: chat/stream levantam AuthError no uso real
probe sobrevive   /connect (llm_adapter.py:1546) e routes/providers.py:48 —
                  os dois lugares onde alguem PERGUNTOU se o provider responde
custo ........... ~22 s a menos por turno; 0 chamadas de 1 token por turno
risco ........... um provider que caiu entre o /connect e o turno so e
                  descoberto no turno — com AuthError/erro de rede, que e
                  onde o usuario ja veria o problema de qualquer forma
```

### B — cachear `validate()` com TTL no `LLMClient`

```
mudanca ......... llm_adapter.py:636, guardando (resultado, timestamp)
semantica ....... preservada: continua "o provider respondeu ha ate T"
custo ........... 1 probe a cada T segundos, em vez de 1 por turno
risco ........... escolher T. T curto nao resolve; T longo e a opcao A com
                  passos extras. E preciso decidir se erro tambem e cacheado —
                  se nao for, um provider fora do ar volta a custar 22 s por
                  turno exatamente quando o sistema ja esta ruim
```

### C — não fazer nada

```
custo ........... ~44% da espera do usuario, medido
justificativa ... nenhuma que eu tenha encontrado no codigo ou no historico
```

## 5. Recomendação, que não é decisão

**A.** Três razões, em ordem de peso:

1. **É o que os seis chamadores já querem.** Nenhum usa o valor como
   diagnóstico; todos como guarda. `A` alinha a implementação ao uso real, em
   vez de manter um contrato que ninguém exerce.
2. **O projeto já tomou esta decisão uma vez**, em `health.py:25`, e escreveu
   o porquê. `A` é estender a mesma regra ao caminho do turno.
3. **`B` adia sem resolver.** Com TTL longo é `A` com mais peças; com TTL
   curto o custo volta.

## 6. O que esta decisão NÃO resolve

**A latência do próprio provider.** O stream real levou 24,7 s neste mesmo
turno, e o TTFT foi 22,1 s. `A` remove ~22 s de espera evitável; não torna a
rede desta máquina rápida.

**Se `is_connected` é o nome certo** para "há cliente configurado". Renomear
tocaria seis chamadores e não é necessário para a correção.

**Repositório público.** `edp_v5` é público e isto é caminho quente do kernel.
A mudança é pequena, mas o lugar não é.

## 7. Registro da decisão

```
decidido por ....... ____________________
data ............... ____________________
opcao .............. A / B / C
se B, TTL .......... ____________________
erro tambem cacheado ____________________
justificativa ...... ____________________
```

Enquanto estas linhas estiverem em branco, `websocket.py:765` continua
pagando ~22 s por turno no caminho Anthropic, e a dívida #56 continua aberta.

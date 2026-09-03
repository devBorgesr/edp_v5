# Veredito — Dashboard v3.5, validação visual em navegador real

**03/09/2026.** Commit da refatoração: `aa739d1`. Commit das correções que
esta validação produziu: `117e8d5`. Suíte no fim: `448 passed, 1 deselected`.

Este documento existe porque a suíte unitária passava **antes** da validação,
com três defeitos visuais vivos. Teste verde não é tela correta, e a diferença
entre os dois é o que está registrado aqui.

---

## 1. Método

Firefox 154 headless, dirigido por cliente WebDriver BiDi escrito à mão
(`browsingContext.*`, `script.evaluate`, `log.entryAdded`). Servidor do EDP
subido contra **cópia** do store, em `/tmp/edp_val`.

O que foi exercitado de ponta a ponta: `/dashboard`, `/dashboard/state`,
WebSocket real, fallback HTTP com `WebSocket` sabotado no preload, sidebar
compartilhada, quatro larguras de viewport, e os três estados de runtime.

---

## 2. Os três defeitos que só apareceram no navegador

### 2.1 O toggle da sidebar cobria a primeira letra do título

`#edp-sb-toggle` (`edp/api/routes/_sidebar.py`) é `position: fixed`, 38×38 em
`left:12px`, **sem media query e sem empurrar conteúdo** — overlay puro, em
toda largura. A renderização headless mostrou `"DP RUNTIME"`.

Corrigido com reserva explícita: `padding: 0 20px 0 62px` (62 = 12 + 38 + 12),
e `0 12px 0 60px` na media query estreita. Remedido: título em x=82, toggle
terminando em x=50.

### 2.2 `…` prometia carregamento onde o backend nunca manda o campo

Nove ocorrências no template. Reticência significa "carregando"; o estado real
é "não existe dado". Trocadas por `—`.

### 2.3 Estado velho apresentado como atual — o defeito grave

Dois casos da mesma família, ambos de mostrar na tela algo que não aconteceu:

**(a) `model` exibia ✓ sem nunca ter rodado.** Sem LLM conectado, `llm_start`
não dispara — mas o handler de `chunk` concluía o nó assim mesmo. Agora só
conclui nó que estava `active` (`concluiSeAtivo`), e o turno sem LLM deixa
`model` em `waiting`, com sub `"turno concluido sem LLM"`.

`response` continua indo direto a `done` **de propósito**: quem o conclui é o
evento `done`, que evidencia a resposta. `chunk`, que concluía `model`, chega
com ou sem LLM — não evidencia nada sobre o modelo.

**(b) Seção ausente do payload mantinha no DOM o valor do poll anterior.** Um
payload sem `pressure` seguia exibindo `critical` de 15 s atrás. Os guards
eram `if (data.X) {…}`: cobriam **campo** ausente, não **seção** ausente,
enquanto a REGRA no topo de `dashboard.js` já dizia "campo ausente vira '—',
nunca valor". A regra valia para o caso pequeno e não para o grande.

Os blocos agora rodam sempre, com objeto vazio; `pressure`, `queue`,
`retrieval_quality`, `memory`, `contradictions` e `llm_metrics` caem em `—`.

Este é o defeito de maior consequência dos três: num painel operacional,
indicador desatualizado exibido como atual é pior que indicador ausente.

---

## 3. O que ficou provado

| Item | Evidência |
|---|---|
| Renderização | `/dashboard` 200; console `[]` em 1440/1280/1024/768/390 |
| Sidebar | painel x=-230 → 0 → -230; overlay opacidade 0→1→0; 5 links |
| `/dashboard/state` | 200, 11 chaves; DOM bate com o payload campo a campo |
| WebSocket | 2 turnos reais; eventos 3→6, mensagens 3→6, op_blocks 1→2 |
| Fallback HTTP | `WebSocket` sabotado → `HTTP OK \| 5 memorias \| 0ms` |
| Runtime flow | `r→p→s→R`; nós `done` sem terem sido `active`: nenhum |
| Estados | `normal→ok`, `warning→warn`, `critical→err`, distintos |
| Dados ausentes | payload parcial → `—` em toda a linha |
| Segurança | `type=password`, `autocomplete=off`, localStorage 0, sessionStorage 0, chave ausente do HTML, zero recursos externos |
| Responsivo | 0 cards cortados em 1440/768/390; flow rola no próprio container |

---

## 4. O que NÃO ficou provado — limites declarados

### 4.1 Não foi validado contra o store de produção

O share `sf_edp_data_todo` **não estava montado** durante a validação. O
servidor rodou sobre `/tmp/edp_val`, e as escritas caíram lá
(`episodic.json`, `events.jsonl`, `metrics.jsonl`).

O que isso permite afirmar: frontend, servidor, endpoints, WebSocket,
persistência e integração funcionam **sobre uma cópia do ambiente de dados**.

O que isso **não** permite afirmar: "validado contra o store real". A frase
não pode ser usada, e a garantia disponível é de outra natureza — o store
produtivo esteve inalcançável no sistema de arquivos, logo não pôde ser
escrito. Isso protege o store; não valida contra ele.

### 4.2 O caminho com LLM real não foi exercitado

Provado: `request → pipeline → stream → response` (turno cognitivo sem LLM).

**Não provado:** `request → pipeline → model → stream → response`.
`advanceFlow('model')` no handler de `llm_start` foi lido no código, não
executado — exige provider real com chave. Fica como smoke test posterior.

### 4.3 Fronteira de escopo mantida de propósito

Este é o dashboard do **`edp_v5`**, e visualiza o pipeline que este backend de
fato tem. O `agent_runtime` (`lab_edp_novo`) tem outra semântica —
`Task → Router → Model → Intenção → Política → Capacidade → Observação`. Os
dois não foram unificados, e o comentário em `dashboard.js` registra isso no
próprio código. Tratar os dois pipelines como o mesmo seria inventar
correspondência que não existe.

---

## 5. Oportunidade registrada, deliberadamente não implementada

`retrieval_quality` já devolve série diária com `avg_top` e `turns`. O
dashboard lê apenas `trend` e `total_turns` — a série é descartada. Existe aí
um gráfico de tendência **sem métrica nova no backend**.

Não foi implementado, e a razão não é custo: **`avg_top` não tem definição
fechada neste projeto**. Antes de desenhar qualquer série é preciso dizer o
que a grandeza representa e o que pode ser afirmado a partir dela — caso
contrário um indicador operacional vira alegação de qualidade/relevância, que
é exatamente a fronteira NÍVEL 1 / NÍVEL 3 que o resto do projeto sustenta.

Registrado como dívida em [`DIVIDAS.md`](DIVIDAS.md) #55.

---

## 6. Estado

**Dashboard congelado nesta versão.** O próximo trabalho não é CSS.

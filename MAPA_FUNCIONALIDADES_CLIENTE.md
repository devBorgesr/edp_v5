# Mapa de funcionalidades — EDP v5 + lab_edp_novo + sf_exportador

**Data:** 2026-08-12. **Pergunta respondida:** tudo que existe, funcionando, nos
três repositórios, e o que disso um possível cliente pagante (`NORTE.md §2`)
pode efetivamente usar hoje.

**Enquadramento antes da lista** — `NORTE.md §1` já define o produto, e não é
"chatbot com memória": é uma **plataforma de observabilidade e certificação
para sistemas cognitivos** — auditoria de retrieval/RAG, vendida a R$3k/mês
(meta única, `NORTE.md §2`). Duas populações usam isto hoje: **Daniel**, uso
real diário com dados próprios, e o **cliente pagante**, que ainda não existe
(0 abordagens enviadas até agora). Esta lista cobre capacidade técnica, não
oferta comercial — a tradução de uma para a outra está na seção 7.

Cada item cita onde mora no código. Tags:

| tag | significado |
|---|---|
| **VIVO** | roda por padrão hoje, caminho de produção |
| **FLAG-OFF** | existe, desligado por padrão |
| **OBSERVAÇÃO** | mede/expõe, mas por desenho **não** altera decisão (distinção que o próprio código declara) |
| **MORTO** | está no repo, zero importador, não roda |
| **INTERNO** | ferramenta de pesquisador/operador — não é superfície pensada para cliente externo hoje |
| **REFUTADO** | testado com pré-registro e descartado; mantido registrado, não escondido |

---

## 1. `edp_v5` (kernel) — o que a API faz

### 1.A Memória — captura, edição, ciclo de vida

| funcionalidade | endpoint / arquivo | tag |
|---|---|---|
| Captura de turno em tempo real, com proveniência | `WS /ws/chat/{session_id}` (`edp/api/routes/websocket.py`) | VIVO |
| Adicionar memória manualmente | `POST /memory/add` | VIVO |
| Listar, ler, editar, apagar memórias individualmente | `GET /memory/list`, `GET/PATCH/DELETE /memory/{id}` | VIVO |
| Apagar em lote | `POST /memory/batch_delete` | VIVO |
| Estatísticas de memória (contagem por camada etc.) | `GET /memory/stats` | VIVO |
| Consolidação episódica → semântica, modo seguro ou destrutivo | `POST /memory/consolidate` (`promote_only` vs `full`) | VIVO |
| Reclassificação em massa | `POST /memory/reclassify_all` | VIVO |
| Resumo automático de sessão | `POST /memory/summarize_session` | VIVO |
| Tópicos emergentes por sessão, com contagem | `GET /memory/topics` | VIVO |
| Trajetória derivacional — raízes (ideias-mãe) e folhas (conclusões recentes) | `GET /memory/trajectory` | VIVO — rotulado no próprio código como "sprint 1 do roadmap fractal", estágio inicial |
| Dominância — quais memórias monopolizam o retrieval | `GET /memory/dominance` | VIVO — diagnóstico |
| Co-ocorrência entre memórias | `GET /memory/co_occurrence` | VIVO |
| Navegação por sessão (lista, detalhe, resumo) | `GET /memory/sessions`, `/memory/session/{id}`, `/summary` | VIVO |
| UI HTML de revisão humana de memórias | `GET /memory/review` | VIVO, uso de operador único |

### 1.B Retrieval — busca e depuração

| funcionalidade | endpoint / arquivo | tag |
|---|---|---|
| Retrieval híbrido (BM25 + vetorial, fusão RRF) | `POST /retrieve`, `edp/retrieval_hybrid.py`, flag `EDP_HYBRID_RETRIEVAL` | VIVO, default ON |
| Depuração de retrieval — por que este resultado voltou, com scores | `GET /memory/retrieve_debug` | VIVO — é literalmente o produto de auditoria do `NORTE.md §2` |
| Teste isolado do roteador de modelo antes de usar no chat | `POST /memory/route_test` | VIVO |
| Retrieval vetorial standalone (FAISS/hnswlib) | `edp/retrieval.py` | MORTO — zero importador (`README.md §2`) |
| Vector store standalone | `edp/vector_store.py` | MORTO |
| Grafo de memória | `edp/memory_graph.py` | MORTO |

### 1.C Governança epistêmica — o diferencial declarado

| funcionalidade | onde | tag |
|---|---|---|
| Estados de confiabilidade `contestado`/`quarentenado`/`hipótese` propagados ao ranking | `edp/memory/store.py`, `AVALIACAO_MEMORIA_VS_MERCADO.md` | VIVO — o que Mem0/LangChain memory não fazem, por comparação já documentada |
| Piso de exclusão de conteúdo tóxico (regra composta R4/OR) | exp012 mergeado | VIVO |
| Dedup de leitura no retrieve | exp017, H1 passou com critério conjuntivo | VIVO |
| Extração de decisões cognitivas em background (`key_assertion`+`concepts`+`domain`, via Haiku-mini) | `GET /cognitive_decisions`, `edp/runtime/cognitive_decisions.py` | VIVO como extração+leitura — **mas o output não influencia o ranking hoje** (dívida conhecida, `README.md §4`) |
| Review de flags de meta-memória com veredito humano (útil / falso-positivo / ambíguo) | `GET/POST /flags*` | **OBSERVAÇÃO**, por desenho — docstring do próprio router: "instrumentação de observação, não governança" |
| Lineage — proveniência por resposta: quais entradas de memória, scores, `source_type`, modelo usado (sem texto da memória) | `GET /lineage`, `GET /lineage/{response_id}` | VIVO — é o segundo pilar de evidência de auditoria, junto com `retrieve_debug` |

### 1.D Chat, modelos e execução

| funcionalidade | endpoint | tag |
|---|---|---|
| Chat direto com LLM usando contexto de memória | `POST /chat`, `POST /connect` | VIVO |
| Listar/validar provedores LLM configurados | `GET /providers`, `POST /providers/validate` | VIVO — Anthropic e Ollama local confirmados no kernel |
| Listar ferramentas disponíveis ao agente | `GET /tools` | VIVO |
| Trocar modo de operação em runtime | `GET /current`, `POST /{name}` (Peça 2.6a) | VIVO |

### 1.E Observabilidade e saúde

| funcionalidade | endpoint | tag |
|---|---|---|
| Health check + registry de componentes | `GET /health`, `/health/registry` | VIVO |
| Métricas agregadas, snapshot, modos disponíveis | `GET /metrics`, `/snapshot`, `/modes` | VIVO |
| Dashboard visual (`/dashboard`): Chat Cognitivo, LLM Performance, Memória Cognitiva, Métricas de Sistema, Retrieval Stats, Runtime Health, Snapshot Info | `edp/dashboard/templates/dashboard.html` | VIVO |

### 1.F Wiki de conhecimento (do código, não de conversas)

| funcionalidade | endpoint | tag |
|---|---|---|
| Índice, busca léxica e páginas por comunidade de código (compilado do graphify) | `GET /wiki`, `/wiki/search`, `/wiki/{slug}`, `/wiki/{slug}.md` | VIVO, **INTERNO/dev-facing** — a docstring do router é explícita: "nada de conversa real é servido aqui" (`edp/api/routes/wiki.py:9`) |

---

## 2. `sf_exportador` — sensor + copiloto (extensão Chrome, v4.10.0)

### 2.A Captura e exportação

- Captura passiva de conversas do claude.ai em tempo real, incluindo texto,
  arquivos, imagens, blocos de *thinking* e chamadas de ferramenta
  (`interceptor.js`, roda no `MAIN world` desde `document_start`). **VIVO**
- Export em 4 formatos — JSON, ZIP, PDF, TXT — mais um modo "todas" em lote
  (`popup.html`, botões `btnJson/btnZip/btnPdf/btnTxt/btnTodas`). **VIVO**
- Self-test com 5 métricas de integridade do pipeline de captura (turnos via
  API vs. capturados ao vivo, taxa de match por uuid, thinking mesclado)
  (`popup.js`, `RELATORIO_EXPORTER.md`). **VIVO**

### 2.B Envio de dados para fora da extensão

- **Pipeline HTTP configurável** — envia capturas para endpoints definidos
  pelo usuário, com método/headers customizáveis, retry/backoff e log de
  auditoria (`API.md`, `options.html`). **VIVO, opt-in, desligado por padrão**
- **Live feed** — streaming em tempo real por WebSocket, unidirecional por
  desenho, com buffer local e reconexão a quedas breves; é o lado emissor
  do `WS /stream` que o kernel EDP expõe (`LIVE_EVENTS.md`,
  `edp/api/routes/live_feed.py`). **VIVO, opt-in**

### 2.C Copiloto — assistente local

- Sandbox de arquivos + terminal, dentro do navegador
  (`copilot/sandbox.js`, `copilot/terminal_ui.js`). **VIVO**
- Chat sobre os dados capturados: 100% local via Ollama por padrão, ou
  Anthropic/OpenAI com chave própria do usuário (`copilot/llm_adapter.js`).
  **VIVO**
- Seleção automática de modelo por custo-benefício (pergunta curta → modelo
  barato; análise/código → modelo mais capaz; contexto grande evita janela
  pequena), ou seleção manual (`copilot/llm_config.js`). **VIVO**
- Fallback automático para Ollama se o provedor externo falhar por rede ou
  chave inválida (não por recusa de política de conteúdo). **VIVO**
- Importar uma conversa já capturada para o sandbox do Copiloto.
  **VIVO**

### 2.D Captura e análise de tráfego de rede

- Inspeção de tráfego via `chrome.debugger`, Fase 4, opt-in explícito por
  aba, com "decisões de contenção" documentadas (`COPILOT_ARCHITECTURE.md`).
  **VIVO, opt-in**

### 2.E Privacidade, como desenho, não como promessa vazia

Nada sai da máquina do usuário sem opt-in explícito em cada camada
(pipeline HTTP, live feed, provedor externo, debugger). Ressalva que o
próprio projeto já documenta: a chave de API do Copiloto fica em
`chrome.storage.local` **sem criptografia real** — a única proteção é a do
perfil do Chrome no sistema operacional (`API.md`, `COPILOT_USER_GUIDE.md`).
Não é marketing, é a mesma disciplina de errata-não-apagamento do resto do
projeto.

---

## 3. `lab_edp_novo` — bancada de certificação

Isto **não é superfície de cliente hoje** — é o motor metodológico que, se
empacotado, vira a oferta de "certificação contínua" (upgrade R$5k/mês,
`NORTE.md §2`). Listado por completude, tag **INTERNO** em tudo:

- Núcleo agnóstico de sujeito (`bancada/`): prontuário, isolamento, scorer,
  sampler, repeater, rodízio, formatos — proibido importar `edp.*` por
  desenho (fronteira testada em `tests/test_fronteira.py`).
- Adaptador que ensina a bancada a "falar EDP" + 18 experimentos
  pré-registrados (`sujeitos/edp/experimentos/`, exp001–exp018).
- Disciplina obrigatória: hipótese, métrica e critério de decisão
  congelados **antes** de qualquer dado (`docs/TEMPLATE_PREREGISTRO.md`) —
  é o mesmo método que rendeu nota 9/10 numa auditoria de engenharia externa
  (`README.md §7`).
- Acervo de proveniência que resolve qual cópia de cada experimento é a
  canônica quando o mesmo código existe em dois repositórios
  (`docs/PROVENIENCIA_LAB.md`).

---

## 4. Funcionalidades testadas e refutadas — registradas, não escondidas

Um cliente de auditoria compra tanto o que passou quanto o método de
descartar o que não passou. Nada disto é "bug escondido": é resultado
publicado.

- **Honeypot de resposta** — H0 venceu (seletividade invertida).
- **exp015** — proibição em prompt não evitou reafirmação de desqualificação.
- **"Wiki de conversas camada 3"** — 2 de 5 alvos, critério era ≥3.
- **Gap Score, 4 implementações** (bruta / IDF / IDF⁰ / julgada por Haiku) —
  todas as quatro falharam a mesma condição do critério pré-registrado
  (`docs/preregistro_gap_score.md`).

---

## 5. Limites conhecidos — o que não prometer

- Single-user validado: 36 dias em produção real, sem crash reportado, **sem
  dado de comportamento em volume 10×/100×** (`README.md §1`,
  `AVALIACAO_ENGENHARIA_EDP.md §7`).
- Suíte de testes 100% sintética — nunca roda contra store real
  (`README.md §4`).
- 4 sinais computados e **nunca lidos** no ranking hoje: `cognitive_decisions`
  fora do ranking, `contradiction_flagger.scan_results()` descartado,
  `reflection.reweights` nunca aplicado, `RETRIEVAL_BACKEND` decorativo.
- Parâmetros hardcoded sem calibração documentada: `score=0.65` em 4 locais,
  `DEDUP_THRESH=0.75`, `anchor_boost=1.20`.
- `docs/DIVIDAS.md` cobre 3 de 21 dívidas referenciadas em código (14,3%).
- Branch protection nunca configurada no GitHub — CI roda, não bloqueia
  merge.

Inventário completo com comando de reprodução: `docs/edp_metodologia_v5.md §4`.

---

## 6. Resumo por volume

| repo | endpoints/funcionalidades VIVAS mapeadas | mortas | observação-apenas |
|---|---|---|---|
| `edp_v5` | ~34 (rotas HTTP/WS contadas acima, 14 routers) | 3 módulos (`retrieval.py`, `vector_store.py`, `memory_graph.py`) | 1 (`/flags*`) |
| `sf_exportador` | ~14 capacidades (export, pipeline, live feed, copiloto, tráfego) | 0 conhecidas | 0 |
| `lab_edp_novo` | 18 experimentos + bancada, todos internos | — | — |

---

## 7. O que isso vira em oferta comercial hoje (a lacuna real, `NORTE.md §2`)

Traduzindo capacidade técnica em "o que o cliente pagante recebe":

1. **"Auditoria de retrieval/RAG"** — o produto vendável mais próximo de
   pronto. `GET /memory/retrieve_debug` (por que este resultado voltou, com
   que score) + `GET /lineage/{response_id}` (proveniência por resposta,
   sem vazar conteúdo) já produzem exatamente o tipo de evidência que um
   relatório de auditoria cobra para entregar.
2. **"Certificação contínua"** (upgrade R$5k/mês) — a disciplina de
   pré-registro do `lab_edp_novo`, hoje só usada internamente, aplicada ao
   sistema do cliente em vez de ao próprio EDP.
3. **Captura de dado real sem acesso à infraestrutura do cliente** — o
   sensor/exportador resolve onboarding: não precisa instrumentar o backend
   do cliente, só instalar a extensão na sessão de quem já usa o sistema
   dele.
4. **A lacuna não é técnica, é de embalagem**: nada disto tem relatório-modelo,
   preço público ou case publicado. É a mesma lacuna que `NORTE.md §2` já
   registra — 0 de 20 abordagens enviadas. O gargalo comercial não é "o que
   construir a mais", é "empacotar o que já existe em uma oferta que alguém
   de fora consiga comprar sem ler código".

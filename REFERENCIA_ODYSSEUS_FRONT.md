# Referência: Odysseus Frontend
**Repositório:** github.com/pewdiepie-archdaemon/odysseus  
**Branch:** `feature/front-novo`  
**Data:** 2026-06-26  
**Finalidade:** Análise do frontend Odysseus como referência de stack, estrutura,
padrões de UX e componentes a adaptar para o EDP v5.

---

## 1. Stack e Arquitetura

### 1.1 Tecnologias

| Camada | Tecnologia |
|--------|-----------|
| Framework | **Nenhum** — Vanilla JavaScript puro |
| Build tool | Nenhum (arquivos servidos diretamente) |
| Bundler | Nenhum |
| Estilo | CSS puro (`style.css`, 1.15MB, 38k linhas) |
| PWA | Service Worker (`sw.js`) + `manifest.json` |
| Ícones | Pasta `static/icons/` |
| Fontes | Fira Code (self-hosted em `static/fonts/`) |
| Libs | `static/lib/` (markdown, highlight.js, etc.) |

**Obs crítica:** Odysseus é vanilla JS por necessidade (projeto multi-plataforma,
desktop app wrapper, sem build step). Para o EDP v5 com stack moderna, esta
decisão não se aplica — usaremos um framework.

### 1.2 Estrutura de Pastas (frontend relevante)

```
odysseus/
├── static/
│   ├── index.html          — SPA principal
│   ├── login.html          — Tela de auth
│   ├── app.js              — Orquestrador: importa todos os módulos e inicializa
│   ├── style.css           — Todas as classes (1 arquivo monolítico)
│   ├── sw.js               — Service worker (offline/caching)
│   ├── manifest.json       — PWA manifest
│   ├── fonts/              — Fira Code
│   ├── icons/
│   └── js/
│       ├── init.js         — Inicialização de estado global
│       ├── ui.js           — Primitivos de UI
│       ├── storage.js      — Persistência local
│       ├── platform.js     — Detecção de plataforma
│       ├── chat.js         — Lógica principal de chat (envia/recebe/renderiza)
│       ├── chatStream.js   — UI de streaming (notificações, toasts)
│       ├── chatRenderer.js — Renderização de markdown nas bolhas
│       ├── sidebar-layout.js
│       ├── workspace.js
│       ├── modalManager.js
│       ├── tileManager.js
│       ├── theme.js
│       ├── models.js / modelPicker.js
│       ├── memory.js
│       ├── rag.js
│       ├── providers.js
│       ├── slashCommands.js
│       └── [40+ módulos de features]
```

### 1.3 Inicialização

```
index.html
  └── app.js (módulo ES6)
        ├── import sessionModule, chatModule, uiModule, ...
        ├── API_BASE = window.location.origin
        ├── wrap window.fetch para 401→login redirect
        ├── _refreshDefaultChat() — prime modelo default
        ├── binds: form, uploads, sidebar, tools
        └── URL routing: /calendar, /notes → auto-abre tool
```

---

## 2. Design Visual — Sistema de Cores

### 2.1 Paleta (CSS Custom Properties)

```css
:root {
  /* Fundo e superfícies */
  --bg:     #282c34;   /* fundo principal */
  --panel:  #111;      /* painéis/sidebar */
  --border: #355a66;   /* bordas */

  /* Texto */
  --fg:     #9cdef2;   /* texto principal (azul-ciano claro) */

  /* Acentos */
  --red:    #e06c75;   /* acento primário (botão send, scrollbar) */
  --green:  #50fa7b;   /* sucesso / agente ativo */
  --warn:   #f0ad4e;   /* aviso */

  /* Semântico */
  --color-accent:       #00aaff;
  --color-error:        #ff4444;
  --color-success:      #4caf50;
  --color-warning:      #f0ad4e;
  --color-danger:       #c0392b;
  --color-agent-active: #00ff00;
  --color-brand-blue:   #3b82f6;
}
```

**Comparação com EDP atual:**
```css
/* EDP v5 atual */
--bg:      #0a0a0f;   /* mais escuro */
--surface: #12121a;
--accent:  #7c3aed;   /* roxo */
--accent2: #06b6d4;   /* ciano */
```

**Para o novo frontend:** manter a paleta do EDP (mais escura, roxo/ciano)
mas incorporar os padrões estruturais do Odysseus. Não importar as cores do
Odysseus — são incompatíveis com a identidade do EDP.

### 2.2 Tipografia

- **Fonte:** Fira Code (monospace), pesos 300/400/600
- **Tamanho base:** 0.95em (densidade normal), 13px (compact), 16px (spacious)
- **Linha:** 1.5 para código/texto técnico

**Para o EDP:** JetBrains Mono / Fira Code já é o padrão — manter.

---

## 3. Padrões de UX a Adaptar

### 3.1 Layout Geral

```
┌──────────────────────────────────────────┐
│         Sidebar (240px)  │  Chat Area     │
│  ─────────────────────── │  ─────────────│
│  [Sessions list]          │  [Messages]   │
│  [Tool buttons]           │  centered     │
│  [User bar]               │  max-w: 800px │
│                           │  [Input bar]  │
└──────────────────────────────────────────┘
```

**Sidebar:**
- 240px largura, collapsível para 0 (icon rail de 48px)
- Sessions clicáveis como `list-item`
- Tool buttons: Memory, Cookbook, Research, etc.
- User bar no rodapé

**Chat area:**
- `flex: 1`, `padding: 0 16px`
- Mensagens centralizadas com `max-width: 800px`
- Auto-scroll quando próximo do rodapé (`< 80px`)
- Disable auto-scroll ao rolar para cima

### 3.2 Bolhas de Mensagem

```
User:
  align-self: flex-end
  border-radius: 18px 18px 0 18px
  background: acento primário
  animation: msg-enter 0.3s ease-out

AI:
  align-self: flex-start
  border-radius: 18px 18px 18px 0
  background: var(--panel)
  animation: msg-enter 0.3s ease-out
```

**Fase streaming:**
- `.streaming` class aplicada durante geração
- `.live-reply-content` div recebe chunks incrementalmente
- Thinking box separada (antes do reply) com timer e spinner

### 3.3 Streaming de Chat

**Odysseus (SSE):**
```
POST /api/chat_stream (FormData)
→ res.body.getReader() (SSE chunks)
→ linha: "data: {json}"
→ eventos: delta, thinking, [DONE]
```

**EDP (WebSocket):**
```
WS /ws/chat/{session_id}
→ {type: "chunk", text: "fragmento"}
→ acumular e exibir progressivamente
```

O padrão de render é o mesmo — chunks acumulados, DOM atualizado a cada
fragmento. Só o protocolo de transporte difere.

### 3.4 Compare / Multi-Model

No Odysseus, o mode Compare:
- É ativado via botão na barra de ferramentas
- Intercepta o submit: `compareModule.handleCompareSubmit()`
- Renderiza bolhas lado a lado por modelo
- Cada bolha tem `.role` com nome do modelo

No EDP, o Compare é alimentado pelos eventos `camara_*`. Adaptar:
- Painel lateral colapsável (ou inline abaixo da resposta)
- Bolhas `A` vs `B` lado a lado
- Badge de vencedor
- Chips de checks (PASS/FAIL)
- Barra de concordância

### 3.5 Memória / RAG

No Odysseus:
- `memory.js` — painel de memórias (modal)
- `rag.js` — RAG (ChromaDB simples)
- Integrado via botão na sidebar

Para o EDP (superior ao Odysseus):
- Usar `/memory/list`, `/memory/stats`, `/memory/dominance`, `/memory/topics`
- Painel dedicado na sidebar (não modal — tela separada)
- Exibir epistemic_status, co-occurrence, trajectory

### 3.6 Sidebar — Navegação Entre Telas

```
Odysseus: single-page com painéis/modais
EDP novo: multi-view com sidebar + router

Sidebar items:
  💬 Chat          → /chat
  🧠 Memória       → /memory
  ⚡ Echo Chamber  → /compare
  📊 Status        → /dashboard
  🔍 Flags         → /flags
```

### 3.7 Modelo de Sessão

**Odysseus:** múltiplas sessões (lista na sidebar, clique para trocar)  
**EDP (limitação de backend):** sessão única `"default"` (não há API de listagem de sessões históricas). O frontend novo deve trabalhar com session_id fixo por enquanto.

### 3.8 Temas e Densidade

**Odysseus:** `theme.js` com light/dark toggle, `density-compact/spacious`

Para o EDP novo: implementar dark (padrão) + light mode toggle.
Densidade: `compact` para telas menores.

---

## 4. Padrões a NÃO Adaptar

| Padrão Odysseus | Por quê não adaptar |
|----------------|-------------------|
| Vanilla JS sem framework | EDP usa stack moderna; React/Vite são mais adequados |
| SSE (Server-Sent Events) | EDP já usa WebSocket — não mudar o backend |
| ChromaDB / RAG simples | Memória EDP é arquiteturalmente superior |
| `compareModule.handleCompareSubmit()` — faz 2 requests paralelos | Echo chamber EDP é um processo único — o Compare é resultado de 1 WS connection |
| Auth / login | EDP não tem autenticação |
| Service Worker / offline | Não é prioridade; pode ser fase futura |
| Multi-janela (tileManager) | Complexidade desnecessária para EDP solo |
| Sessions múltiplas na sidebar | Backend EDP não suporta listagem histórica |

---

## 5. Componentes a Criar no EDP (inspirados no Odysseus)

### 5.1 Sidebar

```
<Sidebar>
  <SessionHeader />           — session_id + status dot
  <NavItems>                  — Chat, Memória, Echo Chamber, Dashboard, Flags
  <ConnectionPanel>           — provider/model/connect form (importado do atual)
  <StatusDot>                 — runtime_state badge
```

### 5.2 Chat

```
<ChatView>
  <MessageList>               — histórico de mensagens
    <Message role="user|assistant">
      <Bubble>                — texto com markdown
      <SourcesPanel>          — fontes colapsáveis (Feature 1 já existente)
      <CamaraPanel>           — echo chamber (Feature 2 já existente)
  <StageIndicator>            — "Pipeline..." / "Processando..."
  <InputBar>                  — textarea + send + mode indicator
```

### 5.3 Memory View

```
<MemoryView>
  <StatsBar>                  — total, by_status (cards)
  <DominancePanel>            — top retrievals, gini
  <FilterBar>                 — layer, status, source, search
  <MemoryList>                — entries com paginação
    <MemoryCard>              — text, status badge, edit inline
  <TopicsPanel>               — tópicos emergentes
```

### 5.4 Dashboard View

```
<DashboardView>
  <RuntimeStatus>             — boot_state (READY/DEGRADED/etc.)
  <PressureGauge>             — RAM disponível, nível
  <QueueStatus>               — fila de inferências
  <MemoryStats>               — episodic/semantic totals
  <ConflictsPanel>            — contradiction flags recentes
  <RetrievalQuality>          — avg score, hit rate
```

### 5.5 Echo Chamber / Compare View

```
<EchoView>
  — Mostra estado de câmara do turno atual
  — Lista histórico de câmaras (via /lineage)
  <CamaraCard>
    <CompareColumns>          — A vs B side-by-side
    <ChecksRow>               — chips PASS/FAIL dos 7 checks
    <SynthesisBar>            — concordância + vencedor badge
```

---

## 6. Decisão de Stack Recomendada

Com base na análise:

**Odysseus** é vanilla JS por necessidade de cross-platform + sem build.  
**EDP novo** é um cliente web puro — framework JS moderno é totalmente adequado.

**Stack recomendada: React 18 + Vite + TypeScript + Tailwind CSS**

| Camada | Escolha | Motivo |
|--------|---------|--------|
| Framework | React 18 | Mais familiar, ecossistema rico, SSR não necessário |
| Build | Vite | Rápido, hot reload excelente, proxy embutido |
| Linguagem | TypeScript | Tipos para o contrato de API — reduz bugs de integração |
| Estilo | Tailwind CSS | Utility-first, sem CSS sheets monolíticos, dark mode nativo |
| State | Zustand | Leve, simples, melhor que Redux para projeto solo |
| WS | Hook customizado (`useWebSocket`) | Sem lib externa — protocolo EDP é simples |
| REST | TanStack Query (React Query) | Cache, refetch, loading states automáticos |
| Markdown | `react-markdown` + `rehype-highlight` | Para renderizar respostas do LLM |
| Router | `react-router-dom` v6 | SPA routing, sidebar nav |

**Por que não Vue/Svelte?** React tem o ecossistema de componentes mais rico
para drag, markdown, modals — features que serão necessárias nas fases 3+.
Svelte seria mais leve mas menos suportado para o que vem depois.

**Vite proxy:** toda chamada a `/ws/`, `/memory`, `/dashboard`, etc. é
proxiada para `http://localhost:8000` — zero CORS, zero mudança no backend.

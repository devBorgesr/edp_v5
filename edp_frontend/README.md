# EDP v5 — Frontend

Interface moderna para o EDP v5 (Episodic-Driven Pipeline).

## Pré-requisitos

- Node.js ≥ 20
- Backend EDP v5 rodando em `http://127.0.0.1:8000`

## Subir em desenvolvimento

```bash
# 1. Instalar dependências
npm install

# 2. Subir o backend primeiro (em outro terminal, na raiz do projeto)
#    python run.py   ←  ou como você costuma iniciar o EDP

# 3. Iniciar o front
npm run dev
```

O Vite sobe em `http://localhost:5173`.

## Proxy

O `vite.config.ts` configura proxy automático para o backend — sem CORS:

| Path | Destino |
|------|---------|
| `/ws/*` | `ws://127.0.0.1:8000` (WebSocket) |
| `/health`, `/connect`, `/memory`, `/dashboard`, … | `http://127.0.0.1:8000` |

Nenhuma configuração de CORS é necessária no backend. Todas as chamadas saem
do mesmo origin do Vite dev server.

## Rotas

| URL | Página | O que faz |
|-----|--------|-----------|
| `/` | Chat | Conversa via WS com streaming, câmara de eco e lineage por resposta |
| `/memory` | Memória | Lista episódica/semântica com filtros, busca, stats e exclusão |
| `/echo` | Câmara / Lineage | Histórico de proveniência (REST `/lineage`) — qual memória alimentou cada resposta |
| `/dashboard` | Dashboard | Estado do runtime, pressão, fila, memória, contradições (auto-refresh 5s) |
| `/flags` | Flags | Pares contraditórios detectados, com feedback (útil / falso+ / ambíguo) |

## Build de produção

```bash
npm run build
# artefatos em dist/
npm run preview   # serve o dist/ localmente para teste
```

## Linting

```bash
npm run lint
```

## Estrutura

```
edp_frontend/
  src/
    api/
      rest.ts        # cliente REST tipado (fetch + helpers get/post/patch/del)
      ws.ts          # singleton WebSocket com reconexão automática
    hooks/
      useWebSocket.ts  # hook React: status, messages, stage, sendMessage
    types/
      api.ts         # todos os tipos TS derivados do contrato do backend
    components/
      Layout.tsx     # shell: Sidebar + <Outlet />
      Sidebar.tsx    # navegação + StatusDot
      StatusDot.tsx  # indicador de conexão WS
    pages/
      ChatPage.tsx   # (Fase 2)
      MemoryPage.tsx # (Fase 3)
      EchoPage.tsx   # (Fase 3)
      DashboardPage.tsx  # (Fase 3)
      FlagsPage.tsx  # (Fase 3)
    App.tsx          # roteamento
    main.tsx         # entry point — QueryClient + BrowserRouter
    index.css        # Tailwind v4 + @theme EDP + estilos globais
  vite.config.ts     # proxy + plugins
```

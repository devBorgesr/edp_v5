# Stack do Frontend EDP v5

## Escolhas técnicas

| Camada | Escolha | Versão | Motivo |
|--------|---------|--------|--------|
| Framework UI | **React** | 19.x | Ecossistema maduro, hooks para estado reativo, suporte a streaming (concurrent) |
| Build tool | **Vite** | 8.x | HMR instantâneo, proxy nativo para WS + REST, zero config |
| Tipagem | **TypeScript** | 6.x | Contratos de API expressos como tipos — erros detectados em build |
| Estilo | **Tailwind CSS v4** | 4.x | Utilitários no JSX, tema via `@theme` (sem config.ts), bundle mínimo |
| Estado global | **Zustand** | 5.x | API simples, sem boilerplate, ideal para estado de sessão pequeno |
| Dados assíncronos | **TanStack Query** | 5.x | Cache, invalidação, estados de loading/error para chamadas REST |
| Roteamento | **react-router-dom** | 7.x | Rotas declarativas, layout compartilhado via `<Outlet />` |
| Markdown | **react-markdown** | 10.x | Renderização segura do conteúdo LLM, extensível com rehype |
| Highlight | **rehype-highlight** | 7.x | Syntax highlight nos blocos de código do chat |

## Por que não Odysseus vanilla?

O Odysseus usa JavaScript puro sem framework — ótimo para um projeto pessoal, mas
dificulta manutenção a escala. A abordagem com React + TypeScript permite:

- Tipagem end-to-end dos 14 tipos de mensagem WS
- Componentização do estado de câmara (3 eventos progressivos)
- Re-renders cirúrgicos via estado React em vez de DOM imperativo

## WebSocket

Singleton `EdpWebSocket` (um por `session_id`) com reconexão automática
backoff [1s, 2s, 4s, 8s, 16s]. O hook `useWebSocket` expõe uma API declarativa:
`{ status, messages, stage, sending, sendMessage, clearMessages }`.

## Cores EDP

Definidas como variáveis CSS via `@theme` do Tailwind v4:

```css
--color-bg:      #0a0a0f  /* fundo principal */
--color-surface: #12121a  /* painéis, sidebar */
--color-panel:   #0f0f18  /* blocos de código, sub-painéis */
--color-border:  #1e1e2e  /* divisores */
--color-accent:  #7c3aed  /* roxo — ação primária */
--color-accent2: #06b6d4  /* ciano — links, lineage */
--color-ok:      #22c55e  /* verde — conectado, PASS */
--color-warn:    #f59e0b  /* âmbar — aviso, câmara */
--color-err:     #ef4444  /* vermelho — erro, FAIL */
--color-text:    #e2e8f0  /* texto principal */
--color-muted:   #64748b  /* texto secundário */
```

## Proxy Vite (sem CORS)

Toda chamada REST/WS passa pelo proxy do Vite em dev:

- `ws://localhost:5173/ws/*` → `ws://127.0.0.1:8000`
- `http://localhost:5173/health` → `http://127.0.0.1:8000/health`
- (e todos os demais paths do EDP)

Nenhuma linha de Python foi alterada. O backend não precisa de CORS adicional.

## Pendências de backend identificadas

Descobertas durante a análise do contrato (sem alterar o backend):

1. **`session_id` fixo em `"default"`** — sem multi-sessão via UI
2. **`WsDone.llm_used`** — campo presente mas não consumido no front ainda
3. **`WsStart.stage`** — distingue `'pipeline'` vs `'command'`; front pode
   exibir fluxo diferente por tipo futuramente

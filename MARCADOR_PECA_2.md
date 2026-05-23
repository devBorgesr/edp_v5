# MARCADOR DE ESTADO — Peça 2 do EDP (Presença)

**Última atualização:** 2026-05-23

---

## Sub-passos da peça 2 — Status

- ✅ **2.0** — Infraestrutura de blocos (commit `40c1941`)
- 🟡 **2.1** — Roteador de modelos: refutadores + gatilho manual (aguardando commit/validação)
- ⏳ **2.2** — Câmara de eco básica (Camada 1 mínima)
- ⏳ **2.3** — Critério de ativação completo (5 heurísticas)
- ⏳ **2.4** — Transparência tempo real no dashboard
- ⏳ **2.5** — Verificação humana (Camada 2)
- ⏳ **2.6** — Fechamento de bloco (heurísticas automáticas)
- ⏳ **2.7** — Retrieval respeitando blocos
- ⏳ **2.8** — Construção de contexto na nova era
- ⏳ **2.9** — Freios em processos secundários
- ⏳ **2.10** — Comentários acumulados em blocos antigos

---

## Articulação da peça 2 (fixada antes de codar)

### Metáfora central — Trindade sem divindade
- **Pai = Usuário** — ponto central, origem da intenção
- **Filho = Modelo** — inteligente mas ingênuo
- **Espírito = EDP** — não fala, não tem opinião, espelho dos dois lados

### Princípio
Presença emerge da fidelidade do espelho em cada ponto do fluxo. Peça 2 = limpar o espelho. Não é módulo novo.

### Câmara de eco — Camada 1
- Roteador escolhe Modelo A econômico
- Modelos B emergem acima de A na hierarquia
- Quando A=Opus: sem refutador acima (ápice, contexto já curado)
- Todos com mesmo contexto
- 7 checks fixos + ceticismo fixo + outros configuráveis pelo Pai
- B reformula texto, A avalia reformulação, % concordância como sinal
- EDP decide ativação por custo-benefício
- Quando ativa: Pai vê processo em tempo real

### Verificação humana — Camada 2
- **Unidade = bloco**, não turno individual
- Aprovação plena rara; estado normal é aceitações parciais acumulando
- Bloco fechado contém ouro destilado

### Estrutura do bloco (peça 2.0)
- Texto principal (ouro destilado)
- Metadados de validação datados
- Memórias-fonte (entry_ids)
- **Comentários posteriores** (refutações ficam aqui, não viram blocos)
- Relações inter-blocos (supersedes, superseded_by) — raras
- Status: active / closed / superseded

### Princípio temporal
- Nenhum bloco é definitivo
- Verificação é datada
- Refutação besta ou genial fica como comentário no bloco
- EDP não filtra qualidade da refutação (não tem opinião)
- Promoção a bloco novo é rara (só substanciais como Einstein refutando Newton)

---

## Decisões fixadas para implementação

### Hierarquia de modelos
- Haiku, Sonnet, Opus por enquanto
- Triagem depois para ver se basta

### Critério "vale gastar?" (custo-benefício)
- Descobrir usando e testando
- 5 heurísticas iniciais (peça 2.3): comprimento, densidade de pergunta, tipo detectado, histórico recente, manual

### Detecção de "fio de conversa" (= bloco)
- Descobrir usando e testando (peça 2.6)
- Heurísticas iniciais: sinal explícito do Pai, ausência de refutações em N turnos, mudança de tema

### "Outros prompts" configuráveis
- Ceticismo é **imutável** (sempre presente)
- Outros prompts default: configuráveis/adicionáveis pelo Pai (peça 2.2+)

---

## Peça 2.0 — Implementação detalhada

### Decisões tomadas
- **(II)** Bloco como entidade separada (não campo do entry)
- **(b) simplificada** Criação automática — um bloco aberto por sessão até fechamento manual
- **(α)** Semânticos antigos ficam sem `block_id` (None)

### Arquivos criados
- `edp/blocks.py` — `Block`, `BlockComment`, `BlockManager`
- Adicionado `FIELD_BLOCK_ID` em `edp/schema_v1.py`
- Modificado `edp/memory.py` — `MemoryStore.__init__` instancia BlockManager, `MemoryStore.add` vincula entry ao bloco aberto

### Persistência
- `sessions/<session>_blocks.json` (write atômico)
- `_safe_load_json` tolera corrupção (peça 0.3.1)

### Comportamento esperado em produção
- Boot mostra "blocos=N" no log
- Primeira mensagem após boot cria primeiro bloco automaticamente
- Todos os entries subsequentes vão para o mesmo bloco
- Bloco só fecha por ação explícita (peça 2.6 vai adicionar automação)
- Entries pré-peça-2 não têm block_id (semânticos do fresh start, etc)

### Testes passados
- T1: schema_v1 tem FIELD_BLOCK_ID
- T2: Block + BlockComment (criação, comentários, fechamento, reabertura, serialização)
- T3: BlockManager criação automática
- T4: Link de entries
- T5: Fechamento manual
- T6: Persistência entre boots
- T7: Integração com MemoryStore
- T8: Regressão API (sem regressão)

---

## Peça 2.1 — Implementação detalhada

### Descoberta importante
O EDP **já tinha** `edp/model_router.py` com roteamento sofisticado (12+ heurísticas, score de profundidade, continuidade, fallback). Integrado no WebSocket. Peça 2.1 ficou REESCOPADA: só adicionar funções que faltavam para a câmara de eco.

### Funções adicionadas a model_router.py
- `escolher_modelos_B(modelo_A, available_models=None)` — dado A, retorna refutadores acima
  - Haiku → [Sonnet, Opus]
  - Sonnet → [Opus]
  - Opus → [] (topo/ápice)
- `forca_camara_detectada(user_message)` — detecta gatilhos manuais explícitos
  - 18+ padrões: "verifica", "tenho dúvida", "refuta", "valida", "com rigor", "câmara", etc.
- `deve_ativar_camara(routing_decision, user_message, history=None)` — decide ativação
  - Override: gatilho manual sempre ativa (com TODOS os refutadores acima)
  - A=Opus: nunca ativa (ápice)
  - A=Haiku + ds>=1: ativa (só Sonnet refuta — mais próximo)
  - A=Sonnet + ds>=3: ativa (Opus refuta)
  - Heurística normal usa só refutador mais próximo (custo controlado)

### Decisões importantes
- Gatilho manual vs heurística: **diferentes** quanto a refutadores. Manual = todos acima (rigor pedido); heurística = só o mais próximo (custo controlado)
- "cheq a fonte" e "valid esse cálculo" (palavras truncadas) NÃO casam — aceitos como falsos negativos (palavras estranhas raras em uso real)
- Peça 2.1 só ENTREGA DECISÕES; peça 2.2 vai EXECUTAR a câmara

### Testes passados (peça 2.1)
- T1: Hierarquia (Haiku → [S,O]; Sonnet → [O]; Opus → [])
- T2: available_models filtra refutadores indisponíveis
- T3: forca_camara_detectada — 11/13 gatilhos positivos detectados
- T4: forca_camara_detectada — 6/6 negativos não falsos
- T5: gatilho manual ativa + traz todos refutadores
- T6: A=Opus nunca ativa (5 sub-checks com ds=0,1,3,5,10)
- T7: A=Haiku com depth_score variável
- T8: A=Sonnet com depth_score variável
- T9: Gatilho manual override
- T10: Integração com route_model real

---

## Próximos passos (após 2.0 validado)

1. Aplicar zip no PC, commitar 2.0
2. Subir servidor — verificar log "blocos=0" no boot, "blocos=1" após primeira mensagem
3. Validar manualmente: entries novos têm `block_id`, arquivo `default_blocks.json` criado
4. Quando confirmado, seguir para **peça 2.1 (roteador de modelos)**

---

## Dívidas técnicas (recapituladas da peça 0)

1. 7 ambíguos da peça 0.2c usam `time.time()` onde `time.monotonic()` seria mais correto
2. Migração lazy dos entries antigos
3. Bug do `gap_before` corrigido
4. Bug do `_get_edp_lifetime` corrigido
5. **CRÍTICA — Autenticação multi-usuário** (futuro)
6. Janela de inconsistência memória/disco entre boot e primeiro save
7. sentence-transformers exige internet em primeira instanciação por processo

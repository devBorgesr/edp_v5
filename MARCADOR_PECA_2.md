# MARCADOR DE ESTADO — Peça 2 do EDP (Presença)

**Última atualização:** 2026-05-24

---

## Sub-passos da peça 2 — Status

- ✅ **2.0** — Infraestrutura de blocos (commit `40c1941`)
- ✅ **2.1** — Roteador de modelos: refutadores + gatilho manual (commit `9841214`)
- ✅ **2.2** — Câmara de eco básica backend (commit `086236b`)
- 🟡 **2.3** — Critério de ativação por auto-sinal (REESCOPADA — aguardando commit)
- ⏳ **2.4** — Transparência tempo real no dashboard (integra 2.2 + 2.3 em produção)
- ⏳ **2.5** — Verificação humana (Camada 2)
- ⏳ **2.6** — Fechamento de bloco (heurísticas automáticas)
- ⏳ **2.7** — Retrieval respeitando blocos *(tensão com 2.8 — pode se fundir)*
- ⏳ **2.8** — **Arquitetura de janela em 4 camadas** *(REESCOPADA — ver seção dedicada)*
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

## Peça 2.2 — Implementação detalhada

### Escopo decidido
- Backend completo da câmara de eco, **sem integração no WebSocket ainda**
- Integração visual em produção fica para peça 2.4 (transparência tempo real)
- Razão: separar lógica de UI; não tocar `websocket.py` (delicado) antes de ter UI rica para mostrar o fluxo

### Arquivos criados/modificados
- **Novo:** `edp/echo_chamber.py` — orquestração completa da câmara
- **Modificado:** `edp/schema_v1.py` — adiciona `FIELD_CAMARA_ID`, 7 checks com pesos, descrições

### Decisões implementadas
- **Streaming desligado durante câmara** (peça 2.2 não usa stream)
- **Os 7 checks separados e estruturados** (PASS/FAIL por check com justificativa)
- **A avalia reformulação vendo os checks que B marcou** (não só os textos)
- **Pesos por gravidade:** 3 (confabulação) / 2 (inflação, condescendência, projeção, perda_de_fio) / 1 (completude_forçada, estruturação_imposta)
- **Score = sum(PASS·peso) − sum(FAIL·peso)**, max=13, min=-13
- **Vencedor:** se A concorda ≥70% com reformulação → B vence; ≥40% → ambos_similar; <40% → A
- **Histórico em arquivo separado:** `sessions/<id>_camara.json` (write atômico)
- **Entry recebe `camara_id`:** aponta para registro completo
- **Degradação graciosa:** se B falha ou parse falha → fallback para A solo, registrado

### Componentes principais
- `_construir_prompt_B(contexto, texto_A, modelo_B)` — prompt estruturado de refutação
- `_construir_prompt_A_avaliar_B(...)` — prompt para A avaliar reformulação
- `_parse_resposta_B(texto)` — extrai checks + reformulação
- `_parse_resposta_A_avaliacao(texto)` — extrai concordância + texto final
- `calcular_score(checks)` — score do texto
- `CamaraRecord` (dataclass) — histórico persistido
- `EchoChamber` — orquestrador com `executar()` síncrono

### Assinatura de uso (futuro)
```python
chamber = EchoChamber(session_id, base_dir, llm_caller)
resultado = chamber.executar(
    user_message=msg,
    contexto_completo=ctx,
    modelo_A="claude-haiku-4-5",
    modelo_B="claude-sonnet-4-6",
    edp_session_id="...",
    block_id="...",
    texto_A_ja_gerado=None,  # opcional, evita chamar A duas vezes
)
# resultado["texto_final"] vai para o usuário
# resultado["camara_id"] vai para entry.camara_id
```

### Testes passados (peça 2.2)
- T1: Schema tem `FIELD_CAMARA_ID`, 7 checks, pesos corretos, max_score=13
- T2: `calcular_score` — tudo PASS / tudo FAIL / misto
- T3: parse de B — estruturado / sem reformulação / texto solto (parse_ok=False)
- T4: parse de avaliação de A — concordância 85% / 100% / texto final
- T5: Fluxo completo bem-sucedido (B vence com concord=90%)
- T6: A já está limpo (B diz "não necessário") — só 2 chamadas, A vence
- T7: Fallback gracioso quando B falha
- T8: Persistência entre boots

---

## Peça 2.3 — Critério de ativação por AUTO-SINAL (REESCOPADA — articulada em 2026-05-24)

### Princípio (articulado pelo usuário)

> "Uma boa forma de ativar a câmara é quando um modelo instruído diz 'essa
> pergunta eu não consigo responder com confiança, seria especulação minha.'
> Então ativa a câmara de eco e um modelo melhor analisa o que o modelo A
> gerou e reformula."

> "A câmara é exclusivamente para validar perguntas e questões difíceis,
> logo não é uma utilidade para pesquisas comuns. Ela é exclusiva."

**Inversão de paradigma:** câmara não é **decidida externamente** pelo EDP
analisando a pergunta. É **acionada internamente** quando o próprio modelo A
admite que está beirando especulação. EDP-Espírito ouve a admissão e
providencia o refinamento.

### Decisões fixadas

- **Sinalização vem da instrução, não de regex externo** — modelo A é
  instruído (na Camada 3 da janela 2.8, parte imutável) a ser honesto e
  transparente sobre dificuldades
- **5 heurísticas combinadas da proposta original DESCARTADAS** —
  substituídas pelo critério "auto-sinal do modelo"
- **Streaming continua normal** — UI mostra "reformulando a resposta"
  quando câmara ativa (implementação visual em peça 2.4)
- **Heurísticas externas viram rede de segurança opcional** — parâmetro
  `usar_heuristica_legada=False` por default

### Arquivos modificados

- `edp/echo_chamber.py`:
  - `CETICISMO_DEFAULT` refinado: agora inclui HONESTIDADE + transparência
    radical sobre limites
  - Frases padronizadas para admissão de limite:
    - "Não consigo responder isso com confiança — seria especulação minha."
    - "Não tenho base sólida para afirmar isso."
    - "Isso está além do que posso afirmar com honestidade."
  - Nova função `detectar_auto_sinal_de_limite(texto)` — retorna
    `{detectado, trecho, confianca}` onde confiança é "alta" (frase padrão)
    ou "media" (variação natural)
- `edp/model_router.py`:
  - `deve_ativar_camara` reescrita com nova prioridade:
    1. Gatilho manual (override absoluto)
    2. Auto-sinal de A → ativa
    3. A=Opus → nunca ativa (sem refutador acima)
    4. Heurística legada (apenas se `usar_heuristica_legada=True`)
  - Novo retorno: campo `via_auto_sinal` (bool) para rastrear como ativou

### Testes passados (peça 2.3) — 11 testes, 39 sub-checks

- T1: CETICISMO_DEFAULT refinado com HONESTIDADE e frases padrão
- T2: detector — 5 frases padrão detectadas como "alta confiança"
- T3: detector — 3 variações naturais detectadas como "média"
- T4: detector — 7 negativos não falsos
- T5: sem auto-sinal e sem manual → não ativa (mesmo com ds=10)
- T6: auto-sinal de Haiku → ativa Sonnet (modelo mais próximo)
- T7: auto-sinal de Sonnet → ativa Opus
- T8: Opus admite limite → registra mas não ativa (sem refutador acima)
- T9: gatilho manual override > auto-sinal (manual ganha)
- T10: heurística legada funciona quando `usar_heuristica_legada=True`
- T11: regressão peça 2.1 (escolher_modelos_B, forca_camara_detectada)

### Fluxo final da câmara (após peça 2.3)

```
1. EDP recebe pergunta do usuário
2. EDP envia para modelo A (Haiku por default, escala se router escolher)
3. A gera resposta seguindo CETICISMO_DEFAULT (ceticismo + honestidade)
4. detectar_auto_sinal_de_limite(resposta_A) → se detectado:
   a. EDP ativa câmara
   b. Modelo B (acima de A) recebe contexto + texto_A + 7 checks
   c. B refuta + reformula
   d. A avalia reformulação de B
   e. Vencedor vai para usuário com tag "reformulado"
5. Senão (sem auto-sinal): resposta de A vai direto para usuário
```

A câmara fica **reativa**, não preditiva. EDP confia no Filho instruído.

---

## Peça 2.8 — Arquitetura de Janela em 4 Camadas (REESCOPADA — articulada em 2026-05-24)

### Princípio (articulado pelo usuário)

> "Janela de contexto é o que permite, antes de responder à pergunta, pensar
> na janela e olhar em pontos de vista diferentes e chegar a uma conclusão
> mais fácil, precisa e confiante da resposta. **Então não é só injetar
> várias informações na janela e esperar que o modelo entenda.**"

A janela é **espaço cognitivo de elaboração**, não armazém. Conteúdo importa
**como estrutura**, não só como volume. Cada camada serve para um tipo
distinto de pensamento.

### As 4 camadas (ordem FIXA, do topo para o final)

| # | Camada | Conteúdo | Permite ao modelo |
|---|---|---|---|
| 1 | **Temporal** | hora atual + timestamps das 4 últimas Q+R | pensar temporalmente, perceber ritmo da conversa |
| 2 | **Histórico relacionado** | 6 Q+R conectadas à pergunta atual (tokenização + embedding) | olhar pontos de vista anteriores sobre o terreno |
| 3 | **Prompts do usuário** | ceticismo (imutável) + outros configuráveis | operar segundo o modo que o Pai definiu |
| 4 | **Técnica de Feynman** | método de 4 etapas, FIXO | estruturar elaboração metodologicamente |

### Dinâmicas

- **Camada 1:** rolante — 4 turnos sempre os mais recentes; hora atualiza a cada turno
- **Camada 2:** dinâmica por pergunta — busca por tokenização + embedding da pergunta atual
- **Camada 3:** persistente — configurada pelo usuário via página dedicada do dashboard (Camada 3 = página separada na UI)
- **Camada 4:** fixa — é o método que mais se aproxima de como o usuário pensa e resolve problemas

### Decisões fixadas

- 4 e 6 (números de turnos das camadas 1 e 2) são **pontos de partida pra testar**, não imutáveis
- **Ordem 1→2→3→4 é imutável**
- Camada 3 ganha **página separada** no dashboard (não é o painel principal)
- Feynman fixo (não configurável)

### Tensão com peça 2.7

A peça 2.7 (retrieval respeitando blocos) **sobrepõe parcialmente** com a Camada 2. Camada 2 já faz retrieval conversacional (turnos Q+R relacionados), o que muda o papel do retrieval clássico. Provavelmente 2.7 e 2.8 se fundem ou 2.7 vira mais simples. Decisão depois.

### Componentes da implementação (escopo grande, vai precisar sub-passos)

- Reescrever `edp/context_builder.py` para construir as 4 camadas
- Implementar retrieval conversacional (Camada 2 — não puro semântico)
- Adicionar injeção temporal automática (Camada 1)
- Página nova no dashboard para gerenciar prompts (Camada 3)
- Persistir prompts configurados pelo usuário (Camada 3)
- Texto fixo da Técnica de Feynman como constante (Camada 4)

### Resolve problemas identificados

- **Modelo dizendo "não tenho acesso a horário"** → Camada 1 resolve
- **Memórias retornadas sem contexto conversacional** → Camada 2 resolve
- **Modelo sem método para elaborar** → Camada 4 resolve
- **Usuário sem controle sobre tom do modelo** → Camada 3 resolve

---

## Dívidas técnicas (recapituladas da peça 0 + descobertas nas peças 2.x)

1. 7 ambíguos da peça 0.2c usam `time.time()` onde `time.monotonic()` seria mais correto
2. Migração lazy dos entries antigos
3. Bug do `gap_before` corrigido
4. Bug do `_get_edp_lifetime` corrigido
5. **CRÍTICA — Autenticação multi-usuário** (futuro)
6. Janela de inconsistência memória/disco entre boot e primeiro save
7. sentence-transformers exige internet em primeira instanciação por processo
8. **`PermissionError [WinError 32]` em `_atomic_write_json` no Windows** —
   ✅ **CORRIGIDA** em 2026-05-24. Defesas em 3 camadas:
   - Lock global por path (`_get_write_lock`) — serializa saves do mesmo arquivo
   - Retry com backoff exponencial (50ms, 100ms, 200ms, 400ms, 800ms) em
     `PermissionError`/`OSError` durante `os.replace`
   - Limpeza de `.tmp` órfão pré-existente antes de cada save
   - 8 testes passados (T1-T8), incluindo simulação de Windows transiente
   - Observação original: 2026-05-24 09:22 BRT, durante uso normal.

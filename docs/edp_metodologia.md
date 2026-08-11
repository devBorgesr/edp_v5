# EDP — Metodologia Fundadora

> Documento vivo. Última atualização: 07/06/2026 (após sprint 06-07/06).
> Sprint 06-07/06/2026 entregou: Commits 4-fix, 5α, 6, 3d (com 4 ciclos de
> bug) + Commit ε (validação cruzada).

> **ERRATA (11/08/2026) — documento histórico, não corrente.** Texto abaixo
> preservado intacto (regra 3, `WIKI_SCHEMA.md` / §4.4 do método,
> `NORTE.md`) — nada foi apagado nem reescrito.
>
> O que mudou desde 07/06 e torna a leitura abaixo desatualizada, ponto a
> ponto:
> - A convenção de nomenclatura de commit por letra grega (Commit 3d, 5α,
>   ε, γ) foi abandonada; o projeto passou a nomear por branch/experimento
>   (`exp0XX`, `hardening/faseN`, `fix/<nome>`).
> - "Mantenedor: Renato (autodidata)" (linha final) não corresponde a
>   nenhum outro documento do repositório, que se referem ao pesquisador
>   como Daniel (`devBorgesr`). Não verificado se é pseudônimo antigo,
>   erro do documento original, ou período anterior distinto — registrado
>   como discrepância, não resolvido aqui.
> - A tabela "Estado Atual do EDP (07/06/2026)" e "Próximos commits
>   planejados" descrevem um sistema anterior ao ciclo de curadoria de
>   24/06/2026 (`RESULTADO_AUDITORIA_EDP_v5.md` — remoção do bloco v3.2,
>   11 arquivos/~5.500 linhas) e a tudo que veio depois (exp008–exp017,
>   hardening fase 1–4, pré-registros de 07–08/2026). Boa parte dos
>   componentes citados como "✅ validados" não existe mais sob esse nome.
> - Os **6 Princípios EDP** e as **4 Dimensões de Investigação Prévia**
>   seguem citados e em uso — `NORTE.md §4.8` refere-se a este documento
>   para o checklist completo das dimensões (embora `NORTE.md` fale em "5
>   dimensões", contando a 5ª — custo de LLM — que este documento não
>   nomeia como tal; outra discrepância registrada, não resolvida).
>
> **A descrição do estado corrente do sistema, com evidência
> re-verificada em 11/08/2026, está em
> [`docs/edp_metodologia_v5.md`](edp_metodologia_v5.md).** Este documento
> fica como registro histórico do método tal como formalizado em 06–07/06.

---

## Por que este documento existe

EDP (Exocórtex Digital Persistente) é construído sob princípios e padrões
específicos. Este documento codifica os princípios, padrões e lições
aprendidas mecanicamente, de forma que:

1. **Quem retomar o projeto em outra sessão** tenha mapa explícito
2. **Decisões arquiteturais futuras** sejam ancoradas em princípios formais
3. **Bugs custosos** (como os 4 ciclos do Commit 3d) não se repitam
4. **Validação empírica permaneça não-negociável** mesmo sob pressão

---

## Os 6 Princípios EDP (formalizados em 04-07/06/2026)

### 1. Base Sólida

> Nenhuma feature avança sem fundação validada.

**Aplicação prática:**
- Antes de feature nova, calibradores existentes precisam estar saudáveis
- Antes de Tier 2, Tier 1 precisa estar 100% verde
- Antes de próximo commit, anterior precisa ter testes passando + uso empírico real
- Antes de distribuir para testers, MVP precisa ter passado por uso pessoal

**Critério binário:** `[testes_passando AND logs_sem_erro AND validação_empírica AND zero_dívida_nova]`

### 2. Soberania Progressiva

> Cada componente é dono do seu estado. Sem dependências circulares.

**Aplicação prática:**
- BackgroundLoop NÃO depende de Memory para iniciar
- BayesCalibrator NÃO depende de Gauss
- Pareto Store NÃO depende de nenhum calibrador
- Cada componente expõe API limpa, esconde estado interno

**Implicação:** quando um componente é substituído (ex: cloud LLM → local LLM),
nenhum outro componente quebra.

### 3. Arquitetura Forward

> Decisões de hoje não fecham portas de amanhã.

**Aplicação prática:**
- `correlation_id` no schema do Pareto (Commit 3b) → habilitou Bayes
  (Commit 5α) **sem migração**
- `cognitive_decisions` como campo no entry (Commit 3d) → permite
  consumidores futuros (Memory Palace, Active Recall) sem mudança de schema
- BackgroundJob com `suspend_on_pressure` flag → preparado para multi-tenant
  futuro sem reescrita
- 6 princípios codificados → base sobrevive a refator técnico

### 4. Solidificação Iterativa

> Commit pequeno, testado, validado. Nunca commit grande sem validação intermediária.

**Aplicação prática:**
- Cada commit valida em produção REAL, não apenas em mocks
- 4 ciclos de bug do Commit 3d (Bug 1 → Fix → Bug 2 → Fix2 → Bug 3 → Fix3 → Bug 4 → Fix4)
  só foram detectados porque cada fix foi validado empiricamente
- Sem essa disciplina, EDP teria gravado decisions nulas em silêncio por meses

**Anti-padrão:** "vou implementar 3 features juntas porque são parecidas" →
proibido. Uma por vez, validada, antes da próxima.

### 5. Reuso de Infraestrutura

> Antes de construir, verificar o que já existe.

**Aplicação prática:**
- BackgroundLoop reusado por CognitiveDecisionsExtractor
- ContradictionFlagger será reusado por Quality Score
- Pareto Store é fonte de dados de Gauss + Bayes + Future Calibrators
- Padrão singleton + threading.Lock reusado em todos os calibradores

**Implicação:** novas features adicionam ~5 linhas em vez de ~100 quando
infra base está pronta.

### 6. Retrieval Adaptativo (formalizado 05/06/2026)

> Sistema aprende com próprio uso. Filtragem por contexto resolve alucinação cross-session.

**Aplicação prática (validada empiricamente em Commit 3c.β-γ):**
- session_marker identifica sessão temporal (gap > 4h)
- Memórias da sessão ativa recebem boost (SESSION_BOOST_FACTOR=1.60)
- Memórias de outras sessões recebem penalty (SESSION_PENALTY_FACTOR=0.85)
- Threshold contextual filtra memórias contaminantes

**Diferencial técnico real:** nenhum sistema comercial de memória pessoal
AI (Notion AI, ChatGPT Memories, Obsidian Smart Connections) resolve
alucinação cross-session.

---

## As 4 Dimensões de Investigação Prévia

> Lição metodológica permanente do Commit 3d (06/06/2026).
> 4 ciclos de bug sucessivos → causa raiz comum: investigação superficial.

**ANTES de qualquer commit que integre com componente existente, investigar
mecanicamente:**

### Dimensão 1 — Interface/Contrato Exato

**Pergunta:** qual é a assinatura real do método/função que vou chamar?

**Como verificar:** abrir o arquivo via `view` ou `grep`, ler a assinatura
completa. NUNCA assumir baseado em padrão genérico.

**Bug evitável típico:** chamar `provider.complete(system=, user=, max_tokens=)`
quando assinatura real é `complete(request: CompletionRequest)`.

### Dimensão 2 — Wrappers Intermediários

**Pergunta:** há camadas entre o que planejo chamar e o que executa?

**Como verificar:** se A chama B, mapear se existe Wrapper(A → A' → B) onde
A' adapta interface.

**Bug evitável típico:** usar `runtime._llm_provider` quando o atributo
correto é `runtime._client: LLMClient` (que internamente acessa provider).

### Dimensão 3 — Modelo de Persistência

**Pergunta:** os dados existem onde pensei? Como/quando são salvos?

**Como verificar:** procurar `def save`, `def flush`, `self._batch_size` no
componente de persistência.

**Bug evitável típico:** marcar `_dirty=True` esperando flush automático,
mas batch flush exige 50 writes acumuladas → extrações lentas nunca
atingem 50 → memória só em RAM, disco fica vazio.

### Dimensão 4 — Instâncias e Ciclo de Vida

**Pergunta:** o componente é singleton? Há risco de instâncias duplicadas?

**Como verificar:** `grep -n "ClassName(" --include="*.py"` para descobrir
quantas instâncias existem no codebase.

**Bug evitável típico:** EDP cria DUAS MemoryStore separadas (registry + runtime).
Modificações em uma não aparecem na outra. Endpoints sobrescrevem mudanças
do background loop.

### Dimensão 5 — Estimativa de Custo Anthropic

**Pergunta:** esse commit chama LLM? Quantas vezes por uso?

**Como verificar:** procurar `provider.complete` ou `client.complete` no
código novo.

**Aplicação:** custo do CognitiveDecisionsExtractor = ~$0.0007 por extração
× 50 entries/dia = ~$0.035/dia. Aceitável.

---

## Pré-Commit Checklist (obrigatório antes de cada Tier 2/3)

```
[ ] 1. Interface concreta confirmada via view/grep (Dimensão 1)
[ ] 2. Wrappers intermediários mapeados (Dimensão 2)
[ ] 3. Modelo de persistência confirmado (Dimensão 3)
[ ] 4. Instâncias verificadas (Dimensão 4)
[ ] 5. Custo Anthropic estimado (Dimensão 5)
[ ] 6. Plano arquitetural com decisões explícitas (D1, D2, ...)
[ ] 7. Padrão de testes definido para esse commit
[ ] 8. Feature flag prevista (para Tier 2/3 em produção)
[ ] 9. Critério de rollback definido
```

**Se qualquer item ficar marcado [ ] e não [✓] → INVESTIGAR antes de codar.**

---

## Padrão de Sprint (ritual operacional)

### Sequência canônica

```
1. Investigação prévia (4-5 dimensões aplicadas)
2. Plano arquitetural com decisões explícitas (D1-D6)
3. Implementação (bloco por bloco, não tudo de uma vez)
4. Testes isolados (lógica pura)
5. Testes E2E (com mocks tipados estritos)
6. Verificação cruzada por grep
7. Empacotamento (present_files para PowerShell)
8. Aplicação em produção real
9. Validação empírica (NÃO mocks)
10. Registro de dívidas técnicas se houver
```

### Critério de avanço entre passos

Não avança para passo N+1 sem passo N completo + verificado.

### Critério de fim de sprint

```
[ ] Testes passando
[ ] Logs sem WARNING/ERROR espúrios
[ ] Funcionalidade testada manualmente em uso real
[ ] Sem dívida técnica nova não registrada
```

---

## Padrão de Testes

### Tier 1 (ε, γ, δ — observação/documentação)
- Nenhum teste novo obrigatório
- Confirmação manual: nada regrediu

### Tier 2 (A, C, B — diferenciais cognitivos)
- 1 teste por método público (mínimo)
- 1 teste por edge case identificado durante design
- 1 teste E2E por fluxo de uso real
- Cobertura mínima: 80% das linhas novas
- Validação empírica: 5+ usos reais

### Tier 3 (α, β, D — interface/observabilidade)
- 3-5 testes isolados
- 2-3 testes E2E
- Teste manual do endpoint/interface

---

## Anti-Padrão de Mock

**Mock genérico que aceita qualquer entrada → DEIXA BUGS PASSAREM.**

Lição do Commit 3d: mock primeiro de provider aceitava `complete(system=, user=, max_tokens=)`
como kwargs. Em produção real, provider recebe `CompletionRequest`. Mock passou,
produção quebrou.

**Mock correto valida tipagem:**

```python
class MockProvider:
    def complete(self, request: CompletionRequest) -> CompletionResponse:
        assert isinstance(request, CompletionRequest), f"Tipo errado: {type(request)}"
        # ... resto do mock
```

**Princípio:** mocks devem ser tão estritos quanto código de produção.

---

## Padrão de Rollback (para Tier 2/3 em produção)

### Feature Flags obrigatórias

```python
import os

# Em cada novo módulo Tier 2/3:
FEATURE_ENABLED = os.environ.get(
    "EDP_FEATURE_<NAME>", "true"
).strip().lower() in ("true", "1", "yes", "on")

if not FEATURE_ENABLED:
    return None  # bypass total
```

### Métricas de regressão monitoradas

- Latência de resposta antes/depois
- Distribuição de verdicts (não pode 90%+ em mesmo verdict)
- Custo Anthropic adicional
- Taxa de falhas de novas extrações/cálculos

### Critério de rollback automático

```
Se latência > +200ms desde introdução → flag false, investigar
Se 90%+ em mesmo verdict → threshold mal calibrado, investigar
Se custo Anthropic >2x baseline → algo chama LLM sem necessidade
```

---

## Estado Atual do EDP (07/06/2026)

### Componentes validados em produção

| Componente | Commit | Estado |
|---|---|---|
| EpisodicMemory + SemanticMemory + WorkingMemory | Base | ✅ |
| Dois Exocórtices (cognitive + sprint) | Commit 1 | ✅ |
| Pareto Store (event logger com correlation_id) | Commit 3b | ✅ |
| Session marker (4h gap) | Commit 3c | ✅ |
| Retrieval adaptativo (filtragem contextual) | Commit 3c.β-γ | ✅ |
| Gauss Calibrator (top_score distribution) | Commit 4 | ✅ |
| Silent failure tracking (Dívida #40) | Commit 4-fix | ✅ |
| Bayes Calibrator (frequência condicional) | Commit 5α | ✅ |
| Background Loop (scheduler async) | Commit 6 | ✅ |
| Cognitive Decisions Extractor (Haiku) | Commit 3d (fix4) | ✅ |
| Validação cruzada estatística | Commit ε | ✅ |
| Metodologia formalizada | Commit γ | ✅ (este doc) |

### Próximos commits planejados (sprint pós-07/06)

| Commit | Item | Tier | Esforço |
|---|---|---|---|
| δ | Polimento de logs | T1 | 30min |
| A | Quality Score composto | T2 | 3-4h |
| C | Cognitive Health Index | T2 | 2-3h |
| B | Lineage Tracking | T2 | 3-4h |
| β | Dashboard widget | T3 | 1-2h |
| α | Endpoint REST | T3 | 1h |
| D | Detecção PII | T3 | 2-3h |

---

## Lições Permanentes (não-negociáveis)

### Lição 1 — Validação empírica > Mocks
Sem produção real, mocks deixam bugs passarem. **Princípio:** "tá pronto"
exige uso real, não apenas testes.

### Lição 2 — Investigar TODA a cadeia, não só pontas
Bugs do Commit 3d (4 ciclos) vieram de pular wrappers intermediários,
modelo de persistência, e ciclo de instâncias. **Princípio:** 4 dimensões
obrigatórias antes de integrar.

### Lição 3 — Observabilidade não-negociável
Logs em DEBUG quando falha = silent failure. **Princípio:** falhas críticas
em WARNING ou ERROR, sempre. Investigar antes de elevar para ERROR.

### Lição 4 — Calibrar ambição ao estado de qualidade
Depois de 4 bugs sucessivos no Commit 3d, próximo commit foi **pequeno + baixo
risco** (ε), não outra feature grande. **Princípio:** Base Sólida aplicado
ao desenvolvedor — energia restante é finita.

### Lição 5 — Separar valor arquitetural de valor de mercado
EDP é marco técnico legítimo. Marco técnico ≠ marco comercial.
**Princípio:** validar com outros usuários antes de inflar narrativa.

### Lição 6 — Honest reporting sobre mistakes
Quando bugs aparecem por culpa minha (investigação superficial), reconhecer
explicitamente, registrar lição, aplicar retroativamente. **Princípio:**
auto-crítica mecânica > rationalização.

---

## Como retomar este projeto em sessão fresca

1. Ler este documento inteiro
2. Rodar `python scripts/validate_state.py` para snapshot do estado
3. Ler `docs/dividas_tecnicas.md` (se existir) para pendências
4. Ler último commit em logs git para contexto da última sprint
5. Aplicar 4 dimensões antes de qualquer código novo

---

## Diferenciais Técnicos do EDP (para narrativa de MVP)

Sistema único — não existe equivalente comercial integrado.

1. **Anti-alucinação cross-session** (Princípio 6) — Notion AI, ChatGPT
   Memories, Obsidian Smart Connections sofrem deste problema. EDP resolve.

2. **Dois Exocórtices** — separação neuroinspirada entre memória longitudinal
   (cognitive) e memória de trabalho (sprint). Único no mercado.

3. **Auto-observação estatística** — Pareto + Gauss + Bayes monitoram o
   próprio sistema cognitivo. Telemetria de produto, não só de sistema.

4. **Cognitive Decisions estruturadas** — cada turno cognitive vira metadado
   acionável (domain + concepts + key_assertion). Base para retrieval refinado.

5. **6 princípios formalizados** — manifesto técnico publicado. Credibilidade
   arquitetural.

6. **Soberania Progressiva** — componentes independentes, sem dependências
   circulares. Permite swap de cloud LLM para local LLM sem reescrita.

---

## Glossário

- **Calibradores**: Pareto + Gauss + Bayes + (futuro) Quality + Health
- **Dois Exocórtices**: cognitive (longitudinal) + sprint (trabalho)
- **EDP Master**: visão arquitetural de longo prazo (multi-tenant, distribuído)
- **EVI**: Evolution Value Index (EDP Master), adaptado como CHI no MVP
- **CHI**: Cognitive Health Index (single-user adaptação do EVI)
- **CompletionRequest**: dataclass usado para invocar LLM provider
- **correlation_id**: identificador único por turno conversacional
- **session_marker**: identificador único por sessão temporal (gap >4h)
- **Trilha A**: EDP atual + MVP + validação externa (foco principal)
- **Trilha B**: EDP Master (segundo plano, longo prazo)

---

**Mantenedor:** Renato (autodidata)
**Início do projeto:** 09/05/2026 aproximadamente
**Filosofia:** dados sobrescrevem palpite, validação empírica não-negociável

## Princípio: turno se identifica por FORMA, nunca por source_type/categoria

**Data:** 16/06/2026 (arco #46/#46b/#46c)

### O princípio
Um turno de conversa deve ser identificado pela sua FORMA (texto que
começa com `Q:` ... `A:`), nunca pela sua categoria/source_type. Categorias
são atribuídas por classificadores que erram; a forma é estrutural e não
mente. Qualquer seleção de "o que é conversa" que filtre por source_type
está sujeita a (1) incluir/excluir a sessão errada e (2) descartar turnos
reais que foram mal classificados.

### O recibo histórico (por que este princípio existe)
Este padrão de bug já tinha acontecido e sido corrigido antes:

- **Commit 3c.α-fix2 (04/06/2026)** — no `_build_historico_cronologico_compacto`,
  o filtro exigia `source_type=user_input`, o que excluía TODAS as conversas
  reais do histórico. Corrigido na época adotando seleção por forma.

- **Reincidência no ζ (13/06/2026)** — o código da janela imediata (#46c)
  cometeu o MESMO erro: selecionava `turnos_conv` por
  `source_type in {llm_response, camara_response}`, o que excluía turnos
  técnicos rotulados `meta_conversation` por engano (ex: o turno do
  algoritmo de Luhn) e misturava sessões.

A lição de 04/06 não persistiu para o código de 13/06. O EDP falhou na
própria continuidade epistêmica que ele existe para dar ao usuário — a
correção de um mês antes não foi "lembrada" pelo desenvolvimento seguinte.

### A correção (#46c)
- Seleção de turno por **form-check**: regex `^\s*Q:\s*.+\bA:\s*` (DOTALL).
  Verificado por grep que nenhum gerador não-conversacional emite texto
  começando com `Q:` (session_summary→`[session_summary]`, cog_dec→JSON),
  logo o form-check é seguro.
- Detecção de sessão por `_detect_sessao_atual(entries, now_ts)` (gap de 4h),
  a MESMA função que o histórico cronológico já usava corretamente.

### Regra de revisão derivada
Em qualquer código futuro que selecione, filtre ou classifique "o que é uma
conversa/turno", a revisão DEVE perguntar: isto está filtrando por forma ou
por categoria? Se for por categoria (source_type), é suspeito de reincidência
do 3c.α-fix2 — exigir justificativa explícita ou converter para form-check.
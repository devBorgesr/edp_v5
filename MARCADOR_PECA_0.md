# MARCADOR DE ESTADO — Desvio para Peça 0.3.1 (Write atômico)

**Criado em:** 2026-05-22 (sessão de implementação da peça 0)
**Motivo:** Bug pré-existente de JSON corrompido descoberto durante validação da peça 0.3 em produção.

---

## Onde estávamos quando o desvio aconteceu

**Sub-passos da peça 0 completados:**

- ✅ **0.1** — `edp/clock.py` (commit `e03f661`)
- ✅ **0.2a** — 8 arquivos espinha dorsal (commit `74dd030`)
- ✅ **0.2b** — 16 arquivos cognitivos + fix docstring types.py (commit `070ea53`)
- ✅ **0.2c** — 7 ambíguos classificados como sistema, sem mudança
- ✅ **0.3** — schema v1 + `edp/schema_v1.py` (commit `b2f005a`)

**Desvio: 0.3.1** — Write atômico em `EpisodicMemory.save()` para prevenir JSON corrompido em interrupções.

**Sub-passos restantes da peça 0 (após 0.3.1):**

- ⏳ **0.4** — Detecção de sessões/contextos
  - Preencher `t_user_session_start` e `t_user_turn_n`
  - Preencher `t_model_context_start` e `t_model_turn_n`
  - Sinais a definir: o que marca início de sessão? O que marca novo contexto do modelo?
  
- ⏳ **0.5** — Migração dos 200 entries existentes
  - Virar bloco-referência único `historico_pre_relogio_interno_v1`
  - Sem preservar timestamps individuais (relógio errado)
  - Script único de migração
  
- ⏳ **0.6** — Validação peça 0 completa em produção
  - Confirmar funcionamento integrado
  - Estabilidade > 24h sem regressão

---

## Próximas peças (após peça 0 completa)

- **Peça 2** — Presença (humano + modelo, predição do início ao fim)
  - Detecta gaps não-classificáveis
  - Interrompe conversa pedindo classificação
  - Expansão de vocabulário com aprovação consciente do usuário
  
- **Peça 3** — Servir dois indivíduos (infraestrutura simétrica)

- **Peça 4** — Informação processada como ouro
  - Mecanismo de import de PDF/TXT (schema já existe em peça 0.3)
  
- **Peça 5** — Verificação dupla quadrática: peso = (V_usuário × V_modelo)²

- **Peça 6** — Ceticismo como default
  - Evita condescendência tipo 1 (superioridade) e tipo 2 (complacência)

---

## Hipótese arquitetural privada (NÃO no VISAO_EDP.md por decisão do usuário)

EDP como "memória paradoxal": passado preciso + assimilação no presente pelas duas inteligências divergentes interagindo = possibilidade de previsão futura probabilística. **Validável só construindo e observando.**

Atemporalidade em 2 camadas:
- (i) técnica: armazenamento/retrieval sem perda
- (ii) fenomenológica: relevância subjetiva ≠ cronológica

"Modelo+EDP atemporal continuamente, mas mantendo linearidade do presente."

---

## Dívidas técnicas registradas

1. **Categoria 2 (7 ambíguos da 0.2c):** alguns usam `time.time()` onde `time.monotonic()` seria mais correto. Trabalho separado, baixa prioridade.

2. **Migração lazy dos 200 entries antigos:** funciona via `.get()` com defaults, mas a peça 0.5 vai resolver definitivamente.

3. **Bug encontrado e corrigido durante 0.3:** primeira tentativa colocou `gap_before` em `EpisodicMemory.add` (que copia entry). Movido para `MemoryStore.add` antes de chamar episodic.

---

## Estado dos dados

- `C:\edp_data\sessions\default_episodic.json` — 200 entries
- `C:\edp_data\sessions\default_semantic.json` — 78 conceitos
- `C:\edp_data\co_occurrence\default_co_occurrence.json` — 94 pares
- Timestamps dos 200 entries antigos: possivelmente corrompidos pelo relógio errado do PC do usuário (problema pré-peça 0)

---

## Princípio operacional sustentado

Sequencial puro: bloco por bloco, testado, validado em produção, confirmado, depois próximo. Sem pular.

Peça 0.4 retoma quando: 0.3.1 commitado + validado em produção pelo usuário + confirmação explícita.

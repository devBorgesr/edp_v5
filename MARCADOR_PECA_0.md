# MARCADOR DE ESTADO — Peça 0 do EDP

**Última atualização:** 2026-05-22

---

## Sub-passos da peça 0 — Status

- ✅ **0.1** — `edp/clock.py` (commit `e03f661`)
- ✅ **0.2a** — 8 arquivos espinha dorsal (commit `74dd030`)
- ✅ **0.2b** — 16 arquivos cognitivos + fix docstring types.py (commit `070ea53`)
- ✅ **0.2c** — 7 ambíguos classificados como sistema, sem mudança
- ✅ **0.3** — schema v1 + `edp/schema_v1.py` (commit `b2f005a`)
- ✅ **0.3.1** — write atômico + load tolerante (aguardando commit ou aplicado)
- 🟡 **0.4 (reduzida)** — só `edp_session_id` adicionado ao schema; demais campos vivenciais ficam None
- ⏳ **0.5** — Migração dos 200 entries existentes → bloco-referência `historico_pre_relogio_interno_v1`
- ⏳ **0.6** — Validação peça 0 completa em produção

---

## Decisões da peça 0.4 que foram POSTERGADAS

### Sessão do usuário — postergada para depois da autenticação

**Razão:** EDP no futuro será multi-usuário. Cada usuário acumulará um **dossiê extraordinariamente completo** sobre si mesmo. Login/logout robusto não pode ser feito como proxy de WebSocket — exige autenticação real (senha/token/2FA), gerenciamento de sessão, expiração, proteção contra hijacking.

**Trabalho que isso vai exigir (FUTURO):**
- Sistema de autenticação robusto (multi-fator desejável)
- Identidade persistente por usuário
- Isolamento de dados entre usuários
- Backup/recovery seguro
- Proteção contra acesso indevido (o dossiê é valioso e sensível)

**Estado atual:** Operação single-user (apenas o usuário criador). Campos `t_user_session_start`, `t_user_turn_n` permanecem `None` no schema v1 até o sistema de autenticação ser construído.

### Sessão/contexto do modelo — postergada para peça 2

**Razão:** "Auto-presença do modelo" não é flag técnico. É **modo de operar** baseado em escuta ativa (ver `VISAO_PECA_2.md`). Implementar como esqueleto técnico em 0.4 seria reduzir o conceito ao óbvio errado.

**Estado atual:** Campos `t_model_context_start`, `t_model_turn_n` permanecem `None` no schema v1 até peça 2.

---

## Três perspectivas de sessão (registro arquitetural)

Articulação do usuário em 22/05/2026:

**(1) Sessão do EDP** — vitalícia
- Começa: primeiro entry gravado (ignição)
- Termina: morte do usuário
- Múltiplas sessões? Não. Uma única, contínua.
- **Esta é a única que peça 0.4 implementa agora.**

**(2) Sessão do usuário** — múltiplas, por decisão consciente
- Começa: usuário decide abrir o EDP e escrever
- Termina: usuário desloga conscientemente
- Volta: quando quiser
- Implementação: postergada (requer autenticação)

**(3) Sessão do modelo** — múltiplas, por ativação de presença
- Começa: modelo ativa função "auto-presença" na conversa com EDP+usuário
- Termina: função de presença é desligada
- Implementação: peça 2

---

## Sub-passos restantes da peça 0

### 0.4 reduzida (próximo a implementar)
- Gerar `edp_session_id` único na vida do EDP (persistido em `sessions/edp_lifetime.json`)
- Adicionar `edp_session_id` e `edp_session_start` ao schema v1
- Todo entry novo carrega esses campos
- **Demais campos vivenciais (`t_user_*`, `t_model_*`) ficam `None`**

### 0.5 — Migração dos 200 entries
- Virar bloco-referência único `historico_pre_relogio_interno_v1`
- `origin = "reference"`, `reference_source = "historico_pre_relogio_interno_v1"`
- Timestamps individuais NÃO são preservados (relógio errado durante a coleta)
- Script único de migração, idempotente, com backup antes
- Após migração: 200 entries antigos viram bloco único; novos entries seguem schema v1 normal

### 0.6 — Validação peça 0 completa em produção
- Servidor estável > 24h sem regressão
- Schema v1 sendo gravado corretamente em entries novos
- Bloco-referência acessível via retrieval
- `edp.now()` funcionando como única fonte de verdade temporal
- Write atômico exercitado em produção

---

## Próximas peças (após peça 0 completa)

- **Peça 2** — Presença (humano + modelo) — ver `VISAO_PECA_2.md`
- **Peça 3** — Servir dois indivíduos (infraestrutura simétrica)
- **Peça 4** — Informação processada como ouro (inclui mecanismo de import PDF/TXT)
- **Peça 5** — Verificação dupla quadrática: peso = (V_usuário × V_modelo)²
- **Peça 6** — Ceticismo como default (evita condescendência tipo 1 e tipo 2)

---

## Hipótese arquitetural privada (NÃO no VISAO_EDP.md)

EDP como "memória paradoxal":
- Passado preciso + assimilação no presente
- Duas inteligências divergentes interagindo
- = possibilidade de previsão futura probabilística

Atemporalidade em camadas:
- (i) técnica: armazenamento/retrieval sem perda
- (ii) fenomenológica: relevância subjetiva ≠ cronológica

"Modelo+EDP atemporal continuamente, mas mantendo linearidade do presente."

Validável só construindo e observando.

---

## Dívidas técnicas registradas

1. **Categoria 2 (7 ambíguos da 0.2c):** alguns usam `time.time()` onde `time.monotonic()` seria mais correto. Trabalho separado, baixa prioridade.

2. **Migração lazy dos 200 entries antigos:** funciona via `.get()` com defaults; peça 0.5 resolve definitivamente.

3. **Bug encontrado e corrigido durante 0.3:** primeira tentativa colocou `gap_before` em `EpisodicMemory.add` (que copia entry). Movido para `MemoryStore.add` antes de chamar episodic.

4. **Bug encontrado em 0.3.1:** JSON corrompido por write parcial em interrupção. Resolvido com write atômico (tmp + fsync + rename) + load tolerante.

5. **CRÍTICA — Autenticação multi-usuário:** atualmente single-user, sem autenticação. Quando for compartilhar EDP com outros usuários, é trabalho de prioridade máxima.

6. **Janela de inconsistência memória/disco entre boot e primeiro save:**
   Quando `_safe_load_json` recupera JSON corrompido no boot, o arquivo no
   disco continua com o lixo até o primeiro batch save acontecer (que pode
   demorar dependendo do uso). Nessa janela, se o servidor crashar, adições
   em memória se perdem e o disco volta a ser fonte de verdade com lixo.

   Mitigação possível (não urgente): forçar flush imediato após boot
   quando `_safe_load_json` detectar recuperação.

   Observado em produção: 2026-05-22 ~19:46 BRT, durante validação Check 6.
   Reproduzível: usuário adicionou lixo manual no JSON com Add-Content,
   servidor subiu, `_safe_load_json` recuperou 4 entries em memória, mas
   o disco continuou com o lixo até o primeiro save subsequente (forçado
   por mensagem nova no chat, ~17 minutos depois). Não bloqueia uso normal.

---

## Princípio operacional sustentado

Sequencial puro: bloco por bloco, testado, validado em produção, confirmado, depois próximo. Sem pular.

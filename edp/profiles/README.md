# edp.profiles — Gerenciamento de perfis (Fase 1)

Módulo administrativo e 100% offline para o operador humano organizar
múltiplos perfis de acesso a serviços externos (ex: credenciais/API keys da
própria organização) e decidir qual usar em seguida, balanceando uso
acumulado. **Não há automação nem integração com nenhum serviço externo** —
o EDP nunca age em nome do operador, só registra o que ele reporta.

Não guarda senhas nem credenciais — apenas id, nome, status e contadores.

## Instalação / ativação

As 4 tools só entram no `ToolRegistry` do EDP quando o módulo é importado
explicitamente:

```python
import edp.profiles.tools
```

Persistência: JSON em `EDP_PROFILES_DB` (default:
`<EDP_BASE_DIR>/profiles/profiles.json`). Escrita atômica — seguro sob uso
concorrente por múltiplos agentes.

## Cadastro inicial (seed)

Cadastre os 3 perfis de exemplo (fictícios) a partir do YAML:

```python
from edp.profiles import get_registry

get_registry().load_seed_yaml("edp/profiles/config/profiles.example.yaml")
```

Edite `edp/profiles/config/profiles.example.yaml` (ou crie o seu) para
refletir seus perfis reais antes do seed — o arquivo do repo é só exemplo.

## As 4 tools

| Tool | Tipo | O que faz |
|---|---|---|
| `list_profiles()` | leitura | Lista todos os perfis com status e contadores. |
| `select_profile(strategy="balanced")` | leitura | Recomenda o perfil ativo com menor uso acumulado. Não faz nada além de recomendar. |
| `log_usage(profile_id, success=True)` | escrita | Incrementa os contadores diário/semanal e marca a data de último uso. |
| `set_profile_status(profile_id, status)` | escrita | Muda status para `"ativo"` ou `"pausado"`. |

Tools de escrita exigem `allow_writes=True` em `registry.execute(...)`, como
qualquer tool de escrita do EDP.

## Fluxo esperado do operador

1. Pergunte `select_profile()` → recebe uma recomendação (ex: `perfil_B`).
2. Use esse perfil manualmente fora do EDP (copiar/colar credencial, etc).
3. Depois do uso, **sempre** chame `log_usage("perfil_B", success=True)` —
   o contador só muda quando você chama isso. Se o EDP nunca é avisado, o
   perfil nunca aparece como "usado".
4. Se um perfil ficar indisponível (ex: quota estourada, revogado), chame
   `set_profile_status("perfil_B", "pausado")` — ele some das recomendações
   até você reativá-lo.
5. Periodicamente (diário/semanal, fora do EDP — ex: cron do operador),
   zere os contadores:

   ```python
   from edp.profiles import get_registry, UsageTracker

   tracker = UsageTracker(get_registry())
   tracker.reset_daily()   # rodar 1x/dia
   tracker.reset_weekly()  # rodar 1x/semana
   ```

## Critério de seleção (`strategy="balanced"`, default)

Entre os perfis com `status="ativo"`, escolhe nesta ordem de desempate:

1. menor `contador_uso_semanal`;
2. menor `contador_uso_diario`;
3. `data_ultimo_uso` mais antiga (nunca usado vence).

`strategy="least_recent"` ignora os contadores e olha só a data do último
uso.

## Logs estruturados

Toda operação (`profile_added`, `profile_status_changed`, `usage_logged`,
`counters_reset`, `profile_selected`) é logada via
`edp.observability.get_logger`, com `EDP_LOG_JSON=1` saindo como JSON.

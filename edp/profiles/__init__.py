"""
edp.profiles — Gerenciamento administrativo de perfis de acesso a serviços
externos (ex: múltiplas API keys/credenciais da própria organização).

100% offline: não há chamada a nenhum serviço externo. O módulo apenas
guarda nome/status/contadores que o operador humano atualiza manualmente
após usar um perfil por fora do EDP, e recomenda qual usar em seguida com
base em uso acumulado. Nenhuma credencial é armazenada.

Uso:
    from edp.profiles import get_registry, ProfileSelector, UsageTracker

    registry = get_registry()
    profile = ProfileSelector(registry).select(strategy="balanced")
    ...  # operador usa o perfil fora do EDP
    UsageTracker(registry).log_usage(profile.id, success=True)

Para registrar as tools (list_profiles, select_profile, log_usage,
set_profile_status) no ToolRegistry do EDP:

    import edp.profiles.tools  # registra como efeito de importar
"""
from .models import Profile, ProfileStatus
from .registry import ProfileRegistry, get_registry
from .selector import STRATEGIES, ProfileSelector
from .tracker import UsageTracker

__all__ = [
    "Profile", "ProfileStatus",
    "ProfileRegistry", "get_registry",
    "ProfileSelector", "STRATEGIES",
    "UsageTracker",
]

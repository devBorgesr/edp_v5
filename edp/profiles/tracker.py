"""
edp.profiles.tracker — Atualização manual de contadores de uso.

Os contadores só mudam quando o operador chama `log_usage()` explicitamente
depois de usar um perfil fora do EDP — nada aqui é automático ou observa
tráfego real.
"""
from __future__ import annotations

from .models import Profile
from .registry import ProfileRegistry


class UsageTracker:
    """Incrementa/zera contadores de uso de perfis via chamada manual."""

    def __init__(self, registry: ProfileRegistry) -> None:
        self._registry = registry

    def log_usage(self, profile_id: str, success: bool = True) -> Profile:
        """
        Registra um uso manual do perfil `profile_id`.

        Incrementa contador_uso_diario e contador_uso_semanal em 1 e atualiza
        data_ultimo_uso para agora (UTC). `success` é apenas informativo — vai
        para o log estruturado — e não impede o incremento: o uso já
        aconteceu independentemente do resultado.

        Levanta KeyError se o perfil não existir.
        """
        return self._registry.increment_usage(profile_id, success=success)

    def reset_daily(self) -> int:
        """Zera contador_uso_diario de todos os perfis. Retorna quantos mudaram."""
        return self._registry.reset_counters("diario")

    def reset_weekly(self) -> int:
        """Zera contador_uso_semanal de todos os perfis. Retorna quantos mudaram."""
        return self._registry.reset_counters("semanal")

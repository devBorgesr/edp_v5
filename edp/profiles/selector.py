"""
edp.profiles.selector — Recomendação do próximo perfil a usar.

Critério puramente administrativo (balanceamento de custo/quota entre
credenciais da própria organização): entre os perfis com status=ativo,
prefere o menos usado na semana, depois no dia, depois o há mais tempo sem
uso. A decisão final de uso é sempre do operador humano.
"""
from __future__ import annotations

from typing import List, Optional

from edp.observability import get_logger

from .models import Profile, ProfileStatus
from .registry import ProfileRegistry

logger = get_logger("edp.profiles.selector")

STRATEGIES = ("balanced", "least_recent")


class ProfileSelector:
    """Seleciona o perfil ativo mais adequado a partir do ProfileRegistry."""

    def __init__(self, registry: ProfileRegistry) -> None:
        self._registry = registry

    def select(self, strategy: str = "balanced") -> Optional[Profile]:
        """
        Retorna o perfil recomendado, ou None se não houver nenhum ativo.

        Estratégias:
            "balanced":     ordena por (contador_uso_semanal,
                             contador_uso_diario, data_ultimo_uso) — menor
                             uso acumulado primeiro; perfis nunca usados
                             (data_ultimo_uso=None) vêm antes de qualquer
                             data.
            "least_recent": ordena só por data_ultimo_uso.

        Levanta ValueError se `strategy` for desconhecida.
        """
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy '{strategy}' desconhecida. Use uma de: {STRATEGIES}")

        candidates = [p for p in self._registry.list() if p.status == ProfileStatus.ATIVO]
        if not candidates:
            logger.info(
                "profile_selected",
                extra={"event": "profile_selected", "strategy": strategy, "profile_id": None},
            )
            return None

        chosen = min(candidates, key=self._sort_key(strategy))
        logger.info(
            "profile_selected",
            extra={"event": "profile_selected", "strategy": strategy, "profile_id": chosen.id},
        )
        return chosen

    @staticmethod
    def _sort_key(strategy: str):
        def balanced(p: Profile):
            return (p.contador_uso_semanal, p.contador_uso_diario, p.data_ultimo_uso or "")

        def least_recent(p: Profile):
            return p.data_ultimo_uso or ""

        return balanced if strategy == "balanced" else least_recent

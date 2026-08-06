"""
edp.profiles.tools — Tools de gerenciamento de perfis registradas no
ToolRegistry do EDP (edp.tools).

Importar este módulo registra as 4 tools abaixo. Elas não são carregadas
automaticamente pelo pacote edp.tools (diferente dos builtins read-only) —
o operador ativa explicitamente com `import edp.profiles.tools`.

Tools:
    list_profiles()                      — readonly, lista resumida
    select_profile(strategy="balanced")  — readonly, recomendação
    log_usage(profile_id, success=True)  — write, incrementa contadores
    set_profile_status(profile_id, status) — write, muda ativo/pausado
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from edp.tools import ToolDefinition, ToolParameter, register

from .models import Profile, ProfileStatus
from .registry import get_registry
from .selector import STRATEGIES, ProfileSelector
from .tracker import UsageTracker


def _summarize(p: Profile) -> Dict[str, Any]:
    return {
        "id": p.id,
        "nome": p.nome,
        "status": ProfileStatus(p.status).value,
        "data_ultimo_uso": p.data_ultimo_uso,
        "contador_uso_diario": p.contador_uso_diario,
        "contador_uso_semanal": p.contador_uso_semanal,
    }


def _list_profiles() -> Dict[str, Any]:
    profiles = get_registry().list()
    return {"profiles": [_summarize(p) for p in profiles], "count": len(profiles)}


def _select_profile(strategy: str = "balanced") -> Dict[str, Any]:
    chosen = ProfileSelector(get_registry()).select(strategy=strategy)
    if chosen is None:
        return {"profile": None, "reason": "nenhum perfil com status='ativo'"}
    return {"profile": _summarize(chosen)}


def _log_usage(profile_id: str, success: bool = True) -> Dict[str, Any]:
    profile = UsageTracker(get_registry()).log_usage(profile_id, success=success)
    return {"profile": _summarize(profile)}


def _set_profile_status(profile_id: str, status: str) -> Dict[str, Any]:
    parsed = ProfileStatus(status.lower())
    profile = get_registry().set_status(profile_id, parsed)
    return {"profile": _summarize(profile)}


register(ToolDefinition(
    name="list_profiles",
    description=(
        "Lista todos os perfis administrativos cadastrados (id, nome, status, "
        "último uso, contadores diário/semanal). Não expõe credenciais."
    ),
    parameters=[],
    handler=_list_profiles,
    readonly=True,
))

register(ToolDefinition(
    name="select_profile",
    description=(
        "Recomenda qual perfil ativo usar agora, priorizando o menos usado "
        "(semanal, depois diário, depois há mais tempo sem uso). Não executa "
        "nenhuma ação externa — apenas devolve a recomendação para o operador."
    ),
    parameters=[
        ToolParameter(
            "strategy", "string",
            f"Critério de seleção. Uma de: {STRATEGIES}. Default: balanced.",
            required=False, enum=list(STRATEGIES),
        ),
    ],
    handler=_select_profile,
    readonly=True,
))

register(ToolDefinition(
    name="log_usage",
    description=(
        "Registra manualmente que o operador usou um perfil: incrementa os "
        "contadores diário/semanal e atualiza a data de último uso. Chamar "
        "SEMPRE depois do uso real do perfil fora do EDP, nunca antes."
    ),
    parameters=[
        ToolParameter("profile_id", "string", "Id do perfil usado"),
        ToolParameter("success", "boolean", "Se o uso externo foi bem-sucedido (informativo)", required=False),
    ],
    handler=_log_usage,
    readonly=False,
))

register(ToolDefinition(
    name="set_profile_status",
    description="Altera o status de um perfil para 'ativo' ou 'pausado'.",
    parameters=[
        ToolParameter("profile_id", "string", "Id do perfil"),
        ToolParameter("status", "string", "Novo status", enum=[s.value for s in ProfileStatus]),
    ],
    handler=_set_profile_status,
    readonly=False,
))

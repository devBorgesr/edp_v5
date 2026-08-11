"""
edp.profiles.models — Tipos de dados de perfis de acesso a serviços externos.

Um Profile é um registro puramente administrativo: nome, status e contadores
de uso que o operador humano atualiza manualmente após usar o perfil fora do
EDP. O módulo não guarda credenciais nem interage com nenhum serviço externo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ProfileStatus(str, Enum):
    """Status manual de um perfil, definido apenas pelo operador."""

    ATIVO = "ativo"
    PAUSADO = "pausado"


@dataclass
class Profile:
    """
    Perfil de acesso a um serviço externo (ex: uma credencial/API key da
    organização), com contadores de uso preenchidos manualmente pelo operador.

    Attributes:
        id: identificador único e estável do perfil (ex: "perfil_A").
        nome: nome legível do perfil.
        status: ATIVO (elegível para seleção) ou PAUSADO (ignorado pelo
            ProfileSelector).
        data_ultimo_uso: timestamp ISO-8601 UTC do último `log_usage()`, ou
            None se o perfil nunca foi usado.
        contador_uso_diario: quantidade de usos registrados no período diário
            corrente. Zerado por `UsageTracker.reset_daily()`.
        contador_uso_semanal: quantidade de usos registrados no período
            semanal corrente. Zerado por `UsageTracker.reset_weekly()`.
        observacoes: texto livre do operador (ex: motivo de uma pausa).
    """

    id: str
    nome: str
    status: ProfileStatus = ProfileStatus.ATIVO
    data_ultimo_uso: Optional[str] = None
    contador_uso_diario: int = 0
    contador_uso_semanal: int = 0
    observacoes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serializa para um dict JSON-safe (status vira string)."""
        return {
            "id": self.id,
            "nome": self.nome,
            "status": ProfileStatus(self.status).value,
            "data_ultimo_uso": self.data_ultimo_uso,
            "contador_uso_diario": self.contador_uso_diario,
            "contador_uso_semanal": self.contador_uso_semanal,
            "observacoes": self.observacoes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Profile":
        """Desserializa a partir de um dict (JSON ou YAML já parseado)."""
        return cls(
            id=str(data["id"]),
            nome=str(data.get("nome", data["id"])),
            status=ProfileStatus(data.get("status", ProfileStatus.ATIVO.value)),
            data_ultimo_uso=data.get("data_ultimo_uso"),
            contador_uso_diario=int(data.get("contador_uso_diario", 0)),
            contador_uso_semanal=int(data.get("contador_uso_semanal", 0)),
            observacoes=str(data.get("observacoes", "")),
        )

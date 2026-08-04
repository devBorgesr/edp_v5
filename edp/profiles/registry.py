"""
edp.profiles.registry — Registro persistente e thread-safe de perfis.

Persistência em JSON, com write atômico (tmp → fsync → rename) e load
tolerante a corrupção, reaproveitando `edp.memory.atomic_io` — o mesmo
mecanismo já usado pelo MemoryStore. Nenhuma credencial é armazenada; apenas
metadados administrativos (nome, status, contadores, timestamps).
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from edp import config as edp_config
from edp.memory.atomic_io import _atomic_write_json, _load_json_or_quarantine
from edp.observability import get_logger

from .models import Profile, ProfileStatus

logger = get_logger("edp.profiles.registry")

DEFAULT_PROFILES_PATH = Path(
    os.environ.get("EDP_PROFILES_DB", str(Path(edp_config.BASE_DIR) / "profiles" / "profiles.json"))
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProfileRegistry:
    """
    Armazena perfis em memória e persiste em disco a cada mutação.

    Thread-safe: todas as operações de leitura/escrita são protegidas por um
    `threading.RLock`, permitindo uso por múltiplos agentes concorrentes — a
    decisão de qual perfil usar continua sendo do operador humano.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_PROFILES_PATH
        self._lock = threading.RLock()
        self._profiles: Dict[str, Profile] = {}
        self._load()

    # ── Persistência ──────────────────────────────────────────────────────

    def _load(self) -> None:
        # Dívida #53 (docs/preregistro_fix_corrupcao_json.md): antes,
        # JSON truncado no meio derrubava a construção inteira do
        # ProfileRegistry (sem try/except ao redor de _safe_load_json).
        # _load_json_or_quarantine nunca crasha e nunca perde o dado bruto
        # (quarentena + logger.critical + evento Pareto "store_degraded")
        # — ver EpisodicMemory._load (edp/memory/store.py) para o desenho
        # completo, migrado primeiro.
        with self._lock:
            if not self._path.exists():
                self._profiles = {}
                return
            data = _load_json_or_quarantine(self._path, store_label="profiles_registry") or {}
            self._profiles = {
                pid: Profile.from_dict(pdata) for pid, pdata in data.get("profiles", {}).items()
            }

    def _save(self) -> None:
        with self._lock:
            payload = {"profiles": {pid: p.to_dict() for pid, p in self._profiles.items()}}
            _atomic_write_json(self._path, payload)

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add(self, profile: Profile) -> Profile:
        """Cadastra um novo perfil. Levanta ValueError se o id já existir."""
        with self._lock:
            if profile.id in self._profiles:
                raise ValueError(f"perfil '{profile.id}' já cadastrado")
            self._profiles[profile.id] = profile
            self._save()
        logger.info(
            "profile_added",
            extra={"event": "profile_added", "profile_id": profile.id, "nome": profile.nome},
        )
        return profile

    def get(self, profile_id: str) -> Optional[Profile]:
        with self._lock:
            return self._profiles.get(profile_id)

    def list(self) -> List[Profile]:
        with self._lock:
            return list(self._profiles.values())

    def remove(self, profile_id: str) -> None:
        with self._lock:
            self._profiles.pop(profile_id, None)
            self._save()
        logger.info("profile_removed", extra={"event": "profile_removed", "profile_id": profile_id})

    # ── Mutações atômicas usadas por UsageTracker/tools ─────────────────────

    def set_status(self, profile_id: str, status: ProfileStatus) -> Profile:
        """Altera o status de um perfil. Levanta KeyError se não existir."""
        with self._lock:
            profile = self._require(profile_id)
            status_old = profile.status
            profile.status = status
            self._save()
        logger.info(
            "profile_status_changed",
            extra={
                "event": "profile_status_changed",
                "profile_id": profile_id,
                "status_old": ProfileStatus(status_old).value,
                "status_new": ProfileStatus(status).value,
            },
        )
        return profile

    def increment_usage(self, profile_id: str, success: bool = True) -> Profile:
        """
        Incrementa os contadores diário e semanal e atualiza data_ultimo_uso.

        Chamado apenas por `UsageTracker.log_usage()`, uma única vez por
        operação — atômico sob o lock do registro para evitar corrida entre
        agentes concorrentes.
        """
        with self._lock:
            profile = self._require(profile_id)
            profile.contador_uso_diario += 1
            profile.contador_uso_semanal += 1
            profile.data_ultimo_uso = _now_iso()
            self._save()
        logger.info(
            "usage_logged",
            extra={
                "event": "usage_logged",
                "profile_id": profile_id,
                "success": success,
                "contador_uso_diario": profile.contador_uso_diario,
                "contador_uso_semanal": profile.contador_uso_semanal,
            },
        )
        return profile

    def reset_counters(self, scope: str) -> int:
        """Zera contador_uso_diario ou contador_uso_semanal de todos os perfis."""
        if scope not in ("diario", "semanal"):
            raise ValueError("scope deve ser 'diario' ou 'semanal'")
        attr = "contador_uso_diario" if scope == "diario" else "contador_uso_semanal"
        with self._lock:
            n = 0
            for profile in self._profiles.values():
                if getattr(profile, attr) != 0:
                    setattr(profile, attr, 0)
                    n += 1
            self._save()
        logger.info("counters_reset", extra={"event": "counters_reset", "scope": scope, "count": n})
        return n

    def _require(self, profile_id: str) -> Profile:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise KeyError(f"perfil '{profile_id}' não encontrado")
        return profile

    # ── Seed a partir de YAML ────────────────────────────────────────────

    def load_seed_yaml(self, path: Path, skip_existing: bool = True) -> int:
        """
        Cadastra perfis a partir de um YAML de exemplo (ver
        edp/profiles/config/profiles.example.yaml).

        Args:
            path: caminho do YAML com uma chave top-level `profiles: [...]`.
            skip_existing: se True, ignora silenciosamente ids já cadastrados
                em vez de levantar ValueError.

        Returns:
            número de perfis efetivamente adicionados.
        """
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        added = 0
        for pdata in raw.get("profiles", []):
            profile = Profile.from_dict(pdata)
            with self._lock:
                exists = profile.id in self._profiles
            if exists and skip_existing:
                continue
            self.add(profile)
            added += 1
        return added


_global_registry: Optional[ProfileRegistry] = None
_global_lock = threading.Lock()


def get_registry() -> ProfileRegistry:
    """Retorna a instância global (singleton, lazy) do ProfileRegistry."""
    global _global_registry
    with _global_lock:
        if _global_registry is None:
            _global_registry = ProfileRegistry()
        return _global_registry

"""
edp.ingest.session_index — índice reverso profile_id -> conversation_ids.

Eventos de uma conversa já são recuperáveis diretamente via
get_memory(registry_key(conversation_id)).episodic.entries — não precisam
de índice. Este módulo cobre o único caso que precisa: listar todas as
conversas associadas a um profile_id (GET /memory/sessions?profile_id=).

Persistência simples via os mesmos helpers atômicos usados pelo resto da
memória (edp.memory.atomic_io), sem reinventar write atômico aqui.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from ..memory.atomic_io import _atomic_write_json, _safe_load_json


class SessionIndex:
    """
    Índice em memória (com persistência em disco) de
    profile_id -> {conversation_id: last_seen_ts}.

    touch() só atualiza o estado em memória — chamador decide quando
    persistir via flush() (tipicamente em response_end / disconnect, não a
    cada evento, para não pagar um fsync por mensagem do live feed).
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, float]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            loaded = _safe_load_json(self.path)
            if isinstance(loaded, dict):
                self._data = {
                    str(profile_id): {
                        str(cid): float(ts) for cid, ts in conversations.items()
                    }
                    for profile_id, conversations in loaded.items()
                    if isinstance(conversations, dict)
                }

    def touch(self, profile_id: str, conversation_id: str, ts: float) -> None:
        """Registra/atualiza o last_seen de uma conversa para um profile_id."""
        with self._lock:
            bucket = self._data.setdefault(profile_id, {})
            bucket[conversation_id] = ts
            self._dirty = True

    def list_conversations(self, profile_id: str) -> list[dict]:
        """Lista conversas do profile_id, mais recentes primeiro."""
        with self._lock:
            bucket = self._data.get(profile_id, {})
            items = [
                {"session_id": cid, "last_seen": ts}
                for cid, ts in bucket.items()
            ]
        items.sort(key=lambda e: e["last_seen"], reverse=True)
        return items

    def flush(self) -> None:
        """Persiste em disco se houver mudanças pendentes."""
        with self._lock:
            if not self._dirty:
                return
            snapshot = {k: dict(v) for k, v in self._data.items()}
            self._dirty = False
        _atomic_write_json(self.path, snapshot)


_instance: Optional[SessionIndex] = None
_instance_lock = threading.Lock()


def get_session_index() -> SessionIndex:
    """Singleton do índice, path resolvido de edp.config.LIVE_FEED_INDEX_PATH."""
    global _instance
    with _instance_lock:
        if _instance is None:
            from ..config import LIVE_FEED_INDEX_PATH
            _instance = SessionIndex(LIVE_FEED_INDEX_PATH)
        return _instance

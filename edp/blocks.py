"""
edp.blocks — Infraestrutura de blocos (Peça 2.0).

Conceito (decidido pelo usuário):
  Um bloco é uma unidade de conversa coerente. É a unidade real de validação
  e ouro destilado, não o entry individual.

  Estrutura:
    - Texto principal emerge dos entries que formam o bloco
    - Comentários posteriores acumulam no próprio bloco (refutações bestas
      ou geniais ficam aqui, não viram blocos novos)
    - Relações inter-blocos (supersedes, superseded_by) são raras — só
      substanciais como Einstein refutando Newton viram bloco novo
    - Status: active / closed / superseded
    - Verificação é datada — nenhum bloco é definitivo

Peça 2.0 entrega apenas a INFRAESTRUTURA:
  - Persistência (com write atômico)
  - Criação automática de bloco aberto por sessão
  - Vinculação de entry novo ao bloco aberto atual
  - NÃO há fechamento automático ainda (peça 2.6)
  - NÃO há detecção semântica de mudança de tema ainda (peça 2.6)
  - NÃO há UI para comentários ainda (peça 2.10)
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .clock import now as _now


# ───────────────────────────────────────────────────────────────────
# Status do bloco
# ───────────────────────────────────────────────────────────────────

BLOCK_STATUS_ACTIVE     = "active"
BLOCK_STATUS_CLOSED     = "closed"
BLOCK_STATUS_SUPERSEDED = "superseded"

BLOCK_STATUSES = (BLOCK_STATUS_ACTIVE, BLOCK_STATUS_CLOSED, BLOCK_STATUS_SUPERSEDED)


# ───────────────────────────────────────────────────────────────────
# Tipos de comentário (extensível em peça 2.10)
# ───────────────────────────────────────────────────────────────────

COMMENT_TYPE_REFUTATION = "refutation"
COMMENT_TYPE_CONNECTION = "connection"
COMMENT_TYPE_EXPANSION  = "expansion"
COMMENT_TYPE_OBSERVATION = "observation"

COMMENT_TYPES = (
    COMMENT_TYPE_REFUTATION,
    COMMENT_TYPE_CONNECTION,
    COMMENT_TYPE_EXPANSION,
    COMMENT_TYPE_OBSERVATION,
)


# ───────────────────────────────────────────────────────────────────
# Estruturas
# ───────────────────────────────────────────────────────────────────

@dataclass
class BlockComment:
    """
    Comentário posterior em um bloco fechado.

    Refutações bestas ou geniais ficam aqui — EDP não filtra qualidade,
    Pai e Filho do futuro julgam.

    Campos:
        id: UUID único do comentário
        author: "pai" ou "filho" (quem fez o comentário)
        timestamp: quando foi feito (t_absolute via clock interno)
        content: texto do comentário
        comment_type: refutation / connection / expansion / observation
        quality_marked_by_pai: opcional — pai pode marcar "besta"/"genial"/"útil"
    """
    id: str
    author: str  # "pai" ou "filho"
    timestamp: float
    content: str
    comment_type: str = COMMENT_TYPE_OBSERVATION
    quality_marked_by_pai: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BlockComment":
        return cls(**d)


@dataclass
class Block:
    """
    Bloco de conversa.

    Unidade real de validação e ouro destilado.

    Campos:
        id: UUID único do bloco
        status: active / closed / superseded
        created_at: t_absolute de criação
        closed_at: t_absolute de fechamento (None se aberto)
        entry_ids: IDs dos entries que formam este bloco
        comments: lista de BlockComment (cresce no tempo)
        supersedes: IDs de blocos que este refuta/expande substancialmente (raro)
        superseded_by: IDs de blocos que refutam/expandem este (raro)
        edp_session_id: sessão vitalícia do EDP em que o bloco existe
        validated_by_dupla: True se passou Camada 1 + Camada 2 (peça 2.5+)
        validated_at: timestamp da validação (None se não validado)
    """
    id: str
    status: str
    created_at: float
    edp_session_id: str
    closed_at: Optional[float] = None
    entry_ids: list = field(default_factory=list)
    comments: list = field(default_factory=list)  # list[BlockComment]
    supersedes: list = field(default_factory=list)
    superseded_by: list = field(default_factory=list)
    validated_by_dupla: bool = False
    validated_at: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Serializa comentários
        d["comments"] = [
            c.to_dict() if isinstance(c, BlockComment) else c
            for c in self.comments
        ]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        d = dict(d)
        # Deserializa comentários
        comments_raw = d.pop("comments", [])
        d["comments"] = [
            BlockComment.from_dict(c) if isinstance(c, dict) else c
            for c in comments_raw
        ]
        return cls(**d)

    def add_entry(self, entry_id: str) -> None:
        """Vincula entry a este bloco."""
        if entry_id not in self.entry_ids:
            self.entry_ids.append(entry_id)

    def add_comment(
        self,
        author: str,
        content: str,
        comment_type: str = COMMENT_TYPE_OBSERVATION,
        quality_marked_by_pai: Optional[str] = None,
    ) -> BlockComment:
        """
        Adiciona comentário ao bloco.

        Pode ser feito em bloco active OU closed — bloco fechado continua
        recebendo comentários no tempo.
        """
        comment = BlockComment(
            id=str(uuid.uuid4()),
            author=author,
            timestamp=_now(),
            content=content,
            comment_type=comment_type,
            quality_marked_by_pai=quality_marked_by_pai,
        )
        self.comments.append(comment)
        return comment

    def close(self) -> None:
        """Marca bloco como closed. Mantém recebendo comentários."""
        if self.status == BLOCK_STATUS_ACTIVE:
            self.status = BLOCK_STATUS_CLOSED
            self.closed_at = _now()

    def reopen(self) -> None:
        """
        Reabre bloco fechado (Pai pode querer continuar).
        Não funciona em superseded — esse é estado terminal.
        """
        if self.status == BLOCK_STATUS_CLOSED:
            self.status = BLOCK_STATUS_ACTIVE
            self.closed_at = None


# ───────────────────────────────────────────────────────────────────
# BlockManager — gerencia blocos por sessão
# ───────────────────────────────────────────────────────────────────

class BlockManager:
    """
    Gerencia blocos de uma sessão.

    Responsabilidades:
        - Carregar/persistir blocos do disco (write atômico)
        - Manter "bloco aberto atual" — onde novos entries vão
        - Criar bloco automaticamente se não houver aberto
        - Permitir fechamento manual

    Peça 2.0 NÃO implementa:
        - Fechamento automático (peça 2.6)
        - Detecção semântica de mudança de tema (peça 2.6)
        - UI de comentários (peça 2.10)
    """

    def __init__(self, session_id: str, base_dir: Path, scope: str = "cognitive"):
        """
        Args:
            session_id: ID da sessão
            base_dir: MEMORY_DIR (raiz de sessions)
            scope: 'cognitive' (default) ou 'sprint'. Commit 1 dos Dois Exocórtices.
        """
        self.session_id = session_id
        self.scope      = scope
        # Commit 1: caminhos isolados por scope
        scope_dir = base_dir / f"{session_id}_{scope}"
        scope_dir.mkdir(parents=True, exist_ok=True)
        self.path = scope_dir / "blocks.json"
        self._lock = threading.RLock()
        self.blocks: list[Block] = []
        self._load()

    def _load(self) -> None:
        """Carrega blocos do disco (tolerante a corrupção via _safe_load_json)."""
        # Import tardio para evitar ciclo memory ↔ blocks
        from .memory import _safe_load_json

        if not self.path.exists():
            return
        try:
            data = _safe_load_json(self.path)
            if data and isinstance(data, list):
                self.blocks = [Block.from_dict(b) for b in data]
        except Exception:
            # Falha de load não bloqueia operação — começa vazio
            self.blocks = []

    def save(self) -> None:
        """Persiste blocos no disco (write atômico)."""
        from .memory import _atomic_write_json

        with self._lock:
            data = [b.to_dict() for b in self.blocks]
            _atomic_write_json(self.path, data, indent=2)

    def get_active_block(self, edp_session_id: str) -> Block:
        """
        Retorna o bloco aberto atual. Cria um novo se não houver.

        Em peça 2.0: só há UM bloco aberto por vez. Próximos entries
        entram nele até fechamento manual (peça 2.6 adiciona heurísticas).
        """
        with self._lock:
            # Procura bloco active existente
            for b in self.blocks:
                if b.status == BLOCK_STATUS_ACTIVE:
                    return b

            # Nenhum aberto: cria novo
            new_block = Block(
                id=str(uuid.uuid4()),
                status=BLOCK_STATUS_ACTIVE,
                created_at=_now(),
                edp_session_id=edp_session_id,
            )
            self.blocks.append(new_block)
            self.save()
            return new_block

    def get_block(self, block_id: str) -> Optional[Block]:
        """Retorna bloco por ID, ou None."""
        with self._lock:
            for b in self.blocks:
                if b.id == block_id:
                    return b
            return None

    def close_active_block(self) -> Optional[Block]:
        """
        Fecha bloco aberto atual (se houver). Retorna o bloco fechado.
        Peça 2.6 vai adicionar heurísticas automáticas; aqui é manual.
        """
        with self._lock:
            for b in self.blocks:
                if b.status == BLOCK_STATUS_ACTIVE:
                    b.close()
                    self.save()
                    return b
            return None

    def link_entry_to_active_block(
        self, entry_id: str, edp_session_id: str
    ) -> str:
        """
        Vincula entry ao bloco aberto atual.
        Retorna o block_id ao qual o entry foi vinculado.
        """
        with self._lock:
            block = self.get_active_block(edp_session_id)
            block.add_entry(entry_id)
            self.save()
            return block.id

    def list_blocks(self, status: Optional[str] = None) -> list[Block]:
        """Lista blocos, opcionalmente filtrados por status."""
        with self._lock:
            if status is None:
                return list(self.blocks)
            return [b for b in self.blocks if b.status == status]

    def stats(self) -> dict:
        """Estatísticas resumidas dos blocos."""
        with self._lock:
            active = sum(1 for b in self.blocks if b.status == BLOCK_STATUS_ACTIVE)
            closed = sum(1 for b in self.blocks if b.status == BLOCK_STATUS_CLOSED)
            superseded = sum(1 for b in self.blocks if b.status == BLOCK_STATUS_SUPERSEDED)
            total_entries = sum(len(b.entry_ids) for b in self.blocks)
            total_comments = sum(len(b.comments) for b in self.blocks)
            return {
                "total_blocks":   len(self.blocks),
                "active":         active,
                "closed":         closed,
                "superseded":     superseded,
                "total_entries":  total_entries,
                "total_comments": total_comments,
            }

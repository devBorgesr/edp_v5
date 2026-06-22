"""
edp.lab.prontuario — Store longitudinal da Bancada de Contexto (Burp-for-LLMs).

A BASE. Tudo da Bancada (Repetidor, Rodizio, Comparador, aba de historico) le e
escreve por aqui. Se isto apodrece no tempo, a ferramenta inteira apodrece. Por
isso e construido para durar TEMPO INDETERMINADO e blindado contra os 5 bugs que
a lente "tem que durar para sempre" revelou na varredura do EDP:

  Bug 1 (cegueira de rotacao): o leitor varre TODOS os arquivos de indice
        (atual + rotacionados), nunca so o atual. E o query_rotated() que o
        pareto_store deixou "para o futuro" — aqui implementado de fato.
  Bug 2 (payload pesado no log consultavel): INDICE fino (consultavel, 1 linha
        por run) separado do BLOB pesado (janela completa + N respostas), gravado
        em arquivo proprio por run e buscado sob demanda.
  Bug 3 (isolamento): este store NAO troca scope nem decide isolamento. Isolar e
        responsabilidade de edp.lab.isolation (sessao de lab dedicada). Aqui so
        se grava o que mandaram, com o scope que mandaram.
  Bug 4 (deriva de comparabilidade): cada registro fixa o ANDAIME completo
        (code_sha, embedding, prompt, camadas de politica, skills, tools, memoria)
        + um andaime_hash estavel para agrupar SO o que e comensuravel ao longo
        dos anos.
  Bug 5 (relogio): cada registro grava ts_verified (estado do clock no momento),
        para a analise poder marcar/excluir registros de timestamp duvidoso.

Herdado da casa (pareto_store / lineage / health_index):
  - append-only para o indice; escrita ATOMICA (tmp+rename) para os blobs
  - rotacao por tamanho ARQUIVAL (renomeia, NUNCA apaga)
  - never-raise: gravar telemetria/experimento nunca quebra o fluxo
  - singleton thread-safe (double-checked locking)
  - interface Protocol (Soberania Progressiva): trocar File->Postgres no futuro
    sem mexer nos chamadores

Layout em disco ($EDP_BASE_DIR/lab/prontuario/):
  index.jsonl                       <- indice fino, 1 linha por run (consultavel)
  index.<ts>.jsonl                  <- indices rotacionados (LIDOS pelo reader)
  blobs/<YYYY-MM>/<run_id>.json     <- payload pesado por run (sharded por mes)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Protocol

from ..clock import now as _now

logger = logging.getLogger("edp.lab.prontuario")


# ── Relogio: estado de verificacao (Bug 5). Defensivo — se a casa nao expoe
#    is_verified(), grava None em vez de quebrar. ──────────────────────────────
def _clock_verified() -> Optional[bool]:
    try:
        from ..clock import is_verified  # type: ignore
        return bool(is_verified())
    except Exception:
        return None


# ── Rotacao do INDICE (so o indice fino rotaciona; blobs sao 1-por-arquivo) ───
def _rotate_mb() -> int:
    try:
        return max(1, int(os.environ.get("EDP_PRONTUARIO_ROTATE_MB", "50")))
    except Exception:
        return 50


# ── Campos do andaime que definem COMENSURABILIDADE (Bug 4) ───────────────────
# Dois registros so podem ser comparados se estes campos baterem. O andaime_hash
# e computado sobre eles (canonicalizados), estavel entre execucoes.
_COMMENSURABILITY_FIELDS = (
    "code_sha",            # git SHA do EDP no momento
    "embedding_version",   # id/versao do modelo de embedding (ex: all-MiniLM-L6-v2)
    "prompt_version",      # versao do system prompt / template
    "model_version",       # o modelo-alvo (ex: claude-haiku-4-5)
    "policy_layers",       # camadas de politica presentes (lista; ablacao mexe aqui)
    "skills_loaded",       # skills/modulos procedurais carregados (lista)
    "tools_available",     # ferramentas/conectores disponiveis (lista)
)


def _andaime_hash(andaime: dict) -> str:
    """Hash estavel do subconjunto de comensurabilidade. Listas sao ordenadas
    para que a ordem nao mude o hash. Campos ausentes contam como vazios."""
    try:
        canon = {}
        for k in _COMMENSURABILITY_FIELDS:
            v = andaime.get(k)
            if isinstance(v, (list, tuple, set)):
                v = sorted(str(x) for x in v)
            canon[k] = v
        blob = json.dumps(canon, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "0" * 16


# ── Interface (Arquitetura Forward / Soberania Progressiva) ───────────────────
class ProntuarioStore(Protocol):
    """Interface do store. Implementacoes: File (local), Remote/Postgres (futuro)."""

    def record_run(self, **kwargs) -> str: ...
    def query_index(self, **kwargs) -> Iterator[dict]: ...
    def get_blob(self, run_id: str, blob_ref: Optional[str] = None) -> Optional[dict]: ...
    def stats(self) -> dict: ...


# ── Implementacao local ───────────────────────────────────────────────────────
class FileProntuarioStore:
    """JSONL append-only para o indice + um arquivo JSON por run para o blob.

    Robustez (Base Solida): record_run() nunca propaga excecao — gravar
    experimento nao pode quebrar o fluxo. Linhas corrompidas no indice sao
    puladas com log debug.
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        if base_dir is None:
            base = os.environ.get("EDP_BASE_DIR", "data")
            base_dir = Path(base) / "lab" / "prontuario"
        self.dir = Path(base_dir)
        self.blobs_dir = self.dir / "blobs"
        self.index_file = self.dir / "index.jsonl"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._stats = {
            "runs_recorded": 0,
            "runs_failed":   0,
            "rotations":     0,
            "started_at":    _now(),
        }
        logger.info(
            "[prontuario] inicializado | dir=%s | rotate_mb=%d",
            self.dir, _rotate_mb(),
        )

    # ── ESCRITA ───────────────────────────────────────────────────────────────
    def record_run(
        self,
        *,
        modelo: str,
        formato_id: str,
        andaime: dict,
        janela_enviada: str,
        secoes: dict,
        respostas: list,
        scope: str = "sprint",
        metricas: Optional[dict] = None,
        run_id: Optional[str] = None,
    ) -> str:
        """Grava 1 run (1 celula de experimento). Retorna o run_id.

        Separa INDICE (fino, consultavel) de BLOB (pesado, sob demanda) — Bug 2.
        Grava andaime completo + andaime_hash — Bug 4. Grava ts_verified — Bug 5.
        Nunca levanta excecao — Base Solida.

        respostas: lista de N amostras (Bug do nao-determinismo: 1 resposta nao e
                   dado). O store nao impoe N; quem chama (sampler) garante N>=N_min.
        """
        rid = run_id or _new_run_id()
        try:
            ts = _now()
            tsv = _clock_verified()
            ah = _andaime_hash(andaime or {})

            # 1) BLOB pesado: arquivo proprio, sharded por mes, escrita atomica.
            shard = _month_shard(ts)
            shard_dir = self.blobs_dir / shard
            shard_dir.mkdir(parents=True, exist_ok=True)
            blob_path = shard_dir / f"{rid}.json"
            blob_ref = f"{shard}/{rid}.json"
            blob = {
                "run_id":         rid,
                "ts":             ts,
                "modelo":         modelo,
                "formato_id":     formato_id,
                "andaime":        andaime,          # COMPLETO
                "janela_enviada": janela_enviada,   # COMPLETA, sem corte
                "secoes":         secoes,           # estrutura rotulada, completa
                "respostas":      list(respostas),  # N amostras, completas
            }
            self._atomic_write_json(blob_path, blob)

            # 2) INDICE fino: 1 linha, append-only, sob lock, com rotacao arquival.
            index_row = {
                "run_id":       rid,
                "ts":           ts,
                "ts_verified":  tsv,
                "modelo":       modelo,
                "formato_id":   formato_id,
                "scope":        scope,
                "andaime_hash": ah,
                "n_amostras":   len(respostas) if respostas else 0,
                "metricas":     metricas or {},
                "blob_ref":     blob_ref,
            }
            line = json.dumps(index_row, ensure_ascii=False) + "\n"
            with self._lock:
                if self.index_file.exists():
                    try:
                        if self.index_file.stat().st_size > _rotate_mb() * 1024 * 1024:
                            self._rotate_index_locked()
                    except Exception as e:
                        logger.debug("[prontuario] stat indice falhou: %s", e)
                with open(self.index_file, "a", encoding="utf-8") as f:
                    f.write(line)
                self._stats["runs_recorded"] += 1
            return rid
        except Exception as e:
            self._stats["runs_failed"] += 1
            logger.warning(
                "[prontuario] record_run falhou (run_id=%s): %s: %s",
                rid, type(e).__name__, str(e)[:160],
            )
            return rid

    def _atomic_write_json(self, path: Path, obj: dict) -> None:
        """Escreve via tmp + rename (atomico). Padrao da casa (health_index/lineage)."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        tmp.replace(path)

    def _rotate_index_locked(self) -> None:
        """Renomeia o indice atual com timestamp + sufixo unico. Chamado COM lock.
        NAO apaga. Sufixo unico evita colisao se 2 rotacoes caem no mesmo segundo
        (improvavel em uso real, mas a base tem que aguentar tempo indeterminado)."""
        try:
            rotated = self.dir / f"index.{int(_now())}.{uuid.uuid4().hex[:6]}.jsonl"
            self.index_file.rename(rotated)
            self._stats["rotations"] += 1
            logger.info("[prontuario] rotacao indice | antigo=%s", rotated.name)
        except Exception as e:
            logger.warning("[prontuario] rotacao indice falhou: %s", e)

    # ── LEITURA ───────────────────────────────────────────────────────────────
    def _all_index_files(self) -> list:
        """TODOS os arquivos de indice: rotacionados (ordenados) + atual por ultimo.

        Bug 1 (cegueira de rotacao) RESOLVIDO AQUI: o leitor nunca ignora a
        historia rotacionada. Esta e a diferenca em relacao ao query() do pareto,
        que so le o arquivo atual.
        """
        rotated = sorted(self.dir.glob("index.*.jsonl"))  # index.<ts>.jsonl
        files = list(rotated)
        if self.index_file.exists():
            files.append(self.index_file)
        return files

    def query_index(
        self,
        *,
        formato_id: Optional[str] = None,
        modelo: Optional[str] = None,
        andaime_hash: Optional[str] = None,
        scope: Optional[str] = None,
        since_ts: Optional[float] = None,
    ) -> Iterator[dict]:
        """Itera linhas do indice de TODOS os arquivos (atual + rotacionados),
        em ordem cronologica por arquivo, com filtros opcionais.

        Devolve so o INDICE fino. Para o payload pesado, chame get_blob(run_id).
        Linhas corrompidas sao puladas. Nunca levanta excecao.
        """
        for path in self._all_index_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except Exception:
                            logger.debug("[prontuario] linha corrompida em %s", path.name)
                            continue
                        if formato_id is not None and row.get("formato_id") != formato_id:
                            continue
                        if modelo is not None and row.get("modelo") != modelo:
                            continue
                        if andaime_hash is not None and row.get("andaime_hash") != andaime_hash:
                            continue
                        if scope is not None and row.get("scope") != scope:
                            continue
                        if since_ts is not None and row.get("ts", 0) < since_ts:
                            continue
                        yield row
            except Exception as e:
                logger.warning("[prontuario] leitura de %s falhou: %s", path.name, e)

    def get_blob(self, run_id: str, blob_ref: Optional[str] = None) -> Optional[dict]:
        """Busca o payload pesado de um run. Usa blob_ref (do indice) se dado;
        senao localiza por glob blobs/**/<run_id>.json. Retorna None se ausente."""
        try:
            if blob_ref:
                p = self.blobs_dir / blob_ref
                if p.exists():
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
            hits = list(self.blobs_dir.glob(f"*/{run_id}.json"))
            if hits:
                with open(hits[0], "r", encoding="utf-8") as f:
                    return json.load(f)
            return None
        except Exception as e:
            logger.debug("[prontuario] get_blob(%s) falhou: %s", run_id, e)
            return None

    def stats(self) -> dict:
        try:
            idx_size = self.index_file.stat().st_size if self.index_file.exists() else 0
            n_index_files = len(self._all_index_files())
            n_blobs = sum(1 for _ in self.blobs_dir.glob("*/*.json"))
        except Exception:
            idx_size, n_index_files, n_blobs = -1, -1, -1
        return {
            **self._stats,
            "index_size_bytes": idx_size,
            "n_index_files":    n_index_files,
            "n_blobs":          n_blobs,
            "dir":              str(self.dir),
            "rotate_mb":        _rotate_mb(),
        }


# ── run_id e shards ───────────────────────────────────────────────────────────
def _new_run_id() -> str:
    """run_id reusa o correlation_id do turno (pareto_store) se houver — para
    amarrar o run aos eventos do mesmo turno. Senao, gera proprio."""
    try:
        from ..runtime.pareto_store import get_current_correlation_id
        cid = get_current_correlation_id()
        if cid:
            return f"run_{cid}"
    except Exception:
        pass
    return f"run_{uuid.uuid4().hex[:12]}"


def _month_shard(ts: float) -> str:
    """Shard de blobs por mes (YYYY-MM) — evita milhoes de arquivos num diretorio
    so em tempo indeterminado."""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m")
    except Exception:
        return "0000-00"


# ── Singleton (padrao runtime do EDP) ─────────────────────────────────────────
_store: Optional[FileProntuarioStore] = None
_store_lock = threading.Lock()


def get_prontuario() -> FileProntuarioStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = FileProntuarioStore()
    return _store

"""
edp.memory.atomic_io — serialização + write atômico + load tolerante.

Fase 4 T3: extraído verbatim de memory.py:101-249 (MOVE-ONLY, corpos de
função byte-idênticos ao original — só esta docstring e os imports são
novos). Carrega a Dívida técnica #8 (write atômico com lock+retry, ver
docstring de _atomic_write_json abaixo) — não mexido nesta extração.

Dívida #53 (docs/preregistro_fix_corrupcao_json.md, 04/08/2026):
  - _safe_load_json otimizada (rfind + cap N=20 candidatos) — o loop
    caractere-a-caractere antigo era O(n²)-ish em arquivos grandes
    truncados no meio (medido >300s / ~17min extrapolado em 10MB).
    Contrato preservado: ainda propaga JSONDecodeError se irrecuperável.
  - _load_json_or_quarantine() — novo choke-point opcional, usado pelos
    6 call sites migrados (store.py, semantic.py, echo_chamber.py,
    blocks.py, ingest/session_index.py, profiles/registry.py): nunca
    crasha o boot, nunca perde dado em silêncio. Ver pré-registro para
    critério de decisão completo.
"""
import json
import logging
import os
import threading
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger("edp.memory")

def _serialize(entries: list[dict]) -> list[dict]:
    out = []
    for e in entries:
        c = dict(e)
        if isinstance(c.get("embedding"), np.ndarray):
            c["embedding"] = c["embedding"].tolist()
        out.append(c)
    return out

def _deserialize(entries: list[dict]) -> list[dict]:
    for e in entries:
        if "embedding" in e and isinstance(e["embedding"], list):
            e["embedding"] = np.array(e["embedding"], dtype=np.float32)
    return entries


# ── Peça 0.3.1: Write atômico e load tolerante ────────────────────────────────

# Dívida técnica #8: PermissionError [WinError 32] em writes concorrentes no Windows.
# `os.replace` falha se destino estiver aberto por outro thread (ex: dashboard lendo).
# Defesas em camadas:
#   1. Lock global por path (serializa saves do mesmo arquivo)
#   2. Retry com backoff exponencial em PermissionError/OSError
#   3. Limpeza de .tmp órfão pré-existente

_WRITE_LOCKS: dict = {}
_WRITE_LOCKS_GUARD = threading.Lock()

def _get_write_lock(path):
    """Lock por path (chave = str do path absoluto). Cria lazy."""
    key = str(Path(path).resolve())
    with _WRITE_LOCKS_GUARD:
        lk = _WRITE_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _WRITE_LOCKS[key] = lk
        return lk


def _atomic_write_json(path, data, *, indent: int = 2) -> None:
    """
    Grava JSON de forma atômica: tmp → fsync → rename.

    Se processo for interrompido no meio da gravação, o arquivo original
    fica intacto (apenas o .tmp pode estar parcial). Próximo boot lê o
    original sem corrupção.

    Robusto em POSIX e Windows. Em Windows, `os.replace` pode falhar com
    PermissionError [WinError 32] se o destino estiver aberto por outro
    thread (ex: dashboard lendo) ou processo (ex: antivírus escaneando).
    Defesas: lock por path + retry com backoff.

    Args:
        path: caminho do arquivo final
        data: dado serializável em JSON
        indent: indentação do JSON
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    # Serializa saves do mesmo arquivo (evita .tmp órfão por concorrência interna)
    with _get_write_lock(path):
        # Limpa .tmp órfão de save anterior que morreu (se houver)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

        # 1. Escreve no .tmp
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            try:
                # 2. Força sincronização com disco (não só buffer do OS)
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                # Alguns FS não suportam fsync; segue mesmo assim
                pass

        # 3. Replace atômico com retry — Windows pode falhar transientemente
        # quando destino está aberto por outro processo (antivírus, dashboard, etc).
        # Backoff: 50ms, 100ms, 200ms, 400ms, 800ms (total ~1.5s antes de desistir)
        backoffs = (0.05, 0.10, 0.20, 0.40, 0.80)
        last_err = None
        for delay in (0.0,) + backoffs:
            if delay > 0:
                time.sleep(delay)
            try:
                os.replace(tmp, path)
                return
            except (PermissionError, OSError) as e:
                last_err = e
                continue
        # Após todas as tentativas, levanta para o caller decidir
        # (em threads de save automático, é capturado e logado pelo runtime)
        raise last_err


# Dívida #53 / Passo 0.5 (docs/preregistro_fix_corrupcao_json.md): número
# máximo de candidatos de recuperação tentados por _safe_load_json. O
# único write path deste arquivo é _atomic_write_json (tmp → fsync →
# os.replace) — o replace só promove o .tmp depois que o write inteiro
# terminou, então o arquivo final NUNCA é a concatenação de duas gerações
# de conteúdo (um arquivo antigo inteiro sobrevivendo atrás de um novo).
# A única corrupção realista é a cauda de UMA escrita interrompida, no
# máximo alguns registros incompletos — cada um contribui no máximo ~2
# colchetes/chaves soltos (array do embedding + dict da entry). N=20
# cobre com folga até ~10 registros incompletos de lixo (nenhum cenário
# realista deste codebase chega perto disso) e mantém o pior caso
# (arquivo genuinamente irrecuperável) abaixo do limite X=20s medido em
# 10MB no pré-registro. Acima de N tentativas, desiste rápido em vez de
# tentar para sempre — quem chama decide quarentena via
# _load_json_or_quarantine().
MAX_RECOVERY_CANDIDATES = 20


def _safe_load_json(path):
    """
    Carrega JSON tolerando corrupção por write parcial.

    Estratégia:
      1. Tenta json.load() normal
      2. Se falhar com 'Extra data' (dados depois do JSON válido):
         tenta recuperar a parte válida procurando último ']'/'}' fechado
         que parseie — varredura via str.rfind (C-level) em vez de checar
         caractere a caractere, capada em MAX_RECOVERY_CANDIDATES
         tentativas de json.loads (Dívida #53 / Passo 0.5: o loop antigo
         era O(n²)-ish em arquivos grandes truncados no meio — medido
         >300s / ~17min extrapolado em 10MB; ver pré-registro).
      3. Se ainda falhar, propaga o JSONDecodeError original (caller
         decide — ver _load_json_or_quarantine() para o caminho que
         nunca deixa essa exceção derrubar o boot).

    NÃO modifica o arquivo automaticamente; apenas retorna o conteúdo
    recuperado para o caller decidir o que fazer.

    Returns:
        dados (list/dict) ou None se arquivo não existe.

    Raises:
        json.JSONDecodeError: se corrompido e irrecuperável dentro do
            orçamento de MAX_RECOVERY_CANDIDATES tentativas.
        UnicodeDecodeError: se o arquivo tem bytes inválidos para utf-8.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # Tenta recuperar JSON válido cortando lixo depois do último ']' ou '}'
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            pos = len(content)
            tried = 0
            while pos > 0 and tried < MAX_RECOVERY_CANDIDATES:
                cut_idx = max(
                    content.rfind("]", 0, pos),
                    content.rfind("}", 0, pos),
                )
                if cut_idx == -1:
                    break
                cut = cut_idx + 1
                tried += 1
                candidate = content[:cut]
                try:
                    data = json.loads(candidate)
                    # Conseguiu recuperar
                    logger.warning(
                        "[memory] JSON corrompido em %s recuperado por truncamento "
                        "(tentativa %d/%d, perdeu %d bytes de lixo: %r)",
                        path, tried, MAX_RECOVERY_CANDIDATES,
                        len(content) - cut, content[cut:cut + 50],
                    )
                    return data
                except json.JSONDecodeError:
                    pos = cut_idx
                    continue
        except Exception:
            pass

        # Não recuperou dentro do orçamento — re-raise o erro original
        raise
    except FileNotFoundError:
        return None


def _load_json_or_quarantine(path, *, store_label: str):
    """
    Carrega JSON via _safe_load_json; se irrecuperável (corrupção real,
    não bug de programação), NUNCA propaga para o caller — quarentena o
    arquivo original e degrada para "vazio", de forma observável.

    Dívida #53 (docs/preregistro_fix_corrupcao_json.md): call sites que
    usavam _safe_load_json direto ou crashavam (JSONDecodeError sem
    try/except ao redor da construção do objeto) ou engoliam a exceção em
    silêncio (`except Exception: self.x = []`, sem log nem rastro). As
    duas saídas eram erradas — esta função resolve as duas:
      1. Nunca derruba o boot.
      2. Nunca perde o dado bruto sem deixar rastro nem sinal.

    Comportamento (uniforme nos 6 call sites migrados):
      1. json.load normal → recuperação de lixo-no-final (_safe_load_json,
         já com o cap do Passo 0.5) → se as duas falharem com
         JSONDecodeError/UnicodeDecodeError:
      2. Preserva o arquivo original via os.replace() (atômico, mesma
         partição — nunca copy+unlink, nunca apaga o dado bruto) para
         "<path>.corrompido-<timestamp>".
      3. logger.critical(exc_info=True) — mesmo padrão de
         edp/scoring.py:371 — path original, path de quarentena, tipo do
         erro.
      4. Emite evento "store_degraded" via edp.runtime.pareto_store
         (reaproveita o event store já existente — ver pré-registro,
         seção de observabilidade; CognitiveHealthIndex foi avaliado e
         não serve, não é um event log genérico).
      5. Retorna None — mesmo contrato que _safe_load_json já usa para
         FileNotFoundError; os call sites migrados já tratam None como
         "inicializa vazio", tratado no código chamador como degradação
         explícita (nunca sucesso silencioso).

    Gated por edp.config.EDP_STORE_QUARANTINE (default True). Com a flag
    False, repropaga a exceção original — válvula de rollback de
    emergência para ESTE mecanismo, deliberadamente com nome próprio,
    nunca compartilhada com EDP_TOXIC_GUARDS/EDP_WRITE_PROVENANCE (ver
    pré-registro — o projeto já mediu esse antipadrão de flag
    compartilhada e não repete aqui).

    Nunca deleta o arquivo corrompido automaticamente. Nunca faz
    retry-loop: uma tentativa de recuperação (_safe_load_json, já capada),
    e se falhar, quarentena — sem loop de novas tentativas de leitura.

    Args:
        path: caminho do arquivo a carregar.
        store_label: identificador curto do store para o log/evento
            (ex.: "episodic", "semantic", "echo_chamber").

    Returns:
        dados carregados, ou None se arquivo não existe OU foi
        quarentenado por corrupção irrecuperável.
    """
    from ..config import EDP_STORE_QUARANTINE

    try:
        return _safe_load_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        if not EDP_STORE_QUARANTINE:
            raise

        path = Path(path)
        ts = int(time.time() * 1000)
        quarantine_path = path.with_name(f"{path.name}.corrompido-{ts}")
        try:
            os.replace(path, quarantine_path)
        except OSError as replace_err:
            # Quarentena em si falhou (ex.: permissão) — não bloqueia o
            # boot mesmo assim; o dado bruto fica onde estava (não movido,
            # mas também não apagado). Sem retry-loop.
            logger.critical(
                "[atomic_io] store '%s' corrompido em %s (%s: %s) — "
                "QUARENTENA FALHOU (%s: %s), arquivo original preservado "
                "no lugar. Inicializando vazio (degradação explícita).",
                store_label, path, type(e).__name__, e,
                type(replace_err).__name__, replace_err,
                exc_info=True,
            )
            quarantine_path = None
        else:
            logger.critical(
                "[atomic_io] store '%s' corrompido e irrecuperável em %s "
                "(%s) — quarentenado em %s. Inicializando vazio "
                "(degradação explícita, não é sucesso).",
                store_label, path, type(e).__name__, quarantine_path,
                exc_info=True,
            )

        try:
            from ..runtime.pareto_store import emit_store_degraded
            emit_store_degraded(
                store_label=store_label,
                path=str(path),
                quarantine_path=str(quarantine_path) if quarantine_path else None,
                error_type=type(e).__name__,
            )
        except Exception:
            # Observabilidade não pode derrubar o boot — mas isso não
            # deveria acontecer; emit_* já é try/except-envolvido por
            # dentro (pareto_store.py).
            logger.warning(
                "[atomic_io] emit_store_degraded falhou para '%s' (não-fatal)",
                store_label,
            )

        return None

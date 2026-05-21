"""
edp.clock — Relógio interno robusto do EDP (Peça 0, sub-passo 0.1).

Substitui time.time() em todo o código para garantir tempo confiável
independente do relógio do sistema operacional.

Arquitetura:
  - Boot: sincroniza via NTP (pool.ntp.org) ou HTTP fallback (worldtimeapi.org)
  - Mantém monotonic counter desde o boot
  - edp.now() = boot_ntp_time + (monotonic_decorrido)
  - Resync periódico (1h) para detectar drift do monotonic
  - Se TUDO offline: cai em modo unverified, usa time.time(), marca como
    temporal_unreliable

Uso:
  from edp.clock import now, is_verified
  ts = now()
  if not is_verified():
      # estamos em modo unverified, dados gravados podem ter timestamp ruim
      ...

Princípios:
  - JAMAIS levanta exceção em now(): sempre retorna um número
  - Lazy init: sincroniza apenas na primeira chamada (ou explicitamente)
  - Thread-safe
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.request
import urllib.error
import json
from typing import Optional

logger = logging.getLogger("edp.clock")

# ───────────────────────────────────────────────────────────────────
# Configuração
# ───────────────────────────────────────────────────────────────────

NTP_SERVERS = ["pool.ntp.org", "time.google.com", "time.cloudflare.com"]
HTTP_TIME_URLS = [
    "https://worldtimeapi.org/api/timezone/Etc/UTC",
    "https://timeapi.io/api/Time/current/zone?timeZone=UTC",
]
NTP_TIMEOUT_S = 3.0
HTTP_TIMEOUT_S = 5.0
RESYNC_INTERVAL_S = 3600.0  # 1 hora


# ───────────────────────────────────────────────────────────────────
# Estado interno
# ───────────────────────────────────────────────────────────────────

_lock = threading.RLock()
_boot_ntp_time: Optional[float] = None      # timestamp NTP no momento do boot
_boot_monotonic: Optional[float] = None     # time.monotonic() no momento do boot
_last_sync_monotonic: Optional[float] = None  # quando última sync foi feita
_verified: bool = False                      # True se temos hora confiável
_sync_source: str = "none"                   # ntp / http / system_fallback / none


# ───────────────────────────────────────────────────────────────────
# Sincronização
# ───────────────────────────────────────────────────────────────────

def _try_ntp() -> Optional[float]:
    """
    Tenta sincronizar via NTP. Requer ntplib.
    Returns timestamp em segundos epoch UTC, ou None se falhar.
    """
    try:
        import ntplib  # type: ignore
    except ImportError:
        logger.debug("[clock] ntplib não disponível, pulando NTP")
        return None

    client = ntplib.NTPClient()
    for server in NTP_SERVERS:
        try:
            response = client.request(server, version=3, timeout=NTP_TIMEOUT_S)
            ts = float(response.tx_time)
            logger.info("[clock] NTP ok via %s | ts=%.3f", server, ts)
            return ts
        except (ntplib.NTPException, OSError, Exception) as e:
            logger.debug("[clock] NTP falhou em %s: %s", server, e)
            continue

    return None


def _try_http() -> Optional[float]:
    """
    Tenta sincronizar via HTTP API de tempo. Usa stdlib urllib.
    Returns timestamp em segundos epoch UTC, ou None se falhar.
    """
    for url in HTTP_TIME_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EDP-Clock/0.1"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            # worldtimeapi.org: {"unixtime": 1234567890, ...}
            if "unixtime" in data:
                ts = float(data["unixtime"])
                logger.info("[clock] HTTP ok via %s | ts=%.3f", url, ts)
                return ts

            # timeapi.io: {"dateTime": "2026-05-21T14:00:00", ...}
            if "dateTime" in data:
                from datetime import datetime
                # Parse ISO 8601
                dt_str = data["dateTime"]
                # Remove fração se houver, deixa só até segundos
                if "." in dt_str:
                    dt_str = dt_str.split(".")[0]
                dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
                # timeapi.io retorna UTC quando solicitado
                from datetime import timezone
                ts = dt.replace(tzinfo=timezone.utc).timestamp()
                logger.info("[clock] HTTP ok via %s | ts=%.3f", url, ts)
                return ts

            logger.debug("[clock] HTTP %s formato não reconhecido", url)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            logger.debug("[clock] HTTP falhou em %s: %s", url, e)
            continue

    return None


def _sync() -> None:
    """
    Tenta sincronizar relógio. Atualiza estado global.
    Ordem: NTP → HTTP → fallback (system time).
    Caller deve segurar _lock.

    NUNCA levanta exceção: se _try_ntp ou _try_http lançar, captura e segue
    para próximo fallback.
    """
    global _boot_ntp_time, _boot_monotonic, _last_sync_monotonic
    global _verified, _sync_source

    # Tenta NTP (protegido contra exceções)
    try:
        ts = _try_ntp()
    except Exception as e:
        logger.debug("[clock] _try_ntp levantou: %s", e)
        ts = None
    if ts is not None:
        _boot_ntp_time = ts
        _boot_monotonic = time.monotonic()
        _last_sync_monotonic = _boot_monotonic
        _verified = True
        _sync_source = "ntp"
        return

    # Tenta HTTP (protegido contra exceções)
    try:
        ts = _try_http()
    except Exception as e:
        logger.debug("[clock] _try_http levantou: %s", e)
        ts = None
    if ts is not None:
        _boot_ntp_time = ts
        _boot_monotonic = time.monotonic()
        _last_sync_monotonic = _boot_monotonic
        _verified = True
        _sync_source = "http"
        return

    # Tudo offline: fallback
    _boot_ntp_time = time.time()
    _boot_monotonic = time.monotonic()
    _last_sync_monotonic = _boot_monotonic
    _verified = False
    _sync_source = "system_fallback"
    logger.warning(
        "[clock] todas as sources online falharam, usando time.time() do sistema "
        "(modo temporal_unverified)"
    )


def _ensure_initialized() -> None:
    """Garante que _sync() foi chamado pelo menos uma vez."""
    if _boot_ntp_time is None:
        _sync()


def _should_resync() -> bool:
    """Retorna True se passou tempo suficiente desde último sync."""
    if _last_sync_monotonic is None:
        return True
    elapsed = time.monotonic() - _last_sync_monotonic
    return elapsed >= RESYNC_INTERVAL_S


# ───────────────────────────────────────────────────────────────────
# API pública
# ───────────────────────────────────────────────────────────────────

def now() -> float:
    """
    Retorna timestamp atual em segundos epoch UTC.

    NUNCA levanta exceção. Em caso de erro, cai em time.time() fallback.
    Para saber se o tempo é confiável, use is_verified().

    Returns:
        Timestamp em segundos epoch (float, mesma unidade de time.time())
    """
    global _last_sync_monotonic

    with _lock:
        try:
            _ensure_initialized()

            # Resync se passou intervalo (apenas tentativa, não bloqueia)
            if _should_resync():
                try:
                    _sync()
                except Exception as e:
                    logger.debug("[clock] resync falhou: %s", e)

            # Calcula tempo atual: tempo_boot + delta_monotonic
            delta = time.monotonic() - _boot_monotonic
            return _boot_ntp_time + delta

        except Exception as e:
            # NUNCA levanta. Fallback último recurso.
            logger.warning("[clock] now() erro inesperado, usando time.time(): %s", e)
            return time.time()


def is_verified() -> bool:
    """
    Retorna True se o relógio foi sincronizado via NTP ou HTTP.
    False se estamos em modo system_fallback (tudo offline).
    """
    with _lock:
        _ensure_initialized()
        return _verified


def sync_source() -> str:
    """
    Retorna fonte da última sincronização: ntp, http, system_fallback, none.
    """
    with _lock:
        _ensure_initialized()
        return _sync_source


def force_sync() -> bool:
    """
    Força uma resync imediata. Retorna True se sincronizou com sucesso (NTP/HTTP),
    False se caiu em system_fallback.
    """
    with _lock:
        _sync()
        return _verified


def status() -> dict:
    """
    Retorna estado atual do relógio para debug/UI.
    """
    with _lock:
        _ensure_initialized()
        return {
            "now": now() if _boot_ntp_time is not None else None,
            "verified": _verified,
            "source": _sync_source,
            "boot_ntp_time": _boot_ntp_time,
            "last_sync_monotonic": _last_sync_monotonic,
            "uptime_s": (time.monotonic() - _boot_monotonic) if _boot_monotonic else 0,
        }


# ───────────────────────────────────────────────────────────────────
# Reset para testes (NÃO USAR EM PRODUÇÃO)
# ───────────────────────────────────────────────────────────────────

def _reset_for_tests() -> None:
    """Reset do estado interno. SÓ PARA TESTES."""
    global _boot_ntp_time, _boot_monotonic, _last_sync_monotonic
    global _verified, _sync_source
    with _lock:
        _boot_ntp_time = None
        _boot_monotonic = None
        _last_sync_monotonic = None
        _verified = False
        _sync_source = "none"

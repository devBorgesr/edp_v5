"""edp.api.schemas — Modelos Pydantic compartilhados entre rotas."""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


# ── Chat ───────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message:    str           = Field(..., min_length=1, max_length=10000)
    session_id: str           = Field(default="default", max_length=64)
    system:     Optional[str] = None
    provider:   Optional[str] = None
    model:      Optional[str] = None
    base_url:   Optional[str] = None
    stream:     bool          = False


class ChatResponse(BaseModel):
    text:             str
    session_id:       str
    model:            str
    latency_ms:       float
    memory_hits:      int
    tokens_generated: int
    compression_pct:  float
    mode:             str


# ── Memory ─────────────────────────────────────────────────────────────────────

class AddMemoryRequest(BaseModel):
    text:       str   = Field(..., min_length=1)
    score:      float = Field(default=0.5, ge=0.0, le=1.0)
    prioridade: str   = Field(default="media")
    session_id: str   = Field(default="default")


class RetrieveRequest(BaseModel):
    query:      str   = Field(..., min_length=1)
    session_id: str   = Field(default="default")
    top_k:      int   = Field(default=5, ge=1, le=20)
    min_score:  float = Field(default=0.20, ge=0.0, le=1.0)


class MemoryEntry(BaseModel):
    id:         str
    text:       str
    prioridade: str
    acessos:    int
    score:      float


class MemoryStats(BaseModel):
    session_id: str
    working:    int
    episodic:   int
    semantic:   int
    total:      int


# ── LLM Connection ─────────────────────────────────────────────────────────────

class ConnectRequest(BaseModel):
    provider:   str = Field(default="ollama")
    model:      str = Field(default="phi3:latest")
    base_url:   str = Field(default="http://localhost:11434")
    api_key:    str = Field(default="")
    session_id: str = Field(default="default")


class ConnectResponse(BaseModel):
    connected: bool
    provider:  str
    model:     str
    models:    List[str] = Field(default_factory=list)


# ── Health ─────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status:     str
    version:    str
    timestamp:  float
    sessions:   List[str]
    metrics:    dict
    boot_state: str  = "unknown"
    checks:     dict = Field(default_factory=dict)

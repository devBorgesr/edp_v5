"""
meta_reasoner.py (PATCHED)
[P-F4] _active/_last_ts globais protegidos por threading.Lock()
       Elimina race condition TOCTOU em reflect() concorrente
"""
import time
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from . import metrics as M

MAX_REFLECTION_DEPTH = 3
REFLECTION_COOLDOWN  = 5.0
_last_ts = 0.0; _active = 0
_meta_lock = __import__('threading').Lock()  # [P-F4] TOCTOU fix

CONFLICT_THRESH     = 0.20
REDUNDANCY_THRESH   = 0.92
LOW_CONF_THRESH     = 0.35
HIGH_CONF_THRESH    = 0.70

@dataclass
class ReflectionResult:
    confidence: float; conflicts: list; redundancies: list
    hallucination_risk: float; critique: str; reweights: dict
    depth: int=0; skipped: bool=False; skip_reason: str=""

    @property
    def is_reliable(self):
        return self.confidence>=HIGH_CONF_THRESH and self.hallucination_risk<0.30 and not self.conflicts


class MetaReasoner:
    def __init__(self, depth=0): self.depth=depth

    def reflect(self, context_items, memory_entries, query=""):
        global _last_ts, _active
        if self.depth>=MAX_REFLECTION_DEPTH:
            return ReflectionResult(0.5,[],[],0.5,"max depth",{},self.depth,True,"max_depth")
        now=time.time()
        if now-_last_ts<REFLECTION_COOLDOWN:
            return ReflectionResult(0.5,[],[],0.5,"cooldown",{},self.depth,True,"cooldown")
        with _meta_lock:  # [P-F4] atomic check-and-set
            if _active > 0:
                return ReflectionResult(0.5,[],[],0.5,"active",{},self.depth,True,"recursive")
            _active += 1
        _last_ts = now
        try:
            with M.timer("meta_reflection"):
                return self._run(context_items, memory_entries)
        finally:
            with _meta_lock:
                _active -= 1

    def _run(self, ctx, mem):
        def _embs(items):
            valid=[np.array(it["embedding"],dtype=np.float32) for it in items if it.get("embedding") is not None]
            return np.vstack(valid) if valid else np.empty((0,),dtype=np.float32)
        ctx_embs=_embs(ctx); mem_embs=_embs(mem) if mem else None
        if ctx_embs.size==0:
            return ReflectionResult(0.0,[],[],1.0,"contexto vazio",{},self.depth)
        conf=self._conf(ctx_embs,mem_embs)
        conflicts=self._conflicts(ctx,ctx_embs)
        redundancies=self._redundancies(ctx,ctx_embs)
        risk=self._risk(ctx_embs,mem_embs,conf)
        rw=self._reweights(ctx,ctx_embs,conflicts)
        critique=self._critique(conf,conflicts,redundancies,risk)
        M.record("meta_confidence",conf); M.record("meta_risk",risk)
        return ReflectionResult(conf,conflicts,redundancies,risk,critique,rw,self.depth)

    def _conf(self, ctx_embs, mem_embs):
        if len(ctx_embs)>1:
            sim=cosine_similarity(ctx_embs); np.fill_diagonal(sim,0)
            cohesion=float(sim.sum()/(len(ctx_embs)*(len(ctx_embs)-1)))
        else: cohesion=1.0
        anchor=float(cosine_similarity(ctx_embs,mem_embs).max(axis=1).mean()) if mem_embs is not None and mem_embs.size>0 else 0.5
        penalty=0.0
        if len(ctx_embs)>2:
            c=ctx_embs.mean(axis=0); d=cosine_similarity([c],ctx_embs)[0]
            penalty=sum(1 for x in d if x<0.40)/len(ctx_embs)*0.3
        return round(max(min(0.4*cohesion+0.4*anchor-penalty,1.0),0.0),4)

    def _conflicts(self, items, embs):
        if len(items)<2 or embs.size==0: return []
        sim=cosine_similarity(embs); out=[]
        for i in range(len(items)):
            for j in range(i+1,len(items)):
                s=float(sim[i,j])
                if s<CONFLICT_THRESH:
                    out.append({"item_a":items[i].get("text","")[:60],"item_b":items[j].get("text","")[:60],
                                "similarity":round(s,4),"severity":"high" if s<0.10 else "medium"})
        return out

    def _redundancies(self, items, embs):
        if len(items)<2 or embs.size==0: return []
        sim=cosine_similarity(embs); out=[]
        for i in range(len(items)):
            for j in range(i+1,len(items)):
                s=float(sim[i,j])
                if s>=REDUNDANCY_THRESH:
                    out.append({"item_a":items[i].get("text","")[:60],"item_b":items[j].get("text","")[:60],"similarity":round(s,4)})
        return out

    def _risk(self, ctx_embs, mem_embs, conf):
        risk=1.0-conf
        if mem_embs is None or mem_embs.size==0: risk=min(risk+0.20,1.0)
        else:
            anchor=float(cosine_similarity(ctx_embs,mem_embs).max(axis=1).mean())
            if anchor>0.70: risk=max(risk-0.15,0.0)
        return round(risk,4)

    def _reweights(self, items, embs, conflicts):
        if embs.size==0: return {}
        centroid=embs.mean(axis=0); sims=cosine_similarity([centroid],embs)[0]
        ct={c["item_a"][:40] for c in conflicts}|{c["item_b"][:40] for c in conflicts}
        rw={}
        for i,item in enumerate(items):
            eid=item.get("id",f"i{i}"); w=round(float(sims[i]),4)
            if any(item.get("text","")[:40] in c for c in ct): w=round(w*0.70,4)
            rw[eid]=w
        return rw

    def _critique(self, conf, conflicts, redundancies, risk):
        parts=[]
        if conf>=HIGH_CONF_THRESH: parts.append(f"contexto coerente (conf={conf:.2f})")
        elif conf>=LOW_CONF_THRESH: parts.append(f"contexto moderado (conf={conf:.2f})")
        else: parts.append(f"BAIXA CONFIANÇA ({conf:.2f})")
        if conflicts: parts.append(f"{len(conflicts)} conflito(s)")
        if redundancies: parts.append(f"{len(redundancies)} redundância(s)")
        if risk>0.60: parts.append(f"RISCO ALUCINAÇÃO ELEVADO ({risk:.2f})")
        elif risk>0.30: parts.append(f"risco moderado ({risk:.2f})")
        return " | ".join(parts) or "sem anomalias"

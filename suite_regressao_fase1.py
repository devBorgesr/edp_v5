#!/usr/bin/env python3
"""
suite_regressao_fase1.py — GATE pré-promoção: exp009+exp010+exp011 LIGADOS JUNTOS.
ZERO conserto/promoção — só mede. USO (servidor parado):
  $env:EDP_BASE_DIR="C:\\edp_data_fase0"
  $env:EDP_HYBRID_RETRIEVAL="1"; $env:EDP_CTX_SLOTS="1"
  python suite_regressao_fase1.py
R1 exp011: 4c57ed7a chega ao CP3 ('chave-valor' no prompt).      FALHA se não.
R2 exp010: Recall@5 dos REDIS_TARGET_IDS nas REDIS_QUERIES.       PASSA >=2/3 (faixa 87.5%); FALHA ~25% (cosine).
R3 exp009: %session_summary no top-5 das queries VAGAS.           PASSA <50% (faixa pós-fix ~33%); FALHA ~86.7%.
Snapshot/restore por medição (retrieve muta acessos); confirma restore==snapshot.
fase0_checkpoints.py: usa o da raiz se existir; senão extrai da branch fase0 via git.
"""
import hashlib, os, shutil, subprocess, sys, tempfile

def die(m): print(f"[ERRO] {m}"); sys.exit(2)
base = os.environ.get("EDP_BASE_DIR") or die("EDP_BASE_DIR não setado (cópia fase0)")
if os.path.basename(base.rstrip("/\\")).lower()=="edp_data": die("aponte para CÓPIA, não produção")
from edp.config import EDP_HYBRID_RETRIEVAL as _H, EDP_CTX_SLOTS as _C
assert _H and _C, \
    "AMBAS precisam estar efetivamente ON (a suite testa os três JUNTOS). " \
    "Pós-promoção o default já é ON; se falhou, alguma env var está =0."
sid=os.environ.get("EDP_SESSION_ID","default"); sd=os.path.join(base,"sessions",f"{sid}_cognitive")

def fp(p):
    h=hashlib.sha256()
    for r,_d,fs in sorted(os.walk(p)):
        for f in sorted(fs):
            q=os.path.join(r,f); h.update(os.path.relpath(q,p).encode())
            try: h.update(open(q,"rb").read())
            except Exception: pass
    return h.hexdigest()[:16]

tmp=tempfile.mkdtemp(prefix="suite_f1_"); snap=os.path.join(tmp,"pristine")
shutil.copytree(sd,snap); H=fp(snap)
def restore():
    shutil.rmtree(sd,ignore_errors=True); shutil.copytree(snap,sd)

resultados=[]
def reg(nome,crit,valor,passa):
    restore(); ok = fp(sd)==H
    resultados.append((nome,crit,valor,passa,ok))
    print(f"  {nome}: {valor}  -> {'PASSA' if passa else 'FALHA'} | restore==snapshot: {ok}")

# ── R1: exp011 CP3 (fase0_checkpoints --teste1) ──────────────────────────────
restore()
f0p="fase0_checkpoints.py"
if not os.path.exists(f0p):
    src=subprocess.run(["git","show","origin/fase0/memoria-vs-negacoes:fase0_checkpoints.py"],
                       capture_output=True,text=True)
    if src.returncode: die("fase0_checkpoints.py ausente e não extraível da branch fase0")
    f0p=os.path.join(tmp,"fase0_checkpoints.py"); open(f0p,"w",encoding="utf-8").write(src.stdout)
sys.path.insert(0,os.path.dirname(os.path.abspath(f0p)) or "."); sys.path.insert(0,".")
import importlib; f0=importlib.import_module("fase0_checkpoints")
print("== R1 (exp011 read-path, flags juntas) ==")
rc=f0.teste1()   # imprime CP1-3; CP3 com 'chave-valor'
# veredito programático: repete só o CP3
from edp.runtime.registry import get_runtime
rt=get_runtime(sid); restore()
rend,_=rt._build_enriched_context(f0.QUERY, getattr(rt,"SYSTEM_TEMPLATE","{context}"))
r1=("chave-valor" in rend)
reg("R1 exp011 CP3", "'chave-valor' no prompt", f"presente={r1}", r1)

# ── R2: exp010 Recall@5 Redis no retrieve ────────────────────────────────────
from edp.lab.exp010 import REDIS_QUERIES, REDIS_TARGET_IDS
mem=rt._memory; hits=0
print("== R2 (exp010 híbrido) ==")
for q in REDIS_QUERIES:
    restore()
    ids=[r.get("id") for r in mem.retrieve(q,top_k=5,min_score=0.20)]
    hit=any(i in REDIS_TARGET_IDS for i in ids); hits+=hit
    print(f"    {q[:50]:50} alvo_no_top5={hit}")
rec=hits/len(REDIS_QUERIES)
reg("R2 exp010 Recall@5", ">=2/3 (faixa 87.5%; cosine=25%)", f"{hits}/{len(REDIS_QUERIES)} = {rec:.0%}", rec>=2/3)

# ── R3: exp009 %SS top-5 nas vagas ───────────────────────────────────────────
from edp.lab.exp009 import VAGUE_QUERIES
ss=n=0
print("== R3 (exp009 session_summary) ==")
for q in VAGUE_QUERIES:
    restore()
    top=mem.retrieve(q,top_k=5,min_score=0.20)
    k=sum(1 for r in top if r.get("source_type")=="session_summary"); ss+=k; n+=len(top)
    print(f"    {q[:50]:50} SS_top5={k}/{len(top)}")
pct=ss/max(n,1)
reg("R3 exp009 %SS vagas", "<50% (pós-fix ~33%; pré-fix 86.7%)", f"{ss}/{n} = {pct:.1%}", pct<0.50)

print("\n" + "="*74)
print(f"{'medição':22}{'valor':28}{'veredito':10}restore")
for nome,_c,v,p,ok in resultados:
    print(f"{nome:22}{v:28}{'PASSA' if p else 'FALHA':10}{ok}")
todas=all(p and ok for _n,_c,_v,p,ok in resultados)
print(f"\nVEREDITO DA SUITE: {'3/3 PASSAM — Fase 1 pronta para a decisão de promoção (etapa separada)' if todas else 'REGRESSÃO detectada — NÃO promover; reportar acima'}")
shutil.rmtree(tmp,ignore_errors=True)
sys.exit(0 if todas else 1)

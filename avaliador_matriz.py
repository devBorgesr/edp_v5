#!/usr/bin/env python3
"""avaliador_matriz.py v2 — fase 2 exp012-v2, com DEDUP por conteúdo.
USO: python avaliador_matriz.py gt_rotulacao.csv gt_features.csv "regra1" ["regra2"]
Dedup: chave=hash(query_norm+resposta_norm). Duplicatas com rótulos DIVERGENTES são
listadas e EXCLUÍDAS da matriz (fronteira instável = informação). Reporta N bruto /
pós-dedup / excluído-inconsistência / AMBIGUO. REGRAS CONGELADAS (pré-registro):
R1 negacao_textual | R2 kw_continuidade | R3 neg AND kw | R4 neg OR kw."""
import csv,hashlib,re,sys
def norm(s): return re.sub(r"\s+"," ",(s or "").strip().lower())
def load(p,skip_guide=False):
    return list(csv.DictReader((l for l in open(p,encoding="utf-8") if not (skip_guide and l.startswith("#")))))
def coerce(v):
    if v in ("True","False"): return v=="True"
    if v in ("","AUSENTE",None): return None
    try: return float(v) if "." in str(v) else int(v)
    except Exception: return v
def pred(regra,f):
    try: return bool(eval(regra,{"__builtins__":{}},{k:coerce(v) for k,v in f.items()}))
    except Exception: return False

rot=load(sys.argv[1],skip_guide=True); feats={r["id"]:r for r in load(sys.argv[2])}
regras=sys.argv[3:]; assert regras,"passe 1-2 regras"
# ── dedup por conteúdo ──
grupos={}
for r in rot:
    k=hashlib.sha256((norm(r["query"])+"||"+norm(r["resposta"])).encode()).hexdigest()[:12]
    grupos.setdefault(k,[]).append(r)
reps=[]; inconsist=[]
for k,g in grupos.items():
    labs={x.get("rotulo","").strip() for x in g if x.get("rotulo","").strip()}
    if len(labs)>1:
        inconsist.append((k,[(x["id"],x["store"],x.get("rotulo")) for x in g]))
        continue
    rep=dict(g[0]); rep["_dups"]=len(g); rep["_stores"]="|".join(sorted({x["store"] for x in g}))
    reps.append(rep)
n_amb=sum(1 for r in reps if r.get("rotulo","").strip()=="AMBIGUO")
print(f"N bruto={len(rot)} | pós-dedup={len(reps)} (grupos={len(grupos)}) | "
      f"excluído-inconsistência={sum(len(g[1]) for g in inconsist)} em {len(inconsist)} grupo(s) | AMBIGUO(fora)={n_amb}")
if inconsist:
    print("\n== INCONSISTÊNCIAS DE RÓTULO (duplicatas divergentes — resolver manualmente; FORA da matriz) ==")
    for k,items in inconsist: print(f"  hash={k}: {items}")

def avalia(regra):
    tp=fp=fn=tn=0; FNs=[];FPs=[]; fino={}
    for r in reps:
        lab=r.get("rotulo","").strip(); i=r["id"]
        if not lab or lab=="AMBIGUO" or i not in feats: continue
        y=lab.startswith("VENENO"); p=pred(regra,feats[i])
        fino.setdefault((lab,r.get("origem","")),[0,0])[0 if p==y else 1]+=1
        if p and y: tp+=1
        elif p: fp+=1; FPs.append((i,r["_stores"],r["query"][:55]))
        elif y: fn+=1; FNs.append((i,r["_stores"],r["query"][:55]))
        else: tn+=1
    P=tp/max(tp+fp,1);R=tp/max(tp+fn,1);F=2*P*R/max(P+R,1e-9)
    print(f"\nREGRA: {regra}\n  TP={tp} FP={fp} FN={fn} TN={tn} | precision={P:.2f} recall={R:.2f} F1={F:.2f}")
    for t,l in (("FN",FNs),("FP",FPs)):
        print(f"  {t}: {len(l)}"); [print(f"    {i} [{s}] {q}") for i,s,q in l[:10]]
    print("  (classe,origem)[ok,erro]:",{f"{a}/{b}":v for (a,b),v in sorted(fino.items())})
    return {r["id"]:pred(regra,feats[r["id"]]) for r in reps if r["id"] in feats}
preds=[avalia(r) for r in regras]
if len(preds)==2:
    disc=[i for i in preds[0] if preds[0][i]!=preds[1][i]]
    print(f"\nQUADRANTE DE DISCORDÂNCIA ({len(disc)}):")
    byid={r["id"]:r for r in reps}
    for i in disc[:15]:
        print(f"  {i} r1={preds[0][i]} r2={preds[1][i]} rotulo={byid[i].get('rotulo')} | {byid[i]['query'][:55]}")

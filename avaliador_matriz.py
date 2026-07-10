#!/usr/bin/env python3
"""avaliador_matriz.py — fase 2 exp012-v2. USO:
python avaliador_matriz.py gt_rotulacao.csv gt_features.csv "regra1" ["regra2"]
Regra = expressão Python sobre as features (ex.: "kw_continuidade and lineage_n_sources==0").
Emite matriz binária (quarentenar⟺VENENO_*), P/R/F1, FN/FP listados, recortes por classe
fina e origem, e quadrante de discordância entre 2 regras. AMBIGUO fica fora da matriz."""
import csv,sys
def load(p,skip_guide=False):
    rows=list(csv.DictReader((l for l in open(p,encoding="utf-8") if not (skip_guide and l.startswith("#")))))
    return {r["id"]:r for r in rows}
def coerce(v):
    if v in ("True","False"): return v=="True"
    if v in ("","AUSENTE"): return None
    try: return float(v) if "." in v else int(v)
    except Exception: return v
def pred(regra,f):
    env={k:coerce(v) for k,v in f.items()}
    try: return bool(eval(regra,{"__builtins__":{}},env))
    except Exception: return False
rotu=load(sys.argv[1],skip_guide=True); feats=load(sys.argv[2]); regras=sys.argv[3:]
assert regras,"passe 1-2 regras"
def avalia(regra):
    tp=fp=fn=tn=0; FNs=[];FPs=[]; fino={}
    for i,r in rotu.items():
        lab=r.get("rotulo","").strip()
        if not lab or lab=="AMBIGUO" or i not in feats: continue
        y=lab.startswith("VENENO"); p=pred(regra,feats[i])
        fino.setdefault((lab,r.get("origem","")),[0,0])[0 if p==y else 1]+=1
        if p and y: tp+=1
        elif p: fp+=1; FPs.append((i,r["query"][:60],r["resposta"][:60]))
        elif y: fn+=1; FNs.append((i,r["query"][:60],r["resposta"][:60]))
        else: tn+=1
    P=tp/max(tp+fp,1); R=tp/max(tp+fn,1); F=2*P*R/max(P+R,1e-9)
    print(f"\nREGRA: {regra}\n  TP={tp} FP={fp} FN={fn} TN={tn} | precision={P:.2f} recall={R:.2f} F1={F:.2f}")
    for t,l in (("FN (veneno escapou)",FNs),("FP (legitimo pego)",FPs)):
        print(f"  {t}: {len(l)}"); [print(f"    {i} | {q} | {a}") for i,q,a in l[:10]]
    print("  por (classe,origem) [acertos,erros]:", {f"{k[0]}/{k[1]}":v for k,v in sorted(fino.items())})
    return {i:pred(regra,feats[i]) for i in rotu if i in feats}
preds=[avalia(r) for r in regras]
if len(preds)==2:
    disc=[i for i in preds[0] if preds[0][i]!=preds[1][i]]
    print(f"\nQUADRANTE DE DISCORDÂNCIA ({len(disc)}):")
    for i in disc[:15]:
        print(f"  {i} r1={preds[0][i]} r2={preds[1][i]} rotulo={rotu[i].get('rotulo')} | {rotu[i]['query'][:60]}")

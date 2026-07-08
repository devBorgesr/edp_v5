#!/usr/bin/env python3
"""
exp012_calibracao.py — calibração pré-freeze da regra B (§2.3). SÓ LEITURA.
USO: $env:EDP_BASE_DIR="C:\\edp_data_hybrid_test"; python exp012_calibracao.py
Critério de congelamento: 6/6 lixo pegas E 0 falso-positivo nos legítimos.
Como entradas retroativas NÃO têm proveniência gravada, a rodada SIMULA o
carimbo de falha (n_mem_prompt=0) para TODAS — assim mede exatamente o braço
textual-auxiliar da regra (o gate de proveniência já é determinístico por
construção). Coluna extra mostra recusa_alta cru p/ diagnóstico.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edp.write_provenance import classify, eh_recusa_alta

LIXO  = ["9738c6f9","b4dbb2b1","b9cfb9c5","757b3aa2","7683deb4","3d34504c"]
LEGIT = ["4c57ed7a","0c78fa08","7c7d6ce9"]
LEGIT_NEEDLES = ["supernova","docker","kafka","bayes"]
CONVERSA_LEGITIMA = ("Q: você encontrou o arquivo relatorio_final.xlsx que te mandei?\n"
                     "A: Procurei nos anexos e não encontrei o arquivo relatorio_final.xlsx; "
                     "pode reenviar? Enquanto isso, segue o resumo do que já analisamos sobre ele.")

base = os.environ.get("EDP_BASE_DIR") or sys.exit("[ERRO] EDP_BASE_DIR não setado")
sid = os.environ.get("EDP_SESSION_ID","default")
entries=[]
for n in ("episodic.json","semantic.json"):
    p=os.path.join(base,"sessions",f"{sid}_cognitive",n)
    if os.path.exists(p):
        d=json.load(open(p,encoding="utf-8"))
        entries += (d.get("entries",d) if isinstance(d,dict) else d)
by8={str(e.get("id"))[:8]:e for e in entries if isinstance(e,dict)}

def linha(tag, i8, txt):
    cls = classify({"n_mem_prompt":0}, txt)      # proveniência de falha SIMULADA
    ok = (cls=="not_found") if tag=="LIXO" else (cls is None)
    print(f"  [{tag:6}] {i8:8} recusa_alta={eh_recusa_alta(txt)!s:5} classe={cls!s:9} -> {'ok' if ok else 'FALHA'}")
    return ok

print(f"store={base} | entries={len(entries)}")
res=[]
for i in LIXO:
    e=by8.get(i);  res.append(linha("LIXO", i, (e or {}).get("text","")) if e else print(f"  [LIXO  ] {i} AUSENTE"))
for i in LEGIT:
    e=by8.get(i);  res.append(linha("LEGIT", i, (e or {}).get("text","")) if e else print(f"  [LEGIT ] {i} AUSENTE"))
for nd in LEGIT_NEEDLES:
    e=next((x for x in entries if nd in (x.get("text") or "").lower() and str(x.get("id"))[:8] not in LIXO), None)
    res.append(linha("LEGIT", nd[:8], e.get("text","")) if e else print(f"  [LEGIT ] '{nd}' sem match no store"))
res.append(linha("LEGIT","conversa", CONVERSA_LEGITIMA))
ok=[r for r in res if r is not None]
print(f"\nCALIBRACAO: {sum(ok)}/{len(ok)} — {'100% ✓ REGRA PODE CONGELAR' if all(ok) else 'NAO congela: reportar e reavaliar (não afrouxar)'}")

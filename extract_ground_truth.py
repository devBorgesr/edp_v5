#!/usr/bin/env python3
"""extract_ground_truth.py — exp012-v2 Etapa 0 (seed=42). SÓ LEITURA de cópias.
USO: python extract_ground_truth.py C:\\edp_data_gt_extract C:\\edp_data_hybrid_test [C:\\edp_data_fase0]
Emite gt_rotulacao.csv (SEM sinais) + gt_features.csv (join por id). Nunca C:\edp_data."""
import csv, json, os, random, re, sys
random.seed(42)
KW = ["continuar","continuando","o que discutimos","o que conversamos","me lembra","lembra",
      "voltando ao","retomar","retomando","onde estávamos","o que você me explicou","última conversa","falamos"]
IDS_LIXO = ["9738c6f9","b4dbb2b1","b9cfb9c5","757b3aa2","7683deb4","3d34504c"]
IDS_LEG  = ["4c57ed7a","0c78fa08","7c7d6ce9"]
NEEDLES  = ["supernova","docker","kafka","bayes","ciclo estelar","aniversário","transformer"]
NEG = re.compile(r"n[ãa]o (encontro|tenho registro|h[áa] registro|localizo)", re.I)
QA  = re.compile(r"^Q:\s*(.*?)\nA:\s*(.*)$", re.S)
GUIA = ("# ROTULO em: VENENO_NEGACAO|VENENO_CONFABULACAO|LEGITIMO_CONHECIMENTO|"
        "LEGITIMO_META|LEGITIMO_CONTEUDO|AMBIGUO  (guia completo no RELATORIO_ETAPA0_EXP012V2.md)")
SINT = [("sint_neg_%d"%i,q,a) for i,(q,a) in enumerate([
 ("continuando nossa conversa sobre criptografia pós-quântica","Não encontro registro de conversa anterior sobre criptografia pós-quântica."),
 ("me lembra o que discutimos sobre grafos de conhecimento","Não tenho registro dessa discussão em nossas conversas."),
 ("voltando ao que falamos de compiladores JIT","Não há registro anterior sobre compiladores JIT entre nós.")])] + \
[("sint_meta_%d"%i,q,a) for i,(q,a) in enumerate([
 ("você encontrou o arquivo relatorio_final.xlsx?","Procurei nos anexos e não encontrei o relatorio_final.xlsx; pode reenviar?"),
 ("achou aquele dataset no drive?","Não localizei o dataset na pasta compartilhada; o link pode ter expirado."),
 ("encontrou o bug no log?","Não encontrei o erro no trecho de log enviado; mande o arquivo completo.")])] + \
[("sint_conf_%d"%i,q,a) for i,(q,a) in enumerate([
 ("continuando nossa conversa sobre orquestração","Claro! Como discutimos, você preferia Kubernetes com Helm; seguindo daquele ponto..."),
 ("retomando o plano de migração","Perfeito — retomando: tínhamos definido as 3 etapas na conversa passada..."),
 ("voltando ao seu projeto de drone","Sim! Da última vez avançamos no controle PID do seu drone; continuando...")])]

LINEAGE_TOL=120
def lineage_lookup(idx_sorted, ts):
    """registro mais proximo em ±LINEAGE_TOL s; None se nenhum (join por chave
    direta NAO existe — P4; igualdade exata de segundo era degenerada)."""
    if not idx_sorted or not ts: return None
    import bisect
    ks=[k for k,_ in idx_sorted]; i=bisect.bisect_left(ks,ts)
    best=None
    for j in (i-1,i):
        if 0<=j<len(idx_sorted) and abs(idx_sorted[j][0]-ts)<=LINEAGE_TOL:
            if best is None or abs(idx_sorted[j][0]-ts)<abs(best[0]-ts): best=idx_sorted[j]
    return best[1] if best else None

def lineage_idx(base):
    idx={}
    for r,_d,fs in os.walk(os.path.join(base,"lineage")) if os.path.isdir(os.path.join(base,"lineage")) else []:
        for f in fs:
            try:
                for ln in open(os.path.join(r,f),encoding="utf-8"):
                    try: d=json.loads(ln); idx[round(d.get("timestamp",0))]=d
                    except Exception: pass
            except Exception: pass
    return sorted(idx.items())

rot,feat=[],[]
def add(sid,store,origem,q,a,e,lin):
    rot.append(dict(id=sid,store=store,origem=origem,query=q.replace("\n"," ")[:500],
                    resposta=a.replace("\n"," ")[:1500],rotulo="",observacao=""))
    kws=[k for k in KW if k in q.lower()]
    ln=lineage_lookup(lin, e.get("timestamp") or 0) if e else None
    src=(ln or {}).get("source_entries") or []
    prov=(e or {}).get("ctx_provenance") or {}
    feat.append(dict(id=sid,n_mem_prompt=prov.get("n_mem_prompt","AUSENTE"),
        lineage_n_sources=(ln or {}).get("n_sources","AUSENTE"),
        lineage_score_max=(max((s.get("score",0) for s in src),default="AUSENTE") if ln else "AUSENTE"),
        negacao_textual=bool(NEG.search(a)),kw_continuidade=bool(kws),kw_acionadas="|".join(kws),
        len_resposta=len(a),len_query=len(q),timestamp=(e or {}).get("timestamp",""),
        epistemic_status=(e or {}).get("epistemic_status",""),prioridade=(e or {}).get("prioridade",""),
        score_gravacao=(e or {}).get("score_inicial",(e or {}).get("score","")),
        eh_session_summary=((e or {}).get("source_type")=="session_summary")))

censo={}
for base in sys.argv[1:]:
    assert os.path.basename(base.rstrip("/\\")).lower()!="edp_data","PRODUCAO E SAGRADA"
    lin=lineage_idx(base); tot=cand=0; por_gat={}; store=os.path.basename(base)
    resto=[]
    for scope in ("cognitive","sprint"):
        root=os.path.join(base,"sessions")
        for d in (os.listdir(root) if os.path.isdir(root) else []):
            if not d.endswith("_"+scope): continue
            for fn in ("episodic.json",):
                p=os.path.join(root,d,fn)
                if not os.path.exists(p): continue
                data=json.load(open(p,encoding="utf-8"))
                for e in (data.get("entries",data) if isinstance(data,dict) else data):
                    if not isinstance(e,dict): continue
                    m=QA.match(e.get("text") or "")
                    if not m: continue
                    tot+=1; q,a=m.group(1),m.group(2)
                    i8=str(e.get("id"))[:8]
                    ln0=lineage_lookup(lin, e.get("timestamp") or 0)
                    # gatilhos FORTES (v2 pos-censo-degenerado): lineage-baixo
                    # NAO qualifica (ausente e o estado normal do backlog — P4);
                    # entra so como FEATURE no gt_features.csv.
                    g_neg=bool(NEG.search(a))
                    g_kw=any(k in q.lower() for k in KW)
                    g_id=(i8 in IDS_LIXO or i8 in IDS_LEG
                          or any(nd in (e.get("text") or "").lower() for nd in NEEDLES))
                    g_lin=(ln0 is None or (ln0 or {}).get("n_sources")==0)  # feature/diagnostico
                    for k2,v2 in (("neg",g_neg),("kw",g_kw),("id",g_id),("lineage_baixo(nao-gatilho)",g_lin)):
                        por_gat[k2]=por_gat.get(k2,0)+int(v2)
                    if g_neg or g_kw or g_id: cand+=1; add(i8,store,"real",q,a,e,lin)
                    else: resto.append((i8,q,a,e))
    for i8,q,a,e in random.sample(resto,min(20,len(resto))):
        add(i8,store,"real",q,a,e,lin)
    censo[store]=dict(qa_total=tot,candidatas=cand,controles=min(20,len(resto)),por_gatilho=por_gat)
for sid,q,a in SINT: add(sid,"sintetico","sintetico",q,a,None,{})

with open("gt_rotulacao.csv","w",newline="",encoding="utf-8") as f:
    f.write(GUIA+"\n"); w=csv.DictWriter(f,fieldnames=list(rot[0])); w.writeheader(); w.writerows(rot)
with open("gt_features.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=list(feat[0])); w.writeheader(); w.writerows(feat)
print("CENSO:",json.dumps(censo,ensure_ascii=False),"| linhas rotulacao:",len(rot))

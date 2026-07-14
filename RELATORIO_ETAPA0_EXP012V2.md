# ETAPA 0 exp012-v2 — respostas de fonte, protocolo GT, instrumentos. ZERO regra/freeze/diff-hot-path.

## PARTE 1 — P1..P7 (verificado na fonte NESTA investigação; file:line)
**P1** Classificador de intenção de query: **NÃO EXISTE** (varreduras de session_summary/cognitive_decisions/echo_chamber/websocket/llm_adapter desta investigação). Escopo do gravador (websocket.py:1198-1218): `message`, `full_text`, `combined`, `runtime` (→ `_last_ctx_provenance`), `memory`, `camara_source`, `lineage_retrieved` (:717), `lineage_quality` (:651). Matéria-prima suficiente p/ features.
**P2** F2 hot-path — medir p/ provavelmente DESCARTAR: instrução entraria no SYSTEM_TEMPLATE (llm_adapter:786; 771 tokens, remaining≈146 pós-exp011 → +50-100 tokens de instrução = risco real de estouro). Remoção pré-usuário exigiria parse no stream; **CRÍTICO**: WS envia chunk-a-chunk (websocket, eventos `chunk`) — prefixo estruturado obriga bufferizar antes de exibir = latência percebida + mudança do protocolo de streaming. Custo alto, risco alto. **Recomendação: descartar em favor de P3.**
**P3** cognitive_decisions (auditado nesta investigação): (a) SIM — texto da entry = Q/A completo, cap 3000 chars (cognitive_decisions.py:340,73) + entry dict inteiro em mãos; (b) SIM, prompt JSON estrito de 3 campos (:77-93); +1 campo `answer_class` no MESMO call = custo marginal ~zero; quebra: `from_json_str` valida os 3 obrigatórios (:156-174) — precisa aceitar o campo novo (opcional, backward-compat trivial); (c) pendência = `cognitive_decisions is None` + janela 60s<idade<24h (:277-289, MAX_ENTRY_AGE_SEC=86400) → **backlog histórico NÃO re-enfileirável sem alargar a janela de 24h** (mudança de 1 constante; registrar como decisão da fase 2); (d) sem catch-up além da janela: desligou antes e passou de 24h → nunca classifica; janela típica 60-120s (tick 60s, max 3/tick); (e) aterrissa em `entry["cognitive_decisions"]` no episodic.json (persist :402, flush :487) — `answer_class` iria DENTRO desse dict.
**P4** lineage (runtime/lineage.py; build/persist websocket:1305-1313): campos = response_id, session_id, model_used, n_sources, source_entries[{entry_id, score, source_type, timestamp}], quality_score/verdict, timestamp. (a) associação à entrada de memória: **NÃO há chave direta** (lineage tem response_id; a memória do turno tem id próprio) — só por proximidade de timestamp (proxy fraco; registrar). (b) existe desde α Tier 3 (13/06/2026) — entradas anteriores SEM lineage. **LIMITAÇÃO REGISTRADA**: histórico sem ctx_provenance; lineage mede o que o retrieve DEVOLVEU, não o que chegou ao prompt (Fase 0) → classificador do backlog NÃO pode depender de n_mem_prompt; entra como feature quando existir.
**P5** SIM, distinguível: `_last_similarity_blocks` (candidatos) vs `_last_ctx_provenance.n_mem_prompt` (chegaram) — candidatos>0 e n_mem==0 = "havia e não chegou". **Registrado só como canário de regressão do read-path** (fronteira conceitual: fora do classificador).
**P6** Quarentena — mapa de leitura: (a) cosine: fator `nf_floor` (×NOT_FOUND_FLOOR=0.05) no produto de rank (memory.py, bloco anchor_boost — diff desta branch); (b) BM25: índice construído em `_hybrid_index` (memory.py, exp010) com **cache** por (scope, len_epi, len_sem, últimos ids) → tag escrita in-place SEM mudar contagem **NÃO invalida o cache** — quarentena só vale no próximo rebuild/restart (dívida já documentada; fase 2 decide: invalidação explícita ou aceitar latência); (c) RRF herda dos dois braços via exclusão NO BUILD do índice (diff desta branch) — sem tratamento próprio; (d) janela imediata (llm_adapter:2534-2556)/histórico (:2590)/bloco atual (:2319) leem episodic.entries direto — **quarentena NÃO os afeta**; escopo correto: mira recuperação entre sessões, a sessão corrente vê a resposta por design.
**P7** Corrida: instância ÚNICA de MemoryStore via registry (bug 3d-fix4 já resolveu a dualidade) → hot path (add+flush websocket:1218) e job background operam o MESMO objeto em RAM; writes serializados pelo GIL + save atômico; risco residual = interleaving flush job/hot-path sobrescrevendo — mesmo padrão já vivido pelo cog_dec (flush :487) sem incidente; registrar, monitorar na fase 2.

## PARTE 2 — protocolo (extrator entregue; CSVs nascem na SUA rodada, stores na sua máquina)
`extract_ground_truth.py` (seed=42): roda por store (gt_extract/hybrid_test/fase0; NUNCA C:\edp_data), escopos cognitive+sprint; extrai Q/A; censo de features; conjunto de rotulação pelos 4 gatilhos + amostra aleatória n=20; casos de fronteira sintéticos (3×3, origem=sintetico) embutidos; emite gt_rotulacao.csv (SEM sinais — rotulação independente) e gt_features.csv (join por id). Keywords v1 CONGELADAS no script. Contagens por store: preencher no relatório após sua rodada.

## GUIA DE ROTULAÇÃO (CONGELADO; também no topo do CSV)
VENENO_NEGACAO (nega memória/registro/contexto pedido — negação honesta ENTRA) · VENENO_CONFABULACAO (afirma continuidade sem base) · LEGITIMO_CONHECIMENTO · LEGITIMO_META (não-achar objeto EXTERNO/meta-conversa) · LEGITIMO_CONTEUDO · AMBIGUO (fora da matriz). Binário: quarentenar ⟺ VENENO_*. Otimiza RECALL de VENENO_* (veneno compõe; quarentena é downweight reversível, nunca delete).

## PRÉ-REGISTRO (predições da fase 2 — verbatim)
- PR-1: a auto-declaração do modelo (F2) terá recall alto em VENENO_NEGACAO e recall BAIXO em VENENO_CONFABULACAO (o modelo não sabe que confabula).
- PR-2: kw_continuidade terá recall alto em ambos os venenos, com falsos positivos em LEGITIMO_META.
- PR-3: a fusão OU (kw_continuidade OR declaração) no estrato de proveniência baixa maximiza recall com precisão aceitável; a fusão E perde a confabulação.
- PR-4: nenhum sinal isolado atinge 100% — a regra final será composta.

Integridade: zero regra, zero freeze, zero diff no hot path, zero API, produção intocada. PARADO para rotulação humana.

## ADENDO (pré-rotulação) — regras congeladas da matriz + PR-5
REGRAS (congeladas ANTES de qualquer rótulo visto): R1 `negacao_textual` | R2 `kw_continuidade` | R3 `negacao_textual and kw_continuidade` | R4 `negacao_textual or kw_continuidade`.
- **PR-5:** no quadrante neg=F ∧ kw=T, confabulação e continuação-bem-sucedida são inseparáveis sem proveniência; o backlog pagará FPs em continuações (aceitável pelo custo assimétrico); entradas novas usarão `kw AND n_mem_prompt==0`.
Matriz roda no `avaliador_matriz.py` v2: dedup por conteúdo (hash de Q+A normalizados; gt_extract/fase0/hybrid_test compartilham população), duplicatas com rótulos DIVERGENTES listadas e EXCLUÍDAS (fronteira instável = informação), contagens N bruto/pós-dedup/excluído/AMBIGUO.

## CORREÇÃO PÓS-ROTULAÇÃO (pré-matriz fase 2)
`sint_meta_0/1/2` rotulados por engano como VENENO_NEGACAO → corrigidos para **LEGITIMO_META**. Motivo: negam objeto EXTERNO (arquivo/anexo/log), não a própria memória episódica — são os controles de fronteira negativa do conjunto. Quarentená-los premiaria exatamente o comportamento que a regra deve evitar. `observacao` de `96a26e9b` (Java Records): verificada — já estava vazia no arquivo corrente (sem conteúdo cruzado de outro caso a remover); nenhuma edição necessária nesse campo.

## FASE 2 — MATRIZ (pós-correção, N=193 bruto / 97 pós-dedup, 0 inconsistências, 0 AMBIGUO)

| Regra | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| R1 `negacao_textual` | 12 | 0 | 14 | 71 | 1.00 | 0.46 | 0.63 |
| R2 `kw_continuidade` | 15 | 2 | 11 | 69 | 0.88 | 0.58 | 0.70 |
| R3 `negacao_textual and kw_continuidade` | 9 | 0 | 17 | 71 | 1.00 | 0.35 | 0.51 |
| R4 `negacao_textual or kw_continuidade` | 18 | 2 | 8 | 69 | 0.90 | 0.69 | 0.78 |

Recorte fino classe×origem (ok,erro): LEGITIMO_META real=[7,0] sintetico=[3,0] em **todas as 4 regras** — os controles de fronteira corrigidos não geram FP em nenhuma variante, validando a correção.

Quadrante de discordância R1×R2 (11 casos): 8 em neg=F∧kw=T (5 VENENO_CONFABULACAO, 2 LEGITIMO continuação-bem-sucedida, 1 VENENO_NEGACAO) — confirma PR-5 (confabulação e continuação inseparáveis por kw isolado). 3 em neg=T∧kw=F.

**Veredito PR-1..PR-5:**
- PR-1: NÃO TESTÁVEL — F2 (auto-declaração) foi descartado na Parte 1 (P2), não existe em `gt_features.csv`.
- PR-2: REFUTADA — recall de kw_continuidade em ambos os venenos é moderado (44–71% por classe/origem, não "alto"), e o FP-em-LEGITIMO_META previsto não ocorreu (0 erros nas 10 linhas META).
- PR-3: CONFIRMADA (com ressalva) — a fusão OU eleva recall de confabulação de 0% (AND) para 71%, com precisão ainda aceitável (0.90); estratificação por proveniência baixa (`n_mem_prompt`) não foi rodada nesta passada.
- PR-4: CONFIRMADA — nenhum sinal isolado (R1, R2) atinge performance perfeita; a regra composta (R4/OR) supera ambos em F1.
- PR-5: CONFIRMADA — no quadrante neg=F∧kw=T, confabulação e continuação legítima aparecem misturadas (5 vs 2), e os 2 FP de R2/R4 são exatamente continuações legítimas.

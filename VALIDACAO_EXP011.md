# exp011 — VALIDAÇÃO (critérios §1.4 congelados no pré-registro da Fase 1)

## Veredito oficial §1.4 (rodada do pesquisador, C:\edp_data_fase0, 0 negações): SUCESSO
- OFF: CP3 MORRE — 'chave-valor' False, ids no prompt=[], retrieval_tokens=294 (reprodução do Defeito 1; default intocado).
- ON:  CP3 VIVE — 'chave-valor' True, 5 ids Redis no prompt, retrieval_tokens=1610,
  retrieval_kept=[249,149,69,116,597, 1208,802,1269,782,1208] = 5 metadados FORA da contagem + 5 memórias reais NO slot.

## Guardas (§1.5)
| # | guarda | veredito | evidência |
|---|---|---|---|
| 1–3 | âncora/histórico/bloco atual presentes | PASSA | lidas nos dois prompts da rodada oficial |
| 4 | janela imediata intocada | PASSA (rebalanceamento) | diff não toca a janela; recent 4→3 vem do Nível 4 do manager (context_window_manager.py:316-322, inalterado): budget consumido pelas memórias (294→1610) acomoda 1 turno a menos. Lógica da janela: zero mudança |
| 5 | query sem match | mecânica PASSA (fixture, OFF e ON: sem crash, âncora presente, remaining≥0) | rodada oficial: `python exp011_guardas.py --guarda5` sobre a cópia fase0 |
| 6 | regressão ground truth exp009/010 | mecânica PASSA (fixture: todo alvo CP1→CP3 com ON, 0 falhas) | rodada oficial: `python exp011_guardas.py --guarda6` |

## Registro de tensão (não é bug do exp011 — só anotado)
Com ON o remaining fechou em 147 no store real: system prompt de 771 tokens +
memórias grandes apertam a janela de 4096. Dívida do system prompt obeso, já anotada.

## Estado
exp011 VALIDADO no §1.4 + guardas 1–4; guardas 5–6 com mecânica verificada e runner
entregue (`exp011_guardas.py`) para a rodada oficial no store fase0. Teste vivo §1.6 =
rodada do pesquisador (cópia descartável). exp012 segue BLOQUEADO (§2.0) até a
confirmação final. Produção intocada; sem PR; sem merge.

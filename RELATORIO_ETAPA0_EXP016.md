# exp016 — Etapa 0: 3ª classe de veneno (desqualificação auto-referente)

Só leitura e desenho. Nenhum diff no runtime, nenhuma execução contra store
(não há stores nesta VM — o dry-run é do Daniel, contra `C:\edp_data_exp016`).

**Contexto (exp015 REFUTADO, 14/07):** cabeçalho de proveniência física +
proibição explícita no system prompt não impediram o modelo de reafirmar
desqualificação presente na janela imediata ("foram fabricadas por mim"). O
veneno não se vence por prompt — se remove do contexto. exp016 estende o
mecanismo já provado do exp012 (`answer_class` → peso-piso + exclusão do
híbrido) para esta 3ª classe.

Exemplares conhecidos (13/07, 00h52–56, `episodic.json`, backup
`sessions_backup_exp013`, ~linhas 61871 e 62297): "Tudo isso **eu inventei**
para preencher um vazio" e "foi fabricada por mim em turno anterior — eu a
inventei".

---

## P1 — Semântica do gate

`edp/memory.py` checa **igualdade literal de string**, não presença/truthiness,
nos dois pontos que o exp012 já usa:

**Peso-piso**, dentro de `EpisodicMemory.retrieve()`:
```
# memory.py:717-719
# exp012 (EDP_WRITE_PROVENANCE): peso-piso p/ answer_class=not_found
from .config import EDP_WRITE_PROVENANCE as _WP, NOT_FOUND_FLOOR as _NF
nf_floor = _NF if (_WP and e.get("answer_class") == "not_found") else 1.0
```

**Exclusão do índice híbrido**, dentro de `MemoryStore._hybrid_index()`:
```
# memory.py:1731-1735
# exp012: not_found fora do índice híbrido (piso operacional;
# a entry NÃO é deletada — segue no store e no cosine com piso)
from .config import EDP_WRITE_PROVENANCE as _WP12
if _WP12 and e.get("answer_class") == "not_found":
    continue
```

Ambos usam `== "not_found"` (comparação de string exata), não `if e.get("answer_class")`
nem `.get(..., False)`. Consequência prática: um terceiro valor (ex.:
`"disqualification"`) hoje **não** aciona nenhum dos dois — passa batido.

**Ajuste mínimo proposto (NÃO implementado nesta etapa):** trocar a comparação
pontual por pertencimento a um conjunto de valores tóxicos:
```python
TOXIC_ANSWER_CLASSES = {"not_found", "disqualification"}
...
nf_floor = _NF if (_WP and e.get("answer_class") in TOXIC_ANSWER_CLASSES) else 1.0
...
if _WP12 and e.get("answer_class") in TOXIC_ANSWER_CLASSES:
    continue
```
Dois pontos de edição, mesmo padrão dos dois já existentes. Sem overload de
`NOT_FOUND_FLOOR` — a decisão de usar o mesmo piso 0.05 ou um piso próprio p/
desqualificação fica para o pesquisador (a exp012_fase4_backfill_apply.py já
documentou a assimetria abaixo, que se herda 1:1 para a 3ª classe).

**Achado herdado do exp012 fase 4 (relevante para exp016, não novo):**
`SemanticMemory.retrieve()` (memory.py:1212-1263) não lê `answer_class` — o
peso-piso só cobre `episodic`. A exclusão do híbrido cobre as duas camadas
igualmente. Isso já vale para `not_found` hoje e valeria do mesmo jeito para
`disqualification` amanhã: se `EDP_HYBRID_RETRIEVAL` for desligado, cópias
semânticas desqualificadas voltam a competir sem piso algum.

---

## P2 — Vetor janela imediata (mapeado, não alterado)

`edp/llm_adapter.py`, método `_retrieve_context()`, bloco "Janela imediata"
(linhas ~2139-2280). Fluxo:

1. `real_entries` = todos os `episodic.entries` do scope ativo, exceto
   `source_type == "session_summary"`, ordenados por `timestamp` (linha 2187-2193).
2. `recent_entries = real_entries[-JANELA_IMEDIATA_N:]` com `JANELA_IMEDIATA_N = 6`
   (linha 2157, 2221) — sempre os N mais recentes por timestamp, sem nenhum
   filtro de conteúdo.
3. Cada entry vira bloco de texto (`[label] txt`, linha 2269) com cap de chars
   por posição — nenhuma outra checagem.

**Confirmado por leitura linha a linha: em nenhum ponto deste bloco há leitura
de `e.get("answer_class")` ou de qualquer campo de proveniência/toxicidade.**
A janela imediata é cega a `answer_class` por construção — é decisão de design
diferente (Peça 2.5a, "sem thread cognitiva = condescendência crônica"),
anterior e ortogonal ao mecanismo do exp012.

**Decisão de design PENDENTE do pesquisador — não implementar sem autorização:**
o piso/exclusão do exp012 nunca poderia, por si, impedir a reafirmação vista
no exp015, porque a entry desqualificante de 00h52-56 caía dentro da janela
imediata (N=6) de um turno próximo, que ignora `answer_class`. Duas rotas
possíveis, **nenhuma escolhida aqui**:
  - (a) censurar a janela imediata por `answer_class` — rejeitada
    preliminarmente: a janela imediata existe justamente para preservar
    "Não tenho base sólida" e outras auto-admissões de limite (Peça 2.5a);
    filtrar por conteúdo nela reabre o risco que a Peça 2.5a foi desenhada
    para fechar.
  - (b) **recomendação desta etapa**: fechamento por envelhecimento — não
    mexer na janela imediata; deixar N=6 turnos "empurrarem" a entry
    desqualificante para fora naturalmente conforme a conversa avança com
    turnos neutros antes da query-alvo. O piso+exclusão do exp016 cobre o
    retrieval por similaridade (que é onde a entry reapareceria depois de
    sair da janela); a janela imediata cobre-se sozinha pelo tempo.

---

## P3 — Detector DISQ v1 (regra congelada ANTES de qualquer dado)

Mesma disciplina do exp012 (lista congelada, comentário de rastreabilidade,
robustez a acento/cp1252 no mesmo estilo do `NEG` já existente em
`edp/write_provenance.py:31`):

```python
import re

# exp016 P3 (congelado ANTES do dry-run, 15/07/2026) — detector DISQ v1.
# Regex sobre o texto da RESPOSTA. Exemplares-fonte: episodic.json,
# backup sessions_backup_exp013, ~L61871 e ~L62297 (13/07 00h52-56).
# [ãa]/[óo]/[íi]/[êe] no mesmo padrão de NEG (write_provenance.py:31) —
# robustez a mangling cp1252 (ver df0e3fa).
DISQ_PATTERNS = [
    re.compile(r"fabricad[ao]s?\s+por\s+mim", re.I),
    re.compile(r"eu\s+(a\s+|as\s+)?inventei", re.I),
    re.compile(
        r"n[ãa]o\s+(é|são|e|sao)\s+(uma\s+)?"
        r"mem[óo]ri(a|as)\s+(sua|genu[íi]na|aut[êe]ntica|real)",
        re.I,
    ),
    re.compile(r"n[ãa]o\s+corresponde\w*\s+a\s+nenhuma\s+pergunta\s+sua", re.I),
]

def disq_features(resposta: str) -> dict:
    """Retorna {padrao_idx: bool} — NÃO decide sozinho, só coleta (Fase 4)."""
    return {i: bool(p.search(resposta or "")) for i, p in enumerate(DISQ_PATTERNS)}
```

Sem colisão verificada com `NEG` (`n[ãa]o (encontro|tenho registro|h[áa]
registro|localizo)`, write_provenance.py:31) — nenhum dos 4 padrões DISQ
compartilha os verbos `encontro/tenho/há/localizo`.

`cognitive_decisions.key_assertion` é coletado como **sinal auxiliar** quando
existir (mesmo padrão da Fase 4 do exp012: coleta, não decide nesta fase).

---

## P4 — Predições pré-registradas (escritas antes do dry-run do Daniel)

**DEVE pegar:** as 2 desqualificações de 13/07 00h52-56, em todas as camadas
onde existirem (episodic e/ou semantic, cognitive e/ou sprint — a regra R4 do
exp012 já mostrou que a mesma entry pode ter cópias em mais de uma camada).

**NÃO DEVE pegar:**
  - os 3 conteúdos Redis genuínos (`4c57ed7a`, `0c78fa08`, `7c7d6ce9`);
  - os 10 `LEGITIMO_META`;
  - o summary tóxico `31162822` — é **1ª geração** (confabulação de
    continuidade), não desqualificação; **se a regex DISQ o capturar, é
    achado a reportar no dry-run, não a esconder ou ajustar a regra
    retroativamente**;
  - as negações de 1ª classe já carimbadas pela regra R4/NEG do exp012 (a
    regex DISQ não deve colidir — verificado acima, mas o dry-run confirma
    empiricamente);
  - correções/retratações humanas (texto do usuário, não do modelo — o
    detector roda sobre resposta do assistente).

Qualquer divergência do dry-run em relação a estas predições é achado, não
motivo para reescrever a regra antes de reportar.

---

## P5 — `exp016_dryrun.py`

Escrito no molde exato de `exp012_fase4_backfill_dryrun.py` (mesma leitura
JSON pura, sem importar `edp.memory`; mesmo guard anti-produção; mesma
assinatura de `find_candidatas` importável). Ver arquivo no repo. **Não
executado nesta sessão** — não há store nesta VM.

## P6 — Cobertura do `key_assertion` (mapeado, não rodado)

`edp/runtime/cognitive_decisions.py`, `_select_pending_entries()`
(linhas 228-293). Filtros (todos AND):
  - `layer == "episodic"` (linha 273) — **semantic nunca é varrido por este
    extractor**, em nenhum scope.
  - fonte é sempre `cog_view = mem._cognitive_view` (linha 257) — **scope
    sprint nunca é varrido por este extractor** (docstring do módulo, linha
    6-8: sprint já tem extração própria via comentário HTML embutido na
    resposta do modelo, mecanismo totalmente separado).
  - `source_type == "llm_response"` (linha 275).
  - `e.get(FIELD_COGNITIVE_DECISIONS) is not None` → pula (linha 277) — só
    processa entries **sem** o campo, nunca reprocessa.
  - janela de idade: `tsf >= now - 86400` **e** `tsf <= now - 60`
    (linhas 267-268, 286-289) — só entries com 60s a 24h de idade no momento
    do tick.

**Consequência para as 2 desqualificações-alvo:** hoje é 15/07; as entries são
de 13/07 00h52-56 — já saíram da janela de 24h há muito. Mesmo que o job
tivesse rodado normalmente naquela madrugada, o registro de pressão
(`suspend_on_pressure=True`, linha 533 do módulo) suspende o job em
pressure=WARNING+; a hipótese de trabalho é que **pressure=CRITICAL na
madrugada de 13/07 suspendeu os ticks**, então essas entries provavelmente
**nunca tiveram `cognitive_decisions` extraído** — nem na janela de 24h nem
depois (o filtro de idade as excluiria permanentemente do polling normal).
O dry-run (`ja_tem_answer_class`/`key_assertion` no output de
`find_candidatas`) confirma ou refuta isso empiricamente.

**Script desenhado (NÃO executado) para popular `key_assertion` offline, caso
o dry-run confirme a ausência:** ver `exp016_cognitive_decisions_backfill.py`
no repo — molde de `exp012_fase4_backfill_apply.py` (backup obrigatório antes
de escrever, idempotente, auditoria própria, guard anti-produção), mas exige
**autorização explícita separada do pesquisador antes de qualquer execução**,
porque faz 1 chamada Haiku por candidata (custo, não é leitura pura).

**Custo estimado:** reusa o mesmo prompt/modelo de
`CognitiveDecisionsExtractor` (~$0.001/extração, conforme docstring do
módulo, linha 13). Para N candidatas cognitive/episodic sem `key_assertion`
identificadas pelo dry-run, custo ≈ `N × $0.001`. Nas Fases anteriores do
exp012 esse N ficou na casa de 12-16 — custo esperado ≈ $0.01-0.02, mas o
número real só se confirma com o dry-run do Daniel.

---

## Resumo do que fica pronto para o dry-run do Daniel

1. `exp016_dryrun.py` — só lista, nenhuma escrita, guard anti-produção.
2. `exp016_cognitive_decisions_backfill.py` — desenhado, **não autorizado a
   rodar** nesta etapa (requer autorização explícita separada, como no exp012
   fase 4).
3. Regra DISQ v1 congelada (P3) e predições (P4) — registradas antes de
   qualquer dado real.
4. Ajuste mínimo do gate (P1) e decisão pendente da janela imediata (P2) —
   propostos, **nenhum implementado**.

PARADO aqui, conforme instrução.

# Pré-registro — Experimento 009
## Despriorizar `session_summary` corrige a dominância no retrieve sem quebrar o caso legítimo?

**Bancada de Contexto — EDP.** Categoria **RETRIEVAL-QUALITY** (inaugurada pelo
exp008): mede o que o retrieve **SELECIONA**, antes do modelo. **O LLM nunca é
chamado. Custo de API zero.**

> **Régua da Bancada:** este documento declara hipótese, condições, dataset,
> métricas e limiares de decisão **ANTES de qualquer dado**. A encarnação
> (`exp009.py`) espelha este `.md` e é **CONGELADA após o 1º disparo real**.
> Anti-mock: retrieve REAL sobre memórias REAIS (cópia da produção). Produção e
> código de produção intocados.

Data de pré-registro: **2026-07-02** (antes do disparo). Congelar ao primeiro fire.

---

## §1. Contexto provado (não re-derivado; código + medição real)

**Mecanismo (código):** `session_summary` nasce com `prioridade="alta"` (×1.30) e
`epistemic_status="verified"` (escapa do desconto ×0.85) — `session_summary.py:244,247`;
ganha `src_weight` ×1.15 (`memory_classifier.py:144`); `access_boost = 1+0.05·ln(1+n)`
(`temporal.py:42`) com **feedback loop** (o retrieve muta `acessos++`,
`memory.py:871`); fórmula de 9 fatores em `memory.py:739-743`. Único freio:
`dom_penalty` 0.70.

**Magnitude (medição real — `measure_ss_dominance.py` sobre CÓPIA da produção,
isolamento verificado):** `session_summary` = **45/177 entries (25% do store)**;
acessos médios **13.9 vs 4.2** das normais (máx **85**). Ocupam **86.7% do top-5**
em queries vagas de continuação e **73.3%** em específicas. O alvo real do Redis
(memória de conteúdo) foi **EXPULSO do top-5 em 2/3 queries** — superado por
summaries fragmentados (`"| Redis | Memcached |"`) e até **VAZIOS** (`"Nada. Esta
é a primeira mensagem da conversa"`, score 0.584).

**Nota sobre `session_boost`:** na medição acima, as SS antigas provavelmente
pegaram `session_boost` ×0.85/×1.0 (markers de sessões antigas) — e dominaram
MESMO ASSIM. Em conversa viva (summary da sessão atual, ×1.60) tende a ser pior.
**O dry-run deste experimento reporta qual `session_boost` as SS recebem no
ambiente da medição** (distribuição ×1.60/×0.85/×1.0), para leitura honesta.

---

## §2. Hipótese (declarada antes do dado)

- **H1:** Remover os **privilégios de nascença** das `session_summary`
  (prioridade alta + verified) **reduz drasticamente** a fração delas no top-5 e
  **devolve as memórias-alvo de conteúdo ao top-5**, sem eliminar o
  `session_summary` do **caso legítimo** (query que pede explicitamente um resumo).
- **H0:** A dominância **persiste** mesmo sem os privilégios — ou seja, vem do
  **embedding genérico/acessos**, não dos boosts de nascença. (H0 vencer também é
  achado: aponta o fix para outro lugar — embedding/canal de injeção.)

---

## §3. Condições (mesmo store clonado, mesmas queries)

Toda manipulação é de **LEITURA no CLONE** (arquivos `episodic.json`/`semantic.json`
da cópia de trabalho, editados **após o restore e antes de instanciar o
MemoryStore** de cada rodada). Nunca no snapshot, nunca na produção, nunca em
`edp/*.py`.

| rótulo | manipulação no clone |
|---|---|
| `baseline` | nenhuma — entries exatamente como no snapshot |
| `trat_gravador` | toda entry `source_type=="session_summary"`: `prioridade→"media"`, `epistemic_status→"hypothesis"` (o que o fix no gravador produziria; nada mais muda) |
| `trat_trivial` | SS **TRIVIAIS** (regra §3a) são **removidas do índice de retrieval** (excluídas dos JSONs do clone) |
| `trat_combinado` | gravador + trivial juntos |
| `trat_gravador_srcw` **(§EXPLORATÓRIO)** | gravador + `src_weight` neutro (1.0) para SS — isola a contribuição do `src_weight`. Implementado por patch **em memória, no processo do lab** (`SOURCE_TYPE_WEIGHTS["session_summary"]=1.0`, restaurado ao fim da condição); nenhum arquivo de produção tocado. Gera hipótese, **não** entra no critério confirmatório. |

### §3a. Regra de "trivial" (CONGELADA)

Após remover o prefixo `[session_summary]` e normalizar espaços
(`re.sub(r"\s+"," ",·).strip()`), a SS é **trivial** se:
- `len(texto_util) < 80` caracteres; **OU**
- o texto (minúsculas) **começa com `"nada"`**; **OU**
- contém `"primeira mensagem da conversa"`.

**O dry-run LISTA quais entries a regra pega** (id + preview), para revisão
humana **antes** de armar. Se a regra pegar entries legítimas, ajusta-se ANTES do
disparo (e o ajuste fica registrado aqui); depois do disparo, congelada.

### Identificação de SS nas métricas

O conjunto de ids SS é capturado **do snapshot pristine** e a contagem nas
métricas é **por id** (robusto a qualquer edição de campos nas condições).

---

## §4. Dataset (CONGELADO)

### 4a. VAGAS de continuação (n=6, inalteradas do `measure_ss_dominance.py`)
1. `"vamos continuar nossa conversa"`
2. `"continuando o que falávamos"`
3. `"o que a gente tinha concluído mesmo?"`
4. `"me lembra o que discutimos"`
5. `"voltando ao que estávamos vendo"`
6. `"sobre o que conversamos até agora"`

### 4b. ESPECÍFICAS do Redis (n=3; ids reais já validados na medição)
Alvo = **qualquer** destas 3 memórias de conteúdo (recuperar qualquer uma conta):
- `0c78fa08-8a51-4a04-ad15-2b23d0800a0b`
- `a5ef2402-c1c0-4404-93c1-5b23bf8e2a3e`
- `4c57ed7a-c275-4155-93eb-e1efa5a164d5`

Queries (linguagem natural, como já usadas na medição):
1. `"vamos continuar a conversa sobre Redis e Memcached"`
2. `"me lembra o que a gente concluiu sobre cache de sessões web com Redis"`
3. `"voltando ao assunto do Redis para sessões web"`

### 4c. ESPECÍFICAS de outros tópicos (n=6; alvo por needle NÃO-SS)
Needle resolve para memórias **não**-`session_summary` (validado no dry-run; se
ambíguo/0-match, o pesquisador troca por `id` explícito **antes de armar**).
Queries em linguagem **NATURAL**, nunca "domínio-puro" (lição do exp008):

| query | needle |
|---|---|
| `"continuando nossa conversa sobre transformers e atenção em LLMs"` | `transformer` |
| `"me lembra o que vimos sobre FAISS e busca vetorial"` | `faiss` |
| `"voltando ao que discutimos sobre embeddings de frases"` | `embedding` |
| `"sobre o RAG e as alucinações que a gente discutiu"` | `rag` |
| `"continuando o papo sobre desempenho de Python em tempo real"` | `python` |
| `"me lembra da nossa discussão sobre memória episódica do EDP"` | `episódic` |

*(As linhas de 4c são as constantes editáveis do `exp009.py` — o pesquisador
ajusta needles/ids ao conteúdo real do store DELE no dry-run; congela ao armar.)*

### 4d. GUARDA — caso legítimo (n=3)
Queries que **pedem explicitamente** resumo/consolidação. Nelas, um
`session_summary` no top-5 é **SUCESSO** (a guarda protege contra over-correção):
1. `"me dá um resumo do que consolidamos"`
2. `"o que ficou registrado como resumo da sessão passada?"`
3. `"qual foi o resumo das nossas últimas sessões?"`

---

## §5. Métricas (por condição; Wilson 95% via `edp.lab.scorer._wilson`)

- **%SS no top-5 e top-10**, separado **vagas / específicas**. Proporção de slots:
  `k = Σ slots SS`, `n = 5·(nº queries)` (idem top-10). Queda esperada vs baseline
  (referência da medição: 86.7% vagas / 73.3% específicas).
- **Recall@5 do alvo específico** (`k = queries com alvo no top-5`, `n = queries
  com alvo resolvido`) e **MRR** (1/rank, 0 se fora do top-10). Subida esperada;
  **Redis de volta ao top-5**.
- **Guarda:** nas queries de resumo (4d), fração com **≥1 SS no top-5** — não pode
  zerar.
- **Exemplos completos** de top-5 por (condição × query) gravados no prontuário
  (campo `respostas`): id, rank, score, source_type, is_target, is_ss, preview.
  **O número não é o achado.**

---

## §6. Critério de decisão (limiares CONGELADOS antes do disparo)

Avaliado pós-coleta (`--score`), só registros REAIS do experimento `009`:

**H1 CONFIRMADA** se existir condição C ∈ {`trat_gravador`, `trat_combinado`} tal
que **todas** as três guardas passem em C:
1. **%SS top-5 (vagas) < 40%** (vs 86.7% da medição baseline);
2. **alvo do Redis no top-5 em ≥ 2/3** das queries 4b;
3. **guarda intacta:** em **≥ 1** das queries de resumo (4d), há **≥1 SS no
   top-5** (o caso legítimo não zera).

**H0 VENCE** se em `trat_combinado` a dominância persistir (**%SS top-5 vagas ≥
40%** ou Redis fora do top-5 em ≥2/3): o problema é embedding/acessos, e o fix
correto é outro (canal de injeção separado / embedding). Qualquer outro resultado:
reportar exatamente **qual guarda falhou** — dado válido, decisão informada.

Comparações baseline-vs-tratamento reportadas com ICs de Wilson; a separação de
ICs é **informativa** (o critério confirmatório são os limiares acima, declarados
antes por serem legíveis e acionáveis para decisão de fix).

---

## §7. Anti-mock, isolamento e mecânica (herdada e citada)

- **Retrieve REAL:** `MemoryStore(...).retrieve(query, top_k=10, min_score=0.0)`
  (`edp/memory.py:1612`) — o mesmo do hot path. Nada reimplementado.
- **Isolamento por cópia** (padrão `measure_ss_dominance.py`): `EDP_BASE_DIR`
  aponta para uma **CÓPIA** da produção (guarda recusa basename `edp_data` sem
  `ALLOW_PROD=1`). O sujeito É o conteúdo do store de produção, então (diferente
  do exp008) não se usa sessão `__lab__` vazia — o isolamento é: cópia + snapshot
  pristine + **restore antes de CADA query** (o retrieve muta e salva,
  `memory.py:871-880`) + verificação final de **no-divergência** (hash do dir ==
  snapshot).
- Modelo de embedding carregado **UMA vez** no processo (singleton de
  `edp.embeddings`).
- **Trava `EDP_LAB_ARMED=1`** para o disparo real. `--dry-run` = prova-no-espelho:
  mostra pares query→alvo (com resolução de needles), **as entries que
  `trat_trivial` pegaria**, e **qual `session_boost` as SS recebem** no ambiente.
  `--score` = métricas+Wilson+veredito vs §6. `--audit` = exemplos de top-5.
- `record_run` no prontuário (padrão da Bancada), `dry_run=True` marcado no
  andaime quando aplicável (o scorer ignora dry-runs).

## §8. Constantes congeladas (espelhadas em `exp009.py`)

| constante | valor |
|---|---|
| `EXPERIMENTO` | `"009"` |
| `TOP_K` / `MIN_SCORE` | `10` / `0.0` |
| condições | `baseline`, `trat_gravador`, `trat_trivial`, `trat_combinado` (+ `trat_gravador_srcw` exploratória) |
| trivial | `len<80` úteis OU começa com `"nada"` OU contém `"primeira mensagem da conversa"` |
| limiar H1 | %SS top-5 vagas `< 40%` E Redis top-5 `≥ 2/3` E guarda `≥ 1` resumo c/ SS |
| ids Redis | `0c78fa08…`, `a5ef2402…`, `4c57ed7a…` (§4b) |
| gravador | `prioridade→"media"`, `epistemic_status→"hypothesis"` |

**CONGELADO ao primeiro disparo real. Mudou a régua → é o Experimento 010.**

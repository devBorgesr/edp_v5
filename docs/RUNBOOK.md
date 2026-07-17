# RUNBOOK — EDP v3.5

Pra quando é 2h da manhã e o processo morreu. Comandos copiáveis, sem
enrolação. PowerShell (host de produção é Windows) salvo indicação
contrária.

---

## 1. Boot

```powershell
$env:EDP_BASE_DIR = "C:\edp_data"
python run.py serve
```

Leva **~40-45s** — o warmup do modelo de embedding (`warmup_model()` em
`edp/embeddings.py`) é síncrono no startup de propósito, pra eliminar
cold start no primeiro turno real. Não é travamento; é esperado.

No log de boot, confira a linha do pressure governor:

```
[pressure] psutil OK | critical=0.3GB warning=0.6GB
```

Se os valores impressos **divergirem** de `critical=0.3GB warning=0.6GB`,
há um override de ambiente ativo — vá para §2 AGORA, antes de investigar
qualquer outra coisa. Esse log é a fonte de verdade sobre o que o
processo está USANDO, não o que está no código-fonte.

Depois do boot, confirme o estado real (não assuma pelo banner sozinho):

```powershell
curl http://localhost:8000/health
```

`boot_state` deve ser `"ready"`. Se vier 503 ou `"warming"`/`"starting"`
por mais que ~1 min depois do boot terminar, o startup travou em algum
componente — olhe o log de `[startup]` no console do processo.

---

## 2. Fantasma de env — SEMPRE checar primeiro

**Incidente real, 17/07/2026:** `EDP_PRESSURE_CRITICAL_GB=1.0` e
`EDP_PRESSURE_WARNING_GB=1.5` ficaram persistidas em escopo **User** e
derrotaram silenciosamente os defaults do repo (0.30/0.60) por semanas.
O boot NUNCA avisou — só o log `[pressure] psutil OK | critical=...`
mostrava o valor efetivo, e ninguém olhou até o sintoma (CRITICAL quase
permanente) aparecer nos smokes.

```powershell
# 1. O que está setado NA SESSÃO ATUAL do shell:
Get-ChildItem Env:EDP_PRESSURE*

# 2. O que está persistido nos escopos User/Machine (sobrevive a reboot,
#    é a causa mais provável de "o código está certo mas o boot não bate"):
[System.Environment]::GetEnvironmentVariable("EDP_PRESSURE_CRITICAL_GB", "User")
[System.Environment]::GetEnvironmentVariable("EDP_PRESSURE_WARNING_GB",  "User")
[System.Environment]::GetEnvironmentVariable("EDP_PRESSURE_CRITICAL_GB", "Machine")
[System.Environment]::GetEnvironmentVariable("EDP_PRESSURE_WARNING_GB",  "Machine")
```

Se algo aparecer em User/Machine que não devia estar lá:

```powershell
[System.Environment]::SetEnvironmentVariable("EDP_PRESSURE_CRITICAL_GB", $null, "User")
[System.Environment]::SetEnvironmentVariable("EDP_PRESSURE_WARNING_GB",  $null, "User")
```

Reconcilie sempre contra a fonte do repo
(`edp/runtime/pressure_governor.py`, defaults 0.30/0.60), nunca contra
memória do que "deveria" estar setado.

---

## 3. Rollbacks por env var (nenhum código muda)

Todos revertíveis na hora, sem redeploy — sobem antes do `python run.py
serve`:

```powershell
$env:EDP_WRITE_PROVENANCE  = "0"     # desliga carimbo de proveniência
$env:EDP_HYBRID_RETRIEVAL  = "0"     # volta pro cosine puro (sem HybridRetriever)
$env:EDP_CTX_SLOTS         = "0"     # metadados voltam a contar no budget de contexto

# Regime antigo de pressure (dimensionado pra inferência LOCAL, 8GB+ —
# NÃO usar no deployment API-only atual, é o regime que causou o
# CRITICAL permanente do incidente de 17/07):
$env:EDP_PRESSURE_CRITICAL_GB = "1.2"
$env:EDP_PRESSURE_WARNING_GB  = "2.0"
```

Todos com default `"1"` (ligado) exceto os dois de pressure, que têm
default 0.30/0.60 — setar explicitamente só pra REVERTER.

---

## 4. Semântica do pressure

- **WARNING** (`CRITICAL_GB < RAM disponível < WARNING_GB`): jobs do
  `background_loop` com `suspend_on_pressure=True` pulam o tick.
- **CRITICAL** (`RAM disponível < CRITICAL_GB`): pula o tick **inteiro**
  do `background_loop`; rejeita nova inferência **LOCAL**.
- Inferência **CLOUD** (Anthropic/OpenAI via rede) **nunca** é bloqueada
  pelo pressure governor — o gate é só pra RAM residente local
  (embedding model + processo), não pra chamadas de API.

Faixa normal observada na máquina: **0.28-1.45GB disponível** (RAM total
~4.1GB). Isso é NORMAL pra este deployment API-only — não é sintoma de
nada quebrado.

**Suspeita #1 em inanição/travamento:** VM Kali aberta consome 1-2GB. Se
o pressure estiver preso em CRITICAL sem explicação, confira primeiro o
que mais está rodando na máquina antes de mexer em threshold.

---

## 5. Relógio

A âncora temporal do EDP é **VERIFICADA** (NTP/HTTP) e é a fonte
confiável — não o timestamp do log do host.

- Host Windows tem **drift conhecido** (~3h em 17/07/2026, provável
  fuso horário mal configurado). Em qualquer investigação forense
  (ordem de eventos, "o que aconteceu antes de quê"), a âncora manda; o
  timestamp do log do SO pode estar simplesmente errado.
- `"[modo fallback]"` na âncora = o boot não conseguiu verificação
  online naquela subida (NTP/HTTP falhou) — trate qualquer timestamp
  daquela sessão com desconfiança extra até a próxima âncora verificada.

---

## 6. Stores: produção vs. teste — NUNCA confundir

| Path | O que é | Pode escrever? |
|---|---|---|
| `C:\edp_data` | **PRODUÇÃO.** Intocável fora de restore deliberado. | Só via app real ou restore explícito |
| `C:\edp_data_fase0` | Store da suite de regressão (`suite_regressao_fase1.py`). Smoke vivo SEMPRE roda aqui, em cópia descartável. | Sim — é descartável |

Restore seletivo em produção:

```powershell
# Copie SÓ os arquivos default_*.json — NUNCA bench_*.json (são de
# benchmark/teste, não são estado de sessão real):
Copy-Item C:\edp_data_backup_<data>\sessions\default_*.json C:\edp_data\sessions\ -Force
```

Smokes escrevem em disco (marker de sessão, summary, extractor,
consolidação) — rodar um smoke contra `C:\edp_data` real contamina
produção com dado sintético. Sempre `$env:EDP_BASE_DIR =
"C:\edp_data_fase0"` (ou outra cópia descartável) antes de qualquer
smoke/teste manual.

---

## 7. Quarentena

```powershell
Get-Content "$env:EDP_BASE_DIR\sessions\default_cognitive\quarantine_audit.jsonl" -Tail 50
Get-Content "$env:EDP_BASE_DIR\sessions\default_cognitive\backfill_audit.jsonl"    -Tail 50
```

`"blocked_toxic"` no log do `auto_consol` é a guarda de qualidade
agindo como esperado — não é erro, é o sistema recusando promover uma
entry cujo `answer_class` está em `TOXIC_ANSWER_CLASSES = {"not_found",
"disqualification"}` (`edp/config.py`). `"disqualification"` é
INCONDICIONAL (decisão do pesquisador, 15/07/2026) — nunca promove,
mesmo com score alto.

---

## 8. Corrupção de `episodic.json`

```powershell
python repair_episodic.py                # diagnóstico, não modifica nada
python repair_episodic.py --repair       # repara (backup automático em .json.broken antes)
```

Cobre o caso comum: "Extra data" (write interrompido deixando lixo
depois de um JSON válido — cenário PRÉ write-atômico). O ciclo completo
de failsafe (`edp/failsafe.py`: `incremental_backup` /
`restore_backup` / `detect_corruption` / `validate_memory_json`) tem
teste dedicado (`tests/test_failsafe_roundtrip.py`, Hardening Fase 3
T1) — inclusive um teste que documenta que truncamento GENUÍNO no meio
de um objeto (array nunca fecha) **não** é recuperável pela mesma
lógica e propaga erro no reload; nesse caso o caminho é
`restore_backup()` a partir do backup mais recente, não
`repair_episodic.py`.

**WAL: risco conhecido, NÃO resolvido.** Mitigação atual = write
atômico (`_atomic_write_json`: tmp + fsync + rename) + failsafe +
disciplina de backup manual. Não existe write-ahead-log real.

---

## 9. Credenciais

**Incidente real, 17/07/2026:** um comentário com travessão (`—`) colado
sem querer no campo de API key virou parte da "key", causando 503 por
erro de decodificação latin-1 no header HTTP. O sistema degradou limpo
(erro claro, não crash silencioso), mas custou 2 tentativas até alguém
notar que o problema era o clipboard, não a credencial.

**Sempre confira o conteúdo do clipboard antes de colar uma API key** —
um `Ctrl+C` de linha errada (comentário, trecho de log) sobrevive até o
paste.

---

## 10. Ruídos conhecidos (cosméticos, não são bugs)

- **Hook do graphify no Windows**: dispara rebuild em background a cada
  troca de branch/commit; cosmético, não afeta o app.
- **Eco do próprio prompt no `session_summary`** em sessão quase-vazia
  (N=2, família `exp009`): pendência registrada, não um bug de dado —
  o summary reflete o próprio prompt de volta quando não há conteúdo
  suficiente pra sumarizar de verdade.

---

## 11. Gates

```powershell
python -m pytest tests/ -q                          # suite default (POSIX + Windows)
python -m pytest tests/ -m windows_only -q           # Windows: Dívida #8 (os.replace/PermissionError)

$env:EDP_BASE_DIR = "C:\edp_data_fase0"
python suite_regressao_fase1.py
```

CI (`.github/workflows/tests.yml`, Hardening Fase 3 T3): roda os dois
primeiros automaticamente em `ubuntu-latest`/`windows-latest` a cada
push/PR. **Branch protection (bloquear merge com CI vermelho) não é
config de repo — é um passo manual no GitHub:** Settings → Branches →
Branch protection rules → `main` → "Require status checks to pass
before merging" → marcar o job `tests`. Ninguém fez isso ainda.

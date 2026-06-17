"""
scripts/validate_state.py — Validação cruzada estatística do EDP.

Commit ε (Renato + Claude, 07/06/2026):
  Fotografia do estado atual de Pareto/Gauss/Bayes/Decisions.
  NÃO modifica nada — apenas inspeciona disco + memória.

Aplicação:
  python scripts/validate_state.py
  (rodar standalone, com EDP NÃO necessariamente rodando)

Paths validados via investigação mecânica (07/06/2026):
  - Pareto:    $EDP_BASE_DIR/pareto/events.jsonl  (JSONL append-only)
  - Bayes:     in-memory cache (sem arquivo) — acessado via get_bayes_calibrator()
  - Decisions: campo "cognitive_decisions" dentro de entries em
               $EDP_BASE_DIR/sessions/default_cognitive/episodic.json

Saída: relatório textual em terminal.
Retorno: 0 se tudo saudável, 1 se houver problema.
"""
from __future__ import annotations

import json
import os
import sys
import statistics
from pathlib import Path
from collections import Counter
from typing import List, Optional


# ── Configuração ─────────────────────────────────────────────────────────────

EDP_BASE_DIR = Path(os.environ.get("EDP_BASE_DIR", "C:/edp_data"))
SESSION_ID   = os.environ.get("EDP_SESSION_ID", "default")

GAUSS_THRESHOLD = 0.30   # threshold validado empiricamente (Commit 4)
BAYES_MIN_N     = 3      # mínimo para confiança estatística (Commit 5α)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _print_header(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _print_kv(key: str, value, indent: int = 2) -> None:
    spaces = " " * indent
    print(f"{spaces}{key}: {value}")


# ── Validação 1: Pareto + Gauss ──────────────────────────────────────────────

def validate_pareto_gauss() -> bool:
    """
    Pareto events.jsonl: confere estrutura + contagem por event_type.
    Gauss: extrai memory_accessed events e calcula distribuição de top_score.
    """
    _print_header("1. Pareto + Gauss (top_score distribution)")

    pareto_path = EDP_BASE_DIR / "pareto" / "events.jsonl"
    _print_kv("path", str(pareto_path))

    if not pareto_path.exists():
        print("  ❌ FALHA: arquivo não existe")
        return False

    # Lê todos eventos
    try:
        with open(pareto_path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f.readlines() if ln.strip()]
    except Exception as e:
        print(f"  ❌ FALHA: erro lendo arquivo: {e}")
        return False

    events: List[dict] = []
    corrupted = 0
    for ln in lines:
        try:
            events.append(json.loads(ln))
        except json.JSONDecodeError:
            corrupted += 1

    _print_kv("total_lines", len(lines))
    _print_kv("valid_events", len(events))
    _print_kv("corrupted_lines", corrupted)

    if not events:
        print("  ⚠️  AVISO: zero eventos válidos")
        return False

    # Contagem por event_type
    event_counts = Counter(e.get("event", "unknown") for e in events)
    print()
    print("  Distribuição por event_type:")
    for ev_type, count in event_counts.most_common():
        print(f"    {ev_type:25s} {count}")

    # Gauss: filtra memory_accessed e calcula stats de top_score
    accessed = [e for e in events if e.get("event") == "memory_accessed"]
    if not accessed:
        print()
        print("  ⚠️  Gauss: sem eventos memory_accessed ainda")
        return True  # OK, sistema novo

    top_scores = [
        float(e["top_score"])
        for e in accessed
        if isinstance(e.get("top_score"), (int, float))
    ]

    if not top_scores:
        print("  ⚠️  Gauss: memory_accessed sem top_score válido")
        return True

    n         = len(top_scores)
    avg       = sum(top_scores) / n
    above_thr = sum(1 for s in top_scores if s >= GAUSS_THRESHOLD)
    stdev     = statistics.stdev(top_scores) if n >= 2 else 0.0
    minv      = min(top_scores)
    maxv      = max(top_scores)

    print()
    print(f"  Gauss top_score statistics (n={n}):")
    _print_kv("avg",                 f"{avg:.4f}",         indent=4)
    _print_kv("stdev",               f"{stdev:.4f}",       indent=4)
    _print_kv("min",                 f"{minv:.4f}",        indent=4)
    _print_kv("max",                 f"{maxv:.4f}",        indent=4)
    _print_kv(f"above {GAUSS_THRESHOLD}",
              f"{above_thr}/{n} ({100*above_thr/n:.1f}%)", indent=4)

    # Saúde: pelo menos 30% acima do threshold
    healthy = (above_thr / n) >= 0.30
    print(f"  Status: {'✅ saudável' if healthy else '⚠️  baixa precisão'}")
    return True


# ── Validação 2: Bayes ───────────────────────────────────────────────────────

def validate_bayes() -> bool:
    """
    Bayes: NÃO tem arquivo. Acessar via singleton em memória.
    Como script standalone, importar e calcular sobre disco.
    """
    _print_header("2. Bayes (frequência condicional)")

    # Bayes opera sobre os mesmos eventos do Pareto (correlation_id co-occur)
    # Script standalone replica lógica simplificada — sem importar EDP runtime
    pareto_path = EDP_BASE_DIR / "pareto" / "events.jsonl"
    if not pareto_path.exists():
        print("  ❌ FALHA: pareto/events.jsonl não existe")
        return False

    try:
        with open(pareto_path, "r", encoding="utf-8") as f:
            events = [json.loads(ln) for ln in f if ln.strip()]
    except Exception as e:
        print(f"  ❌ FALHA: {e}")
        return False

    # Agrupa por correlation_id
    by_corr: dict[str, set[str]] = {}
    n_with_corr = 0
    n_without   = 0
    for e in events:
        corr = e.get("correlation_id")
        ev   = e.get("event")
        if not corr or not ev:
            n_without += 1
            continue
        n_with_corr += 1
        by_corr.setdefault(corr, set()).add(ev)

    _print_kv("eventos COM correlation_id",  n_with_corr)
    _print_kv("eventos SEM correlation_id",  n_without)
    _print_kv("turnos únicos (correlation_id)", len(by_corr))

    # Conditionals KNOWN do BayesCalibrator
    known_pairs = [
        ("task_started",  "task_completed"),
        ("memory_added",  "memory_accessed"),
        ("mode_switched", "task_started"),
    ]

    print()
    print("  Conditional probabilities (P(B|A)):")
    healthy_pairs = 0
    for (a, b) in known_pairs:
        # P(B|A) = #(A and B) / #(A)
        n_a       = sum(1 for evs in by_corr.values() if a in evs)
        n_a_and_b = sum(1 for evs in by_corr.values() if a in evs and b in evs)
        n_b_total = sum(1 for evs in by_corr.values() if b in evs)

        if n_a < BAYES_MIN_N:
            print(f"    {a} → {b}:  n_a={n_a} (< {BAYES_MIN_N}, sem confiança)")
            continue

        p = n_a_and_b / n_a
        healthy_pairs += 1
        print(f"    P({b} | {a}) = {p:.4f}  n_a={n_a}  n_a∩b={n_a_and_b}  n_b_total={n_b_total}")

    print()
    status = "✅ saudável" if healthy_pairs > 0 else "⚠️  poucos dados ainda"
    print(f"  Status: {status} ({healthy_pairs}/{len(known_pairs)} pares calculáveis)")
    return True


# ── Validação 3: Cognitive Decisions ─────────────────────────────────────────

def validate_cognitive_decisions() -> bool:
    """
    Decisions: lê episodic.json e conta entries com campo cognitive_decisions.
    """
    _print_header("3. Cognitive Decisions (Commit 3d)")

    episodic_path = EDP_BASE_DIR / "sessions" / f"{SESSION_ID}_cognitive" / "episodic.json"
    _print_kv("path", str(episodic_path))

    if not episodic_path.exists():
        print("  ❌ FALHA: arquivo não existe")
        return False

    try:
        with open(episodic_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except Exception as e:
        print(f"  ❌ FALHA: erro lendo JSON: {e}")
        return False

    if not isinstance(entries, list):
        print("  ❌ FALHA: episodic.json não é lista")
        return False

    _print_kv("total_entries", len(entries))

    # Filtra entries com cognitive_decisions
    with_cd = [
        e for e in entries
        if isinstance(e, dict) and e.get("cognitive_decisions") is not None
    ]
    _print_kv("entries com decisions", len(with_cd))

    if not with_cd:
        print("  ⚠️  AVISO: zero entries extraídas — extrator pode estar inativo")
        return True

    # Distribuição de domains
    domains = Counter(
        e["cognitive_decisions"].get("domain", "unknown")
        for e in with_cd
    )

    print()
    print(f"  Distribuição de domains (top 10):")
    for domain, count in domains.most_common(10):
        print(f"    {count}x  {domain}")

    # Distribuição de concepts
    all_concepts: List[str] = []
    for e in with_cd:
        cd = e["cognitive_decisions"]
        concepts = cd.get("concepts", [])
        if isinstance(concepts, list):
            all_concepts.extend(concepts)

    concept_counts = Counter(all_concepts)
    print()
    print(f"  Top 10 concepts (de {len(all_concepts)} extrações):")
    for concept, count in concept_counts.most_common(10):
        print(f"    {count}x  {concept}")

    # Modelos usados
    models = Counter(
        e["cognitive_decisions"].get("model_used", "unknown")
        for e in with_cd
    )
    print()
    print(f"  Modelos usados na extração:")
    for model, count in models.most_common():
        print(f"    {count}x  {model}")

    print()
    print(f"  Status: ✅ {len(with_cd)} extrações persistidas")
    return True


# ── Validação 4: Saúde geral do EDP ──────────────────────────────────────────

def validate_general() -> bool:
    """
    Verificações gerais: dirs existem, files críticos presentes.
    """
    _print_header("4. Saúde geral do EDP")

    critical_paths = {
        "EDP_BASE_DIR":     EDP_BASE_DIR,
        "pareto/":          EDP_BASE_DIR / "pareto",
        "sessions/":        EDP_BASE_DIR / "sessions",
        "cognitive/":       EDP_BASE_DIR / "sessions" / f"{SESSION_ID}_cognitive",
        "sprint/":          EDP_BASE_DIR / "sessions" / f"{SESSION_ID}_sprint",
    }

    all_ok = True
    for name, path in critical_paths.items():
        exists = path.exists()
        status = "✅" if exists else "❌"
        print(f"  {status} {name:20s} {path}")
        if not exists and name != "sprint/":  # sprint pode não existir se não usou
            all_ok = False

    return all_ok


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print()
    print("╔" + "═" * 68 + "╗")
    print("║   EDP State Validation — Commit ε                                  ║")
    print("║   Investigation: paths confirmed mechanically (07/06/2026)         ║")
    print("╚" + "═" * 68 + "╝")
    print()
    _print_kv("EDP_BASE_DIR", EDP_BASE_DIR)
    _print_kv("SESSION_ID",   SESSION_ID)

    results = [
        validate_general(),
        validate_pareto_gauss(),
        validate_bayes(),
        validate_cognitive_decisions(),
    ]

    print()
    print("=" * 70)
    if all(results):
        print("  ✅ SISTEMA SAUDÁVEL — todos os validadores passaram")
        print("=" * 70)
        return 0
    else:
        n_failed = sum(1 for r in results if not r)
        print(f"  ⚠️  PROBLEMAS DETECTADOS — {n_failed}/{len(results)} validadores falharam")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
scripts/organizar_export_conta.py — quebra um export de conta em um
arquivo por conversa, pronto para o ingest da wiki.

PROBLEMA QUE RESOLVE: tanto o "Export Data" oficial do claude.ai quanto o
export da extensão entregam um blob único. A wiki precisa de atribuição
por conversa — `fontes:` no frontmatter aponta para
`conv:<uuid>#t<n>`, e isso exige que a fronteira entre conversas exista.

ACEITA DUAS FONTES, normaliza para a MESMA forma:

  1. Export Data oficial (Configurações → Privacidade → Export Data).
     Chega como .zip com `conversations.json` dentro, ou o .json solto.
     Traz TODAS as conversas da conta, inclusive as que a extensão nunca
     abriu e as anteriores a ela existir.

  2. Export da extensão (`{uuid, title, turns:[...]}`), 1 conversa.

SAÍDA:
  <destino>/conversas/<AAAA-MM-DD>_<titulo>_<uuid8>.json   (1 por conversa)
  <destino>/_indice.json                                    (catálogo)

READ-ONLY na entrada. Sem LLM, sem rede, sem custo.

USO (PowerShell):
  python scripts/organizar_export_conta.py `
      --entrada "$env:USERPROFILE\\Downloads\\data-2026-08-07.zip" `
      --destino "$env:USERPROFILE\\Downloads\\conversas_edp"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def slug(texto: str, limite: int = 60) -> str:
    n = unicodedata.normalize("NFKD", (texto or "conversa").lower())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^\w\s-]", "", n)
    return (re.sub(r"[\s_]+", "-", n).strip("-") or "conversa")[:limite]


def data_de(iso: str | None) -> str:
    if not iso:
        return "0000-00-00"
    try:
        return datetime.fromisoformat(
            str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return str(iso)[:10]


# ── Normalização: bloco da API → bloco do nosso formato ──────────────────────

def _blocos_e_thinking(conteudo: list) -> tuple[list, list, list]:
    """
    Espelha content.js:_processBlock (v4.9.0). thinking NÃO entra em
    blocks — é campo de primeira classe, mesmo contrato que a extensão usa,
    para que os scripts a jusante leiam os dois formatos igual.
    """
    blocks, th_blocks, th_sums = [], [], []
    for i, b in enumerate(conteudo or []):
        if not isinstance(b, dict):
            continue
        tipo = b.get("type")
        if tipo == "text":
            t = (b.get("text") or "").strip()
            if t:
                blocks.append({"type": "text", "content": t})
        elif tipo == "thinking":
            if (b.get("thinking") or "").strip():
                th_blocks.append({
                    "index": i, "content": b["thinking"], "source": "export_oficial",
                    "start_timestamp": b.get("start_timestamp"),
                    "stop_timestamp": b.get("stop_timestamp"),
                    "truncated": b.get("truncated", False),
                    "cut_off": b.get("cut_off", False),
                })
            sums = [s if isinstance(s, str) else (s or {}).get("summary")
                    for s in (b.get("summaries") or [])]
            sums = [s for s in sums if s and s.strip()]
            if sums:
                th_sums.append({"index": i, "summaries": sums,
                                "source": "export_oficial"})
        elif tipo == "tool_use":
            blocks.append({"type": "tool_use", "tool": b.get("name", ""),
                           "input": b.get("input", {}) or {},
                           "message": b.get("message", "") or ""})
        elif tipo == "tool_result":
            txt = "\n".join(c.get("text", "") for c in (b.get("content") or [])
                            if isinstance(c, dict) and c.get("type") == "text")
            blocks.append({"type": "tool_result", "tool": b.get("name", ""),
                           "content": txt, "artifact": None,
                           "is_error": b.get("is_error", False)})
    return blocks, th_blocks, th_sums


def normalizar_conversa(c: dict) -> dict | None:
    """Conversa do export oficial → forma da extensão."""
    msgs = c.get("chat_messages")
    if not isinstance(msgs, list):
        return None

    turns = []
    for idx, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        blocks, th_b, th_s = _blocos_e_thinking(m.get("content"))
        # Export oficial às vezes traz só `text` no topo, sem `content`.
        if not blocks and (m.get("text") or "").strip():
            blocks = [{"type": "text", "content": m["text"].strip()}]
        turns.append({
            "index": idx, "uuid": m.get("uuid"),
            "role": m.get("sender") or m.get("role"),
            "created_at": m.get("created_at"),
            "blocks": blocks, "attachments": m.get("attachments") or [],
            "files": m.get("files") or [],
            "raw_text": "\n".join(b["content"] for b in blocks
                                  if b["type"] == "text").strip(),
            "thinking_blocks": th_b, "thinking_summaries": th_s,
            "has_thinking": bool(th_b),
            "has_thinking_summary": bool(th_s),
        })

    return {
        "uuid": c.get("uuid"), "title": (c.get("name") or "Conversa").strip(),
        "model": c.get("model") or "", "summary": c.get("summary") or "",
        "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "origem": "export_oficial", "turns": turns,
        "stats": {
            "total_turns": len(turns),
            "human_turns": sum(1 for t in turns if t["role"] == "human"),
            "assistant_turns": sum(1 for t in turns if t["role"] == "assistant"),
            "turns_with_thinking": sum(1 for t in turns if t["has_thinking"]),
            "total_thinking_blocks": sum(len(t["thinking_blocks"]) for t in turns),
        },
    }


# ── Leitura da entrada ───────────────────────────────────────────────────────

def carregar(entrada: Path) -> list[dict]:
    """Retorna lista de conversas já normalizadas."""
    if entrada.suffix.lower() == ".zip":
        with zipfile.ZipFile(entrada) as z:
            alvos = [n for n in z.namelist()
                     if n.endswith("conversations.json") or n.endswith(".json")]
            if not alvos:
                raise SystemExit(f"ERRO: nenhum .json dentro de {entrada.name}")
            preferido = next((a for a in alvos if a.endswith("conversations.json")),
                             alvos[0])
            print(f"  lendo {preferido} de dentro do zip")
            doc = json.loads(z.read(preferido).decode("utf-8", "replace"))
    else:
        doc = json.loads(entrada.read_text(encoding="utf-8", errors="replace"))

    # Export oficial: lista de conversas com chat_messages
    if isinstance(doc, list):
        out = [normalizar_conversa(c) for c in doc if isinstance(c, dict)]
        out = [c for c in out if c]
        if out:
            return out

    if isinstance(doc, dict):
        # já no formato da extensão
        if isinstance(doc.get("turns"), list):
            d = dict(doc)
            d.setdefault("origem", "extensao")
            return [d]
        if isinstance(doc.get("chat_messages"), list):
            c = normalizar_conversa(doc)
            return [c] if c else []
        # invólucro
        for chave in ("conversations", "data", "items"):
            v = doc.get(chave)
            if isinstance(v, list):
                out = [normalizar_conversa(c) if "chat_messages" in c else c
                       for c in v if isinstance(c, dict)]
                out = [c for c in out if c and c.get("turns") is not None]
                if out:
                    return out

    raise SystemExit("ERRO: formato não reconhecido. Esperado export oficial "
                     "(lista com chat_messages) ou export da extensão (turns).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entrada", type=Path, required=True,
                    help=".zip do Export Data oficial, ou .json")
    ap.add_argument("--destino", type=Path, required=True,
                    help="pasta de saída (será criada)")
    ap.add_argument("--min-turnos", type=int, default=2,
                    help="descarta conversas com menos turnos (default 2)")
    args = ap.parse_args()

    if not args.entrada.exists():
        raise SystemExit(f"ERRO: {args.entrada} não existe.")

    print(f"entrada: {args.entrada.name} "
          f"({args.entrada.stat().st_size / 1_048_576:.1f} MB)")
    conversas = carregar(args.entrada)
    print(f"conversas encontradas: {len(conversas)}")

    pasta = args.destino / "conversas"
    pasta.mkdir(parents=True, exist_ok=True)

    indice, escritas, descartadas, colisoes = [], 0, 0, 0
    vistos: set[str] = set()

    for c in conversas:
        turns = c.get("turns") or []
        if len(turns) < args.min_turnos:
            descartadas += 1
            continue
        uuid = str(c.get("uuid") or "")[:8] or f"{escritas:04d}"
        nome = f"{data_de(c.get('created_at'))}_{slug(c.get('title'))}_{uuid}.json"
        if nome in vistos:
            colisoes += 1
            nome = f"{nome[:-5]}_{colisoes}.json"
        vistos.add(nome)

        (pasta / nome).write_text(
            json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
        escritas += 1

        st = c.get("stats") or {}
        indice.append({
            "arquivo": f"conversas/{nome}", "uuid": c.get("uuid"),
            "titulo": c.get("title"), "criada": c.get("created_at"),
            "atualizada": c.get("updated_at"), "modelo": c.get("model"),
            "turnos": st.get("total_turns", len(turns)),
            "turnos_com_thinking": st.get("turns_with_thinking", 0),
            "origem": c.get("origem", "?"),
        })

    indice.sort(key=lambda r: str(r.get("criada") or ""), reverse=True)
    total_turnos = sum(r["turnos"] for r in indice)
    com_think = sum(1 for r in indice if r["turnos_com_thinking"])

    (args.destino / "_indice.json").write_text(json.dumps({
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "entrada": args.entrada.name,
        "conversas": len(indice), "turnos": total_turnos,
        "conversas_com_thinking": com_think,
        "itens": indice,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nescritas:      {escritas} conversas em {pasta}")
    if descartadas:
        print(f"descartadas:   {descartadas} (menos de {args.min_turnos} turnos)")
    if colisoes:
        print(f"colisões:      {colisoes} nomes desambiguados com sufixo")
    print(f"turnos totais: {total_turnos}")
    print(f"com thinking:  {com_think} conversas")
    if com_think == 0 and escritas:
        print("\n  ATENÇÃO: nenhuma conversa trouxe thinking. Se a entrada foi o")
        print("  Export Data oficial, é sinal de que ele NÃO carrega os blocos de")
        print("  raciocínio — nesse caso o caminho da extensão (por conversa)")
        print("  continua sendo a única fonte de thinking histórico.")
    print(f"índice:        {args.destino / '_indice.json'}")

    if indice[:5]:
        print("\n5 mais recentes:")
        for r in indice[:5]:
            print(f"  {str(r['criada'])[:10]}  {r['turnos']:>5} turnos  "
                  f"{(r['titulo'] or '')[:48]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""rotulador.py — CLI de rotulação humana para gt_rotulacao.csv (exp012-v2, T2.5).
USO: python rotulador.py gt_rotulacao.csv
Lê e escreve SOMENTE gt_rotulacao.csv — nunca gt_features.csv (anti-circularidade).
Dedup por conteúdo: hash(query_norm+resposta_norm), MESMA normalização de
avaliador_matriz.py (strip, lower, whitespace colapsado). Rotula uma vez por
conteúdo único; o rótulo propaga para todas as linhas do grupo. Resume automático.
"""
import csv, hashlib, re, sys, os

CLASSES = {
    "1": "VENENO_NEGACAO",
    "2": "VENENO_CONFABULACAO",
    "3": "LEGITIMO_CONHECIMENTO",
    "4": "LEGITIMO_META",
    "5": "LEGITIMO_CONTEUDO",
    "6": "AMBIGUO",
}

GUIA = """\
  1 VENENO_NEGACAO        nega ter memória/registro do que a query pedia — TÓXICO mesmo quando justificado
  2 VENENO_CONFABULACAO   afirma continuidade/histórico sem base real
  3 LEGITIMO_CONHECIMENTO conhecimento geral respondendo query factual
  4 LEGITIMO_META         nega achar objeto EXTERNO (arquivo, dado), não a própria memória; ou meta-conversa legítima
  5 LEGITIMO_CONTEUDO     conteúdo normal — inclui continuação BEM-SUCEDIDA
  6 AMBIGUO               indecidível (sai da matriz)
  o = observação (texto livre)   s = pular   q = salvar e sair
"""

REQUIRED_COLS = ("id", "store", "origem", "query", "resposta", "rotulo", "observacao")


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def content_key(row):
    h = norm(row["query"]) + "||" + norm(row["resposta"])
    return hashlib.sha256(h.encode("utf-8")).hexdigest()[:12]


def load(path):
    with open(path, encoding="utf-8") as f:
        comment = f.readline()
    if not comment.startswith("#"):
        raise SystemExit(f"{path}: primeira linha não é comentário (#) — abortando para não corromper o arquivo.")
    with open(path, encoding="utf-8", newline="") as f:
        f.readline()  # pula a linha de comentário, já capturada acima
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    for col in REQUIRED_COLS:
        if col not in (fieldnames or []):
            raise SystemExit(f"{path}: coluna obrigatória ausente: {col}")
    return comment.rstrip("\n"), fieldnames, rows


def save(path, comment, fieldnames, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(comment + "\n")
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def getch():
    """Lê 1 tecla sem Enter num tty real; cai para readline() quando stdin não é tty
    (pipes/testes), consumindo uma linha inteira como 'tecla'."""
    try:
        import termios, tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return ch
    except Exception:
        line = sys.stdin.readline()
        if not line:
            return "q"
        return line.strip()[:1] or "\n"


def group_rows(rows):
    grupos = {}
    for r in rows:
        grupos.setdefault(content_key(r), []).append(r)
    return grupos


def group_label(members):
    labels = {m.get("rotulo", "").strip() for m in members if m.get("rotulo", "").strip()}
    return next(iter(labels)) if len(labels) == 1 else None


def main():
    if len(sys.argv) != 2:
        raise SystemExit("USO: python rotulador.py gt_rotulacao.csv")
    path = sys.argv[1]
    comment, fieldnames, rows = load(path)

    grupos = group_rows(rows)
    keys = list(grupos.keys())
    n_total = len(keys)
    print(f"Conteúdos únicos: {n_total} (linhas brutas: {len(rows)})")

    pendentes = [k for k in keys if group_label(grupos[k]) is None]
    n_ja = n_total - len(pendentes)
    print(f"Já rotulados: {n_ja} | Faltam: {len(pendentes)}\n")

    for idx, k in enumerate(pendentes, start=1):
        members = grupos[k]
        rep = members[0]
        ids = [m["id"] for m in members]
        stores = sorted({m["store"] for m in members})
        origens = sorted({m["origem"] for m in members})
        query = rep["query"]
        resposta = rep["resposta"]

        parar = False
        while True:
            print("=" * 78)
            print(f"[{n_ja + idx}/{n_total}] grupo={k}  ids={ids}  store={'|'.join(stores)}  origem={'|'.join(origens)}")
            print("-" * 78)
            print("QUERY:")
            print(query)
            print("-" * 78)
            print("RESPOSTA:")
            if len(resposta) > 2000:
                print(resposta[:2000])
                print(f"... [TRUNCADO NA TELA — {len(resposta)} chars no total; o arquivo mantém o texto completo]")
            else:
                print(resposta)
            print("-" * 78)
            print(GUIA)
            escolha = getch()
            print()

            if escolha in CLASSES:
                rotulo = CLASSES[escolha]
                for m in members:
                    m["rotulo"] = rotulo
                save(path, comment, fieldnames, rows)
                print(f"-> {rotulo} salvo para {len(members)} linha(s).\n")
                break
            elif escolha == "o":
                obs = input("Observação: ").strip()
                for m in members:
                    m["observacao"] = obs
                save(path, comment, fieldnames, rows)
                print("-> observação salva. Escolha a classe (1-6) para avançar.\n")
                continue
            elif escolha == "s":
                print("-> pulado (permanece não rotulado).\n")
                break
            elif escolha == "q":
                parar = True
                break
            else:
                print(f"-> entrada inválida ({escolha!r}). Tente novamente.\n")
                continue
        if parar:
            break

    grupos_final = group_rows(rows)
    labeled = {k: group_label(v) for k, v in grupos_final.items()}
    labeled = {k: v for k, v in labeled.items() if v is not None}
    dist = {}
    for lab in labeled.values():
        dist[lab] = dist.get(lab, 0) + 1

    print("=" * 78)
    print(f"Rotulados: {len(labeled)}/{len(grupos_final)} | Faltam: {len(grupos_final) - len(labeled)}")
    print("Distribuição por classe:")
    for cls in CLASSES.values():
        print(f"  {cls}: {dist.get(cls, 0)}")


if __name__ == "__main__":
    main()

# Arestas removidas por apontarem para o vazio

Registro append-only de arestas que a wiki **declarou** e que apontavam
para página que nunca existiu.

## Por que este arquivo existe

Ao remover os 2 links quebrados de `r1-seletividade-invertida` em
11/08/2026 (item 1 do E-3.5, `preregistro_gap_score.md`), provei que a
remoção era neutra **para as métricas agregadas** da E-3.2 — 27 arestas,
mediana 4,5, min 2, max 8, idênticos antes e depois.

**Não era neutra para a pergunta de pesquisa, e isso passou.**

Uma aresta declarada apontando para página nunca escrita é uma instância
do fenômeno que o `Gap Event` tenta formalizar: *o ponto em que a
estrutura promete uma transição e não entrega*. Alguém, numa sessão
anterior, tentou estabelecer uma relação e ela não foi sustentada. Apagar
a declaração descartava um caso real do fenômeno **antes de o detector
existir**.

"Remover do frontmatter" e "descartar o dado" não podem ser a mesma
operação. A remoção mantém o lint limpo — que é o motivo legítimo dela;
este arquivo mantém o dado.

**Uso previsto:** quando `capacidade_de_satisfação()` for definido (item
2 do E-3), estas linhas são casos de calibração com rótulo conhecido —
promessas de transição que sabidamente não se sustentam. Corpus pequeno,
mas é dado real e não sintético.

## Registro

| removida em | origem | alvo pretendido | assunto do alvo | como foi detectada |
|---|---|---|---|---|
| 2026-08-11 | `r1-seletividade-invertida` | `honeypot-refutado` | resultado H0 do honeypot, 0/14 (`dd06b87`) | `scripts/lint_wiki.py`, regra 5 |
| 2026-08-11 | `r1-seletividade-invertida` | `blob-qa-comprime-faixa-dinamica` | perda de 47% de faixa dinâmica no blob `Q+A` | `scripts/lint_wiki.py`, regra 5 |

**As duas foram removidas, não convertidas em páginas.** Criar as páginas
acrescentaria conteúdo ao corpus sob teste — e
`blob-qa-comprime-faixa-dinamica` é o assunto de **Q6**, pergunta do
conjunto A do gabarito. Ver a nota de execução do item 1 em
`preregistro_gap_score.md`.

## Regra

Toda remoção de aresta por alvo inexistente entra aqui **antes** de a
edição ser feita. Linha nova, nunca sobrescrita — regra 3 do
`WIKI_SCHEMA.md`. E o `sha256` do corpus resultante vai para o
`preregistro_gap_score.md`, já que `edp_wiki/` é gitignored e a edição
não produz diff versionado.

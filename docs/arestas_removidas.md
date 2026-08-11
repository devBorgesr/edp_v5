# Arestas removidas por apontarem para o vazio

Registro append-only de arestas que a wiki **declarou** e que apontavam
para página que nunca existiu.

> **ESTE ARQUIVO É SEGURO PARA TRABALHO CEGO AO GABARITO.**
> Ele registra que uma declaração existiu e não foi sustentada — origem,
> alvo, data, detector. Nada sobre o que o alvo tratava, e nada que ligue
> uma linha daqui a qualquer pergunta de teste.
>
> `docs/preregistro_gap_score.md` e
> `docs/preregistro_rodagem_cruzada_wiki.md` **não são seguros**: contêm
> o gabarito inteiro. Quem os ler está desqualificado para desenhar
> critério que será medido contra ele.

## Por que este arquivo existe

Ao remover os 2 links quebrados em 11/08/2026 (item 1 do E-3.5), provei
que a remoção era neutra **para as métricas agregadas** — 27 arestas,
mediana 4,5, min 2, max 8, idênticos antes e depois.

Não era neutra para o dado. Uma aresta declarada apontando para página
nunca escrita registra que alguém tentou estabelecer uma relação e ela
não foi sustentada. Apagar a declaração descartava isso.

"Remover do frontmatter" e "descartar o dado" não podem ser a mesma
operação. A remoção mantém o lint limpo — motivo legítimo dela; este
arquivo mantém o dado.

## O que estas linhas NÃO são

**Não são casos de calibração de `capacidade_de_satisfação()`.** A versão
anterior deste arquivo afirmava isso; era otimismo, e assumia uma
equivalência que ninguém demonstrou:

| fenômeno | processo gerador |
|---|---|
| **link quebrado de autoria** (isto aqui) | alguém escreveu `links: [...]` numa sessão e nunca criou a página de destino |
| **dead-end de navegação** (o que o `Gap Event` quer detectar) | um navegador, em tempo de consulta, tenta avançar e não acha aresta sustentável |

Podem correlacionar. **Não foi demonstrado que correlacionam**, e com
N=2 — as duas do mesmo nó de origem — não há como demonstrar aqui.

O que estas linhas são, com honestidade: **evidência anedótica de que
declaração-fantasma existe neste corpus.** Nada além disso até que
alguém meça.

## Registro

| removida em | origem | alvo pretendido | detectada por |
|---|---|---|---|
| 2026-08-11 | `r1-seletividade-invertida` | `honeypot-refutado` | `scripts/lint_wiki.py`, regra 5 |
| 2026-08-11 | `r1-seletividade-invertida` | `blob-qa-comprime-faixa-dinamica` | `scripts/lint_wiki.py`, regra 5 |

As duas foram **removidas, não convertidas em páginas** — criar página
acrescentaria conteúdo ao corpus sob teste. O raciocínio completo, que
inclui informação que vaza gabarito, está na nota de execução do item 1
em `preregistro_gap_score.md`, que é leitura desqualificante.

## Regra

Toda remoção de aresta por alvo inexistente entra aqui **antes** de a
edição ser feita. Linha nova, nunca sobrescrita — regra 3 do
`WIKI_SCHEMA.md`. Sem coluna de assunto, sem descrição do alvo: o slug
já identifica, e descrever é vazar.

O `sha256` do corpus resultante vai para `preregistro_gap_score.md`, já
que `edp_wiki/` é gitignored e a edição não produz diff versionado.

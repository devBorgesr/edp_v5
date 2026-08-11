# AVISO — leia isto antes de trabalhar na frente do Gap Event

**Este arquivo é seguro.** Ele não contém gabarito, nem pergunta de
teste, nem resultado. Só regras de higiene. Pode ser lido por qualquer
instância, em qualquer momento.

Ele existe porque as duas regras abaixo estavam escritas **dentro do
arquivo que elas mandam não abrir** — catch-22 literal: quem obedecesse
nunca aprenderia que precisava obedecer.

---

## Regra 1 — o que desqualifica

Estes arquivos contêm o **gabarito completo** de um experimento em curso:

- `docs/preregistro_gap_score.md`
- `docs/preregistro_rodagem_cruzada_wiki.md`

Quem os lê fica **desqualificado** para:

- definir o predicado de satisfação de transição (item 2 da frente)
- desenhar o vocabulário de tipo de aresta (item 3)
- mapear as arestas nele (item 4)

Qualquer critério desenhado por quem viu o gabarito não pode ser lido
como evidência de que generaliza — só de que cobre aquele gabarito.

**Ler o arquivo e escavar o histórico dele são a mesma leitura.** A
desqualificação cobre também `git log`, `git show`, `git blame` e
`git diff` sobre:

```
docs/preregistro_*.md
docs/arestas_removidas.md
docs/AVISO_INSTANCIA_LIMPA.md
scripts/medir_gap_score*.py
scripts/medir_alcance_wiki.py
tests/test_medicao_wiki.py
```

Mensagens de commit desta frente carregam metadado estrutural sobre o
gabarito — quais itens importam — mesmo quando não dizem o conteúdo.

## Regra 2 — sessões contaminadas do corpus de Code

**Sessões de Claude Code a partir de 2026-08-11 contêm o gabarito por
inteiro**, porque o experimento foi conduzido dentro delas.

Elas ficam em `~/.claude/projects/-media-sf-edp-v5-main/*.jsonl`, que é
o mesmo diretório usado como corpus da condição C.

> Qualquer trabalho futuro que compile corpus a partir de sessões de
> Code — nova condição C, wiki compilada de sessões, agente que leia o
> histórico — **tem de excluir 2026-08-11 em diante**, ou aceitar que o
> gabarito reentrou por uma porta que nenhum lint enxerga.

A fatia congelada em julho precede isto e segue válida.

O mesmo vale para exports do `claude-exporter`, que captura tráfego do
claude.ai incluindo blocos de raciocínio.

## Regra 3 — as ferramentas do próprio EDP são um vetor

Esta é a recontaminação mais provável neste projeto, e a mais difícil de
perceber: **não passa por abrir arquivo nenhum.**

`cognitive_decisions`, a memória episódica, o retrieval, e o pipeline que
compila wiki a partir de sessões **existem para atravessar a fronteira de
sessão**. É o propósito declarado deles. Uma instância que se considera
limpa, trabalhando dentro do EDP, pode buscar contexto sobre Gap Event,
navegação ou tipo de aresta e **reencontrar o gabarito por retrieval
automático** — sem nunca ter aberto `preregistro_gap_score.md`.

A Regra 1 não alcança isso. "Não leia estes arquivos" não governa um
sistema de memória cuja função é justamente recuperar o que foi dito
antes.

> Quem for projetar os itens 2, 3 ou 4 **não deve consultar a memória do
> EDP, nem rodar retrieval, nem usar wiki compilada de sessão** sobre
> esses assuntos. Se precisar de contexto, ele vem do
> `docs/WIKI_SCHEMA.md` e do corpus em `edp_wiki/paginas/` — nada que
> tenha passado por compilação de sessão.

Não há lint que pegue isto. É disciplina, e está aqui porque este é o
único lugar do projeto onde nomeá-la é natural.

## O que é seguro ler

- `docs/WIKI_SCHEMA.md` — as convenções da wiki
- `edp_wiki/paginas/` — o corpus em si
- `docs/arestas_removidas.md` — registro de arestas declaradas e não
  sustentadas, sem descrição de assunto
- este arquivo

---

## Tensão estrutural conhecida, sem conserto

O slug deste corpus **é** o resumo do achado, por convenção
(`r1-seletividade-invertida`, `contagem-de-nos-como-medida-de-vagueza`).
Boa propriedade para navegar e para o lint casar; ruim para "seguro para
leitura cega".

Qualquer arquivo que referencie uma página pelo nome carrega uma fração
do conteúdo pelo próprio nome — inclusive `arestas_removidas.md`, mesmo
sem coluna de assunto. **Não há conserto de arquivo que resolva.** É
tensão entre dois objetivos do projeto, não defeito a corrigir. Quem
desenhar o item 2 vai esbarrar nela.

---

## Estado da frente, em 2026-08-11

A frente do Gap Event **precisa de uma instância nova para revisar tudo
do zero**, e isso não é excesso de zelo.

A instância que produziu este trabalho ficou contaminada durante ele, e
cada correção de vazamento exigiu que ela olhasse o gabarito para
decidir o que cortar. Ela pode **descrever** o próprio processo de
limpeza; não pode **certificá-lo** — "isto é seguro publicar" e "estou
desqualificado para decidir isso" são o mesmo julgamento feito pela
mesma parte.

**O primeiro trabalho de uma instância limpa deveria ser re-auditar essa
limpeza, não confiar nela.** E fazer isso exige ler o território
contaminado — o que a desqualifica para os itens 2, 3 e 4. São dois
papéis diferentes, para duas instâncias diferentes.

### O que aqui é mecanismo e o que é confiança

Vale ser exato, porque o resto deste arquivo soa mais firme do que é:

| | |
|---|---|
| **mecanismo real** | o `sha256` do corpus em `preregistro_gap_score.md`, que faz `medir_gap_score_haiku.py` abortar se a wiki mudar; os testes em `tests/`; o `lint_wiki.py` |
| **só confiança** | **tudo neste arquivo** |

Nada impede tecnicamente que uma instância leia os pré-registros, rode
`git log`, consulte a memória do EDP, ou que a sessão que produziu este
trabalho continue projetando os itens 2–4 se for instruída a isso. A
contaminação já está no contexto dela, independentemente do que este
documento diga.

Um gate de verdade seria possível — o projeto já usa hooks em
`.claude/settings.json`. Não existe hoje. Quem construir um deveria
lembrar que **decidir o que o gate bloqueia é, de novo, um julgamento
sobre o gabarito** — e portanto não é trabalho para quem já o viu.

# O que precisamos de você — especificação de entrada

Um único arquivo. Nada mais.

## Formato

Um arquivo `.jsonl`: **uma linha JSON por query**, sem vírgula entre
linhas, codificado em UTF-8.

```json
{"query": "como configuro o SSO?", "results": [{"id": "doc-8812", "text": "Para configurar o SSO, acesse...", "score": 0.83}, {"id": "doc-1140", "text": "O provedor de identidade...", "score": 0.71}]}
{"query": "qual o limite de upload?", "results": [{"id": "doc-2093", "text": "O limite por arquivo é...", "score": 0.66}]}
```

## Campos

| campo | obrigatório | o que é |
|---|---|---|
| `query` | **sim** | o texto da busca, exatamente como chegou ao sistema |
| `results` | **sim** | lista dos resultados devolvidos, **na ordem em que o sistema os ordenou** |
| `results[].text` | **sim** | o texto do trecho/documento recuperado |
| `results[].id` | não | identificador do documento ou chunk |
| `results[].score` | não | o score de relevância que o seu sistema atribuiu |

**Sem `id`**, as métricas de duplicação por identificador são omitidas do
relatório, com nota. **Sem `score`**, a análise de escala é omitida, com
nota. O relatório nunca finge ter medido o que não recebeu.

## Como gerar

Rode as suas queries reais contra o seu sistema de busca e serialize a
resposta. Em qualquer linguagem, o laço é:

```python
import json

with open("export.jsonl", "w", encoding="utf-8") as out:
    for q in queries:
        hits = meu_retriever.search(q, top_k=10)   # o SEU sistema
        registro = {
            "query": q,
            "results": [
                {"id": h.id, "text": h.text, "score": h.score}
                for h in hits
            ],
        }
        out.write(json.dumps(registro, ensure_ascii=False) + "\n")
```

## Quantas queries

Mínimo útil: **30**. Abaixo disso a análise de repetição cross-query fica
sem pares suficientes e é omitida.

Recomendado: **100 a 500 queries reais**, do log de produção. Queries
inventadas para o teste medem o sistema respondendo a perguntas que
ninguém faz — o resultado sai bonito e não serve para nada.

## Preserve a ordem cronológica

Se possível, escreva as linhas na ordem em que as buscas realmente
aconteceram. A análise de repetição entre buscas consecutivas usa a ordem
das linhas do arquivo; se ela for arbitrária, essa métrica reflete a ordem
do arquivo, não a experiência real do usuário — e o relatório vai declarar
esse caveat.

## O que NÃO mandar

- **Não mande o seu corpus completo.** Só o que o retrieval devolveu.
- **Não mande PII se puder evitar.** Se as queries dos seus usuários
  contêm dado pessoal, anonimize antes: as métricas funcionam sobre texto
  normalizado, não sobre identidade.
- **Não mande credenciais, chaves de API ou configuração de ambiente.**
  Não precisamos de acesso ao seu sistema em momento nenhum.

## Se o arquivo vier com defeito

Linhas malformadas (JSON inválido, sem `query`, `results` que não é lista)
são **puladas, contadas e reportadas** no relatório. Resultados sem texto
utilizável são descartados e contados à parte, sem invalidar o resto da
query. A ferramenta não quebra com dado ruim — mas se a contagem de
descarte vier alta, avisamos antes de faturar, porque o diagnóstico teria
sido feito sobre uma fração do que você quis medir.

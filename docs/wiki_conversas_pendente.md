# Wiki — indexação de conversas e thinking (PENDENTE, com motivo)

Item 2 da tarefa do Degrau 1 ("Indexar conversas e thinking: associar
trechos de conversas com thinking a comunidades relevantes; criar uma
seção 'Conversas relevantes' em cada página").

**Não implementado em 06/08/2026.** O motivo é segurança, não escopo — e
não é uma objeção à ideia, que é boa.

---

## 1. O problema, verificado no código

Uma página da wiki é servida por `GET /wiki/{slug}`, na mesma aplicação
FastAPI que:

| onde | o quê |
|---|---|
| `edp/api/main.py:260` | `allow_origins=["*"]` — qualquer origem pode ler |
| `edp/config.py:219` | `EDP_LIVE_FEED_TOKEN = os.environ.get(..., "")` — sem token por padrão |

Ou seja: **não há autenticação por padrão, e a política de origem é
aberta.** Qualquer página web que o navegador abra enquanto o EDP roda
consegue fazer `fetch("http://localhost:8000/wiki/...")` e ler a resposta.

Para conteúdo derivado do código do projeto isso é aceitável — é o mesmo
perfil de risco do `/graph`, e está documentado em `edp/config.py`
(`EDP_GRAPH_VIEWER`). Para **trecho de conversa real**, não é: reabriria
exatamente o que foi fechado duas vezes esta semana:

- `3076559` — `.graphifyignore`, para conversa não entrar no grafo
- `99d827c` — `.gitignore`, para conversa não entrar em commit

Fechar os vetores de commit e de grafo e depois abrir um vetor de HTTP
seria desfazer o trabalho pela porta dos fundos.

## 2. Estado atual

`edp/config.py` já declara a flag, **default OFF**, e **nenhum código a
consome**:

```python
EDP_WIKI_CONVERSAS = os.environ.get("EDP_WIKI_CONVERSAS", "0") == "1"
```

`tests/test_wiki.py::test_flag_conversas_default_off` trava o default, e
`test_wiki_nao_serve_conversa_real` falha se qualquer página passar a
referenciar arquivo de conversa/export.

## 3. Desenho seguro (o que falta antes de ligar)

Pré-requisitos, em ordem. Nenhum é grande; o ponto é que **precedem** a
indexação, não a acompanham.

1. **Autenticação no caminho HTTP.** Hoje `EDP_LIVE_FEED_TOKEN` protege
   só o WebSocket `/stream`. Estender a exigência às rotas que servem
   conteúdo derivado de conversa, ou introduzir um token próprio.
2. **Fechar o CORS.** `allow_origins=["*"]` → lista explícita
   (`http://localhost:*`) quando qualquer rota servir conversa.
3. **Recusa em bloco se 1 ou 2 não estiverem satisfeitos.** Ligar
   `EDP_WIKI_CONVERSAS=1` com token vazio deve **falhar no boot** com
   mensagem clara, não servir mesmo assim. Flag de conveniência que
   silenciosamente expõe dado é pior que flag nenhuma.
4. **Fonte explícita.** Decidir de onde vem o trecho: export do sensor
   (`Análise_*.json`, com `thinking_blocks` desde a v4.9.0) ou store do
   EDP. Os dois estão hoje fora do grafo por `.graphifyignore` — a
   indexação teria que ler direto, sem passar pelo graphify, para não
   afrouxar aquele ignore.
5. **Redação por escopo.** Definir se todo o turno entra ou só um trecho,
   e se `thinking` entra — é o conteúdo mais sensível do export.

## 4. Alternativa que não precisa de nada disso

Se o objetivo é **navegar** o próprio raciocínio e não **publicá-lo**, o
comando `/wiki/{slug}.md` já devolve Markdown cru, e um script local pode
juntar página + trechos de conversa **em arquivo local**, sem passar pela
API. Zero superfície de rede, mesma utilidade para leitura própria.

Essa é a rota que eu recomendaria primeiro: entrega o valor sem abrir o
vetor.

---

Reabrir com: `EDP_WIKI_CONVERSAS`, os 5 pré-requisitos do §3, e um teste
que prove que o boot falha quando a flag está ligada sem token.

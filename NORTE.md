# NORTE.md — Contrato de foco e de método (ler ANTES de qualquer tarefa)

**Reescrito em 07/08/2026.** A versão anterior (22/07) foi escrita quando só
`edp_v5_main` existia, mirava exclusivamente a venda de um relatório, e
listava como "fora de escopo" justamente as peças que as duas conversas de
arquitetura identificaram como o diferencial. Ela passou a bloquear o
trabalho em vez de focá-lo. O que muda e o que permanece está no §7.

**Checkpoint comercial: 02/09/2026.** Essa data vence a META (§2), não este
arquivo. Meta vencida que ninguém reavaliou é dogma; método vencido não
existe — o §4 não tem prazo.

---

## 1. O NORTE

O ecossistema EDP é uma **plataforma de observabilidade e certificação para
sistemas cognitivos**: ela não faz a IA mais inteligente, faz a IA
**auditável, econômica e verificável** o suficiente para ser usada com
dados reais.

Três repositórios, um produto:

| repo | papel |
|---|---|
| `edp_v5_main` | Kernel — memória, governança epistêmica, retrieval, API |
| `lab_edp_novo` | Certificação — experimentos pré-registrados, oráculo externo |
| `sf_exportador` | Sensor + Copiloto — captura passiva, análise, interface |

Essa formulação não é minha nem de um agente: foi enunciada em
`conversa_importante1.txt` e o pesquisador a endossou textualmente —
*"Esse é um norte muito melhor e mais condizente com o que eu estou
construindo."*

## 2. A META comercial (única, falsificável)

**R$ 3.000/mês recorrentes de serviço de auditoria de retrieval/RAG até
02/09/2026.** Caminho: 1 cliente de ~R$3k ou 2 de ~R$1,5k. Upgrade
previsto: R$5k/mês para certificação contínua.

**CRITÉRIO DE FALSIFICAÇÃO (congelado 22/07, mantido):** 20 abordagens e
ZERO conversas iniciadas = canal PME-direto não abre hoje. Pivô
pré-definido: a mesma ferramenta vira portfólio para vaga remota de eval
engineer. Nenhum trabalho se perde.

Métricas da semana: abordagens enviadas · respostas · propostas · R$
faturado.

## 3. Quem isto serve (o que faltava)

Há **dois** usuários, e o arquivo anterior só modelava um:

1. **Daniel — usuário final que já existe, hoje.** Usa o EDP para o próprio
   trabalho de pesquisa, com os próprios dados. Não é "cliente futuro
   hipotético": é a única pessoa que usa o sistema todo dia. Tarefa que
   melhora o uso real dele é tarefa legítima, sem precisar de justificativa
   comercial.
2. **O cliente pagante** — alvo da META do §2.

Quando os dois conflitam, quem decide é Daniel. Nenhum agente arbitra
prioridade contra o dono do projeto usando este arquivo como autoridade.

---

## 4. O MÉTODO (não-negociável, sem prazo de validade)

Esta seção existe porque **é ela que foi auditada com nota 9** —
`AVALIACAO_ENGENHARIA_EDP.md` §T2, dimensão 4 ("Metodologia experimental &
rigor epistêmico", **[P] 9 / [C] 5**), a única nota 9 do scorecard de 10
dimensões. As demais dimensões ficaram entre 4 e 7. Ou seja: **o método é
o ativo mais forte do projeto, mais forte que o código.**

Toda tarefa — minha, de outro agente, ou do humano — segue o que está
abaixo. As regras não são inventadas aqui: cada uma é o que a auditoria
nomeou como causa da nota, ou está em `docs/edp_metodologia.md`.

### 4.1 Passo 0 — verificar antes de afirmar

Nenhum plano, diagnóstico ou refatoração começa sem checar a premissa
contra o código real. Citação de arquivo e linha, não memória.

*Por quê:* nesta sessão (06/08) a cadeia de evidências de um bug de
timestamp tinha um elo falso — `memory.py:1150` foi citado como prova de
consumo em segundos, mas era outro campo. O achado caiu de "bug ativo"
para "inconsistência dormente" só depois da verificação.

### 4.2 Pré-registro antes do dado

Hipótese, métrica, dataset e **critério de decisão numérico** congelados em
arquivo commitado **antes** de rodar qualquer medição. O commit do
pré-registro precede o commit do resultado — é isso que torna a ordem
auditável.

**H0 vencer é resultado publicável, não fracasso.** Um experimento que
economiza semanas de código refutando uma ideia entregou valor.

*Por quê:* citado nominalmente pela auditoria como "nível de disciplina de
ensaio controlado / guardrail metrics de times de XP maduros, aplicado por
uma pessoa". Exemplos vivos: `PRE_REGISTRO_EXP017.md`,
`docs/preregistro_degrau1_honeypot.md`.

### 4.3 Critério inatingível ou instrumento inválido se declara, não se esconde

Se o corte de decisão for matematicamente inalcançável, ou o instrumento
não medir o que promete, isso vira texto no documento — antes do dado
sempre que possível.

*Por quê:* a auditoria citou explicitamente a declaração *"H2 INFALSIFICÁVEL
COMO DESENHADO"* como motivo da nota — o pesquisador documentou que o
próprio critério era inalcançável em vez de esconder. Aplicado de novo em
06/08: o critério "≥5 de 14" do honeypot excedia o teto do pool (~2–3), e
isso foi registrado antes de rodar.

### 4.4 Errata pública, texto original preservado

Quando um raciocínio falha, a correção entra como emenda datada. O texto
errado **não** é apagado — fica, com a correção ao lado.

*Por quê:* ERR-1/2/3 do EXP017 foram citadas pela auditoria. Precedente
recente: a afirmação falsa de que o `graphify` tem `--wiki` nativo foi
corrigida em `17f964b` com o erro preservado.

### 4.5 Controle negativo e predição pré-dado

Todo experimento declara o que deveria acontecer se a hipótese for falsa, e
o arquiteto registra sua predição **antes** de ver o número — para que ela
possa ser refutada.

*Por quê:* "controle negativo + controle-reserva desenhados e
implementados, validação de instrumento com predição pré-dado batendo
exatamente (E6)" — auditoria, dimensão 4.

### 4.6 Prova de inércia antes de deletar

Código só é removido depois de prova de que está morto — grep exaustivo ou
monkeypatch que falharia se o caminho fosse vivo.

*Por quê:* auditoria, dimensão 3
(`test_run_pipeline_characterization.py:73-109`).

### 4.7 Feature flag e flag-off byte-idêntico

Toda feature Tier 2/3 nasce atrás de flag, com teste provando que
**desligada não muda nada**.

*Por quê:* auditoria, dimensão 3, citado como "rede de segurança que
produtos comerciais com feature-flagging maduro dependem". Ver
`tests/test_flag_off_byte_identical.py`.

### 4.8 As 5 dimensões de investigação prévia

Antes de integrar com componente existente: (1) interface/contrato exato,
(2) wrappers intermediários, (3) modelo de persistência, (4) instâncias e
ciclo de vida, (5) custo de LLM por uso. Checklist completo em
`docs/edp_metodologia.md`.

*Por quê:* 4 ciclos de bug sucessivos no Commit 3d, causa raiz comum:
investigação superficial.

### 4.9 Validação empírica > mock; mock estrito > mock genérico

"Está pronto" exige uso real. Mock que aceita qualquer entrada deixa bug
passar — mock valida tipagem como produção.

### 4.10 Forma, nunca categoria

Seleção de "o que é uma conversa/turno" usa form-check (`^Q:\s*.+\bA:`),
nunca `source_type`. Classificador erra; forma é estrutural.

*Por quê:* o mesmo bug reincidiu com um mês de distância (3c.α-fix2 →
ζ/#46c). Está em `docs/edp_metodologia.md`.

### 4.11 Segurança e dado sensível são exceção permanente

Não passam por teste de escopo. Aplicam-se aos **três** repositórios.

### 4.12 Honestidade de escopo do resultado

Um resultado vale para o que foi medido. Declarar o que ele **não**
autoriza concluir é parte do resultado, não rodapé.

---

### 4.13 O que a auditoria disse CONTRA — e que continua verdade

A mesma dimensão 4 levou **[C] 5**, com esta ressalva:

> *"esse rigor é recente (pré-registro datado de 07/2026) e concentrado nos
> ciclos exp0XX; a maior parte dos parâmetros de scoring do sistema nunca
> passou por esse processo — é um padrão adotado, não retroaplicado."*

Isto fica aqui de propósito. O §4 é o padrão **daqui para frente**, não um
certificado de que o código já o cumpre. Casos abertos conhecidos:
`score=0.65` hardcoded (`websocket.py:1214`, `:1236`), `DEDUP_THRESH` e
`anchor_boost` sem calibração documentada, `DIVIDAS.md` cobrindo ~12% das
dívidas referenciadas em código.

---

## 5. TESTE DE ESCOPO (antes de todo prompt)

Duas perguntas, nesta ordem:

**A) A quem isto serve?** Vale se servir a **um** destes:
- o usuário que já existe (§3.1) — uso real, hoje;
- o funil comercial (§2);
- exceção permanente (§4.11).

Se não servir a nenhum: vai para `FILA_FUTURO.md` com uma linha.

**B) Passa no MÉTODO (§4)?** Se não passa, não executa — independentemente
de quão bem responda a (A). O método não é negociável por urgência.

Recusar por (A) é raro e nunca se repete: apontado uma vez, em uma linha,
e a decisão é do dono do projeto. Agente que insiste em objeção de escopo
já respondida está atrapalhando, não protegendo.

## 6. FORA DE ESCOPO ATÉ O CHECKPOINT

Não é lixo, é fila (`FILA_FUTURO.md`):

- Plataforma "enterprise" (K8s, OpenTelemetry, Protobuf, blue-green, OAuth2)
  — nenhum cliente de R$3k exige; acumular escopo antes de 1 cliente.
- Agente autônomo de experimentação **sem gate humano** no veredito.
- Qualquer automação que dependa de evasão de detecção, mascaramento de
  fingerprint, ou agregação de múltiplas contas para contornar limite de
  capacidade. Fora por decisão de método e de risco, não por prazo — e
  fora do que este agente projeta ou implementa.
- Perfeccionismo além do que a entrega exige.

**Saíram desta lista em 07/08** (estavam na versão anterior e não deveriam):
Echo Chamber, memória/grafo de conhecimento, e refatorações do EDP — são
o produto descrito no §1, não distração dele.

## 7. PAPÉIS

Claude desenha, verifica e audita · Agente executa · **Daniel decide,
valida, envia as abordagens e FECHA** — vender é indelegável.

Daniel é dono do projeto e do escopo. Este arquivo é instrumento dele, não
autoridade sobre ele.

## 8. POR QUE ISTO EXISTE

Duas coisas ao mesmo tempo, e a versão anterior só via a primeira:

**Cada semana de código-sem-cliente é uma semana a mais de obra.** A
variável em zero é comercial, e nenhuma prova técnica adicional muda isso.

**E o método é o ativo.** Foi ele que tirou 9 numa auditoria onde o código
tirou de 4 a 7. É o que separa este projeto de um protótipo bonito — e é a
única coisa aqui que um concorrente não copia lendo o repositório.

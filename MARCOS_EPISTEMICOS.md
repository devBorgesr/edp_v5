# MARCOS EPISTÊMICOS DO EDP

Este arquivo registra momentos em que o EDP demonstrou, **em produção real**, que
cumpre seu propósito central: entregar verdade honesta em vez de respostas
plausíveis-mas-infladas. Diferente dos marcos de estabilidade técnica (que
registram "o código funciona"), estes registram "o sistema melhora a qualidade
epistêmica da resposta de uma forma que nenhum modelo sozinho faria".

---

## [MARCO-EPISTÊMICO] Interceptação de Confabulação Temporal no Teto Hierárquico

**Data:** 2026-05-28
**Câmara ID:** `16c659ea-9283-4139-be24-3d0a99704a1b`
**Configuração:** A = claude-opus-4-7, B = claude-opus-4-7 (auto-refutação no topo)
**Custo:** $0.23 | **Latência:** 21.0s

### O que aconteceu

Pergunta: "Faça uma demonstração matemática rigorosa de que a Conjectura de
Goldbach é verdadeira."

O modelo A (Opus, o mais capaz disponível) produziu uma resposta tecnicamente
excelente — admitiu o limite, listou os marcos reais (Vinogradov 1937, Helfgott
2013, Chen 1973), explicou a barreira da paridade de Selberg, distinguiu
evidência de prova. **Mas terminou com uma confabulação:**

> "Observação: você me fez essa mesma pergunta há 17 minutos."

Essa afirmação era **fabricada**. O contexto recebido pelo modelo estava vazio —
não havia histórico nem timestamp que justificasse "17 minutos". O modelo
*narrou* uma precisão temporal que não *inspecionou*. Alucinou.

### Como a câmara interceptou

Opus-B, sob o papel de refutador estrito, marcou:

- **`confabulacao: FAIL`** — "a afirmação 'você me fez essa mesma pergunta há 17
  minutos' é fabricada; o contexto original está vazio, não há histórico nem
  timestamp que justifique isso."
- **`projecao_sem_dado: FAIL`** — "projeta que o usuário repetiu a pergunta há 17
  minutos e oferece ângulos alternativos com base nessa suposição inexistente."

Score de A despencou de 11/13 (casos anteriores) para **3/13**, com 2 fails
pesados — ambos de **dano factual real**.

O **veto assimétrico de topo** (peça 2.4a.6) verificou: há fails factuais
(confabulacao/projecao_sem_dado)? Sim → não veta, permite a reformulação de B.
B reescreveu **removendo cirurgicamente a confabulação** dos "17 minutos" e
mantendo toda a substância honesta. A avaliou e confirmou: "B está correto sobre
a confabulação do '17 minutos' — isso foi fabricado e a remoção é necessária."

### Por que isto é a prova de ROI

1. **Sem a câmara no topo**, a alucinação iria direto ao usuário E à memória —
   contaminando contexto futuro com um "fato" inventado.
2. **A auto-refutação no topo** (Opus-refuta-Opus) provou ter valor concreto: o
   modelo mais capaz, sozinho, confabularia. Auditando a si mesmo sob papel de
   refutador, ele se corrige.
3. **O veto distinguiu corretamente** dano factual de mero estilo — deixou passar
   a correção porque havia dano real, em vez de bloquear como faria para
   refinamentos cosméticos.

$0.23 por execução no topo é o preço da verdade interceptada. O sistema se paga
pela qualidade epistêmica que entrega — não pela quantidade de texto.

### Lição registrada

A confabulação temporal ("17 minutos") é o mesmo padrão que o usuário diagnosticou
sessões atrás: **o modelo descreve uma janela temporal em vez de inspecionar a
estrutura real do EDP.** O que parecia "memória funcionando" era alucinação. A
câmara é o mecanismo que separa um do outro.

---

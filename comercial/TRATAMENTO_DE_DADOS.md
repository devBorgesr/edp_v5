# Tratamento de dados do cliente

Este documento descreve o que acontece com o arquivo que você envia. As
afirmações técnicas abaixo são verificáveis no código, não são promessa
comercial.

## O que a ferramenta faz e não faz

**Não faz nenhuma chamada de rede.** O programa que produz o relatório
importa exclusivamente biblioteca padrão do Python (`argparse`, `hashlib`,
`json`, `re`, `statistics`, `sys`, `pathlib`) — a lista completa, conferível
com `grep "^import\|^from" audit/retrieval_audit.py`. Não há cliente HTTP, socket, nem
SDK de provedor de modelo em nenhum ponto do caminho de execução.

**Não usa LLM.** Nenhuma parte da análise é feita por modelo de linguagem.
Todas as métricas são contagem, hash e estatística determinística. Seu
texto não é enviado para OpenAI, Anthropic, Google ou qualquer outro
serviço — não porque prometemos não enviar, mas porque não existe código
capaz de enviar.

**Roda offline.** A auditoria pode ser executada em máquina sem conexão.
Se você preferir, podemos executá-la na sua infraestrutura, com você
assistindo, e o arquivo nunca sai do seu ambiente.

## Recebimento

Canal combinado antes do envio. Preferência, em ordem: (1) execução no seu
ambiente, sem transferência; (2) link temporário do seu próprio storage,
com expiração; (3) anexo cifrado.

## Armazenamento e retenção

- O arquivo de entrada é mantido apenas durante a execução da auditoria e
  o período de revisão do relatório com você.
- **Prazo padrão de exclusão: 30 dias após a entrega do relatório**, ou
  imediatamente após a entrega se você pedir.
- Nada do seu dado é versionado em repositório, público ou privado. Os
  repositórios de trabalho negam arquivos `.jsonl` e `.csv` **por classe,
  não por nome** — a regra é bloquear por padrão e liberar arquivo a
  arquivo com motivo declarado, porque o modo de falha oposto (listar o
  que bloquear) não dá sintoma nenhum quando alguém esquece. No
  repositório do laboratório essa regra é imposta por teste automatizado;
  no repositório da ferramenta, por regra de `.gitignore` verificável com
  `git check-ignore`.
- Nada do seu dado é usado para treinar, calibrar ou ajustar qualquer
  modelo ou heurística nossa.

## Acesso

Uma pessoa. Não há equipe, subcontratado, nem terceiro com acesso ao
material. Se isso mudar, você é avisado antes, não depois.

## Isolamento entre auditorias

Cada auditoria roda em diretório próprio, com o arquivo de entrada
identificado pelo cliente. Nenhum dado de um cliente entra no relatório,
na calibração ou nos exemplos de outro. Os relatórios de exemplo que
usamos comercialmente são gerados sobre **dado nosso**, nunca sobre dado
de cliente — a menos que você autorize por escrito e o material seja
anonimizado com a sua revisão.

## Proveniência sem exposição

O registro de cada auditoria guarda: versão da ferramenta, configuração
usada, contagem de queries e de linhas descartadas, e as métricas
resultantes. **Não guarda o conteúdo dos seus documentos.** Isso permite
reproduzir e defender o número sem reter o material que o gerou.

## Confidencialidade formal

Este documento descreve prática, não substitui contrato. Trabalhamos com
o **seu** NDA padrão sem discussão. Se você não tiver um e quiser um termo
simples de nossa parte, ele é fornecido — e a recomendação honesta é que
o seu jurídico o revise, porque não somos escritório de advocacia.

## Limite de responsabilidade, dito na frente

A auditoria produz medição sobre o arquivo que você enviou. Ela não
garante que o seu sistema vá melhorar, não substitui teste em produção, e
o escopo do que ela **não** consegue enxergar está declarado no próprio
relatório, na seção de limitações — não em letra miúda.

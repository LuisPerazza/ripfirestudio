# Correções aplicadas na v3

Revisei `app.py`, `static/app.js`, `static/index.html`, `config.json`.
O fluxo e o visual continuam os mesmos — só os pontos abaixo mudaram.

## 1. Bug crítico: o app quebrava depois do primeiro pedido
Em `static/app.js`, a tabela de pedidos do Admin chamava uma função
chamada `escapeHtml(...)` — só que essa função **nunca existiu** no
arquivo (só existe uma chamada `esc(...)`, com outro nome). Assim que
o primeiro pedido fosse criado, a página inteira passava a quebrar
com "Erro ao iniciar: escapeHtml is not defined" toda vez que
carregasse, porque a lista de pedidos era carregada automaticamente
no início. Corrigido trocando pelas chamadas corretas a `esc(...)`.

## 2. Falha de segurança grave: painel Admin sem nenhuma senha
Antes, qualquer pessoa que abrisse o site (uma vez publicado num
domínio) conseguia:
- ver a lista completa de pedidos, com nome, e-mail e WhatsApp de
  todos os clientes (`GET /api/orders`);
- cadastrar ou poluir o catálogo de artes (`POST /api/admin/artworks`);
- **liberar qualquer pedido como pago sem pagar nada de verdade**,
  clicando em "SIMULAR PAGAMENTO" (`POST /api/orders/{id}/mark-paid`)
  — isso valia dinheiro de verdade sendo "roubado" via um botão que
  qualquer visitante conseguia acionar pelo DevTools do navegador.

Corrigido: as três rotas agora exigem um cabeçalho `X-Admin-Key` que
precisa bater com o `admin_key` do `config.json`. No site, a aba
Admin agora pede essa chave a primeira vez que você entra nela.

**Troque o `admin_key` padrão no `config.json` antes de publicar.**

## 3. Pedidos de arte digital não entregavam o arquivo comprado
Quando o cliente comprava uma arte digital avulsa (aba "Artes
digitais"), o pedido criado não incluía o arquivo da arte em lugar
nenhum — só os dados do pedido. Ou seja, o cliente pagava e não
recebia nada pra baixar/receber. Corrigido: agora o arquivo original
da arte comprada é copiado pra dentro da pasta do pedido como
`ARTE_DIGITAL.<extensão>`, e por consequência também aparece na pasta
de produção depois que o pagamento é liberado — assim o operador
consegue localizar e enviar o arquivo pro cliente (por e-mail ou
WhatsApp, já que essa venda não passa por impressão).

## 4. Corrida na geração do número do pedido
`next_order_number()` conta quantos pedidos já existem no dia e soma
1 — se dois pedidos forem criados quase ao mesmo tempo (dois clientes
comprando junto), os dois podiam calcular o mesmo número, e o
segundo quebrava com um erro cru de banco de dados (a pasta em disco
ficava criada, mas sem registro no banco). Agora, se isso acontecer,
o sistema detecta e tenta de novo automaticamente com um sufixo
único, sem perder a arte que o cliente já tinha enviado.

## 5. Custo em aberto: geração por IA sem limite
`POST /api/ai/generate` não tinha limite nenhum de uso — uma vez
publicado, alguém poderia gerar centenas de imagens seguidas e gerar
uma conta alta na OpenAI sem comprar nada. Adicionei um limite simples
(6 gerações por minuto por IP) e um limite de tamanho no texto do
prompt. Isso cobre abuso básico, mas se o tráfego crescer bastante
vale evoluir pra um limite mais robusto (ex.: por conta de cliente
logado, não só por IP).

## Não mexi (mas fica registrado)

- **`gpt-image-2`** como nome padrão do modelo no `.env.example` — não
  tenho como confirmar se esse é o nome exato do modelo disponível na
  sua conta OpenAI hoje. Confira no painel da OpenAI antes de ativar.
- **Rate limit da IA é em memória** — se você reiniciar o servidor,
  o contador zera. Para uma loja pequena isso é aceitável; para um
  volume maior, um rate limit baseado em banco/Redis seria mais
  confiável.

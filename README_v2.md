# Ripfire Studio Local v2 — Pagamento → Pasta de Produção

Nesta versão o sistema NÃO envia nada automaticamente ao Flexi.

## Fluxo

CLIENTE
→ envia/cria/escolhe a arte
→ finaliza o pedido
→ pedido fica AGUARDANDO_PAGAMENTO
→ pagamento é aprovado
→ sistema cria automaticamente a pasta de produção
→ operador abre essa pasta e manda imprimir manualmente

## Exemplo

Depois do pagamento:

PRODUCAO_PAGA/
└── 260829-0001__JOAO_SILVA__DTF_ONLY/
    ├── ARTE_PRODUCAO.png
    ├── pedido.json
    └── LEIA-ME-PRODUCAO.json

## Configurar a pasta do PC da impressora

Edite `config.json`.

Mesmo PC:

"production_drop_folder": "D:\\PEDIDOS_DTF_PAGOS"

Pasta compartilhada em outro PC:

"production_drop_folder": "\\\\PC-IMPRESSORA\\DTF\\PEDIDOS_PAGOS"

O usuário do Windows que roda o aplicativo precisa ter permissão de gravação nessa pasta.

## Chave de administrador (nova na v3)

O painel Admin (cadastro de artes, lista de pedidos com dados de
clientes, botão SIMULAR PAGAMENTO) agora exige uma chave.

1. Edite `config.json` e troque `"admin_key": "TROQUE-ESTA-CHAVE-ADMIN"`
   por uma chave sua, longa e aleatória.
2. Ao clicar na aba "Admin" no site, vai aparecer uma caixa pedindo
   essa chave. Ela fica guardada só na memória do navegador enquanto
   a página estiver aberta (não fica salva em lugar nenhum).

Sem isso, qualquer pessoa que abrisse o site conseguiria ver todos os
pedidos (nome, e-mail, WhatsApp) e liberar qualquer pedido como pago
sem pagar nada — um risco sério assim que o site estiver num domínio
público.

## Pagamento de teste

No Admin aparece o botão:

SIMULAR PAGAMENTO

Ao clicar:
- payment_status vira PAID
- status vira LIBERADO_PRODUCAO
- arquivo é copiado para PRODUCAO_PAGA

## Pagamento real

Já existe um endpoint base:

POST /api/payments/webhook

Header:

X-Webhook-Secret: TROQUE-ESTA-CHAVE

JSON:

{
  "order_id": "ID_INTERNO",
  "payment_status": "PAID"
}

Depois conectamos esse webhook ao gateway escolhido.

## Regra importante

O pedido NÃO deve cair na produção apenas porque o cliente clicou "pagar".

Ele só deve ser liberado quando o gateway confirmar o pagamento como aprovado.

## Rodar

Use `start.bat`

ou:

.venv\Scripts\activate
python app.py

Abra:

http://127.0.0.1:8787

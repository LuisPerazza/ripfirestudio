# Ripfire Studio Local v1

Projeto local para abrir no VSCode e testar a ideia do novo site antes de hospedar na internet.

## Funcionalidades desta versão

- 3 modalidades de venda:
  - Somente DTF
  - DTF + camiseta lisa
  - Camiseta pronta/prensada
- Editor visual:
  - upload de PNG/JPG/WEBP
  - mover
  - redimensionar
  - girar
  - centralizar
  - adicionar texto
- Catálogo próprio de artes Ripfire
- Cadastro de artes no Admin
- Loja de artes digitais
- Quantidade de DTF
- Quantidade por tamanho de camiseta
- Modelo e cor da camiseta
- Cálculo de preço de teste
- Exportação da arte em PNG de produção a 300 DPI
- Pasta automática por pedido
- `pedido.json` com os dados do pedido
- Banco SQLite
- Painel administrativo de pedidos
- Mudança de status: NOVO / APROVADO / PRODUCAO / CONCLUIDO / CANCELADO
- Geração opcional de imagem por IA

## Como rodar no Windows / VSCode

### 1. Instale Python

Use Python 3.11 ou 3.12 e marque `Add Python to PATH` durante a instalação.

### 2. Abra esta pasta no VSCode

`File > Open Folder` e selecione `ripfire_studio_local_v1`.

### 3. Jeito mais fácil

Execute `start.bat`.

Ele cria `.venv`, instala as dependências, inicia o servidor e abre:

`http://127.0.0.1:8787`

### 4. Pelo terminal do VSCode

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## Primeiro teste

1. Abra a aba `Admin`.
2. Cadastre algumas artes PNG.
3. Abra `Personalizar`.
4. Escolha Somente DTF, DTF + camiseta ou Camiseta pronta.
5. Escolha uma arte cadastrada ou envie um arquivo.
6. Arraste, redimensione e gire.
7. Defina quantidade e tamanho.
8. Preencha o nome do cliente.
9. Clique em `Criar pedido de teste`.
10. Confira a pasta `orders/`.

Exemplo:

```text
orders/
└── 260829-0001__JOAO_SILVA/
    ├── ARTE_PRODUCAO.png
    └── pedido.json
```

## Artes digitais

Cadastre uma arte no Admin e marque `Vender arquivo digital`.
Ela aparecerá na aba `Artes digitais`.

Ao gerar um pedido digital, o sistema cria uma pasta contendo uma cópia do arquivo original.

## Configurar preços e produtos

Edite `config.json`.

Você pode alterar:

- modelos de camiseta
- cores
- tamanhos
- preço do DTF
- preço da camiseta
- valor da prensagem
- largura máxima
- DPI

Os preços atuais são apenas valores de demonstração.

## Ativar IA

A IA é opcional. O restante do sistema funciona sem ela.

1. Copie `.env.example` para `.env`.
2. Informe sua chave:

```env
OPENAI_API_KEY=sua_chave
OPENAI_IMAGE_MODEL=gpt-image-2
```

3. Reinicie o programa.

A chave fica somente no Python no seu PC, não no navegador.

O modelo padrão configurado é `gpt-image-2`.

## Importante: pagamentos

Esta v1 NÃO cobra Pix/cartão. Ela simula o checkout e cria o pedido no PC.

O objetivo desta etapa é validar:

- editor
- catálogo
- produtos
- quantidades
- arquivo de produção
- organização dos pedidos
- experiência do cliente

Depois podemos integrar pagamento, frete e o agente que envia os pedidos ao Flexi.

## Próxima versão sugerida

- frente e costas
- remoção de fundo
- mockup mais realista
- carrinho
- Pix/cartão
- login do cliente
- download digital após pagamento
- licença pessoal/comercial
- integração com o domínio Ripfire
- sincronização nuvem → PC
- painel de produção
- integração com Flexi Production Manager

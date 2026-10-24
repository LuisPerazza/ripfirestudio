# Ripfire Studio

Loja online de camisetas personalizadas e impressão DTF: o cliente monta a
estampa no editor, paga pelo Mercado Pago e os arquivos de produção ficam
prontos para o RIP.

- **Backend:** Python + FastAPI, banco SQLite (`app.py`)
- **Frontend:** HTML, CSS e JavaScript sem framework (`static/`)

## Rodar no PC

Dê dois cliques em `start.bat`. Ele cria o ambiente Python, instala as
dependências e abre `http://127.0.0.1:8787`. O Admin fica em
`http://127.0.0.1:8787/#admin`.

Antes da primeira vez:

1. Copie `config.example.json` para `config.json` e troque as senhas de
   admin (`admin_key` e `admins`) pelo hash gerado com
   `.venv\Scripts\python app.py --hash-senha` (a senha não fica legível no arquivo).
2. (Opcional) Copie `.env.example` para `.env` para ligar IA, login com
   Google, Mercado Pago e métricas. Cada linha do arquivo explica onde pegar
   a chave; vazio = desligado.

Para publicar numa VPS com domínio e HTTPS, siga o [DEPLOY.md](DEPLOY.md).

## O que o site faz

- **Editor de camiseta:** frente e verso (mangas desligadas por enquanto: `SLEEVES_ENABLED` no `app.py`), com upload, artes da
  biblioteca ou geradas por IA; limite de área por tamanho; foto de posição
  para quem estampa.
- **Somente DTF:** monta a chapa (gang sheet) com encaixe automático e cobra
  por metro.
- **Biblioteca de artes:** lado Grátis (com conta) e Premium (assinatura
  manual de 30 dias, sem renovação automática).
- **Conta do cliente:** cadastro por e-mail ou Google, pedidos com etapas,
  downloads, dados pessoais, cópia dos dados e exclusão da conta.
- **Pagamento:** Mercado Pago (Pix, cartão, boleto). Pedido não pago é
  cancelado em 24 h; o pagamento aprovado libera a produção sozinho.
- **Admin:** pedidos (com **BAIXAR ZIP** dos arquivos de produção), artes,
  mockups, biblioteca, solicitações LGPD e métricas próprias.
- **Privacidade:** aviso de cookies com consentimento por categoria,
  GA4 / Pixel da Meta / Google Tag Manager só com aceite, formulário LGPD,
  contagem de visitas anônima.
- **Senhas:** só o hash fica guardado (PBKDF2-SHA256, 600 mil iterações, sal aleatório),
  inclusive as do Admin no `config.json`; mínimo de 8 caracteres. Depois de
  **5 senhas erradas** (por IP e por conta) o acesso fica bloqueado 15 minutos
  (`"login"` no `config.json`).
- **Rotas da API fechadas por padrão:** toda rota exige login de cliente ou de
  Admin, menos as da lista `PUBLIC_ROUTES` no `app.py`. Rota nova sem proteção
  e fora da lista impede o servidor de iniciar (e diz qual é).
- **Acessibilidade:** WCAG 2.1 AA, inclusive o editor pelo teclado.

## Arquivos

| Caminho | O que é |
|---|---|
| `app.py` | Servidor: rotas, banco, pagamento, LGPD, métricas |
| `static/index.html`, `app.js`, `styles.css` | O site (uma página só, com abas) |
| `static/privacidade.html`, `termos.html`, `reembolso.html`, `lgpd.html` | Páginas legais e formulário LGPD |
| `static/fonts/` | Fontes servidas pelo próprio site (sem Google Fonts) |
| `config.json` | Preços, modelos, tamanhos, senhas do admin (não vai pro Git) |
| `.env` | Chaves de serviços externos (não vai pro Git) |
| `data/` | Banco `studio.db` e a biblioteca de artes |
| `orders/` | Uma pasta por pedido, com as artes e o `pedido.json` |
| `PRODUCAO_PAGA/`, `SomenteDTFs/` | Cópias para a produção depois do pagamento |
| `media/` | Artes do catálogo, mockups e imagens geradas por IA |
| `deploy/` | Serviço, nginx e backup diário para a VPS |
| `docs/historico/` | Anotações de versões antigas (só para consulta) |

## Antes de abrir para clientes

Veja a lista no fim do [DEPLOY.md](DEPLOY.md): credencial de produção do
Mercado Pago, senhas fortes, dados da empresa nas páginas legais (campos
entre colchetes), número do WhatsApp e um pedido de teste completo.

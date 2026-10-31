# Colocar o Ripfire Studio no ar (Render + Supabase)

Neste modo o site roda no **Render** e guarda tudo no **Supabase**:

- **Banco** (contas, pedidos, catálogo, LGPD, métricas): Postgres do Supabase.
- **Arquivos** (artes dos pedidos, catálogo, mockups, biblioteca, artes da IA):
  Supabase Storage. O disco do Render é apagado a cada deploy; lá ele é só cache.
- **Artes dos pedidos**: vão do navegador do cliente **direto** para o Storage,
  sem passar pelo Render. Uma chapa de 500 MB não depende do limite do proxy do
  Render nem da memória do servidor.
- **Produção**: o PC do RIP roda o `sincronizar_producao.bat`, que baixa cada
  pedido pago para `D:\PEDIDOS_DTF_PAGOS` (ou a pasta que você escolher), com
  o mesmo nome de pasta de sempre.

No seu PC nada muda: sem as variáveis do Supabase no `.env`, o `start.bat`
continua usando o SQLite e as pastas locais.

**Custo:** Supabase **Pro** (US$ 25/mês) + Render **Starter** (US$ 7/mês). O
Supabase grátis não serve para a loja: limita cada arquivo a 50 MB, o total a
1 GB, e pausa o projeto depois de uma semana sem uso. O Pro também faz backup
diário do banco.

---

## 1. Supabase

1. Em [supabase.com](https://supabase.com), crie o projeto. Na **região**,
   escolha **East US (North Virginia)**, a mesma do Render: cada consulta ao
   banco fica rápida. Anote a senha do banco.
2. Passe o projeto para o plano **Pro** (Organization > Billing).
3. **Storage > Settings**: em *Upload file size limit*, coloque **600 MB**
   (o mesmo `max_upload_mb` do `config.json`). Sem isso, chapas grandes são
   recusadas.
4. **Connect** (botão no topo) > **Session pooler**: copie a connection string
   e troque `[YOUR-PASSWORD]` pela senha do banco. Ela começa com
   `postgresql://postgres.` e termina com `:5432/postgres`. Esse é o
   `DATABASE_URL`.
5. **Project Settings > API Keys**: copie a **Project URL**
   (`https://xxxx.supabase.co`, o `SUPABASE_URL`) e uma chave **secreta**:
   a *secret key* (`sb_secret_...`) ou a *service_role* das chaves antigas
   (`eyJ...`). Esse é o `SUPABASE_SERVICE_KEY`. **Ela abre tudo**: nunca cole
   no GitHub, no navegador ou em mensagem.

Não precisa criar tabela nem bucket: o site cria sozinho na primeira vez
(bucket `ripfire`, privado). As tabelas ficam com RLS ligado e sem regras, ou
seja, a API pública do Supabase não lê nada delas; só o site, que entra
direto no banco.

## 2. Levar os dados do PC (se a loja já tem clientes, pedidos ou catálogo)

**No seu PC**, com o site local **desligado** (feche o `start.bat`), na pasta
do projeto:

```powershell
pip install -r requirements.txt
python migrar_para_supabase.py
```

Ele pede a connection string, a Project URL e a chave secreta, copia o banco
inteiro e sobe as pastas `media`, `data\digital_library`, `orders` e
`SomenteDTFs`. Se a internet cair no meio, rode de novo: o que já subiu é
pulado (`--so-arquivos` pula o banco, que já foi).

Pedidos antigos já produzidos ocupam muito espaço: com
`python migrar_para_supabase.py --sem-pedidos` o histórico dos pedidos vai
para o banco, mas os arquivos deles ficam só no PC.

## 3. GitHub

Envie para o GitHub os arquivos novos e alterados (`git add .`,
`git commit`, `git push`). O `.gitignore` já deixa de fora `config.json`,
`.env`, `data/`, `orders/` e o `sync_producao.json`.

> Seu repositório está **público**. Não há senhas nele, mas qualquer pessoa
> lê o código da loja. Vale deixar **privado** (GitHub > Settings >
> Danger Zone > Change visibility); o Render funciona igual com repositório
> privado.

## 4. Render

1. Em [render.com](https://render.com): **New > Blueprint**, conecte o GitHub
   e escolha o repositório. O Render lê o `render.yaml` e monta o serviço
   (Python 3.13, plano Starter, região Virginia, fuso de São Paulo).
2. Ele pede as variáveis marcadas como secretas:
   - `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`: do passo 1.
   - `SITE_URL`: o endereço do site com `https://`. No começo é
     `https://ripfire-studio.onrender.com` (ou o nome que o Render der);
     troque quando ligar o seu domínio.
   - `MERCADOPAGO_ACCESS_TOKEN`, `OPENAI_API_KEY`, `GOOGLE_CLIENT_ID`: os
     mesmos do seu `.env` (pode deixar em branco o que não usa).
   - As outras do `.env` (GA4, Pixel, GTM) você adiciona depois em
     **Environment**, se usar.
3. **O `config.json`**: no serviço, **Environment > Secret Files > Add
   Secret File**. Nome: `config.json`. Conteúdo: cole o seu `config.json`
   inteiro. Confira antes que as senhas do Admin estão como hash
   (`pbkdf2_sha256$...`, gerado com `python app.py --hash-senha`).
4. **Deploy**. Em **Logs** deve aparecer `Application startup complete`.
   Abra o endereço do site.

**Domínio próprio:** no serviço, **Settings > Custom Domains**. Depois
atualize o `SITE_URL` e, se usa login com Google, adicione o domínio em
*Origens JavaScript autorizadas* no Google Cloud.

**Mercado Pago:** com o `SITE_URL` em `https://`, o aviso de pagamento
(webhook) e a volta para o site passam a funcionar sozinhos.

## 5. PC da produção

O site não copia mais nada para o PC. Quem traz os pedidos pagos é o
`sync_producao.py`:

1. Copie `sync_producao.py` e `sincronizar_producao.bat` para uma pasta no PC
   (ex.: `C:\Ripfire`). Precisa do Python instalado; o programa instala
   sozinho o Pillow e o pypdf na primeira vez.
2. No Admin, aba **Segurança**, crie um usuário com papel **Produção** (ele só
   vê os pedidos, baixa os arquivos e muda a etapa; não ligue o 2FA nele).
   Dê dois cliques no `sincronizar_producao.bat`. Na primeira vez ele pergunta
   o endereço do site, esse usuário e a senha, e a pasta de produção.
   Pode ser uma pasta de rede: use o caminho `\\PC-IMPRESSORA\DTF\PEDIDOS`
   (não a letra do drive mapeado).
3. Deixe a janela aberta. A cada minuto ele confere o site e monta cada
   pedido pago. Somente DTF fica assim:

   ```
   <pasta de produção>\Maria Souza\2026-10-24\
       261024-0001_CHAPA_58,5x120cm.png        a chapa como a cliente montou
       261024-0001_ARTE_01_logo_10x12cm_3un.png  cada arte no tamanho escolhido
       261024-0001_DADOS_DO_PEDIDO.txt         cliente, artes e valor cobrado
   ```

   O cliente pode mandar PNG, PDF, TIFF, JPG ou WEBP. Se todas as artes do
   pedido são do mesmo tipo, tudo sai nesse tipo (JPG e WEBP saem em PNG); se
   misturou, tudo sai em PDF. Tudo a 300 dpi no tamanho real; PDF continua
   em vetor.

   Camiseta pronta fica na mesma pasta do cliente e do dia:

   ```
   261024-0002_FRENTE_P_24x30cm_2un.png    arte de cada lado, por tamanho
   261024-0002_COSTAS_GG_29,5x36,9cm_1un.png
   261024-0002_CHAPA_58,5x80cm.png         todas as cópias numa chapa só
   261024-0002_POSICAO_FRENTE.jpg          a camiseta com a arte no lugar
   261024-0002_FICHA_PRODUCAO.pdf          grade, medidas, fotos e mapa da chapa
   261024-0002_DADOS_DO_PEDIDO.txt
   ```

   O menor tamanho do pedido sai como o cliente montou; os maiores crescem na
   proporção da área de impressão de cada tamanho (`size_print_limits`), sem
   passar dela. Manga sai igual em todos os tamanhos. Chapa com mais de 2,5 m
   é dividida em várias (`_1de2`, `_2de2`).
4. Ao terminar de baixar, o pedido passa sozinho para **Em fila** no Admin.

Para abrir junto com o Windows: `Win + R`, digite `shell:startup` e coloque
lá um atalho do `sincronizar_producao.bat`.

## 6. Conferir

- [ ] Criar conta e entrar.
- [ ] Fazer um pedido de **Somente DTF** com uma chapa grande (a mensagem
      "Enviando arte... X de Y MB" aparece).
- [ ] Pagar com a credencial de teste do Mercado Pago (`TEST-...`) e ver o
      pedido virar "Liberado para produção".
- [ ] Ver a pasta do pedido aparecer no PC da produção.
- [ ] **Manual Deploy > Deploy latest commit** no Render e conferir que
      catálogo, mockups e pedidos continuam lá (vêm do Supabase).

## O que muda no Admin

- **Backups**: a aba avisa que o backup agora é do Supabase
  (*Database > Backups*, diário no plano Pro).
- **Baixar arquivos do pedido** continua funcionando (o zip é montado a
  partir do Storage).

## Segurança

- **HTTPS**: o Render emite e renova o certificado; o site redireciona http
  para https e manda HSTS (o navegador passa a usar só https).
- **Headers**: CSP (só roda script do site e dos serviços que a loja usa),
  anti-iframe, nosniff, Referrer-Policy e Permissions-Policy em toda resposta.
- **Admin**: login com sessão de 12 h e verificação em duas etapas (aba
  Segurança), obrigatória para o papel Admin. Papéis: Admin (tudo) e
  Produção (pedidos e arquivos). Admin é criado na aba Segurança, com e-mail;
  os logins do config.json podem ser desligados lá mesmo.
- **Recuperar senha sem e-mail configurado**: o cliente pede pelo WhatsApp e o
  Admin (aba Clientes) gera o link de nova senha e envia pelo WhatsApp dele.
  Para a equipe, outro admin gera o link na aba Segurança.
- **Recuperar senha** (cliente e Admin): link por e-mail, vale 30 min e uma
  vez só. O Render grátis bloqueia SMTP, então o e-mail sai pela API do
  Resend (`RESEND_API_KEY`) ou do Brevo (`BREVO_API_KEY`), com o remetente em
  `MAIL_FROM` (ex.: `Ripfire Studio <nao-responda@ripfire.com.br>`).
- **Limites**: senha errada (cliente e Admin) bloqueia por 15 min depois de 5
  tentativas; cada IP faz no máximo 900 chamadas à API em 5 min.
- **CSRF**: o login é por token no header (sem cookie) e chamada que muda
  dados vinda de outro site é recusada.
- **Banco**: RLS ligado em todas as tabelas no Supabase (a API pública não
  lê nada); cada cliente só vê os próprios pedidos.

### Recuperar o acesso do Admin

- **Esqueceu a senha de um login do config.json:** abra
  `/static/gerar-hash.html` no site, gere o hash da senha nova e troque o
  `"key"` desse usuário no config.json (Render > Environment > Secret Files).
  Salve com "Save, rebuild, and deploy".
- **Perdeu o celular do 2FA e não há outro admin:** coloque no config.json
  `"zerar_2fa": ["SeuUsuario"]` e salve. Ao subir, o site desliga o 2FA desse
  usuário. Entre, ligue o 2FA de novo no celular novo e **tire a linha** do
  config.json (enquanto ela estiver lá, todo reinício desliga o 2FA de novo).
- **Limite de arquivo:** com o Supabase grátis cada arquivo vai até 50 MB
  (`STORAGE_MAX_MB` no Environment muda o limite, ex.: 500 no plano Pro). O
  cliente vê o aviso antes de enviar.

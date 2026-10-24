# Colocar o Ripfire Studio no ar (VPS)

Passo a passo para publicar o site num servidor próprio (VPS) com domínio e
HTTPS. Os comandos marcados como **no servidor** rodam no terminal da VPS
(via `ssh`); os marcados como **no seu PC** rodam no PowerShell, dentro da
pasta do projeto.

> Hospedagem compartilhada (planos de WordPress/PHP) **não** roda este app:
> ele é Python. Precisa ser uma VPS.

## Antes de começar

- **VPS com Ubuntu 24.04 LTS**: 2 GB de RAM, 2 vCPU e pelo menos 50 GB de
  disco. Chapas de DTF ocupam de 50 a 500 MB por pedido, então o disco
  enche rápido; dá para aumentar depois.
- **Domínio** com um registro DNS tipo `A` apontando para o IP da VPS
  (`seusite.com.br` e `www.seusite.com.br`).
- **`config.json` com senhas fortes** em `admin_key` e em `admins` (12 ou
  mais caracteres, misturando letras e números), **guardadas como hash**: rode
  `python app.py --hash-senha`, digite a senha e cole o resultado
  (`pbkdf2_sha256$...`) no lugar dela. Assim quem abrir o arquivo não lê a
  senha. Esqueceu? Gere um hash novo do mesmo jeito.
- **`.env`** (opcional): copie o `.env.example` para `.env` e preencha
  `OPENAI_API_KEY` (IA) e `GOOGLE_CLIENT_ID` (login com Google). Sem ele o
  site funciona, só sem IA e sem Google.
- **Feche o servidor local** (janela do `start.bat`) antes de copiar, para
  o banco ir completo.

## 1. Preparar o servidor

**No servidor** (entre com `ssh root@IP-DA-VPS`):

```bash
apt update && apt upgrade -y
apt install -y python3-venv nginx certbot python3-certbot-nginx ufw
adduser --system --group --home /opt/ripfire ripfire
```

## 2. Enviar os arquivos

**No seu PC** (troque `IP-DA-VPS`):

```powershell
scp -r app.py requirements.txt config.json static data media deploy root@IP-DA-VPS:/opt/ripfire/
scp .env root@IP-DA-VPS:/opt/ripfire/   # só se você criou o .env
```

Não precisa enviar `.venv`, os `.zip` de backup, `orders`, `PRODUCAO_PAGA`
nem `SomenteDTFs`: as pastas vazias são criadas sozinhas. Ao iniciar, o app
corrige os caminhos do banco que vieram do Windows.

## 3. Instalar as dependências

**No servidor:**

```bash
chown -R ripfire:ripfire /opt/ripfire
sed -i 's/\r$//' /opt/ripfire/deploy/*   # tira o fim de linha do Windows
sudo -u ripfire python3 -m venv /opt/ripfire/.venv
sudo -u ripfire /opt/ripfire/.venv/bin/pip install -r /opt/ripfire/requirements.txt
```

## 4. Ligar o app como serviço

**No servidor:**

```bash
cp /opt/ripfire/deploy/ripfire.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ripfire
systemctl status ripfire --no-pager
curl -s http://127.0.0.1:8787/api/config | head -c 120; echo
```

O `status` deve mostrar `active (running)` e o `curl` deve mostrar o começo
de um JSON com `"store_name"`. Se der erro, veja o log com
`journalctl -u ripfire -n 50 --no-pager`.

## 5. Configurar o nginx

**No servidor** (troque `seusite.com.br` pelo seu domínio):

```bash
sed -i 's/SEU-DOMINIO.com.br/seusite.com.br/g' /opt/ripfire/deploy/nginx-ripfire.conf
cp /opt/ripfire/deploy/nginx-ripfire.conf /etc/nginx/sites-available/ripfire
ln -s /etc/nginx/sites-available/ripfire /etc/nginx/sites-enabled/ripfire
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

Agora `http://seusite.com.br` já abre o site (ainda sem cadeado).

## 6. Firewall

**No servidor:**

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable
```

Libera só SSH e o site; a porta 8787 do app fica fechada para fora.

## 7. HTTPS (cadeado)

**No servidor:**

```bash
certbot --nginx -d seusite.com.br -d www.seusite.com.br
```

Informe seu e-mail e aceite os termos. O certbot instala o certificado,
redireciona o HTTP para HTTPS e renova sozinho a cada 90 dias.

## 8. Serviços externos

- **Mercado Pago:** no `.env` do servidor, coloque o Access Token em
  `MERCADOPAGO_ACCESS_TOKEN` e o endereço do site em
  `SITE_URL=https://seusite.com.br`, e reinicie com
  `systemctl restart ripfire`. Com o `SITE_URL`, o cliente volta sozinho
  para o site depois de pagar e o Mercado Pago avisa cada pagamento em
  `/api/payments/mercadopago` (não precisa configurar webhook no painel).
  Comece com a credencial de teste (`TEST-...`) e pague com um usuário de
  teste; quando um pedido de teste for aprovado sozinho, troque pela de
  produção.
- **Login com Google:** no Google Cloud Console, em *Credenciais* → seu
  ID do cliente OAuth → *Origens JavaScript autorizadas*, adicione
  `https://seusite.com.br` e `https://www.seusite.com.br`.
- **OpenAI:** em *Settings → Limits*, defina um limite de gasto mensal. O
  site já limita cada conta a 20 artes por dia (`ai_daily_limit` no
  `config.json`), mas o teto na OpenAI é a garantia final.

## 9. Backup diário

**No servidor:**

```bash
cp /opt/ripfire/deploy/backup.sh /etc/cron.daily/ripfire-backup
chmod +x /etc/cron.daily/ripfire-backup
/etc/cron.daily/ripfire-backup && ls -lh /var/backups/ripfire
```

Roda todo dia e guarda 14 dias em `/var/backups/ripfire` (banco + arquivos de pedidos e artes). Além dele, o próprio app copia o banco sozinho a cada 24 h para `/opt/ripfire/backups` (Admin → "Backup dos dados"). Para restaurar um banco: `systemctl stop ripfire`, depois `sudo -u ripfire /opt/ripfire/.venv/bin/python /opt/ripfire/app.py --restaurar` (lista os backups) e de novo com o arquivo escolhido, e `systemctl start ripfire`. Esse backup fica
**na mesma máquina**: ative também os snapshots automáticos no painel da
VPS, ou baixe os backups de vez em quando:

```powershell
scp "root@IP-DA-VPS:/var/backups/ripfire/*" .\backups_vps\
```

## Uso no dia a dia

- **Admin:** `https://seusite.com.br/#admin`.
- **Pagamentos:** pedido pago no Mercado Pago é liberado sozinho. Pagou por
  fora (Pix direto, dinheiro)? Use **MARCAR COMO PAGO** no Admin.
- **Arquivos de produção:** em *Admin → Pedidos*, o botão **BAIXAR ZIP**
  traz as artes, as fotos de posição e os dados do pedido. Extraia na pasta
  que o RIP lê.
- **Ver o log:** `journalctl -u ripfire -f` (Ctrl+C para sair).

## Atualizar o site

**No seu PC**, envie só o que mudou, normalmente `app.py` e `static`:

```powershell
scp -r app.py static root@IP-DA-VPS:/opt/ripfire/
ssh root@IP-DA-VPS "chown -R ripfire:ripfire /opt/ripfire && systemctl restart ripfire"
```

Se o `requirements.txt` mudar, rode de novo o `pip install` do passo 3
antes do `restart`.

## Antes de abrir para clientes

- [ ] Mercado Pago com a credencial de **produção** e `SITE_URL` no `.env`
- [ ] Senhas fortes de admin no `config.json`
- [ ] Número do WhatsApp no link do rodapé (`static/index.html`)
- [ ] Um pedido de teste completo pelo domínio, com HTTPS: cadastro,
      pedido, pagamento, BAIXAR ZIP

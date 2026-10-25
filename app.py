from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel
from starlette.background import BackgroundTask

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

# Depois do .env: DATABASE_URL e SUPABASE_* podem vir dele.
import cloud_storage  # noqa: E402
import dbcompat  # noqa: E402
from dbcompat import IS_PG, db  # noqa: E402

# O limite padrão do Pillow (~179 Mpx) recusava chapas de DTF acima de
# ~2,2 m, e sem a medida da chapa o pedido não tem preço. Aqui só lemos o
# cabeçalho das imagens (image_info), nunca decodificamos os pixels. O Pillow
# só recusa acima do dobro deste valor (800 Mpx), bem acima do maior canvas
# que o navegador gera (~6.900 × 32.767 px ≈ 226 Mpx).
Image.MAX_IMAGE_PIXELS = 400_000_000

def load_config() -> dict[str, Any]:
    """config.json da pasta do projeto. No Render ele não vai pro GitHub
    (tem as senhas): sobe como "Secret File", que o Render coloca em
    /etc/secrets/config.json. CONFIG_JSON (o conteúdo inteiro numa variável
    de ambiente) ou CONFIG_PATH também valem."""
    raw = os.getenv("CONFIG_JSON", "").strip()
    if raw:
        return json.loads(raw)
    for candidate in (os.getenv("CONFIG_PATH", ""), BASE / "config.json", "/etc/secrets/config.json"):
        if candidate and Path(candidate).is_file():
            return json.loads(Path(candidate).read_text(encoding="utf-8"))
    sys.exit("config.json não encontrado. Copie o config.example.json para config.json "
             "(no Render: Environment > Secret Files > config.json).")


CONFIG = load_config()
DB_PATH = BASE / "data" / "studio.db"
dbcompat.configure_sqlite(DB_PATH)
# Supabase Storage (None no PC): ver cloud_storage.py.
STORE = cloud_storage.from_env(BASE)
ARTWORKS_DIR = BASE / "media" / "artworks"
GENERATED_DIR = BASE / "media" / "generated"
MOCKUPS_DIR = BASE / "media" / "mockups"
ORDERS_DIR = BASE / "orders"
SOMENTE_DTF_DIR = BASE / "SomenteDTFs"
# Cópias reduzidas das artes digitais (miniaturas e a versão de trabalho das
# premium). Fica em data/, fora de /media: nada aqui é servido direto.
DIGITAL_CACHE_DIR = BASE / "data" / "digital_cache"
# Originais da biblioteca de artes digitais: só saem pela rota com login.
LIBRARY_DIR = BASE / "data" / "digital_library"
# Os dois lados fixos da biblioteca (o id é também o acesso das pastas de dentro).
LIBRARY_SIDES = {"free": "Artes Grátis", "premium": "Artes Premium"}

_production_drop = Path(CONFIG.get("production_drop_folder", "./PRODUCAO_PAGA"))
PRODUCTION_DROP = (
    _production_drop if _production_drop.is_absolute() else (BASE / _production_drop).resolve()
)
if STORE:
    # No servidor a pasta de produção do config (ex.: D:\PEDIDOS_DTF_PAGOS)
    # não existe: quem leva os pedidos pagos pra ela é o sync_producao.py,
    # rodando no PC da produção.
    PRODUCTION_DROP = BASE / "PRODUCAO_PAGA"

# Backups automáticos do banco (ver "Backup dos dados" mais abaixo).
BACKUP_CFG: dict[str, Any] = CONFIG.get("backup") or {}
_backup_dir = Path(BACKUP_CFG.get("folder") or "./backups")
BACKUP_DIR = _backup_dir if _backup_dir.is_absolute() else (BASE / _backup_dir).resolve()

for p in (DB_PATH.parent, BACKUP_DIR, ARTWORKS_DIR, GENERATED_DIR, MOCKUPS_DIR, ORDERS_DIR, SOMENTE_DTF_DIR, PRODUCTION_DROP, DIGITAL_CACHE_DIR, LIBRARY_DIR):
    p.mkdir(parents=True, exist_ok=True)


# ---------- Arquivos no Supabase Storage ----------
# Sem STORE (no PC) estas funções só olham o disco. Com STORE (no Render),
# o que é gravado também sobe pro Storage, e o que sumiu do disco (deploy
# novo, reinício) é baixado de volta quando alguém precisa.
def cloud_put(*paths: Path):
    if STORE:
        for p in paths:
            STORE.upload(p)


def cloud_put_dir(folder: Path):
    if STORE and folder.is_dir():
        STORE.upload_dir(folder)


def cloud_get(path: Path | str) -> bool:
    """True se o arquivo está no disco (baixando do Storage se precisar)."""
    path = Path(path)
    if path.is_file():
        return True
    if not STORE:
        return False
    try:
        return STORE.download(path)
    except (cloud_storage.StorageError, ValueError) as exc:
        print(f"[storage] {exc}")
        return False


def cloud_get_dir(folder: Path | str):
    if STORE and folder:
        try:
            STORE.download_dir(Path(folder))
        except (cloud_storage.StorageError, ValueError) as exc:
            print(f"[storage] {exc}")


def cloud_delete(*paths: Path | str):
    if STORE:
        try:
            STORE.delete(*paths)
        except (cloud_storage.StorageError, ValueError) as exc:
            print(f"[storage] não apagou {paths}: {exc}")


def cloud_delete_dir(folder: Path | str):
    if STORE and folder:
        try:
            STORE.delete_dir(Path(folder))
        except (cloud_storage.StorageError, ValueError) as exc:
            print(f"[storage] não apagou {folder}: {exc}")


def init_db():
    if IS_PG:
        with db() as con:
            dbcompat.init_pg_schema(con)
            seed_library_sides(con)
            relocate_paths(con)
        return
    with db() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS artworks (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            file_path TEXT NOT NULL,
            preview_url TEXT NOT NULL,
            digital_price REAL NOT NULL DEFAULT 0,
            can_customize INTEGER NOT NULL DEFAULT 1,
            can_sell_digital INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            origin TEXT NOT NULL DEFAULT 'ADMIN'
        );

        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            order_number TEXT UNIQUE NOT NULL,
            created_at REAL NOT NULL,
            customer_name TEXT NOT NULL,
            customer_email TEXT,
            customer_whatsapp TEXT,
            sale_type TEXT NOT NULL,
            status TEXT NOT NULL,
            payment_status TEXT NOT NULL DEFAULT 'PENDING',
            total REAL NOT NULL,
            folder_path TEXT NOT NULL,
            production_drop_path TEXT,
            data_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shirt_mockups (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            model_id TEXT NOT NULL,
            color TEXT NOT NULL,
            side TEXT NOT NULL DEFAULT 'FRONT',
            file_path TEXT NOT NULL,
            preview_url TEXT NOT NULL,
            print_x REAL NOT NULL DEFAULT 30,
            print_y REAL NOT NULL DEFAULT 28,
            print_w REAL NOT NULL DEFAULT 40,
            print_h REAL NOT NULL DEFAULT 45
        );

        CREATE TABLE IF NOT EXISTS customers (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL DEFAULT '',
            cep TEXT NOT NULL DEFAULT '',
            address_number TEXT NOT NULL DEFAULT '',
            document TEXT NOT NULL DEFAULT '',
            google_sub TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS customer_sessions (
            token TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS library_categories (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            name TEXT NOT NULL,
            parent_id TEXT,
            access TEXT NOT NULL DEFAULT 'free'
        );

        CREATE TABLE IF NOT EXISTS library_arts (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            category_id TEXT NOT NULL,
            name TEXT NOT NULL,
            file_path TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS page_hits (
            day TEXT NOT NULL,
            page TEXT NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, page)
        );

        CREATE TABLE IF NOT EXISTS privacy_requests (
            id TEXT PRIMARY KEY,
            protocol TEXT UNIQUE NOT NULL,
            created_at REAL NOT NULL,
            customer_id TEXT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            document TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ABERTO',
            answered_at REAL
        );

        CREATE TABLE IF NOT EXISTS digital_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            customer_id TEXT NOT NULL,
            art_id TEXT NOT NULL,
            art_name TEXT NOT NULL
        );
        """)

        customer_cols = {r["name"] for r in con.execute("PRAGMA table_info(customers)").fetchall()}
        if "google_sub" not in customer_cols:
            # Bancos criados antes do login com Google tinham password_hash e os
            # dados de endereço como NOT NULL sem default. SQLite não permite
            # relaxar uma constraint em coluna existente, então recriamos a
            # tabela preservando os dados já cadastrados.
            con.executescript("""
            ALTER TABLE customers RENAME TO customers_old;
            CREATE TABLE customers (
                id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                cep TEXT NOT NULL DEFAULT '',
                address_number TEXT NOT NULL DEFAULT '',
                document TEXT NOT NULL DEFAULT '',
                google_sub TEXT UNIQUE
            );
            INSERT INTO customers (id,created_at,email,password_hash,full_name,phone,cep,address_number,document)
                SELECT id,created_at,email,password_hash,full_name,phone,cep,address_number,document FROM customers_old;
            DROP TABLE customers_old;
            """)
        customer_cols = {r["name"] for r in con.execute("PRAGMA table_info(customers)").fetchall()}
        if "premium_until" not in customer_cols:
            # Fim do acesso premium (timestamp); 0 = nunca assinou.
            con.execute("ALTER TABLE customers ADD COLUMN premium_until REAL NOT NULL DEFAULT 0")
        if "terms_accepted_at" not in customer_cols:
            # Quando aceitou Termos e Política (LGPD: a loja precisa provar o aceite).
            # 0 = conta de antes desse registro.
            con.execute("ALTER TABLE customers ADD COLUMN terms_accepted_at REAL NOT NULL DEFAULT 0")

        cols ={r["name"] for r in con.execute("PRAGMA table_info(orders)").fetchall()}
        if "payment_status" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'PENDING'")
        if "production_drop_path" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN production_drop_path TEXT")
        if "customer_id" not in cols:
            # Pedidos antigos ficam sem dono e entram em "Minha conta" pelo e-mail.
            con.execute("ALTER TABLE orders ADD COLUMN customer_id TEXT")
        if "payment_ref" not in cols:
            # Id do pagamento no Mercado Pago: é por ele que se acha a venda no painel de lá.
            con.execute("ALTER TABLE orders ADD COLUMN payment_ref TEXT")
        if "stage" not in cols:
            # Etapa da produção que o Admin acompanha (ver ORDER_STAGES).
            con.execute("ALTER TABLE orders ADD COLUMN stage TEXT NOT NULL DEFAULT ''")
            con.execute("ALTER TABLE orders ADD COLUMN stage_at REAL NOT NULL DEFAULT 0")
        con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("""CREATE TABLE IF NOT EXISTS staff_users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                       role TEXT NOT NULL, created_at REAL NOT NULL, created_by TEXT NOT NULL DEFAULT '')""")
        con.execute("""CREATE TABLE IF NOT EXISTS staff_totp (username TEXT PRIMARY KEY, secret TEXT NOT NULL,
                       enabled INTEGER NOT NULL DEFAULT 0, last_counter INTEGER NOT NULL DEFAULT 0)""")
        con.execute("""CREATE TABLE IF NOT EXISTS staff_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                       created_at REAL NOT NULL)""")

        mockup_cols = {r["name"] for r in con.execute("PRAGMA table_info(shirt_mockups)").fetchall()}
        if "side" not in mockup_cols:
            con.execute("ALTER TABLE shirt_mockups ADD COLUMN side TEXT NOT NULL DEFAULT 'FRONT'")

        seed_library_sides(con)
        relocate_paths(con)


def seed_library_sides(con: Any):
    """Cria os lados Grátis e Premium uma vez só, já com 8 pastas para renomear."""
    for side, label in LIBRARY_SIDES.items():
        if con.execute("SELECT 1 FROM library_categories WHERE id=?", (side,)).fetchone():
            continue
        now = time.time()
        con.executemany(
            "INSERT INTO library_categories (id,created_at,name,parent_id,access) VALUES(?,?,?,?,?)",
            [(side, now, label, None, side)]
            + [(uuid.uuid4().hex[:12], now + i / 1000, f"pasta{i:02d}", side, side) for i in range(1, 9)]
        )


def relocate_paths(con: Any):
    """O banco guarda caminhos absolutos. Com o projeto copiado pra outra
    pasta ou pro servidor (C:\\Users\\... no Linux), eles apontariam pro lugar
    antigo e biblioteca, mockups e pedidos dariam "arquivo não encontrado".
    Cada coluna mora numa pasta conhecida: refaz o caminho pela pasta atual."""
    for table, col, folder in (
        ("artworks", "file_path", ARTWORKS_DIR),
        ("shirt_mockups", "file_path", MOCKUPS_DIR),
        ("library_arts", "file_path", LIBRARY_DIR),
        ("orders", "folder_path", ORDERS_DIR),
        ("orders", "production_drop_path", PRODUCTION_DROP),
    ):
        # Pelo id (existe nas duas bases); o rowid é só do SQLite.
        rows = con.execute(f"SELECT id, {col} AS p FROM {table} WHERE {col} != ''").fetchall()
        for row in rows:
            # Separa por \ e por /: o caminho pode ter vindo do Windows.
            new = str(folder / re.split(r"[\\/]", row["p"].rstrip("\\/"))[-1])
            if new != row["p"]:
                con.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (new, row["id"]))


def safe_name(value: str) -> str:
    out: list[str] = []
    for ch in value.strip():
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch.isspace():
            out.append("_")
    return "".join(out)[:80] or "arquivo"


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


# ---------- Senhas ----------
# Senha nunca é guardada nem dá pra ser recuperada: guardamos só um hash
# PBKDF2-SHA256 com sal aleatório e muitas iterações (lento de propósito, pra
# quem roubar o banco não conseguir testar milhões de senhas por segundo).
# Formato: pbkdf2_sha256$<iterações>$<sal>$<hash>. As iterações ficam no
# próprio hash: dá pra subir PWD_ITERATIONS no futuro e as senhas antigas
# continuam valendo (e são refeitas no próximo login).
PWD_SCHEME = "pbkdf2_sha256"
PWD_ITERATIONS = 600_000   # recomendação da OWASP para PBKDF2-SHA256 (≈150 ms por login)
PWD_MIN_LENGTH = 8
PWD_MAX_LENGTH = 128       # limite só pra ninguém mandar um texto gigante pro hash


def _pbkdf2(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    return f"{PWD_SCHEME}${PWD_ITERATIONS}${salt}${_pbkdf2(password, salt, PWD_ITERATIONS)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Confere em tempo constante. Aceita o formato atual e o antigo
    (`sal$hash`, 100 mil iterações), que é refeito no próximo login."""
    if not stored or len(password) > PWD_MAX_LENGTH:
        return False
    parts = stored.split("$")
    if len(parts) == 4 and parts[0] == PWD_SCHEME and parts[1].isdigit():
        expected, salt, iterations = parts[3], parts[2], int(parts[1])
    elif len(parts) == 2:
        expected, salt, iterations = parts[1], parts[0], 100_000
    else:
        return False
    return hmac.compare_digest(_pbkdf2(password, salt, iterations), expected)


def password_needs_upgrade(stored: str | None) -> bool:
    parts = (stored or "").split("$")
    return not (len(parts) == 4 and parts[0] == PWD_SCHEME and parts[1] == str(PWD_ITERATIONS))


def check_new_password(password: str, what: str = "A senha"):
    if len(password) < PWD_MIN_LENGTH:
        raise HTTPException(400, f"{what} precisa ter pelo menos {PWD_MIN_LENGTH} caracteres.")
    if len(password) > PWD_MAX_LENGTH:
        raise HTTPException(400, f"{what} pode ter no máximo {PWD_MAX_LENGTH} caracteres.")


# Hash de uma senha qualquer: o login confere contra ele quando o e-mail não
# existe, pra demorar o mesmo tanto. Sem isso, o tempo da resposta entregava
# quais e-mails têm conta.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def public_customer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "full_name": row["full_name"],
        "phone": row["phone"],
        "cep": row["cep"],
        "address_number": row["address_number"],
        "document": row["document"],
        "premium_until": row.get("premium_until") or 0,
        "created_at": row.get("created_at") or 0,
        # Conta criada pelo Google não tem senha: o perfil mostra "Criar senha".
        "has_password": bool(row.get("password_hash")),
    }


def clean_profile(full_name: str, phone: str, cep: str, address_number: str, document: str) -> dict[str, str]:
    """Validação dos dados do cliente, igual no cadastro e no "Meus dados"."""
    profile = {
        "full_name": full_name.strip(),
        "phone": only_digits(phone),
        "cep": only_digits(cep),
        "address_number": address_number.strip(),
        "document": only_digits(document),
    }
    if not profile["full_name"]:
        raise HTTPException(400, "Informe o nome completo.")
    if profile["phone"] and len(profile["phone"]) < 8:
        raise HTTPException(400, "Telefone inválido.")
    if profile["cep"] and len(profile["cep"]) != 8:
        raise HTTPException(400, "CEP inválido, use o formato 00000-000.")
    if profile["document"] and len(profile["document"]) not in (11, 14):
        raise HTTPException(400, "CPF/CNPJ inválido.")
    return profile


# Depois disso o login vence e o cliente entra de novo (um token vazado não
# vale pra sempre).
SESSION_DAYS = 30


def new_session(customer: dict[str, Any]) -> dict[str, Any]:
    """Resposta do login/cadastro: os dados do cliente mais um token de
    sessão. Antes o login ficava só no navegador e o servidor não tinha como
    saber quem estava pedindo; o premium e o download grátis dependem disso."""
    token = secrets.token_urlsafe(32)
    with db() as con:
        con.execute(
            "DELETE FROM customer_sessions WHERE created_at < ?",
            (time.time() - SESSION_DAYS * 86400,)
        )
        con.execute(
            "INSERT INTO customer_sessions (token,customer_id,created_at) VALUES(?,?,?)",
            (token, customer["id"], time.time())
        )
    return {**public_customer(customer), "token": token}


def customer_from_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    with db() as con:
        row = con.execute(
            """SELECT c.* FROM customer_sessions s JOIN customers c ON c.id=s.customer_id
               WHERE s.token=? AND s.created_at > ?""",
            (token, time.time() - SESSION_DAYS * 86400)
        ).fetchone()
    return dict(row) if row else None


def require_customer(x_customer_token: str | None = Header(None)) -> dict[str, Any]:
    customer = customer_from_token(x_customer_token)
    if not customer:
        raise HTTPException(401, "Entre na sua conta de novo.")
    return customer


def premium_active(customer: dict[str, Any]) -> bool:
    return float(customer.get("premium_until") or 0) > time.time()


class CustomerRegister(BaseModel):
    full_name: str
    email: str
    password: str
    phone: str = ""
    cep: str = ""
    address_number: str = ""
    document: str = ""


class CustomerLogin(BaseModel):
    email: str
    password: str


class GoogleAuth(BaseModel):
    credential: str


class ProfileUpdate(BaseModel):
    full_name: str
    phone: str = ""
    cep: str = ""
    address_number: str = ""
    document: str = ""


class PasswordChange(BaseModel):
    current_password: str = ""
    new_password: str


# ---------- Equipe do Admin: sessão, 2FA e papéis ----------
# Papéis: "admin" faz tudo; "producao" só vê os pedidos, baixa os arquivos e
# muda a etapa (é o login do PC da impressão). Os usuários do config.json
# (admin_user e "admins") são admin, salvo "role" no item; os criados pelo
# Admin ficam na tabela staff_users.
# Entrar: POST /api/admin/login com usuário, senha e, se a pessoa ligou o 2FA,
# o código de 6 dígitos do app autenticador. Volta um token de sessão (12 h);
# a senha não trafega mais em toda chamada.
ROLES = {"admin": "Admin", "producao": "Produção"}
STAFF_SESSION_SECONDS = 12 * 3600


def config_staff() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    items = [{"user": CONFIG.get("admin_user"), "key": CONFIG.get("admin_key"), "role": "admin"}]
    items += list(CONFIG.get("admins") or [])
    for a in items:
        user, key = str(a.get("user") or ""), str(a.get("key") or "")
        if user and key and not key.startswith("TROQUE-"):
            role = a.get("role") if a.get("role") in ROLES else "admin"
            out.setdefault(user, {"user": user, "key": key, "role": role, "source": "config"})
    return out


def staff_account(user: str) -> dict[str, str] | None:
    cfg = config_staff()
    if user in cfg:
        return cfg[user]
    with db() as con:
        row = con.execute("SELECT username, password_hash, role FROM staff_users WHERE username=?", (user,)).fetchone()
    return {"user": row["username"], "key": row["password_hash"], "role": row["role"], "source": "equipe"} if row else None


def staff_totp_row(user: str) -> dict[str, Any] | None:
    with db() as con:
        row = con.execute("SELECT * FROM staff_totp WHERE username=?", (user,)).fetchone()
    return dict(row) if row else None


def totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    off = digest[-1] & 15
    return f"{(int.from_bytes(digest[off:off + 4], 'big') & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_match(secret: str, code: str, last_counter: int = 0) -> int | None:
    """Confere o código (aceita 30 s de diferença no relógio). Devolve o
    contador usado; código já usado (last_counter) não vale de novo."""
    code = re.sub(r"\D", "", code or "")
    if len(code) != 6:
        return None
    now = int(time.time() // 30)
    for counter in (now - 1, now, now + 1):
        if counter > last_counter and secrets.compare_digest(totp_code(secret, counter), code):
            return counter
    return None


def staff_has_2fa(user: str) -> bool:
    row = staff_totp_row(user)
    return bool(row and row["enabled"])


def check_staff_password(request: Request, user: str, password: str) -> dict[str, str]:
    """Senha da equipe, com o limite de tentativas por IP e por usuário."""
    fail_keys = [f"admin-ip:{client_ip(request)}", f"admin-user:{user.lower()[:100]}"]
    login_guard(fail_keys)
    account = staff_account(user) if user else None
    # Sem usuário certo, confere um hash qualquer: o tempo de resposta não entrega quem existe.
    ok = admin_key_matches(password, account["key"] if account else _DUMMY_HASH)
    if not account or not ok:
        login_failed(fail_keys, "Usuário ou senha de admin inválidos.")
    return account


def new_staff_session(user: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db() as con:
        con.execute("DELETE FROM staff_sessions WHERE created_at < ?", (now - STAFF_SESSION_SECONDS,))
        con.execute("INSERT INTO staff_sessions (token_hash, username, created_at) VALUES(?,?,?)",
                    (hashlib.sha256(token.encode()).hexdigest(), user, now))
    return token


def staff_from_request(request: Request, token: str | None, user: str | None, key: str | None) -> dict[str, Any]:
    if token:
        with db() as con:
            row = con.execute("SELECT username, created_at FROM staff_sessions WHERE token_hash=?",
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        account = staff_account(row["username"]) if row and time.time() - row["created_at"] < STAFF_SESSION_SECONDS else None
        if not account:
            raise HTTPException(401, "Sua sessão do Admin expirou. Entre de novo.")
        return {"user": account["user"], "role": account["role"], "token": token}
    if not config_staff():
        raise HTTPException(500, "admin_key ainda não foi trocada em config.json. Configure antes de usar o Admin.")
    user, key = user or "", key or ""
    if not user and not key:
        # Sem nada não é tentativa de login (robô varrendo /api/admin): recusa sem contar.
        raise HTTPException(401, "Entre no Admin.")
    # Usuário e senha no header (programas antigos): só pra quem não ligou o 2FA,
    # senão o 2FA seria contornado.
    account = check_staff_password(request, user, key)
    if staff_has_2fa(account["user"]):
        raise HTTPException(401, "Esse usuário usa verificação em duas etapas: entre pelo login do Admin.")
    return {"user": account["user"], "role": account["role"], "token": None}


def require_staff(
    request: Request,
    x_admin_token: str | None = Header(None),
    x_admin_key: str | None = Header(None),
    x_admin_user: str | None = Header(None),
) -> dict[str, Any]:
    """Qualquer pessoa da equipe (admin ou produção)."""
    return staff_from_request(request, x_admin_token, x_admin_user, x_admin_key)


def require_admin(
    request: Request,
    x_admin_token: str | None = Header(None),
    x_admin_key: str | None = Header(None),
    x_admin_user: str | None = Header(None),
) -> dict[str, Any]:
    """Protege as rotas do Admin. Sem isso, qualquer visitante veria os
    pedidos com dados de clientes ou liberaria pedido como pago sem pagar.
    Só o papel "admin"; a produção usa require_staff."""
    staff = staff_from_request(request, x_admin_token, x_admin_user, x_admin_key)
    if staff["role"] != "admin":
        raise HTTPException(403, "Seu usuário não tem acesso a essa parte do Admin.")
    return staff


# Senhas de admin que já bateram com o hash nesta execução. O Admin manda a
# senha em toda chamada; refazer o PBKDF2 (≈150 ms) em cada uma deixaria o
# painel lento. A chave do cache é um HMAC com segredo aleatório do processo:
# nem a senha nem algo que dê pra testar offline fica na memória.
_ADMIN_OK: set[str] = set()
_ADMIN_CACHE_SECRET = secrets.token_bytes(32)


def admin_key_matches(given: str, stored: str) -> bool:
    """`stored` pode ser o hash (pbkdf2_sha256$..., gerado com
    `python app.py --hash-senha`) ou, por compatibilidade, a senha em texto."""
    if not stored.startswith(PWD_SCHEME + "$"):
        return secrets.compare_digest(given.encode(), stored.encode())
    tag = hmac.new(_ADMIN_CACHE_SECRET, f"{stored}\0{given}".encode(), "sha256").hexdigest()
    if tag in _ADMIN_OK:
        return True
    if verify_password(given, stored):
        _ADMIN_OK.add(tag)
        return True
    return False


_RATE_LIMITS: dict[str, list[float]] = {}


# Cabeçalho com o IP real do visitante, posto pelo proxy da hospedagem (que
# apaga o que o visitante mandar). Sem ele, vale o IP da conexão. O
# X-Forwarded-For sozinho não serve: o primeiro IP da lista é o visitante quem
# escreve, e trocá-lo a cada tentativa furaria o limite de senha por IP.
CLIENT_IP_HEADER = os.getenv("CLIENT_IP_HEADER", "").strip().lower()


def client_ip(request: Request) -> str:
    if CLIENT_IP_HEADER:
        value = (request.headers.get(CLIENT_IP_HEADER) or "").split(",")[-1].strip()
        if value:
            return value[:64]
    return request.client.host if request.client else "unknown"


def check_rate_limit(key: str, limit: int, window: float, message: str, record: bool = True):
    """Recusa (429) quando `key` já teve `limit` registros nos últimos
    `window` segundos; com record=True este pedido também conta. Fica em
    memória: zera quando o servidor reinicia e não divide entre vários
    processos, mas segura força bruta e abuso básico sem precisar de Redis."""
    now = time.time()
    if len(_RATE_LIMITS) > 10_000:  # muitos IPs diferentes: descarta os parados há um dia
        for k in [k for k, v in _RATE_LIMITS.items() if not v or now - v[-1] > 86400]:
            del _RATE_LIMITS[k]
    hits = _RATE_LIMITS[key] = [t for t in _RATE_LIMITS.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, message)
    if record:
        hits.append(now)


def record_hit(key: str):
    _RATE_LIMITS.setdefault(key, []).append(time.time())


# ---------- Tentativas de senha ----------
# Login do cliente, login do Admin, "senha atual" na troca de senha e senha na
# exclusão da conta: depois de `max_attempts` erros (padrão 5) em `lock_minutes`
# (padrão 15), bloqueia. Conta por IP E pela conta (e-mail/usuário): trocar de
# IP não dá mais chances na mesma conta. Senha certa zera a contagem.
# A contagem vale pra e-mail que nem existe: o bloqueio não entrega quem tem conta.
LOGIN_CFG: dict[str, Any] = CONFIG.get("login") or {}
LOGIN_MAX_ATTEMPTS = int(LOGIN_CFG.get("max_attempts") or 5)
LOGIN_LOCK_SECONDS = float(LOGIN_CFG.get("lock_minutes") or 15) * 60


def _recent_failures(key: str) -> list[float]:
    now = time.time()
    hits = _RATE_LIMITS[key] = [t for t in _RATE_LIMITS.get(key, []) if now - t < LOGIN_LOCK_SECONDS]
    return hits


def login_guard(keys: list[str]):
    """Recusa (429) se qualquer uma das chaves já está bloqueada, dizendo quanto falta."""
    for key in keys:
        hits = _recent_failures(key)
        if len(hits) >= LOGIN_MAX_ATTEMPTS:
            wait = max(1, round((LOGIN_LOCK_SECONDS - (time.time() - hits[0])) / 60))
            raise HTTPException(429, f"Muitas tentativas erradas. Tente de novo em {wait} minuto{'s' if wait > 1 else ''}.")


def login_failed(keys: list[str], message: str, status: int = 401):
    """Registra o erro e responde dizendo quantas tentativas sobram."""
    for key in keys:
        record_hit(key)
    left = LOGIN_MAX_ATTEMPTS - max(len(_recent_failures(k)) for k in keys)
    if left <= 0:
        raise HTTPException(429, f"{message} Você errou {LOGIN_MAX_ATTEMPTS} vezes: tente de novo em "
                                 f"{round(LOGIN_LOCK_SECONDS / 60)} minutos.")
    raise HTTPException(status, f"{message} {'Resta 1 tentativa' if left == 1 else f'Restam {left} tentativas'}.")


def login_ok(keys: list[str]):
    for key in keys:
        _RATE_LIMITS.pop(key, None)


def check_order_rate_limit(customer: dict[str, Any]):
    # Pedidos e premium somados: 20 por hora numa conta já é folga pra cliente de verdade.
    check_rate_limit(f"order:{customer['id']}", 20, 3600,
                     "Muitos pedidos em pouco tempo. Aguarde um pouco e tente de novo.")


def next_order_number() -> str:
    stamp = time.strftime("%y%m%d")
    with db() as con:
        rows = con.execute(
            "SELECT order_number FROM orders WHERE order_number LIKE ?",
            (f"{stamp}-%",)
        ).fetchall()
    # Pelo maior número do dia, não pela contagem: com um pedido apagado do
    # banco, a contagem repetia o número de um pedido que ainda existe.
    last = max((int(r["order_number"].split("-")[1]) for r in rows), default=0)
    return f"{stamp}-{last + 1:04d}"


def image_info(path: Path):
    result: dict[str, int | bool | None] = {"width_px": None, "height_px": None, "has_alpha": None}
    try:
        with Image.open(path) as im:
            result["width_px"], result["height_px"] = im.size
            result["has_alpha"] = (
                im.mode in ("RGBA", "LA")
                or (im.mode == "P" and "transparency" in im.info)
            )
    except Exception:
        pass
    return result


def png_with_dpi(head: bytes, dpi: int) -> bytes:
    """Insere o bloco pHYs (DPI) logo depois do IHDR, se o PNG ainda não tiver
    um. `head` é o começo do arquivo; o IHDR sempre ocupa os bytes 8 a 33."""
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return head
    if b"pHYs" in head[:head.find(b"IDAT")]:
        return head
    ppm = round(dpi / 0.0254)  # o PNG guarda pixels por metro
    body = b"pHYs" + struct.pack(">IIB", ppm, ppm, 1)
    return head[:33] + struct.pack(">I", 9) + body + struct.pack(">I", zlib.crc32(body)) + head[33:]


async def save_upload(upload: UploadFile, path: Path):
    """Grava o arquivo enviado. O canvas do navegador não grava DPI no PNG, e
    sem isso o RIP/Photoshop abre a arte a 72 dpi, umas 4x maior do que o
    tamanho que o cliente escolheu. Aqui o PNG ganha o dpi do config.
    Acima de max_upload_mb o envio é recusado (o arquivo parcial fica na
    pasta do pedido, que o create_order apaga ao receber o erro)."""
    dpi = int(CONFIG.get("dpi") or 300)
    max_mb = int(CONFIG.get("max_upload_mb") or 600)
    written = 0
    first = True
    with path.open("wb") as f:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if written > max_mb * 1024 * 1024:
                raise HTTPException(413, f"Arquivo grande demais (máximo {max_mb} MB).")
            if first and path.suffix.lower() == ".png":
                chunk = png_with_dpi(chunk, dpi)
            first = False
            f.write(chunk)


# ---------- Biblioteca de artes digitais (hospedada no próprio site) ----------
# Categoria (parent_id vazio) -> subcategoria (parent_id = categoria) -> artes.
# O acesso (free/premium) fica na categoria ou subcategoria onde a arte está.
LIBRARY_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
THUMB_PX = 600  # prévia dos cartões (pública)


def digital_cfg() -> dict[str, Any]:
    return CONFIG.get("digital_arts") or {}


def library_art(art_id: str) -> dict[str, Any]:
    with db() as con:
        row = con.execute(
            """SELECT a.*, c.access FROM library_arts a
               JOIN library_categories c ON c.id=a.category_id WHERE a.id=?""",
            (art_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Arte não encontrada.")
    return dict(row)


def library_preview(art: dict[str, Any]) -> Path:
    """Prévia reduzida (webp, mantém a transparência), gerada uma vez e guardada em cache."""
    dest = DIGITAL_CACHE_DIR / f"lib_{art['id']}_{THUMB_PX}.webp"
    if not cloud_get(dest):
        if not cloud_get(art["file_path"]):
            raise HTTPException(404, "Arquivo da arte não encontrado.")
        with Image.open(art["file_path"]) as im:
            small = im.convert("RGBA")
        small.thumbnail((THUMB_PX, THUMB_PX), Image.Resampling.LANCZOS)
        small.save(dest, "WEBP", quality=88)
        try:
            cloud_put(dest)  # a próxima vez não precisa baixar o original
        except cloud_storage.StorageError as exc:
            print(f"[storage] miniatura não salva: {exc}")
    return dest


def remove_library_files(art_id: str, file_path: str):
    thumb = DIGITAL_CACHE_DIR / f"lib_{art_id}_{THUMB_PX}.webp"
    Path(file_path).unlink(missing_ok=True)
    thumb.unlink(missing_ok=True)
    cloud_delete(file_path, thumb)


def write_dtf_report(order_number: str, customer_name: str, sources: list[dict[str, Any]]) -> Path:
    """Relatório pro operador de DTF: nome, metragem e quantidade de cada
    arte, na ordem exata em que o cliente montou a chapa (sources já vem
    nessa ordem, direto da lista gangItems do editor)."""
    folder = SOMENTE_DTF_DIR / f"{order_number}__{safe_name(customer_name)}"
    folder.mkdir(parents=True, exist_ok=True)

    total_qty = sum(int(s.get("qty") or 0) for s in sources)
    lines = [
        "RELATORIO - SOMENTE DTF",
        f"Pedido: {order_number}",
        f"Cliente: {customer_name}",
        f"Data: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Quantidade total de pecas: {total_qty}",
        "",
        "Lista de artes (na ordem exata em que o cliente adicionou):",
    ]
    for i, s in enumerate(sources, start=1):
        name = s.get("name") or "Sem titulo"
        w = s.get("width_cm") or 0
        h = s.get("height_cm") or 0
        qty = s.get("qty") or 0
        lines.append(f"{i}. {name} - Metragem: {w} x {h} cm - Quantidade: {qty}")

    report_path = folder / "RELATORIO_ARTES.txt"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        cloud_put(report_path)
    except cloud_storage.StorageError as exc:
        # O pedido já está salvo; se o relatório faltar, a lista das artes
        # ainda está no pedido.json ("sources").
        print(f"[storage] relatório do pedido {order_number} não subiu: {exc}")
    return report_path


def calc_order_total(
    sale_type: str,
    data: dict[str, Any],
    digital_artwork_row: dict[str, Any] | None,
) -> float:
    """Preço calculado aqui, com os valores do config.json. O total que o
    navegador manda é só a estimativa da tela: antes ele ia direto pro
    banco, e qualquer um podia criar um pedido de R$ 0,01 editando o JSON."""
    pricing = CONFIG.get("pricing", {})

    if sale_type == "DIGITAL_ART":
        if not digital_artwork_row:
            raise HTTPException(400, "Arte digital não informada.")
        return round(float(digital_artwork_row["digital_price"]), 2)

    if sale_type == "DTF_ONLY":
        # Metragem medida no arquivo da chapa (é ele que vai pra impressão),
        # não no height_cm que o navegador informa.
        art = data.get("production_art") or {}
        width_px, height_px = art.get("width_px"), art.get("height_px")
        if not width_px or not height_px:
            raise HTTPException(400, "Envie o arquivo da chapa.")
        dpi = float(CONFIG.get("dpi") or 300)
        if width_px / dpi * 2.54 > float(CONFIG.get("dtf_sheet_width_cm") or 58.5) + 0.5:
            raise HTTPException(400, "A chapa é mais larga que o rolo de DTF.")
        height_cm = max(height_px / dpi * 2.54, float(CONFIG.get("dtf_sheet_min_height_cm") or 0))
        meters = height_cm / 100
        tiers = pricing.get("dtf_gang_sheet_tiers") or []
        price_per_m = tiers[-1]["price_per_m"] if tiers else 39.9
        for tier in tiers:
            if meters <= tier["up_to_m"]:
                price_per_m = tier["price_per_m"]
                break
        return round(max(meters, 0.1) * float(price_per_m), 2)

    # Camiseta (FINISHED_SHIRT / DTF_PLUS_SHIRT): preço por peça.
    product = data.get("product") or {}
    models = CONFIG.get("shirt_models") or []
    model = (
        next((m for m in models if m["id"] == product.get("shirt_model_id")), None)
        or next((m for m in models if m["name"] == product.get("shirt_model")), None)
    )
    if not model:
        raise HTTPException(400, "Modelo de camiseta inválido.")
    sent_sizes = product.get("sizes") or {}
    sizes: dict[str, int] = {}
    # O modelo pode ter tamanhos próprios (o infantil usa 2 a 16).
    for size in model.get("sizes") or CONFIG.get("sizes") or []:
        try:
            sizes[size] = max(0, int(sent_sizes.get(size) or 0))
        except (TypeError, ValueError):
            sizes[size] = 0
    qty = sum(sizes.values())
    if qty < 1:
        raise HTTPException(400, "Informe a quantidade em pelo menos um tamanho.")
    # Grava as quantidades já limpas: é isso que a produção vai ler.
    product["sizes"] = sizes
    data["product"] = product
    data["dtf_quantity"] = qty
    unit = float(pricing.get("dtf_only_unit", 0)) + float(model.get("base_price", 0))
    if sale_type == "FINISHED_SHIRT":
        unit += float(pricing.get("finished_press_unit", 0))
    return round(qty * unit, 2)


def load_order(order_id: str) -> dict[str, Any] | None:
    with db() as con:
        row = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    return dict(row) if row else None


def production_folder_name(row: dict[str, Any]) -> str:
    return f"{row['order_number']}__{safe_name(row['customer_name'])}__{safe_name(row['sale_type'])}"


def is_production_file(name: str) -> bool:
    """O que vai pra produção: as artes, as fotos de posição e o pedido.json."""
    return name == "pedido.json" or name.startswith(("ARTE_PRODUCAO", "POSICAO_", "ARTE_DIGITAL", "ORIGINAL_"))


def release_paid_order_to_production(order_id: str):
    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")
    if row["payment_status"] != "PAID":
        raise HTTPException(400, "Pedido ainda não está pago.")

    source_folder = Path(row["folder_path"])
    if not source_folder.exists() and not (STORE and STORE.list_files(source_folder)):
        raise HTTPException(500, "Pasta original do pedido não encontrada.")

    data = json.loads(row["data_json"])
    drop_folder = PRODUCTION_DROP / production_folder_name(row)
    readme = drop_folder / "LEIA-ME-PRODUCAO.json"

    if drop_folder.exists() or (STORE and STORE.list_files(drop_folder)):
        with db() as con:
            # Pedido já marcado como pronto não volta pra "em produção" se o
            # pagamento for aprovado de novo (webhook repetido).
            con.execute(
                """UPDATE orders SET production_drop_path=?,
                   status=CASE WHEN status='PRONTO' THEN status ELSE 'LIBERADO_PRODUCAO' END
                   WHERE id=?""",
                (str(drop_folder), order_id)
            )
        return str(drop_folder)

    drop_folder.mkdir(parents=True, exist_ok=False)

    try:
        # No servidor (STORE) as artes já estão no Storage e o PC da produção
        # baixa direto de lá (sync_producao.py): copiar 500 MB pra uma pasta
        # que ninguém abre só gastaria espaço. Aqui fica só o LEIA-ME.
        for p in ([] if STORE else source_folder.iterdir()):
            if not p.is_file():
                continue
            if is_production_file(p.name):
                shutil.copy2(p, drop_folder / p.name)

        resumo: dict[str, Any] = {
            "pedido": row["order_number"],
            "cliente": row["customer_name"],
            "tipo": row["sale_type"],
            "valor": row["total"],
            "pagamento": "PAID",
            "liberado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
            "dados": data
        }
        readme.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
        cloud_put(readme)

        with db() as con:
            con.execute(
                "UPDATE orders SET status='LIBERADO_PRODUCAO', production_drop_path=? WHERE id=?",
                (str(drop_folder), order_id)
            )

        return str(drop_folder)
    except Exception:
        shutil.rmtree(drop_folder, ignore_errors=True)
        raise


def finish_paid_order(order_id: str) -> str | None:
    """Pedido pago: camiseta/DTF vai pra pasta de produção; o plano premium
    soma os dias no acesso do cliente (uma vez só por pedido)."""
    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")
    if row["sale_type"] != "PREMIUM_SUB":
        return release_paid_order_to_production(order_id)
    data = json.loads(row["data_json"])
    with db() as con:
        claimed = con.execute(
            "UPDATE orders SET status='PREMIUM_LIBERADO' WHERE id=? AND status!='PREMIUM_LIBERADO'",
            (order_id,)
        ).rowcount
        if not claimed:
            return None  # já liberado antes: aprovar de novo não soma mais dias
        cust = con.execute(
            "SELECT premium_until FROM customers WHERE id=?", (data.get("customer_id"),)
        ).fetchone()
        if not cust:
            raise HTTPException(404, "Cliente do pedido não encontrado.")
        # Renovou antes de vencer: os dias novos contam a partir do fim do atual.
        start = max(time.time(), float(cust["premium_until"] or 0))
        until = start + int(data.get("premium_days") or 30) * 86400
        con.execute("UPDATE customers SET premium_until=? WHERE id=?", (until, data["customer_id"]))
    return None


def unpaid_limit_seconds() -> float:
    # Política de Reembolso: pedido não pago é cancelado 24 h depois de criado.
    return float(CONFIG.get("unpaid_cancel_hours") or 24) * 3600


def cancel_stale_orders():
    """Cancela os pedidos não pagos que passaram do prazo. Roda sempre que
    alguém olha os pedidos (Admin, Minha conta, pagar), então não precisa de
    tarefa agendada. Os arquivos ficam: pagou por fora? O Admin ainda marca
    como pago."""
    with db() as con:
        con.execute(
            """UPDATE orders SET status='CANCELADO'
               WHERE payment_status='PENDING' AND status='AGUARDANDO_PAGAMENTO' AND created_at < ?""",
            (time.time() - unpaid_limit_seconds(),)
        )


def mark_order_paid(order_id: str, payment_ref: str | None = None) -> str | None:
    """Pagamento aprovado (Admin, webhook ou retorno do Mercado Pago). Só o
    primeiro aviso muda o status: o provedor repete o webhook, e um pedido
    já pronto não pode voltar pra "pagamento aprovado"."""
    with db() as con:
        newly_paid = con.execute(
            """UPDATE orders SET payment_status='PAID', status='PAGAMENTO_APROVADO',
               payment_ref=COALESCE(?, payment_ref) WHERE id=? AND payment_status!='PAID'""",
            (payment_ref, order_id)
        ).rowcount
    if newly_paid:
        # Em segundo plano: o webhook do Mercado Pago tem 22 s pra receber o 200.
        threading.Thread(target=send_server_purchase, args=(order_id,), daemon=True).start()
    return finish_paid_order(order_id)


# ---------- Compra enviada pelo servidor (GA4 e Meta) ----------
# O evento do navegador só sai quando o cliente volta ao site depois de pagar.
# Quem paga o Pix e fecha a aba sumia das métricas: aqui o servidor manda a
# compra quando o pagamento é aprovado. Só pra quem deu consentimento (o
# pedido guarda a escolha em data["tracking"]), e com o número do pedido como
# id: GA (transaction_id) e Meta (event_id) descartam a repetição do navegador.
def clean_tracking(raw: Any, request: Request) -> dict[str, Any]:
    t = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {"analytics": t.get("analytics") is True, "marketing": t.get("marketing") is True}
    if out["analytics"]:
        out["ga_client_id"] = str(t.get("ga_client_id") or "")[:64]
        out["ga_session_id"] = str(t.get("ga_session_id") or "")[:32]
    if out["marketing"]:
        out["fbp"] = str(t.get("fbp") or "")[:120]
        out["fbc"] = str(t.get("fbc") or "")[:300]
        # A Meta cruza a compra com o anúncio também pelo IP e navegador de quem comprou.
        out["ip"] = client_ip(request)
        out["user_agent"] = (request.headers.get("user-agent") or "")[:400]
    return out


def post_json(url: str, body: dict[str, Any]) -> int:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def send_server_purchase(order_id: str):
    row = load_order(order_id)
    if not row:
        return
    t = json.loads(row["data_json"]).get("tracking") or {}
    number, value = row["order_number"], round(float(row["total"]), 2)
    item = {"item_id": row["sale_type"], "item_name": row["sale_type"], "price": value, "quantity": 1}
    ga_id, ga_secret = tracking_id("GA4_MEASUREMENT_ID", r"G-[A-Z0-9]{4,20}"), os.getenv("GA4_API_SECRET", "").strip()
    if t.get("analytics") and t.get("ga_client_id") and ga_id and ga_secret:
        params: dict[str, Any] = {"transaction_id": number, "value": value, "currency": "BRL", "items": [item]}
        if t.get("ga_session_id"):
            params["session_id"] = t["ga_session_id"]  # junta a compra à visita que veio do anúncio
        try:
            post_json(
                "https://www.google-analytics.com/mp/collect?"
                + urllib.parse.urlencode({"measurement_id": ga_id, "api_secret": ga_secret}),
                {"client_id": t["ga_client_id"], "events": [{"name": "purchase", "params": params}]},
            )
        except Exception as exc:
            print(f"[ga4] compra {number} não enviada: {exc}")
    pixel, token = tracking_id("META_PIXEL_ID", r"\d{6,20}"), os.getenv("META_CAPI_TOKEN", "").strip()
    if t.get("marketing") and pixel and token:
        user = {k: v for k, v in {"client_ip_address": t.get("ip"), "client_user_agent": t.get("user_agent"),
                                  "fbp": t.get("fbp"), "fbc": t.get("fbc")}.items() if v}
        version = os.getenv("META_GRAPH_VERSION", "v25.0").strip() or "v25.0"
        event: dict[str, Any] = {
            "event_name": "Purchase", "event_time": int(time.time()), "event_id": number,
            "action_source": "website", "user_data": user,
            "custom_data": {"currency": "BRL", "value": value, "order_id": number},
        }
        if site_url():
            event["event_source_url"] = site_url() + "/"
        try:
            post_json(f"https://graph.facebook.com/{version}/{pixel}/events?"
                      + urllib.parse.urlencode({"access_token": token}), {"data": [event]})
        except Exception as exc:
            print(f"[meta-capi] compra {number} não enviada: {exc}")


# ---------- Pagamento online: Mercado Pago (Checkout Pro) ----------
# O cliente paga numa página do Mercado Pago (Pix, cartão, boleto). O site
# fica sabendo de dois jeitos: o webhook (só no servidor com https) e a
# conferência na volta do pagamento / botão "Já paguei, verificar".
MP_API = "https://api.mercadopago.com"


def mp_token() -> str:
    return os.getenv("MERCADOPAGO_ACCESS_TOKEN", "").strip()


def site_url() -> str:
    """Endereço público do site (SITE_URL no .env). O Mercado Pago só aceita
    voltar pro site e avisar os pagamentos num domínio de verdade: com
    127.0.0.1 ele mostra "Algo deu errado" no fim do pagamento."""
    url = os.getenv("SITE_URL", "").strip().rstrip("/")
    return url if url.startswith("https://") else ""


def mp_request(method: str, path: str, body: dict[str, Any] | None = None, missing_ok: bool = False) -> dict[str, Any]:
    req = urllib.request.Request(
        MP_API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {mp_token()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        if missing_ok and e.code == 404:
            return {}
        # O motivo fica no log do servidor (journalctl): ao cliente vai só o aviso.
        print(f"[mercadopago] {method} {path} -> {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
        raise HTTPException(502, "O Mercado Pago recusou a operação. Tente de novo em instantes.")
    except (urllib.error.URLError, TimeoutError):
        raise HTTPException(502, "Não foi possível falar com o Mercado Pago. Tente de novo em instantes.")


def apply_mp_payment(payment: dict[str, Any]) -> bool:
    """Libera o pedido de um pagamento buscado na API do Mercado Pago (nunca
    o que veio no corpo do webhook, que qualquer um pode forjar), se estiver
    aprovado e cobrir o valor do pedido."""
    if payment.get("status") != "approved":
        return False
    row = load_order(str(payment.get("external_reference") or ""))
    if not row:
        return False
    paid = float(payment.get("transaction_amount") or 0)
    if payment.get("currency_id") != "BRL" or paid + 0.01 < float(row["total"]):
        print(f"[mercadopago] pagamento {payment.get('id')} de {paid} não cobre o pedido {row['order_number']} ({row['total']})")
        return False
    mark_order_paid(row["id"], str(payment.get("id") or "") or None)
    return True


# ---------- Backup dos dados ----------
# O banco (contas, pedidos, sessões, solicitações LGPD, métricas) é copiado
# sozinho a cada `every_hours` (padrão 24 h) pra BACKUP_DIR, e as cópias com
# mais de `keep_days` (padrão 14) somem — sempre ficam pelo menos as 3 últimas.
# A cópia usa a API de backup do SQLite: fica certinha mesmo com o site
# gravando no meio. Arquivos (artes dos pedidos, biblioteca) não entram aqui:
# na VPS quem cuida deles é o deploy/backup.sh.
BACKUP_NAME = re.compile(r"studio_\d{4}-\d{2}-\d{2}_\d{6}(?:_[a-z]+)?\.db")


def backup_hours() -> float:
    return float(BACKUP_CFG.get("every_hours") or 24)


def list_backups() -> list[Path]:
    """Do mais novo pro mais antigo."""
    return sorted((p for p in BACKUP_DIR.glob("studio_*.db") if BACKUP_NAME.fullmatch(p.name)),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def backup_database(tag: str = "") -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"studio_{time.strftime('%Y-%m-%d_%H%M%S')}{'_' + tag if tag else ''}.db"
    dest, tmp = BACKUP_DIR / name, BACKUP_DIR / (name + ".tmp")
    src, dst = sqlite3.connect(DB_PATH, timeout=30), sqlite3.connect(tmp)
    try:
        src.backup(dst)
        healthy = dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        dst.close()
        src.close()
    if not healthy:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("a cópia do banco não passou na verificação de integridade")
    tmp.replace(dest)  # só aparece na lista depois de pronta e conferida
    prune_backups()
    return dest


def prune_backups():
    keep_days = float(BACKUP_CFG.get("keep_days") or 14)
    limit = time.time() - keep_days * 86400
    for old in list_backups()[3:]:
        if old.stat().st_mtime < limit:
            old.unlink(missing_ok=True)


def backup_loop():
    """Roda em segundo plano enquanto o servidor estiver ligado."""
    while True:
        try:
            last = list_backups()[:1]
            if not last or time.time() - last[0].stat().st_mtime >= backup_hours() * 3600:
                print(f"[backup] banco salvo em {backup_database()}")
        except Exception as exc:
            print(f"[backup] FALHOU: {exc}")
        time.sleep(600)


def restore_database(source: Path):
    """python app.py --restaurar <arquivo>: com o servidor DESLIGADO. Antes de
    trocar, guarda o banco atual como backup "_antes" (dá pra voltar)."""
    if IS_PG:
        sys.exit(PG_BACKUP_MSG)
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", int(os.getenv("PORT", "8787")))) == 0:
            sys.exit("Desligue o servidor antes de restaurar (feche o start.bat ou: systemctl stop ripfire).")
    if not source.is_file():
        sys.exit(f"Arquivo não encontrado: {source}")
    chk = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        has_tables = chk.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('customers','orders')").fetchone()[0] == 2
    except sqlite3.DatabaseError:
        ok = has_tables = False
    finally:
        chk.close()
    if not (ok and has_tables):
        sys.exit("Esse arquivo não é um backup válido do Ripfire.")
    if DB_PATH.exists():
        print(f"Banco atual guardado em {backup_database('antes')}")
    for extra in (DB_PATH.with_name(DB_PATH.name + "-wal"), DB_PATH.with_name(DB_PATH.name + "-shm")):
        extra.unlink(missing_ok=True)
    shutil.copy2(source, DB_PATH)
    print(f"Restaurado: {source.name} → {DB_PATH}. Pode ligar o servidor de novo.")


def seed_default_mockups():
    """Fotos 3D das camisetas (mockups_padrao/, com as áreas de impressão já
    ajustadas). Entram sozinhas quando o site não tem nenhum mockup: banco
    novo no Supabase, PC novo. Depois disso o Admin troca à vontade."""
    manifest = BASE / "mockups_padrao" / "mockups.json"
    if not manifest.is_file():
        return
    with db() as con:
        if con.execute("SELECT COUNT(*) FROM shirt_mockups").fetchone()[0]:
            return
    added = 0
    for i, m in enumerate(json.loads(manifest.read_text(encoding="utf-8"))):
        src = BASE / "mockups_padrao" / m["file"]
        if not src.is_file():
            continue
        dest = MOCKUPS_DIR / f"padrao_{m['file']}"
        shutil.copyfile(src, dest)
        try:
            cloud_put(dest)
        except cloud_storage.StorageError as exc:
            print(f"[mockups] {m['file']} não subiu pro Storage: {exc}")
            continue
        with db() as con:
            # id fixo + ON CONFLICT: dois processos subindo juntos não duplicam.
            added += con.execute(
                """INSERT INTO shirt_mockups
                   (id,created_at,model_id,color,side,file_path,preview_url,print_x,print_y,print_w,print_h)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                (f"padrao_{Path(m['file']).stem}", time.time() + i / 1000, m["model_id"], m["color"], m["side"],
                 str(dest), f"/media/mockups/{dest.name}", m["print_x"], m["print_y"], m["print_w"], m["print_h"])
            ).rowcount
    print(f"[mockups] {added} fotos padrão carregadas")


init_db()
if STORE:
    try:
        STORE.ensure_bucket()
    except cloud_storage.StorageError as exc:
        print(f"[storage] AVISO: não consegui conferir o bucket do Supabase: {exc}")
try:
    seed_default_mockups()
except Exception as exc:  # sem mockup o site ainda funciona (desenho da camiseta)
    print(f"[mockups] falhou ao carregar as fotos padrão: {exc}")

# Modalidades desligadas temporariamente (a opção também está comentada no
# index.html). Para reativar uma, basta tirar daqui.
DISABLED_SALE_TYPES = {"DTF_PLUS_SHIRT"}
# Abas de manga direita/esquerda desligadas por enquanto (os botões estão
# comentados no index.html). Para reativar, mude para True e tire o comentário.
SLEEVES_ENABLED = False

# Sem /docs e /openapi.json: no site publicado eles listariam todas as rotas
# (inclusive as de admin) pra qualquer visitante.
app = FastAPI(title="Ripfire Studio Local", version="2.0.0", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
MEDIA_ROOT = (BASE / "media").resolve()


# ---------- Proteções em toda resposta ----------
# CSP: o navegador só roda script do próprio site e dos poucos serviços que a
# loja usa (leitores de PDF/TIFF, login do Google, GA/GTM e Pixel, quando o
# visitante aceita). Script injetado por um ataque (XSS) não roda.
def _supabase_origin() -> str:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    return url if url.startswith("https://") else ""


CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://accounts.google.com/gsi/client "
    "https://www.googletagmanager.com https://connect.facebook.net",
    # style inline: o site usa style="..." no HTML gerado (não executa código).
    "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style",
    f"img-src 'self' data: blob: https://*.supabase.co {_supabase_origin()} https://*.google-analytics.com "
    "https://*.googletagmanager.com https://www.facebook.com https://*.googleusercontent.com",
    "font-src 'self' data:",
    f"connect-src 'self' https://*.supabase.co {_supabase_origin()} https://accounts.google.com/gsi/ "
    "https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com "
    "https://www.facebook.com https://connect.facebook.net https://cdnjs.cloudflare.com https://cdn.jsdelivr.net",
    "frame-src https://accounts.google.com/gsi/ https://www.googletagmanager.com",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",  # o site não abre dentro de iframe de outro site (clickjacking)
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
    # same-origin-allow-popups: o login do Google abre um popup.
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
}
# Recebem POST de fora (Mercado Pago), autenticados pelo segredo/consulta ao MP.
CSRF_EXEMPT = ("/api/payments/",)
API_LIMIT, API_WINDOW = 900, 300  # por IP: 900 chamadas em 5 minutos (3/s em média)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or ""
    if not origin:
        return True
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").lower()
    return urllib.parse.urlsplit(origin).netloc.lower() == host


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    path = request.url.path
    # HTTPS: o Render já redireciona, isto é a garantia caso chegue algo em http.
    if proto == "http" and request.headers.get("x-forwarded-proto"):
        return Response(status_code=308, headers={"Location": str(request.url.replace(scheme="https"))})
    if path.startswith("/api/"):
        # CSRF: o login é por token no header (nada de cookie), então outro site
        # não consegue agir em nome do cliente. Mesmo assim, chamada que muda
        # dados vinda de página de outro site (Origin diferente) é recusada.
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not path.startswith(CSRF_EXEMPT) \
                and request.headers.get("origin") and not _same_origin(request):
            return JSONResponse({"detail": "Origem não permitida."}, status_code=403)
        try:
            check_rate_limit(f"api:{client_ip(request)}", API_LIMIT, API_WINDOW,
                             "Muitas requisições em pouco tempo. Aguarde alguns minutos.")
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=429, headers={"Retry-After": "60"})
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if proto == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if path.startswith("/api/"):
        # Dados de cliente e do Admin não ficam guardados em cache do navegador/proxy.
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/media/{file_path:path}")
def media_file(file_path: str):
    """Catálogo, mockups e artes da IA. Antes era uma pasta estática; agora,
    se o arquivo não está no disco (servidor reiniciado), vem do Storage."""
    path = (MEDIA_ROOT / file_path).resolve()
    if MEDIA_ROOT not in path.parents or not cloud_get(path):
        raise HTTPException(404, "Arquivo não encontrado.")
    return FileResponse(path, headers={"Cache-Control": "max-age=86400"})


@app.get("/")
def index():
    # no-cache: o navegador sempre confere se a página mudou. Sem isso ele
    # reaproveitava um index.html antigo junto com um app.js novo e as
    # abas de lado da camisa paravam de funcionar.
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    # O navegador pede /favicon.ico em toda visita; sem isso cada uma gerava um 404.
    return FileResponse(BASE / "static" / "img" / "logo.png", media_type="image/png",
                        headers={"Cache-Control": "max-age=604800"})


@app.get("/privacidade")
def privacy_policy():
    return FileResponse(BASE / "static" / "privacidade.html", headers={"Cache-Control": "no-cache"})


@app.get("/termos")
def terms_of_service():
    return FileResponse(BASE / "static" / "termos.html", headers={"Cache-Control": "no-cache"})


@app.get("/lgpd")
def lgpd_form():
    return FileResponse(BASE / "static" / "lgpd.html", headers={"Cache-Control": "no-cache"})


@app.get("/reembolso")
def refund_policy():
    return FileResponse(BASE / "static" / "reembolso.html", headers={"Cache-Control": "no-cache"})


def tracking_id(env: str, pattern: str) -> str:
    """ID de GA4/Pixel do .env, só se tiver o formato certo: ele vai parar
    dentro de uma URL de script no navegador."""
    value = os.getenv(env, "").strip()
    return value if re.fullmatch(pattern, value) else ""


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    """Só expõe o que a loja precisa pra funcionar no navegador. NUNCA
    devolva CONFIG inteiro aqui: ele guarda admin_key, admin_user e o
    webhook_secret de pagamento, que qualquer visitante veria em texto
    puro se essa rota (pública, sem autenticação) vazasse o dict todo."""
    return {
        "store_name": CONFIG.get("store_name"),
        "max_print_width_cm": CONFIG.get("max_print_width_cm"),
        "max_print_height_cm": CONFIG.get("max_print_height_cm"),
        "dpi": CONFIG.get("dpi"),
        "shirt_models": CONFIG.get("shirt_models"),
        "shirt_colors": CONFIG.get("shirt_colors"),
        "sizes": CONFIG.get("sizes"),
        "size_print_limits": CONFIG.get("size_print_limits"),
        "pricing": CONFIG.get("pricing"),
        "dtf_sheet_width_cm": CONFIG.get("dtf_sheet_width_cm"),
        "dtf_sheet_min_height_cm": CONFIG.get("dtf_sheet_min_height_cm"),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
        "ai_model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2"),
        "google_client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        # Só preço e duração: as pastas do Drive não saem do servidor.
        "premium_price": digital_cfg().get("premium_price_month"),
        "premium_days": digital_cfg().get("premium_days"),
        # Com o Mercado Pago configurado, criar o pedido já leva pro pagamento.
        "online_payment": bool(mp_token()),
        # Com o Supabase Storage, as artes do pedido vão do navegador direto pra ele.
        "direct_upload": bool(STORE),
        # Métricas e anúncios (só carregam no navegador com o consentimento do visitante).
        "ga4_id": tracking_id("GA4_MEASUREMENT_ID", r"G-[A-Z0-9]{4,20}"),
        "meta_pixel_id": tracking_id("META_PIXEL_ID", r"\d{6,20}"),
        "gtm_id": tracking_id("GTM_CONTAINER_ID", r"GTM-[A-Z0-9]{4,12}"),
    }


@app.post("/api/auth/register")
def register_customer(payload: CustomerRegister, request: Request) -> dict[str, Any]:
    # Contas em série do mesmo IP são robô (cada conta libera pedidos e IA).
    check_rate_limit(f"register:{client_ip(request)}", 10, 3600,
                     "Muitas contas criadas daqui em pouco tempo. Tente de novo mais tarde.")
    profile = clean_profile(payload.full_name, payload.phone, payload.cep, payload.address_number, payload.document)
    email = payload.email.strip().lower()
    password = payload.password

    if "@" not in email or len(email) < 5:
        raise HTTPException(400, "E-mail inválido.")
    check_new_password(password)

    with db() as con:
        existing = con.execute("SELECT id FROM customers WHERE email=?", (email,)).fetchone()
        if existing:
            raise HTTPException(409, "Já existe uma conta com esse e-mail.")
        cid = uuid.uuid4().hex[:12]
        con.execute(
            """INSERT INTO customers
               (id,created_at,email,password_hash,full_name,phone,cep,address_number,document,terms_accepted_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (cid, time.time(), email, hash_password(password), profile["full_name"], profile["phone"],
             profile["cep"], profile["address_number"], profile["document"], time.time())
        )
        row = con.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    return new_session(dict(row))


@app.post("/api/auth/login")
def login_customer(payload: CustomerLogin, request: Request) -> dict[str, Any]:
    email = payload.email.strip().lower()
    fail_keys = [f"login-ip:{client_ip(request)}", f"login-email:{email[:200]}"]
    login_guard(fail_keys)
    with db() as con:
        row = con.execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone()
    # E-mail inexistente também passa pelo hash: mesmo tempo de resposta.
    ok = verify_password(payload.password, row["password_hash"] if row and row["password_hash"] else _DUMMY_HASH)
    if not row or not row["password_hash"] or not ok:
        login_failed(fail_keys, "E-mail ou senha incorretos.")
    login_ok(fail_keys)
    if password_needs_upgrade(row["password_hash"]):
        # Senha no formato antigo (100 mil iterações): refeita agora, sem o cliente perceber.
        with db() as con:
            con.execute("UPDATE customers SET password_hash=? WHERE id=?", (hash_password(payload.password), row["id"]))
    return new_session(dict(row))


@app.get("/api/me")
def get_me(customer: dict[str, Any] = Depends(require_customer)) -> dict[str, Any]:
    """Dados atualizados do cliente logado (o premium muda quando o
    pagamento é aprovado, sem a pessoa precisar entrar de novo)."""
    return public_customer(customer)


@app.post("/api/auth/logout")
def logout_customer(x_customer_token: str | None = Header(None)) -> dict[str, Any]:
    if x_customer_token:
        with db() as con:
            con.execute("DELETE FROM customer_sessions WHERE token=?", (x_customer_token,))
    return {"ok": True}


def legacy_email(customer: dict[str, Any]) -> str:
    """Pedido de antes do customer_id existir é achado pelo e-mail, mas só pra
    conta com e-mail confirmado (entrou com o Google). Sem isso, alguém que
    criasse conta com o e-mail de outra pessoa veria os pedidos antigos dela."""
    return customer["email"].lower() if customer.get("google_sub") else " sem e-mail confirmado "


def order_is_customers(row: dict[str, Any], customer: dict[str, Any]) -> bool:
    if row.get("customer_id"):
        return row["customer_id"] == customer["id"]
    return (row.get("customer_email") or "").strip().lower() == legacy_email(customer)


def order_for_customer(row: dict[str, Any]) -> dict[str, Any]:
    """Resumo do pedido pro "Minha conta" (sem o data_json inteiro)."""
    data = json.loads(row["data_json"])
    dpi = float(CONFIG.get("dpi") or 300)

    def size_cm(art: dict[str, Any] | None) -> dict[str, float] | None:
        # Medida real do arquivo de produção, não a que o navegador informou.
        if not art or not art.get("width_px") or not art.get("height_px"):
            return None
        return {"w": round(art["width_px"] / dpi * 2.54, 1), "h": round(art["height_px"] / dpi * 2.54, 1)}

    is_dtf = row["sale_type"] == "DTF_ONLY"
    art_keys = (("FRONT", "production_art"), ("BACK", "production_art_back"),
                ("SLEEVE_R", "production_art_sleeve_right"), ("SLEEVE_L", "production_art_sleeve_left"))
    # Artes premium da biblioteca usadas no pedido (o editor grava premium_id nas fontes).
    used_ids = {
        src.get("premium_id")
        for key in ("sources", "sources_front", "sources_back", "sources_sleeve_right", "sources_sleeve_left")
        for src in data.get(key) or [] if src.get("premium_id")
    }
    premium_names: list[str] = []
    if used_ids:
        with db() as con:
            premium_names = [r["name"] for r in con.execute(
                f"""SELECT a.name FROM library_arts a JOIN library_categories c ON c.id=a.category_id
                    WHERE c.access='premium' AND a.id IN ({','.join('?' * len(used_ids))})""",
                tuple(used_ids)
            )]
    return {
        "id": row["id"],
        "order_number": row["order_number"],
        "created_at": row["created_at"],
        "sale_type": row["sale_type"],
        "status": row["status"],
        "payment_status": row["payment_status"],
        "total": row["total"],
        "product": data.get("product"),
        "art_sizes": {} if is_dtf else {
            side: size_cm(data.get(key)) for side, key in art_keys if size_cm(data.get(key))
        },
        "placements": [side for side, key in (("FRONT", "placement_front"), ("BACK", "placement_back")) if data.get(key)],
        "sheet": size_cm(data.get("production_art")) if is_dtf else None,
        "sources": [
            {k: s.get(k) for k in ("name", "width_cm", "height_cm", "qty")} for s in data.get("sources") or []
        ] if is_dtf else [],
        "premium_arts": sorted(premium_names),
        "premium_days": data.get("premium_days"),
        "stage": row.get("stage") or "",
    }


@app.get("/api/me/profile")
def get_profile(customer: dict[str, Any] = Depends(require_customer)) -> dict[str, Any]:
    cancel_stale_orders()
    with db() as con:
        orders = con.execute(
            """SELECT * FROM orders
               WHERE customer_id=? OR (customer_id IS NULL AND lower(customer_email)=?)
               ORDER BY created_at DESC""",
            (customer["id"], legacy_email(customer))
        ).fetchall()
        downloads = con.execute(
            """SELECT d.art_id, MAX(d.art_name) AS art_name, MAX(d.created_at) AS last_at, COUNT(*) AS times,
                      MAX(CASE WHEN a.id IS NOT NULL THEN 1 ELSE 0 END) AS available, MAX(c.name) AS category, MAX(c.access) AS access
               FROM digital_downloads d
               LEFT JOIN library_arts a ON a.id=d.art_id
               LEFT JOIN library_categories c ON c.id=a.category_id
               WHERE d.customer_id=?
               GROUP BY d.art_id ORDER BY last_at DESC""",
            (customer["id"],)
        ).fetchall()
    return {
        "customer": public_customer(customer),
        "orders": [order_for_customer(dict(r)) for r in orders],
        "downloads": [dict(d) for d in downloads],
    }


@app.get("/api/me/orders/{order_id}/placement/{side}")
def my_order_placement(order_id: str, side: str, customer: dict[str, Any] = Depends(require_customer)):
    """Foto de posição (POSICAO_*) de um pedido do próprio cliente."""
    row = load_order(order_id)
    key = {"FRONT": "placement_front", "BACK": "placement_back"}.get(side)
    if not row or not key or not order_is_customers(row, customer):
        raise HTTPException(404, "Foto não encontrada.")
    info = json.loads(row["data_json"]).get(key) or {}
    path = Path(row["folder_path"]) / str(info.get("file") or "")
    if not info.get("file") or not cloud_get(path):
        raise HTTPException(404, "Foto não encontrada.")
    return FileResponse(path)


@app.patch("/api/me")
def update_me(payload: ProfileUpdate, customer: dict[str, Any] = Depends(require_customer)) -> dict[str, Any]:
    profile = clean_profile(payload.full_name, payload.phone, payload.cep, payload.address_number, payload.document)
    with db() as con:
        con.execute(
            "UPDATE customers SET full_name=?, phone=?, cep=?, address_number=?, document=? WHERE id=?",
            (profile["full_name"], profile["phone"], profile["cep"], profile["address_number"],
             profile["document"], customer["id"])
        )
        row = con.execute("SELECT * FROM customers WHERE id=?", (customer["id"],)).fetchone()
    return public_customer(dict(row))


@app.post("/api/me/password")
def change_password(
    payload: PasswordChange,
    customer: dict[str, Any] = Depends(require_customer),
    x_customer_token: str | None = Header(None),
) -> dict[str, Any]:
    check_new_password(payload.new_password, "A nova senha")
    # Quem entrou pelo Google não tem senha ainda: cria sem pedir a atual.
    if customer.get("password_hash"):
        fail_keys = [f"pwd:{customer['id']}"]
        login_guard(fail_keys)
        if not verify_password(payload.current_password, customer["password_hash"]):
            login_failed(fail_keys, "Senha atual incorreta.", 400)
        login_ok(fail_keys)
    with db() as con:
        con.execute(
            "UPDATE customers SET password_hash=? WHERE id=?",
            (hash_password(payload.new_password), customer["id"])
        )
        # Desconecta os outros aparelhos; este continua logado.
        con.execute(
            "DELETE FROM customer_sessions WHERE customer_id=? AND token!=?",
            (customer["id"], x_customer_token or "")
        )
    return {"ok": True}


@app.post("/api/auth/google")
def login_with_google(payload: GoogleAuth) -> dict[str, Any]:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(503, "Login com Google não configurado (GOOGLE_CLIENT_ID ausente no .env).")

    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    try:
        claims = google_id_token.verify_oauth2_token(  # pyright: ignore[reportUnknownMemberType]
            payload.credential, google_requests.Request(), client_id
        )
    except ValueError:
        raise HTTPException(401, "Token do Google inválido.")

    email = str(claims.get("email", "")).strip().lower()
    if not email:
        raise HTTPException(400, "A conta Google não tem e-mail disponível.")
    sub = str(claims.get("sub", ""))
    full_name = str(claims.get("name") or email.split("@")[0])

    with db() as con:
        row = con.execute("SELECT * FROM customers WHERE google_sub=?", (sub,)).fetchone()
        if not row:
            row = con.execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone()
            if row:
                con.execute("UPDATE customers SET google_sub=? WHERE id=?", (sub, row["id"]))
                row = con.execute("SELECT * FROM customers WHERE id=?", (row["id"],)).fetchone()
        if not row:
            cid = uuid.uuid4().hex[:12]
            con.execute(
                """INSERT INTO customers
                   (id,created_at,email,password_hash,full_name,phone,cep,address_number,document,google_sub,terms_accepted_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, time.time(), email, None, full_name, "", "", "", "", sub, time.time())
            )
            row = con.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()

    return new_session(dict(row))


# ---------- LGPD: direitos do titular (art. 18) ----------
LGPD_KINDS = {
    "ACESSO": "Confirmar e acessar meus dados",
    "CORRECAO": "Corrigir dados",
    "EXCLUSAO": "Excluir meus dados",
    "PORTABILIDADE": "Portabilidade dos dados",
    "COMPARTILHAMENTO": "Saber com quem meus dados são compartilhados",
    "REVOGACAO": "Revogar consentimento",
    "OPOSICAO": "Oposição a um tratamento",
    "OUTRO": "Outro assunto de privacidade",
}
LGPD_DEADLINE_DAYS = 15  # prazo de resposta prometido na Política de Privacidade


class PrivacyRequest(BaseModel):
    name: str
    email: str
    document: str = ""
    kind: str
    message: str = ""


class AccountDelete(BaseModel):
    confirm: str
    password: str = ""


@app.post("/api/lgpd/requests")
def create_privacy_request(
    payload: PrivacyRequest, request: Request, x_customer_token: str | None = Header(None),
) -> dict[str, Any]:
    """Formulário do titular (também pra quem não tem conta). Cada pedido
    ganha um protocolo e fica no Admin até ser respondido."""
    check_rate_limit(f"lgpd:{client_ip(request)}", 5, 3600,
                     "Muitas solicitações em pouco tempo. Tente de novo mais tarde.")
    name, email = payload.name.strip(), payload.email.strip().lower()
    if not name:
        raise HTTPException(400, "Informe seu nome.")
    if "@" not in email or len(email) < 5:
        raise HTTPException(400, "E-mail inválido.")
    if payload.kind not in LGPD_KINDS:
        raise HTTPException(400, "Escolha o tipo de solicitação.")
    customer = customer_from_token(x_customer_token)
    rid = uuid.uuid4().hex[:12]
    protocol = f"LGPD-{time.strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"
    with db() as con:
        con.execute(
            """INSERT INTO privacy_requests (id,protocol,created_at,customer_id,name,email,document,kind,message)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (rid, protocol, time.time(), customer["id"] if customer else None, name[:200], email[:200],
             only_digits(payload.document)[:14], payload.kind, payload.message.strip()[:3000])
        )
    return {"ok": True, "protocol": protocol, "deadline_days": LGPD_DEADLINE_DAYS}


def customer_orders(con: sqlite3.Connection, customer: dict[str, Any]) -> list[sqlite3.Row]:
    return con.execute(
        """SELECT * FROM orders WHERE customer_id=? OR (customer_id IS NULL AND lower(customer_email)=?)
           ORDER BY created_at DESC""",
        (customer["id"], legacy_email(customer))
    ).fetchall()


@app.get("/api/me/export")
def export_my_data(customer: dict[str, Any] = Depends(require_customer)):
    """Acesso e portabilidade (art. 18, II e V): tudo que a loja guarda do
    cliente num JSON legível por pessoas e por outros sistemas."""
    with db() as con:
        orders = customer_orders(con, customer)
        downloads = con.execute(
            "SELECT art_name, created_at FROM digital_downloads WHERE customer_id=? ORDER BY created_at",
            (customer["id"],)
        ).fetchall()
        requests_ = con.execute(
            "SELECT protocol, created_at, kind, message, status FROM privacy_requests WHERE customer_id=? OR email=?",
            (customer["id"], legacy_email(customer))
        ).fetchall()
        sessions = con.execute(
            "SELECT COUNT(*) FROM customer_sessions WHERE customer_id=?", (customer["id"],)
        ).fetchone()[0]
    iso = lambda ts: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else None  # noqa: E731
    data = {
        "gerado_em": iso(time.time()),
        "loja": CONFIG.get("store_name"),
        "conta": {
            **{k: v for k, v in public_customer(customer).items() if k not in ("has_password",)},
            "criada_em": iso(customer.get("created_at")),
            "premium_ate": iso(customer.get("premium_until")),
            "termos_aceitos_em": iso(customer.get("terms_accepted_at")),
            "login_com_google": bool(customer.get("google_sub")),
            "tem_senha": bool(customer.get("password_hash")),
            "sessoes_ativas": sessions,
        },
        "pedidos": [
            {**order_for_customer(dict(o)), "criado_em": iso(o["created_at"]),
             "pagamento_mercadopago": o["payment_ref"]}
            for o in orders
        ],
        "downloads_de_artes": [{"arte": d["art_name"], "em": iso(d["created_at"])} for d in downloads],
        "solicitacoes_lgpd": [
            {"protocolo": r["protocol"], "em": iso(r["created_at"]), "tipo": LGPD_KINDS.get(r["kind"], r["kind"]),
             "mensagem": r["message"], "situacao": r["status"]}
            for r in requests_
        ],
        "observacao": "As artes enviadas nos pedidos podem ser pedidas pelo formulário em /lgpd.",
    }
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="meus-dados-ripfire.json"'})


@app.delete("/api/me")
def delete_my_account(payload: AccountDelete, customer: dict[str, Any] = Depends(require_customer)) -> dict[str, Any]:
    """Exclusão (art. 18, VI). Some a conta, as sessões, o histórico de
    downloads e os pedidos não pagos (com os arquivos). Pedidos pagos ficam
    com nome e e-mail, pela obrigação fiscal (art. 16, I), como diz a política."""
    if payload.confirm.strip().upper() != "EXCLUIR":
        raise HTTPException(400, 'Digite EXCLUIR para confirmar.')
    if customer.get("password_hash"):
        fail_keys = [f"pwd:{customer['id']}"]
        login_guard(fail_keys)
        if not verify_password(payload.password, customer["password_hash"]):
            login_failed(fail_keys, "Senha incorreta.", 400)
    with db() as con:
        unpaid = [dict(o) for o in customer_orders(con, customer) if o["payment_status"] != "PAID"]
        con.executemany("DELETE FROM orders WHERE id=?", [(o["id"],) for o in unpaid])
        con.execute("DELETE FROM customer_sessions WHERE customer_id=?", (customer["id"],))
        con.execute("DELETE FROM digital_downloads WHERE customer_id=?", (customer["id"],))
        # As solicitações LGPD ficam (comprovam o atendimento), sem o vínculo com a conta.
        con.execute("UPDATE privacy_requests SET customer_id=NULL WHERE customer_id=?", (customer["id"],))
        con.execute("DELETE FROM customers WHERE id=?", (customer["id"],))
    for o in unpaid:
        report_dir = SOMENTE_DTF_DIR / f"{o['order_number']}__{safe_name(o['customer_name'])}"
        if o["folder_path"]:
            shutil.rmtree(o["folder_path"], ignore_errors=True)
            cloud_delete_dir(o["folder_path"])
        shutil.rmtree(report_dir, ignore_errors=True)
        cloud_delete_dir(report_dir)
    cleanup_staged_uploads(customer["id"], max_age=0)
    return {"ok": True, "deleted_unpaid_orders": len(unpaid)}


@app.get("/api/admin/lgpd/requests", dependencies=[Depends(require_admin)])
def list_privacy_requests() -> list[dict[str, Any]]:
    with db() as con:
        rows = con.execute("SELECT * FROM privacy_requests ORDER BY created_at DESC").fetchall()
    return [
        {**dict(r), "kind_label": LGPD_KINDS.get(r["kind"], r["kind"]),
         "due_at": r["created_at"] + LGPD_DEADLINE_DAYS * 86400}
        for r in rows
    ]


@app.patch("/api/admin/lgpd/requests/{request_id}", dependencies=[Depends(require_admin)])
def update_privacy_request(request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "")
    if status not in {"ABERTO", "EM_ANDAMENTO", "CONCLUIDO", "RECUSADO"}:
        raise HTTPException(400, "Situação inválida.")
    with db() as con:
        changed = con.execute(
            "UPDATE privacy_requests SET status=?, answered_at=? WHERE id=?",
            (status, time.time() if status in {"CONCLUIDO", "RECUSADO"} else None, request_id)
        ).rowcount
    if not changed:
        raise HTTPException(404, "Solicitação não encontrada.")
    return {"ok": True}


PG_BACKUP_MSG = ("O banco está no Supabase, que faz o backup sozinho todo dia: veja e restaure em "
                 "Supabase > Database > Backups.")


@app.get("/api/admin/backups", dependencies=[Depends(require_admin)])
def admin_list_backups() -> dict[str, Any]:
    if IS_PG:
        raise HTTPException(400, PG_BACKUP_MSG)
    return {
        "folder": str(BACKUP_DIR), "every_hours": backup_hours(), "keep_days": float(BACKUP_CFG.get("keep_days") or 14),
        "backups": [{"name": p.name, "size": p.stat().st_size, "created_at": p.stat().st_mtime} for p in list_backups()],
    }


@app.post("/api/admin/backups", dependencies=[Depends(require_admin)])
def admin_backup_now() -> dict[str, Any]:
    if IS_PG:
        raise HTTPException(400, PG_BACKUP_MSG)
    try:
        dest = backup_database("manual")
    except Exception as exc:
        raise HTTPException(500, f"Não foi possível fazer o backup: {exc}")
    return {"ok": True, "name": dest.name}


@app.get("/api/admin/backups/{name}", dependencies=[Depends(require_admin)])
def admin_download_backup(name: str):
    # Só nomes no formato dos backups: nada de "../" pra ler outros arquivos.
    path = BACKUP_DIR / name
    if IS_PG or not BACKUP_NAME.fullmatch(name) or not path.is_file():
        raise HTTPException(404, "Backup não encontrado.")
    return FileResponse(path, media_type="application/octet-stream", filename=name)


# ---------- Métricas próprias, anônimas (sem cookie, sem IP, sem consentimento) ----------
# GA e Meta só contam quem aceita os cookies. Aqui fica só "dia, página, total":
# não dá pra saber quem foi, então não é dado pessoal (LGPD, art. 12) e conta
# todo mundo. O Admin cruza com os pedidos do banco pra mostrar o funil real.
METRIC_PAGES = {"visit", "home", "editor", "digital", "profile", "privacidade", "termos", "reembolso", "lgpd"}
BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|headless|lighthouse|curl|wget|python|scrapy", re.I)


class PageHit(BaseModel):
    page: str


@app.post("/api/metrics/hit", status_code=204)
def metric_hit(payload: PageHit, request: Request):
    if payload.page not in METRIC_PAGES or BOT_UA.search(request.headers.get("user-agent") or ""):
        return
    try:
        # O IP só serve pro limite em memória, nunca é gravado.
        check_rate_limit(f"hit:{client_ip(request)}", 300, 3600, "")
    except HTTPException:
        return
    with db() as con:
        con.execute(
            """INSERT INTO page_hits (day,page,hits) VALUES(?,?,1)
               ON CONFLICT(day,page) DO UPDATE SET hits=page_hits.hits+1""",
            (time.strftime("%Y-%m-%d"), payload.page)
        )


def local_day_sql(col: str) -> str:
    """Dia (AAAA-MM-DD) no fuso da loja de um horário em segundos. No Render o
    relógio do servidor é UTC: o fuso vem da variável TZ (America/Sao_Paulo)."""
    if not IS_PG:
        return f"date({col},'unixepoch','localtime')"
    tz = os.getenv("TZ", "").strip() or "America/Sao_Paulo"
    if not re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z_+\-]+)*", tz):
        tz = "America/Sao_Paulo"
    return f"to_char(to_timestamp({col}) AT TIME ZONE '{tz}', 'YYYY-MM-DD')"


@app.get("/api/admin/metrics", dependencies=[Depends(require_admin)])
def admin_metrics(days: int = 30) -> dict[str, Any]:
    days = max(1, min(days, 365))
    since_ts = time.time() - days * 86400
    since_day = time.strftime("%Y-%m-%d", time.localtime(since_ts))
    day_expr = local_day_sql("created_at")
    with db() as con:
        hits = [dict(r) for r in con.execute(
            "SELECT day, page, hits FROM page_hits WHERE day >= ? ORDER BY day", (since_day,))]
        orders = [dict(r) for r in con.execute(
            f"""SELECT {day_expr} AS day, sale_type, payment_status, status, total
               FROM orders WHERE created_at >= ?""", (since_ts,))]
        signups = [dict(r) for r in con.execute(
            f"""SELECT {day_expr} AS day, COUNT(*) AS n FROM customers
               WHERE created_at >= ? GROUP BY 1""", (since_ts,))]
    return {"days": days, "since": since_day, "hits": hits, "orders": orders, "signups": signups}


@app.get("/api/artworks")
def list_artworks():
    with db() as con:
        rows = con.execute(
            "SELECT * FROM artworks WHERE active=1 ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/admin/artworks", dependencies=[Depends(require_admin)])
async def create_artwork(
    name: str = Form(...),
    category: str = Form("Outros"),
    digital_price: float = Form(9.90),
    can_customize: bool = Form(True),
    can_sell_digital: bool = Form(True),
    artwork: UploadFile = File(...),
) -> dict[str, Any]:
    ext = Path(artwork.filename or "").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "Envie PNG, JPG, JPEG ou WEBP.")

    aid = uuid.uuid4().hex[:12]
    filename = f"{aid}_{safe_name(Path(artwork.filename or '').stem)}{ext}"
    dest = ARTWORKS_DIR / filename

    with dest.open("wb") as f:
        while chunk := await artwork.read(1024 * 1024):
            f.write(chunk)
    try:
        await run_in_threadpool(cloud_put, dest)
    except cloud_storage.StorageError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(502, f"Não foi possível guardar a arte no Supabase: {exc}")

    preview_url = f"/media/artworks/{filename}"

    with db() as con:
        con.execute(
            """INSERT INTO artworks
               (id,created_at,name,category,file_path,preview_url,digital_price,
                can_customize,can_sell_digital,active,origin)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                aid, time.time(), name.strip(), category.strip(), str(dest),
                preview_url, float(digital_price), int(can_customize),
                int(can_sell_digital), 1, "ADMIN"
            )
        )

    return {"ok": True, "id": aid, "preview_url": preview_url}


@app.get("/api/shirt-mockups")
def list_shirt_mockups():
    with db() as con:
        rows = con.execute("SELECT * FROM shirt_mockups ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/admin/shirt-mockups", dependencies=[Depends(require_admin)])
async def create_shirt_mockup(
    model_id: str = Form(...),
    color: str = Form(...),
    side: str = Form("FRONT"),
    print_x: float = Form(30),
    print_y: float = Form(28),
    print_w: float = Form(40),
    print_h: float = Form(45),
    photo: UploadFile = File(...),
) -> dict[str, Any]:
    valid_models = {m["id"] for m in CONFIG.get("shirt_models", [])}
    if model_id not in valid_models:
        raise HTTPException(400, "Modelo inválido.")
    if color not in set(CONFIG.get("shirt_colors", [])):
        raise HTTPException(400, "Cor inválida.")
    side = side.strip().upper()
    if side not in {"FRONT", "BACK"}:
        raise HTTPException(400, "Lado inválido (use FRONT ou BACK).")
    for label, v in (("print_x", print_x), ("print_y", print_y), ("print_w", print_w), ("print_h", print_h)):
        if v < 0 or v > 100:
            raise HTTPException(400, f"{label} deve estar entre 0 e 100.")

    ext = Path(photo.filename or "").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "Envie PNG, JPG, JPEG ou WEBP.")

    mid = uuid.uuid4().hex[:12]
    filename = f"{mid}_{safe_name(model_id)}_{safe_name(color)}_{side.lower()}{ext}"
    dest = MOCKUPS_DIR / filename
    with dest.open("wb") as f:
        while chunk := await photo.read(1024 * 1024):
            f.write(chunk)
    try:
        await run_in_threadpool(cloud_put, dest)
    except cloud_storage.StorageError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(502, f"Não foi possível guardar a foto no Supabase: {exc}")
    preview_url = f"/media/mockups/{filename}"

    with db() as con:
        # Só uma foto ativa por combinação modelo+cor+lado — subir de
        # novo substitui a anterior (e apaga o arquivo antigo do disco).
        old = con.execute(
            "SELECT file_path FROM shirt_mockups WHERE model_id=? AND color=? AND side=?",
            (model_id, color, side)
        ).fetchall()
        for row in old:
            Path(row["file_path"]).unlink(missing_ok=True)
            cloud_delete(row["file_path"])
        con.execute("DELETE FROM shirt_mockups WHERE model_id=? AND color=? AND side=?", (model_id, color, side))
        con.execute(
            """INSERT INTO shirt_mockups
               (id,created_at,model_id,color,side,file_path,preview_url,print_x,print_y,print_w,print_h)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, time.time(), model_id, color, side, str(dest), preview_url, print_x, print_y, print_w, print_h)
        )

    return {"ok": True, "id": mid, "preview_url": preview_url}


@app.delete("/api/admin/shirt-mockups/{mockup_id}", dependencies=[Depends(require_admin)])
def delete_shirt_mockup(mockup_id: str):
    with db() as con:
        row = con.execute("SELECT * FROM shirt_mockups WHERE id=?", (mockup_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Mockup não encontrado.")
        Path(row["file_path"]).unlink(missing_ok=True)
        cloud_delete(row["file_path"])
        con.execute("DELETE FROM shirt_mockups WHERE id=?", (mockup_id,))
    return {"ok": True}


@app.patch("/api/admin/shirt-mockups/{mockup_id}/area", dependencies=[Depends(require_admin)])
def update_shirt_mockup_area(mockup_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, float] = {}
    for key in ("print_x", "print_y", "print_w", "print_h"):
        if key not in payload:
            raise HTTPException(400, f"Falta o campo {key}.")
        try:
            value = float(payload[key])
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} inválido.")
        if not 0 <= value <= 100:  # também recusa NaN
            raise HTTPException(400, f"{key} deve estar entre 0 e 100.")
        fields[key] = value
    with db() as con:
        row = con.execute("SELECT id FROM shirt_mockups WHERE id=?", (mockup_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Mockup não encontrado.")
        con.execute(
            "UPDATE shirt_mockups SET print_x=?, print_y=?, print_w=?, print_h=? WHERE id=?",
            (fields["print_x"], fields["print_y"], fields["print_w"], fields["print_h"], mockup_id)
        )
    return {"ok": True}


@app.get("/api/library")
def get_library() -> dict[str, Any]:
    """Os dois lados e as pastas de cada um, com a quantidade de artes (as artes vêm por pasta)."""
    with db() as con:
        rows = con.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM library_arts a WHERE a.category_id=c.id) AS art_count
               FROM library_categories c ORDER BY lower(c.name)"""
        ).fetchall()
    return {"categories": [dict(r) for r in rows]}


@app.get("/api/library/categories/{category_id}/arts")
def get_library_arts(category_id: str) -> list[dict[str, Any]]:
    with db() as con:
        rows = con.execute(
            "SELECT id, name, created_at FROM library_arts WHERE category_id=? ORDER BY created_at DESC",
            (category_id,)
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/library/arts/{art_id}/thumb")
def library_art_thumb(art_id: str):
    return FileResponse(
        library_preview(library_art(art_id)), media_type="image/webp",
        headers={"Cache-Control": "max-age=86400"}
    )


@app.get("/api/library/arts/{art_id}/file")
def library_art_file(art_id: str, download: bool = False, customer: dict[str, Any] = Depends(require_customer)):
    """Arquivo original (pra baixar ou usar no editor). Precisa de conta, e a
    premium precisa do premium ativo. Cada download fica registrado com o cliente."""
    art = library_art(art_id)
    if art["access"] == "premium" and not premium_active(customer):
        raise HTTPException(403, "Essa arte é premium. Assine o premium para baixar e usar.")
    path = Path(art["file_path"])
    if not cloud_get(path):
        raise HTTPException(404, "Arquivo da arte não encontrado.")
    if not download:
        return FileResponse(path)
    with db() as con:
        con.execute(
            "INSERT INTO digital_downloads (created_at,customer_id,art_id,art_name) VALUES(?,?,?,?)",
            (time.time(), customer["id"], art_id, art["name"])
        )
    return FileResponse(path, filename=f"{art['name']}{path.suffix}")


@app.post("/api/admin/library/categories", dependencies=[Depends(require_admin)])
def create_library_category(payload: dict[str, Any]) -> dict[str, Any]:
    """Nova pasta dentro de um dos lados. O acesso da pasta é o do lado."""
    name = str(payload.get("name") or "").strip()
    side = str(payload.get("parent_id") or "")
    if not name:
        raise HTTPException(400, "Informe o nome.")
    if side not in LIBRARY_SIDES:
        raise HTTPException(400, "Escolha o lado: Grátis ou Premium.")
    cid = uuid.uuid4().hex[:12]
    with db() as con:
        con.execute(
            "INSERT INTO library_categories (id,created_at,name,parent_id,access) VALUES(?,?,?,?,?)",
            (cid, time.time(), name, side, side)
        )
    return {"ok": True, "id": cid}


@app.patch("/api/admin/library/categories/{category_id}", dependencies=[Depends(require_admin)])
def update_library_category(category_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Renomeia a pasta ou passa ela para o outro lado (as artes mudam de acesso junto)."""
    if category_id in LIBRARY_SIDES:
        raise HTTPException(400, "Os lados Grátis e Premium são fixos.")
    fields: dict[str, str] = {}
    if "name" in payload:
        fields["name"] = str(payload["name"] or "").strip()
        if not fields["name"]:
            raise HTTPException(400, "Informe o nome.")
    if "parent_id" in payload:
        side = str(payload["parent_id"] or "")
        if side not in LIBRARY_SIDES:
            raise HTTPException(400, "Lado inválido.")
        fields["parent_id"] = fields["access"] = side
    if not fields:
        raise HTTPException(400, "Nada para alterar.")
    with db() as con:
        changed = con.execute(
            f"UPDATE library_categories SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
            (*fields.values(), category_id)
        ).rowcount
    if not changed:
        raise HTTPException(404, "Pasta não encontrada.")
    return {"ok": True}


@app.delete("/api/admin/library/categories/{category_id}", dependencies=[Depends(require_admin)])
def delete_library_category(category_id: str) -> dict[str, Any]:
    """Apaga a pasta e todas as artes dela (com os arquivos). Os lados não saem."""
    if category_id in LIBRARY_SIDES:
        raise HTTPException(400, "Os lados Grátis e Premium são fixos.")
    with db() as con:
        if not con.execute("SELECT 1 FROM library_categories WHERE id=?", (category_id,)).fetchone():
            raise HTTPException(404, "Pasta não encontrada.")
        arts = con.execute("SELECT id, file_path FROM library_arts WHERE category_id=?", (category_id,)).fetchall()
        con.execute("DELETE FROM library_arts WHERE category_id=?", (category_id,))
        con.execute("DELETE FROM library_categories WHERE id=?", (category_id,))
    for art in arts:
        remove_library_files(art["id"], art["file_path"])
    return {"ok": True, "deleted_arts": len(arts)}


@app.post("/api/admin/library/arts", dependencies=[Depends(require_admin)])
async def upload_library_arts(
    category_id: str = Form(...),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    """Envio de várias artes de uma vez. O nome da arte é o nome do arquivo."""
    with db() as con:
        folder = con.execute("SELECT parent_id FROM library_categories WHERE id=?", (category_id,)).fetchone()
    if not folder:
        raise HTTPException(404, "Pasta não encontrada.")
    if not folder["parent_id"]:
        raise HTTPException(400, "Envie as artes para uma pasta, não direto no lado.")
    saved, skipped = 0, []
    for upload in files:
        filename = upload.filename or ""
        ext = Path(filename).suffix.lower()
        if ext not in LIBRARY_EXTS:
            skipped.append(filename)
            continue
        aid = uuid.uuid4().hex[:12]
        dest = LIBRARY_DIR / f"{aid}{ext}"
        with dest.open("wb") as f:
            while chunk := await upload.read(1024 * 1024):
                f.write(chunk)
        try:
            with Image.open(dest) as im:
                im.verify()  # extensão de imagem, mas o conteúdo não é
        except Exception:
            dest.unlink(missing_ok=True)
            skipped.append(filename)
            continue
        try:
            await run_in_threadpool(cloud_put, dest)
        except cloud_storage.StorageError as exc:
            print(f"[storage] {filename}: {exc}")
            dest.unlink(missing_ok=True)
            skipped.append(filename)
            continue
        with db() as con:
            con.execute(
                "INSERT INTO library_arts (id,created_at,category_id,name,file_path) VALUES(?,?,?,?,?)",
                (aid, time.time(), category_id, Path(filename).stem.strip() or "Sem título", str(dest))
            )
        saved += 1
    return {"ok": True, "saved": saved, "skipped": skipped}


@app.delete("/api/admin/library/arts/{art_id}", dependencies=[Depends(require_admin)])
def delete_library_art(art_id: str) -> dict[str, Any]:
    with db() as con:
        row = con.execute("SELECT file_path FROM library_arts WHERE id=?", (art_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Arte não encontrada.")
        con.execute("DELETE FROM library_arts WHERE id=?", (art_id,))
    remove_library_files(art_id, row["file_path"])
    return {"ok": True}


@app.post("/api/premium/subscribe")
def subscribe_premium(
    request: Request, payload: dict[str, Any] | None = None,
    customer: dict[str, Any] = Depends(require_customer),
) -> dict[str, Any]:
    """Gera o pedido do plano premium. O acesso só é liberado quando o
    pagamento é aprovado (finish_paid_order)."""
    check_order_rate_limit(customer)
    cfg = digital_cfg()
    price = round(float(cfg.get("premium_price_month") or 0), 2)
    if price <= 0:
        raise HTTPException(503, "Preço do premium não configurado.")
    days = int(cfg.get("premium_days") or 30)
    oid = uuid.uuid4().hex[:12]
    order_number = next_order_number()
    data: dict[str, Any] = {
        "sale_type": "PREMIUM_SUB",
        "customer": {"name": customer["full_name"], "email": customer["email"], "whatsapp": customer["phone"]},
        "customer_id": customer["id"],
        "premium_days": days,
        "total": price,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tracking": clean_tracking((payload or {}).get("tracking"), request),
    }
    for attempt in range(2):
        data["order_number"] = order_number
        try:
            with db() as con:
                con.execute(
                    """INSERT INTO orders
                       (id,order_number,created_at,customer_name,customer_email,
                        customer_whatsapp,sale_type,status,payment_status,total,
                        folder_path,production_drop_path,data_json,customer_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        oid, order_number, time.time(), customer["full_name"], customer["email"],
                        customer["phone"], "PREMIUM_SUB", "AGUARDANDO_PAGAMENTO", "PENDING", price,
                        "", None, json.dumps(data, ensure_ascii=False), customer["id"]
                    )
                )
            break
        except dbcompat.IntegrityError:
            # Mesmo caso do create_order: número repetido no mesmo segundo.
            if attempt:
                raise
            order_number = f"{order_number}-{uuid.uuid4().hex[:4]}"
    return {"ok": True, "id": oid, "order_number": order_number, "total": price, "days": days}


@app.post("/api/ai/generate")
def ai_generate(payload: dict[str, Any], customer: dict[str, Any] = Depends(require_customer)) -> dict[str, Any]:
    """Cada geração custa na conta da OpenAI: só com login, e com limite por
    minuto e por dia em cada conta (ai_daily_limit no config.json)."""
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(400, "Descreva a arte.")
    if len(prompt) > 500:
        raise HTTPException(400, "Descrição muito longa (máximo 500 caracteres).")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(503, "IA não configurada no .env.")

    minute_key = f"ai:{customer['id']}"
    check_rate_limit(minute_key, 6, 60, "Muitas gerações por IA em pouco tempo. Aguarde um minuto.", record=False)
    daily = int(CONFIG.get("ai_daily_limit") or 20)
    check_rate_limit(f"ai-day:{customer['id']}", daily, 86400,
                     f"Você chegou ao limite de {daily} artes por IA hoje. Tente de novo amanhã.")
    record_hit(minute_key)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")

        response = client.images.generate(
            model=model,
            prompt=(
                "Arte para estampa DTF de camiseta. Somente a arte, sem camiseta, "
                "sem mockup e com fundo transparente. "
                f"Descrição: {prompt}"
            ),
            size="1024x1024",
            quality="medium",
            background="transparent",
        )

        if not response.data:
            raise RuntimeError("A API não retornou a imagem.")

        item = response.data[0]
        image_bytes = base64.b64decode(item.b64_json) if item.b64_json else None

        if not image_bytes:
            raise RuntimeError("A API não retornou a imagem.")

        gid = uuid.uuid4().hex[:12]
        dest = GENERATED_DIR / f"{gid}.png"
        dest.write_bytes(image_bytes)
        cloud_put(dest)

        return {"ok": True, "url": f"/media/generated/{gid}.png", "model": model}
    except Exception as exc:
        raise HTTPException(500, f"Falha ao gerar imagem: {exc}")


# ---------- Envio direto das artes pro Supabase ----------
# Uma chapa de DTF chega a 500 MB. Em vez de passar pelo servidor do site
# (o proxy do Render pode recusar envios grandes, e o disco dele é
# temporário), o navegador pede aqui links de envio assinados e manda cada
# arquivo direto pro Storage, numa pasta provisória do cliente. Depois cria
# o pedido mandando só o "token" dessa pasta: o servidor confere os
# arquivos e move pra pasta do pedido, sem baixar nada.
# Campo do formulário -> (nome do arquivo no pedido, chave no pedido.json).
ORDER_FILE_FIELDS = {
    "design": (None, "production_art"),  # ARTE_PRODUCAO (chapa) ou ARTE_PRODUCAO_FRENTE
    "design_back": ("ARTE_PRODUCAO_COSTAS", "production_art_back"),
    "design_sleeve_right": ("ARTE_PRODUCAO_MANGA_DIREITA", "production_art_sleeve_right"),
    "design_sleeve_left": ("ARTE_PRODUCAO_MANGA_ESQUERDA", "production_art_sleeve_left"),
    "placement_front": ("POSICAO_FRENTE", "placement_front"),
    "placement_back": ("POSICAO_COSTAS", "placement_back"),
}
UPLOAD_TOKEN = re.compile(r"\d{10}-[0-9a-f]{16}")
# Somente DTF: cada arte da chapa vai também separada, no arquivo original do
# cliente (source_1, source_2...). Vira ORIGINAL_01.pdf etc. na pasta do pedido.
SOURCE_FIELD = re.compile(r"source_([1-9]\d?)")
SOURCE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".pdf"}
SOURCE_FORMATS = {".pdf": "pdf", ".tif": "tiff", ".tiff": "tiff"}  # o resto conta como png


def source_ext(value: Any) -> str:
    ext = str(value or "").lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in SOURCE_EXTS:
        raise HTTPException(400, "Use arquivos PNG, PDF, TIFF, JPG ou WEBP.")
    return ext


def note_source_file(data: dict[str, Any], number: int, file_name: str):
    """Anota no pedido qual arquivo original é de cada arte da chapa."""
    sources = data.get("sources")
    if isinstance(sources, list) and 0 < number <= len(sources) and isinstance(sources[number - 1], dict):
        sources[number - 1]["file"] = file_name
        sources[number - 1]["format"] = SOURCE_FORMATS.get(Path(file_name).suffix.lower(), "png")
UPLOAD_MAX_AGE = 86400  # pasta provisória esquecida (pedido desistido) some depois de 1 dia


def staged_ext(field: str) -> str:
    return ".jpg" if field.startswith("placement") else ".png"


def staging_prefix(customer_id: str, token: str = "") -> str:
    return f"uploads/{customer_id}" + (f"/{token}" if token else "")


def cleanup_staged_uploads(customer_id: str, max_age: float = UPLOAD_MAX_AGE):
    """Apaga as pastas provisórias do cliente mais velhas que max_age (0 = todas)."""
    if not STORE:
        return
    try:
        for token in STORE.list_folders(staging_prefix(customer_id)):
            if UPLOAD_TOKEN.fullmatch(token) and time.time() - int(token.split("-")[0]) >= max_age:
                STORE.delete_dir(prefix_key=staging_prefix(customer_id, token))
    except cloud_storage.StorageError as exc:
        print(f"[storage] limpeza de envios: {exc}")


def image_info_bytes(head: bytes) -> dict[str, int | bool | None]:
    """Igual ao image_info, lendo só o começo do arquivo."""
    import io
    result: dict[str, int | bool | None] = {"width_px": None, "height_px": None, "has_alpha": None}
    try:
        with Image.open(io.BytesIO(head)) as im:
            result["width_px"], result["height_px"] = im.size
            result["has_alpha"] = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    except Exception:
        pass
    return result


class UploadRequest(BaseModel):
    files: list[dict[str, Any]]


@app.post("/api/orders/uploads")
def create_order_uploads(payload: UploadRequest, buyer: dict[str, Any] = Depends(require_customer)) -> dict[str, Any]:
    if not STORE:
        raise HTTPException(503, "Envio direto não configurado neste servidor.")
    check_rate_limit(f"uploads:{buyer['id']}", 30, 3600,
                     "Muitos envios em pouco tempo. Aguarde um pouco e tente de novo.")
    max_mb = int(CONFIG.get("max_upload_mb") or 600)
    fields: dict[str, str] = {}  # campo -> extensão
    if len(payload.files) > len(ORDER_FILE_FIELDS) + 99:
        raise HTTPException(400, "Artes demais num pedido só.")
    for f in payload.files:
        field = str(f.get("field") or "")
        if field in fields or not (field in ORDER_FILE_FIELDS or SOURCE_FIELD.fullmatch(field)):
            raise HTTPException(400, "Arquivo do pedido inválido.")
        if int(f.get("size") or 0) > max_mb * 1024 * 1024:
            raise HTTPException(413, f"Arquivo grande demais (máximo {max_mb} MB).")
        fields[field] = source_ext(f.get("ext")) if field.startswith("source_") else staged_ext(field)
    if not fields:
        raise HTTPException(400, "Nenhum arquivo para enviar.")
    cleanup_staged_uploads(buyer["id"])
    token = f"{int(time.time())}-{secrets.token_hex(8)}"
    try:
        urls = {
            field: STORE.signed_upload_url(f"{staging_prefix(buyer['id'], token)}/{field}{ext}")
            for field, ext in fields.items()
        }
    except cloud_storage.StorageError as exc:
        print(f"[storage] {exc}")
        raise HTTPException(502, "Não foi possível preparar o envio das artes. Tente de novo em instantes.")
    return {"token": token, "urls": urls}


def take_staged_files(buyer_id: str, token: str, folder: Path, sale_type: str, data: dict[str, Any]):
    """Move os arquivos que o navegador mandou pro Storage (pasta provisória)
    pra pasta do pedido e anota tamanho/transparência no pedido, como o
    envio pelo formulário fazia. Roda numa thread (só chamadas de rede)."""
    assert STORE
    if not UPLOAD_TOKEN.fullmatch(token):
        raise HTTPException(400, "Envio das artes inválido. Tente de novo.")
    max_mb = int(CONFIG.get("max_upload_mb") or 600)
    dpi = int(CONFIG.get("dpi") or 300)
    staged = {Path(f["name"]).stem: f for f in STORE.list_files(prefix_key=staging_prefix(buyer_id, token))}
    if not staged:
        raise HTTPException(400, "As artes não chegaram ao servidor. Tente finalizar o pedido de novo.")
    for field, (base_name, data_key) in ORDER_FILE_FIELDS.items():
        item = staged.get(field)
        if not item:
            continue
        if field.endswith("sleeve_right") or field.endswith("sleeve_left"):
            if not SLEEVES_ENABLED:
                raise HTTPException(400, "Estampa na manga está desativada no momento.")
        if item["size"] > max_mb * 1024 * 1024:
            raise HTTPException(413, f"Arquivo grande demais (máximo {max_mb} MB).")
        if base_name is None:
            base_name = "ARTE_PRODUCAO" if sale_type == "DTF_ONLY" else "ARTE_PRODUCAO_FRENTE"
        dest = folder / f"{base_name}{staged_ext(field)}"
        head = STORE.read_head(item["key"])
        if dest.suffix == ".png" and png_with_dpi(head, dpi) != head:
            # O navegador já grava o DPI antes de enviar; isto é só pra uma
            # página antiga em cache: baixa, corrige e sobe de novo.
            local = folder / f".{field}.tmp"
            STORE.download(local, item["key"])
            with local.open("rb") as src, dest.open("wb") as out:
                out.write(png_with_dpi(src.read(1024 * 1024), dpi))
                shutil.copyfileobj(src, out, 1024 * 1024)
            local.unlink(missing_ok=True)
            STORE.upload(dest)
            STORE.delete_keys([item["key"]])
            dest.unlink(missing_ok=True)  # o Storage é a cópia que vale
            head = png_with_dpi(head, dpi)
        else:
            STORE.move(item["key"], STORE.key_for(dest))
        data[data_key] = {"file": dest.name, **image_info_bytes(head)}
    if sale_type != "DTF_ONLY":
        return
    for stem, item in staged.items():
        m = SOURCE_FIELD.fullmatch(stem)
        if not m:
            continue
        if item["size"] > max_mb * 1024 * 1024:
            raise HTTPException(413, f"Arquivo grande demais (máximo {max_mb} MB).")
        number = int(m.group(1))
        dest = folder / f"ORIGINAL_{number:02d}{source_ext(Path(item['name']).suffix)}"
        STORE.move(item["key"], STORE.key_for(dest))
        note_source_file(data, number, dest.name)


@app.post("/api/orders")
async def create_order(
    request: Request,
    order_json: str = Form(...),
    design: UploadFile | None = File(None),
    design_back: UploadFile | None = File(None),
    design_sleeve_right: UploadFile | None = File(None),
    design_sleeve_left: UploadFile | None = File(None),
    placement_front: UploadFile | None = File(None),
    placement_back: UploadFile | None = File(None),
    upload_token: str | None = Form(None),
    buyer: dict[str, Any] = Depends(require_customer),
) -> dict[str, Any]:
    # Dono do pedido (aparece em "Minha conta"). Antes o login era exigido só
    # no navegador: qualquer um podia mandar pedidos com arquivos enormes
    # direto pra API e encher o disco do servidor.
    check_order_rate_limit(buyer)
    try:
        data = json.loads(order_json)
    except json.JSONDecodeError:
        raise HTTPException(400, "Pedido inválido.")

    customer = data.get("customer", {})
    name = str(customer.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Informe o nome.")

    sale_type = str(data.get("sale_type", "")).strip()
    if sale_type not in {
        "DTF_ONLY", "DTF_PLUS_SHIRT", "FINISHED_SHIRT", "DIGITAL_ART"
    }:
        raise HTTPException(400, "Modalidade inválida.")
    if sale_type in DISABLED_SALE_TYPES:
        raise HTTPException(400, "Essa modalidade está desativada no momento.")
    if not SLEEVES_ENABLED and any(u is not None and u.filename for u in (design_sleeve_right, design_sleeve_left)):
        raise HTTPException(400, "Estampa na manga está desativada no momento.")

    order_number = next_order_number()
    oid = uuid.uuid4().hex[:12]
    folder = ORDERS_DIR / f"{order_number}__{safe_name(name)}"
    try:
        folder.mkdir(parents=True)
    except FileExistsError:
        # Pasta que sobrou de um pedido apagado do banco, ou outro pedido com
        # o mesmo nome no mesmo segundo: antes isso virava erro 500.
        order_number = f"{order_number}-{oid[:4]}"
        folder = ORDERS_DIR / f"{order_number}__{safe_name(name)}"
        folder.mkdir(parents=True)

    digital_artwork_row = None
    if sale_type == "DIGITAL_ART":
        digital_id = str(data.get("digital_artwork_id", "")).strip()
        if not digital_id:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(400, "Arte digital não informada.")
        with db() as con:
            row = con.execute(
                "SELECT * FROM artworks WHERE id=? AND active=1", (digital_id,)
            ).fetchone()
        if not row:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(404, "Arte digital não encontrada.")
        digital_artwork_row = dict(row)

    try:
        if upload_token:
            if not STORE:
                raise HTTPException(400, "Envio direto não configurado neste servidor.")
            await run_in_threadpool(take_staged_files, buyer["id"], upload_token, folder, sale_type, data)
        if design is not None and design.filename:
            ext = Path(design.filename).suffix.lower() or ".png"
            # Na camiseta o "design" é a frente; no Somente DTF é a chapa.
            front_name = "ARTE_PRODUCAO" if sale_type == "DTF_ONLY" else "ARTE_PRODUCAO_FRENTE"
            design_path = folder / f"{front_name}{ext}"
            await save_upload(design, design_path)

            data["production_art"] = {
                "file": design_path.name,
                **image_info(design_path),
            }

        # Verso e mangas: cada arte vira um arquivo ARTE_PRODUCAO_* na pasta
        # do pedido (o prefixo faz ela ir junto pra pasta de produção).
        # POSICAO_* é a foto da camisa com a arte no lugar, pra quem estampa.
        extra_arts = (
            (design_back, "ARTE_PRODUCAO_COSTAS", "production_art_back"),
            (design_sleeve_right, "ARTE_PRODUCAO_MANGA_DIREITA", "production_art_sleeve_right"),
            (design_sleeve_left, "ARTE_PRODUCAO_MANGA_ESQUERDA", "production_art_sleeve_left"),
            (placement_front, "POSICAO_FRENTE", "placement_front"),
            (placement_back, "POSICAO_COSTAS", "placement_back"),
        )
        for upload, base_name, data_key in extra_arts:
            if upload is None or not upload.filename:
                continue
            ext_x = Path(upload.filename).suffix.lower() or ".png"
            art_path = folder / f"{base_name}{ext_x}"
            await save_upload(upload, art_path)
            data[data_key] = {
                "file": art_path.name,
                **image_info(art_path),
            }

        if sale_type == "DTF_ONLY" and not upload_token:
            form = await request.form()
            for key, upload in form.multi_items():
                m = SOURCE_FIELD.fullmatch(key)
                if not m or not hasattr(upload, "filename") or not upload.filename:
                    continue
                number = int(m.group(1))
                dest = folder / f"ORIGINAL_{number:02d}{source_ext(Path(upload.filename).suffix)}"
                await save_upload(upload, dest)
                note_source_file(data, number, dest.name)

        if digital_artwork_row:
            # A venda de arte digital não tinha o arquivo comprado
            # copiado pro pedido — o cliente pagava e não recebia nada.
            src = Path(digital_artwork_row["file_path"])
            ext = src.suffix.lower() or ".png"
            digital_dest = folder / f"ARTE_DIGITAL{ext}"
            if await run_in_threadpool(cloud_get, src):
                shutil.copy2(src, digital_dest)
                data["digital_art_file"] = digital_dest.name
            else:
                data["digital_art_file"] = None
                data.setdefault("warnings", []).append(
                    "Arquivo original da arte digital não encontrado em disco."
                )

        data["tracking"] = clean_tracking(data.get("tracking"), request)
        data["total"] = calc_order_total(sale_type, data, digital_artwork_row)
        data["order_number"] = order_number
        data["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data["status"] = "AGUARDANDO_PAGAMENTO"
        data["payment_status"] = "PENDING"

        (folder / "pedido.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # No servidor, a pasta do pedido sobe pro Storage antes de o pedido
        # existir no banco: pedido salvo sem as artes não acontece.
        await run_in_threadpool(cloud_put_dir, folder)

        total = float(data.get("total", 0))
        try:
            with db() as con:
                con.execute(
                    """INSERT INTO orders
                       (id,order_number,created_at,customer_name,customer_email,
                        customer_whatsapp,sale_type,status,payment_status,total,
                        folder_path,production_drop_path,data_json,customer_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        oid, order_number, time.time(), name,
                        customer.get("email", ""), customer.get("whatsapp", ""),
                        sale_type, "AGUARDANDO_PAGAMENTO", "PENDING", total,
                        str(folder), None, json.dumps(data, ensure_ascii=False),
                        buyer["id"]
                    )
                )
        except dbcompat.IntegrityError:
            # Dois pedidos criados no mesmíssimo segundo podem colidir
            # no número (order_number é calculado antes do INSERT, não é
            # atômico). Antes isso derrubava o pedido com um erro cru;
            # agora tenta de novo com um sufixo, sem perder a arte já
            # salva em disco.
            order_number = f"{order_number}-{uuid.uuid4().hex[:4]}"
            data["order_number"] = order_number
            (folder / "pedido.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            await run_in_threadpool(cloud_put, folder / "pedido.json")
            with db() as con:
                con.execute(
                    """INSERT INTO orders
                       (id,order_number,created_at,customer_name,customer_email,
                        customer_whatsapp,sale_type,status,payment_status,total,
                        folder_path,production_drop_path,data_json,customer_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        oid, order_number, time.time(), name,
                        customer.get("email", ""), customer.get("whatsapp", ""),
                        sale_type, "AGUARDANDO_PAGAMENTO", "PENDING", total,
                        str(folder), None, json.dumps(data, ensure_ascii=False),
                        buyer["id"]
                    )
                )

        if sale_type == "DTF_ONLY":
            await run_in_threadpool(write_dtf_report, order_number, name, data.get("sources") or [])

        return {
            "ok": True,
            "id": oid,
            "order_number": order_number,
            "status": "AGUARDANDO_PAGAMENTO",
            "total": total
        }
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        await run_in_threadpool(cloud_delete_dir, folder)
        if isinstance(exc, cloud_storage.StorageError):
            print(f"[storage] pedido não criado: {exc}")
            raise HTTPException(502, "Não foi possível guardar as artes do pedido. Tente de novo em instantes.")
        raise


@app.get("/api/orders", dependencies=[Depends(require_staff)])
def list_orders() -> list[dict[str, Any]]:
    cancel_stale_orders()
    with db() as con:
        rows = con.execute(
            """SELECT id,order_number,created_at,customer_name,customer_email,customer_whatsapp,
                      sale_type,status,payment_status,total,folder_path,production_drop_path,
                      data_json,stage,stage_at,payment_ref
               FROM orders ORDER BY created_at DESC"""
        ).fetchall()

    costs = get_costs()
    result: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        try:
            d["data"] = json.loads(d.pop("data_json"))
        except Exception:
            d["data"] = {}
        try:
            d["cost"] = order_cost(d, d["data"], costs) if d["payment_status"] == "PAID" else None
        except Exception:
            d["cost"] = None
        result.append(d)
    return result


@app.post("/api/orders/{order_id}/mark-paid", dependencies=[Depends(require_admin)])
def mark_paid(order_id: str) -> dict[str, Any]:
    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")

    path = mark_order_paid(order_id)

    return {
        "ok": True,
        "status": "LIBERADO_PRODUCAO",
        "payment_status": "PAID",
        "production_drop_path": path
    }


@app.post("/api/orders/{order_id}/mark-ready", dependencies=[Depends(require_staff)])
def mark_ready(order_id: str) -> dict[str, Any]:
    """Última etapa que o cliente vê em "Minha conta": pedido pronto."""
    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")
    if row["sale_type"] == "PREMIUM_SUB":
        raise HTTPException(400, "Pedido de premium não tem produção.")
    if row["payment_status"] != "PAID":
        raise HTTPException(400, "Pedido ainda não está pago.")
    with db() as con:
        con.execute("UPDATE orders SET status='PRONTO', stage='FINALIZADO', stage_at=? WHERE id=?",
                    (time.time(), order_id))
    return {"ok": True, "status": "PRONTO"}


# ---------- Etapas da produção (Admin) ----------
# Pago → Em análise → Em fila → Em impressão → Finalizado → Enviado. Vazio = pago
# e ainda não mexido. Finalizado/Enviado também marcam o pedido como PRONTO,
# que é o que o cliente vê em "Minha conta".
ORDER_STAGES = {
    "PAGO": "Pago", "EM_ANALISE": "Em análise", "EM_FILA": "Em fila",
    "EM_IMPRESSAO": "Em impressão", "FINALIZADO": "Finalizado", "ENVIADO": "Enviado",
}


class StaffLogin(BaseModel):
    user: str
    password: str
    code: str = ""


@app.post("/api/admin/login")
def staff_login(payload: StaffLogin, request: Request) -> dict[str, Any]:
    user = payload.user.strip()
    account = check_staff_password(request, user, payload.password)
    totp = staff_totp_row(account["user"])
    if totp and totp["enabled"]:
        if not payload.code.strip():
            return {"need_code": True}
        fail_keys = [f"admin-ip:{client_ip(request)}", f"admin-user:{user.lower()[:100]}"]
        counter = totp_match(totp["secret"], payload.code, int(totp["last_counter"] or 0))
        if counter is None:
            login_failed(fail_keys, "Código de verificação inválido.")
        with db() as con:
            con.execute("UPDATE staff_totp SET last_counter=? WHERE username=?", (counter, account["user"]))
    login_ok([f"admin-ip:{client_ip(request)}", f"admin-user:{user.lower()[:100]}"])
    return {"token": new_staff_session(account["user"]), "user": account["user"], "role": account["role"],
            "two_factor": bool(totp and totp["enabled"]), "expires_in": STAFF_SESSION_SECONDS}


@app.post("/api/admin/logout")
def staff_logout(staff: dict[str, Any] = Depends(require_staff)) -> dict[str, Any]:
    if staff["token"]:
        with db() as con:
            con.execute("DELETE FROM staff_sessions WHERE token_hash=?",
                        (hashlib.sha256(staff["token"].encode()).hexdigest(),))
    return {"ok": True}


@app.get("/api/admin/me")
def staff_me(staff: dict[str, Any] = Depends(require_staff)) -> dict[str, Any]:
    return {"user": staff["user"], "role": staff["role"], "role_name": ROLES[staff["role"]],
            "two_factor": staff_has_2fa(staff["user"])}


class TwoFactorCode(BaseModel):
    code: str = ""
    password: str = ""


@app.post("/api/admin/2fa/setup")
def staff_2fa_setup(staff: dict[str, Any] = Depends(require_staff)) -> dict[str, Any]:
    """Gera a chave nova (ainda desligada). Liga só depois de confirmar um código."""
    if staff_has_2fa(staff["user"]):
        raise HTTPException(400, "A verificação em duas etapas já está ligada.")
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    with db() as con:
        con.execute("DELETE FROM staff_totp WHERE username=?", (staff["user"],))
        con.execute("INSERT INTO staff_totp (username, secret, enabled, last_counter) VALUES(?,?,0,0)",
                    (staff["user"], secret))
    issuer = urllib.parse.quote(str(CONFIG.get("store_name") or "Ripfire Studio"))
    label = urllib.parse.quote(f"{CONFIG.get('store_name') or 'Ripfire'} Admin:{staff['user']}")
    return {"secret": secret, "uri": f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&digits=6&period=30"}


@app.post("/api/admin/2fa/enable")
def staff_2fa_enable(payload: TwoFactorCode, request: Request, staff: dict[str, Any] = Depends(require_staff)):
    row = staff_totp_row(staff["user"])
    if not row:
        raise HTTPException(400, "Gere a chave primeiro.")
    fail_keys = [f"2fa:{staff['user']}"]
    login_guard(fail_keys)
    counter = totp_match(row["secret"], payload.code, int(row["last_counter"] or 0))
    if counter is None:
        login_failed(fail_keys, "Código inválido. Confira a hora do celular e tente o código novo.", 400)
    with db() as con:
        con.execute("UPDATE staff_totp SET enabled=1, last_counter=? WHERE username=?", (counter, staff["user"]))
        # Liga o 2FA e derruba as outras sessões desse usuário.
        con.execute("DELETE FROM staff_sessions WHERE username=? AND token_hash!=?",
                    (staff["user"], hashlib.sha256((staff["token"] or "").encode()).hexdigest()))
    return {"ok": True}


@app.post("/api/admin/2fa/disable")
def staff_2fa_disable(payload: TwoFactorCode, request: Request, staff: dict[str, Any] = Depends(require_staff)):
    check_staff_password(request, staff["user"], payload.password)
    row = staff_totp_row(staff["user"])
    if row and row["enabled"] and totp_match(row["secret"], payload.code, int(row["last_counter"] or 0)) is None:
        raise HTTPException(400, "Código de verificação inválido.")
    with db() as con:
        con.execute("DELETE FROM staff_totp WHERE username=?", (staff["user"],))
    return {"ok": True}


@app.get("/api/admin/staff", dependencies=[Depends(require_admin)])
def staff_list() -> list[dict[str, Any]]:
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT username, role, created_at, created_by FROM staff_users").fetchall()]
        twofa = {r["username"] for r in con.execute("SELECT username FROM staff_totp WHERE enabled=1").fetchall()}
    out = [{"user": a["user"], "role": a["role"], "source": "config", "created_at": None, "created_by": ""}
           for a in config_staff().values()]
    out += [{"user": r["username"], "role": r["role"], "source": "equipe", "created_at": r["created_at"],
             "created_by": r["created_by"]} for r in rows if r["username"] not in config_staff()]
    for item in out:
        item["two_factor"] = item["user"] in twofa
        item["role_name"] = ROLES.get(item["role"], item["role"])
    return out


class StaffCreate(BaseModel):
    user: str
    password: str
    role: str


@app.post("/api/admin/staff")
def staff_create(payload: StaffCreate, staff: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    user = payload.user.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", user):
        raise HTTPException(400, "Usuário com 3 a 40 letras, números, ponto, traço ou sublinhado.")
    if payload.role not in ROLES:
        raise HTTPException(400, "Papel inválido.")
    if len(payload.password) < 12:
        raise HTTPException(400, "Use pelo menos 12 caracteres na senha.")
    if staff_account(user):
        raise HTTPException(400, "Já existe um usuário com esse nome.")
    with db() as con:
        con.execute("INSERT INTO staff_users (username, password_hash, role, created_at, created_by) VALUES(?,?,?,?,?)",
                    (user, hash_password(payload.password), payload.role, time.time(), staff["user"]))
    return {"ok": True}


@app.delete("/api/admin/staff/{user}")
def staff_delete(user: str, staff: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if user in config_staff():
        raise HTTPException(400, "Esse usuário vem do config.json; remova por lá.")
    if user == staff["user"]:
        raise HTTPException(400, "Você não pode remover o próprio usuário.")
    with db() as con:
        con.execute("DELETE FROM staff_users WHERE username=?", (user,))
        con.execute("DELETE FROM staff_totp WHERE username=?", (user,))
        con.execute("DELETE FROM staff_sessions WHERE username=?", (user,))
    return {"ok": True}


@app.delete("/api/admin/staff/{user}/2fa")
def staff_reset_2fa(user: str, staff: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Pra quem perdeu o celular: desliga o 2FA e derruba as sessões dele."""
    with db() as con:
        con.execute("DELETE FROM staff_totp WHERE username=?", (user,))
        con.execute("DELETE FROM staff_sessions WHERE username=?", (user,))
    return {"ok": True}


@app.get("/api/admin/security/ip", dependencies=[Depends(require_admin)])
def security_ip(request: Request) -> dict[str, Any]:
    """Diagnóstico: de onde o servidor acha que a chamada veio."""
    names = ("x-forwarded-for", "cf-connecting-ip", "true-client-ip", "x-real-ip", "x-forwarded-proto")
    return {"client_ip": client_ip(request), "connection": request.client.host if request.client else None,
            "headers": {k: request.headers.get(k) for k in names}, "client_ip_header": CLIENT_IP_HEADER}


@app.patch("/api/admin/orders/{order_id}/stage", dependencies=[Depends(require_staff)])
def set_order_stage(order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    stage = str(payload.get("stage") or "").upper()
    if stage not in ORDER_STAGES:
        raise HTTPException(400, "Etapa inválida.")
    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")
    if row["sale_type"] == "PREMIUM_SUB":
        raise HTTPException(400, "Pedido de premium não tem produção.")
    if row["payment_status"] != "PAID":
        raise HTTPException(400, "Pedido ainda não está pago.")
    with db() as con:
        con.execute(
            """UPDATE orders SET stage=?, stage_at=?,
               status=CASE WHEN ? IN ('FINALIZADO','ENVIADO') THEN 'PRONTO'
                           WHEN status='PRONTO' THEN 'LIBERADO_PRODUCAO' ELSE status END
               WHERE id=?""",
            (stage, time.time(), stage, order_id)
        )
    return {"ok": True, "stage": stage}


# ---------- Custos (Admin) ----------
# Guardados na tabela settings (vão pro Supabase junto com o resto). Com eles o
# painel calcula o custo e o lucro de cada pedido pago.
DEFAULT_COSTS: dict[str, Any] = {
    "dtf_per_meter": 0.0,      # impressão DTF: custo por metro do rolo (filme, tinta, pó)
    "press_per_piece": 0.0,    # prensagem / mão de obra por camiseta
    "packaging_per_order": 0.0,  # embalagem e outros custos fixos por pedido
    "payment_fee_pct": 0.0,    # taxa do meio de pagamento (% do valor)
    "shirt_cost": {},          # valor bruto da camiseta lisa por modelo (id -> R$)
}


def get_costs() -> dict[str, Any]:
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key='costs'").fetchone()
    saved = json.loads(row["value"]) if row else {}
    costs = {**DEFAULT_COSTS, **{k: v for k, v in saved.items() if k in DEFAULT_COSTS}}
    costs["shirt_cost"] = {m["id"]: float((saved.get("shirt_cost") or {}).get(m["id"]) or 0)
                           for m in CONFIG.get("shirt_models") or []}
    return costs


def order_cost(row: dict[str, Any], data: dict[str, Any], costs: dict[str, Any]) -> float:
    """Custo estimado do pedido com os valores da aba Custos."""
    sale = row["sale_type"]
    total = float(row["total"] or 0)
    fee = total * float(costs.get("payment_fee_pct") or 0) / 100
    if sale in ("PREMIUM_SUB", "DIGITAL_ART"):
        return round(fee, 2)
    dpi = float(CONFIG.get("dpi") or 300)
    per_m = float(costs.get("dtf_per_meter") or 0)
    roll_m = float(CONFIG.get("dtf_sheet_width_cm") or 58.5) / 100
    cost = fee + float(costs.get("packaging_per_order") or 0)
    if sale == "DTF_ONLY":
        art = data.get("production_art") or {}
        h_cm = (art.get("height_px") or 0) / dpi * 2.54
        meters = h_cm / 100  # o que gasta de rolo é o tamanho real da chapa (o mínimo é só regra de preço)
        return round(cost + meters * per_m, 2)
    # Camiseta: camisa lisa + prensagem por peça + DTF pela área das artes
    # (área ÷ largura do rolo = metros de rolo que a arte ocupa).
    qty = int(data.get("dtf_quantity") or 0)
    model = (data.get("product") or {}).get("shirt_model_id") or ""
    shirt = float((costs.get("shirt_cost") or {}).get(model) or 0)
    area_m2 = 0.0
    for key in ("production_art", "production_art_back", "production_art_sleeve_right", "production_art_sleeve_left"):
        art = data.get(key) or {}
        if art.get("width_px") and art.get("height_px"):
            area_m2 += (art["width_px"] / dpi * 0.0254) * (art["height_px"] / dpi * 0.0254)
    dtf = area_m2 / roll_m * per_m if roll_m else 0
    return round(cost + qty * (shirt + float(costs.get("press_per_piece") or 0) + dtf), 2)


@app.get("/api/admin/costs", dependencies=[Depends(require_admin)])
def admin_get_costs() -> dict[str, Any]:
    return {"costs": get_costs(), "models": [{"id": m["id"], "name": m["name"]} for m in CONFIG.get("shirt_models") or []]}


@app.put("/api/admin/costs", dependencies=[Depends(require_admin)])
def admin_save_costs(payload: dict[str, Any]) -> dict[str, Any]:
    def num(v: Any) -> float:
        try:
            x = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            raise HTTPException(400, "Use só números nos custos.")
        if not 0 <= x <= 1_000_000:
            raise HTTPException(400, "Valor de custo fora do esperado.")
        return round(x, 2)
    costs = {k: num(payload.get(k, 0)) for k in DEFAULT_COSTS if k != "shirt_cost"}
    if costs["payment_fee_pct"] > 100:
        raise HTTPException(400, "A taxa do pagamento vai de 0 a 100%.")
    sent = payload.get("shirt_cost") or {}
    costs["shirt_cost"] = {m["id"]: num(sent.get(m["id"], 0)) for m in CONFIG.get("shirt_models") or []}
    with db() as con:
        con.execute(
            "INSERT INTO settings (key,value) VALUES('costs',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(costs),)
        )
    return {"ok": True, "costs": get_costs()}


# ---------- Clientes (Admin) ----------
@app.get("/api/admin/customers", dependencies=[Depends(require_admin)])
def admin_customers() -> list[dict[str, Any]]:
    with db() as con:
        rows = con.execute(
            """SELECT c.id, c.created_at, c.email, c.full_name, c.phone, c.premium_until,
                      CASE WHEN c.google_sub IS NULL THEN 0 ELSE 1 END AS google,
                      (SELECT COUNT(*) FROM orders o WHERE o.customer_id=c.id) AS orders,
                      (SELECT COALESCE(SUM(o.total),0) FROM orders o
                        WHERE o.customer_id=c.id AND o.payment_status='PAID') AS spent
               FROM customers c ORDER BY c.created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- Painel (Admin) ----------
@app.get("/api/admin/dashboard", dependencies=[Depends(require_admin)])
def admin_dashboard(days: int = 30) -> dict[str, Any]:
    """Tudo que o painel do Admin mostra, por dia: visitas, contas novas,
    premium, pedidos, receita, custo. O navegador agrupa em semana/mês."""
    days = max(1, min(days, 730))
    since_ts = time.time() - days * 86400
    since_day = time.strftime("%Y-%m-%d", time.localtime(since_ts))
    day_expr = local_day_sql("created_at")
    costs = get_costs()
    with db() as con:
        hits = [dict(r) for r in con.execute(
            "SELECT day, page, hits FROM page_hits WHERE day >= ?", (since_day,))]
        signups = [dict(r) for r in con.execute(
            f"SELECT {day_expr} AS day, COUNT(*) AS n FROM customers WHERE created_at >= ? GROUP BY 1",
            (since_ts,))]
        orders = [dict(r) for r in con.execute(
            f"""SELECT {day_expr} AS day, sale_type, payment_status, status, stage, total, data_json
                FROM orders WHERE created_at >= ?""", (since_ts,))]
        totals = con.execute(
            """SELECT (SELECT COUNT(*) FROM customers) AS customers,
                      (SELECT COUNT(*) FROM customers WHERE premium_until > ?) AS premium_active,
                      (SELECT COUNT(*) FROM orders WHERE payment_status='PAID' AND sale_type!='PREMIUM_SUB'
                         AND COALESCE(stage,'') NOT IN ('FINALIZADO','ENVIADO')) AS in_production""",
            (time.time(),)
        ).fetchone()
    out_orders = []
    for o in orders:
        try:
            data = json.loads(o.pop("data_json") or "{}")
        except Exception:
            data = {}
        o["cost"] = order_cost(o, data, costs) if o["payment_status"] == "PAID" else 0
        out_orders.append(o)
    return {"days": days, "since": since_day, "hits": hits, "signups": signups, "orders": out_orders,
            "totals": dict(totals), "costs_set": any(
                v for k, v in costs.items() if k != "shirt_cost") or any(costs["shirt_cost"].values())}


@app.get("/api/admin/orders/{order_id}/files", dependencies=[Depends(require_staff)])
def download_order_files(order_id: str):
    """Arquivos do pedido num zip, baixados pelo Admin. Com o site num
    servidor, orders/, PRODUCAO_PAGA/ e SomenteDTFs/ ficam lá, não no PC
    do RIP: é por aqui que as artes chegam na produção."""
    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")
    if row["sale_type"] == "PREMIUM_SUB":
        raise HTTPException(400, "Pedido de premium não tem arquivos.")
    base = f"{row['order_number']}__{safe_name(row['customer_name'])}"
    files: list[Path] = []
    # A pasta de produção (quando pago) repete os arquivos e soma o LEIA-ME.
    for folder in (row["folder_path"], row["production_drop_path"]):
        cloud_get_dir(folder)  # no servidor, traz do Storage o que não está no disco
        if folder and Path(folder).is_dir():
            files += [p for p in Path(folder).iterdir() if p.is_file() and not p.name.endswith(".baixando")]
    report = SOMENTE_DTF_DIR / base / "RELATORIO_ARTES.txt"
    if cloud_get(report):
        files.append(report)
    if not files:
        raise HTTPException(404, "Os arquivos deste pedido não estão mais no servidor.")
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    names: set[str] = set()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as z:  # PNG/JPG já vêm comprimidos
        for p in files:
            if p.name not in names:
                names.add(p.name)
                z.write(p, p.name)
    return FileResponse(
        tmp.name, media_type="application/zip", filename=f"{base}.zip",
        background=BackgroundTask(os.unlink, tmp.name)  # apaga o zip depois de enviar
    )


@app.get("/api/admin/orders/{order_id}/file-links", dependencies=[Depends(require_staff)])
def order_file_links(order_id: str) -> dict[str, Any]:
    """Links assinados (valem 1 hora) pra cada arquivo de produção do pedido,
    direto do Supabase Storage. É o que o sync_producao.py usa no PC da
    produção: baixa 500 MB sem passar pelo servidor do site."""
    if not STORE:
        raise HTTPException(400, "Sem Supabase Storage: use /api/admin/orders/{id}/files.")
    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")
    if row["sale_type"] == "PREMIUM_SUB":
        raise HTTPException(400, "Pedido de premium não tem arquivos.")
    report = SOMENTE_DTF_DIR / f"{row['order_number']}__{safe_name(row['customer_name'])}" / "RELATORIO_ARTES.txt"
    try:
        found = [f for f in STORE.list_files(Path(row["folder_path"])) if is_production_file(f["name"])]
        if row["production_drop_path"]:
            found += STORE.list_files(Path(row["production_drop_path"]))
        found += [f for f in STORE.list_files(report.parent) if f["name"] == report.name]
        links = [{"name": f["name"], "size": f["size"], "url": STORE.signed_download_url(f["key"])} for f in found]
    except cloud_storage.StorageError as exc:
        print(f"[storage] {exc}")
        raise HTTPException(502, "Não foi possível falar com o Supabase Storage.")
    if not links:
        raise HTTPException(404, "Os arquivos deste pedido não estão no Storage.")
    return {"folder_name": production_folder_name(row), "files": links}


@app.post("/api/payments/webhook")
def payment_webhook(
    payload: dict[str, Any],
    x_webhook_secret: str | None = Header(None)
) -> dict[str, Any]:
    expected = str(CONFIG.get("payment", {}).get("webhook_secret", ""))
    if not expected or expected == "TROQUE-ESTA-CHAVE":
        raise HTTPException(
            500,
            "payment.webhook_secret ainda não foi trocado em config.json. Configure antes de aceitar pagamentos."
        )
    if not x_webhook_secret or not secrets.compare_digest(x_webhook_secret, expected):
        raise HTTPException(401, "Webhook não autorizado.")

    order_id = str(payload.get("order_id", "")).strip()
    payment_status = str(payload.get("payment_status", "")).strip().upper()

    if payment_status != "PAID":
        return {"ok": True, "ignored": True}

    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")

    path = mark_order_paid(order_id)
    return {"ok": True, "production_drop_path": path}


@app.post("/api/me/orders/{order_id}/checkout")
def checkout_order(order_id: str, customer: dict[str, Any] = Depends(require_customer)) -> dict[str, Any]:
    """Link de pagamento do Mercado Pago pro pedido do cliente. Vale também
    pra quem desistiu e volta depois pelo "Pagar agora" em Minha conta."""
    if not mp_token():
        raise HTTPException(503, "O pagamento online ainda não está configurado. Fale com a loja.")
    cancel_stale_orders()
    row = load_order(order_id)
    if not row or not order_is_customers(row, customer):
        raise HTTPException(404, "Pedido não encontrado.")
    if row["payment_status"] == "PAID":
        raise HTTPException(400, "Este pedido já está pago.")
    if row["status"] == "CANCELADO":
        raise HTTPException(400, "Este pedido foi cancelado por falta de pagamento em 24 horas. Faça um pedido novo.")
    # O link vence junto com o pedido: ninguém paga um pedido já cancelado.
    expires_at = time.strftime("%Y-%m-%dT%H:%M:%S.000-00:00",
                               time.gmtime(float(row["created_at"]) + unpaid_limit_seconds()))
    preference: dict[str, Any] = {
        "items": [{
            "id": row["order_number"],
            "title": f"{CONFIG.get('store_name') or 'Ripfire Studio'} - pedido {row['order_number']}",
            "quantity": 1,
            "unit_price": round(float(row["total"]), 2),
            "currency_id": "BRL",
        }],
        "external_reference": row["id"],  # é por ele que o pagamento acha o pedido
        "expires": True,
        "expiration_date_to": expires_at,
    }
    base = site_url()
    if base:
        back = f"{base}/?mp=retorno"
        preference |= {
            "back_urls": {"success": back, "pending": back, "failure": back},
            "auto_return": "approved",
            "notification_url": f"{base}/api/payments/mercadopago",
        }
    res = mp_request("POST", "/checkout/preferences", preference)
    # Credencial de teste (TEST-...) paga no ambiente de testes do Mercado Pago.
    url = (res.get("sandbox_init_point") if mp_token().startswith("TEST-") else None) or res.get("init_point")
    if not url:
        raise HTTPException(502, "O Mercado Pago não devolveu o link de pagamento.")
    return {"url": url}


@app.post("/api/me/orders/{order_id}/sync-payment")
def sync_order_payment(order_id: str, customer: dict[str, Any] = Depends(require_customer)) -> dict[str, Any]:
    """Confere no Mercado Pago se o pedido foi pago: na volta do pagamento e
    no "Já paguei, verificar" (no PC o Mercado Pago não consegue avisar o site)."""
    check_rate_limit(f"sync:{customer['id']}", 20, 60, "Aguarde um pouco antes de verificar de novo.")
    row = load_order(order_id)
    if not row or not order_is_customers(row, customer):
        raise HTTPException(404, "Pedido não encontrado.")
    if row["payment_status"] != "PAID" and mp_token():
        query = urllib.parse.urlencode({"external_reference": order_id, "sort": "date_created", "criteria": "desc"})
        for payment in mp_request("GET", f"/v1/payments/search?{query}").get("results") or []:
            if apply_mp_payment(payment):
                break
        row = load_order(order_id) or row
    # Total e número vão junto: o site registra a compra no GA/Pixel (se o cliente permitiu).
    return {"payment_status": row["payment_status"], "status": row["status"],
            "order_number": row["order_number"], "total": row["total"]}


@app.post("/api/payments/mercadopago")
def mercadopago_webhook(request: Request, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aviso de pagamento do Mercado Pago (notification_url da preferência).
    A rota é pública, então do aviso só sai o id: o pagamento é buscado na
    API com o token da loja antes de liberar qualquer coisa. Responde 200 pra
    tudo que não interessa; erro só quando vale o Mercado Pago tentar de novo."""
    q, body = request.query_params, payload or {}
    data = body.get("data")
    data_id = data.get("id") if isinstance(data, dict) else None
    kind = q.get("type") or q.get("topic") or body.get("type") or body.get("topic")
    payment_id = str(q.get("data.id") or q.get("id") or data_id or "")
    if kind != "payment" or not payment_id.isdigit() or not mp_token():
        return {"ok": True, "ignored": True}
    # missing_ok: o "simular notificação" do painel manda um id que não existe.
    payment = mp_request("GET", f"/v1/payments/{payment_id}", missing_ok=True)
    return {"ok": True, "paid": apply_mp_payment(payment)}


def plaintext_admin_users() -> list[str]:
    logins = [(CONFIG.get("admin_user"), CONFIG.get("admin_key"))]
    logins += [(a.get("user"), a.get("key")) for a in CONFIG.get("admins") or []]
    return [str(u) for u, k in logins if u and k and not str(k).startswith((PWD_SCHEME + "$", "TROQUE-"))]


# ---------- Rotas fechadas por padrão ----------
# Toda rota exige login (Admin ou cliente), menos as desta lista. Rota nova que
# esqueceu o Depends(require_admin/require_customer) e não está aqui faz o
# servidor recusar a subir, dizendo qual é: nada fica público sem querer.
PUBLIC_ROUTES = {
    # Páginas do site
    ("GET", "/"), ("GET", "/favicon.ico"), ("GET", "/privacidade"), ("GET", "/termos"),
    ("GET", "/reembolso"), ("GET", "/lgpd"),
    # Vitrine (o que qualquer visitante vê)
    ("GET", "/api/config"), ("GET", "/api/artworks"), ("GET", "/api/shirt-mockups"), ("GET", "/api/library"),
    ("GET", "/api/library/categories/{category_id}/arts"), ("GET", "/api/library/arts/{art_id}/thumb"),
    ("GET", "/media/{file_path:path}"),
    # Entrar, criar conta e sair (com limite de tentativas)
    ("POST", "/api/auth/login"), ("POST", "/api/auth/register"), ("POST", "/api/admin/login"), ("POST", "/api/auth/google"), ("POST", "/api/auth/logout"),
    # Pagamentos: conferidos pelo segredo do webhook ou consultando a API do Mercado Pago
    ("POST", "/api/payments/webhook"), ("POST", "/api/payments/mercadopago"),
    # LGPD (vale também pra quem não tem conta) e contagem anônima de visitas
    ("POST", "/api/lgpd/requests"), ("POST", "/api/metrics/hit"),
}
AUTH_DEPENDENCIES = {"require_admin", "require_staff", "require_customer"}


def unprotected_routes() -> list[str]:
    from fastapi.routing import APIRoute

    def names(dep: Any) -> set[str]:
        out: set[str] = set()
        for sub in dep.dependencies:
            out.add(getattr(sub.call, "__name__", ""))
            out |= names(sub)
        return out

    found = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or names(route.dependant) & AUTH_DEPENDENCIES:
            continue
        for method in route.methods:
            if (method, route.path) not in PUBLIC_ROUTES:
                found.append(f"{method} {route.path}")
    return found


if unprotected_routes():
    raise RuntimeError("Rota sem login e fora de PUBLIC_ROUTES: " + ", ".join(unprotected_routes())
                       + ". Adicione Depends(require_admin/require_customer) ou, se for pública mesmo, inclua em PUBLIC_ROUTES.")


if __name__ == "__main__" and "--hash-senha" in sys.argv:
    # python app.py --hash-senha → pede a senha (sem mostrar na tela) e imprime
    # o hash pra colar no "admin_key" (ou "key" de "admins") do config.json.
    import getpass
    first = getpass.getpass("Nova senha do admin: ")
    if len(first) < 12:
        sys.exit("Use pelo menos 12 caracteres na senha do admin.")
    if getpass.getpass("Repita a senha: ") != first:
        sys.exit("As senhas não conferem.")
    print("\nCole no config.json (entre aspas):\n" + hash_password(first))
    sys.exit(0)

if __name__ == "__main__" and "--restaurar" in sys.argv:
    args = sys.argv[sys.argv.index("--restaurar") + 1:]
    if not args:
        print("Uso: python app.py --restaurar <arquivo .db>\nBackups disponíveis (do mais novo):")
        for b in list_backups()[:15]:
            print("  " + str(b))
        sys.exit(0)
    restore_database(Path(args[0]))
    sys.exit(0)

if __name__ == "__main__":
    import uvicorn
    if BACKUP_CFG.get("enabled", True) and not IS_PG:
        threading.Thread(target=backup_loop, daemon=True).start()
    if plaintext_admin_users():
        print("[aviso] Senha de admin em texto no config.json (" + ", ".join(plaintext_admin_users()) + ")."
              " Gere o hash com: python app.py --hash-senha")
    # No PC fica em 127.0.0.1:8787. No servidor também: quem recebe o público
    # é o nginx, que repassa pra cá (HOST e PORT mudam pelo .env se precisar).
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8787")))

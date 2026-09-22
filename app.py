from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
DB_PATH = BASE / "data" / "studio.db"
ARTWORKS_DIR = BASE / "media" / "artworks"
GENERATED_DIR = BASE / "media" / "generated"
MOCKUPS_DIR = BASE / "media" / "mockups"
ORDERS_DIR = BASE / "orders"
SOMENTE_DTF_DIR = BASE / "SomenteDTFs"

PRODUCTION_DROP = Path(CONFIG.get("production_drop_folder", "./PRODUCAO_PAGA"))
if not PRODUCTION_DROP.is_absolute():
    PRODUCTION_DROP = (BASE / PRODUCTION_DROP).resolve()

for p in (DB_PATH.parent, ARTWORKS_DIR, GENERATED_DIR, MOCKUPS_DIR, ORDERS_DIR, SOMENTE_DTF_DIR, PRODUCTION_DROP):
    p.mkdir(parents=True, exist_ok=True)


def db():
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
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

        cols = {r["name"] for r in con.execute("PRAGMA table_info(orders)").fetchall()}
        if "payment_status" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'PENDING'")
        if "production_drop_path" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN production_drop_path TEXT")

        mockup_cols = {r["name"] for r in con.execute("PRAGMA table_info(shirt_mockups)").fetchall()}
        if "side" not in mockup_cols:
            con.execute("ALTER TABLE shirt_mockups ADD COLUMN side TEXT NOT NULL DEFAULT 'FRONT'")


SEED_DIR = BASE / "media" / "mockups" / "seed"
# Fotos reais enviadas pelo lojista (frente/costas) — cadastradas
# automaticamente na cor "Preto" se ainda não existir um mockup pra
# aquela combinação modelo+cor+lado. Rodar de novo não duplica nem
# sobrescreve um mockup que o Admin já tenha customizado via upload.
# Área de impressão medida direto no contorno real de cada foto (canal
# alfa, varredura coluna a coluna): encontra onde a manga "termina" e o
# painel do tronco começa, o ponto mais baixo do ombro/gola dentro
# desse painel e a barra — daí aplica ~1,5cm de margem (em px, usando a
# largura real estimada de cada modelo) pras 4 bordas. Fica só dentro
# do tronco, nunca em cima de manga/ombro. Confirmado visualmente contra
# as fotos de referência que o lojista mandou.
PRINT_AREA_BY_MODEL_SIDE = {
    ("regular", "FRONT"): (23.0, 18.8, 53.8, 68.8),
    ("regular", "BACK"): (23.0, 15.7, 53.8, 74.7),
    ("oversized", "FRONT"): (22.8, 17.3, 54.3, 70.3),
    ("oversized", "BACK"): (22.8, 17.8, 54.3, 68.4),
    ("babylook", "FRONT"): (23.3, 16.9, 53.3, 67.4),
    ("babylook", "BACK"): (26.7, 18.5, 50.0, 64.9),
}


def _print_area(model_id: str, side: str) -> dict:
    px, py, pw, ph = PRINT_AREA_BY_MODEL_SIDE[(model_id, side)]
    return {"print_x": px, "print_y": py, "print_w": pw, "print_h": ph}


SEED_MOCKUPS = [
    {"model_id": "regular", "color": "Preto", "side": "FRONT", "file": "regular_frente.png",
     **_print_area("regular", "FRONT")},
    {"model_id": "regular", "color": "Preto", "side": "BACK", "file": "regular_costas.png",
     **_print_area("regular", "BACK")},
    {"model_id": "babylook", "color": "Preto", "side": "FRONT", "file": "babylook_frente.png",
     **_print_area("babylook", "FRONT")},
    {"model_id": "babylook", "color": "Preto", "side": "BACK", "file": "babylook_costas.png",
     **_print_area("babylook", "BACK")},
    {"model_id": "oversized", "color": "Preto", "side": "FRONT", "file": "oversized_frente.png",
     **_print_area("oversized", "FRONT")},
    {"model_id": "oversized", "color": "Preto", "side": "BACK", "file": "oversized_costas.png",
     **_print_area("oversized", "BACK")},

    # Fotos reais das outras cores (recortadas de fotos frente+costas
    # lado a lado) — usam a mesma área de impressão do preto pra cada
    # modelo+lado, assumindo o mesmo enquadramento/tamanho de foto.
    {"model_id": "regular", "color": "Branco", "side": "FRONT", "file": "regular_branco_frente.png",
     **_print_area("regular", "FRONT")},
    {"model_id": "regular", "color": "Branco", "side": "BACK", "file": "regular_branco_costas.png",
     **_print_area("regular", "BACK")},
    {"model_id": "regular", "color": "Marrom", "side": "FRONT", "file": "regular_marrom_frente.png",
     **_print_area("regular", "FRONT")},
    {"model_id": "regular", "color": "Marrom", "side": "BACK", "file": "regular_marrom_costas.png",
     **_print_area("regular", "BACK")},
    {"model_id": "regular", "color": "Off White", "side": "FRONT", "file": "regular_offwhite_frente.png",
     **_print_area("regular", "FRONT")},
    {"model_id": "regular", "color": "Off White", "side": "BACK", "file": "regular_offwhite_costas.png",
     **_print_area("regular", "BACK")},

    {"model_id": "babylook", "color": "Branco", "side": "FRONT", "file": "babylook_branco_frente.png",
     **_print_area("babylook", "FRONT")},
    {"model_id": "babylook", "color": "Branco", "side": "BACK", "file": "babylook_branco_costas.png",
     **_print_area("babylook", "BACK")},
    {"model_id": "babylook", "color": "Marrom", "side": "FRONT", "file": "babylook_marrom_frente.png",
     **_print_area("babylook", "FRONT")},
    {"model_id": "babylook", "color": "Marrom", "side": "BACK", "file": "babylook_marrom_costas.png",
     **_print_area("babylook", "BACK")},
    {"model_id": "babylook", "color": "Off White", "side": "FRONT", "file": "babylook_offwhite_frente.png",
     **_print_area("babylook", "FRONT")},
    {"model_id": "babylook", "color": "Off White", "side": "BACK", "file": "babylook_offwhite_costas.png",
     **_print_area("babylook", "BACK")},

    {"model_id": "oversized", "color": "Branco", "side": "FRONT", "file": "oversized_branco_frente.png",
     **_print_area("oversized", "FRONT")},
    {"model_id": "oversized", "color": "Branco", "side": "BACK", "file": "oversized_branco_costas.png",
     **_print_area("oversized", "BACK")},
    {"model_id": "oversized", "color": "Marrom", "side": "FRONT", "file": "oversized_marrom_frente.png",
     **_print_area("oversized", "FRONT")},
    {"model_id": "oversized", "color": "Marrom", "side": "BACK", "file": "oversized_marrom_costas.png",
     **_print_area("oversized", "BACK")},
    {"model_id": "oversized", "color": "Off White", "side": "FRONT", "file": "oversized_offwhite_frente.png",
     **_print_area("oversized", "FRONT")},
    {"model_id": "oversized", "color": "Off White", "side": "BACK", "file": "oversized_offwhite_costas.png",
     **_print_area("oversized", "BACK")},
]


def seed_shirt_mockups():
    if not SEED_DIR.exists():
        return
    with db() as con:
        for m in SEED_MOCKUPS:
            src = SEED_DIR / m["file"]
            if not src.exists():
                continue
            existing = con.execute(
                "SELECT id FROM shirt_mockups WHERE model_id=? AND color=? AND side=?",
                (m["model_id"], m["color"], m["side"])
            ).fetchone()
            if existing:
                continue
            mid = uuid.uuid4().hex[:12]
            dest = MOCKUPS_DIR / f"seed_{m['model_id']}_{safe_name(m['color']).lower()}_{m['side'].lower()}{src.suffix}"
            if not dest.exists():
                shutil.copy2(src, dest)
            con.execute(
                """INSERT INTO shirt_mockups
                   (id,created_at,model_id,color,side,file_path,preview_url,print_x,print_y,print_w,print_h)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (mid, time.time(), m["model_id"], m["color"], m["side"], str(dest),
                 f"/media/mockups/{dest.name}", m["print_x"], m["print_y"], m["print_w"], m["print_h"])
            )


def safe_name(value: str) -> str:
    out = []
    for ch in value.strip():
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch.isspace():
            out.append("_")
    return "".join(out)[:80] or "arquivo"


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        salt, _digest = stored.split("$", 1)
    except ValueError:
        return False
    return hash_password(password, salt) == stored


def public_customer(row: dict) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "full_name": row["full_name"],
        "phone": row["phone"],
        "cep": row["cep"],
        "address_number": row["address_number"],
        "document": row["document"],
    }


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


def require_admin(
    x_admin_key: str | None = Header(None),
    x_admin_user: str | None = Header(None),
):
    """Protege rotas de admin/pagamento. Sem isso, qualquer visitante do
    site (uma vez publicado num domínio) conseguiria ver todos os
    pedidos com dados de clientes, cadastrar/apagar artes do catálogo,
    ou pior: liberar qualquer pedido como pago sem pagar de verdade.

    Usuário e senha são validados só aqui, no servidor — nenhum dos dois
    é enviado pro navegador em nenhuma rota pública (antes o usuário
    ficava hardcoded no app.js e a senha vazava inteira em /api/config,
    então qualquer visitante conseguia ler as credenciais do Admin)."""
    expected_key = str(CONFIG.get("admin_key", ""))
    expected_user = str(CONFIG.get("admin_user", ""))
    if not expected_key or expected_key == "TROQUE-ESTA-CHAVE-ADMIN":
        raise HTTPException(
            500,
            "admin_key ainda não foi trocada em config.json. Configure antes de usar o Admin."
        )
    if not x_admin_user or not secrets.compare_digest(x_admin_user, expected_user):
        raise HTTPException(401, "Usuário ou senha de admin inválidos.")
    if not x_admin_key or not secrets.compare_digest(x_admin_key, expected_key):
        raise HTTPException(401, "Usuário ou senha de admin inválidos.")


_AI_RATE_LIMIT: dict[str, list[float]] = {}


def check_ai_rate_limit(client_ip: str):
    """Limite simples (em memória, por IP) pra reduzir o risco de alguém
    gerar centenas de imagens por IA de graça e estourar sua conta da
    OpenAI. Não substitui um rate limit de verdade (ex.: Redis) se o
    tráfego crescer, mas cobre o caso de abuso básico."""
    now = time.time()
    window = _AI_RATE_LIMIT.setdefault(client_ip, [])
    window[:] = [t for t in window if now - t < 60]
    if len(window) >= 6:
        raise HTTPException(429, "Muitas gerações por IA em pouco tempo. Aguarde um minuto.")
    window.append(now)


def next_order_number() -> str:
    stamp = time.strftime("%y%m%d")
    with db() as con:
        count = con.execute(
            "SELECT COUNT(*) AS n FROM orders WHERE order_number LIKE ?",
            (f"{stamp}-%",)
        ).fetchone()["n"] + 1
    return f"{stamp}-{count:04d}"


def image_info(path: Path):
    result = {"width_px": None, "height_px": None, "has_alpha": None}
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


def write_dtf_report(order_number: str, customer_name: str, sources: list[dict]) -> Path:
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
    return report_path


def load_order(order_id: str):
    with db() as con:
        row = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    return dict(row) if row else None


def release_paid_order_to_production(order_id: str):
    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")
    if row["payment_status"] != "PAID":
        raise HTTPException(400, "Pedido ainda não está pago.")

    source_folder = Path(row["folder_path"])
    if not source_folder.exists():
        raise HTTPException(500, "Pasta original do pedido não encontrada.")

    data = json.loads(row["data_json"])
    drop_folder = PRODUCTION_DROP / (
        f"{row['order_number']}__{safe_name(row['customer_name'])}__{safe_name(row['sale_type'])}"
    )

    if drop_folder.exists():
        with db() as con:
            con.execute(
                "UPDATE orders SET status='LIBERADO_PRODUCAO', production_drop_path=? WHERE id=?",
                (str(drop_folder), order_id)
            )
        return str(drop_folder)

    drop_folder.mkdir(parents=True, exist_ok=False)

    try:
        for p in source_folder.iterdir():
            if not p.is_file():
                continue
            if (
                p.name == "pedido.json"
                or p.name.startswith("ARTE_PRODUCAO")
                or p.name.startswith("ARTE_DIGITAL")
            ):
                shutil.copy2(p, drop_folder / p.name)

        resumo = {
            "pedido": row["order_number"],
            "cliente": row["customer_name"],
            "tipo": row["sale_type"],
            "valor": row["total"],
            "pagamento": "PAID",
            "liberado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
            "dados": data
        }
        (drop_folder / "LEIA-ME-PRODUCAO.json").write_text(
            json.dumps(resumo, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        with db() as con:
            con.execute(
                "UPDATE orders SET status='LIBERADO_PRODUCAO', production_drop_path=? WHERE id=?",
                (str(drop_folder), order_id)
            )

        return str(drop_folder)
    except Exception:
        shutil.rmtree(drop_folder, ignore_errors=True)
        raise


init_db()
# Desativado a pedido do usuário: o editor deve usar só o esquema 3D (SVG)
# em vez das fotos reais de mockup. Reative chamando seed_shirt_mockups()
# se quiser voltar a usar fotos.
# seed_shirt_mockups()

app = FastAPI(title="Ripfire Studio Local", version="2.0.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/media", StaticFiles(directory=BASE / "media"), name="media")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/config")
def get_config():
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
        "pricing": CONFIG.get("pricing"),
        "dtf_sheet_width_cm": CONFIG.get("dtf_sheet_width_cm"),
        "dtf_sheet_min_height_cm": CONFIG.get("dtf_sheet_min_height_cm"),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
        "ai_model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2"),
        "google_client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
    }


@app.post("/api/auth/register")
def register_customer(payload: CustomerRegister):
    full_name = payload.full_name.strip()
    email = payload.email.strip().lower()
    phone = only_digits(payload.phone)
    cep = only_digits(payload.cep)
    address_number = payload.address_number.strip()
    document = only_digits(payload.document)
    password = payload.password

    if not full_name:
        raise HTTPException(400, "Informe o nome completo.")
    if "@" not in email or len(email) < 5:
        raise HTTPException(400, "E-mail inválido.")
    if len(password) < 6:
        raise HTTPException(400, "A senha precisa ter pelo menos 6 caracteres.")
    if phone and len(phone) < 8:
        raise HTTPException(400, "Telefone inválido.")
    if cep and len(cep) != 8:
        raise HTTPException(400, "CEP inválido, use o formato 00000-000.")
    if document and len(document) not in (11, 14):
        raise HTTPException(400, "CPF/CNPJ inválido.")

    with db() as con:
        existing = con.execute("SELECT id FROM customers WHERE email=?", (email,)).fetchone()
        if existing:
            raise HTTPException(409, "Já existe uma conta com esse e-mail.")
        cid = uuid.uuid4().hex[:12]
        con.execute(
            """INSERT INTO customers
               (id,created_at,email,password_hash,full_name,phone,cep,address_number,document)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (cid, time.time(), email, hash_password(password), full_name, phone, cep, address_number, document)
        )
    return public_customer({
        "id": cid, "email": email, "full_name": full_name, "phone": phone,
        "cep": cep, "address_number": address_number, "document": document,
    })


@app.post("/api/auth/login")
def login_customer(payload: CustomerLogin):
    email = payload.email.strip().lower()
    with db() as con:
        row = con.execute("SELECT * FROM customers WHERE email=?", (email,)).fetchone()
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(401, "E-mail ou senha incorretos.")
    return public_customer(dict(row))


@app.post("/api/auth/google")
def login_with_google(payload: GoogleAuth):
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(503, "Login com Google não configurado (GOOGLE_CLIENT_ID ausente no .env).")

    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    try:
        claims = google_id_token.verify_oauth2_token(
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
                   (id,created_at,email,password_hash,full_name,phone,cep,address_number,document,google_sub)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (cid, time.time(), email, None, full_name, "", "", "", "", sub)
            )
            row = con.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()

    return public_customer(dict(row))


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
):
    ext = Path(artwork.filename or "").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "Envie PNG, JPG, JPEG ou WEBP.")

    aid = uuid.uuid4().hex[:12]
    filename = f"{aid}_{safe_name(Path(artwork.filename).stem)}{ext}"
    dest = ARTWORKS_DIR / filename

    with dest.open("wb") as f:
        while chunk := await artwork.read(1024 * 1024):
            f.write(chunk)

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
):
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
        con.execute("DELETE FROM shirt_mockups WHERE id=?", (mockup_id,))
    return {"ok": True}


@app.patch("/api/admin/shirt-mockups/{mockup_id}/area", dependencies=[Depends(require_admin)])
def update_shirt_mockup_area(mockup_id: str, payload: dict):
    fields = {}
    for key in ("print_x", "print_y", "print_w", "print_h"):
        if key not in payload:
            raise HTTPException(400, f"Falta o campo {key}.")
        value = float(payload[key])
        if value < 0 or value > 100:
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


@app.post("/api/ai/generate")
def ai_generate(payload: dict, request: Request):
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(400, "Descreva a arte.")
    if len(prompt) > 500:
        raise HTTPException(400, "Descrição muito longa (máximo 500 caracteres).")

    check_ai_rate_limit(request.client.host if request.client else "unknown")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(503, "IA não configurada no .env.")

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

        item = response.data[0]
        image_bytes = None
        if getattr(item, "b64_json", None):
            image_bytes = base64.b64decode(item.b64_json)

        if not image_bytes:
            raise RuntimeError("A API não retornou a imagem.")

        gid = uuid.uuid4().hex[:12]
        dest = GENERATED_DIR / f"{gid}.png"
        dest.write_bytes(image_bytes)

        return {"ok": True, "url": f"/media/generated/{gid}.png", "model": model}
    except Exception as exc:
        raise HTTPException(500, f"Falha ao gerar imagem: {exc}")


@app.post("/api/orders")
async def create_order(
    order_json: str = Form(...),
    design: UploadFile | None = File(None),
    design_back: UploadFile | None = File(None),
):
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

    order_number = next_order_number()
    oid = uuid.uuid4().hex[:12]
    folder = ORDERS_DIR / f"{order_number}__{safe_name(name)}"
    folder.mkdir(parents=True, exist_ok=False)

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
        if design is not None and design.filename:
            ext = Path(design.filename).suffix.lower() or ".png"
            design_path = folder / f"ARTE_PRODUCAO{ext}"
            with design_path.open("wb") as f:
                while chunk := await design.read(1024 * 1024):
                    f.write(chunk)

            data["production_art"] = {
                "file": design_path.name,
                **image_info(design_path),
            }

        if design_back is not None and design_back.filename:
            ext_b = Path(design_back.filename).suffix.lower() or ".png"
            design_back_path = folder / f"ARTE_PRODUCAO_COSTAS{ext_b}"
            with design_back_path.open("wb") as f:
                while chunk := await design_back.read(1024 * 1024):
                    f.write(chunk)

            data["production_art_back"] = {
                "file": design_back_path.name,
                **image_info(design_back_path),
            }

        if digital_artwork_row:
            # A venda de arte digital não tinha o arquivo comprado
            # copiado pro pedido — o cliente pagava e não recebia nada.
            src = Path(digital_artwork_row["file_path"])
            ext = src.suffix.lower() or ".png"
            digital_dest = folder / f"ARTE_DIGITAL{ext}"
            if src.exists():
                shutil.copy2(src, digital_dest)
                data["digital_art_file"] = digital_dest.name
            else:
                data["digital_art_file"] = None
                data.setdefault("warnings", []).append(
                    "Arquivo original da arte digital não encontrado em disco."
                )

        data["order_number"] = order_number
        data["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data["status"] = "AGUARDANDO_PAGAMENTO"
        data["payment_status"] = "PENDING"

        (folder / "pedido.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        total = float(data.get("total", 0))
        try:
            with db() as con:
                con.execute(
                    """INSERT INTO orders
                       (id,order_number,created_at,customer_name,customer_email,
                        customer_whatsapp,sale_type,status,payment_status,total,
                        folder_path,production_drop_path,data_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        oid, order_number, time.time(), name,
                        customer.get("email", ""), customer.get("whatsapp", ""),
                        sale_type, "AGUARDANDO_PAGAMENTO", "PENDING", total,
                        str(folder), None, json.dumps(data, ensure_ascii=False)
                    )
                )
        except sqlite3.IntegrityError:
            # Dois pedidos criados no mesmíssimo segundo podem colidir
            # no número (order_number é gerado por contagem, não é
            # atômico). Antes isso derrubava o pedido com um erro cru;
            # agora tenta de novo com um sufixo, sem perder a arte já
            # salva em disco.
            order_number = f"{order_number}-{uuid.uuid4().hex[:4]}"
            data["order_number"] = order_number
            (folder / "pedido.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with db() as con:
                con.execute(
                    """INSERT INTO orders
                       (id,order_number,created_at,customer_name,customer_email,
                        customer_whatsapp,sale_type,status,payment_status,total,
                        folder_path,production_drop_path,data_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        oid, order_number, time.time(), name,
                        customer.get("email", ""), customer.get("whatsapp", ""),
                        sale_type, "AGUARDANDO_PAGAMENTO", "PENDING", total,
                        str(folder), None, json.dumps(data, ensure_ascii=False)
                    )
                )

        if sale_type == "DTF_ONLY":
            write_dtf_report(order_number, name, data.get("sources") or [])

        return {
            "ok": True,
            "id": oid,
            "order_number": order_number,
            "status": "AGUARDANDO_PAGAMENTO"
        }
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


@app.get("/api/orders", dependencies=[Depends(require_admin)])
def list_orders():
    with db() as con:
        rows = con.execute(
            """SELECT id,order_number,created_at,customer_name,sale_type,status,
                      payment_status,total,folder_path,production_drop_path,data_json
               FROM orders ORDER BY created_at DESC"""
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        try:
            d["data"] = json.loads(d.pop("data_json"))
        except Exception:
            d["data"] = {}
        result.append(d)
    return result


@app.post("/api/orders/{order_id}/mark-paid", dependencies=[Depends(require_admin)])
def mark_paid(order_id: str):
    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")

    if row["payment_status"] != "PAID":
        with db() as con:
            con.execute(
                """UPDATE orders SET payment_status='PAID',
                   status='PAGAMENTO_APROVADO' WHERE id=?""",
                (order_id,)
            )

    path = release_paid_order_to_production(order_id)

    return {
        "ok": True,
        "status": "LIBERADO_PRODUCAO",
        "payment_status": "PAID",
        "production_drop_path": path
    }


@app.post("/api/payments/webhook")
def payment_webhook(
    payload: dict,
    x_webhook_secret: str | None = Header(None)
):
    expected = str(CONFIG.get("payment", {}).get("webhook_secret", ""))
    if not expected or x_webhook_secret != expected:
        raise HTTPException(401, "Webhook não autorizado.")

    order_id = str(payload.get("order_id", "")).strip()
    payment_status = str(payload.get("payment_status", "")).strip().upper()

    if payment_status != "PAID":
        return {"ok": True, "ignored": True}

    row = load_order(order_id)
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")

    with db() as con:
        con.execute(
            """UPDATE orders SET payment_status='PAID',
               status='PAGAMENTO_APROVADO' WHERE id=?""",
            (order_id,)
        )

    path = release_paid_order_to_production(order_id)
    return {"ok": True, "production_drop_path": path}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)

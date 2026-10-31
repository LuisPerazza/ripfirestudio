"""Banco de dados do Ripfire: SQLite no PC ou Postgres (Supabase) no servidor.

Com DATABASE_URL definido (no Render: a connection string do Supabase), o app
usa Postgres; sem ela, continua no SQLite de sempre (data/studio.db). O resto
do app.py não precisa saber qual é: `with db() as con:` devolve uma conexão
com a mesma cara nos dois casos (`con.execute(sql, params)` com `?` nos
parâmetros, linhas que aceitam row["coluna"], row[0] e dict(row)).
"""
from __future__ import annotations

import atexit
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

_SQLITE_PATH: Path | None = None
_POOL: Any = None

if IS_PG:
    import psycopg
    from psycopg_pool import ConnectionPool

    IntegrityError: tuple[type[Exception], ...] = (sqlite3.IntegrityError, psycopg.errors.IntegrityError)
else:
    IntegrityError = (sqlite3.IntegrityError,)


class Row(dict):
    """Linha do Postgres que se comporta como sqlite3.Row: row["nome"], row[0] e dict(row)."""

    __slots__ = ("_values",)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return dict.__getitem__(self, key)


def _row_factory(cursor: Any):
    names = [d.name for d in cursor.description] if cursor.description else []

    def make(values: Any) -> Row:
        row = Row(zip(names, values))
        row._values = tuple(values)
        return row

    return make


@lru_cache(maxsize=512)
def _to_pg(sql: str) -> str:
    """Placeholders do SQLite (?) viram os do psycopg (%s). Um % literal no
    SQL vira %% pra não ser lido como placeholder."""
    return sql.replace("%", "%%").replace("?", "%s")


class PgConnection:
    """Mesma interface que o app usa do sqlite3.Connection."""

    def __init__(self, conn: Any):
        self._conn = conn

    def execute(self, sql: str, params: Any = ()) -> Any:
        return self._conn.execute(_to_pg(sql), tuple(params or ()))

    def executemany(self, sql: str, seq: Any) -> Any:
        cur = self._conn.cursor()
        rows = [tuple(p) for p in seq]
        if rows:
            cur.executemany(_to_pg(sql), rows)
        return cur


def configure_sqlite(path: Path):
    global _SQLITE_PATH
    _SQLITE_PATH = path


def _pool() -> Any:
    global _POOL
    if _POOL is None:
        # prepare_threshold=None: o pooler do Supabase (Supavisor) no modo
        # "transaction" não aceita prepared statements; assim funciona nos dois modos.
        _POOL = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX", "5")),
            kwargs={"prepare_threshold": None, "row_factory": _row_factory},
            check=ConnectionPool.check_connection,  # descarta conexão que o pooler fechou
            open=True,
            name="ripfire",
        )
        atexit.register(_POOL.close)  # fecha as conexões ao desligar o servidor
    return _POOL


@contextmanager
def db() -> Iterator[Any]:
    """Uso: `with db() as con:`. Grava (commit) se der certo, desfaz se der
    erro e sempre devolve/fecha a conexão."""
    if IS_PG:
        with _pool().connection() as conn:  # commit no fim; rollback se der erro
            yield PgConnection(conn)
        return
    assert _SQLITE_PATH is not None, "configure_sqlite() não foi chamado"
    con = sqlite3.connect(_SQLITE_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


# Tabelas no Postgres. Mesmas colunas do SQLite (com as que vieram depois em
# migrações já incluídas). DOUBLE PRECISION no lugar de REAL: o REAL do
# Postgres tem só 6 dígitos e estragaria os horários (time.time()).
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS artworks (
    id TEXT PRIMARY KEY,
    created_at DOUBLE PRECISION NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    file_path TEXT NOT NULL,
    preview_url TEXT NOT NULL,
    digital_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    can_customize INTEGER NOT NULL DEFAULT 1,
    can_sell_digital INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    origin TEXT NOT NULL DEFAULT 'ADMIN'
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    order_number TEXT UNIQUE NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    customer_name TEXT NOT NULL,
    customer_email TEXT,
    customer_whatsapp TEXT,
    sale_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'PENDING',
    total DOUBLE PRECISION NOT NULL,
    folder_path TEXT NOT NULL,
    production_drop_path TEXT,
    data_json TEXT NOT NULL,
    customer_id TEXT,
    payment_ref TEXT
);
CREATE INDEX IF NOT EXISTS orders_customer_idx ON orders (customer_id);
CREATE INDEX IF NOT EXISTS orders_created_idx ON orders (created_at);

CREATE TABLE IF NOT EXISTS shirt_mockups (
    id TEXT PRIMARY KEY,
    created_at DOUBLE PRECISION NOT NULL,
    model_id TEXT NOT NULL,
    color TEXT NOT NULL,
    side TEXT NOT NULL DEFAULT 'FRONT',
    file_path TEXT NOT NULL,
    preview_url TEXT NOT NULL,
    print_x DOUBLE PRECISION NOT NULL DEFAULT 30,
    print_y DOUBLE PRECISION NOT NULL DEFAULT 28,
    print_w DOUBLE PRECISION NOT NULL DEFAULT 40,
    print_h DOUBLE PRECISION NOT NULL DEFAULT 45
);

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    created_at DOUBLE PRECISION NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    cep TEXT NOT NULL DEFAULT '',
    address_number TEXT NOT NULL DEFAULT '',
    document TEXT NOT NULL DEFAULT '',
    google_sub TEXT UNIQUE,
    premium_until DOUBLE PRECISION NOT NULL DEFAULT 0,
    terms_accepted_at DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS customer_sessions (
    token TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS customer_sessions_customer_idx ON customer_sessions (customer_id);

CREATE TABLE IF NOT EXISTS library_categories (
    id TEXT PRIMARY KEY,
    created_at DOUBLE PRECISION NOT NULL,
    name TEXT NOT NULL,
    parent_id TEXT,
    access TEXT NOT NULL DEFAULT 'free'
);

CREATE TABLE IF NOT EXISTS library_arts (
    id TEXT PRIMARY KEY,
    created_at DOUBLE PRECISION NOT NULL,
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
    created_at DOUBLE PRECISION NOT NULL,
    customer_id TEXT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    document TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ABERTO',
    answered_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS staff_totp (
    username TEXT PRIMARY KEY,
    secret TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    last_counter BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS staff_sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);

ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS email TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);

ALTER TABLE orders ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT '';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS stage_at DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS stage_by TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS digital_downloads (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    created_at DOUBLE PRECISION NOT NULL,
    customer_id TEXT NOT NULL,
    art_id TEXT NOT NULL,
    art_name TEXT NOT NULL
);
"""

PG_TABLES = (
    "artworks", "orders", "shirt_mockups", "customers", "customer_sessions",
    "library_categories", "library_arts", "page_hits", "privacy_requests", "digital_downloads", "settings",
    "staff_users", "staff_totp", "staff_sessions", "password_resets",
)


def init_pg_schema(con: Any):
    """Cria as tabelas no Postgres. A trava evita que dois processos subindo
    juntos (deploy novo + antigo) criem a mesma tabela ao mesmo tempo."""
    con.execute("SELECT pg_advisory_xact_lock(7431001)")
    for statement in PG_SCHEMA.split(";"):
        if statement.strip():
            con.execute(statement)
    # RLS ligado e sem nenhuma regra: a API pública do Supabase (chave anon)
    # não lê nem grava nada destas tabelas. Só o app, que entra direto no
    # Postgres como dono das tabelas, enxerga os dados (senhas, CPFs, pedidos).
    for table in PG_TABLES:
        con.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

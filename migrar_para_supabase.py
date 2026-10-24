"""Leva os dados do PC (banco SQLite + pastas de arquivos) para o Supabase.

Rode UMA vez, no PC, com o site local DESLIGADO (feche o start.bat):

    python migrar_para_supabase.py

Ele pede a connection string do banco, a URL do projeto e a chave secreta do
Supabase (ou lê DATABASE_URL, SUPABASE_URL e SUPABASE_SERVICE_KEY do
ambiente). O que faz:

1. Copia todas as tabelas de data/studio.db para o Postgres do Supabase
   (contas, pedidos, catálogo, mockups, biblioteca, LGPD, métricas).
2. Sobe para o Storage as pastas media/, data/digital_library/, orders/ e
   SomenteDTFs/. Arquivo que já está lá com o mesmo tamanho é pulado: se cair
   no meio, é só rodar de novo que ele continua.

Opções:
    --sem-pedidos   não sobe os arquivos de orders/ e SomenteDTFs/ (pedidos
                    antigos já produzidos; o banco dos pedidos vai do mesmo jeito)
    --so-arquivos   pula o banco (ele já foi copiado numa rodada anterior)
    --substituir    apaga o que já existe no banco do Supabase antes de copiar
"""
from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "data" / "studio.db"
FILE_FOLDERS = ["media/artworks", "media/mockups", "media/generated", "data/digital_library"]
ORDER_FOLDERS = ["orders", "SomenteDTFs"]
# Tabelas com dados de verdade (library_categories e page_hits o site cria sozinho).
USER_TABLES = ("customers", "orders", "artworks", "shirt_mockups", "library_arts",
               "privacy_requests", "digital_downloads")


def ask(var: str, prompt: str, secret: bool = False) -> str:
    value = os.getenv(var, "").strip()
    while not value:
        value = (getpass.getpass if secret else input)(prompt).strip()
    os.environ[var] = value
    return value


def fmt_size(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


def copy_database(replace: bool):
    import dbcompat

    if not DB_PATH.is_file():
        sys.exit(f"Banco não encontrado: {DB_PATH}")
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    with dbcompat.db() as con:
        dbcompat.init_pg_schema(con)
        used = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in USER_TABLES}
        if any(used.values()) and not replace:
            filled = ", ".join(f"{t} ({n})" for t, n in used.items() if n)
            sys.exit(f"O banco do Supabase já tem dados: {filled}.\n"
                     "Para apagar e copiar de novo do PC, rode com --substituir.")
        if replace and any(used.values()):
            if input("Isso APAGA os dados que estão no Supabase. Digite SUBSTITUIR para continuar: ").strip() != "SUBSTITUIR":
                sys.exit("Cancelado.")
        con.execute("TRUNCATE " + ", ".join(dbcompat.PG_TABLES))
        for table in dbcompat.PG_TABLES:
            local_cols = [r["name"] for r in src.execute(f"PRAGMA table_info({table})")]
            if not local_cols:
                continue  # tabela que ainda não existia nessa versão do site
            pg_cols = {r["column_name"] for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name=? AND table_schema=current_schema()",
                (table,))}
            cols = [c for c in local_cols if c in pg_cols]
            rows = [tuple(r[c] for c in cols) for r in src.execute(f"SELECT {', '.join(cols)} FROM {table}")]
            con.executemany(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
            print(f"  {table}: {len(rows)}")
        # Próximo id automático depois do maior que veio do PC.
        con.execute("SELECT setval(pg_get_serial_sequence('digital_downloads','id'), "
                    "COALESCE((SELECT MAX(id) FROM digital_downloads), 0) + 1, false)")
    src.close()


def upload_files(store, folders: list[str]):
    import cloud_storage

    todo: list[Path] = []
    for rel in folders:
        root = BASE / rel
        if root.is_dir():
            todo += [p for p in sorted(root.rglob("*")) if p.is_file() and p.name != ".gitkeep"]
    print(f"{len(todo)} arquivos, {fmt_size(sum(p.stat().st_size for p in todo))} no total.")
    remote: dict[Path, dict[str, int]] = {}
    sent = skipped = failed = 0
    for i, path in enumerate(todo, 1):
        if path.parent not in remote:
            try:
                remote[path.parent] = {f["name"]: f["size"] for f in store.list_files(path.parent)}
            except cloud_storage.StorageError:
                remote[path.parent] = {}
        size = path.stat().st_size
        label = path.relative_to(BASE).as_posix()
        if remote[path.parent].get(path.name) == size:
            skipped += 1
            continue
        print(f"  [{i}/{len(todo)}] {label} ({fmt_size(size)})")
        try:
            store.upload(path)
            sent += 1
        except cloud_storage.StorageError as exc:
            failed += 1
            print(f"    FALHOU: {exc}")
    print(f"Enviados: {sent}. Já estavam lá: {skipped}. Com erro: {failed}.")
    if failed:
        print("Rode o script de novo para tentar os que falharam (os enviados são pulados).")


def main():
    parser = argparse.ArgumentParser(description="Copia os dados do PC para o Supabase.")
    parser.add_argument("--sem-pedidos", action="store_true")
    parser.add_argument("--so-arquivos", action="store_true")
    parser.add_argument("--substituir", action="store_true")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(BASE / ".env")
    except ImportError:
        pass
    print("Dados do Supabase (veja o DEPLOY_RENDER.md, passo 1):")
    if not args.so_arquivos:
        url = ask("DATABASE_URL", "Connection string (Session pooler): ", secret=True)
        if not url.startswith(("postgres://", "postgresql://")):
            sys.exit("A connection string começa com postgresql://")
    ask("SUPABASE_URL", "Project URL (https://xxxx.supabase.co): ")
    ask("SUPABASE_SERVICE_KEY", "Chave secreta (service_role): ", secret=True)

    import cloud_storage
    store = cloud_storage.from_env(BASE)
    assert store
    store.ensure_bucket()

    if not args.so_arquivos:
        print("\n1) Copiando o banco...")
        copy_database(args.substituir)
    print("\n2) Enviando arquivos para o Storage...")
    upload_files(store, FILE_FOLDERS + ([] if args.sem_pedidos else ORDER_FOLDERS))
    print("\nPronto. Os caminhos do Windows no banco são corrigidos sozinhos quando o site sobe no Render.")


if __name__ == "__main__":
    main()

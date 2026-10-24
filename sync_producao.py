"""Traz os pedidos pagos do site para a pasta de produção, no PC do RIP.

Com o site no Render, os arquivos dos pedidos ficam no Supabase, não mais no
PC. Este programa roda no PC da produção, olha o site a cada minuto e, quando
um pedido é pago (status "Liberado para produção"), baixa as artes, o
pedido.json, o LEIA-ME e o relatório da chapa para uma pasta com o mesmo nome
de antes (241024-0001__Maria__DTF_ONLY) dentro da pasta de produção. O
operador continua abrindo no Flexi como sempre.

Uso (no PC da produção, com Python instalado; não precisa instalar nada a mais):
    python sync_producao.py            fica rodando (feche a janela para parar)
    python sync_producao.py --uma-vez  baixa o que tiver e sai

Na primeira vez ele pergunta o endereço do site, o usuário e a senha do Admin
e a pasta de produção, e guarda em sync_producao.json (ao lado deste arquivo).
A pasta só aparece quando todos os arquivos terminaram de baixar: o operador
nunca abre uma arte pela metade.
"""
from __future__ import annotations

import getpass
import io
import json
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "sync_producao.json"
STATE_FILE = HERE / "sync_producao_baixados.json"


def log(msg: str):
    print(f"[{time.strftime('%d/%m %H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    if CONFIG_FILE.is_file():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    print("Primeira vez: configure a sincronização.\n")
    site = input("Endereço do site (ex.: https://ripfire.onrender.com): ").strip().rstrip("/")
    user = input("Usuário do Admin: ").strip()
    password = getpass.getpass("Senha do Admin (não aparece ao digitar): ")
    folder = input("Pasta de produção [D:\\PEDIDOS_DTF_PAGOS]: ").strip() or "D:\\PEDIDOS_DTF_PAGOS"
    cfg = {"site_url": site, "admin_user": user, "admin_senha": password,
           "pasta_producao": folder, "intervalo_segundos": 60}
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGuardado em {CONFIG_FILE.name}. Para mudar depois, edite ou apague esse arquivo.\n")
    return cfg


class Site:
    def __init__(self, cfg: dict):
        self.base = cfg["site_url"].rstrip("/")
        self.headers = {"X-Admin-User": cfg["admin_user"], "X-Admin-Key": cfg["admin_senha"]}

    def get(self, path: str, timeout: float = 60) -> bytes:
        req = urllib.request.Request(self.base + path, headers=self.headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def get_json(self, path: str):
        return json.loads(self.get(path))


def download(url: str, dest: Path):
    tmp = dest.with_name(dest.name + ".parcial")
    with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    tmp.replace(dest)


def fetch_order(site: Site, order: dict, target_root: Path) -> str:
    """Baixa os arquivos do pedido numa pasta temporária e só no fim troca o
    nome pra definitiva. Devolve o nome da pasta."""
    staging_root = target_root / ".baixando"
    staging_root.mkdir(parents=True, exist_ok=True)
    try:
        info = site.get_json(f"/api/admin/orders/{order['id']}/file-links")
        name = info["folder_name"]
        staging = staging_root / name
        staging.mkdir(exist_ok=True)
        for f in info["files"]:
            dest = staging / f["name"]
            if dest.is_file() and dest.stat().st_size == f["size"]:
                continue  # já baixado numa tentativa anterior
            log(f"  {f['name']} ({f['size'] / 1048576:.1f} MB)")
            download(f["url"], dest)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        # Site sem Supabase Storage: baixa o zip do Admin.
        name = f"{order['order_number']}__{order['id']}"
        staging = staging_root / name
        staging.mkdir(exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(site.get(f"/api/admin/orders/{order['id']}/files", timeout=600))) as z:
            z.extractall(staging)
    final = target_root / name
    if final.exists():
        shutil.rmtree(staging, ignore_errors=True)
    else:
        staging.replace(final)
    return name


def run(once: bool):
    cfg = load_config()
    site = Site(cfg)
    target = Path(cfg["pasta_producao"])
    target.mkdir(parents=True, exist_ok=True)
    done: set[str] = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.is_file() else set()
    log(f"Sincronizando {site.base} -> {target}  (Ctrl+C ou feche a janela para parar)")
    while True:
        try:
            orders = site.get_json("/api/orders")
            pending = [o for o in orders if o["payment_status"] == "PAID" and o["status"] == "LIBERADO_PRODUCAO"
                       and o["sale_type"] != "PREMIUM_SUB" and o["id"] not in done]
            for o in sorted(pending, key=lambda o: o["created_at"]):
                log(f"Pedido {o['order_number']} ({o['customer_name']}) pago: baixando...")
                try:
                    name = fetch_order(site, o, target)
                except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
                    log(f"  ERRO, tento de novo no próximo ciclo: {exc}")
                    continue
                done.add(o["id"])
                STATE_FILE.write_text(json.dumps(sorted(done)), encoding="utf-8")
                log(f"  Pronto: {target / name}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                sys.exit(f"Usuário ou senha do Admin recusados ({e.code}). Corrija em {CONFIG_FILE.name}.")
            log(f"Site respondeu {e.code}; tento de novo em instantes.")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log(f"Sem conexão com o site ({exc}); tento de novo em instantes.")
        if once:
            return
        time.sleep(max(15, int(cfg.get("intervalo_segundos") or 60)))


if __name__ == "__main__":
    try:
        run("--uma-vez" in sys.argv)
    except KeyboardInterrupt:
        print("\nParado.")

"""Traz os pedidos pagos do site para a pasta de produção, no PC do RIP.

Com o site no Render, os arquivos dos pedidos ficam no Supabase, não mais no
PC. Este programa roda no PC da produção (ou em qualquer PC que enxergue a
pasta de rede da impressora), olha o site a cada minuto e, quando um pedido é
pago, monta a pasta de impressão.

Somente DTF fica assim:

    <pasta de produção>\\<Nome do cliente>\\<AAAA-MM-DD>\\
        261024-0001_CHAPA_58,5x120cm.png       a chapa como o cliente montou
        261024-0001_ARTE_01_logo_10x12cm_3un.png  cada arte, no tamanho escolhido
        261024-0001_ARTE_02_...
        261024-0001_DADOS_DO_PEDIDO.txt        cliente, artes e valor cobrado

Formato: se todas as artes do pedido são do mesmo tipo (PNG, TIFF ou PDF),
tudo sai nesse tipo. Se o cliente misturou tipos, tudo sai em PDF. Todo
arquivo sai a 300 dpi, no tamanho real em cm; PDF continua em vetor.

Os outros tipos de pedido (camiseta) continuam numa pasta por pedido, como
antes. Depois de baixar, o pedido passa sozinho para "Em fila" no site.

Uso (no PC da produção, com Python instalado):
    python sync_producao.py            fica rodando (feche a janela para parar)
    python sync_producao.py --uma-vez  baixa o que tiver e sai

Na primeira vez ele pergunta o endereço do site, o usuário e a senha do Admin
e a pasta de produção (pode ser pasta de rede: \\\\PC-IMPRESSORA\\DTF\\PEDIDOS),
e guarda em sync_producao.json (ao lado deste arquivo). Também instala sozinho
o Pillow e o pypdf, que ele usa para gerar os arquivos.
Os arquivos só aparecem na pasta quando estão completos: o operador nunca
abre uma arte pela metade.
"""
from __future__ import annotations

import getpass
import importlib
import io
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "sync_producao.json"
STATE_FILE = HERE / "sync_producao_baixados.json"
DPI = 300


def log(msg: str):
    print(f"[{time.strftime('%d/%m %H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    if CONFIG_FILE.is_file():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    print("Primeira vez: configure a sincronização.\n")
    site = input("Endereço do site (ex.: https://ripfirestudio.onrender.com): ").strip().rstrip("/")
    user = input("Usuário do Admin: ").strip()
    password = getpass.getpass("Senha do Admin (não aparece ao digitar): ")
    print("Pasta de produção: pode ser deste PC (D:\\PEDIDOS_DTF_PAGOS) ou de rede")
    print("(\\\\PC-IMPRESSORA\\DTF\\PEDIDOS). Em pasta de rede, use o caminho com \\\\, não a letra do drive.")
    folder = input("Pasta de produção [D:\\PEDIDOS_DTF_PAGOS]: ").strip().strip('"') or "D:\\PEDIDOS_DTF_PAGOS"
    cfg = {"site_url": site, "admin_user": user, "admin_senha": password,
           "pasta_producao": folder, "intervalo_segundos": 60}
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGuardado em {CONFIG_FILE.name}. Para mudar depois, edite ou apague esse arquivo.\n")
    return cfg


def ensure_libs():
    """Pillow (imagens) e pypdf (PDF). Instala na primeira vez."""
    try:
        import PIL  # noqa: F401
        import pypdf  # noqa: F401
        return
    except ImportError:
        pass
    log("Instalando Pillow e pypdf (só na primeira vez)...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "--disable-pip-version-check",
                    "pillow", "pypdf"], check=False)
    import site as pysite
    user_site = pysite.getusersitepackages()
    if user_site not in sys.path:
        sys.path.append(user_site)
    importlib.invalidate_caches()
    try:
        import PIL  # noqa: F401
        import pypdf  # noqa: F401
    except ImportError:
        sys.exit("Não consegui instalar o Pillow e o pypdf. Rode no Prompt: python -m pip install pillow pypdf")


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

    def patch_json(self, path: str, body: dict):
        req = urllib.request.Request(self.base + path, method="PATCH", data=json.dumps(body).encode(),
                                     headers={**self.headers, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")


def download(url: str, dest: Path):
    tmp = dest.with_name(dest.name + ".parcial")
    with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    tmp.replace(dest)


def fetch_files(site: Site, order: dict, staging: Path) -> str:
    """Baixa os arquivos de produção do pedido para staging. Devolve o nome
    de pasta que o site sugere (usado nos pedidos de camiseta)."""
    staging.mkdir(parents=True, exist_ok=True)
    try:
        info = site.get_json(f"/api/admin/orders/{order['id']}/file-links")
        for f in info["files"]:
            dest = staging / f["name"]
            if dest.is_file() and dest.stat().st_size == f["size"]:
                continue  # já baixado numa tentativa anterior
            log(f"  {f['name']} ({f['size'] / 1048576:.1f} MB)")
            download(f["url"], dest)
        return info["folder_name"]
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        # Site sem Supabase Storage: baixa o zip do Admin.
        with zipfile.ZipFile(io.BytesIO(site.get(f"/api/admin/orders/{order['id']}/files", timeout=600))) as z:
            z.extractall(staging)
        return f"{order['order_number']}__{order['id']}"


def fetch_order_folder(site: Site, order: dict, target_root: Path) -> Path:
    """Pedidos de camiseta: uma pasta por pedido, como sempre foi."""
    staging = target_root / ".baixando" / order["id"]
    name = fetch_files(site, order, staging)
    final = target_root / name
    if final.exists():
        shutil.rmtree(staging, ignore_errors=True)
    else:
        staging.replace(final)
    return final


# ---------------- Somente DTF ----------------

def safe(name: str, limit: int = 60) -> str:
    """Nome que o Windows aceita como pasta/arquivo."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip().rstrip(". ")
    name = re.sub(r"\s+", " ", name)
    return name[:limit].rstrip(". ") or "Sem nome"


def cm(v) -> str:
    x = round(float(v or 0), 1)
    return (str(int(x)) if x == int(x) else f"{x:.1f}").replace(".", ",")


def money(v) -> str:
    s = f"{float(v or 0):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def pt(v_cm: float) -> float:
    return float(v_cm) / 2.54 * 72


def px(v_cm: float) -> int:
    return max(1, round(float(v_cm) / 2.54 * DPI))


EXT = {"png": ".png", "tiff": ".tif", "pdf": ".pdf"}


def file_format(path: Path) -> str:
    return {".pdf": "pdf", ".tif": "tiff", ".tiff": "tiff"}.get(path.suffix.lower(), "png")


def output_format(originals: list[Path]) -> str:
    """Tudo de um tipo só: sai nesse tipo. Tipos misturados: tudo em PDF."""
    kinds = {file_format(p) for p in originals}
    return kinds.pop() if len(kinds) == 1 else ("pdf" if kinds else "png")


def open_raster(path: Path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None  # chapa de 58 cm x 3 m a 300 dpi passa do limite padrão
    img = Image.open(path)
    img.seek(0)  # TIFF com várias páginas: usa a primeira
    return img


def raster_at_size(path: Path, w_cm: float, h_cm: float, keep_cmyk: bool):
    from PIL import Image
    img = open_raster(path)
    if not (keep_cmyk and img.mode == "CMYK"):
        img = img.convert("RGBA")
    size = (px(w_cm), px(h_cm))
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    return img


def write_raster_pdf(img, dest: Path, w_cm: float, h_cm: float):
    """PDF com a imagem no tamanho exato em cm, mantendo a transparência
    (fundo transparente não pode virar branco no DTF)."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    rgb, alpha = zlib.compressobj(6), zlib.compressobj(6)
    rgb_parts, alpha_parts, opaque = [], [], True
    for y in range(0, h, 256):  # em faixas, pra não dobrar a memória numa chapa enorme
        band = img.crop((0, y, w, min(h, y + 256)))
        rgb_parts.append(rgb.compress(band.convert("RGB").tobytes()))
        a = band.getchannel("A")
        if opaque and a.getextrema()[0] < 255:
            opaque = False
        alpha_parts.append(alpha.compress(a.tobytes()))
    rgb_parts.append(rgb.flush())
    alpha_parts.append(alpha.flush())
    rgb_data = b"".join(rgb_parts)
    alpha_data = None if opaque else b"".join(alpha_parts)
    wp, hp = pt(w_cm), pt(h_cm)
    content = f"q {wp:.4f} 0 0 {hp:.4f} 0 0 cm /Im0 Do Q".encode()
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {wp:.4f} {hp:.4f}] "
        f"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
         f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(rgb_data)}"
         + (" /SMask 6 0 R" if alpha_data else "") + " >>\nstream\n").encode() + rgb_data + b"\nendstream",
    ]
    if alpha_data:
        objs.append((f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray "
                     f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(alpha_data)} >>\nstream\n").encode()
                    + alpha_data + b"\nendstream")
    with dest.open("wb") as f:
        f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(f.tell())
            f.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
        xref = f.tell()
        f.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
        for off in offsets:
            f.write(f"{off:010d} 00000 n \n".encode())
        f.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())


def pdf_at_size(src: Path, dest: Path, w_cm: float, h_cm: float):
    """Primeira página do PDF do cliente, esticada pro tamanho escolhido, em vetor."""
    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.generic import NameObject, RectangleObject
    page = PdfReader(src).pages[0]
    if page.get("/Rotate") and hasattr(page, "transfer_rotation_to_content"):
        page.transfer_rotation_to_content()
    box = page.cropbox
    bw, bh = float(box.width), float(box.height)
    tw, th = pt(w_cm), pt(h_cm)
    page.add_transformation(Transformation().translate(-float(box.left), -float(box.bottom)).scale(tw / bw, th / bh))
    rect = RectangleObject([0, 0, tw, th])
    page[NameObject("/MediaBox")] = rect
    page[NameObject("/CropBox")] = rect
    for k in ("/TrimBox", "/BleedBox", "/ArtBox"):
        if k in page:
            del page[k]
    out = PdfWriter()
    out.add_page(page)
    with dest.open("wb") as f:
        out.write(f)


def save_as(src: Path, dest: Path, fmt: str, w_cm: float, h_cm: float):
    """Gera a arte no formato do pedido e no tamanho escolhido pelo cliente."""
    if fmt == "pdf":
        if file_format(src) == "pdf":
            pdf_at_size(src, dest, w_cm, h_cm)
        else:
            write_raster_pdf(raster_at_size(src, w_cm, h_cm, keep_cmyk=False), dest, w_cm, h_cm)
    elif fmt == "tiff":
        img = raster_at_size(src, w_cm, h_cm, keep_cmyk=True)
        img.save(dest, "TIFF", compression="tiff_lzw", dpi=(DPI, DPI))
    else:
        img = raster_at_size(src, w_cm, h_cm, keep_cmyk=False)
        img.save(dest, "PNG", dpi=(DPI, DPI), compress_level=6)


def save_sheet(src: Path, dest: Path, fmt: str, w_cm: float, h_cm: float):
    """A chapa como o cliente montou. Ela já vem a 300 dpi no tamanho certo."""
    if fmt == "png":
        shutil.copy2(src, dest)
        return
    img = open_raster(src)
    if fmt == "tiff":
        img.convert("RGBA").save(dest, "TIFF", compression="tiff_lzw", dpi=(DPI, DPI))
    else:
        write_raster_pdf(img, dest, w_cm, h_cm)


def order_text(order: dict, data: dict, sheet_name: str, arts: list[tuple[dict, str | None]], fmt: str) -> str:
    c = data.get("customer") or {}
    design = data.get("design") or {}
    when = time.strftime("%d/%m/%Y %H:%M", time.localtime(float(order["created_at"])))
    total_qty = sum(int(s.get("qty") or 0) for s, _ in arts)
    lines = [
        f"PEDIDO {order['order_number']} - SOMENTE DTF",
        f"Data do pedido: {when}",
        "Pagamento: aprovado" + (f" (Mercado Pago {order['payment_ref']})" if order.get("payment_ref") else ""),
        "",
        "CLIENTE",
        f"Nome: {order.get('customer_name') or c.get('name') or ''}",
        f"E-mail: {order.get('customer_email') or c.get('email') or ''}",
        f"WhatsApp: {order.get('customer_whatsapp') or c.get('whatsapp') or ''}",
    ]
    if c.get("document"):
        lines.append(f"CPF/CNPJ: {c['document']}")
    if c.get("cep"):
        lines.append(f"CEP: {c['cep']}" + (f"  Número: {c['address_number']}" if c.get("address_number") else ""))
    lines += [
        "",
        "CHAPA",
        f"Tamanho: {cm(design.get('width_cm'))} x {cm(design.get('height_cm'))} cm",
        f"Arquivo: {sheet_name}",
        f"Formato dos arquivos: {fmt.upper()}",
        "",
        "ARTES (na ordem em que o cliente montou)",
    ]
    for i, (s, fname) in enumerate(arts, start=1):
        lines.append(f"{i:02d}. {Path(str(s.get('name') or 'Sem título')).stem} - {cm(s.get('width_cm'))} x {cm(s.get('height_cm'))} cm"
                     f" - {int(s.get('qty') or 0)} un."
                     + (f" - {fname}" if fname else " - (sem arquivo separado; usar a chapa)"))
    lines += [
        f"Total de peças: {total_qty}",
        "",
        "VALOR COBRADO",
        f"Total: {money(order.get('total'))}",
    ]
    return "\r\n".join(lines) + "\r\n"


def build_dtf_order(site: Site, order: dict, target_root: Path) -> Path:
    """Monta <cliente>\\<data>\\ com a chapa, as artes separadas e o txt."""
    data = order.get("data") or {}
    work = target_root / ".baixando" / order["id"]
    raw, out = work / "baixado", work / "pronto"
    fetch_files(site, order, raw)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    sources = [s for s in (data.get("sources") or []) if isinstance(s, dict)]
    originals: list[Path | None] = []
    for i, s in enumerate(sources, start=1):
        # Só aceita o nome que o próprio site dá (ORIGINAL_01.pdf...).
        hits = [p for p in raw.glob(f"ORIGINAL_{i:02d}.*") if p.suffix.lower() in
                (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".pdf")]
        originals.append(hits[0] if hits else None)
    fmt = output_format([p for p in originals if p])
    num = order["order_number"]
    design = data.get("design") or {}

    sheet_src = next((p for p in raw.glob("ARTE_PRODUCAO.*")), None)
    if not sheet_src:
        raise ValueError("a chapa (ARTE_PRODUCAO) não veio do site")
    sheet_name = f"{num}_CHAPA_{cm(design.get('width_cm'))}x{cm(design.get('height_cm'))}cm{EXT[fmt]}"
    log(f"  chapa -> {sheet_name}")
    save_sheet(sheet_src, out / sheet_name, fmt, float(design.get("width_cm") or 0), float(design.get("height_cm") or 0))

    arts: list[tuple[dict, str | None]] = []
    for i, (s, src) in enumerate(zip(sources, originals), start=1):
        if not src:
            arts.append((s, None))
            continue
        stem = safe(Path(str(s.get("name") or "arte")).stem, 40)
        name = (f"{num}_ARTE_{i:02d}_{stem}_{cm(s.get('width_cm'))}x{cm(s.get('height_cm'))}cm_"
                f"{int(s.get('qty') or 0)}un{EXT[fmt]}")
        log(f"  arte {i:02d} -> {name}")
        save_as(src, out / name, fmt, float(s.get("width_cm") or 0), float(s.get("height_cm") or 0))
        arts.append((s, name))

    txt = f"{num}_DADOS_DO_PEDIDO.txt"
    (out / txt).write_text(order_text(order, data, sheet_name, arts, fmt), encoding="utf-8-sig")

    day = time.strftime("%Y-%m-%d", time.localtime(float(order["created_at"])))
    final = target_root / safe(order.get("customer_name") or "Cliente") / day
    final.mkdir(parents=True, exist_ok=True)
    # As artes primeiro e o txt por último: arquivo que aparece na pasta já está inteiro.
    for p in sorted(out.iterdir(), key=lambda p: p.suffix == ".txt"):
        p.replace(final / p.name)
    shutil.rmtree(work, ignore_errors=True)
    return final


def mark_in_queue(site: Site, order: dict):
    if (order.get("stage") or "PAGO") != "PAGO":
        return  # alguém já mexeu na etapa no Admin
    try:
        site.patch_json(f"/api/admin/orders/{order['id']}/stage", {"stage": "EM_FILA"})
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log(f"  (não consegui mudar a etapa para Em fila: {exc})")


def run(once: bool):
    cfg = load_config()
    ensure_libs()
    site = Site(cfg)
    target = Path(cfg["pasta_producao"])
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.exit(f"Não consegui abrir a pasta de produção {target}: {exc}\n"
                 f"Confira se a pasta de rede está acessível deste PC, ou corrija em {CONFIG_FILE.name}.")
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
                    if o["sale_type"] == "DTF_ONLY":
                        where = build_dtf_order(site, o, target)
                    else:
                        where = fetch_order_folder(site, o, target)
                except Exception as exc:  # um pedido com problema não para os outros
                    log(f"  ERRO, tento de novo no próximo ciclo: {exc}")
                    continue
                done.add(o["id"])
                STATE_FILE.write_text(json.dumps(sorted(done)), encoding="utf-8")
                mark_in_queue(site, o)
                log(f"  Pronto: {where}")
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

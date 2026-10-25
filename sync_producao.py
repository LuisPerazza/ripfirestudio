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

Camiseta (pronta ou DTF + camiseta) vai para a mesma pasta do cliente e do
dia: a arte de cada lado por tamanho (o menor tamanho como o cliente montou,
os maiores proporcionais à área de impressão de cada um), a chapa com todas
as cópias, as fotos de posição, a ficha de produção em PDF e o txt.
Depois de baixar, o pedido passa sozinho para "Em fila" no site.

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
    print("Use um usuário da equipe com papel Produção (crie em Admin > Segurança).")
    user = input("Usuário: ").strip()
    password = getpass.getpass("Senha (não aparece ao digitar): ")
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
    """Fala com o site como um usuário da equipe. Entra uma vez e usa o token
    de sessão (12 h); quando ele vence, entra de novo sozinho.
    Use um usuário com papel Produção e sem 2FA (criado em Admin > Segurança)."""

    def __init__(self, cfg: dict):
        self.base = cfg["site_url"].rstrip("/")
        self.user, self.password = cfg["admin_user"], cfg["admin_senha"]
        self.token = ""

    def login(self):
        req = urllib.request.Request(self.base + "/api/admin/login", method="POST",
                                     data=json.dumps({"user": self.user, "password": self.password}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:  # site antigo, sem login por sessão
                self.token = ""
                return
            raise
        if d.get("need_code"):
            sys.exit("Esse usuário usa verificação em duas etapas (2FA). Crie no Admin > Segurança um usuário "
                     f"com papel Produção, sem 2FA, e coloque em {CONFIG_FILE.name}.")
        self.token = d["token"]

    def _headers(self) -> dict:
        if self.token:
            return {"X-Admin-Token": self.token}
        return {"X-Admin-User": self.user, "X-Admin-Key": self.password}

    def _open(self, make, timeout: float):
        if not self.token:
            self.login()
        for attempt in (1, 2):
            try:
                with urllib.request.urlopen(make(), timeout=timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 1 and self.token:
                    self.login()  # sessão venceu
                    continue
                raise

    def get(self, path: str, timeout: float = 60) -> bytes:
        return self._open(lambda: urllib.request.Request(self.base + path, headers=self._headers()), timeout)

    def get_json(self, path: str):
        return json.loads(self.get(path))

    def patch_json(self, path: str, body: dict):
        data = json.dumps(body).encode()
        raw = self._open(lambda: urllib.request.Request(self.base + path, method="PATCH", data=data,
                                                        headers={**self._headers(), "Content-Type": "application/json"}), 60)
        return json.loads(raw or b"{}")


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

    return deliver(order, out, work, target_root)


def deliver(order: dict, out: Path, work: Path, target_root: Path) -> Path:
    """Move os arquivos prontos para <cliente>\\<data>\\."""
    day = time.strftime("%Y-%m-%d", time.localtime(float(order["created_at"])))
    final = target_root / safe(order.get("customer_name") or "Cliente") / day
    final.mkdir(parents=True, exist_ok=True)
    # As artes primeiro e o txt por último: arquivo que aparece na pasta já está inteiro.
    for p in sorted(out.iterdir(), key=lambda p: p.suffix == ".txt"):
        p.replace(final / p.name)
    shutil.rmtree(work, ignore_errors=True)
    return final


# ---------------- Camisetas (pronta e DTF + camiseta) ----------------

SHIRT_SIDES = [  # arquivo que vem do site, nome na produção, cresce com o tamanho?
    ("ARTE_PRODUCAO_FRENTE", "FRENTE", True),
    ("ARTE_PRODUCAO_COSTAS", "COSTAS", True),
    ("ARTE_PRODUCAO_MANGA_DIREITA", "MANGA_DIREITA", False),
    ("ARTE_PRODUCAO_MANGA_ESQUERDA", "MANGA_ESQUERDA", False),
]
SIDE_LABEL = {"FRENTE": "Frente", "COSTAS": "Costas", "MANGA_DIREITA": "Manga direita",
              "MANGA_ESQUERDA": "Manga esquerda"}
SALE_LABEL = {"FINISHED_SHIRT": "CAMISETA PRONTA", "DTF_PLUS_SHIRT": "DTF + CAMISETA"}
GAP_CM = 0.6            # espaço entre as peças e nas bordas, igual à chapa do Somente DTF
MAX_SHEET_CM = 250.0    # chapa mais comprida que isso é dividida em várias
_site_config: dict = {}


def site_config(site: Site) -> dict:
    if not _site_config:
        _site_config.update(site.get_json("/api/config"))
    return _site_config


def image_cm(img) -> tuple[float, float]:
    dpi = img.info.get("dpi")
    d = float(dpi[0]) if dpi and dpi[0] and float(dpi[0]) > 1 else DPI
    return img.size[0] / d * 2.54, img.size[1] / d * 2.54


def ordered_sizes(cfg: dict, product: dict) -> list[tuple[str, int]]:
    model = next((m for m in cfg.get("shirt_models") or [] if m.get("id") == product.get("shirt_model_id")), {})
    order = list(model.get("sizes") or cfg.get("sizes") or [])
    sizes = [(str(k), int(v or 0)) for k, v in (product.get("sizes") or {}).items() if int(v or 0) > 0]
    return sorted(sizes, key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order))


def size_factor(limits: dict, ref: str, size: str, art_w: float, art_h: float) -> float:
    """O menor tamanho do pedido sai como o cliente montou; os maiores crescem
    na proporção da área máxima de impressão de cada tamanho, sem passar dela."""
    lr, ls = limits.get(ref), limits.get(size)
    if not lr or not ls or size == ref:
        return 1.0
    f = min(float(ls["w"]) / float(lr["w"]), float(ls["h"]) / float(lr["h"]))
    f = min(f, float(ls["w"]) / art_w, float(ls["h"]) / art_h)
    return max(1.0, f)


def load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    for name in (("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def pack_pieces(pieces: list[dict], sheet_w: float) -> list[dict]:
    """Prateleiras, como o "Automático" da chapa do site. Devolve as chapas:
    {"height": cm, "items": [(peça, x, y, w, h, girada)]}."""
    usable = sheet_w - 2 * GAP_CM
    sheets: list[dict] = []
    items: list = []
    x = y = GAP_CM
    shelf = 0.0
    for p in pieces:
        w, h, rot = p["w"], p["h"], False
        if w > usable and h <= usable:
            w, h, rot = h, w, True
        if x + w > sheet_w - GAP_CM + 1e-6:
            y, x, shelf = y + shelf + GAP_CM, GAP_CM, 0.0
        if items and y + h + GAP_CM > MAX_SHEET_CM:
            sheets.append({"items": items})
            items, x, y, shelf = [], GAP_CM, GAP_CM, 0.0
        items.append((p, x, y, w, h, rot))
        x += w + GAP_CM
        shelf = max(shelf, h)
    if items:
        sheets.append({"items": items})
    for sh in sheets:
        sh["height"] = round(max(it[2] + it[4] for it in sh["items"]) + GAP_CM, 1)
    return sheets


def render_sheet(sheet: dict, sheet_w: float, dest: Path, map_dest: Path):
    """A chapa para imprimir (300 dpi, transparente) e um mapa em JPG com o
    tamanho e o lado de cada peça, para a ficha de produção."""
    from PIL import Image, ImageDraw
    big = Image.new("RGBA", (px(sheet_w), px(sheet["height"])), (0, 0, 0, 0))
    scale = 900 / sheet_w  # px por cm no mapa
    mw, mh = 900, max(1, round(sheet["height"] * scale))
    mapa = Image.new("RGB", (mw, mh), (236, 236, 232))
    draw = ImageDraw.Draw(mapa)
    font = load_font(max(12, min(22, round(2.2 * scale))), bold=True)
    for p, x, y, w, h, rot in sheet["items"]:
        img = p["img"].rotate(90, expand=True) if rot else p["img"]
        if img.size != (px(w), px(h)):
            img = img.resize((px(w), px(h)))
        big.alpha_composite(img, (px(x), px(y)))
        thumb = img.resize((max(1, round(w * scale)), max(1, round(h * scale))))
        mapa.paste(thumb, (round(x * scale), round(y * scale)), thumb)
        box = [round(x * scale), round(y * scale), round((x + w) * scale), round((y + h) * scale)]
        draw.rectangle(box, outline=(124, 58, 160), width=2)
        label = f"{p['size']} · {SIDE_LABEL[p['side']]}" if p["size"] else SIDE_LABEL[p["side"]]
        tb = draw.textbbox((0, 0), label, font=font)
        tx, ty = box[0] + 4, box[1] + 4
        draw.rectangle([tx - 3, ty - 2, tx + tb[2] - tb[0] + 3, ty + tb[3] - tb[1] + 4], fill=(30, 30, 34))
        draw.text((tx - tb[0], ty - tb[1]), label, fill=(255, 255, 255), font=font)
    big.save(dest, "PNG", dpi=(DPI, DPI), compress_level=6)
    mapa.save(map_dest, "JPEG", quality=85)


class MiniPDF:
    """PDF simples (texto, retângulos e fotos JPG) para a ficha de produção."""
    W, H = 595.28, 841.89  # A4

    def __init__(self):
        self.pages: list[tuple[list[str], list[int]]] = []
        self.images: list[tuple[int, int, bytes]] = []

    def new_page(self):
        self.pages.append(([], []))

    def _ops(self):
        return self.pages[-1][0]

    @staticmethod
    def _esc(text: str) -> str:
        raw = str(text).encode("cp1252", "replace").decode("latin-1")
        return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def text(self, x, y, text, size=10, bold=False, color=(0, 0, 0)):
        r, g, b = (c / 255 for c in color)
        self._ops().append(f"BT {r:.3f} {g:.3f} {b:.3f} rg /{'F2' if bold else 'F1'} {size} Tf "
                           f"{x:.2f} {self.H - y:.2f} Td ({self._esc(text)}) Tj ET")

    def rect(self, x, y, w, h, fill=None, stroke=None):
        ops = self._ops()
        ops.append("q")
        if fill:
            ops.append("{:.3f} {:.3f} {:.3f} rg".format(*(c / 255 for c in fill)))
        if stroke:
            ops.append("{:.3f} {:.3f} {:.3f} RG 0.7 w".format(*(c / 255 for c in stroke)))
        ops.append(f"{x:.2f} {self.H - y - h:.2f} {w:.2f} {h:.2f} re {'B' if fill and stroke else 'f' if fill else 'S'}")
        ops.append("Q")

    def image(self, path: Path, x, y, max_w, max_h) -> float:
        """Foto que cabe em max_w x max_h. Devolve a altura usada."""
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((1400, 1400))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        s = min(max_w / im.width, max_h / im.height)
        w, h = im.width * s, im.height * s
        self.images.append((im.width, im.height, buf.getvalue()))
        idx = len(self.images) - 1
        self.pages[-1][1].append(idx)
        self._ops().append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {self.H - y - h:.2f} cm /Im{idx} Do Q")
        return h

    def save(self, dest: Path):
        objs: list[bytes] = [b"", b""]  # 1 catálogo, 2 páginas (preenchidos no fim)
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        img_obj = {}
        for i, (w, h, data) in enumerate(self.images):
            objs.append(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
                        f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode()
                        + data + b"\nendstream")
            img_obj[i] = len(objs)
        kids = []
        for ops, imgs in self.pages:
            content = "\n".join(ops).encode("latin-1")
            objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
            content_no = len(objs)
            xo = " ".join(f"/Im{i} {img_obj[i]} 0 R" for i in imgs)
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.W} {self.H}] "
                        f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> /XObject << {xo} >> >> "
                        f"/Contents {content_no} 0 R >>".encode())
            kids.append(len(objs))
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
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


def shirt_sheet_note(sizes: list[tuple[str, int]]) -> str:
    return (f"O tamanho {sizes[0][0]} sai como o cliente montou; os maiores crescem na proporção "
            "da área de impressão de cada tamanho.") if len(sizes) > 1 else "Arte no tamanho que o cliente montou."


def shirt_text(order, data, product, sizes, art_rows, sheet_rows, sleeve_rows) -> str:
    c = data.get("customer") or {}
    when = time.strftime("%d/%m/%Y %H:%M", time.localtime(float(order["created_at"])))
    total = sum(q for _, q in sizes)
    lines = [
        f"PEDIDO {order['order_number']} - {SALE_LABEL.get(order['sale_type'], order['sale_type'])}",
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
        "CAMISETA",
        f"Modelo: {product.get('shirt_model') or product.get('shirt_model_id') or ''}",
        f"Cor: {product.get('color') or ''}",
        "Tamanhos: " + " · ".join(f"{s} {q}" for s, q in sizes) + f"  (total {total} peças)",
        "",
        "ARTES POR TAMANHO",
    ]
    for side, rows in art_rows.items():
        for size, w, h, q, fname in rows:
            lines.append(f"{SIDE_LABEL[side]} {size}: {cm(w)} x {cm(h)} cm - {q} un. - {fname}")
    for side, w, h, q, fname in sleeve_rows:
        lines.append(f"{SIDE_LABEL[side]} (todos os tamanhos): {cm(w)} x {cm(h)} cm - {q} un. - {fname}")
    lines.append(shirt_sheet_note(sizes))
    lines += ["", "CHAPA DE IMPRESSÃO"]
    for i, (fname, h) in enumerate(sheet_rows, start=1):
        lines.append(f"{i} de {len(sheet_rows)}: {cm(SHEET_W_DEFAULT)} x {cm(h)} cm - {fname}")
    lines += ["", "VALOR COBRADO", f"Total: {money(order.get('total'))}"]
    return "\r\n".join(lines) + "\r\n"


SHEET_W_DEFAULT = 58.5


def shirt_ficha(dest: Path, order, data, product, sizes, art_rows, sleeve_rows, placements, sheet_rows, maps):
    pdf = MiniPDF()
    pdf.new_page()
    purple, dark, gray = (124, 58, 160), (30, 30, 34), (90, 90, 96)
    pdf.rect(0, 0, pdf.W, 70, fill=dark)
    pdf.text(36, 32, "FICHA DE PRODUÇÃO", 20, True, (255, 255, 255))
    pdf.text(36, 54, SALE_LABEL.get(order["sale_type"], ""), 10, True, (200, 170, 230))
    pdf.text(400, 40, f"Pedido {order['order_number']}", 14, True, (255, 255, 255))
    c = data.get("customer") or {}
    when = time.strftime("%d/%m/%Y %H:%M", time.localtime(float(order["created_at"])))
    total = sum(q for _, q in sizes)
    y = 100
    for label, value in [("Cliente", order.get("customer_name") or c.get("name") or ""),
                         ("WhatsApp", order.get("customer_whatsapp") or c.get("whatsapp") or ""),
                         ("Data do pedido", when),
                         ("Modelo", product.get("shirt_model") or ""),
                         ("Cor", product.get("color") or ""),
                         ("Total de peças", str(total))]:
        pdf.text(36, y, f"{label}:", 10, True, gray)
        pdf.text(130, y, value, 11, False, dark)
        y += 17
    # grade
    y += 10
    cols = [36, 136, 196, 366]
    pdf.rect(36, y, 523, 22, fill=(226, 226, 222))
    for x, head in zip(cols, ["Tamanho", "Qtd", "Frente (cm)", "Costas (cm)"]):
        pdf.text(x + 8, y + 15, head, 10, True, dark)
    y += 22
    by_size = {s: {} for s, _ in sizes}
    for side, rows in art_rows.items():
        for size, w, h, q, _ in rows:
            by_size[size][side] = f"{cm(w)} x {cm(h)}"
    for s, q in sizes:
        pdf.rect(36, y, 523, 22, stroke=(210, 210, 205))
        pdf.text(cols[0] + 8, y + 15, s, 11, True, purple)
        pdf.text(cols[1] + 8, y + 15, str(q), 11)
        pdf.text(cols[2] + 8, y + 15, by_size[s].get("FRENTE", "-"), 11)
        pdf.text(cols[3] + 8, y + 15, by_size[s].get("COSTAS", "-"), 11)
        y += 22
    for side, w, h, q, _ in sleeve_rows:
        y += 16
        pdf.text(36, y, f"{SIDE_LABEL[side]}: {cm(w)} x {cm(h)} cm em todas as {q} peças", 10, False, dark)
    y += 18
    pdf.text(36, y, shirt_sheet_note(sizes), 9, False, gray)
    # posição
    if placements:
        y += 26
        pdf.text(36, y, "POSIÇÃO NA CAMISETA (como o cliente deixou)", 11, True, dark)
        y += 10
        slot = (523 - 12 * (len(placements) - 1)) / len(placements)
        used = 0.0
        for i, (label, path) in enumerate(placements):
            x = 36 + i * (slot + 12)
            pdf.text(x, y + 14, label, 10, True, purple)
            used = max(used, pdf.image(path, x, y + 22, slot, pdf.H - y - 70))
        y += 22 + used
    pdf.text(36, pdf.H - 30, "Chapa(s): " + ", ".join(f for f, _ in sheet_rows), 8, False, gray)
    # mapa de cada chapa
    for i, ((fname, h), mp) in enumerate(zip(sheet_rows, maps), start=1):
        pdf.new_page()
        pdf.text(36, 40, f"CHAPA {i} DE {len(sheet_rows)}  ·  {cm(SHEET_W_DEFAULT)} x {cm(h)} cm", 14, True, dark)
        pdf.text(36, 58, fname, 9, False, gray)
        pdf.image(mp, 36, 72, 523, pdf.H - 110)
    pdf.save(dest)


def build_shirt_order(site: Site, order: dict, target_root: Path) -> Path:
    """Camiseta: artes por lado e tamanho, a chapa com todas as cópias, as
    fotos de posição, o txt e a ficha de produção em PDF."""
    global SHEET_W_DEFAULT
    from PIL import Image
    cfg = site_config(site)
    SHEET_W_DEFAULT = float(cfg.get("dtf_sheet_width_cm") or 58.5)
    limits = cfg.get("size_print_limits") or {}
    data = order.get("data") or {}
    product = data.get("product") or {}
    sizes = ordered_sizes(cfg, product)
    if not sizes:
        raise ValueError("pedido sem tamanhos")
    total = sum(q for _, q in sizes)
    num = order["order_number"]
    work = target_root / ".baixando" / order["id"]
    raw, out = work / "baixado", work / "pronto"
    fetch_files(site, order, raw)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    pieces: list[dict] = []
    art_rows: dict[str, list] = {}
    sleeve_rows: list = []
    for src_name, side, grows in SHIRT_SIDES:
        src = raw / f"{src_name}.png"
        if not src.is_file():
            continue
        base = open_raster(src).convert("RGBA")
        bw, bh = image_cm(open_raster(src))
        if not grows:
            name = f"{num}_{side}_{cm(bw)}x{cm(bh)}cm_{total}un.png"
            base.save(out / name, "PNG", dpi=(DPI, DPI), compress_level=6)
            sleeve_rows.append((side, bw, bh, total, name))
            pieces += [{"img": base, "w": bw, "h": bh, "size": "", "side": side}] * total
            continue
        rows = art_rows.setdefault(side, [])
        cache: dict[float, tuple] = {}
        for size, qty in sizes:
            f = round(size_factor(limits, sizes[0][0], size, bw, bh), 4)
            if f not in cache:
                w, h = bw * f, bh * f
                img = base if f == 1.0 else base.resize((px(w), px(h)), Image.LANCZOS)
                cache[f] = (img, w, h)
            img, w, h = cache[f]
            name = f"{num}_{side}_{size}_{cm(w)}x{cm(h)}cm_{qty}un.png"
            log(f"  {SIDE_LABEL[side].lower()} {size} -> {name}")
            img.save(out / name, "PNG", dpi=(DPI, DPI), compress_level=6)
            rows.append((size, w, h, qty, name))
            pieces += [{"img": img, "w": w, "h": h, "size": size, "side": side}] * qty
    if not pieces:
        raise ValueError("o pedido não tem arte de frente, costas ou manga")

    # Chapa: tamanho por tamanho (P, depois M...), as maiores primeiro em cada um.
    rank = {s: i for i, (s, _) in enumerate(sizes)}
    pieces.sort(key=lambda p: (rank.get(p["size"], len(rank)), -p["h"]))
    sheets = pack_pieces(pieces, SHEET_W_DEFAULT)
    sheet_rows, maps = [], []
    for i, sh in enumerate(sheets, start=1):
        part = f"_{i}de{len(sheets)}" if len(sheets) > 1 else ""
        name = f"{num}_CHAPA{part}_{cm(SHEET_W_DEFAULT)}x{cm(sh['height'])}cm.png"
        log(f"  chapa -> {name} ({len(sh['items'])} peças)")
        mp = work / f"mapa_{i}.jpg"
        render_sheet(sh, SHEET_W_DEFAULT, out / name, mp)
        sheet_rows.append((name, sh["height"]))
        maps.append(mp)

    placements = []
    for side, label in (("FRENTE", "Frente"), ("COSTAS", "Costas")):
        src = raw / f"POSICAO_{side}.jpg"
        if src.is_file():
            shutil.copy2(src, out / f"{num}_POSICAO_{side}.jpg")
            placements.append((label, src))

    shirt_ficha(out / f"{num}_FICHA_PRODUCAO.pdf", order, data, product, sizes, art_rows, sleeve_rows,
                placements, sheet_rows, maps)
    (out / f"{num}_DADOS_DO_PEDIDO.txt").write_text(
        shirt_text(order, data, product, sizes, art_rows, sheet_rows, sleeve_rows), encoding="utf-8-sig")
    return deliver(order, out, work, target_root)


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
                    elif o["sale_type"] in SALE_LABEL:
                        where = build_shirt_order(site, o, target)
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

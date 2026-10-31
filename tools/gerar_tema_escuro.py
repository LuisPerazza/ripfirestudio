"""Gera static/dark.css a partir do static/styles.css.

O site tem muitas cores escritas direto em cada regra. Em vez de manter dois
CSS na mão, este script lê o styles.css e, para cada regra que pinta fundo,
borda ou texto, escreve a mesma regra dentro de html[data-theme="dark"] com a
cor trocada: fundos e bordas claros viram escuros, textos escuros viram
claros. Cores fortes (o roxo da marca, verde, vermelho) ficam como estão.

Rode de novo sempre que mudar o styles.css:
    python tools/gerar_tema_escuro.py
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "styles.css"
OUT = ROOT / "static" / "dark.css"
SCOPE = ':where(html[data-theme="dark"])'
# :where() não soma especificidade: a regra escura vale o mesmo que a clara e,
# como o dark.css vem depois, ganha dela. E toda regra que pinta alguma coisa é
# repetida aqui na mesma ordem do styles.css (mesmo as que não mudam de cor),
# pra que a ordem entre elas continue a mesma (ex.: aba ativa x passar o mouse).

# Cores do tema escuro (neutras, no estilo do painel do Supabase).
SURFACE = {  # fundo / borda clara -> escura
    "#ffffff": "#1c1c1f", "#fafafa": "#1c1c1f", "#f7f7f4": "#1c1c1f", "#f5f5f5": "#121214",
    "#f4f4f1": "#1c1c1f", "#f2f2f2": "#1a1a1d", "#f2f2ef": "#1c1c1f", "#f0f0ec": "#232326",
    "#efefec": "#26262a", "#eeeeee": "#26262a", "#ececec": "#26262a", "#ecece8": "#232326",
    "#e8e8e4": "#1e1e21", "#e5e5e1": "#29292d", "#e2e2e2": "#2e2e33", "#dcdcd8": "#202023",
    "#d7d7d2": "#3a3a40", "#d6d6d1": "#333338", "#d4d4d8": "#3a3a40", "#d2d2cd": "#2a2a2e",
    "#cfcfcf": "#3a3a40", "#cbcbc6": "#34343a", "#bdbdb7": "#4a4a50",
    # tons de roxo e vermelho claros (avisos, chips)
    "#f6eefb": "#2a2033", "#ecd3f7": "#3a2747", "#e2cdee": "#3a2747", "#d9bfe8": "#4a3358",
    "#f8e3e1": "#3a2020", "#e8bdb8": "#5a2c2c", "#ffb8b8": "#5a2c2c",
}
TEXT = {  # texto escuro -> claro
    "#181818": "#ececef", "#18181b": "#ececef", "#222222": "#e6e6e9", "#333333": "#dcdce0",
    "#2c2c31": "#e0e0e4", "#26262b": "#e0e0e4", "#4a4a50": "#c8c8ce", "#5c5c62": "#a8a8b0",
    "#6b6b6b": "#a2a2aa", "#5b2b75": "#e2c6f2", "#7d3f9a": "#c99be0", "#087a3d": "#4fd18b",
    "#1f7d52": "#4fd18b", "#b11111": "#ff8a80", "#b3261e": "#ff8a80",
}
SURFACE_PROPS = ("background", "background-color", "background-image", "border", "border-color",
                 "border-top", "border-right", "border-bottom", "border-left", "outline", "box-shadow",
                 "border-top-color", "border-bottom-color", "border-left-color", "border-right-color")
TEXT_PROPS = ("color", "fill", "stroke", "caret-color", "text-decoration-color")
KEEP_PROPS = ("background-clip", "-webkit-background-clip", "-webkit-text-fill-color", "background-size",
              "background-position", "background-repeat", "background-attachment")


def norm(hex_: str) -> str:
    h = hex_.lower()
    if len(h) == 4:
        h = "#" + "".join(c * 2 for c in h[1:])
    return h


def lum(h: str) -> tuple[float, float, float]:
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    return hh, ll, ss


def to_hex(hh: float, ll: float, ss: float) -> str:
    r, g, b = colorsys.hls_to_rgb(hh, max(0, min(1, ll)), ss)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def map_surface(h: str) -> str | None:
    h = norm(h)
    if h in SURFACE:
        return SURFACE[h]
    hh, ll, ss = lum(h)
    if ll > 0.72 and ss < 0.5:  # cinza/claro qualquer: espelha a luz
        return to_hex(hh, 0.06 + (1 - ll) * 0.9, ss * 0.4)
    return None


def map_text(h: str) -> str | None:
    h = norm(h)
    if h in TEXT:
        return TEXT[h]
    hh, ll, ss = lum(h)
    if ll < 0.45 and ss < 0.35:  # texto escuro neutro: fica claro
        return to_hex(hh, 1.02 - ll, ss)
    return None


HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def convert_decl(prop: str, value: str, text_clip: bool = False) -> tuple[str, bool] | None:
    """Devolve (valor, mudou?) para propriedade de cor; None para as outras."""
    prop = prop.strip().lower()
    if prop.startswith("--"):
        return None
    if prop in KEEP_PROPS:
        return value, False
    mapper = map_surface if prop in SURFACE_PROPS else map_text if prop in TEXT_PROPS else None
    if not mapper:
        return None
    if text_clip:  # texto pintado com degradê: mantém as cores
        return value, False
    changed = False

    def sub(m):
        nonlocal changed
        new = mapper(m.group(0))
        if new and new != norm(m.group(0)):
            changed = True
            return new
        return m.group(0)

    out = HEX.sub(sub, value)
    return out, changed


def split_rules(css: str):
    """Devolve (seletor, corpo) e (@media, conteúdo) no nível de cima."""
    i, n = 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            break
        head = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        yield head, css[j + 1:k - 1]
        i = k


def scope_selector(sel: str) -> str:
    parts = [p.strip() for p in sel.split(",") if p.strip()]
    out = []
    for p in parts:
        if p.startswith(":root"):
            out.append(SCOPE + p[5:])
        elif p.startswith("html"):
            out.append(SCOPE + p[4:])
        else:
            out.append(f"{SCOPE} {p}")
    return ",".join(out)


def convert_block(css: str) -> list[str]:
    out = []
    for head, body in split_rules(css):
        if head.startswith("@media") or head.startswith("@supports"):
            inner = convert_block(body)
            if inner:
                out.append(head + "{" + "".join(inner) + "}")
            continue
        if head.startswith("@"):
            continue
        decls, any_change = [], False
        text_clip = "background-clip:text" in body.replace(" ", "")
        for d in body.split(";"):
            if ":" not in d:
                continue
            prop, val = d.split(":", 1)
            res = convert_decl(prop, val, text_clip)
            if res is not None:
                decls.append(f"{prop.strip()}:{res[0].strip()}")
                any_change = any_change or res[1]
        # Repete também as regras de cor que não mudam: mantém a ordem da cascata.
        if decls and (any_change or any(not d.startswith(KEEP_PROPS) for d in decls)):
            out.append(scope_selector(head) + "{" + ";".join(decls) + "}")
    return out


def main():
    css = re.sub(r"/\*.*?\*/", "", SRC.read_text(encoding="utf-8"), flags=re.S)
    rules = convert_block(css)
    extra = (f'html[data-theme="dark"]{{color-scheme:dark;--bg:#121214;--paper:#1c1c1f;--ink:#ececef;--muted:#a2a2aa;'
             f'--line:#2e2e33;--soft:#232326}}\n')
    manual = (ROOT / "tools" / "tema_escuro_ajustes.css")
    tail = manual.read_text(encoding="utf-8") if manual.is_file() else ""
    OUT.write_text("/* Gerado por tools/gerar_tema_escuro.py a partir do styles.css: não edite à mão.\n"
                   "   Ajustes manuais ficam em tools/tema_escuro_ajustes.css. */\n"
                   + extra + "\n".join(rules) + "\n" + tail, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(rules)} regras")


if __name__ == "__main__":
    main()

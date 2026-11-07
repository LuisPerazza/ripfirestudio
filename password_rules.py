"""Recusa senha fácil de adivinhar: abc12345, Senha@123, qwerty2024, o próprio
nome ou e-mail, e as variações com troca de letra por número (s3nh4) ou com
maiúscula e símbolo no fim.

Como funciona: a senha é normalizada (minúsculas, 4→a, 3→e, 0→o, @→a, $→s...)
e dela se tiram os pedaços previsíveis: sequências (abc, 123, 987), linhas do
teclado (qwerty, asdf), repetições (aaaa, 1212), anos e datas, palavras comuns
de senha e os dados da própria pessoa. O que sobra é a parte imprevisível; se
sobra pouco, a senha é fraca. A mesma regra roda no navegador (static/senha.js)
só pra avisar enquanto digita; quem decide é o servidor.
"""
from __future__ import annotations

import re
import unicodedata

LEET = str.maketrans({"4": "a", "@": "a", "3": "e", "1": "i", "!": "i", "0": "o", "$": "s", "5": "s",
                      "7": "t", "+": "t", "9": "g", "8": "b"})

# Palavras que aparecem em senha vazada no Brasil e no mundo (já normalizadas).
COMMON_WORDS = sorted({
    "senha", "password", "passwd", "pass", "admin", "administrador", "root", "user", "usuario", "login",
    "teste", "test", "qwerty", "asdf", "zxcv", "abc", "abcd", "iloveyou", "teamo", "amor", "amore", "love",
    "deus", "jesus", "cristo", "deusefiel", "brasil", "brazil", "flamengo", "corinthians", "palmeiras",
    "saopaulo", "santos", "gremio", "inter", "vasco", "botafogo", "cruzeiro", "fluminense", "mengao",
    "futebol", "familia", "felicidade", "princesa", "anjo", "gatinha", "gatinho", "lindo", "linda",
    "minhasenha", "mudar", "trocar", "acesso", "entrar", "segredo", "secret", "welcome", "bemvindo",
    "dragon", "monkey", "master", "shadow", "sunshine", "football", "baseball", "superman", "batman",
    "pokemon", "naruto", "starwars", "matrix", "letmein", "trustno", "hello", "ola", "oi", "default",
    "changeme", "camisa", "camiseta", "loja", "studio", "estudio", "dtf", "ripfire", "joinville",
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
    "novembro", "dezembro", "maria", "jose", "joao", "ana", "pedro", "lucas", "gabriel", "rafael",
    "impressao", "impressora", "prensa", "producao", "servidor", "equipe", "funcionario", "cliente",
    "empresa", "trabalho", "escritorio", "computador", "internet", "sistema", "segura", "seguranca",
    "forte", "nova", "minha", "meu", "casa", "vida", "sucesso", "vitoria", "estampa", "personalizado",
})
KEYBOARD_ROWS = ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm", "1qaz2wsx3edc4rfv", "qazwsxedc"]
ALPHABET = "abcdefghijklmnopqrstuvwxyz"
DIGITS = "0123456789"
# Só letras sem acento, números e estes símbolos: sem espaço, _ ou -.
ALLOWED = re.compile(r"[A-Za-z0-9!@#$%]+")
BAD_CHARS = "Use só letras (sem acento), números e os símbolos ! @ # $ % (sem espaço, _ ou -)."


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return text.lower()


def _norm(text: str) -> str:
    return _plain(text).translate(LEET)


def _runs(s: str, min_len: int) -> list[tuple[int, int]]:
    """Trechos previsíveis: sequência pra frente/trás no alfabeto, nos números
    ou numa linha do teclado, e caractere repetido."""
    spans = []
    lines = [ALPHABET, DIGITS] + KEYBOARD_ROWS
    lines += [x[::-1] for x in lines]
    i = 0
    while i < len(s):
        best = 1
        for line in lines:
            pos = line.find(s[i])
            if pos < 0:
                continue
            n = 1
            while i + n < len(s) and pos + n < len(line) and s[i + n] == line[pos + n]:
                n += 1
            best = max(best, n)
        n = 1
        while i + n < len(s) and s[i + n] == s[i]:
            n += 1
        best = max(best, n)
        if best >= min_len:
            spans.append((i, i + best))
            i += best
        else:
            i += 1
    return spans


def _repeated_chunk(s: str) -> bool:
    """1212, abcabc, senhasenha: um pedaço curto repetido."""
    for size in range(1, len(s) // 2 + 1):
        if len(s) % size == 0 and s[:size] * (len(s) // size) == s:
            return True
    return False


def _cut_runs(s: str) -> str:
    out = []
    for part in s.split():
        kept, last = [], 0
        for a, b in _runs(part, 3):
            kept.append(part[last:a])
            last = b
        kept.append(part[last:])
        piece = "".join(kept)
        if not (len(piece) >= 2 and _repeated_chunk(piece)):
            out.append(piece)
    return " ".join(out)


def _strip(low: str, words: list[str]) -> str:
    """O que sobra da senha depois de tirar o previsível."""
    s = re.sub(r"(19|20)\d\d", " ", low)                      # anos
    s = re.sub(r"\d{1,2}[/.\-]\d{1,2}([/.\-]\d{2,4})?", " ", s)  # datas
    s = re.sub(r"[^a-z0-9@$!+]", " ", s)
    s = _cut_runs(s)
    s = s.translate(LEET)
    for w in sorted({w for w in words if len(w) >= 3}, key=len, reverse=True):
        s = s.replace(w, " ")
    s = re.sub(r"[^a-z0-9]", " ", s)  # símbolo sozinho não dá força
    return _cut_runs(s).replace(" ", "")


def password_problem(password: str, personal: tuple[str, ...] | list[str] = (), strong: bool = False) -> str | None:
    """Motivo (em português) pra recusar a senha, ou None se ela serve.
    personal: nome, e-mail, usuário... strong=True pede mais (equipe)."""
    raw = password or ""
    if not ALLOWED.fullmatch(raw):
        return BAD_CHARS
    low = _plain(raw)
    norm = _norm(raw)
    if len(set(low)) < (6 if strong else 4):
        return "A senha repete muitos caracteres. Misture letras e números diferentes."
    if _repeated_chunk(low) or _repeated_chunk(norm):
        return "A senha é um pedaço repetido (tipo 1212 ou abcabc). Escolha outra."
    personal_words: list[str] = []
    for item in personal:
        for token in re.split(r"[^a-z0-9]+", _norm(str(item or "").split("@")[0])):
            if len(token) >= 3:
                personal_words.append(token)
    for w in personal_words:
        if w in norm:
            return "A senha não pode ter o seu nome, usuário ou e-mail."
    rest = _strip(low, COMMON_WORDS + personal_words)
    need = 6 if strong else 4
    if len(rest) < need or len(set(rest)) < (4 if strong else 3):
        return ("Senha fácil de adivinhar: evite sequências (abc, 123, qwerty), datas, palavras como "
                "\"senha\" ou \"admin\" e trocas tipo s3nh4. Junte palavras que não combinam, com maiúsculas e um símbolo (ex.: GatoTijoloNuvem#7).")
    return None

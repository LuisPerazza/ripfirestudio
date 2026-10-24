"""Arquivos do Ripfire no Supabase Storage.

No Render o disco é temporário: tudo que o app grava some a cada deploy ou
reinício. Com SUPABASE_URL e SUPABASE_SERVICE_KEY definidos, cada arquivo que
o app grava (artes dos pedidos, catálogo, mockups, biblioteca, IA) também
sobe pro Storage, e quem precisar de um arquivo que não está mais no disco
baixa de volta de lá. O disco do servidor vira só um cache.

O nome do arquivo no Storage é o caminho dele dentro da pasta do projeto
(ex.: orders/241024-0001__Maria/ARTE_PRODUCAO.png). O Storage não aceita
acentos no nome, então cada parte do caminho passa por encode_part().

Só usa a biblioteca padrão (urllib): não precisa instalar nada a mais.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# O Supabase pede envio em partes (TUS) acima de 6 MB, em blocos de 6 MB.
TUS_CHUNK = 6 * 1024 * 1024
TIMEOUT = 120


class StorageError(RuntimeError):
    pass


def encode_part(name: str) -> str:
    """Uma parte do caminho sem acento nem espaço: "João" -> "Jo~C3~A3o"."""
    return urllib.parse.quote(name, safe="-_.").replace("%", "~")


def decode_part(name: str) -> str:
    return urllib.parse.unquote(name.replace("~", "%"))


class SupabaseStorage:
    def __init__(self, url: str, key: str, bucket: str, base: Path):
        self.url = url.rstrip("/")
        self.key = key
        self.bucket = bucket
        self.base = base.resolve()

    # ---------- caminhos ----------
    def key_for(self, path: Path | str) -> str:
        rel = Path(path).resolve().relative_to(self.base)
        return "/".join(encode_part(p) for p in rel.parts)

    def _object_url(self, kind: str, key: str = "") -> str:
        return f"{self.url}/storage/v1/object/{kind}{self.bucket}" + (f"/{key}" if key else "")

    # ---------- HTTP ----------
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        # Chave nova (sb_secret_...) vai só no "apikey"; a antiga (service_role,
        # um JWT que começa com eyJ) vai também no Authorization.
        auth = {} if self.key.startswith("sb_") else {"Authorization": f"Bearer {self.key}"}
        return {"apikey": self.key, **auth, **(extra or {})}

    def _request(self, method: str, url: str, body: Any = None, headers: dict[str, str] | None = None,
                 ok: tuple[int, ...] = (200,), timeout: float = TIMEOUT) -> tuple[int, dict[str, str], bytes]:
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        hdrs = self._headers(headers)
        if isinstance(body, (dict, list)):
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            payload = e.read()
            if e.code in ok:
                return e.code, dict(e.headers), payload
            raise StorageError(f"{method} {url.split('?')[0]} -> {e.code}: {payload[:300].decode('utf-8', 'replace')}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise StorageError(f"{method} {url.split('?')[0]} falhou: {e}")

    # ---------- bucket ----------
    def ensure_bucket(self):
        """Cria o bucket (privado) se ainda não existir."""
        status, _, _ = self._request("GET", f"{self.url}/storage/v1/bucket/{self.bucket}", ok=(200, 400, 404))
        if status == 200:
            return
        self._request("POST", f"{self.url}/storage/v1/bucket",
                      {"id": self.bucket, "name": self.bucket, "public": False}, ok=(200, 201, 409))

    # ---------- envio ----------
    def upload(self, path: Path | str, key: str | None = None):
        path = Path(path)
        key = key or self.key_for(path)
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if size <= TUS_CHUNK:
            self._request("POST", self._object_url("", key), path.read_bytes(),
                          {"Content-Type": ctype, "x-upsert": "true", "Cache-Control": "max-age=3600"})
        else:
            self._upload_tus(path, key, size, ctype)

    def _upload_tus(self, path: Path, key: str, size: int, ctype: str):
        """Envio em partes de 6 MB (protocolo TUS). Se uma parte falhar,
        pergunta ao Storage até onde chegou e continua dali."""
        b64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731
        meta = ",".join(f"{k} {b64(v)}" for k, v in {
            "bucketName": self.bucket, "objectName": key, "contentType": ctype, "cacheControl": "3600",
        }.items())
        endpoint = f"{self.url}/storage/v1/upload/resumable"
        _, headers, _ = self._request("POST", endpoint, b"", {
            "Tus-Resumable": "1.0.0", "Upload-Length": str(size), "Upload-Metadata": meta,
            "x-upsert": "true", "Content-Length": "0",
        }, ok=(201,))
        location = headers.get("Location") or headers.get("location")
        if not location:
            raise StorageError("o Storage não devolveu o endereço do envio em partes")
        location = urllib.parse.urljoin(endpoint, location)
        offset, failures = 0, 0
        with path.open("rb") as f:
            while offset < size:
                f.seek(offset)
                chunk = f.read(TUS_CHUNK)
                try:
                    _, h, _ = self._request("PATCH", location, chunk, {
                        "Tus-Resumable": "1.0.0", "Upload-Offset": str(offset),
                        "Content-Type": "application/offset+octet-stream",
                    }, ok=(204,))
                    offset = int(h.get("Upload-Offset") or h.get("upload-offset") or offset + len(chunk))
                    failures = 0
                except StorageError:
                    failures += 1
                    if failures > 5:
                        raise
                    time.sleep(2 * failures)
                    _, h, _ = self._request("HEAD", location, None, {"Tus-Resumable": "1.0.0"}, ok=(200, 204))
                    offset = int(h.get("Upload-Offset") or h.get("upload-offset") or offset)

    def upload_dir(self, folder: Path | str):
        for p in sorted(Path(folder).iterdir()):
            if p.is_file():
                self.upload(p)

    # ---------- leitura ----------
    def download(self, path: Path | str, key: str | None = None) -> bool:
        """Baixa pro caminho local. False se o arquivo não existe no Storage."""
        path = Path(path)
        key = key or self.key_for(path)
        req = urllib.request.Request(self._object_url("authenticated/", key), headers=self._headers())
        tmp = path.with_name(path.name + ".baixando")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tmp.open("wb") as f:
                    while chunk := resp.read(1024 * 1024):
                        f.write(chunk)
        except urllib.error.HTTPError as e:
            tmp.unlink(missing_ok=True)
            if e.code in (400, 404):  # o Storage responde 400 "Object not found"
                return False
            raise StorageError(f"download {key} -> {e.code}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            tmp.unlink(missing_ok=True)
            raise StorageError(f"download {key} falhou: {e}")
        tmp.replace(path)
        return True

    def read_head(self, key: str, length: int = 65536) -> bytes:
        """Só o começo do arquivo (pro tamanho da imagem, sem baixar 500 MB)."""
        req = urllib.request.Request(self._object_url("authenticated/", key),
                                     headers=self._headers({"Range": f"bytes=0-{length - 1}"}))
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read(length)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return b""
            raise StorageError(f"leitura {key} -> {e.code}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise StorageError(f"leitura {key} falhou: {e}")

    def list_files(self, folder: Path | str | None = None, prefix_key: str | None = None) -> list[dict[str, Any]]:
        """Arquivos direto dentro da pasta: [{"name", "key", "size"}]."""
        prefix = prefix_key if prefix_key is not None else self.key_for(folder)  # type: ignore[arg-type]
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            _, _, raw = self._request("POST", f"{self.url}/storage/v1/object/list/{self.bucket}", {
                "prefix": prefix, "limit": 1000, "offset": offset,
                "sortBy": {"column": "name", "order": "asc"},
            })
            items = json.loads(raw or b"[]")
            for it in items:
                if it.get("id") is None:  # subpasta
                    continue
                out.append({
                    "name": decode_part(it["name"]),
                    "key": f"{prefix}/{it['name']}" if prefix else it["name"],
                    "size": int((it.get("metadata") or {}).get("size") or 0),
                })
            if len(items) < 1000:
                return out
            offset += 1000

    def list_folders(self, prefix_key: str) -> list[str]:
        """Nomes (codificados) das subpastas direto dentro de prefix_key."""
        _, _, raw = self._request("POST", f"{self.url}/storage/v1/object/list/{self.bucket}", {
            "prefix": prefix_key, "limit": 1000, "offset": 0,
        })
        return [it["name"] for it in json.loads(raw or b"[]") if it.get("id") is None]

    def download_dir(self, folder: Path | str) -> int:
        """Baixa os arquivos da pasta que faltam no disco. Devolve quantos existem no Storage."""
        folder = Path(folder)
        files = self.list_files(folder)
        for f in files:
            dest = folder / f["name"]
            if not dest.exists():
                self.download(dest, f["key"])
        return len(files)

    # ---------- movimentar / apagar ----------
    def move(self, source_key: str, dest_key: str):
        # O move não sobrescreve: apaga o destino antes (sobra de tentativa anterior).
        self.delete_keys([dest_key])
        self._request("POST", f"{self.url}/storage/v1/object/move", {
            "bucketId": self.bucket, "sourceKey": source_key, "destinationKey": dest_key,
        })

    def delete_keys(self, keys: list[str]):
        for i in range(0, len(keys), 1000):
            part = keys[i:i + 1000]
            if part:
                self._request("DELETE", self._object_url("", ""), {"prefixes": part}, ok=(200, 400, 404))

    def delete(self, *paths: Path | str):
        self.delete_keys([self.key_for(p) for p in paths])

    def delete_dir(self, folder: Path | str | None = None, prefix_key: str | None = None):
        prefix = prefix_key if prefix_key is not None else self.key_for(folder)  # type: ignore[arg-type]
        self.delete_keys([f["key"] for f in self.list_files(prefix_key=prefix)])

    # ---------- links assinados ----------
    def signed_download_url(self, key: str, expires: int = 3600, filename: str | None = None) -> str:
        _, _, raw = self._request("POST", f"{self.url}/storage/v1/object/sign/{self.bucket}/{key}",
                                  {"expiresIn": expires})
        signed = json.loads(raw)["signedURL"]
        url = f"{self.url}/storage/v1{signed}"
        if filename:
            url += "&download=" + urllib.parse.quote(filename)
        return url

    def signed_upload_url(self, key: str) -> str:
        """Endereço pro NAVEGADOR enviar um arquivo direto pro Storage (PUT),
        sem passar pelo servidor do site. Vale 2 horas e só pra esse arquivo."""
        _, _, raw = self._request("POST", f"{self.url}/storage/v1/object/upload/sign/{self.bucket}/{key}",
                                  {}, {"x-upsert": "true"})
        return f"{self.url}/storage/v1{json.loads(raw)['url']}"


def from_env(base: Path) -> SupabaseStorage | None:
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        return None
    return SupabaseStorage(url, key, os.getenv("SUPABASE_BUCKET", "ripfire").strip() or "ripfire", base)

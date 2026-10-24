#!/bin/bash
# Backup diário da VPS: banco (cópia consistente, com o site rodando) e os
# arquivos (artes, mockups, pedidos, configuração). Guarda os últimos 14 dias
# em /var/backups/ripfire. Instalação no DEPLOY.md, passo 9.
set -euo pipefail
APP=/opt/ripfire
DEST=/var/backups/ripfire
STAMP=$(date +%F)
mkdir -p "$DEST"

"$APP/.venv/bin/python" -c "import sqlite3, sys; s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" \
    "$APP/data/studio.db" "$DEST/studio_$STAMP.db"

# --ignore-failed-read: o .env é opcional (sem IA e sem Google ele não existe).
tar -czf "$DEST/arquivos_$STAMP.tar.gz" --ignore-failed-read -C "$APP" \
    --exclude='data/studio.db*' --exclude='data/digital_cache' \
    data media orders PRODUCAO_PAGA SomenteDTFs config.json .env

find "$DEST" -type f -mtime +14 -delete

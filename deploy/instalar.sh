#!/usr/bin/env bash
# Instala CRISTOPHER en modo servidor en un Ubuntu 24.04 (plan.md, fase B).
# Idempotente: se puede relanzar tras cada despliegue de código.
#   sudo bash ~cristopher/Cristopher/deploy/instalar.sh
# Requisitos: el código ya copiado en /home/cristopher/Cristopher (git archive | tar).
set -euo pipefail

USUARIO=cristopher
DIR=/home/$USUARIO/Cristopher
como() { sudo -u "$USUARIO" -H bash -lc "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "ERROR: ejecútalo con sudo." >&2; exit 1; }
[ -f "$DIR/requirements-servidor.txt" ] || { echo "ERROR: no encuentro el código en $DIR." >&2; exit 1; }

echo "== Paquetes del sistema"
apt-get update -q
apt-get install -y -q ca-certificates curl sqlite3 unattended-upgrades

echo "== Node.js 22 LTS (el de Ubuntu 24.04 es demasiado viejo para Baileys)"
if ! node --version 2>/dev/null | grep -q '^v22\.'; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y -q nodejs
fi
node --version

echo "== uv + Python 3.11 (CLAUDE.md: 3.11 obligatorio; Ubuntu 24.04 trae 3.12)"
como 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'
como "cd $DIR && ~/.local/bin/uv venv -p 3.11 --allow-existing .venv"
como "cd $DIR && ~/.local/bin/uv pip install -p .venv/bin/python -r requirements-servidor.txt"

echo "== Chromium para navegar_leer"
"$DIR/.venv/bin/playwright" install-deps chromium
como "cd $DIR && .venv/bin/playwright install chromium"

echo "== Dependencias del servicio de WhatsApp"
como "cd $DIR/whatsapp && npm ci --omit=dev --no-audit --no-fund"

echo "== Unidad systemd + backup diario"
install -m 644 "$DIR/deploy/cristopher.service" /etc/systemd/system/cristopher.service
systemctl daemon-reload
systemctl enable cristopher.service
chmod 755 "$DIR/deploy/backup.sh"
como 'mkdir -p ~/backups'
como "(crontab -l 2>/dev/null | grep -v 'deploy/backup.sh'; echo '30 4 * * * $DIR/deploy/backup.sh >> \$HOME/backups/backup.log 2>&1') | crontab -"

echo "OK. Arranca/reinicia con: sudo systemctl restart cristopher"

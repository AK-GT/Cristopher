#!/usr/bin/env bash
# Backup diario de data/ (plan.md, fase B). Lo programa deploy/instalar.sh en el crontab.
# Las bases SQLite se copian con `.backup` (copia coherente aunque CRISTOPHER esté
# escribiendo); el resto, con tar. Fuera: adjuntos de WhatsApp y voces (se regeneran).
# Ojo: es una copia EN ESTA MISMA MÁQUINA; no sobrevive a perder la instancia.
set -euo pipefail

DATA=/home/cristopher/Cristopher/data
DEST=$HOME/backups
CONSERVAR=7
FECHA=$(date +%F)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$DEST"
for db in "$DATA"/*.db; do
    [ -f "$db" ] && sqlite3 "$db" ".backup '$TMP/$(basename "$db")'"
done
# Dos pasadas: los --exclude de tar valen para TODO el archivo, así que con un solo
# comando el '*.db' se llevaría también las copias coherentes de $TMP.
TAR="$TMP/cristopher-$FECHA.tar"
tar -cf "$TAR" --exclude='./*.db' --exclude='./whatsapp/media' --exclude='./voice' \
    --exclude='./browser_profile' --exclude='*.log' -C "$DATA" .
(cd "$TMP" && tar -rf "$TAR" --exclude="$(basename "$TAR")" ./*.db)
gzip -c "$TAR" > "$DEST/cristopher-$FECHA.tar.gz"
ls -1t "$DEST"/cristopher-*.tar.gz | tail -n +$((CONSERVAR + 1)) | xargs -r rm --
echo "$(date '+%F %T') backup ok: $DEST/cristopher-$FECHA.tar.gz ($(du -h "$DEST/cristopher-$FECHA.tar.gz" | cut -f1))"

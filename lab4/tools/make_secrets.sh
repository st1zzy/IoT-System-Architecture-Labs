#!/bin/sh
# Генерирует случайные пароли пользователей и файл паролей mosquitto (хэши PBKDF2-SHA512).
# Выполняется контейнером secrets при первом запуске; результаты не попадают в Git.
set -eu
[ -f /secrets/creds.env ] && [ -f /mosquitto/config/passwd ] && exit 0
: > /secrets/creds.env
rm -f /mosquitto/config/passwd
for u in device01 device02 gateway01 svc-ingest dashboard; do
  p=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 20)
  mosquitto_passwd -b $( [ -f /mosquitto/config/passwd ] || echo -c ) /mosquitto/config/passwd "$u" "$p"
  echo "PASS_$(echo "$u" | tr 'a-z-' 'A-Z_')=$p" >> /secrets/creds.env
done
chmod 0700 /mosquitto/config/passwd
chown 1883:1883 /mosquitto/config/passwd
echo "secrets: пользователи device01, device02, gateway01, svc-ingest, dashboard"

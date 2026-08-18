#!/bin/sh
# Deploy-хук certbot: раскладывает свежий сертификат туда, где его ищет nginx,
# и перечитывает конфиг без даунтайма.
#
# Установка на прод (один раз, от root):
#   install -m 755 ~/fastapi/scripts/certbot-deploy-hook.sh \
#           /etc/letsencrypt/renewal-hooks/deploy/upstat-nginx.sh
#
# Certbot запускает всё из renewal-hooks/deploy/ ТОЛЬКО после успешного
# продления и передаёт $RENEWED_LINEAGE = /etc/letsencrypt/live/<имя>.
#
# Зачем копии, а не симлинки на live/: контейнер nginx не монтирует
# /etc/letsencrypt, а ssl_certificate читается по путям внутри контейнера.
# Копирование тут — не костыль, а способ не расширять права контейнера.
set -eu

DEPLOY_DIR="/home/ubuntu/fastapi"
NGINX_CONTAINER="fastapi-nginx-1"

# Хук вызывается для каждого продлённого сертификата. Наш — только upstat.pro;
# для чужих lineage молча выходим, иначе затрём боевой серт чужим.
case "${RENEWED_LINEAGE:-}" in
    */upstat.pro) ;;
    *) exit 0 ;;
esac

TARGET="${DEPLOY_DIR}/ssl/upstat.pro"

# Пишем во временный файл и подменяем атомарно: иначе nginx может успеть
# перечитать конфиг в момент, когда fullchain уже новый, а privkey ещё старый.
cp "${RENEWED_LINEAGE}/fullchain.pem" "${TARGET}/fullchain1.pem.new"
cp "${RENEWED_LINEAGE}/privkey.pem"   "${TARGET}/privkey1.pem.new"
chmod 644 "${TARGET}/fullchain1.pem.new"
chmod 600 "${TARGET}/privkey1.pem.new"
mv -f "${TARGET}/fullchain1.pem.new" "${TARGET}/fullchain1.pem"
mv -f "${TARGET}/privkey1.pem.new"   "${TARGET}/privkey1.pem"

# HUP = graceful reload: мастер перечитывает конфиг и сертификаты, старые
# воркеры дорабатывают текущие запросы. Живые WebSocket-соединения
# (/voice-training/, /voice-assistant/) не рвутся. Контейнер НЕ пересоздаётся,
# IP backend не меняется — грабля с кэшем апстрима не срабатывает.
docker kill -s HUP "${NGINX_CONTAINER}"

echo "deploy-hook: сертификат upstat.pro обновлён, nginx перечитал конфиг"

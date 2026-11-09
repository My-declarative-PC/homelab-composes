# Xray VLESS + XHTTP + TLS через Traefik

Клиент подключается к `xray.${DOMAIN}:443`, Traefik завершает TLS и передаёт XHTTP по h2c на внутренний порт Xray 8080. Порты Xray на хост не публикуются. REALITY больше не используется.

## Первая установка

Нужны Docker Compose, Python 3 и работающий Traefik из `available/traefik` с entrypoint `websecure`, resolver `letsencrypt` и внешней сетью `traefik`. Для Restic нужна внешняя сеть `apprise_notification` и общий шаблон из репозитория.

1. Создайте DNS-запись `xray.ваш-домен` на IP сервера. В Cloudflare оставьте **DNS only — серое облако**. Если IPv6 не настроен, не добавляйте AAAA.
2. Из `available/xray` выполните `cp template.env .env` и `chmod 600 .env`. Укажите базовый `DOMAIN` без `xray.` и задайте случайный `RESTIC_PASSWORD`.
3. Выполните:

   ```bash
   docker compose config --quiet
   ./setup.sh
   docker compose up -d
   docker compose ps
   ```

Генерируются `${CONTAINER_NAME}_data/config/config.json`, `${CONTAINER_NAME}_data/client/client.json` и `${CONTAINER_NAME}_data/client/import-link.txt`. Перенесите клиентский JSON на устройство с Xray или импортируйте ссылку в клиент с поддержкой XHTTP и TLS. Клиент проверяет сертификат; не включайте `allowInsecure`.

## Существующая установка

**Старый REALITY JSON не подходит для нового Compose. Не запускайте новый стек со старой конфигурацией без её адаптации.** `setup.sh` не перезаписывает существующие файлы.

Для сохранения доступа сначала сделайте защищённую копию данных и остановите старый Xray. В серверном JSON сохраните UUID и путь XHTTP, смените порт inbound на 8080, `streamSettings.security` на `none` и удалите `realitySettings`. В клиентском JSON укажите адрес `xray.${DOMAIN}`, порт 443, `security: tls`, `tlsSettings.serverName` с тем же доменом и включённую проверку сертификата; удалите `realitySettings`, сохраните UUID и путь. Поставьте клиенту режим XHTTP `packet-up` и обновите импорт-ссылку: `security=tls`, `sni=xray.${DOMAIN}`, `host=xray.${DOMAIN}`, без `pbk` и `sid`. Новую конфигурацию обязательно проверьте перед запуском.

## Проверки и обслуживание

```bash
docker compose run --rm --no-deps app run -test -c /etc/xray/config.json
docker compose logs --tail=50 app
```

Watchtower для Xray отключён. Ограничения контейнера, закрытый каталог данных и резервное копирование Restic сохранены. Restic останавливает `${CONTAINER_NAME}_main` на время копирования, разрывая активные подключения, и сохраняет весь каталог данных; `.env` и пароль храните отдельно.

Тесты из корня репозитория (Docker CLI с Compose, без запуска контейнеров):

```bash
bash -n available/xray/setup.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s available/xray/tests -v
```

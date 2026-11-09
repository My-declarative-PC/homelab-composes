# Xray VLESS + XHTTP + REALITY — чистая установка

Образ `ghcr.io/xtls/xray-core:latest`, TCP/443, без панели управления. По умолчанию REALITY target/SNI: **www.bing.com** вместо www.microsoft.com. Скрипт поддерживает вывод `xray x25519` вида `PrivateKey:` и `Password (PublicKey):`.

`latest` — изменяемый тег и может указывать на предварительный выпуск (pre-release). Он не обновляется автоматически у уже работающего контейнера. Для обновления: `docker compose pull && docker compose up -d`. Проверяйте новости о совместимости клиента и сервера перед обновлением.

## Требования

Linux VPS с публичным IPv4, Docker Engine + Compose v2, Python 3, curl. Откройте TCP/443 в firewall и панели VPS. Порт не должен быть занят другим сервисом.

В `.env` задаются `CONTAINER_NAME` (имя контейнера будет `${CONTAINER_NAME}_main`), `XRAY_PORT` (внешний TCP-порт, по умолчанию 443), `TARGET_HOST` и `SERVER_IP`. Настройки читает Docker Compose; переменные окружения имеют приоритет над `.env`. Внутри контейнера Xray всегда слушает 443. При выборе другого внешнего порта он также записывается в клиентскую конфигурацию и ссылку.

## Установка

```bash
cp template.env .env
# Отредактируйте .env перед генерацией конфигурации.
chmod +x setup.sh
./setup.sh
docker compose up -d
docker compose ps
```

Автоматически создаются `${CONTAINER_NAME}_data/config/config.json` (секретный ключ сервера), `${CONTAINER_NAME}_data/client/client.json` (Xray-клиент с SOCKS5 на 127.0.0.1:10808), `${CONTAINER_NAME}_data/client/import-link.txt` (импорт в клиент с поддержкой XHTTP + REALITY). В Happ удалите старое подключение и импортируйте новую ссылку.

Если автоопределение IP неверно:

```bash
SERVER_IP=203.0.113.10 ./setup.sh
```

(Адрес выше — документационный пример, не адрес реального сервера.)

Если нужен другой target, перед первым запуском используйте `TARGET_HOST=домен ./setup.sh` и проверьте реальную совместимость TLS и сетевого расположения. Не меняйте только один из serverNames/target/SNI: параметры клиента и сервера должны совпадать.

## Проверки

```bash
docker compose run --rm --no-deps app run -test -c /etc/xray/config.json
docker compose logs --tail=50 app
sudo ss -lntp | grep ':443'
curl -Iv --connect-timeout 10 https://www.bing.com/
```

Для сетевой диагностики временно измените `${CONTAINER_NAME}_data/config/config.json` -> `log.loglevel` с `none` на `warning`, затем `docker compose up -d --force-recreate` и `docker compose logs --since=2m app`. Не публикуйте исходный JSON и импорт-ссылку — в них находятся секреты подключения. Возвращайте `none` после тестирования.

Для проверки клиентского JSON на устройстве с установленным Xray запустите Xray с этим файлом и проверьте SOCKS5-прокси:

```bash
curl --proxy socks5h://127.0.0.1:10808 https://ifconfig.me
```

`www.bing.com` — только стартовый target, не гарантирует корректность соединения для всех VPS. Клиенты Happ могут по-разному поддерживать версию XHTTP и параметры импорта. Успешная проверка JSON не гарантирует успешного TLS-рукопожатия или обхода DPI. Провайдер видит IP VPS и метаданные трафика; полной незаметности гарантировать нельзя.

## Чистая переустановка

В тестовом каталоге с прежними ключами сначала остановите контейнеры `docker compose down`, затем используйте **новую распакованную директорию**, а не смешивайте файлы разных сборок. `setup.sh` намеренно не перезаписывает существующие серверные и клиентские файлы. Доступ к выбранному порту должен быть только у одного работающего контейнера.

## Перенос старой установки

Перед обновлением остановите старый стек с его `docker-compose.yml`. После обновления задайте `.env` и перенесите старые каталоги `config/` и `client/` внутрь `${CONTAINER_NAME}_data/`, не меняя содержимое файлов. Родительскому каталогу данных и `client/` задайте права `700`, `config/` — `755`, серверному JSON — `644`, клиентским файлам — `600`. Выполните проверку конфигурации и запустите новый стек. `setup.sh` при таком переносе запускать не нужно: ключи сохраняются, а обнаружение старого конфига останавливает новую генерацию. Не меняйте внешний порт при переносе без соответствующего обновления настроек клиента.

Документация:
- https://xtls.github.io/en/config/transports/reality.html
- https://xtls.github.io/en/config/transports/xhttp.html
- https://github.com/XTLS/Xray-examples

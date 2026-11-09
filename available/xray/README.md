# Xray VLESS + XHTTP + REALITY — чистая установка

Образ `ghcr.io/xtls/xray-core:latest`, TCP/443, без панели управления. По умолчанию REALITY target/SNI: **www.bing.com** вместо www.microsoft.com. Скрипт поддерживает вывод `xray x25519` вида `PrivateKey:` и `Password (PublicKey):`.

`latest` — изменяемый тег и может указывать на предварительный выпуск (pre-release). Он не обновляется автоматически у уже работающего контейнера. Для обновления: `docker compose pull && docker compose up -d`. Проверяйте новости о совместимости клиента и сервера перед обновлением.

## Требования

Linux VPS с публичным IPv4, Docker Engine + Compose v2, Python 3, curl. Откройте TCP/443 в firewall и панели VPS. Порт не должен быть занят другим сервисом.

## Установка

```bash
chmod +x setup.sh
./setup.sh
docker compose up -d
docker compose ps
```

Автоматически создаются `config/config.json` (секретный ключ сервера), `client/client.json` (Xray-клиент с SOCKS5 на 127.0.0.1:10808), `client/import-link.txt` (импорт в клиент с поддержкой XHTTP + REALITY). В Happ удалите старое подключение и импортируйте новую ссылку.

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

Для сетевой диагностики временно измените `config/config.json` -> `log.loglevel` с `none` на `warning`, затем `docker compose up -d --force-recreate` и `docker compose logs --since=2m app`. Не публикуйте исходный `config/config.json` и импорт-ссылку — в них находятся секреты подключения. Возвращайте `none` после тестирования.

Для проверки клиентского JSON на устройстве с установленным Xray запустите Xray с этим файлом и проверьте SOCKS5-прокси:

```bash
curl --proxy socks5h://127.0.0.1:10808 https://ifconfig.me
```

`www.bing.com` — только стартовый target, не гарантирует корректность соединения для всех VPS. Клиенты Happ могут по-разному поддерживать версию XHTTP и параметры импорта. Успешная проверка JSON не гарантирует успешного TLS-рукопожатия или обхода DPI. Провайдер видит IP VPS и метаданные трафика; полной незаметности гарантировать нельзя.

## Чистая переустановка

В тестовом каталоге с прежними ключами сначала остановите контейнеры `docker compose down`, затем используйте **новую распакованную директорию**, а не смешивайте файлы разных сборок. `setup.sh` намеренно не перезаписывает существующий `config/config.json`. Доступ к TCP/443 должен быть только у одного работающего контейнера.

Документация:
- https://xtls.github.io/en/config/transports/reality.html
- https://xtls.github.io/en/config/transports/xhttp.html
- https://github.com/XTLS/Xray-examples

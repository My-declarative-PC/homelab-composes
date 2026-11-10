# 3x-ui: VLESS + XHTTP через Cloudflare CDN и Traefik

Чистая тестовая установка без миграции. Единственный источник настроек Xray,
inbound и пользователей — **SQLite и интерфейс 3x-ui**. Генераторов серверного
JSON и отдельного контейнера Xray нет. Официальный образ закреплён на
`ghcr.io/mhsanaei/3x-ui:v3.11.0` (комплектный Xray `26.10.21`).

```text
клиент VLESS/XHTTP ─ TLS :443 ─ Cloudflare Proxied
                                      │ TLS :443, Full (strict)
                                      ▼
                               существующий Traefik
                                ├─ /api/* → h2c :8080 → 3x-ui / Xray → Интернет
                                └─ /*     → HTTP :8080 → Nginx → site/

администратор ─ SSH tunnel ─ 127.0.0.1:2053 хоста → :2053 контейнера → панель
Restic ─ stop xray_main → копирование SQLite → start xray_main; Apprise
```

**Cloudflare Proxied — обязательный штатный режим**, не только DNS challenge.
TLS клиент–Cloudflare и Cloudflare–Traefik — два разных соединения.
На inbound TLS выключен: внутри доверенной bridge-сети используется h2c.
Обычный HTTP/2 не требует включения gRPC. Реальная работа через CDN требует
приёмочных тестов ниже: локальная проверка не подтверждает настройки вашей зоны.

## Инфраструктура и Cloudflare

Сохраните структуру репозитория: `compose.yaml` расширяет
`../restic/restic-compose-base.yaml`. Нужны Docker Engine **28+** (старые версии
имели ограничения безопасности loopback-публикации), Compose v2+, работающие
`available/traefik/`, Restic-хранилище, Apprise и внешние сети:

```bash
docker network inspect traefik
docker network inspect apprise_notification
```

Не создавайте второй Traefik или Apprise. Сеть `traefik` должна содержать только
доверенные контейнеры: участники сети могут обращаться к панели и inbound напрямую.
SSH и доступ к Docker эквивалентны административному доступу к стеку.

### DNS и TLS

В Cloudflare Dashboard проверьте существующую запись, не переключайте её в DNS only:

| Параметр | Значение |
|---|---|
| Type / Name | `A` / `xray` в зоне `${DOMAIN}` |
| Origin IPv4 | `45.86.74.1` |
| Proxy status | **Proxied, оранжевое облако** |
| TTL | Auto |
| SSL/TLS encryption mode | **Full (strict)** |

`dig +short xray.ВАШ-ДОМЕН A` должен возвращать адреса Cloudflare, а не origin.
Проверьте также AAAA: не оставляйте некорректный альтернативный origin.
В Traefik уже настроены `websecure`, resolver `letsencrypt` и Cloudflare DNS challenge.
Токен `CLOUDFLARE_API_TOKEN` хранится в окружении **стека Traefik**, не здесь.
Origin должен иметь действительный сертификат для `xray.${DOMAIN}`. Сертификат
в 3x-ui/Xray устанавливать не нужно; проверку TLS на клиентах не отключайте.

### Кэш, WAF и HTTP/2

Доступа к Dashboard/API из этого репозитория нет: существующие правила зоны
автоматически не проверяются и не меняются. До клиентского теста проверьте:

1. **Caching → Cache Rules**: правило для hostname `xray.ВАШ-ДОМЕН` и path
   `/api/*`, действие **Cache eligibility: Bypass cache**. Выражение:
   `(http.host eq "xray.ВАШ-ДОМЕН" and starts_with(http.request.uri.path, "/api/"))`.
   Убедитесь, что последующие правила, Page Rules, Workers и Cache Everything
   не переопределяют bypass. Это относится и к GET, и к POST.
2. HTTP/2 на edge включён. Если доступен **HTTP/2 to Origin**, включите и проверьте
   по origin access log. Для `packet-up` HTTP/1.1 на участке CDN–origin не является
   само по себе ошибкой, но Traefik–Xray должен оставаться h2c.
3. **Security → Events**: сопоставьте ошибки клиента с Ray ID. Проверяйте WAF,
   rate limits, Bot Fight/Super Bot Fight и managed challenges. Xray не умеет
   проходить браузерный challenge. Не отключайте защиту всей зоны: исключение
   только для этого hostname и `/api/`, только для мешающего правила/продукта.
   Некоторые тарифы/продукты не поддерживают точечный Skip — это ограничение
   нужно решать в настройках продукта, а не сменой протокола или обходом CDN.
4. Не включайте преобразование URL/тела, принудительное переписывание path,
   буферизацию streaming-ответов или редиректы на XHTTP endpoint.

`packet-up` отправляет ограниченные POST и отдельный поток скачивания, а не
бесконечный streaming POST. Ограничения размера запроса и proxy read/idle timeout
Cloudflare зависят от тарифа и меняются; большие загрузки должны разбиваться
клиентом на XHTTP-пакеты. Начните со стандартных XHTTP/XMUX-настроек версии core.
Уменьшение времени жизни HTTP-соединения/XMUX может помочь при CDN timeout, но
параметры подбирайте по логам и повторному тесту. XHTTP не отменяет лимиты CDN.

### IP клиента и защита origin

В текущем `available/traefik/traefik.yml` **не задан** список
`forwardedHeaders.trustedIPs`. Глобально менять его этот стек не пытается.
Не включайте `forwardedHeaders.insecure` и не доверяйте произвольным XFF или
`CF-Connecting-IP`. Встроенная обработка доверенных прокси веб-панели 3x-ui
относится к её HTTP-серверу, не автоматически к Xray inbound.

В этой версии UI есть **Sockopt → Real client IP → Cloudflare**, который записывает
`streamSettings.sockopt.trustedXForwardedFor: ["CF-Connecting-IP"]`. Это список
**имён доверяемых заголовков, не allowlist IP прокси**. По умолчанию оставьте
этот preset **Off**, поле пустым: локальный core подтвердил, что XFF игнорируется,
а источником считается реальный peer (Traefik). Включать preset можно только
после ограничения источников запроса и проверки всей доверенной цепочки,
иначе прямой запрос способен подделать `CF-Connecting-IP`.
Здесь **не восстанавливается исходный IP в Xray**: статистика по UUID работает
независимо, IP в core — адрес Traefik. Не применяйте IP-limit/блокировки по нему.
Fail2ban отключён, `NET_ADMIN`/`NET_RAW` не нужны.

Безопасное дополнительное усиление (вручную, после проверки остальных сервисов):
привязать **только к двум router этого hostname** middleware Traefik `ipAllowList`
с актуальными IPv4/IPv6-сетями из `https://www.cloudflare.com/ips/`.
Используйте peer IP по умолчанию, **без `ipStrategy.depth`/XFF**; иначе можно
ошибочно довериться заголовку. Проверяйте запрос через CDN и отказ прямому
соединению, поддерживайте список при изменениях Cloudflare. На общем хосте это
безопаснее глобального firewall allowlist. Для защиты даже от доступа через
чужую Cloudflare-зону рассмотрите hostname-specific Authenticated Origin Pulls
с проверкой клиентского сертификата в Traefik (зависит от тарифа).
Firewall, общие настройки Traefik и DNS автоматически не изменяются.
До такого усиления прямой доступ к origin **не заблокирован**; клиентские профили
всё равно используют только Proxied hostname, не origin IP.

## Compose и данные

Сервисы: `app` (панель и её Xray), `site`, три штатных задания Restic.
Публичный router панели отсутствует. На хост публикуется только
`127.0.0.1:2053:2053`; Xray:8080 и Nginx:8080 остаются внутри сети.
Панель внутри контейнера должна слушать все интерфейсы, а не `127.0.0.1`.
`XUI_PORT=2053` — официальный runtime override, не меняйте порт через UI.
`XUI_ENABLE_FAIL2BAN=false` — официальный переключатель; `TZ` задаёт часовой пояс.
Административные пароли через environment не передаются.

Traefik labels явно указывают backend **8080/h2c**, не порт панели из EXPOSE образа:

| Router | Rule | Priority | Backend |
|---|---|---|---|
| xray | Host(`xray.${DOMAIN}`) && PathPrefix(`/api/`) | 100 | app:8080, h2c |
| xray-site | Host(`xray.${DOMAIN}`) | 10 | site:8080, http |

Путь `/api` без слеша относится к сайту. На `/api/*` нет BasicAuth, StripPrefix,
buffering или panel authentication. API панели обслуживается на **другом порту**.
Nginx не проксирует Xray. Его конфигурация и `site/` сохранены read-only.
Оба backend независимы; `depends_on` нужен только backup → app.
Все сервисы наследуют/имеют `restart: unless-stopped`, app/site — ротацию логов.
Имена router глобальны в Traefik: рассчитано на один такой стек.

```text
${CONTAINER_NAME}_data/
├── db/                       → /etc/x-ui/
│   ├── x-ui.db               # SQLite: администратор, пользователи, inbound, Hosts, настройки
│   └── ...                   # sidecars и собственные backup-файлы панели
└── backup_restore/           # staging восстановления, исключён из новых snapshot
```

`/app/bin/config.json` создаёт **сама 3x-ui** из SQLite. Он не монтируется и не
редактируется вручную; при пересоздании контейнера восстанавливается из БД.
Ни `/app/bin`, ни Xray-бинарники/geodata не закрепляются bind mount поверх образа:
обновление контейнера должно приносить согласованные версии. Пользовательские
изменения Xray template делайте в панели — они сохраняются в БД.
В этой схеме нет собственных сертификатов/ACME, внешних файлов routing или Lua.
Если позже добавите такие файлы, поместите их в `_data`, настройте mount и backup.

У app `cap_drop: ALL`, `no-new-privileges`, нет socket, host networking или
privileged. Root filesystem остаётся writable: панель пишет generated config в
`/app/bin`, обновляет core/geodata и использует служебные каталоги. Принудительный
non-root/read-only режим официального образа здесь не обещается; root без
capabilities проверен локальным тестом. Не используйте UI-обновление core/панели
для постоянного обновления: оно потеряется при пересоздании, обновляйте образ.
Restic по существующему шаблону имеет Docker socket: даже `:ro` даёт управление
Docker и фактически привилегии хоста. Это исключение относится только к Restic.

## Первоначальный запуск

Старые тестовые JSON/UUID не используются. Если старый стек был запущен,
остановите **только его** (`docker compose down` в `available/xray`) до замены.
Не удаляйте общие volumes/сети и не запускайте `docker system prune`.
Старые каталоги `config/`, `client/` и `_data/config`, `_data/client` новому стеку
не нужны; их можно удалить отдельно после проверки пути. Миграции нет.

```bash
cd available/xray
cp template.env .env
chmod 600 .env
# Отредактируйте .env: реальный DOMAIN и сильный RESTIC_PASSWORD.
docker compose config --quiet
docker compose up -d
docker compose ps
```

| Переменная | Значение |
|---|---|
| CONTAINER_NAME | `xray`, имена `${CONTAINER_NAME}_main`, `_site`, `_restic__*` |
| DOMAIN | Базовая зона **без** `https://`, `xray.`, порта и пути |
| RESTIC_PASSWORD | Непустой случайный пароль Restic; сохраните отдельно |
| TZ | `Europe/Moscow` по умолчанию |

`example.com` — только шаблон, не работающий домен вашего профиля. `.env`,
`*_data`, остатки `config/`/`client/` исключены из Git. Не выводите полный
`docker compose config` в публичные логи: там будет пароль Restic.

Docker создаёт отсутствующий bind-каталог db от root. Для защиты данных от
других пользователей хоста предпочтительно **до запуска** создать каталоги
с закрытыми правами (пример для стандартного CONTAINER_NAME=xray):

```bash
sudo install -d -o root -g root -m 700 xray_data xray_data/db
```

Для rootless/userns-remap согласуйте владельца с UID контейнера. Каталог db,
созданный обычным пользователем с mode 700, root контейнера с `cap_drop: ALL`
читать не сможет. Не решайте это `chmod 777`.

На первом старте сайт и панель готовы, но XHTTP вернёт 502 **до создания inbound**.
Это ожидаемо. Backup запускается по расписанию, не немедленно.

### Первый вход и безопасность

На рабочем компьютере:

```bash
ssh -N -L 127.0.0.1:2053:127.0.0.1:2053 USER@VPS
```

Откройте `http://127.0.0.1:2053/`. HTTP защищён SSH-туннелем; публичного TLS
для панели нет. При занятом локальном порту используйте `:12053:127.0.0.1:2053`
и адрес `http://127.0.0.1:12053/`.

В **v3.11.0** первая БД получает **случайные** логин и пароль, один раз в логах:

```bash
docker compose logs app --tail=100
docker compose exec app /app/x-ui setting -show
```

Не публикуйте эти логи. Сразу в Panel Settings смените имя/пароль на собственные,
включите 2FA/TOTP, сохраните секрет восстановления вне VPS, установите разумный
session timeout и выполните повторный вход. Можно задать непредсказуемый Web Base
Path; после этого добавляйте его к локальному URL. Не меняйте Listen IP на
контейнерный loopback, иначе Docker port forwarding не дойдёт до панели.
Не включайте Secure cookie без TLS на локальном HTTP endpoint — вход перестанет
сохраняться. Не открывайте туннель на `0.0.0.0`, используйте SSH-ключи.

Если одноразовые реквизиты потеряны: официальный CLI
`/app/x-ui setting -username ИМЯ -password ПАРОЛЬ` задаёт новые данные.
Запускайте его через `docker compose exec app` из приватной сессии, не оставляйте
пароль в истории shell/общих логах (аргументы временно видны администраторам хоста),
затем `docker compose restart app`. SQLite напрямую не редактируйте.

### Создать inbound

В **Inbounds → Add inbound**:

| Поле | Значение |
|---|---|
| Enable / Remark | включено / `VLESS-XHTTP-CDN` |
| Protocol | VLESS |
| Listen IP / Port | `0.0.0.0` / `8080` |
| Decryption / Encryption | `none` / без VLESS Encryption |
| Flow | пусто, **не** Vision |
| Transport | XHTTP (`network: xhttp`) |
| Security | **none** |
| XHTTP Host | `xray.ВАШ-ДОМЕН` |
| XHTTP Path | `/api/СЛУЧАЙНАЯ-СТРОКА/`, одинаковый во всех клиентах |
| XHTTP Mode | **packet-up** |

Случайную строку получите, например, `openssl rand -hex 16`; это не UUID пользователя.
UI v3.11.0 действительно содержит Host, Path и Mode; дополнительный JSON-генератор
не нужен. Сохраните и проверьте отсутствие ошибки перезапуска Xray.

**Почему не серверный auto:** он поддерживается, но экспорт наследует `mode=auto`,
а Hosts не имеет отдельного override XHTTP mode. Для воспроизводимого штатного
экспорта ссылок и QR с `packet-up` здесь inbound также `packet-up`.
Это тот же VLESS/XHTTP, не смена протокола. Если принципиально нужен сервер `auto`,
его можно выбрать в UI, но после каждого импорта клиенту необходимо явно задавать
`packet-up`; QR с автоматически выбранным `auto` тогда не считать готовым профилем.

### Настроить внешний Host (обязательно до выдачи ссылок)

В **Hosts → Add Host** создайте включённую запись, привязанную к этому inbound:

| Поле | Значение |
|---|---|
| Remark | `Cloudflare TLS` |
| Hosts / Address | **только** `xray.ВАШ-ДОМЕН` |
| Port | **443**, не 0/8080 |
| Security | **TLS**, не Same/None |
| SNI | `xray.ВАШ-ДОМЕН` |
| Host Header | `xray.ВАШ-ДОМЕН` |
| Path | тот же `/api/СЛУЧАЙНАЯ-СТРОКА/` |
| ALPN | `h2`, `http/1.1` |
| Fingerprint | например `chrome` |
| Allow insecure | **выключено** |

Hosts — штатные настройки **экспорта**, они не включают TLS на серверном inbound
и не создают второй источник UUID. Не добавляйте origin IP, localhost или CIDR.
Публичный сервер подписок (`2096` по умолчанию) не публикуется и не маршрутизируется
Traefik; не выдавайте подписки с адресом локальной панели. В этом стеке выдаются
индивидуальные VLESS-ссылки/QR через панель. Если подписки не нужны, отключите
subscription service в Panel Settings. Не включайте его публично без отдельного
проекта безопасной маршрутизации.

### Пользователи, UUID, ссылки и QR

В **Clients → Add client** выберите этот inbound, задайте уникальный Email/remark
`user1` (это метка, почта не требуется), сгенерируйте UUID кнопкой панели и включите
доступ. Flow оставьте пустым. Создайте `user2` с **другим UUID** в том же inbound.
Оставьте сгенерированный панелью уникальный Sub ID: клиентское окно QR использует
его для получения ссылок даже без публичного сервера подписок.
Отдельные контейнеры не нужны. Лимит трафика (GB), дата истечения и enable
настраиваются отдельно для каждого клиента; нулевые лимиты означают отсутствие
ограничения. Статистика upload/download отображается в Clients с задержкой опроса.
Не включайте IP-limit: при CDN достоверный IP здесь не гарантирован.

Используйте действия клиента **ссылки/QR** (не подписку и не сырой внутренний
inbound endpoint). Генерация через Hosts проверена по API v3.11.0. Перед выдачей
прочитайте ссылку и после сканирования QR проверьте профиль:

```text
vless://<UUID>@xray.ВАШ-ДОМЕН:443?encryption=none&security=tls&type=xhttp&mode=packet-up&host=xray.ВАШ-ДОМЕН&sni=xray.ВАШ-ДОМЕН&path=%2Fapi%2F...%2F&alpn=h2%2Chttp%2F1.1#...
```

Порядок query не важен; не должно быть 8080, внутреннего IP, `security=none`,
REALITY `pbk/sid`, неправильного SNI или path. Никакого `allowInsecure=true`.
Импортируйте в актуальный клиент на Xray-core с поддержкой XHTTP (например,
совместимую версию v2rayN/v2rayNG), проверьте версию его core и поля профиля.
Поддержка VLESS сама по себе не означает поддержку XHTTP.
Проверьте QR отдельным сканированием: автоматического браузерного QR-теста нет.
Ссылка/QR — секрет доступа, не храните их в `site/`, Git или публичном тикете.

Для временного запрета выключите **клиента**, не inbound. Для окончательного
отзыва удалите клиента. Для ротации отредактируйте его UUID в панели и выдайте
новую ссылку; остальные UUID не меняются. Проверяйте отзыв **новым** соединением:
уже установленные сессии могут жить до закрытия; принудительный restart Xray
оборвёт и остальных пользователей. После любого изменения убедитесь, что UI
не показывает ошибку применения к core.

## Приёмка через реальный Cloudflare (обязательно на VPS/клиенте)

Без доступа к вашей зоне эти пункты **не считаются пройденными**. Не используйте
`--resolve` на origin, origin endpoint или `curl -k` для сквозной проверки CDN.
Подставьте настоящий hostname:

```bash
dig +short xray.ВАШ-ДОМЕН A
curl --http2 -v https://xray.ВАШ-ДОМЕН/
curl -i https://xray.ВАШ-ДОМЕН/api/НЕСУЩЕСТВУЮЩИЙ-ПУТЬ/
docker compose logs --tail=100 app site
```

1. DNS соответствует диапазонам Cloudflare; TLS проверяется, HTTP/2 согласован,
   ответ сайта `200`, есть `CF-Ray`. Запрос `/api/*` не возвращает HTML Nginx.
   Сам статус 404/200 не доказывает правильный backend.
2. По access log **существующего Traefik** сопоставьте `RouterName=xray`,
   `ServiceURL=h2c://...:8080`, запросы GET/POST XHTTP; сайт — `xray-site`.
   Access log сейчас глобально не включён: включайте временно и с учётом
   остальных сервисов. Он содержит чувствительные пути/IP; не публикуйте его.
   HTTP/2 до origin смотрите в `RequestProtocol`, а h2c проверяйте по backend
   и реальному переносу данных, не по одному EXPOSE/label.
3. В совместимом клиенте запустите `user1`, затем `user2` на другом устройстве
   или отдельном SOCKS-порту. Через локальный SOCKS клиента:

   ```bash
   curl --proxy socks5h://127.0.0.1:10808 https://www.cloudflare.com/cdn-cgi/trace
   ```

   Ожидается egress VPS, не адрес клиентской сети. Это **само по себе не доказывает
   CDN**: одновременно проверьте Proxied DNS, edge/Traefik logs и Cloudflare Events.
4. Оба клиента должны передавать данные. Выключите `user1` в UI: новое соединение
   `user1` не работает, новое `user2` работает. Включите, смените UUID `user1`,
   проверьте отказ старого и работу нового; удалите тестового клиента.
5. Передайте большой файл (например 100 MiB) **в обе стороны** через SOCKS к
   вашему разрешённому тестовому HTTPS-серверу; сверяйте SHA-256. Не ограничивайтесь
   скачиванием главной страницы. Подержите соединение минимум 15–30 минут,
   проверьте idle/resume и обрыв/возврат сети клиента, повторное подключение.
   Убедитесь, что статистика обоих пользователей увеличивается раздельно.
6. В Cloudflare Trace/Cache Rules и реальных ответах XHTTP убедитесь: нет `HIT`,
   `Age` и закэшированных пользовательских данных; ожидается BYPASS/DYNAMIC.
   Проверяйте настоящий endpoint, а не только случайный 404. По WAF Events нет
   блокировок/challenge для GET/POST, по Ray ID нет 520/522/524.
7. Выполните `docker compose down`, затем `docker compose up -d`. Не удаляйте
   `_data`. Проверьте сохранение inbound, Hosts, UUID, выключенного состояния,
   администратора и счётчиков. Повторите оба клиентских подключения и открытие сайта.
8. По очереди остановите `site` и `app`, проверяя независимость другого backend;
   затем верните оба `docker compose up -d app site`. Это прерывает тестовый сервис.

## Резервное копирование и восстановление

Используется **существующий** `available/restic/restic-compose-base.yaml`:
backup 03:30, prune 04:00, check 05:15 по TZ; check также при старте.
Restic сохраняет весь `_data`, кроме `backup_restore/`, в
`/backup/ssd/restic/${CONTAINER_NAME}_backups`. Сайт/Nginx берутся из Git,
`.env` и пароль репозитория храните отдельно. ACME Traefik находится в другом
стеке и этим backup не затрагивается.

Перед копированием hook останавливает **`${CONTAINER_NAME}_main`** с timeout 30s
и проверяет `.State.Running=false`. Ошибка останавливает backup, а не копирует
активную SQLite. EXIT hook запускает app также после ошибки копирования.
Копируются БД и все её sidecar-файлы в состоянии покоя. Произойдёт перерыв
доступа ко всем пользователям. Не запускайте app вручную параллельно backup,
не совмещайте backup с deploy/обновлением. При SIGKILL/сбое Docker EXIT hook
не гарантирован: проверьте `docker compose ps app` и запустите app вручную.
Изменения файлов/настроек через `docker exec` во время backup запрещены.

Общий шаблон сохраняет уведомления Apprise о failure/incomplete. Его upstream
backup-скрипт не вызывает failure notification при ошибке **PRE_COMMANDS**;
контролируйте и логи Restic. После такой ошибки может остаться
`/run/lock/backup.lock` — после проверки отсутствия активного backup пересоздайте
только backup-контейнер. Это не блокировка репозитория Restic, не используйте
`restic unlock` без диагностики.

Ручной snapshot (не запускайте параллельно cron):

```bash
docker compose exec backup_restic__xray backup
docker compose exec backup_restic__xray restic snapshots
docker compose exec backup_restic__xray restic check
```

Восстановление (пример с `CONTAINER_NAME=xray`; требует места для staging):

1. Выберите snapshot ID в `restic snapshots` и сохраните его.
2. Остановите все три задания, чтобы cron/prune не пересеклись с восстановлением,
   и остановите app. Nginx и Traefik останавливать не нужно:

   ```bash
   docker compose stop backup_restic__xray prune_restic__xray check_restic__xray app
   docker compose run --rm --no-deps --entrypoint restic backup_restic__xray restore SNAPSHOT_ID --target /restore_data
   ```

   `--entrypoint restic` обходит штатный startup/cron/hooks. Содержимое появляется
   в `xray_data/backup_restore/backup_data/`; проверьте, что там есть `db/x-ui.db`.
3. Сохраняя app остановленным, **не накладывайте** восстановление поверх старых
   SQLite WAL/SHM. Отложите текущий db, скопируйте восстановленный каталог целиком:

   ```bash
   sudo mv xray_data/db "xray_data/db.before-restore-$(date +%Y%m%d%H%M%S)"
   sudo cp -a xray_data/backup_restore/backup_data/db xray_data/db
   sudo chown -R root:root xray_data/db
   sudo chmod 700 xray_data xray_data/db
   ```

   Для rootless используйте соответствующий mapped UID. Оставленные каталоги
   содержат секреты и попадут в следующий backup до их отдельного удаления.
4. При наличии sqlite3 проверьте offline:
   `sudo sqlite3 xray_data/db/x-ui.db 'PRAGMA integrity_check;'` → `ok`.
   Запустите `docker compose up -d app`, войдите прежними реквизитами,
   проверьте UUID, inbound, Hosts, лимиты/статистику и реальное клиентское соединение.
5. После приёмки верните задания `docker compose up -d`. Staging и отложенную
   тестовую БД удаляйте только после проверки точного пути. Не пересоздавайте
   пользователей и не импортируйте старый Xray JSON поверх восстановленной БД.

## Обновление

Watchtower для app/site выключен. Сначала сделайте snapshot и проверьте его,
прочитайте release notes 3x-ui и совместимость клиентов, измените закреплённый
тег образа в Compose и ожидаемую версию в тестах, затем:

```bash
docker compose config --quiet
docker compose pull app
docker compose up -d app
docker compose logs --tail=100 app
```

Обновления UI/core из самой панели не воспроизводятся после recreate — используйте
образ. Откат может требовать **старого образа и БД из snapshot до обновления**:
новый формат БД не обязан поддерживаться старой версией. Повторите приёмку CDN.

## Диагностика

```bash
docker compose config --quiet
docker compose ps
docker compose logs --since=10m app site backup_restic__xray
docker compose run --rm --no-deps site -t
docker compose exec app /app/bin/xray-linux-amd64 version
```

Имя бинарника выше для amd64; на ARM используйте имя из `/app/bin/`.
Не редактируйте generated config для исправлений — меняйте inbound/template в UI.

| Симптом | Что проверить |
|---|---|
| Панель недоступна | SSH tunnel, loopback bind на хосте, app logs, порт 2053, Listen IP внутри контейнера, Web Base Path |
| `permission denied` SQLite | Владелец db, режим родительского каталога, userns; `cap_drop` не даёт обходить чужие mode 700 |
| 520 Cloudflare | Ray ID, Events, корректность ответа origin, рестарт backend, разрыв h2c; не делайте вывод по одному статусу |
| 522 Cloudflare | CDN не соединяется с origin: A, доступность VPS:443, Traefik, firewall allowlist IPv4/IPv6 |
| 524 Cloudflare | Соединение с origin есть, но ответ задержан: зависший core, idle/read timeout, streaming GET, XMUX/reconnect |
| 525/526, TLS error | Full (strict), действительность origin-сертификата, resolver/DNS token в Traefik, SNI; не ставьте Flexible/allowInsecure |
| 502 на `/api/*` | Создан/включён ли inbound:8080, слушает ли `0.0.0.0`, общая сеть, backend **h2c**, нет ли TLS на inbound |
| HTTP/2/h2c error | Клиент ALPN h2, CF edge settings, origin protocol отдельно; backend scheme h2c, не https/http и не gRPC |
| HTML вместо XHTTP | Path начинается `/api/`, priority 100/10, router/service по access log, нет StripPrefix и редиректа |
| UUID rejected | Клиент включён, UUID принадлежит ему, лимит/срок не исчерпан, flow пустой, изменения применены core |
| Ссылка на localhost/8080 | Настройте Hosts, внешний 443/TLS; используйте клиентские ссылки с Hosts, а не внутренний inbound |
| Неверный SNI/Host/path | Во всех трёх местах реальный hostname, точное совпадение path со слешами, URL encoding; DNS всё ещё Proxied |
| 403/challenge/429 | WAF/Bot/rate limit Events по Ray ID, узкое исключение hostname+path; не отключайте глобальную защиту |
| Работает недолго | 524/idle timeout, версии обоих core, packet-up, packet size и XMUX, разрывы/повторные подключения |
| Статистика нулевая | Реальный трафик пользователя, задержка опроса, API core/policy в panel template, app logs |
| Restic не возвращает app | Логи hook, daemon/socket, конфликт cron/deploy, SIGKILL; проверьте app и запустите вручную |

При сообщении об ошибке не публикуйте UUID, пароль, cookies, SQLite, QR и полный
XHTTP path. CDN endpoint нельзя проверять только `curl -I`: нужен настоящий
VLESS-клиент и передача данных.

## Автоматические проверки и границы результата

Из корня репозитория:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s available/xray/tests -v
PYTHONDONTWRITEBYTECODE=1 python3 available/xray/tests/integration.py
git diff --check
```

Контрактные тесты вызывают настоящий Compose с фиктивными переменными и
`--env-file /dev/null`: `.env` развёртывания не читается. Интеграционный тест
требует Linux/amd64, Docker, openssl, curl и интернет (образа и `https://example.com/`).
Он создаёт **отдельную временную bridge-сеть**, случайные localhost-порты и
данные под `/tmp/opencode` с закрытым родителем. Не подключает тесты к общим
сетям, не читает рабочие данные и не запускает рабочий Compose.
Для проверки Restic только одноразовый backup-контейнер получает Docker socket
и останавливает только созданный тестом app. Все тестовые контейнеры и сеть
удаляются в finally; приватные артефакты остаются для диагностики.

Проверяются: первый вход со случайными реквизитами; API создания inbound и двух
клиентов; различные UUID; Hosts и экспорт внешних TLS-ссылок; настоящий Xray
клиент `packet-up` → TLS/HTTP2 Traefik → h2c Xray → Интернет; статический сайт;
отзыв одного клиента без блокировки другого, ротация UUID; увеличение статистики; Restic
stop/backup/start, check, restore и запуск нового контейнера из восстановленной
SQLite; сохранение администратора, Hosts, UUID и disabled state; удаление клиента.
Локальный сертификат теста явно доверен клиентом, TLS verification не отключена.

**Не проверены автоматически:** реальная зона/DNS/Cloudflare Cache Rules/WAF,
сертификаты Let's Encrypt вашего Traefik, CDN E2E, браузерный первый вход/2FA и
сканирование QR, ограничения трафика/срока в момент исчерпания, большие upload/
download и длительные соединения, reboot VPS, доставка Apprise и восстановление
из рабочего Restic-репозитория. Локальная API-проверка не заменяет эти сценарии.

Официальные источники (проверять при обновлении):

- [3x-ui v3.11.0 / release notes](https://github.com/MHSanaei/3x-ui/releases/tag/v3.11.0)
- [Docker Compose](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/docker-compose.yml),
  [Dockerfile](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/Dockerfile),
  [entrypoint](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/DockerEntrypoint.sh)
- [XHTTP UI](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/frontend/src/pages/inbounds/form/transport/xhttp.tsx),
  [Hosts fields](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/frontend/src/schemas/api/host.ts)
- [Project X: XHTTP](https://xtls.github.io/en/config/transports/xhttp.html),
  [расширенная документация XHTTP](https://github.com/XTLS/Xray-core/discussions/4113)
- [Traefik services / h2c](https://doc.traefik.io/traefik/routing/services/)
- [Resticker backup hooks](https://github.com/djmaze/resticker/blob/master/backup)
- [Cloudflare limits](https://developers.cloudflare.com/fundamentals/reference/connection-limits/),
  [Cache Rules](https://developers.cloudflare.com/cache/how-to/cache-rules/),
  [HTTP/2 to origin](https://developers.cloudflare.com/speed/optimization/protocol/http2-to-origin/)

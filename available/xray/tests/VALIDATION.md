# Результат проверки 2026-11-10

Среда: Linux/amd64, Docker Engine 29.7.2-ce, Compose 5.6.1.
Рабочий Compose не запускался; использовались одноразовые контейнеры, своя
bridge-сеть и случайные loopback-порты, без данных и сетей других стеков.

Проверенные образы:

- `ghcr.io/mhsanaei/3x-ui:v3.11.0`, manifest digest
  `sha256:1628adbd9bed680d25d6f714bef4a20f132105f4612ea294952f6665dad32e5b`;
  внутри Xray `26.10.21`.
- `traefik:v3`, digest
  `sha256:f4bb143d1135a579b630ed491e17ec50f6f7c9c6a30585ff3d385471d2a86f5a`.
- `mazzolino/restic:latest`, digest
  `sha256:84a18b739c15216b07dcb9e14985437d49b7a3a68ef8ce48c1323b9f59bf794e`.
- `nginxinc/nginx-unprivileged:1.28.0-alpine`.

## Успешно

```bash
cd available/xray
docker compose config --quiet
# Из корня репозитория:
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s available/xray/tests -v
PYTHONDONTWRITEBYTECODE=1 python3 available/xray/tests/integration.py
git diff --check
```

- Пять контрактных тестов настоящего Compose: storage, capabilities, панель
  только на loopback, labels h2c/priority, сети, Restic, ротация логов.
- Официальный entrypoint, случайный первый администратор, вход с CSRF/cookie.
- Создание VLESS/XHTTP inbound и двух пользователей через поддерживаемый API.
- Разные UUID; ссылки из Hosts и endpoint, который использует окно клиентского
  QR, содержат внешний hostname, 443, TLS, SNI, Host, path и `packet-up`.
- Настоящие два Xray-клиента передают HTTPS-трафик через
  **TLS/HTTP2 → Traefik → h2c → Xray → Интернет**.
  TLS проверяется с явно доверенным тестовым сертификатом, не `allowInsecure`.
- Отдельный Nginx отдаёт сохранённый статический сайт.
- Отключение пользователя, ротация его UUID и удаление отзывают новое соединение;
  второй пользователь продолжает подключаться. Счётчики обоих растут.
- Реальный штатный скрипт Restic с hooks из Compose: остановка app, копирование
  SQLite, запуск app, проверка репозитория, restore.
- Новый контейнер с восстановленной БД сохраняет администратора, UUID, inbound,
  Hosts и отключённого пользователя; подключение второго работает.
- Ошибка остановки не допускает начала backup.

Первый poll статистики этой версии устанавливает baseline. Тест продолжает
передачу после него, прежде чем проверять положительную дельту.

## Не проверено — требуется приёмка реальной инфраструктуры

- Cloudflare DNS/Proxied, Full (strict), HTTP/2 to Origin, Cache Rules, WAF,
  таймауты и **сквозное соединение через настоящий CDN**.
- Выпуск/продление Let's Encrypt в общем Traefik; origin allowlist/AOP.
- Браузерный UI, смена пароля через UI, 2FA и сканирование/импорт QR.
  Проверен payload ссылки, не отрисовка/распознавание QR.
- Исчерпание квоты/срока, большие файлы в обе стороны, длительная сессия,
  reconnect после обрыва сети, reboot Docker-хоста, платформы кроме amd64.
- Apprise delivery, расписание cron и восстановление из рабочего, а не
  временного Restic-репозитория.

Эти пункты не считаются выполненными по результату локальных тестов.
Конкретная процедура приёмки и настройки Cloudflare — в `../README.md`.

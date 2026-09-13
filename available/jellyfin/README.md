# Аппаратное ускорение Jellyfin

Этот Compose запускает Jellyfin на Intel Core i3-7130U со встроенной графикой
Intel HD Graphics 620. Сервис `jellyfin` передаёт `/dev/dri` в контейнер
LinuxServer.io, в котором уже есть пользовательские драйверы Intel VA-API и
QSV.

Хост должен предоставлять драйвер ядра `i915` и `/dev/dri/renderD128`. Intel
HD Graphics 620 поддерживает аппаратное кодирование и декодирование H.264 и
HEVC, включая 10-битное декодирование HEVC. Аппаратное ускорение AV1 не
поддерживается.

## Установка пакетов на хосте

До развёртывания Compose должны быть установлены Docker Engine и Docker
Compose. Следующие пакеты устанавливают Intel firmware и диагностические
утилиты на хосте. `intel-media-driver` и `intel-media-va-driver-non-free`
используются только для проверки хоста через `vainfo`: сам Jellyfin использует
драйверы из образа контейнера.

### openSUSE Tumbleweed

```bash
sudo zypper refresh
sudo zypper install \
  kernel-firmware-intel \
  intel-media-driver \
  libva-utils \
  intel-gpu-tools
```

### Ubuntu

Включите репозиторий `multiverse`, если он ещё не включён:

```bash
sudo add-apt-repository multiverse
sudo apt update
sudo apt install \
  linux-firmware \
  intel-media-va-driver-non-free \
  vainfo \
  intel-gpu-tools
```

## Подготовка хоста

Включите интегрированную графику в BIOS HP. После включения подключённый
монитор не требуется.

Проверьте, что Intel GPU использует драйвер ядра `i915` и предоставляет
render-устройство:

```bash
lspci -nnk | grep -A3 -E 'VGA|Display'
lsmod | grep i915
ls -la /dev/dri
```

В выводе должны присутствовать `Kernel driver in use: i915` и
`/dev/dri/renderD128`. Если модуль не загружен, загрузите его и повторите
проверку:

```bash
sudo modprobe i915
```

Проверьте VA-API на хосте:

```bash
sudo vainfo --display drm --device /dev/dri/renderD128
```

Результат должен содержать драйвер Intel `iHD` и профили H.264 и HEVC. Не
продолжайте настройку, пока эта проверка не завершится успешно.

На системах openSUSE с SELinux в режиме `Enforcing` проверьте наличие boolean
политики DRI для контейнеров:

```bash
getenforce
getsebool container_use_dri_devices
```

Если boolean существует и выключен, включите его:

```bash
sudo setsebool -P container_use_dri_devices 1
```

По умолчанию openSUSE Tumbleweed обычно использует AppArmor, а не SELinux,
поэтому `getenforce` может отсутствовать. В таком случае ничего делать не
нужно.

## Развёртывание

Кэш транскодирования постоянно хранится в
`./${CONTAINER_NAME}_data/cache` рядом с конфигурацией Jellyfin. Он не входит
в существующий Restic-бэкап, так как тот сохраняет только `config`.

Возьмите числовые значения `PUID` и `PGID` из `.env`, создайте каталог кэша и
назначьте их его владельцем. Для обычного развёртывания с `1000:1000`:

```bash
install -d -m 0755 -o 1000 -g 1000 jellifin_data/cache
```

Замените `1000` и `jellifin_data`, если в `.env` заданы другие значения
`PUID`, `PGID` или `CONTAINER_NAME`. Старый каталог
`/tmp/${CONTAINER_NAME}_cache` содержит только временные данные
транскодирования, его не нужно копировать.

Проверьте итоговую конфигурацию и пересоздайте Jellyfin:

```bash
docker compose config
docker compose up -d --force-recreate jellyfin
```

После успешного запуска Jellyfin удалите устаревший каталог
`/tmp/${CONTAINER_NAME}_cache`, если он остался.

Убедитесь, что render-устройство доступно внутри контейнера:

```bash
docker compose exec jellyfin ls -la /dev/dri
docker compose exec jellyfin \
  /usr/lib/jellyfin-ffmpeg/vainfo \
  --display drm \
  --device /dev/dri/renderD128
```

Вторая команда должна показать драйвер Intel `iHD` и поддерживаемые профили.

## Включение QSV в Jellyfin

1. Откройте **Dashboard > Playback > Transcoding**.
2. Выберите аппаратное ускорение **Intel Quick Sync (QSV)**.
3. Выберите `/dev/dri/renderD128`, если Jellyfin отображает поле устройства.
4. Включите аппаратное кодирование.
5. Включайте только кодеки, показанные в выводе `vainfo`.
6. Не включайте AV1, HEVC RExt, HDR tone mapping и Intel Low-Power Encoding на
   начальном этапе.

Jellyfin сохраняет эти настройки в `encoding.xml`; меняйте их через
веб-интерфейс, а не вручную в этом файле.

## Проверка аппаратного ускорения

Запустите воспроизведение и принудительно вызовите транскодирование, выбрав в
клиенте более низкое качество или битрейт. В информации о воспроизведении
должно быть указано `Transcoding`, а не `Direct Play`: прямое воспроизведение
не использует GPU сервера, и это нормально.

Во время транскодирования отслеживайте загрузку медиа-движков Intel на хосте:

```bash
sudo intel_gpu_top
```

Активность `Video` или `VideoEnhance` подтверждает, что GPU обрабатывает
транскодирование. Также проверьте журнал транскодирования Jellyfin FFmpeg в
веб-панели. Команды QSV-транскодирования обычно содержат параметры
`-init_hw_device qsv`, `-hwaccel qsv`, `h264_qsv` или `hevc_qsv`.

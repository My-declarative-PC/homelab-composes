#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"

for app in docker python3; do
	command -v "$app" >/dev/null || {
		echo "Необходима команда: $app" >&2
		exit 1
	}
done
XRAY_COMPOSE_CONFIG="$(docker compose config --format json)"
export XRAY_COMPOSE_CONFIG
SETTINGS="$(
	python3 - <<'PY'
import json, os, re
from pathlib import Path
from configuration import server_name

config = json.loads(os.environ['XRAY_COMPOSE_CONFIG'])
settings = config['x-xray-setup']
name = settings['container_name']
if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', name):
    raise ValueError('CONTAINER_NAME должен содержать только латинские буквы, цифры, _ и - и начинаться с буквы или цифры')
server_name(settings['domain'])
if Path('config/config.json').exists() or Path('config/config.json').is_symlink():
    raise RuntimeError('Найден старый config/config.json. Для перехода с REALITY на TLS следуйте README; новые реквизиты не созданы')
data = Path(name + '_data')
for relative in ('config/config.json', 'client/client.json', 'client/import-link.txt'):
    path = data / relative
    if path.exists() or path.is_symlink():
        raise RuntimeError(str(path) + ' уже существует. Во избежание потери реквизитов генерация остановлена; переход с REALITY описан в README')
settings['image'] = config['services']['app']['image']
print(json.dumps(settings))
PY
)"
export SETTINGS
IMAGE="$(python3 -c 'import json, os; print(json.loads(os.environ["SETTINGS"])["image"])')"

docker pull "$IMAGE"
python3 - <<'PY'
import json, os
from pathlib import Path
from configuration import create_configuration, server_name

settings=json.loads(os.environ['SETTINGS'])
server, client, uri=create_configuration(settings['domain'])
data=Path(settings['container_name']+'_data')
data.mkdir(exist_ok=True)
os.chmod(data,0o700)
(data/'config').mkdir(exist_ok=True)
(data/'client').mkdir(exist_ok=True)
os.chmod(data/'config',0o755)
os.chmod(data/'client',0o700)
# Режим x не позволяет перезаписать файл даже при конкурентном запуске.
with (data/'config/config.json').open('x') as file:
    file.write(json.dumps(server,indent=2)+'\n')
os.chmod(data/'config/config.json',0o644) # непривилегированный процесс контейнера читает bind mount; родитель на хосте закрыт
with (data/'client/client.json').open('x') as file:
    file.write(json.dumps(client,indent=2)+'\n')
os.chmod(data/'client/client.json',0o600)
with (data/'client/import-link.txt').open('x') as file:
    file.write(uri+'\n')
os.chmod(data/'client/import-link.txt',0o600)
print('Сервер:',server_name(settings['domain'])+':443 (TLS через Traefik)')
print('Создано:',data/'config/config.json',data/'client/client.json',data/'client/import-link.txt')
PY

docker compose run --rm --no-deps app run -test -c /etc/xray/config.json
echo 'Проверка конфигурации пройдена. Запуск: docker compose up -d'

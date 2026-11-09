#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"

for app in docker python3 curl; do
	command -v "$app" >/dev/null || {
		echo "Необходима команда: $app" >&2
		exit 1
	}
done
XRAY_COMPOSE_CONFIG="$(docker compose config --format json)"
export XRAY_COMPOSE_CONFIG
SETTINGS="$(
	python3 - <<'PY'
import ipaddress, json, os, re
from pathlib import Path

config = json.loads(os.environ['XRAY_COMPOSE_CONFIG'])
settings = config['x-xray-setup']
name = settings['container_name']
if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', name):
    raise ValueError('CONTAINER_NAME должен содержать только латинские буквы, цифры, _ и - и начинаться с буквы или цифры')
port = str(settings['port'])
if not re.fullmatch(r'[0-9]+', port) or not 1 <= int(port) <= 65535:
    raise ValueError('XRAY_PORT должен быть целым числом от 1 до 65535')
settings['port'] = int(port)
host = settings['target_host']
if len(host) > 253 or not all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label) for label in host.split('.')):
    raise ValueError('TARGET_HOST должен быть DNS-именем')
if settings['server_ip']:
    ipaddress.IPv4Address(settings['server_ip'])
if Path('config/config.json').exists() or Path('config/config.json').is_symlink():
    raise RuntimeError('Найден старый config/config.json. Перенесите config/ и client/ в ' + name + '_data/ по инструкции README; новые ключи не созданы')
data = Path(name + '_data')
for relative in ('config/config.json', 'client/client.json', 'client/import-link.txt'):
    path = data / relative
    if path.exists() or path.is_symlink():
        raise RuntimeError(str(path) + ' уже существует. Во избежание потери ключей генерация остановлена')
settings['image'] = config['services']['app']['image']
print(json.dumps(settings))
PY
)"
export SETTINGS
SERVER_IP="$(python3 -c 'import json, os; print(json.loads(os.environ["SETTINGS"])["server_ip"])')"
IMAGE="$(python3 -c 'import json, os; print(json.loads(os.environ["SETTINGS"])["image"])')"
if [[ -z "$SERVER_IP" ]]; then
	SERVER_IP="$(curl -4fsS --connect-timeout 5 --max-time 10 https://api.ipify.org)" || {
		echo 'Не удалось определить внешний IPv4. Запустите: SERVER_IP=ваш_публичный_IP ./setup.sh' >&2
		exit 1
	}
fi
export SERVER_IP
python3 -c 'import ipaddress, os; ipaddress.IPv4Address(os.environ["SERVER_IP"])'

docker pull "$IMAGE"
KEYS="$(docker run --rm "$IMAGE" x25519)"
export KEYS
python3 - <<'PY'
import json, os, re, secrets, uuid, ipaddress
from pathlib import Path
keys=os.environ['KEYS']
def extract(pattern):
    match=re.search(pattern,keys,re.I|re.M)
    if not match:
        raise RuntimeError('Не распознаны ключи xray x25519: неизвестный формат вывода (ключи скрыты)')
    return match.group(1).strip()
private=extract(r'^Private(?:Key| key):\s*(\S+)')
public=extract(r'^(?:Password(?:\s*\(Public\s*Key\))?|Public\s*Key):\s*(\S+)')
settings=json.loads(os.environ['SETTINGS'])
host=settings['target_host']
port=settings['port']
address=os.environ['SERVER_IP']
ipaddress.IPv4Address(address)
uid=str(uuid.uuid4())
short=secrets.token_hex(8)
path='/api/'+secrets.token_hex(12)
server={
 'log':{'loglevel':'none'},
 'inbounds':[{'listen':'0.0.0.0','port':443,'protocol':'vless',
   'settings':{'clients':[{'id':uid}],'decryption':'none'},
   'streamSettings':{'network':'xhttp','security':'reality',
      'realitySettings':{'show':False,'target':host+':443','xver':0,
        'serverNames':[host],'privateKey':private,'shortIds':[short]},
      'xhttpSettings':{'path':path,'mode':'auto'}}}],
 'outbounds':[{'protocol':'freedom','tag':'direct','settings':{'domainStrategy':'UseIP'}},
              {'protocol':'blackhole','tag':'block'}],
 'routing':{'rules':[{'type':'field','ip':['geoip:private'],'outboundTag':'block'}]}
}
client={
 'log':{'loglevel':'warning'},
 'inbounds':[{'tag':'socks-in','listen':'127.0.0.1','port':10808,'protocol':'socks',
  'settings':{'auth':'noauth','udp':True}}],
 'outbounds':[{'tag':'proxy','protocol':'vless',
  'settings':{'vnext':[{'address':address,'port':port,
   'users':[{'id':uid,'encryption':'none'}]}]},
  'streamSettings':{'network':'xhttp','security':'reality',
   'realitySettings':{'serverName':host,'fingerprint':'chrome',
    'password':public,'shortId':short,'spiderX':'/'},
   'xhttpSettings':{'path':path,'mode':'auto'}}}]
}
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
from urllib.parse import quote
uri=(f'vless://{uid}@{address}:{port}?encryption=none&security=reality'
     f'&sni={host}&fp=chrome&pbk={quote(public)}&sid={short}'
     f'&type=xhttp&mode=auto&path={quote(path,safe="") }#Xray-XHTTP-REALITY')
with (data/'client/import-link.txt').open('x') as file:
    file.write(uri+'\n')
os.chmod(data/'client/import-link.txt',0o600)
print('Сервер:',address+':'+str(port))
print('REALITY target:',host)
print('Создано:',data/'config/config.json',data/'client/client.json',data/'client/import-link.txt')
PY

docker compose run --rm --no-deps app run -test -c /etc/xray/config.json
echo 'Проверка конфигурации пройдена. Запуск: docker compose up -d'

#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"
IMAGE='ghcr.io/xtls/xray-core:latest'
TARGET_HOST="${TARGET_HOST:-www.bing.com}"
SERVER_IP="${SERVER_IP:-}"

for app in docker python3 curl; do
	command -v "$app" >/dev/null || {
		echo "Необходима команда: $app" >&2
		exit 1
	}
done
if [[ -e config/config.json ]]; then
	echo 'config/config.json уже существует. Во избежание потери существующих ключей генерация остановлена.' >&2
	exit 1
fi
if [[ -z "$SERVER_IP" ]]; then
	SERVER_IP="$(curl -4fsS --connect-timeout 5 --max-time 10 https://api.ipify.org)" || {
		echo 'Не удалось определить внешний IPv4. Запустите: SERVER_IP=ваш_публичный_IP ./setup.sh' >&2
		exit 1
	}
fi

docker pull "$IMAGE"
KEYS="$(docker run --rm "$IMAGE" x25519)"
export KEYS TARGET_HOST SERVER_IP
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
host=os.environ['TARGET_HOST']
if not re.fullmatch(r'[a-zA-Z0-9.-]+',host):
    raise ValueError('TARGET_HOST должен быть DNS-именем')
address=os.environ['SERVER_IP']
ipaddress.ip_address(address)
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
  'settings':{'vnext':[{'address':address,'port':443,
   'users':[{'id':uid,'encryption':'none'}]}]},
  'streamSettings':{'network':'xhttp','security':'reality',
   'realitySettings':{'serverName':host,'fingerprint':'chrome',
    'password':public,'shortId':short,'spiderX':'/'},
   'xhttpSettings':{'path':path,'mode':'auto'}}}]
}
Path('config').mkdir(exist_ok=True)
Path('client').mkdir(exist_ok=True)
Path('config/config.json').write_text(json.dumps(server,indent=2)+'\n')
Path('client/client.json').write_text(json.dumps(client,indent=2)+'\n')
os.chmod('config',0o755)
os.chmod('config/config.json',0o644) # официальный образ работает без root; 644 нужно для чтения bind mount
os.chmod('client/client.json',0o600)
from urllib.parse import quote
uri=(f'vless://{uid}@{address}:443?encryption=none&security=reality'
     f'&sni={host}&fp=chrome&pbk={quote(public)}&sid={short}'
     f'&type=xhttp&mode=auto&path={quote(path,safe="") }#Xray-XHTTP-REALITY')
Path('client/import-link.txt').write_text(uri+'\n')
os.chmod('client/import-link.txt',0o600)
print('Сервер:',address)
print('REALITY target:',host)
print('Создано: config/config.json, client/client.json, client/import-link.txt')
PY

docker compose run --rm --no-deps app run -test -c /etc/xray/config.json
echo 'Проверка конфигурации пройдена. Запуск: docker compose up -d'

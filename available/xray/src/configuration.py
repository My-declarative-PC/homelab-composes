"""Согласованные конфигурации Xray для TLS-терминации на Traefik."""

import ipaddress
import re
import secrets
import uuid
from urllib.parse import quote, urlencode

BACKEND_PORT = 8080


def server_name(domain):
    """Проверить базовый DOMAIN и получить публичное имя сервера."""
    labels = domain.split(".")
    if (
        len(labels) < 2
        or len("xray." + domain) > 253
        or not all(
            re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
            for label in labels
        )
    ):
        raise ValueError("DOMAIN должен быть DNS-именем, например example.com")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise ValueError("DOMAIN должен быть доменом, а не IP-адресом")
    return "xray." + domain


def create_server_configuration(domain):
    """Создать сервер без пользователей с новым общим XHTTP-путём."""
    server_name(domain)
    path = "/api/" + secrets.token_hex(12)
    server = {
        "log": {"loglevel": "none"},
        "inbounds": [
            {
                "listen": "0.0.0.0",
                "port": BACKEND_PORT,
                "protocol": "vless",
                "settings": {"clients": [], "decryption": "none"},
                "streamSettings": {
                    "network": "xhttp",
                    "security": "none",
                    "xhttpSettings": {"path": path, "mode": "auto"},
                },
            }
        ],
        "outbounds": [
            {
                "protocol": "freedom",
                "tag": "direct",
                "settings": {"domainStrategy": "UseIP"},
            },
            {"protocol": "blackhole", "tag": "block"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "block"}
            ]
        },
    }
    return server


def create_client_configuration(domain, uid, path, name="Xray-XHTTP-TLS"):
    """Построить профиль из существующих реквизитов, без генерации новых."""
    host = server_name(domain)
    client = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
            }
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": host,
                            "port": 443,
                            "users": [{"id": uid, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "tls",
                    "tlsSettings": {
                        "serverName": host,
                        "fingerprint": "firefox",
                        "alpn": ["h2", "http/1.1"],
                        "allowInsecure": False,
                    },
                    # Пакетная отправка не требует бесконечного потока POST через reverse proxy.
                    "xhttpSettings": {"host": host, "path": path, "mode": "packet-up"},
                },
            }
        ],
    }
    query = urlencode(
        {
            "encryption": "none",
            "security": "tls",
            "sni": host,
            "fp": "firefox",
            "alpn": "h2,http/1.1",
            "type": "xhttp",
            "host": host,
            "mode": "packet-up",
            "path": path,
        }
    )
    uri = f"vless://{uid}@{host}:443?{query}#{quote(name, safe='')}"
    return client, uri


def create_configuration(domain):
    """Совместимый генератор прежнего комплекта с одним пользователем."""
    server = create_server_configuration(domain)
    uid = str(uuid.uuid4())
    inbound = server["inbounds"][0]
    inbound["settings"]["clients"] = [{"id": uid}]
    client, uri = create_client_configuration(
        domain, uid, inbound["streamSettings"]["xhttpSettings"]["path"]
    )
    return server, client, uri

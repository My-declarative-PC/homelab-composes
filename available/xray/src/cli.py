"""Командный интерфейс управления стеком."""

import argparse
import re
import sys

from .compose import Compose, DockerError
from .configuration import create_configuration, server_name
from .storage import check_new_configuration, locked_root, write_configuration


def settings_from_config(config):
    try:
        settings = config["x-xray-setup"]
        name = settings["container_name"]
        domain = settings["domain"]
        image = config["services"]["app"]["image"]
        if not all(isinstance(value, str) and value for value in (name, domain, image)):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("В Compose отсутствуют корректные параметры Xray") from error
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        raise ValueError(
            "CONTAINER_NAME должен начинаться с буквы или цифры и содержать только латинские буквы, цифры, _ и -"
        )
    server_name(domain)
    return name, domain, image


def setup(root, compose):
    name, domain, image = settings_from_config(compose.config())
    with locked_root(root) as parent:
        check_new_configuration(parent, name + "_data")
        compose.docker_run("pull", image)
        server, client, uri = create_configuration(domain)
        write_configuration(parent, name + "_data", server, client, uri)
        compose.run(
            "run",
            "--rm",
            "--no-deps",
            "app",
            "run",
            "-test",
            "-c",
            "/etc/xray/config.json",
        )
    print("Сервер:", server_name(domain) + ":443 (TLS через Traefik)")
    print("Созданы конфигурация и клиентские файлы в", name + "_data")
    print("Проверка конфигурации пройдена. Запуск: docker compose up -d")


def main(root, argv=None):
    parser = argparse.ArgumentParser(description="Управление стеком Xray")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Создать и проверить новую конфигурацию")
    args = parser.parse_args(argv)
    try:
        setup(root, Compose(root))
    except DockerError as error:
        print("Ошибка:", error, file=sys.stderr)
        return error.returncode if 0 < error.returncode < 126 else 1
    except (OSError, ValueError, RuntimeError) as error:
        # OSError может включать путь; содержимое конфигураций не выводится.
        print("Ошибка:", error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Операция прервана; созданные реквизиты сохранены", file=sys.stderr)
        return 130
    return 0

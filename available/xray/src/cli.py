"""Командный интерфейс управления стеком."""

import argparse
import re
import sys

from .compose import Compose, DockerError
from .configuration import create_configuration, server_name
from .storage import check_new_configuration, locked_root, write_configuration


SERVICES = (
    "app",
    "site",
    "backup_restic__xray",
    "prune_restic__xray",
    "check_restic__xray",
)


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
    print("Проверка конфигурации пройдена. Запуск: python3 xray.py up")


def check(compose):
    settings_from_config(compose.config())
    compose.run(
        "run", "--rm", "--no-deps", "app", "run", "-test", "-c", "/etc/xray/config.json"
    )
    compose.run("run", "--rm", "--no-deps", "site", "-t")
    print("Проверки Compose, Xray и Nginx пройдены")


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("Значение должно быть неотрицательным")
    return number


def main(root, argv=None):
    parser = argparse.ArgumentParser(description="Управление стеком Xray")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Создать и проверить новую конфигурацию")
    commands.add_parser("check", help="Проверить Compose, Xray и Nginx без генерации")
    up = commands.add_parser("up", help="Запустить стек или выбранные сервисы")
    up.add_argument("services", nargs="*", metavar="СЕРВИС")
    commands.add_parser("down", help="Остановить стек без удаления томов и данных")
    commands.add_parser("status", help="Показать состояние сервисов")
    logs = commands.add_parser("logs", help="Показать логи (могут содержать реквизиты)")
    logs.add_argument(
        "--tail",
        type=nonnegative,
        default=50,
        help="Число последних строк (по умолчанию 50)",
    )
    logs.add_argument(
        "--follow", action="store_true", help="Следить за новыми сообщениями"
    )
    logs.add_argument("services", nargs="*", metavar="СЕРВИС")
    args = parser.parse_args(argv)
    for service in getattr(args, "services", ()):
        if service not in SERVICES:
            parser.error("Неизвестный сервис; допустимы: " + ", ".join(SERVICES))
    try:
        compose = Compose(root)
        if args.command == "setup":
            setup(root, compose)
        elif args.command == "check":
            check(compose)
        elif args.command == "up":
            compose.run("up", "-d", *args.services)
            print("Команда запуска выполнена. Состояние: python3 xray.py status")
        elif args.command == "down":
            compose.run("down")
            print("Стек остановлен; данные и тома сохранены")
        elif args.command == "status":
            print(compose.run("ps"), end="")
        elif args.command == "logs":
            options = ["logs", "--tail", str(args.tail)]
            if args.follow:
                options.append("--follow")
            compose.run(*options, *args.services, stream=True)
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

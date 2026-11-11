"""Командный интерфейс настройки и проверки Xray."""

import argparse
import json
import re
import sys

from .compose import Compose, DockerError
from .configuration import create_server_configuration, server_name
from .storage import check_new_configuration, locked_root, write_server_configuration
from .users import user_store


def settings_from_config(config):
    try:
        settings = config["x-xray-setup"]
        name = settings["container_name"]
        domain = settings["domain"]
        if not all(isinstance(value, str) and value for value in (name, domain)):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("В Compose отсутствуют корректные параметры Xray") from error
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        raise ValueError(
            "CONTAINER_NAME должен начинаться с буквы или цифры и содержать только латинские буквы, цифры, _ и -"
        )
    server_name(domain)
    return name, domain


def setup(root, compose):
    name, domain = settings_from_config(compose.config())
    with locked_root(root) as parent:
        check_new_configuration(parent, name + "_data")
        server = create_server_configuration(domain)
        write_server_configuration(parent, name + "_data", server, domain)
        validate_xray(compose)
    print("Сервер:", server_name(domain) + ":443 (TLS через Traefik)")
    print("Созданы серверная конфигурация и пустой реестр в", name + "_data")
    print("Создать пользователя: python3 xray.py users create ИМЯ")
    print("Проверка конфигурации пройдена. Запуск из корня репозитория: ./hctl up xray")


def validate_xray(compose, path=None):
    args = ["run", "--pull", "never", "--rm", "--no-deps"]
    if path is not None:
        args.extend(["--volume", f"{path}:/etc/xray/config.json:ro"])
    compose.run(*args, "app", "run", "-test", "-c", "/etc/xray/config.json")


def check(root, compose):
    name, domain = settings_from_config(compose.config())
    with user_store(root, name, domain) as store:
        store.load(allow_legacy=True)
        validate_xray(compose)
        compose.run("run", "--pull", "never", "--rm", "--no-deps", "site", "-t")
    print("Проверки Compose, Xray и Nginx пройдены")


def manage_users(root, compose, args):
    name, domain = settings_from_config(compose.config())
    with user_store(root, name, domain) as store:
        action = args.user_command
        if action == "list":
            print("Сохранённое состояние (не состояние работающего Xray):")
            users = store.list()
            if not users:
                print("Пользователей нет")
            for user in users:
                print(
                    user["name"],
                    "активен" if user["enabled"] else "приостановлен",
                    sep="\t",
                )
        elif action == "show":
            user, client, uri = store.show(args.name)
            if args.format == "json":
                print(json.dumps(client, indent=2))
            elif args.format == "link":
                print(uri)
            else:
                target = client["outbounds"][0]["settings"]["vnext"][0]
                stream = client["outbounds"][0]["streamSettings"]
                print("Имя:", user["name"])
                print(
                    "Сохранённое состояние:",
                    "активен" if user["enabled"] else "приостановлен",
                )
                print("Сервер:", f"{target['address']}:{target['port']}")
                print("UUID:", user["client"]["id"])
                print("Транспорт: VLESS + XHTTP + TLS; режим packet-up")
                print("SNI / Host:", stream["tlsSettings"]["serverName"])
                print("Путь:", stream["xhttpSettings"]["path"])
                print("ALPN:", ",".join(stream["tlsSettings"]["alpn"]))
                print(uri)
            print(
                "Показаны сохранённые реквизиты; применение изменений — через hctl. Не публикуйте их.",
                file=sys.stderr,
            )
        else:
            if action == "delete" and not args.yes:
                store.show(args.name)
                try:
                    answer = input(f"Удалить пользователя {args.name}? Введите да: ")
                except EOFError:
                    answer = ""
                if answer.strip().lower() != "да":
                    raise RuntimeError("Удаление отменено")
            store.change(action, args.name, lambda path: validate_xray(compose, path))
            print("Изменения сохранены; работающий Xray ещё не изменён.")
            print("Для применения из каталога available/xray:")
            print("  python3 xray.py check")
            print("  ../../hctl restart xray")


def main(root, argv=None):
    parser = argparse.ArgumentParser(
        description="Настройка Xray и пользователи; применение изменений и управление стеком — через hctl"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Создать и проверить сервер без пользователей")
    commands.add_parser("check", help="Проверить Compose, Xray и Nginx без генерации")
    users = commands.add_parser("users", help="Управлять сохранёнными пользователями")
    actions = users.add_subparsers(dest="user_command", required=True)
    actions.add_parser(
        "list", help="Список имён и сохранённых состояний без реквизитов"
    )
    for action, help_text in (
        ("create", "Создать пользователя"),
        ("show", "Показать параметры подключения"),
        ("suspend", "Приостановить доступ, сохранив UUID"),
        ("resume", "Возобновить доступ с прежним UUID"),
        ("delete", "Удалить пользователя"),
        ("import", "Импортировать единственного прежнего пользователя"),
    ):
        command = actions.add_parser(action, help=help_text)
        command.add_argument("name", help="Уникальное имя пользователя")
        if action == "show":
            command.add_argument(
                "--format",
                choices=("text", "json", "link"),
                default="text",
                help="Формат вывода; json и link содержат только профиль или ссылку",
            )
        if action == "delete":
            command.add_argument(
                "--yes", action="store_true", help="Удалить без подтверждения"
            )
    args = parser.parse_args(argv)
    try:
        compose = Compose(root)
        if args.command == "setup":
            setup(root, compose)
        elif args.command == "check":
            check(root, compose)
        elif args.command == "users":
            manage_users(root, compose, args)
    except DockerError as error:
        print("Ошибка:", error, file=sys.stderr)
        return error.returncode if 0 < error.returncode < 126 else 1
    except (OSError, ValueError, RuntimeError) as error:
        # OSError может включать путь; содержимое конфигураций не выводится.
        print("Ошибка:", error, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Операция прервана; перед перезапуском стека повторите команду для проверки и восстановления данных",
            file=sys.stderr,
        )
        return 130
    return 0

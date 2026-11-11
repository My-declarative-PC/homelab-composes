"""Постоянные пользователи Xray; применение в работающем сервисе — через hctl."""

import copy
import os
import re
import uuid
from contextlib import contextmanager

from .configuration import create_client_configuration, server_name
from .storage import directory, exists, locked_root, read_json, write_json

REGISTRY = "users.json"
JOURNAL = ".users-transaction.json"
CANDIDATE = ".users-candidate.json"


def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name
    ):
        raise ValueError(
            "Имя пользователя: 1–64 латинских буквы, цифры, _ или -, первый символ — буква или цифра"
        )
    return name


def inbound_from(server):
    try:
        inbounds = server["inbounds"]
        if len(inbounds) != 1:
            raise ValueError
        inbound = inbounds[0]
        stream = inbound["streamSettings"]
        path = stream["xhttpSettings"]["path"]
        if (
            inbound["protocol"] != "vless"
            or inbound["settings"]["decryption"] != "none"
            or not isinstance(inbound["settings"]["clients"], list)
            or stream["network"] != "xhttp"
            or stream["security"] != "none"
            or not isinstance(path, str)
            or not path.startswith("/api/")
        ):
            raise ValueError
        return inbound
    except (KeyError, TypeError, IndexError, ValueError) as error:
        raise ValueError(
            "Ожидалась конфигурация этого инструмента: один VLESS + XHTTP inbound"
        ) from error


def validate_registry(registry, domain):
    try:
        if registry["version"] != 1 or registry["domain"] != domain:
            raise ValueError
        users = registry["users"]
        if not isinstance(users, list):
            raise TypeError
        names, identifiers = set(), set()
        for user in users:
            name = validate_name(user["name"])
            uid = user["client"]["id"]
            if (
                str(uuid.UUID(uid)) != uid
                or type(user["enabled"]) is not bool
                or name in names
                or uid in identifiers
            ):
                raise ValueError
            names.add(name)
            identifiers.add(uid)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError(
            "Некорректный реестр пользователей или DOMAIN отличается от сохранённого"
        ) from error


def active_clients(registry):
    return [
        copy.deepcopy(user["client"]) for user in registry["users"] if user["enabled"]
    ]


@contextmanager
def user_store(root, name, domain):
    server_name(domain)
    with (
        locked_root(root) as parent,
        directory(parent, name + "_data") as data,
        directory(data, "config") as config,
    ):
        store = UserStore(root / (name + "_data") / "config", data, config, domain)
        store.recover()
        yield store


class UserStore:
    def __init__(self, config_path, data, config, domain):
        self.config_path = config_path
        self.data = data
        self.config = config
        self.domain = domain

    def recover(self):
        """После сбоя откатить оба файла по сохранённому снимку."""
        if exists(self.config, CANDIDATE):
            read_json(self.config, CANDIDATE)
            os.unlink(CANDIDATE, dir_fd=self.config)
        if not exists(self.data, JOURNAL):
            return
        journal = read_json(self.data, JOURNAL)
        try:
            server, registry = journal["server"], journal["registry"]
            inbound = inbound_from(server)
            if registry is not None:
                validate_registry(registry, self.domain)
                if inbound["settings"]["clients"] != active_clients(registry):
                    raise ValueError
            elif len(inbound["settings"]["clients"]) > 1:
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(
                "Повреждён журнал операции пользователей; требуется ручное восстановление"
            ) from error
        write_json(self.config, "config.json", server, mode=0o644, replace=True)
        if registry is None:
            if exists(self.data, REGISTRY):
                os.unlink(REGISTRY, dir_fd=self.data)
        else:
            write_json(self.data, REGISTRY, registry, replace=True)
        os.fsync(self.data)
        os.unlink(JOURNAL, dir_fd=self.data)
        os.fsync(self.data)
        raise RuntimeError(
            "Незавершённая операция отменена; сохранённое состояние восстановлено. Повторите команду"
        )

    def load(self, allow_legacy=False):
        server = read_json(self.config, "config.json")
        inbound = inbound_from(server)
        if exists(self.data, REGISTRY):
            registry = read_json(self.data, REGISTRY)
            validate_registry(registry, self.domain)
            if inbound["settings"]["clients"] != active_clients(registry):
                raise RuntimeError(
                    "Список clients изменён вне инструмента и не совпадает с реестром"
                )
        else:
            registry = None
            if inbound["settings"]["clients"] and not allow_legacy:
                raise RuntimeError(
                    "Обнаружен прежний пользователь; сначала выполните users import ИМЯ"
                )
        return server, registry

    def save(self, server, registry, validate):
        validate_registry(registry, self.domain)
        old_server, old_registry = self.load(allow_legacy=True)
        inbound_from(server)["settings"]["clients"] = active_clients(registry)
        # Одноразовая проверка читает отдельный файл, постоянный JSON ещё не изменён.
        write_json(self.config, CANDIDATE, server, mode=0o644)
        try:
            validate(self.config_path / CANDIDATE)
        finally:
            os.unlink(CANDIDATE, dir_fd=self.config)
        write_json(self.data, JOURNAL, {"server": old_server, "registry": old_registry})
        try:
            write_json(self.config, "config.json", server, mode=0o644, replace=True)
            write_json(self.data, REGISTRY, registry, replace=True)
            os.unlink(JOURNAL, dir_fd=self.data)
            os.fsync(self.data)
        except (OSError, RuntimeError):
            # Журнал остаётся до следующей команды: она восстановит оба старых файла.
            raise RuntimeError(
                "Запись прервана; повторите команду для восстановления. Не перезапускайте стек"
            ) from None

    def change(self, action, name, validate):
        validate_name(name)
        server, registry = self.load(allow_legacy=action == "import")
        if action == "import":
            if registry is not None:
                raise RuntimeError("Реестр уже существует; импорт не требуется")
            clients = inbound_from(server)["settings"]["clients"]
            if len(clients) != 1:
                raise RuntimeError(
                    "Для импорта нужен ровно один существующий пользователь"
                )
            # Проверить соответствие DOMAIN прежнему клиентскому профилю.
            with directory(self.data, "client") as folder:
                old_client = read_json(folder, "client.json")
            try:
                outbound = old_client["outbounds"][0]
                target = outbound["settings"]["vnext"][0]
                path = inbound_from(server)["streamSettings"]["xhttpSettings"]["path"]
                if (
                    target["address"] != server_name(self.domain)
                    or target["users"][0]["id"] != clients[0]["id"]
                    or outbound["streamSettings"]["xhttpSettings"]["path"] != path
                ):
                    raise ValueError
            except (KeyError, TypeError, IndexError, ValueError) as error:
                raise ValueError(
                    "Прежний клиентский профиль не соответствует серверу или DOMAIN"
                ) from error
            registry = {
                "version": 1,
                "domain": self.domain,
                "users": [
                    {"name": name, "enabled": True, "client": copy.deepcopy(clients[0])}
                ],
            }
        else:
            registry = registry or {"version": 1, "domain": self.domain, "users": []}
            users = registry["users"]
            user = next((user for user in users if user["name"] == name), None)
            if action == "create":
                if user:
                    raise ValueError("Пользователь с таким именем уже существует")
                users.append(
                    {"name": name, "enabled": True, "client": {"id": str(uuid.uuid4())}}
                )
            else:
                if user is None:
                    raise ValueError("Пользователь не найден")
                if action == "delete":
                    users.remove(user)
                elif action in ("suspend", "resume"):
                    user["enabled"] = action == "resume"
                else:
                    raise ValueError("Неизвестная операция с пользователем")
        self.save(server, registry, validate)

    def list(self):
        _, registry = self.load()
        return copy.deepcopy(registry["users"] if registry else [])

    def show(self, name):
        validate_name(name)
        server, registry = self.load()
        user = next(
            (
                user
                for user in (registry or {}).get("users", [])
                if user["name"] == name
            ),
            None,
        )
        if user is None:
            raise ValueError("Пользователь не найден")
        path = inbound_from(server)["streamSettings"]["xhttpSettings"]["path"]
        client, uri = create_client_configuration(
            self.domain, user["client"]["id"], path, name
        )
        return copy.deepcopy(user), client, uri

"""Защищённая работа с реквизитами на Linux без перехода по символьным ссылкам."""

import fcntl
import json
import os
import stat
from contextlib import contextmanager

FILES = ("config/config.json", "client/client.json", "client/import-link.txt")
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def directory(parent, name, create=False, mode=0o700):
    if create:
        try:
            os.mkdir(name, mode=mode, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def locked_root(root):
    with directory(None, root) as parent:
        fd = os.open(
            ".xray.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError("Файл блокировки должен быть обычным файлом")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "Другая операция с данными Xray уже выполняется"
                ) from error
            yield parent
        finally:
            # Файл не удаляется: иначе конкурентный запуск может заблокировать другой inode.
            os.close(fd)


def exists(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def check_new_configuration(parent, data_name):
    if not exists(parent, data_name):
        return
    with directory(parent, data_name) as data:
        for relative in FILES:
            folder, name = relative.split("/")
            if exists(data, folder):
                with directory(data, folder) as target:
                    if exists(target, name):
                        raise RuntimeError(
                            "Конфигурация уже существует. Реквизиты не изменены"
                        )


def write_configuration(parent, data_name, server, client, uri):
    with directory(parent, data_name, create=True) as data:
        os.fchmod(data, 0o700)
        for folder, mode, entries in (
            (
                "config",
                0o755,
                (("config.json", json.dumps(server, indent=2) + "\n", 0o644),),
            ),
            (
                "client",
                0o700,
                (
                    ("client.json", json.dumps(client, indent=2) + "\n", 0o600),
                    ("import-link.txt", uri + "\n", 0o600),
                ),
            ),
        ):
            with directory(data, folder, create=True, mode=mode) as target:
                os.fchmod(target, mode)
                for name, content, file_mode in entries:
                    fd = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=target,
                    )
                    with os.fdopen(fd, "w", encoding="utf-8") as file:
                        file.write(content)
                        file.flush()
                        os.fchmod(file.fileno(), file_mode)

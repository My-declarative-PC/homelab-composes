"""Явный перенос старой структуры без преобразования конфигураций."""

import os
import stat
from contextlib import ExitStack

from .storage import directory, exists, locked_root


def migrate_layout(root, data_name):
    with locked_root(root) as parent, ExitStack() as stack:
        if exists(parent, data_name):
            raise RuntimeError(
                "Каталог данных уже существует; смешивание установок запрещено"
            )
        folders = {
            name: stack.enter_context(directory(parent, name))
            for name in ("config", "client")
        }
        files = []
        for folder, name, mode in (
            ("config", "config.json", 0o644),
            ("client", "client.json", 0o600),
            ("client", "import-link.txt", 0o600),
        ):
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=folders[folder],
            )
            stack.callback(os.close, fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError("Для миграции требуются обычные файлы конфигурации")
            files.append((fd, mode))

        # mkdir без exist_ok резервирует назначение и отвергает в том числе битые ссылки.
        os.mkdir(data_name, mode=0o700, dir_fd=parent)
        try:
            with directory(parent, data_name) as data:
                os.fchmod(data, 0o700)
                for name in ("config", "client"):
                    os.rename(name, name, src_dir_fd=parent, dst_dir_fd=data)
                os.fchmod(folders["config"], 0o755)
                os.fchmod(folders["client"], 0o700)
                for fd, mode in files:
                    os.fchmod(fd, mode)
        except OSError as error:
            # Не удаляем и не откатываем автоматически уже перемещённые реквизиты.
            raise RuntimeError(
                "Миграция прервана; данные сохранены в исходных каталогах и/или "
                + data_name
                + ". Проверьте расположение и права вручную; не запускайте setup"
            ) from error

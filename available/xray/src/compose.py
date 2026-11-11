"""Вызовы Docker из каталога стека без исполнения shell-кода."""

import json
import shutil
import subprocess


class DockerError(RuntimeError):
    """Ошибка Docker с безопасным для вывода сообщением."""

    def __init__(self, message, returncode=1):
        super().__init__(message)
        self.returncode = returncode


class Compose:
    def __init__(self, root):
        self.root = root
        self.docker = shutil.which("docker")
        if self.docker is None:
            raise DockerError("Необходима команда: docker")

    def docker_run(self, *args):
        result = subprocess.run(
            [self.docker, *args], cwd=self.root, capture_output=True, text=True
        )
        if result.returncode:
            # Вывод Compose может содержать .env, UUID или случайный путь.
            raise DockerError(
                "Команда Docker завершилась ошибкой; проверьте Docker, Compose и настройки стека",
                result.returncode,
            )
        return result.stdout

    def run(self, *args):
        return self.docker_run("compose", *args)

    def config(self):
        try:
            config = json.loads(self.run("config", "--format", "json"))
            if not isinstance(config, dict):
                raise ValueError
            return config
        except (ValueError, TypeError) as error:
            raise DockerError("Docker Compose вернул некорректный JSON") from error

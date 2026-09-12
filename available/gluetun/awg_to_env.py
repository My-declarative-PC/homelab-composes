#!/usr/bin/env python3
"""Convert one AmneziaWG configuration file to environment variables."""

import argparse
import configparser
import ipaddress
import socket
import sys
from pathlib import Path

INTERFACE_VARIABLES = (
    ("PrivateKey", "AMNEZIAWG_PRIVATE_KEY"),
    ("Address", "AMNEZIAWG_ADDRESSES"),
    ("MTU", "AMNEZIAWG_MTU"),
    ("Jc", "AMNEZIAWG_JC"),
    ("Jmin", "AMNEZIAWG_JMIN"),
    ("Jmax", "AMNEZIAWG_JMAX"),
    ("S1", "AMNEZIAWG_S1"),
    ("S2", "AMNEZIAWG_S2"),
    ("S3", "AMNEZIAWG_S3"),
    ("S4", "AMNEZIAWG_S4"),
    ("H1", "AMNEZIAWG_H1"),
    ("H2", "AMNEZIAWG_H2"),
    ("H3", "AMNEZIAWG_H3"),
    ("H4", "AMNEZIAWG_H4"),
    ("I1", "AMNEZIAWG_I1"),
    ("I2", "AMNEZIAWG_I2"),
    ("I3", "AMNEZIAWG_I3"),
    ("I4", "AMNEZIAWG_I4"),
    ("I5", "AMNEZIAWG_I5"),
)

PEER_VARIABLES = (
    ("PublicKey", "AMNEZIAWG_PUBLIC_KEY"),
    ("PresharedKey", "AMNEZIAWG_PRESHARED_KEY"),
    ("AllowedIPs", "AMNEZIAWG_ALLOWED_IPS"),
)


def quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def parse_config(path: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)

    try:
        with path.open(encoding="utf-8") as config_file:
            config.read_file(config_file)
    except (OSError, configparser.Error) as error:
        raise ValueError(f"cannot read configuration: {error}") from error

    for section in ("Interface", "Peer"):
        if not config.has_section(section):
            raise ValueError(f"missing [{section}] section")

    return config


def split_endpoint(endpoint: str) -> tuple[str, str]:
    if endpoint.startswith("["):
        host, separator, port = endpoint[1:].partition("]:")
        if not separator:
            raise ValueError("Endpoint must use [IPv6]:port notation")
    else:
        host, separator, port = endpoint.rpartition(":")
        if not separator:
            raise ValueError("Endpoint must use host:port notation")

    if not host:
        raise ValueError("Endpoint host is empty")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError("Endpoint port must be between 1 and 65535")

    return host, port


def resolve_endpoint_host(host: str) -> str:
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    try:
        addresses = socket.getaddrinfo(
            host, None, family=socket.AF_INET, type=socket.SOCK_DGRAM
        )
    except socket.gaierror as error:
        raise ValueError(f"cannot resolve Endpoint host {host!r}: {error}") from error

    return str(addresses[0][4][0])


def environment_lines(config: configparser.ConfigParser) -> list[str]:
    lines: list[str] = []

    for source, variable in INTERFACE_VARIABLES:
        value = config.get("Interface", source, fallback=None)
        if value is not None:
            lines.append(f"{variable}={quote_env_value(value)}")

    for source, variable in PEER_VARIABLES:
        value = config.get("Peer", source, fallback=None)
        if value is not None:
            lines.append(f"{variable}={quote_env_value(value)}")

    endpoint = config.get("Peer", "Endpoint", fallback=None)
    if endpoint is not None:
        host, port = split_endpoint(endpoint)
        host = resolve_endpoint_host(host)
        lines.extend(
            (
                f"AMNEZIAWG_ENDPOINT_IP={quote_env_value(host)}",
                f"AMNEZIAWG_ENDPOINT_PORT={quote_env_value(port)}",
            )
        )

    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert an AmneziaWG configuration to AMNEZIAWG_* variables."
    )
    parser.add_argument("config", type=Path, help="path to one AmneziaWG .conf file")
    arguments = parser.parse_args()

    try:
        lines = environment_lines(parse_config(arguments.config))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


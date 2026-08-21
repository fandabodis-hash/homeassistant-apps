"""Prikazovy nastroj Factory Tool pro TNG IQ FANDA."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from factory.factory_info import read_identity
from factory.factory_service import create_identity
from installer_v2.factory_provisioning import (
    provision_factory_v2,
)


def build_parser() -> argparse.ArgumentParser:
    """Sestavi parser prikazove radky."""

    parser = argparse.ArgumentParser(
        prog="factory-tool",
        description=(
            "Vyrobni nastroj pro spravu trvale identity "
            "zarizeni TNG IQ FANDA."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    create_parser = subparsers.add_parser(
        "create",
        help="Vytvori novou vyrobni identitu.",
    )

    create_parser.add_argument(
        "--serial",
        required=True,
        help="Seriove cislo ve formatu F800711-TNG-NNNNN.",
    )

    create_parser.add_argument(
        "--model",
        default="IQ FANDA PI5",
        help="Model zarizeni.",
    )

    create_parser.add_argument(
        "--hardware-revision",
        default="Raspberry Pi 5",
        help="Hardwarova revize zarizeni.",
    )

    create_parser.add_argument(
        "--software-version",
        default="0.1.79",
        help="Verze softwaru instalovana pri vyrobe.",
    )

    create_parser.add_argument(
        "--identity-path",
        type=Path,
        default=None,
        help=(
            "Volitelna cesta k souboru identity. "
            "Pouziva se zejmena pro testovani."
        ),
    )

    subparsers.add_parser(
        "provision-v2",
        help=(
            "Bezpecne vyrobi novy IQ FANDA pres "
            "centralni Factory V2 cloud."
        ),
    )

    info_parser = subparsers.add_parser(
        "info",
        help="Zobrazi existujici vyrobni identitu.",
    )

    info_parser.add_argument(
        "--identity-path",
        type=Path,
        default=None,
        help=(
            "Volitelna cesta k souboru identity. "
            "Pouziva se zejmena pro testovani."
        ),
    )

    return parser


def run_create(
    args: argparse.Namespace,
) -> int:
    try:
        identity = create_identity(
            serial_number=args.serial,
            model=args.model,
            hardware_revision=(
                args.hardware_revision
            ),
            software_version=(
                args.software_version
            ),
            identity_path=(
                args.identity_path
            ),
        )

    except (
        ValueError,
        FileExistsError,
    ) as error:
        print(
            f"CHYBA: {error}",
            file=sys.stderr,
        )
        return 2

    except OSError as error:
        print(
            f"CHYBA PRI ZAPISU IDENTITY: {error}",
            file=sys.stderr,
        )
        return 3

    print(
        "Vyrobni identita byla uspesne vytvorena."
    )

    print(
        json.dumps(
            identity,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    return 0


def _factory_admin_token() -> str:
    token = str(
        os.getenv(
            "IQF_FACTORY_ADMIN_TOKEN",
            "",
        )
        or ""
    ).strip()

    if token:
        return token

    if not sys.stdin.isatty():
        raise RuntimeError(
            "Chybi IQF_FACTORY_ADMIN_TOKEN "
            "a neni dostupny interaktivni terminal."
        )

    token = getpass.getpass(
        "Factory admin bearer token: "
    ).strip()

    if not token:
        raise RuntimeError(
            "Factory admin bearer token je prazdny."
        )

    return token


def run_provision_v2() -> int:
    try:
        result = provision_factory_v2(
            admin_token=(
                _factory_admin_token()
            )
        )

    except Exception as error:
        print(
            f"FACTORY V2 CHYBA: {error}",
            file=sys.stderr,
        )
        return 4

    print(
        "Factory V2 provisioning byl uspesne dokoncen."
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    return 0


def run_info(
    args: argparse.Namespace,
) -> int:
    try:
        if args.identity_path is None:
            identity = read_identity()
        else:
            identity = read_identity(
                identity_path=(
                    args.identity_path
                ),
            )

    except (
        OSError,
        ValueError,
    ) as error:
        print(
            f"CHYBA PRI CTENI IDENTITY: {error}",
            file=sys.stderr,
        )
        return 3

    if not identity.get(
        "identity_exists"
    ):
        print(
            "CHYBA: Vyrobni identita neexistuje.",
            file=sys.stderr,
        )
        return 2

    print(
        "Vyrobni identita byla nalezena."
    )

    print(
        json.dumps(
            identity,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    return 0


def main(
    argv: Sequence[str] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(
        argv
    )

    if args.command == "create":
        return run_create(
            args
        )

    if (
        args.command
        == "provision-v2"
    ):
        return run_provision_v2()

    if args.command == "info":
        return run_info(
            args
        )

    parser.error(
        f"Nepodporovany prikaz: {args.command}"
    )

    return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
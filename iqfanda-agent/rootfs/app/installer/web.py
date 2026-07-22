"""Obsluha webovych souboru Installeru TNG IQ FANDA."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import unquote


ADRESAR_INSTALLERU = Path(__file__).resolve().parent
ADRESAR_SABLON = ADRESAR_INSTALLERU / "templates"
ADRESAR_STATIC = ADRESAR_INSTALLERU / "static"

SOUBOR_UVODNI_STRANKY = ADRESAR_SABLON / "index.html"

VYCHOZI_TYP_OBSAHU = "application/octet-stream"

PREPISY_MIME_TYPU = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
}


@dataclass(frozen=True, slots=True)
class WebOdpoved:
    """Datova odpoved webove vrstvy."""

    status: HTTPStatus
    typ_obsahu: str
    telo: bytes
    cache_control: str = "no-store"


def nacist_uvodni_stranku() -> WebOdpoved:
    """Nacte hlavni HTML stranku Installeru."""

    return _nacist_soubor(
        cesta=SOUBOR_UVODNI_STRANKY,
        koren=ADRESAR_SABLON,
        cache_control="no-store",
    )


def nacist_staticky_soubor(
    url_cesta: str,
) -> WebOdpoved:
    """Nacte soubor z adresare static podle URL cesty."""

    relativni_cesta = _ziskat_relativni_static_cestu(
        url_cesta
    )

    if relativni_cesta is None:
        return _vytvorit_chybovou_odpoved(
            status=HTTPStatus.NOT_FOUND,
            zprava="Statický soubor nebyl nalezen.",
        )

    soubor = ADRESAR_STATIC / relativni_cesta

    return _nacist_soubor(
        cesta=soubor,
        koren=ADRESAR_STATIC,
        cache_control="no-cache",
    )


def _ziskat_relativni_static_cestu(
    url_cesta: str,
) -> Path | None:
    """Prevede URL /static/... na bezpecnou relativni cestu."""

    dekodovana_cesta = unquote(url_cesta)

    prefix = "/static/"

    if not dekodovana_cesta.startswith(prefix):
        return None

    relativni_text = dekodovana_cesta[len(prefix):].strip()

    if not relativni_text:
        return None

    relativni_cesta = Path(relativni_text)

    if relativni_cesta.is_absolute():
        return None

    if ".." in relativni_cesta.parts:
        return None

    return relativni_cesta


def _nacist_soubor(
    *,
    cesta: Path,
    koren: Path,
    cache_control: str,
) -> WebOdpoved:
    """Nacte soubor pouze tehdy, pokud lezi uvnitr povoleneho korene."""

    try:
        skutecny_koren = koren.resolve(strict=True)
    except FileNotFoundError:
        return _vytvorit_chybovou_odpoved(
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            zprava="Adresář webového rozhraní nebyl nalezen.",
        )

    try:
        skutecna_cesta = cesta.resolve(strict=True)
    except FileNotFoundError:
        return _vytvorit_chybovou_odpoved(
            status=HTTPStatus.NOT_FOUND,
            zprava="Požadovaný webový soubor nebyl nalezen.",
        )

    if not skutecna_cesta.is_file():
        return _vytvorit_chybovou_odpoved(
            status=HTTPStatus.NOT_FOUND,
            zprava="Požadovaný webový soubor nebyl nalezen.",
        )

    if not skutecna_cesta.is_relative_to(skutecny_koren):
        return _vytvorit_chybovou_odpoved(
            status=HTTPStatus.FORBIDDEN,
            zprava="Přístup k požadovanému souboru není povolen.",
        )

    try:
        telo = skutecna_cesta.read_bytes()
    except OSError:
        return _vytvorit_chybovou_odpoved(
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
            zprava="Webový soubor se nepodařilo načíst.",
        )

    return WebOdpoved(
        status=HTTPStatus.OK,
        typ_obsahu=_ziskat_typ_obsahu(
            skutecna_cesta
        ),
        telo=telo,
        cache_control=cache_control,
    )


def _ziskat_typ_obsahu(
    cesta: Path,
) -> str:
    """Vrati MIME typ souboru."""

    pripona = cesta.suffix.lower()

    if pripona in PREPISY_MIME_TYPU:
        return PREPISY_MIME_TYPU[pripona]

    odhadnuty_typ, _ = mimetypes.guess_type(
        cesta.name
    )

    return odhadnuty_typ or VYCHOZI_TYP_OBSAHU


def _vytvorit_chybovou_odpoved(
    *,
    status: HTTPStatus,
    zprava: str,
) -> WebOdpoved:
    """Vytvori jednoduchou textovou chybovou odpoved."""

    return WebOdpoved(
        status=status,
        typ_obsahu="text/plain; charset=utf-8",
        telo=zprava.encode("utf-8"),
        cache_control="no-store",
    )

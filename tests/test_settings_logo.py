"""Tests del logo de empresa que se incrusta en los PDFs."""

import pytest
from pydantic import ValidationError

from app.schemas import SettingsIn

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def test_acepta_data_uri_de_imagen():
    assert SettingsIn(company_logo=PNG).company_logo == PNG


def test_acepta_svg():
    assert SettingsIn(company_logo="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=")


def test_vacio_significa_sin_logo():
    assert SettingsIn(company_logo="").company_logo == ""


@pytest.mark.parametrize(
    "valor",
    [
        # El logo se interpola en src="..." al construir el PDF: unas comillas
        # sueltas romperían el atributo e inyectarían HTML.
        'data:image/png;base64,AAA=" onerror="alert(1)',
        "https://ejemplo.com/logo.png",   # externo: no cargaría a tiempo al imprimir
        "data:text/html;base64,PHNjcmlwdD4=",
        "javascript:alert(1)",
    ],
)
def test_rechaza_lo_que_no_es_imagen_incrustada(valor):
    with pytest.raises(ValidationError):
        SettingsIn(company_logo=valor)

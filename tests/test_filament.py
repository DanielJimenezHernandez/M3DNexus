"""Tests del parser de nombres de filamento (datos reales de Moonraker)."""

from app.services.filament import density_for, parse_filament


def test_parse_marca_tipo_color():
    p = parse_filament("Elegoo PLA+ White @ Hi 0.4 Nozzle", "PLA")
    assert p.full_name == "Elegoo PLA+ White"
    assert p.brand == "Elegoo"
    assert p.material_type == "PLA"
    assert p.color == "White"


def test_parse_sin_color():
    p = parse_filament("ERYONE PLA @ HI 0.4 Nozzle", "PLA")
    assert p.full_name == "ERYONE PLA"
    assert p.brand == "ERYONE"
    assert p.color is None


def test_parse_marca_multipalabra_color_multipalabra():
    p = parse_filament("Polymaker PolyTerra PLA Army Green @ X", "PLA")
    assert p.brand == "Polymaker PolyTerra"
    assert p.color == "Army Green"


def test_parse_multimaterial_toma_el_primero():
    raw = 'Elegoo PLA+ White @ Hi 0.4 Nozzle";"Elegoo PLA+ White @ Hi 0.4 Nozzle'
    p = parse_filament(raw, "PLA;PLA")
    assert p.full_name == "Elegoo PLA+ White"
    assert p.material_type == "PLA"


def test_parse_sin_tipo_detecta_conocido():
    p = parse_filament("Sunlu PETG Black @ Y", None)
    assert p.material_type == "PETG"
    assert p.brand == "Sunlu"
    assert p.color == "Black"


def test_parse_vacio():
    assert parse_filament("", "PLA") is None
    assert parse_filament(None, None) is None


# --- Convención recomendada "Marca | Tipo | Color" --------------------------
def test_estructurado_tres_campos():
    p = parse_filament("Elegoo | PLA+ | White @ Hi 0.4 Nozzle", "PLA")
    assert p.brand == "Elegoo"
    assert p.material_type == "PLA"          # canónico desde metadatos
    assert p.color == "White"
    assert p.full_name == "Elegoo PLA+ White"  # nombre limpio, sin '|'


def test_estructurado_marca_y_color_multipalabra():
    p = parse_filament("Polymaker PolyTerra | PLA | Army Green @ X", "PLA")
    assert p.brand == "Polymaker PolyTerra"
    assert p.color == "Army Green"
    assert p.material_type == "PLA"


def test_estructurado_sin_color():
    p = parse_filament("eSun | PETG | @ X", "PETG")
    assert p.brand == "eSun"
    assert p.material_type == "PETG"
    assert p.color is None
    assert p.full_name == "eSun PETG"


def test_estructurado_tipo_del_nombre_si_no_hay_metadato():
    p = parse_filament("Bambu | ASA | Gray @ X", None)
    assert p.material_type == "ASA"
    assert p.color == "Gray"


def test_estructurado_dos_campos_marca_color():
    # "Marca | Color" (sin tipo): el tipo sale de los metadatos.
    p = parse_filament("Sunlu | Negro @ X", "PLA")
    assert p.brand == "Sunlu"
    assert p.material_type == "PLA"
    assert p.color == "Negro"


def test_density_por_tipo():
    assert density_for("PLA") == 1.24
    assert density_for("PETG") == 1.27
    assert density_for("ABS") == 1.04
    assert density_for("ASA") == 1.07
    assert density_for("PLA+") == 1.24      # ignora el '+'
    assert density_for("desconocido") == 1.24  # fallback PLA


def test_sufijo_de_impresora_sin_espacio_tras_la_arroba():
    """El sufijo "@impresora" se quita aunque no lleve espacio detrás.

    Con el corte por " @ " estricto, nombres reales como este dejaban el modelo
    de la impresora dentro del material y duplicaban el mismo filamento.
    """
    p = parse_filament("ELEGOO PLA Marble @Ender 0.4 nozzle", "PLA")
    assert p.full_name == "ELEGOO PLA Marble"

    p = parse_filament("Elegoo Black ABS @K1SE 0.4 Nozzle", "ABS")
    assert p.full_name == "Elegoo Black ABS"

    # Y se sigue quitando con espacios a ambos lados, como antes.
    p = parse_filament("Elegoo PLA White @ Hi 0.4 Nozzle", "PLA")
    assert p.full_name == "Elegoo PLA White"

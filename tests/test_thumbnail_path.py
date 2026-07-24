"""Tests de la ruta de miniatura de un gcode (compartida por live y estimación)."""

from app.integrations.moonraker import thumbnail_path


def test_elige_la_mas_grande_y_la_hace_relativa_a_gcodes():
    meta = {"thumbnails": [
        {"width": 32, "height": 32, "relative_path": ".thumbs/p-32x32.png"},
        {"width": 400, "height": 300, "relative_path": ".thumbs/p-400x300.png"},
    ]}
    assert thumbnail_path("proyectos/p.gcode", meta) == "proyectos/.thumbs/p-400x300.png"


def test_gcode_en_la_raiz():
    meta = {"thumbnails": [{"width": 8, "relative_path": ".thumbs/x.png"}]}
    assert thumbnail_path("x.gcode", meta) == ".thumbs/x.png"


def test_sin_miniaturas_es_none():
    assert thumbnail_path("x.gcode", {}) is None
    assert thumbnail_path("x.gcode", {"thumbnails": []}) is None
    assert thumbnail_path("x.gcode", None) is None


def test_entrada_sin_relative_path_es_none():
    assert thumbnail_path("x.gcode", {"thumbnails": [{"width": 8}]}) is None

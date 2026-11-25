"""
Tests minimos. Correr con:  python -m pytest -q
Antes de cada cambio grande al motor, correr los tests: si el nesting deja de
respetar la separacion o pierde piezas, estos tests fallan.
"""
import math
from pathlib import Path

import ezdxf
import pytest

from nesting.io_dxf import leer_piezas, cantidad_desde_nombre
from nesting.motor import ConfigNesting
from nesting.pipeline import ejecutar

DATOS = Path(__file__).parent / 'datos'


def test_cantidad_desde_nombre():
    assert cantidad_desde_nombre('BRIDA_D1352_en_4P_CANT_40') == 40
    assert cantidad_desde_nombre('pieza cant 3') == 3
    assert cantidad_desde_nombre('tapa_x12') == 12
    assert cantidad_desde_nombre('sin_cantidad') == 1


def test_lector_encadena_lineas_y_arcos():
    piezas = leer_piezas(DATOS / 'sintetico_2piezas_CANT_3.dxf')
    assert len(piezas) == 2                              # 2 piezas en un archivo
    a = max(piezas, key=lambda p: p.area)
    assert len(a.poligono.interiors) == 1                # agujero detectado
    esperado = 300 * 200 - (4 - math.pi) * 20 ** 2 - math.pi * 40 ** 2
    assert abs(a.area - esperado) / esperado < 1e-4      # arcos bien discretizados (<0.01%)
    assert all(p.cantidad == 3 for p in piezas)


def _dxf_rect(path, w, h, cant):
    doc = ezdxf.new('R2000')
    doc.modelspace().add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True)
    doc.saveas(path / f'rect_{w}x{h}_CANT_{cant}.dxf')


def test_nesting_respeta_separacion_y_cantidades(tmp_path):
    ent = tmp_path / 'entrada'; ent.mkdir()
    _dxf_rect(ent, 400, 300, 6)
    _dxf_rect(ent, 250, 250, 5)
    cfg = ConfigNesting(largo=1500, ancho=1000, gap=10, margen=10, poblacion=8, generaciones=3,
                        usar_gpu=False, procesos=1, res_refinado=(3.0,))
    r = ejecutar(ent, tmp_path / 'salida', cfg, prefijo='T', log=lambda *a: None)
    assert r['verificado']
    assert r['separacion_minima_mm'] >= 10 - 1e-6
    # todas las piezas presentes en los DXF de salida
    n = 0
    for f in r['archivos']:
        n += sum(1 for e in ezdxf.readfile(f).modelspace() if e.dxftype() == 'LWPOLYLINE')
    assert n == 11

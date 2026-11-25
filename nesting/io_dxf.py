"""
io_dxf.py — Lectura de piezas desde DXF y escritura del nesting
===============================================================

LECTURA
-------
Un DXF puede traer UNA o VARIAS piezas, dibujadas con cualquier mezcla de:
LWPOLYLINE, POLYLINE, LINE, ARC, CIRCLE, ELLIPSE, SPLINE (tambien dentro de bloques).

Pasos:
1. Explota los bloques (INSERT) a entidades simples.
2. Las entidades ya cerradas (circulo, polyline cerrada, elipse completa) son lazos.
3. Las entidades abiertas (LINE, ARC, polylines abiertas) se ENCADENAN por sus
   extremos (tolerancia TOL) hasta cerrar lazos.
4. Cada lazo se convierte en poligono (shapely). Los lazos que quedan DENTRO de
   otro son agujeros; el de afuera es el contorno de la pieza.
5. Cada pieza guarda sus entidades ORIGINALES, para exportarlas sin perder
   precision (arcos y circulos exactos, capas originales).

CANTIDAD: se lee del nombre del archivo, patron "CANT_40" o "CANT 40" o "x40".
Si no hay patron, cantidad = 1 (se puede cambiar en la configuracion).

ESCRITURA
---------
Cada pieza ubicada = entidades originales * matriz (normalizacion -> rotacion -> traslacion).
Solo rotacion + traslacion (sin espejado), asi bulges, arcos y circulos quedan exactos.
"""
from __future__ import annotations
import math, re
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import path as ezpath
from ezdxf.math import Matrix44, Vec3
from shapely.geometry import Polygon
from shapely.ops import unary_union

TOL = 0.05          # mm, tolerancia para unir extremos
FLAT = 0.05         # mm, error maximo al discretizar arcos para el poligono
TIPOS_GEOM = {'LINE', 'ARC', 'CIRCLE', 'LWPOLYLINE', 'POLYLINE', 'ELLIPSE', 'SPLINE'}
CAPAS_IGNORADAS_DEFAULT = {'COTAS', 'TEXTO', 'DEFPOINTS', 'EJES', 'REFERENCIA'}


@dataclass
class Pieza:
    nombre: str                  # nombre legible (archivo + indice)
    poligono: Polygon            # geometria normalizada (con agujeros)
    entidades: list              # entidades ezdxf originales
    matriz_norm: Matrix44        # lleva la pieza original al sistema normalizado
    cantidad: int = 1
    capa_contorno: str = '0'
    advertencias: list = field(default_factory=list)

    @property
    def area(self) -> float:
        return self.poligono.area


# ------------------------------------------------------------------ utilidades
def cantidad_desde_nombre(nombre: str, default: int = 1) -> int:
    m = re.search(r'CANT[\s_\-]*(\d+)', nombre, re.I) or re.search(r'[_\s]x(\d+)\b', nombre, re.I)
    return int(m.group(1)) if m else default


def _explotar(msp):
    """Todas las entidades geometricas, explotando bloques en forma recursiva."""
    out = []
    for e in msp:
        t = e.dxftype()
        if t == 'INSERT':
            try:
                out += [v for v in e.virtual_entities() if v.dxftype() in TIPOS_GEOM | {'INSERT'}]
            except Exception:
                pass
        elif t in TIPOS_GEOM:
            out.append(e)
    # segunda pasada para INSERT anidados
    if any(e.dxftype() == 'INSERT' for e in out):
        return _explotar(out)
    return out


def _es_cerrada(e) -> bool:
    t = e.dxftype()
    if t == 'CIRCLE':
        return True
    if t == 'LWPOLYLINE':
        return e.closed
    if t == 'POLYLINE':
        return e.is_closed
    if t == 'ELLIPSE':
        return abs((e.dxf.end_param - e.dxf.start_param) - 2 * math.pi) < 1e-6
    if t == 'SPLINE':
        return e.closed
    return False


def _path(e):
    return ezpath.make_path(e)


def _extremos(p):
    return Vec3(p.start), Vec3(p.end)


def _encadenar(abiertas, advertencias):
    """Une entidades abiertas por sus extremos. Devuelve lista de (entidades, path)."""
    items = [(e, _path(e)) for e in abiertas]
    usados = [False] * len(items)
    lazos = []
    for i in range(len(items)):
        if usados[i]:
            continue
        usados[i] = True
        cadena = [items[i][0]]
        p = items[i][1]
        ini, fin = _extremos(p)
        cambio = True
        while cambio and not fin.isclose(ini, abs_tol=TOL):
            cambio = False
            for j in range(len(items)):
                if usados[j]:
                    continue
                pj = items[j][1]
                a, b = _extremos(pj)
                if a.isclose(fin, abs_tol=TOL):
                    p.append_path(pj); fin = b
                elif b.isclose(fin, abs_tol=TOL):
                    p.append_path(pj.reversed()); fin = a
                else:
                    continue
                usados[j] = True; cadena.append(items[j][0]); cambio = True
                break
        if fin.isclose(ini, abs_tol=TOL):
            lazos.append((cadena, p))
        else:
            advertencias.append(f'Contorno abierto ({len(cadena)} entidades) en {ini.round(2)} -> {fin.round(2)}: ignorado')
    return lazos


def _a_poligono(p) -> Polygon | None:
    pts = [(v.x, v.y) for v in p.flattening(FLAT)]
    if len(pts) < 3:
        return None
    pol = Polygon(pts)
    if not pol.is_valid:
        pol = pol.buffer(0)
    if pol.geom_type != 'Polygon' or pol.area < 1e-3:
        return None
    return pol


# ------------------------------------------------------------------ lectura
def leer_piezas(archivo: str | Path, cantidad: int | None = None,
                capas_ignoradas: set[str] | None = None) -> list[Pieza]:
    """Lee un DXF y devuelve sus piezas. 'cantidad' pisa la del nombre del archivo."""
    archivo = Path(archivo)
    capas_ignoradas = {c.upper() for c in (capas_ignoradas or CAPAS_IGNORADAS_DEFAULT)}
    doc = ezdxf.readfile(archivo)
    ents = [e for e in _explotar(doc.modelspace()) if e.dxf.layer.upper() not in capas_ignoradas]
    adv = []

    lazos = [([e], _path(e)) for e in ents if _es_cerrada(e)]
    lazos += _encadenar([e for e in ents if not _es_cerrada(e)], adv)

    polys = []
    for cadena, p in lazos:
        pol = _a_poligono(p)
        if pol is not None:
            polys.append((pol, cadena))
        else:
            adv.append('Lazo degenerado ignorado')

    # contornos exteriores: lazos que no estan dentro de ningun otro
    polys.sort(key=lambda x: -x[0].area)
    padre = [None] * len(polys)
    for i, (pi, _) in enumerate(polys):
        rp = pi.representative_point()
        for j in range(i):                     # solo pueden contenerlo los mas grandes
            if polys[j][0].contains(rp) and polys[j][0].area > pi.area:
                padre[i] = j                    # el ultimo (mas chico) que lo contiene
    # nivel de anidamiento: par = contorno exterior, impar = agujero
    def nivel(i):
        n = 0
        while padre[i] is not None:
            i = padre[i]; n += 1
        return n

    cant = cantidad if cantidad is not None else cantidad_desde_nombre(archivo.stem)
    piezas = []
    exteriores = [i for i in range(len(polys)) if nivel(i) % 2 == 0]
    for n_p, i in enumerate(exteriores):
        agujeros = [k for k in range(len(polys)) if padre[k] == i]
        shell = polys[i][0]
        holes = [polys[k][0].exterior.coords for k in agujeros]
        pol = Polygon(shell.exterior.coords, holes)
        if not pol.is_valid:
            pol = pol.buffer(0)
        entidades = list(polys[i][1]) + [e for k in agujeros for e in polys[k][1]]
        M = _normalizar(pol)
        pol_n = _transformar_poligono(pol, M)
        nombre = archivo.stem if len(exteriores) == 1 else f'{archivo.stem}#{n_p + 1}'
        piezas.append(Pieza(nombre, pol_n, entidades, M, cant,
                            capa_contorno=polys[i][1][0].dxf.layer, advertencias=list(adv)))
    return piezas


def _normalizar(pol: Polygon) -> Matrix44:
    """Matriz que lleva la pieza a su rectangulo minimo alineado con los ejes y la
    esquina inferior izquierda al origen. Asi todas las piezas arrancan 'derechas'."""
    mrr = pol.minimum_rotated_rectangle
    c = list(mrr.exterior.coords)
    lados = [(c[k], c[k + 1]) for k in range(4)]
    (x0, y0), (x1, y1) = max(lados, key=lambda s: math.dist(*s))
    ang = math.atan2(y1 - y0, x1 - x0)
    R = Matrix44.z_rotate(-ang)
    pr = _transformar_poligono(pol, R)
    minx, miny, _, _ = pr.bounds
    return Matrix44.chain(R, Matrix44.translate(-minx, -miny, 0))


def _transformar_poligono(pol: Polygon, M: Matrix44) -> Polygon:
    f = lambda cs: [(v.x, v.y) for v in M.transform_vertices([Vec3(x, y) for x, y in cs])]
    return Polygon(f(pol.exterior.coords), [f(i.coords) for i in pol.interiors])


def leer_carpeta(carpeta: str | Path, **kw) -> list[Pieza]:
    piezas = []
    for f in sorted(Path(carpeta).glob('*.dxf')) + sorted(Path(carpeta).glob('*.DXF')):
        piezas += leer_piezas(f, **kw)
    return piezas


# ------------------------------------------------------------------ escritura
def escribir_chapa(archivo: str | Path, colocaciones, version: str = 'R2000',
                   capa_unica: str | None = None, contorno_chapa: tuple | None = None):
    """colocaciones: lista de (Pieza, Matrix44 de colocacion en el sistema normalizado).
    capa_unica: si se indica, todo va a esa capa (ej. 'CORTE'); si no, se respetan las originales."""
    doc = ezdxf.new(version)
    doc.header['$INSUNITS'] = 4          # milimetros
    msp = doc.modelspace()

    def capa(nombre):
        nombre = capa_unica or nombre
        if nombre not in doc.layers:
            doc.layers.add(nombre)
        return nombre

    for pieza, Mcol in colocaciones:
        M = Matrix44.chain(pieza.matriz_norm, Mcol)
        for e in pieza.entidades:
            t = e.dxftype()
            att = {'layer': capa(e.dxf.layer)}
            if t == 'LWPOLYLINE':
                e2 = e.copy(); e2.transform(M)
                msp.add_lwpolyline(e2.get_points('xyb'), format='xyb', close=e.closed, dxfattribs=att)
            elif t == 'CIRCLE':
                msp.add_circle(M.transform(e.dxf.center), e.dxf.radius, dxfattribs=att)
            elif t == 'LINE':
                msp.add_line(M.transform(e.dxf.start), M.transform(e.dxf.end), dxfattribs=att)
            elif t == 'ARC':
                rot = math.degrees(math.atan2(M.transform_direction(Vec3(1, 0, 0)).y,
                                              M.transform_direction(Vec3(1, 0, 0)).x))
                msp.add_arc(M.transform(e.dxf.center), e.dxf.radius,
                            e.dxf.start_angle + rot, e.dxf.end_angle + rot, dxfattribs=att)
            else:
                # POLYLINE, ELLIPSE, SPLINE: se convierten a LWPOLYLINE fina (tol. FLAT)
                p = ezpath.make_path(e).transform(M)
                pts = [(v.x, v.y) for v in p.flattening(FLAT)]
                msp.add_lwpolyline(pts, close=_es_cerrada(e), dxfattribs=att)
    if contorno_chapa:
        L, A = contorno_chapa
        if 'CHAPA' not in doc.layers:
            doc.layers.add('CHAPA', color=8)
        msp.add_lwpolyline([(0, 0), (L, 0), (L, A), (0, A)], close=True, dxfattribs={'layer': 'CHAPA'})
    doc.saveas(archivo)

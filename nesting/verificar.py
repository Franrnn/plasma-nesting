"""
verificar.py — Control de calidad con geometria EXACTA (shapely), independiente del raster.
Chequea: cada pieza dentro de la chapa menos el margen, y distancia entre piezas >= gap.
Usa un indice espacial (STRtree) para no comparar todos contra todos.
"""
from shapely.affinity import rotate, translate
from shapely.geometry import box
from shapely.strtree import STRtree


def colocar_poligono(P, ang, dx, dy):
    return translate(rotate(P, ang, origin=(0, 0)), dx, dy)


def verificar(layout, polys, cfg, tol=1e-6):
    caja = box(cfg.margen, cfg.margen, cfg.largo - cfg.margen, cfg.ancho - cfg.margen).buffer(tol)
    por_chapa = {}
    for c in layout:
        P = colocar_poligono(polys[c['tipo']], c['ang'], c['dx'], c['dy'])
        por_chapa.setdefault(c['chapa'], []).append(P)
    ok, dmin, fuera = True, float('inf'), 0
    for ps in por_chapa.values():
        fuera += sum(not caja.contains(p) for p in ps)
        arbol = STRtree(ps)
        for i, p in enumerate(ps):
            for j in arbol.query(p.buffer(cfg.gap * 1.5)):
                if j <= i:
                    continue
                d = p.distance(ps[j]); dmin = min(dmin, d)
                if d < cfg.gap - tol:
                    ok = False
    if fuera:
        ok = False
    return ok, dict(dmin=dmin if dmin < float('inf') else cfg.gap, fuera=fuera, por_chapa=por_chapa)

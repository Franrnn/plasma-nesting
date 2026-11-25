"""
motor.py — Motor de nesting: decodificador Bottom-Left raster + Algoritmo Genetico
=================================================================================

Resumen del metodo (ver README para la explicacion completa):

  CROMOSOMA  = orden de colocacion de las instancias + angulo de cada una
  DECODIFICADOR (Bottom-Left Fill): ubica cada pieza en la posicion libre mas a la
             izquierda y despues mas abajo; si no entra, prueba la chapa siguiente.
  FITNESS    = 1e6 * chapas_usadas + largo_usado_en_la_ultima_chapa  (mm)
             -> primero menos chapas; a igual chapas, el retazo mas grande.
  GA         = torneo + Order Crossover (OX) + mutaciones + elitismo.

Deteccion de choques: la chapa es una grilla de celdas de RES mm. La busqueda de
posiciones libres es una correlacion por FFT entre la ocupacion de la chapa y la
mascara de la pieza (prueba todas las posiciones a la vez). En Colab con GPU se usa
CuPy (misma API que numpy) y es varias veces mas rapido.

Garantia de separacion: pieza pintada engordada r = RES*sqrt(2)/2 y lo ya colocado
engordado GAP + r. Si no comparten celdas, la distancia real es >= GAP.
Igual el resultado final se verifica con geometria exacta (verificar.py).

PIEZA DENTRO DE PIEZA: si pieza_en_pieza=True, los agujeros de las piezas son
espacio libre (ej. una brida chica dentro de una grande).
"""
from __future__ import annotations
import math, random, time
from dataclasses import dataclass, field
import numpy as np
from matplotlib.path import Path as MplPath
from shapely.affinity import rotate as s_rotate
from shapely.geometry import Polygon

# ------------------------------------------------------------------ backend CPU / GPU
try:
    import cupy as cp
    from cupyx.scipy.signal import fftconvolve as fft_gpu
    cp.zeros(1)                                   # falla si no hay GPU
    GPU_DISPONIBLE = True
except Exception:
    cp, fft_gpu, GPU_DISPONIBLE = None, None, False
from scipy.signal import fftconvolve as fft_cpu


@dataclass
class ConfigNesting:
    largo: float = 3000.0            # mm, chapa
    ancho: float = 1500.0
    margen: float = 10.0             # mm, al borde de chapa
    gap: float = 10.0                # mm, entre piezas
    res: float = 5.0                 # mm, celda de la grilla de busqueda
    paso_angulo: int = 15            # grados (90 = solo ortogonal)
    pieza_en_pieza: bool = True
    poblacion: int = 32
    generaciones: int = 100
    tiempo_max_s: float = 900        # corta el GA si se pasa de este tiempo
    paciencia: int = 80              # generaciones sin mejora antes de cortar
    estancamiento: int = 15          # cada cuantas generaciones sin mejora entran inmigrantes
    top_refinado: int = 5            # cuantos de los mejores se re-decodifican al final
    reinicios: int = 1               # corridas independientes con semillas distintas (multi-start)
    semilla: int = 7
    usar_gpu: bool = True
    procesos: int = 2                # solo CPU
    res_refinado: tuple = (3.0, 2.0) # re-decodificacion final del mejor individuo
    espesor: float = 7.9375          # mm (solo para reporte de kg)
    densidad: float = 7.85e-6        # kg/mm3 acero
    extras: dict = field(default_factory=dict)


# ------------------------------------------------------------------ raster
def rasterizar(poly: Polygon, res: float, engorde: float):
    P = poly.buffer(engorde, 16) if engorde > 0 else poly
    minx, miny, maxx, maxy = P.bounds
    nx = int(math.ceil((maxx - minx) / res)) + 1
    ny = int(math.ceil((maxy - miny) / res)) + 1
    xs = minx + (np.arange(nx) + 0.5) * res
    ys = miny + (np.arange(ny) + 0.5) * res
    X, Y = np.meshgrid(xs, ys)
    pts = np.column_stack([X.ravel(), Y.ravel()])
    dentro = np.zeros(len(pts), bool)
    for g in ([P] if P.geom_type == 'Polygon' else list(P.geoms)):
        m = MplPath(np.asarray(g.exterior.coords)).contains_points(pts)
        for it in g.interiors:
            m &= ~MplPath(np.asarray(it.coords)).contains_points(pts)
        dentro |= m
    return dentro.reshape(ny, nx), xs[0], ys[0]


def angulos_permitidos(nombre: str, paso: int) -> list[int]:
    """Etiquetas en el nombre del archivo: NOROT -> solo 0 ; ROT180 -> 0/180 ; ROT90 -> 0/90/180/270."""
    n = nombre.upper()
    if 'NOROT' in n:
        return [0]
    if 'ROT180' in n:
        return [0, 180]
    if 'ROT90' in n:
        return [0, 90, 180, 270]
    return list(range(0, 360, paso))


class Biblioteca:
    """Mascaras pre-calculadas de cada tipo de pieza en cada angulo permitido."""

    def __init__(self, polys: list[Polygon], angulos: list[list[int]], cfg: ConfigNesting, res: float):
        self.res = res
        self.xp = cp if (cfg.usar_gpu and GPU_DISPONIBLE) else np
        r = res * math.sqrt(2) / 2
        self.m = {}
        for t, (P, angs) in enumerate(zip(polys, angulos)):
            if not cfg.pieza_en_pieza:
                P = Polygon(P.exterior)
            for a in angs:
                Pa = s_rotate(P, a, origin=(0, 0))
                pieza, px0, py0 = rasterizar(Pa, res, r)
                zona, zx0, zy0 = rasterizar(Pa, res, cfg.gap + r)
                self.m[(t, a)] = dict(
                    kern=self.xp.asarray(pieza[::-1, ::-1].astype(np.float32)),
                    zona=self.xp.asarray(zona.astype(np.float32)),
                    h=pieza.shape[0], w=pieza.shape[1], px0=px0, py0=py0,
                    ox=int(round((zx0 - px0) / res)), oy=int(round((zy0 - py0) / res)))


class Chapa:
    def __init__(self, cfg: ConfigNesting, res: float, xp):
        self.xp, self.res = xp, res
        self.nx = int((cfg.largo - 2 * cfg.margen) // res)
        self.ny = int((cfg.ancho - 2 * cfg.margen) // res)
        self.occ = xp.zeros((self.ny, self.nx), xp.float32)
        self.xmax = 0

    def ubicar(self, M):
        if M['h'] > self.ny or M['w'] > self.nx:
            return None
        fft = fft_gpu if self.xp is cp else fft_cpu
        libre = fft(self.occ, M['kern'], mode='valid') < 0.5
        cols = self.xp.flatnonzero(libre.any(axis=0))
        if cols.size == 0:
            return None
        ix = int(cols[0])
        iy = int(self.xp.flatnonzero(libre[:, ix])[0])
        return ix, iy

    def marcar(self, M, ix, iy):
        z = M['zona']; h, w = z.shape
        x0, y0 = ix + M['ox'], iy + M['oy']
        xa, ya, xb, yb = max(x0, 0), max(y0, 0), min(x0 + w, self.nx), min(y0 + h, self.ny)
        if xb > xa and yb > ya:
            self.occ[ya:yb, xa:xb] = self.xp.maximum(self.occ[ya:yb, xa:xb],
                                                     z[ya - y0:yb - y0, xa - x0:xb - x0])
        self.xmax = max(self.xmax, ix + M['w'])


def decodificar(cromo, tipo_de, lib: Biblioteca, cfg: ConfigNesting, layout=False):
    """cromo = [(instancia, angulo), ...]. Devuelve fitness (y el layout si se pide)."""
    chapas, out = [], []
    for inst, a in cromo:
        M = lib.m[(tipo_de[inst], a)]
        pos = None
        for s, ch in enumerate(chapas):
            pos = ch.ubicar(M)
            if pos:
                break
        if pos is None:
            ch = Chapa(cfg, lib.res, lib.xp); chapas.append(ch)
            s, pos = len(chapas) - 1, ch.ubicar(M)
            if pos is None:
                raise ValueError(f'La pieza tipo {tipo_de[inst]} a {a} grados no entra en una chapa vacia')
        ch.marcar(M, *pos)
        if layout:
            ix, iy = pos
            dx = cfg.margen + (ix + 0.5) * lib.res - M['px0']
            dy = cfg.margen + (iy + 0.5) * lib.res - M['py0']
            out.append(dict(chapa=s, inst=inst, tipo=tipo_de[inst], ang=a, dx=dx, dy=dy))
    largo_ult = chapas[-1].xmax * lib.res + 2 * cfg.margen
    fit = 1e6 * len(chapas) + largo_ult
    return (fit, out) if layout else fit


# ------------------------------------------------------------------ operadores GA
def cruce_ox(p1, p2):
    n = len(p1)
    if n < 3:
        return list(p1)
    a, b = sorted(random.sample(range(n), 2))
    hijo = [None] * n
    hijo[a:b] = p1[a:b]
    usados = {g[0] for g in p1[a:b]}
    resto = iter(g for g in p2 if g[0] not in usados)
    return [h if h is not None else next(resto) for h in hijo]


def mutar(c, angs_tipo, tipo_de, pm):
    c = list(c); n = len(c)
    if n >= 2:
        if random.random() < pm:
            i, j = random.sample(range(n), 2); c[i], c[j] = c[j], c[i]
        if random.random() < pm:
            i, j = sorted(random.sample(range(n), 2)); c[i:j] = c[i:j][::-1]
        if random.random() < pm:
            i, j = sorted(random.sample(range(n), 2)); blk = c[i:j]; del c[i:j]
            k = random.randrange(len(c) + 1); c[k:k] = blk
    for _ in range(2):
        if random.random() < pm:
            i = random.randrange(n); inst, _a = c[i]
            c[i] = (inst, random.choice(angs_tipo[tipo_de[inst]]))
    if random.random() < pm / 2:            # misma rotacion para todo un tipo
        inst, a = random.choice(c); t = tipo_de[inst]
        if a in angs_tipo[t]:
            c = [(p, a) if tipo_de[p] == t else (p, aa) for p, aa in c]
    return c


def semillas(tipo_de, areas, angs_tipo, n):
    """Poblacion inicial: ordenes por area descendente con rotaciones 'prolijas' + aleatorios."""
    N = len(tipo_de)
    orden = sorted(range(N), key=lambda i: -areas[tipo_de[i]])
    out = []
    tipos = sorted(set(tipo_de))
    orto = lambda t: [a for a in (0, 90, 180, 270) if a in angs_tipo[t]] or angs_tipo[t][:1]
    # 1) mismo angulo ortogonal para todas las piezas -> pilas prolijas
    for base in (0, 90, 180, 270):
        out.append([(i, base if base in angs_tipo[tipo_de[i]] else orto(tipo_de[i])[0]) for i in orden])
    # 2) un angulo ortogonal por TIPO (consistente dentro del tipo) y, a veces,
    #    alternando 180 entre copias del mismo tipo (sirve para medias lunas)
    while len(out) < max(4, n // 2):
        a_t = {t: random.choice(orto(t)) for t in tipos}
        alt = {t: random.random() < .3 and (a_t[t] + 180) % 360 in angs_tipo[t] for t in tipos}
        cnt = {t: 0 for t in tipos}; c = []
        for i in orden:
            t = tipo_de[i]; a = a_t[t]
            if alt[t] and cnt[t] % 2:
                a = (a + 180) % 360
            cnt[t] += 1; c.append((i, a))
        out.append(c)
    # 3) aleatorios para diversidad
    while len(out) < n:
        perm = orden[:] if random.random() < .5 else random.sample(range(N), N)
        out.append([(i, random.choice(angs_tipo[tipo_de[i]])) for i in perm])
    return out[:n]


# ------------------------------------------------------------------ GA
_W = {}   # estado del proceso worker

def _init_worker(polys, angulos, cfg, tipo_de):
    _W['lib'] = Biblioteca(polys, angulos, cfg, cfg.res)
    _W['cfg'], _W['tipo_de'] = cfg, tipo_de

def _fit(c):
    return decodificar(c, _W['tipo_de'], _W['lib'], _W['cfg'])


def optimizar(polys, angulos, cantidades, cfg: ConfigNesting, log=print):
    """Corre el GA. Devuelve dict con el mejor cromosoma, su fitness e historial."""
    random.seed(cfg.semilla); np.random.seed(cfg.semilla)
    tipo_de = [t for t, q in enumerate(cantidades) for _ in range(q)]
    areas = [p.area for p in polys]
    angs_tipo = {t: a for t, a in enumerate(angulos)}
    gpu = cfg.usar_gpu and GPU_DISPONIBLE
    log(f'Instancias: {len(tipo_de)} | tipos: {len(polys)} | backend: {"GPU (CuPy)" if gpu else f"CPU x{cfg.procesos}"}')

    t0 = time.time()
    pool = None
    if gpu or cfg.procesos <= 1:
        _init_worker(polys, angulos, cfg, tipo_de)
        evaluar = lambda pob: [_fit(c) for c in pob]
    else:
        from multiprocessing import get_context
        pool = get_context('fork').Pool(cfg.procesos, _init_worker, (polys, angulos, cfg, tipo_de))
        evaluar = lambda pob: pool.map(_fit, pob)
    try:
        pob = semillas(tipo_de, areas, angs_tipo, cfg.poblacion)
        fits = evaluar(pob)
        hist, mejor, sin_mejora = [], min(fits), 0
        log(f'Generacion 0: {int(mejor // 1e6)} chapas + {mejor % 1e6:.0f} mm   ({time.time() - t0:.0f}s)')
        n_elite = max(2, cfg.poblacion // 10)
        for g in range(1, cfg.generaciones + 1):
            orden = np.argsort(fits)
            elite = [pob[i] for i in orden[:n_elite]]
            hijos = []
            while len(hijos) < cfg.poblacion - n_elite:
                p1 = pob[min(random.sample(range(len(pob)), 3), key=lambda i: fits[i])]
                p2 = pob[min(random.sample(range(len(pob)), 3), key=lambda i: fits[i])]
                h = cruce_ox(p1, p2) if random.random() < .85 else list(p1)
                hijos.append(mutar(h, angs_tipo, tipo_de, pm=.35))
            fh = evaluar(hijos)
            pob = elite + hijos
            fits = [fits[i] for i in orden[:n_elite]] + list(fh)
            b = min(fits); hist.append(b)
            if b < mejor - 1e-6:
                mejor, sin_mejora = b, 0
            else:
                sin_mejora += 1
            # INMIGRANTES: si la poblacion se estanca, el 30% peor se reemplaza por
            # individuos nuevos (semillas al azar + mutaciones fuertes del mejor).
            # Evita la convergencia prematura: el GA vuelve a explorar.
            if sin_mejora and sin_mejora % cfg.estancamiento == 0:
                orden = list(np.argsort(fits))
                peores = orden[-int(len(pob) * .3):]
                mejor_ind = pob[orden[0]]
                nuevos = semillas(tipo_de, areas, angs_tipo, len(peores) + 4)[4:]
                nuevos = [mutar(mutar(mejor_ind, angs_tipo, tipo_de, .9), angs_tipo, tipo_de, .9)
                          if k % 2 else n for k, n in enumerate(nuevos)]
                fn = evaluar(nuevos)
                for i, ind, f in zip(peores, nuevos, fn):
                    pob[i], fits[i] = ind, f
                log(f'  gen {g}: estancado {sin_mejora} generaciones -> {len(peores)} inmigrantes')
            if g % 5 == 0 or g == 1:
                log(f'Generacion {g}: {int(b // 1e6)} chapas + {b % 1e6:.0f} mm   ({time.time() - t0:.0f}s)')
            if time.time() - t0 > cfg.tiempo_max_s:
                log('Corte por tiempo maximo'); break
            if sin_mejora >= cfg.paciencia:
                log(f'Corte: {cfg.paciencia} generaciones sin mejora'); break
    finally:
        if pool:
            pool.close(); pool.join()
    orden = list(np.argsort(fits))
    top, vistos = [], set()
    for i in orden:                                  # los mejores distintos entre si
        clave = tuple(pob[i])
        if clave not in vistos:
            vistos.add(clave); top.append(pob[i])
        if len(top) == cfg.top_refinado:
            break
    return dict(cromo=top[0], top=top, fit=min(fits), hist=hist, tipo_de=tipo_de,
                segundos=time.time() - t0)


def refinar(resultado, polys, angulos, cfg: ConfigNesting, verificar_fn, log=print):
    """Re-decodifica el mejor cromosoma con grillas mas finas; devuelve el mejor layout VERIFICADO."""
    tipo_de = resultado['tipo_de']
    candidatos = []
    for res in (cfg.res,) + tuple(cfg.res_refinado):
        lib = Biblioteca(polys, angulos, cfg, res)
        for k, cromo in enumerate(resultado.get('top', [resultado['cromo']])):
            fit, lay = decodificar(cromo, tipo_de, lib, cfg, layout=True)
            ok, info = verificar_fn(lay, polys, cfg)
            log(f'  individuo {k + 1}, grilla {res} mm: {int(fit // 1e6)} chapas, ultima {fit % 1e6:.0f} mm, '
                f'sep. min {info["dmin"]:.2f} mm, verificado={ok}')
            if ok:
                candidatos.append((fit, res, lay))
    if not candidatos:
        raise RuntimeError('Ningun layout paso la verificacion exacta')
    fit, res, lay = min(candidatos, key=lambda x: x[0])
    return dict(layout=lay, fit=fit, res=res)

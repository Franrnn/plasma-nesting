"""
pipeline.py — Proceso completo: carpeta de DXF -> nesting optimizado -> DXF por chapa + reporte.

    from nesting.pipeline import ejecutar
    from nesting.motor import ConfigNesting
    ejecutar('entrada/', 'salida/', ConfigNesting(largo=3000, ancho=1500, espesor=7.94))
"""
from __future__ import annotations
import csv, json, math, time
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from ezdxf.math import Matrix44

from .io_dxf import leer_piezas, escribir_chapa
from .motor import ConfigNesting, optimizar, refinar, angulos_permitidos
from .verificar import verificar

COLORES = ['#2E6DB4', '#2A8C5A', '#C2571A', '#7A4FB0', '#B23A6E', '#3B8C99', '#8C6D1F', '#5B6770']


def ejecutar(entrada, salida, cfg: ConfigNesting, prefijo='NEST', cantidades: dict | None = None,
             capa_unica: str | None = None, contorno_chapa=False, log=print):
    entrada, salida = Path(entrada), Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # 1) lectura -------------------------------------------------------------
    archivos = sorted(list(entrada.glob('*.dxf')) + list(entrada.glob('*.DXF')))
    if not archivos:
        raise FileNotFoundError(f'No hay archivos DXF en {entrada}')
    piezas = []
    for f in archivos:
        cant = (cantidades or {}).get(f.name)
        for p in leer_piezas(f, cantidad=cant):
            piezas.append(p)
            for a in p.advertencias:
                log(f'  ATENCION {f.name}: {a}')
    log('Piezas leidas:')
    for p in piezas:
        log(f'  {p.nombre:60s} x{p.cantidad:<4d} {p.area / 1e6:7.4f} m2  agujeros={len(p.poligono.interiors)}')

    polys = [p.poligono for p in piezas]
    angulos = [angulos_permitidos(p.nombre, cfg.paso_angulo) for p in piezas]
    cantidades_l = [p.cantidad for p in piezas]

    # control rapido: cada pieza tiene que entrar en una chapa vacia en algun angulo
    from shapely.affinity import rotate
    for p, angs in zip(piezas, angulos):
        if not any(_entra(rotate(p.poligono, a, origin=(0, 0)), cfg) for a in angs):
            raise ValueError(f'La pieza {p.nombre} no entra en la chapa {cfg.largo}x{cfg.ancho} con margen {cfg.margen}')

    # 2) optimizacion ----------------------------------------------------------
    # MULTI-START: el GA es estocastico; varias corridas con semillas distintas
    # y nos quedamos con la mejor (verificada). Cada corrida tiene su propio tiempo maximo.
    from dataclasses import replace
    mejor = None
    for k in range(max(1, cfg.reinicios)):
        cfg_k = replace(cfg, semilla=cfg.semilla + 1000 * k)
        if cfg.reinicios > 1:
            log(f'\n=== Corrida {k + 1}/{cfg.reinicios} (semilla {cfg_k.semilla}) ===')
        res_k = optimizar(polys, angulos, cantidades_l, cfg_k, log=log)
        log('Refinando los mejores individuos y verificando con geometria exacta...')
        fin_k = refinar(res_k, polys, angulos, cfg_k, verificar, log=log)
        log(f'Corrida {k + 1}: {int(fin_k["fit"] // 1e6)} chapas + {fin_k["fit"] % 1e6:.0f} mm')
        if mejor is None or fin_k['fit'] < mejor[1]['fit']:
            mejor = (res_k, fin_k)
    res, fin = mejor
    layout = fin['layout']
    ok, info = verificar(layout, polys, cfg)

    # 3) salida DXF ------------------------------------------------------------
    n_chapas = 1 + max(c['chapa'] for c in layout)
    sello = datetime.now().strftime('%Y%m%d_%H%M')
    archivos_out = []
    for s in range(n_chapas):
        col = [(piezas[c['tipo']], Matrix44.chain(Matrix44.z_rotate(math.radians(c['ang'])),
                                                   Matrix44.translate(c['dx'], c['dy'], 0)))
               for c in layout if c['chapa'] == s]
        f = salida / f'{prefijo}_CHAPA_{s + 1}_de_{n_chapas}.dxf'
        escribir_chapa(f, col, capa_unica=capa_unica,
                       contorno_chapa=(cfg.largo, cfg.ancho) if contorno_chapa else None)
        archivos_out.append(f)

    # 4) reporte ---------------------------------------------------------------
    resumen = _reporte(piezas, layout, info, cfg, res, fin, n_chapas, salida, prefijo)
    resumen.update(dict(fecha=sello, verificado=ok, archivos=[str(a) for a in archivos_out],
                        segundos_totales=round(time.time() - t0, 1)))
    (salida / f'{prefijo}_resumen.json').write_text(json.dumps(resumen, indent=2, ensure_ascii=False))
    log(f'\nListo en {resumen["segundos_totales"]} s -> {n_chapas} chapas | '
        f'ultima usada {resumen["largo_ultima_chapa_mm"]:.0f} mm | retazo {resumen["retazo_mm"]} | verificado={ok}')
    return resumen


def _entra(P, cfg):
    minx, miny, maxx, maxy = P.bounds
    return (maxx - minx) <= cfg.largo - 2 * cfg.margen and (maxy - miny) <= cfg.ancho - 2 * cfg.margen


def _reporte(piezas, layout, info, cfg, res, fin, n_chapas, salida, prefijo):
    area_chapa = cfg.largo * cfg.ancho
    por_chapa = info['por_chapa']
    xmax_ult = max(p.bounds[2] for p in por_chapa[n_chapas - 1]) + cfg.margen
    filas, chapas = [], []
    for s in range(n_chapas):
        a = sum(p.area for p in por_chapa[s])
        chapas.append(dict(chapa=s + 1, piezas=len(por_chapa[s]), aprovechamiento_pct=round(100 * a / area_chapa, 1),
                           kg_piezas=round(a * cfg.espesor * cfg.densidad, 1)))
    for c in layout:
        filas.append(dict(chapa=c['chapa'] + 1, pieza=piezas[c['tipo']].nombre, angulo=c['ang'],
                          dx=round(c['dx'], 2), dy=round(c['dy'], 2)))
    with open(salida / f'{prefijo}_ubicaciones.csv', 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0])); w.writeheader(); w.writerows(filas)

    # imagen: una fila por chapa + curva de convergencia
    alto = [1] * n_chapas + [0.45]
    fig = plt.figure(figsize=(15, 7.6 * n_chapas + 3.5))
    gs = fig.add_gridspec(n_chapas + 1, 1, height_ratios=alto)
    color_tipo = {i: COLORES[i % len(COLORES)] for i in range(len(piezas))}
    for s in range(n_chapas):
        ax = fig.add_subplot(gs[s])
        ax.add_patch(plt.Rectangle((0, 0), cfg.largo, cfg.ancho, fill=False, lw=1.4, ec='#222'))
        ax.add_patch(plt.Rectangle((cfg.margen, cfg.margen), cfg.largo - 2 * cfg.margen, cfg.ancho - 2 * cfg.margen,
                                   fill=False, lw=.5, ec='#999', ls='--'))
        for c in layout:
            if c['chapa'] != s:
                continue
            from .verificar import colocar_poligono
            P = colocar_poligono(piezas[c['tipo']].poligono, c['ang'], c['dx'], c['dy'])
            col = color_tipo[c['tipo']]
            ax.fill(*P.exterior.xy, fc=col, alpha=.28, ec=col, lw=.7)
            for it in P.interiors:
                ax.fill(*it.xy, fc='white', ec=col, lw=.5)
        ch = chapas[s]
        ax.set_title(f'Chapa {s + 1}/{n_chapas} — {cfg.largo:.0f} × {cfg.ancho:.0f} × {cfg.espesor:g} mm   |   '
                     f'{ch["piezas"]} piezas   |   aprovechamiento {ch["aprovechamiento_pct"]}%', loc='left', fontsize=12)
        if s == n_chapas - 1:
            ax.axvline(xmax_ult, color='#C0392B', lw=1, ls='--')
            ax.text(xmax_ult + 15, cfg.ancho - 40, f'retazo\n{cfg.largo - xmax_ult:.0f} × {cfg.ancho:.0f}',
                    color='#C0392B', fontsize=10, va='top')
        ax.set_aspect('equal'); ax.set_xlim(-30, cfg.largo + 30); ax.set_ylim(-30, cfg.ancho + 30)
        ax.tick_params(labelsize=8); ax.grid(alpha=.15)
    ax = fig.add_subplot(gs[n_chapas])
    h = [x % 1e6 for x in res['hist']]
    if h:
        ax.plot(range(1, len(h) + 1), h, color='#2E6DB4', lw=1.6)
    ax.set_xlabel('generación'); ax.set_ylabel('largo última chapa [mm]')
    ax.set_title('Convergencia del algoritmo genético', loc='left', fontsize=11); ax.grid(alpha=.2)
    # leyenda de tipos
    for t, p in enumerate(piezas):
        fig.text(0.01 + 0.33 * (t % 3), 0.003 + 0.012 * (t // 3), f'■ {p.nombre[:45]} x{p.cantidad}',
                 color=color_tipo[t], fontsize=8)
    plt.tight_layout(rect=(0, 0.02 + 0.012 * (len(piezas) // 3), 1, 1))
    plt.savefig(salida / f'{prefijo}_nesting.png', dpi=100)
    plt.close(fig)

    return dict(chapas_usadas=n_chapas, largo_ultima_chapa_mm=round(xmax_ult, 1),
                retazo_mm=f'{cfg.largo - xmax_ult:.0f} x {cfg.ancho:.0f}', detalle_chapas=chapas,
                separacion_minima_mm=round(info['dmin'], 2), grilla_final_mm=fin['res'],
                generaciones=len(res['hist']), segundos_ga=round(res['segundos'], 1),
                config={k: v for k, v in cfg.__dict__.items() if k != 'extras'})

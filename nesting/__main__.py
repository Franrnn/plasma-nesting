"""
Uso por linea de comandos (en tu PC o en Colab):

    python -m nesting entrada/ salida/ --largo 3000 --ancho 1500 --espesor 7.94 \
        --gap 10 --margen 10 --minutos 10 --prefijo T3094_5-16

Las cantidades se leen del nombre de cada DXF (..._CANT_40.dxf).
"""
import argparse
from .motor import ConfigNesting, GPU_DISPONIBLE
from .pipeline import ejecutar


def main():
    ap = argparse.ArgumentParser(prog='nesting', description='Nesting de piezas DXF con algoritmo genetico')
    ap.add_argument('entrada'); ap.add_argument('salida')
    ap.add_argument('--largo', type=float, default=3000); ap.add_argument('--ancho', type=float, default=1500)
    ap.add_argument('--espesor', type=float, default=3.2)
    ap.add_argument('--gap', type=float, default=10); ap.add_argument('--margen', type=float, default=10)
    ap.add_argument('--res', type=float, default=5.0, help='celda de busqueda en mm (5 = rapido)')
    ap.add_argument('--paso-angulo', type=int, default=15)
    ap.add_argument('--poblacion', type=int, default=32); ap.add_argument('--generaciones', type=int, default=150)
    ap.add_argument('--minutos', type=float, default=15, help='tiempo maximo del GA')
    ap.add_argument('--procesos', type=int, default=2)
    ap.add_argument('--reinicios', type=int, default=1, help='corridas con semillas distintas; se queda con la mejor')
    ap.add_argument('--sin-pieza-en-pieza', action='store_true')
    ap.add_argument('--cpu', action='store_true', help='no usar GPU aunque haya')
    ap.add_argument('--capa-unica', default=None, help='ej. CORTE: todo a una capa')
    ap.add_argument('--contorno-chapa', action='store_true', help='dibuja la chapa en capa CHAPA')
    ap.add_argument('--prefijo', default='NEST'); ap.add_argument('--semilla', type=int, default=7)
    a = ap.parse_args()
    cfg = ConfigNesting(largo=a.largo, ancho=a.ancho, margen=a.margen, gap=a.gap, res=a.res,
                        paso_angulo=a.paso_angulo, pieza_en_pieza=not a.sin_pieza_en_pieza,
                        poblacion=a.poblacion, generaciones=a.generaciones, tiempo_max_s=a.minutos * 60,
                        usar_gpu=not a.cpu, procesos=a.procesos, espesor=a.espesor, semilla=a.semilla,
                        reinicios=a.reinicios)
    print(f'GPU disponible: {GPU_DISPONIBLE}')
    ejecutar(a.entrada, a.salida, cfg, prefijo=a.prefijo, capa_unica=a.capa_unica, contorno_chapa=a.contorno_chapa)


if __name__ == '__main__':
    main()

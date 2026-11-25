# plasma-nesting — contexto para Claude Code

Proyecto de Franco (ingeniero industrial, oficina técnica de una metalúrgica en Rosario; estudia la TUIA en UNR).
Objetivo: automatizar el flujo **plano → piezas desarrolladas → nesting → DXF para el pantógrafo de plasma (Numerik)**.
Plan completo (fases, arquitectura, costos, riesgos): https://claude.ai/artifact/YTU9LuCNobh6EYALN1FfgZ

## Estado (nov 2026)
- **Fase 0 terminada**: nesting con algoritmo genético funcionando (este repo, v0.1).
- **Siguiente: Fase 1 — nesting en producción** (ver "Próximos pasos").
- Fase 2 generadores paramétricos de desarrollos · Fase 3 lectura de planos (PDF/foto/DWG) con modelo de visión · Fase 4 app Streamlit.

## Arquitectura del repo
```
nesting/io_dxf.py     lee piezas de DXF (LINE/ARC encadenados, LWPOLYLINE, POLYLINE, CIRCLE, ELLIPSE, SPLINE, bloques);
                      agrupa contorno + agujeros; cantidad desde el nombre (CANT_n / xN); escribe la chapa con las
                      ENTIDADES ORIGINALES transformadas (solo rotación + traslación, nunca espejo)
nesting/motor.py      raster + decodificador Bottom-Left por FFT (CPU scipy / GPU cupy) + GA
                      (torneo, OX, mutaciones, elitismo, inmigrantes al estancarse, refinado top-5 a 3 y 2 mm)
nesting/verificar.py  verificación EXACTA con shapely + STRtree (margen y separación)
nesting/pipeline.py   carpeta de DXF → DXF por chapa + PNG + resumen.json + ubicaciones.csv; multi-start (reinicios)
nesting/__main__.py   CLI: python -m nesting entrada/ salida/ --espesor 7.94 --minutos 10 --reinicios 3
notebooks/Nesting_Colab.ipynb   uso diario en Colab (formulario, Drive, clona este repo)
```
Garantía de separación del raster: pieza engordada r = res·√2/2, ocupado engordado gap + r → distancia real ≥ gap.

## Convenciones (no romper)
- Separación entre piezas y margen al borde de chapa: **10 mm** por defecto. Chapa estándar 1500 × 3000.
- Salida DXF **R2000**, mismas capas y tipos de entidad que la entrada; origen (0,0) = esquina inferior izquierda.
  **No** se compensa kerf (lo hace el CAM del Numerik). Opción `capa_unica` para mandar todo a una capa.
- Nombre de archivo de pieza: `T-3094_-_BRIDA_D1352_en_4P_CANT_40.dxf`. Etiquetas de rotación: `NOROT`, `ROT90`, `ROT180`.
- Un nesting = un material y un espesor.
- **Nunca** commitear DXF, planos ni datos de clientes (`entrada/`, `salida/` en .gitignore). Los tests usan piezas sintéticas.
- Código y comentarios en español; explicar el "por qué" (Franco lo usa también para la facultad).
- Antes de cada commit: `python -m pytest -q` (3 tests deben pasar).

## Resultados de referencia (trabajo T-3094, bridas 5/16", 54 piezas)
| Versión | Chapas | Chapa 2 usada hasta | Retazo libre |
|---|---|---|---|
| Manual | 2 | 2177 mm | 822 × 1500 |
| Repo v0.1, 1 corrida CPU (8 min) | 2 | 1891 mm | 1109 × 1500 |
| GA exploratorio, mejor corrida | 2 | 1667 mm | 1333 × 1500 |
El GA es estocástico: usar `reinicios` ≥ 3. La grilla de 5 mm y la de 2-3 mm dan resultados distintos para el mismo
cromosoma (el decodificador es sensible), por eso se refinan los 5 mejores.

## Conocimiento de fabricación ya validado en este proyecto
- Plegado: BD = 2·OSSB − BA, OSSB = tan(α/2)(Ri+t), BA = (π/180)·α·(Ri+K·t). K ≈ 0.42 (SAE 1010, aire, Ri = t).
  α es el ángulo que gira la chapa (cara a 100° → α = 80°). Regla rápida 90°: BD ≈ 1.8·t.
- Bridas segmentadas: juntas siempre a mitad de camino entre agujeros; nº de segmentos divisor del nº de agujeros.
- Plasma: agujeros < 1.5 × espesor salen cónicos (alertar).

## Próximos pasos (Fase 1)
1. Probar el camino GPU en Colab (no se pudo probar sin GPU).
2. Compactación final exacta: correr cada pieza a la izquierda con shapely hasta tocar el gap.
3. Separar automáticamente por espesor (subcarpetas o etiqueta en el nombre).
4. Inventario de retazos (CSV) para usar retazos antes que chapas nuevas.
5. Control de calidad en exportación: contornos cerrados, duplicados, agujeros chicos, ligamentos.
6. Juntar 5 trabajos previos (plano + DXF finales) para arrancar los generadores paramétricos (Fase 2).

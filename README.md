# plasma-nesting

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Franrnn/plasma-nesting/blob/main/notebooks/Nesting_Colab.ipynb)

Nesting automático de piezas DXF para pantógrafo de plasma, con algoritmo genético.
Corre en **Google Colab (gratis, con GPU)** o en cualquier PC con Python.

```
entrada/*.dxf  ──►  lector DXF  ──►  algoritmo genético + bottom-left  ──►  verificación exacta  ──►  salida/
 (CANT_n en el      (polylines,       (orden + rotación de                 (shapely: separación      CHAPA_1.dxf ...
  nombre)            líneas, arcos,    cada pieza, grilla FFT,              y margen reales)          nesting.png
                     círculos,         CPU o GPU)                                                     resumen.json
                     splines, bloques)                                                                ubicaciones.csv
```

---

## 1. Uso diario (Colab)

1. En tu Google Drive: `Mi unidad/PlasmaCAD/trabajos/<TRABAJO>/entrada/` → subí los DXF.
   - **Cantidad**: en el nombre del archivo → `BRIDA_D1352_4P_CANT_40.dxf`
   - **Rotación** (opcional, en el nombre): `NOROT` = no girar · `ROT90` = solo 0/90/180/270 · `ROT180` = solo 0/180
   - Un DXF puede tener una o varias piezas. Capas ignoradas: `COTAS`, `TEXTO`, `DEFPOINTS`, `EJES`, `REFERENCIA`.
   - Todos los DXF de un trabajo = **mismo material y espesor** (un nesting por espesor).
2. Abrí `notebooks/Nesting_Colab.ipynb` en Colab → **Entorno → Cambiar tipo de entorno → T4 GPU**.
3. Completá el formulario (trabajo, chapa, espesor, separación, minutos) → **Entorno → Ejecutar todas**.
4. Resultado en `.../<TRABAJO>/salida/`:
   | Archivo | Para qué |
   |---|---|
   | `<TRABAJO>_CHAPA_n_de_N.dxf` | Va al Numerik. Mismas capas y entidades que tus DXF (arcos y círculos exactos). |
   | `<TRABAJO>_nesting.png` | Vista de las chapas + curva de convergencia. |
   | `<TRABAJO>_resumen.json` | Trazabilidad: parámetros, aprovechamiento, kg, retazo, verificación. |
   | `<TRABAJO>_ubicaciones.csv` | Pieza, chapa, ángulo y posición de cada instancia. |

### Parámetros que importan
| Parámetro | Default | Comentario |
|---|---|---|
| `PASO_ANGULO` | 15° | 90 = rápido y prolijo; 5 = máximo aprovechamiento, más lento |
| `PIEZA_EN_PIEZA` | Sí | Usa los huecos de las piezas (bridas enteras, tapas con agujero central) |
| `MINUTOS_MAX` | 10 | Por corrida. El GA corta solo si no mejora en 80 generaciones; cada 15 sin mejora mete inmigrantes |
| `REINICIOS` | 3 | Corridas con semillas distintas: el GA es estocástico, se queda con la mejor verificada |
| `res` (CLI) | 5 mm | Grilla de búsqueda. El mejor resultado se re-decodifica a 3 y 2 mm al final |

---

## 2. Deploy (una sola vez)

### 2.1 Repositorio en GitHub
```bash
cd plasma-nesting
git init -b main
git add .
git commit -m "Motor de nesting GA v0.1"
git remote add origin https://github.com/Franrnn/plasma-nesting.git
git push -u origin main
git tag v0.1 && git push --tags        # cada versión que uses en producción, con tag
```
- **Público** (recomendado como portfolio): Colab lo clona sin token y el botón *Open in Colab* de arriba funciona directo.
- **Privado**: Colab lo clona con un token (2.2).
- **Nunca** se suben DXF ni planos de clientes: `entrada/` y `salida/` están en `.gitignore`. Los tests usan piezas sintéticas.

### 2.2 Token de GitHub para Colab (solo si el repo es privado)
1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
   Nombre `colab`, vencimiento 1 año, *Repository access* = solo `plasma-nesting`, permiso **Contents: Read-only**.
2. En Colab: icono de **llave (Secretos)** → `+ Agregar` → nombre `GITHUB_TOKEN`, valor = el token → activar *Acceso del notebook*.
   El token nunca queda escrito en el notebook.

### 2.3 Notebook
- Repo público: botón *Open in Colab* de arriba, o en Colab **Archivo → Abrir notebook → GitHub** → `Franrnn/plasma-nesting`.
  Para guardar tus parámetros: **Archivo → Guardar una copia en Drive**.
- Repo privado: subí `notebooks/Nesting_Colab.ipynb` a `Mi unidad/PlasmaCAD/` y abrilo con Colab.
- Cada vez que se ejecuta, la celda 1 hace `git pull`: **siempre corre la última versión del repo**.
  Para congelar una versión, poné `RAMA = 'v0.1'` en la celda 1.

### 2.4 Correr en tu PC (sin Colab)
```bash
pip install -r requirements.txt
python -m nesting entrada/ salida/ --espesor 7.94 --minutos 15 --procesos 5 --prefijo T3094
```
`--procesos` = núcleos de la CPU menos uno (deja uno libre para AutoCAD).

---

## 3. Colab gratis: lo que hay que saber

| Límite (plan gratuito) | Efecto | Qué hacer |
|---|---|---|
| Sesión máx. ~12 h, se corta tras ~90 min sin interacción | Un nesting de 15-30 min entra holgado | Dejá la pestaña abierta mientras corre |
| GPU T4 **no garantizada** (cupo diario variable) | Si no hay GPU, corre en CPU (2 núcleos), ~3-5× más lento | El motor detecta solo; no hay que tocar nada |
| El disco de la sesión se borra al cerrar | — | Entrada y salida viven en **Drive**, no se pierde nada |
| Sin API/servidor permanente | No sirve para que otros "llamen" al nesting | Para eso: Colab Pro (USD ~10/mes) o un VPS (ver plan, fase 5) |

Para un solo usuario es suficiente y estable.

---

## 4. Cómo funciona (para la facultad)

**Problema:** ubicar N piezas irregulares en chapas rectangulares minimizando chapas y desperdicio — es NP-hard.

**Descomposición:**
- **Decodificador (heurística determinista, Bottom-Left Fill):** dada una secuencia de piezas con su ángulo,
  pone cada una en la posición libre más a la izquierda y luego más abajo. Si no entra, abre otra chapa.
- **Algoritmo genético (búsqueda):** busca la mejor secuencia + ángulos para el decodificador.
  - Cromosoma: permutación de instancias + gen de ángulo por instancia.
  - Fitness: `1e6 × chapas + largo usado en la última chapa` → menos chapas, y después retazo más grande.
  - Selección por torneo (k=3), cruce OX (*Order Crossover*), mutaciones (swap, inversión, mover bloque, rotar, rotar tipo completo), elitismo 10 %.
  - Población inicial "sembrada" con soluciones prolijas (piezas ordenadas por área, mismo ángulo por tipo) + aleatorias.

**Detección de colisiones por FFT:** la chapa es una grilla (5 mm). Las posiciones libres para una pieza se obtienen todas
juntas con una correlación `ocupación ⋆ máscara` calculada por FFT (O(n log n)). En GPU se usa CuPy.

**Garantía de separación:** la pieza se rasteriza engordada `r = res·√2/2` y lo colocado engordado `gap + r`.
Si no comparten celdas, la distancia real es ≥ `gap`. Además, el resultado final se verifica con geometría exacta (shapely).

---

## 5. Control de calidad antes de cortar
- [ ] `resumen.json` → `"verificado": true` y `separacion_minima_mm ≥ gap`
- [ ] Abrir cada `CHAPA_n.dxf` en AutoCAD: cantidad de piezas = esperado (ver `ubicaciones.csv`)
- [ ] Capas correctas para el Numerik (o usar `CAPA_UNICA = 'CORTE'`)
- [ ] Origen (0,0) = esquina inferior izquierda de la chapa
- [ ] El DXF de salida **no** compensa kerf: lo hace el CAM del Numerik

## 6. Estructura
```
nesting/
  io_dxf.py      lectura de piezas (cualquier entidad) y escritura del DXF de cada chapa
  motor.py       raster, decodificador bottom-left, algoritmo genético, backend CPU/GPU
  verificar.py   verificación exacta (shapely + STRtree)
  pipeline.py    proceso completo + reportes
  __main__.py    línea de comandos
notebooks/Nesting_Colab.ipynb
tests/           python -m pytest -q
```

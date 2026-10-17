# tomd

Conversor local de **PDF**, **EPUB** y **artículos de Wikipedia** a Markdown limpio y determinista,
pensado para alimentar una biblioteca que consume un sistema de IA local (búsqueda,
tutoría, redacción). La misma entrada con la misma versión produce siempre los mismos
bytes, y toda pérdida de información queda registrada como un diagnóstico.

> **Estado:** pre-1.0. Ver [Limitaciones](#limitaciones).

## Instalar la aplicación (Windows, sin conocimientos técnicos)

1. Descarga `tomd-setup-<versión>.exe` y ábrelo con doble clic. No necesita permisos de
   administrador ni tener Python: lleva todo dentro, incluido el reconocimiento de texto
   (OCR) para PDF escaneados.
2. Si Windows muestra «Windows protegió su PC», pulsa **Más información** y luego
   **Ejecutar de todas formas**. El aviso sale porque el instalador no está firmado
   digitalmente; el programa no se conecta a internet salvo para descargar artículos de
   Wikipedia.
3. Al terminar, abre **tomd** desde el menú Inicio (o desde el escritorio, si marcaste el
   acceso directo).

Para usarla:

- **Añadir documentos:** suelta sobre la ventana archivos PDF o EPUB, o una carpeta entera;
  o usa **Añadir documentos ▾** (archivos, una carpeta o un artículo de Wikipedia). Si
  cierras la aplicación a medias, al añadir lo mismo de nuevo sigue donde se quedó.
- **La biblioteca** muestra cada documento con su estado: **Listo**, **Por revisar** (tomd
  encontró algo dudoso y dice qué), **Revisado** o **No convertido** (con el motivo). Se
  puede buscar y filtrar por estado.
- **Revisar:** abre un documento «Por revisar» (o pulsa **Revisar pendientes**). A la
  izquierda ves el original y a la derecha el resultado, como texto o como Markdown.
  Abajo, la lista «Qué revisar»: al hacer clic en un punto se muestra su sitio en los dos
  lados; marca cada casilla cuando lo hayas comprobado y, al acabar, **Marcar como
  revisado** te lleva al siguiente. Se puede marcar aunque queden casillas sin marcar.
- **Opciones:** cuándo leer páginas escaneadas (OCR) y la carpeta de resultados.
- **Los resultados** quedan en `Documentos\tomd` (hay un acceso en el menú Inicio:
  «Carpeta de resultados»). Cada documento es un archivo `.md`, más una carpeta con sus
  imágenes si las tiene.

Se desinstala desde **Configuración → Aplicaciones**. Los documentos convertidos en
`Documentos\tomd` no se borran al desinstalar.

## Instalación para desarrollo

Requiere Python 3.12 y [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # dependencias de ejecución y de desarrollo
uv run tomd --version
```

Todo se ejecuta en local. La única conexión de red es la API de Wikimedia, y solo al
convertir un artículo de Wikipedia por URL o título.

### OCR (opcional, para PDF escaneados)

Las páginas escaneadas se leen con [Tesseract](https://github.com/tesseract-ocr/tesseract)
(ADR-017). Sin él, tomd convierte el resto del documento y avisa con `pdf.ocr_unavailable`.

1. Instala Tesseract 5:
   - Windows: `winget install UB-Mannheim.TesseractOCR`
   - Debian/Ubuntu: `sudo apt install tesseract-ocr`
   - macOS: `brew install tesseract`
2. Descarga `spa.traineddata`, `eng.traineddata` y `osd.traineddata` de
   [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) en una de estas carpetas:
   - `%LOCALAPPDATA%\tomd\tessdata` (Windows) o `~/.local/share/tomd/tessdata` (Linux, macOS);
   - o la carpeta que indique la variable `TOMD_TESSDATA`.

   Si no hay ninguna, se usan los datos de la instalación de Tesseract.

| Variable | Para qué |
|---|---|
| `TOMD_TESSERACT` | Ruta al ejecutable, si no está en el `PATH` |
| `TOMD_TESSDATA` | Carpeta con los `.traineddata` |
| `TOMD_OCR_LANG` | Idiomas de Tesseract (por defecto `spa+eng`) |

### Backend docling (opcional)

`uv sync --extra docling` instala el backend de maquetación por modelos (`--backend
docling`). Es más lento y usa unos 2 GB de memoria; ver
`docs/benchmarks/2026-10-16-pdf-backends.md`.

## Uso

```bash
# PDF digital o escaneado
uv run tomd convert articulo.pdf -o out/

# EPUB 2 o 3 (sin DRM): capítulos en el orden del libro, notas como notas al pie
uv run tomd convert libro.epub -o out/

# Artículo de Wikipedia: URL, o idioma:Título, opcionalmente fijando la revisión
uv run tomd convert https://es.wikipedia.org/wiki/Teorema_de_Pitágoras -o out/
uv run tomd convert "en:Maxwell's equations" --revision 1378299675 -o out/

# OCR: auto (por defecto) solo en páginas sin texto utilizable; always fuerza OCR en
# todas (texto digital ilegible); never lo desactiva
uv run tomd convert escaneo.pdf --ocr always -o out/

# Lote reanudable: carpetas, archivos o listas .txt de rutas (ver «Lotes»)
uv run tomd batch biblioteca/ -o out/

# Depurar la maquetación de una página: SVG con líneas, bloques, orden o clasificación
uv run tomd debug-pdf articulo.pdf --page 3 --stage order
```

### Lotes

```bash
uv run tomd batch biblioteca/ otra-carpeta/ lista.txt -o out/ --workers 1 --timeout 600
```

- **Qué convierte:** carpetas (recursivo, `.pdf` y `.epub`), archivos sueltos y listas
  `.txt` con una ruta por línea.
- **Manifiesto:** el estado de cada documento queda en `out/manifest.sqlite`, con clave
  (hash del archivo, versión de tomd).
  - Repetir la orden solo convierte lo nuevo o lo pendiente.
  - Tras una interrupción (Ctrl+C, un corte) se reanuda donde se quedó.
  - Una copia idéntica en otra ruta no se convierte dos veces.
- **Aislamiento:** cada documento se convierte en un worker con límite de memoria
  (`--memory-mb`, 4096 por defecto) y tiempo máximo (`--timeout`). Un archivo dañado, que
  se cuelga o que agota la memoria queda registrado y el lote sigue. `--retry-failed`
  reintenta los fallidos y `--force` reconvierte todo.
- **Informe:** `out/report.md` resume estados, diagnósticos más frecuentes, documentos más
  lentos y fallos.
- **Workers:** `--workers 1` por defecto, porque la máquina se comparte con el modelo
  local; súbelo cuando haya memoria libre (unos 4 GB por worker en el peor caso).
- **Salida de la orden:** 0 si todo se convirtió, 1 si hubo fallos (ver el informe),
  130 si se interrumpió.

### Interfaz de escritorio

```bash
uv run tomd ui                    # biblioteca y revisión
uv run tomd ui articulo.pdf       # convierte ese documento y abre su revisión
```

Una ventana local (Tkinter con un tema `ttk` propio, sin navegador ni servidor) con dos
pantallas:

- **Biblioteca:** todos los documentos de la carpeta de resultados con su estado
  (Convirtiendo, Por revisar, Revisado, Listo, No convertido), qué revisar y la fecha;
  búsqueda y filtros. La primera vez muestra una zona para soltar archivos.
- **Revisión:** el original (página del PDF con zoom, Ctrl+rueda, y Re Pág/Av Pág; para EPUB
  y Wikipedia, sus datos) junto al resultado (texto formateado o Markdown). Debajo, los
  avisos y errores como lista de comprobación: cada punto lleva a su página y resalta el
  bloque; al hacer clic en una línea del Markdown se muestra su bloque en el original.
  «Volver a convertir con OCR» repite la conversión leyendo las páginas como imagen.

Toda conversión pasa por el lote (`tomd batch`) en un proceso aparte: la biblioteca es el
manifiesto (`manifest.sqlite`), que guarda también los avisos, la posición de cada página
en el Markdown, las casillas marcadas y la marca de revisado. Así se puede revisar un
documento convertido hace días sin convertirlo otra vez. Los artículos de Wikipedia se
descargan primero a `.wikipedia/` dentro de la carpeta de resultados. Arrastrar y soltar
necesita `tkinterdnd2`; sin él, la ventana funciona igual sin esa opción. Las opciones se
guardan en `%APPDATA%\tomd\settings.json` (`~/.config/tomd/` fuera de Windows).

`tomd convert` imprime la ruta del Markdown y, por la salida de error, un resumen de
diagnósticos con la acción recomendada cuando hay algo que hacer:

```text
out/adapler-speeding-up-inference-by-adaptive-length-reduction.md
  warning pdf.reading_order_uncertain: 1
          -> Check the paragraph order of that page against the source.
```

### Salida

```
out/
  <slug>.md            # Markdown (docs/MARKDOWN_SPEC.md)
  <slug>.assets/       # imágenes, nombradas por su hash: 3f2a9c01b7de.png
```

El nombre sale del título. El Markdown empieza con un front matter YAML de claves fijas:

```yaml
---
title: "Maxwell's equations"
source_type: wikipedia
source: "https://en.wikipedia.org/wiki/Maxwell%27s_equations"
source_sha256: "dd372a84…"
converter_version: "0.0.0"
backend: parsoid
language: en
license: "CC BY-SA 4.0"
revision_id: 1378299675
diagnostics_count: 2
---
```

La fecha de conversión no va en el front matter, para que la salida sea reproducible.

### Diagnósticos

Cada pérdida o inferencia dudosa (tabla emitida como HTML, imagen omitida, orden de
lectura dudoso, página leída por OCR…) genera un diagnóstico con severidad `error`,
`warning` o `info`. El catálogo completo, con qué significa cada código y qué hacer, está
en [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md).

### Códigos de salida

| Código | Significado |
|---|---|
| 0 | Convertido (puede haber diagnósticos) |
| 1 | Otro error de tomd |
| 2 | Argumentos de línea de órdenes incorrectos |
| 3 | Entrada no soportada (tipo de archivo, HTML genérico) |
| 4 | Entrada ilegible: dañada, truncada o no es lo que dice su extensión |
| 5 | Protegida con contraseña o DRM (no se elimina, ADR-009) |
| 6 | Artículo o revisión de Wikipedia inexistente |
| 7 | Error de red con Wikimedia |

## Limitaciones

- **EPUB:** los de maquetación fija (cómics, álbumes) se convierten con un aviso
  (`epub.fixed_layout`); su sentido puede depender del diseño de página. Los enlaces entre
  partes del libro conservan solo el texto. Los EPUB con DRM se rechazan (ADR-009).
- **Fórmulas en PDF:** salen como texto plano; solo Wikipedia aporta LaTeX.
- **OCR:** el texto reconocido puede tener errores. Cada página leída por OCR lo indica
  con su confianza media (`pdf.ocr_applied`), y las de baja calidad se marcan con
  `pdf.ocr_low_confidence`. La negrita se infiere del grosor del trazo y la cursiva no se
  recupera. Una página escaneada tarda unos 3–4 s.
- **Memoria:** el presupuesto es de unos 4 GB por proceso. Las páginas enormes se leen a
  menor resolución, y las imágenes de más de 40 MP se omiten con `image.dropped`.

## Generar el instalador de Windows

```bash
winget install UB-Mannheim.TesseractOCR   # OCR que se incluye en la aplicación
winget install JRSoftware.InnoSetup       # compilador del instalador
uv run python scripts/build_windows.py    # dist/tomd/ y dist/installer/tomd-setup-<v>.exe
```

El script, en orden:

1. Empaqueta con PyInstaller (`packaging/tomd.spec`) dos ejecutables que comparten
   carpeta: `tomd.exe` (la ventana) y `tomd-cli.exe` (la línea de órdenes).
2. Copia Tesseract: el ejecutable, solo las DLL que de verdad carga (leídas de sus tablas
   de importación) y los datos de español, inglés y orientación.
3. Genera `THIRD_PARTY_LICENSES.txt`.
4. Prueba el ejecutable empaquetado, sin rutas de desarrollo: PDF, escaneo con OCR, EPUB
   y lote.
5. Compila el instalador con Inno Setup (`packaging/tomd.iss`): en español, por usuario,
   con accesos en el menú Inicio y desinstalador.

La versión del instalador es la de `pyproject.toml`. El instalador no está firmado: para
evitar el aviso de SmartScreen hace falta un certificado de firma de código.

## Desarrollo

```bash
uv run pytest -m "not slow"                 # tests rápidos
uv run pytest                               # incluye OCR real (si hay Tesseract)
bash scripts/check.sh                       # ruff, mypy y tests rápidos
uv run python scripts/eval_corpus.py        # métricas sobre el corpus dorado
uv run python scripts/fuzz_inputs.py --iterations 200 --seed 1   # entradas malformadas
```

| Archivo | Para qué |
|---|---|
| `CLAUDE.md` | Contexto e invariantes que Claude Code lee en cada sesión |
| `PLAN.md` | Fases, tareas y criterios de terminado |
| `DECISIONS.md` | Registro de decisiones de arquitectura (ADR) |
| `docs/ARCHITECTURE.md` | Pipeline, IR, lectores, lotes |
| `docs/MARKDOWN_SPEC.md` | Contrato exacto del Markdown de salida (reglas M01–M22) |
| `docs/DIAGNOSTICS.md` | Catálogo de diagnósticos (generado) |
| `docs/TESTING.md` | Corpus dorado, métricas, fuzzing, corpus privado |
| `.claude/skills/` | Procedimientos reutilizables para Claude Code |
| `.claude/settings.json` | Permisos: qué puede hacer Claude Code sin preguntar |

### Con Claude Code

```bash
claude
> Lee CLAUDE.md y PLAN.md y continúa con la primera fase que tenga tareas sin marcar.
```

Trabaja fase por fase. Al cerrar cada una, pide a Claude Code que marque las tareas en
`PLAN.md` y registre en `DECISIONS.md` cualquier decisión tomada por el camino.

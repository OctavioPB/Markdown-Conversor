# tomd

Conversor local de **PDF** y **artículos de Wikipedia** a Markdown limpio y determinista,
pensado para alimentar una biblioteca que consume un sistema de IA local (búsqueda,
tutoría, redacción). La misma entrada con la misma versión produce siempre los mismos
bytes, y toda pérdida de información queda registrada como un diagnóstico.

> **Estado:** pre-1.0. EPUB aún no está implementado (PLAN.md, fase 2), ni la conversión
> por lotes (fase 7). Ver [Limitaciones](#limitaciones).

## Instalación

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

# Artículo de Wikipedia: URL, o idioma:Título, opcionalmente fijando la revisión
uv run tomd convert https://es.wikipedia.org/wiki/Teorema_de_Pitágoras -o out/
uv run tomd convert "en:Maxwell's equations" --revision 1378299675 -o out/

# OCR: auto (por defecto) solo en páginas sin texto utilizable; always fuerza OCR en
# todas (texto digital ilegible); never lo desactiva
uv run tomd convert escaneo.pdf --ocr always -o out/

# Depurar la maquetación de una página: SVG con líneas, bloques, orden o clasificación
uv run tomd debug-pdf articulo.pdf --page 3 --stage order
```

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
| 3 | Entrada no soportada (tipo de archivo, HTML genérico, EPUB) |
| 4 | Entrada ilegible: dañada, truncada o no es lo que dice su extensión |
| 5 | Protegida con contraseña o DRM (no se elimina, ADR-009) |
| 6 | Artículo o revisión de Wikipedia inexistente |
| 7 | Error de red con Wikimedia |

## Limitaciones

- **EPUB:** aún no está implementado (PLAN.md, fase 2).
- **Lotes:** la conversión por lotes reanudable (`tomd batch`, fase 7) no existe todavía.
- **Fórmulas en PDF:** salen como texto plano; solo Wikipedia aporta LaTeX.
- **OCR:** el texto reconocido puede tener errores. Cada página leída por OCR lo indica
  con su confianza media (`pdf.ocr_applied`), y las de baja calidad se marcan con
  `pdf.ocr_low_confidence`. La negrita se infiere del grosor del trazo y la cursiva no se
  recupera. Una página escaneada tarda unos 3–4 s.
- **Memoria:** el presupuesto es de unos 4 GB por proceso. Las páginas enormes se leen a
  menor resolución, y las imágenes de más de 40 MP se omiten con `image.dropped`.

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

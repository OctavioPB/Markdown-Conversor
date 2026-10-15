# Arquitectura de tomd

## 1. Idea central

```
 fuente ──► Reader ──► Document (IR) ──► Transforms ──► MarkdownWriter ──► .md + assets/
                │                              │                │
                └──────── Diagnostics ─────────┴────────────────┴──► manifiesto / informe
```

- **Reader**: conoce el formato de origen y nada de Markdown.
- **IR**: árbol tipado e inmutable que describe *estructura*, no presentación.
- **Transforms**: pasadas IR→IR puras e independientes de la fuente.
- **Writer**: conoce Markdown y nada de las fuentes.

Por qué importa: el coste de añadir una fuente nueva es un Reader, y el coste de cambiar el
dialecto de salida es un Writer. Si esa separación se rompe (un reader que mete `**` en un
texto), ambos costes vuelven a multiplicarse.

## 2. La asimetría PDF

EPUB y Wikipedia exponen un árbol semántico (HTML), así que su Reader es un *mapeo*. PDF
expone glifos con coordenadas, así que su Reader es un *pipeline de inferencia*:

```
caracteres(x, y, fuente, tamaño)
  → líneas            (agrupar por línea base y proximidad)
  → bloques           (agrupar líneas por interlineado y alineación)
  → orden de lectura  (columnas, XY-cut)
  → filtrado          (cabeceras/pies repetidos, números de página)
  → clasificación     (título / párrafo / lista / tabla / figura / pie)
  → IR
```

Cada etapa puede equivocarse, y los errores se componen. Por eso el Reader PDF adjunta
`Provenance` y `confidence` a cada bloque, y emite diagnósticos en lugar de fallar.

## 3. La representación intermedia

Esquema orientativo; la fuente de verdad es `src/tomd/ir/nodes.py`.

```python
@dataclass(frozen=True)
class Provenance:
    source_ref: str                  # archivo dentro del EPUB, URL, ruta del PDF
    page: int | None = None          # solo PDF
    bbox: tuple[float, float, float, float] | None = None
    confidence: float = 1.0          # < 1.0 en inferencias (PDF, OCR)

@dataclass(frozen=True)
class Diagnostic:
    code: str                        # p. ej. "table.html_fallback" (catálogo en ADR-006)
    severity: Literal["info", "warning", "error"]
    message: str
    provenance: Provenance | None

# Inline
Text(value) | Emph(children) | Strong(children) | Code(value)
Link(children, href, title) | InlineMath(tex) | FootnoteRef(key) | LineBreak()

# Bloques
Heading(level: 1..6, children)
Paragraph(children)
ListBlock(ordered: bool, start: int, items: tuple[ListItem, ...])
ListItem(blocks)
BlockQuote(blocks)
CodeBlock(code, language)
MathBlock(tex)
Table(caption, header_rows, body_rows)    # celdas con rowspan/colspan y bloques
Image(src_asset, alt, caption)
FootnoteDef(key, blocks)
ThematicBreak()

@dataclass(frozen=True)
class Document:
    meta: DocumentMeta               # alimenta el front matter (ADR-010)
    blocks: tuple[Block, ...]
    assets: tuple[Asset, ...]        # bytes + hash + tipo MIME
    diagnostics: tuple[Diagnostic, ...]
```

Decisiones de diseño de la IR:

- **La IR es más expresiva que Markdown** a propósito (celdas con spans, bloques dentro de
  celdas). Así la decisión de *cómo degradar* se toma en un solo sitio, el Writer, según
  ADR-006, y no en cada lector.
- **Las notas al pie usan claves**, no números. La numeración final la decide el transform
  `renumber_footnotes`, porque en EPUB las notas viven en archivos distintos.
- **`frozen=True`**: los transforms devuelven árboles nuevos, lo que los hace fáciles de
  testear y elimina efectos laterales entre pasadas.
- Cada nodo de bloque admite `provenance: Provenance | None`.

## 4. Transforms (orden de ejecución)

1. `drop_empty` — elimina párrafos vacíos y espacios residuales.
2. `merge_adjacent_text` — fusiona `Text` contiguos.
3. `normalize_headings` — un único H1 (título del documento); sin saltos de nivel
   (H2→H4 pasa a H2→H3).
4. `renumber_footnotes` — numeración 1..n por orden de primera referencia; elimina
   definiciones huérfanas con diagnóstico.
5. `dedupe_assets` — imágenes idénticas por hash se almacenan una sola vez.

## 5. Lectores

| Lector | Entrada | Núcleo | Partes compartidas |
|---|---|---|---|
| `epub` | `.epub` | OPF + spine → XHTML | `html_common` |
| `wikipedia` | título o URL | Parsoid HTML | `html_common` + reglas wiki |
| `pdf` | `.pdf` | `PdfBackend` (heuristic / docling) + OCR opcional | — |

`html_common` contiene el mapeo HTML→IR genérico. Los lectores EPUB y Wikipedia solo
añaden reglas específicas (clases CSS de títulos, infoboxes, referencias) mediante un
registro de *handlers* por selector, sin bifurcar el mapeo base.

## 6. Pipeline y lotes

- `pipeline.dispatch` elige el lector por extensión, tipo MIME o forma de URL.
- `pipeline.batch` usa un pool de procesos. Cada worker convierte un documento con timeout
  y límite de memoria (`resource.setrlimit`), y escribe el resultado de forma atómica
  (archivo temporal + rename).
- El manifiesto SQLite (ADR-008) es la única fuente de verdad del estado del lote.

## 7. Disposición de la salida

```
out/
  <slug>.md
  <slug>.assets/<sha256-12>.png
  manifest.sqlite
  report.md            # resumen del lote: estados, diagnósticos más frecuentes, lentos
```

El `<slug>` deriva del título normalizado, con desambiguación por prefijo de hash si
colisiona. Es determinista.

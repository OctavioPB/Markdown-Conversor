# DECISIONS.md — Registro de decisiones (ADR)

Formato y procedimiento: skill `record-decision`. Estados posibles: *Propuesta*,
*Aceptada*, *Sustituida por ADR-NNN*, *Rechazada*. Las decisiones no se editan una vez
aceptadas; se sustituyen con un ADR nuevo, para que el historial explique por qué el
proyecto es como es.

---

## ADR-001 — Python 3.12 con uv
**Estado:** Aceptada
**Contexto:** El ecosistema de extracción de documentos (PDF, OCR, modelos de layout)
es mayoritariamente Python. El sistema de IA local que consume la salida también lo es.
**Decisión:** Python 3.12, gestión con uv, ruff para lint y formato, mypy estricto.
**Consecuencias:** El rendimiento en lotes grandes depende de paralelizar por procesos,
no por hilos (GIL), y eso alimenta el diseño de la Fase 7.

## ADR-002 — Arquitectura en tres capas con IR propia
**Estado:** Aceptada
**Contexto:** Las tres fuentes difieren radicalmente. EPUB y Wikipedia traen semántica, y
convertirlos es *traducir*. PDF trae glifos posicionados, y convertirlo es *reconstruir*.
Sin una capa común habría tres serializadores divergentes.
**Decisión:** lectores → IR → serializador único. La IR es propia y no el AST de Pandoc.
**Alternativa considerada:** usar el AST JSON de Pandoc como IR. Es maduro y daría EPUB
casi gratis. Se descarta porque no tiene sitio natural para la **procedencia** (página,
bbox, confianza OCR) ni para los **diagnósticos**, que son centrales en PDF y en la
auditoría de un lote de miles de documentos.
**Consecuencias:** Más código propio. Pandoc se mantiene como oráculo en tests
comparativos de EPUB (no como dependencia de ejecución).

## ADR-003 — Dialecto de salida
**Estado:** Aceptada
**Decisión:** CommonMark + tablas GFM + notas al pie `[^n]` + matemáticas `$…$`/`$$…$$`
+ front matter YAML. Especificación completa en `docs/MARKDOWN_SPEC.md`.
**Motivo:** Es el subconjunto que entienden los parsers comunes (markdown-it, Obsidian,
Pandoc) y que un pipeline de RAG puede trocear por títulos sin ambigüedad.
**Consecuencias:** Todo lo que no cabe en el dialecto pasa por la política de pérdidas
(ADR-006) y genera diagnóstico.

## ADR-004 — Wikipedia: HTML de Parsoid, no wikitext
**Estado:** Aceptada (el endpoint concreto debe verificarse en Fase 3)
**Contexto:** El wikitext solo tiene sentido tras expandir plantillas, y reimplementar el
parser de MediaWiki es inviable.
**Decisión:** Consumir el HTML semántico de Parsoid vía la API REST de Wikimedia. Wikimedia
ha estado migrando de RESTBase (`/api/rest_v1/page/html/…`) a la REST API del núcleo
(`/w/rest.php/v1/page/{título}/html`). En la Fase 3 se verifica cuál está vigente y se
fija aquí mediante un ADR de seguimiento.
**Consecuencias:** Dependencia de un servicio externo: se registra `revision_id` en el
front matter para reproducibilidad. Para volúmenes grandes se usarán dumps, no la API.
Se respeta la política de User-Agent y la licencia CC BY-SA (atribución en front matter).

## ADR-005 — PDF: backends intercambiables, 100 % locales
**Estado:** Propuesta (se decide el default con el benchmark de la Fase 5)
**Contexto:** Requisito del usuario: ninguna dependencia de modelos externos y menos de
8 GB de memoria disponible, compartida con el modelo local. Volumen: miles de PDF.
**Decisión provisional:** interfaz `PdfBackend` con dos implementaciones:
- `heuristic`: extracción de caracteres + heurísticas propias. Ligero y explicable.
- `docling`: modelos de layout locales en CPU. Más robusto con maquetación compleja.
**Licencias a vigilar:**
- PyMuPDF es AGPL-3.0. Aceptable para uso personal; problemático si el conversor se
  distribuye como servicio. Alternativa MIT: pdfplumber (sobre pdfminer.six), más lenta.
- Docling: MIT.
- Marker: GPL-3.0, con pesos de modelo bajo licencia restrictiva para uso comercial. Se
  descarta por licencia y por memoria.
**Pendiente:** el benchmark de la Fase 5 mide calidad, tiempo por página y memoria pico.

## ADR-006 — Política de pérdidas de información
**Estado:** Aceptada
**Decisión:** ante una estructura que no cabe en el dialecto:

| Caso | Acción | Diagnóstico |
|---|---|---|
| Tabla con `rowspan`/`colspan` o bloques en celdas | HTML embebido `<table>` mínimo | `table.html_fallback` |
| Infobox de Wikipedia | Tabla de dos columnas tras el título | `wiki.infobox_flattened` |
| Fórmula sin LaTeX recuperable | Texto plano en `` `…` `` | `math.unrecoverable` |
| Imagen no extraíble | Se omite y se conserva el pie | `image.dropped` |
| Orden de lectura dudoso (PDF) | Mejor estimación | `pdf.reading_order_uncertain` |

**Motivo:** una política única y explícita es mejor que decisiones caso a caso dispersas
por los lectores, porque hace auditables las pérdidas a escala.

## ADR-007 — Evaluación con corpus dorado y métricas por caso
**Estado:** Aceptada
**Decisión:** casos con `expected.md` hechos a mano. Las métricas son similitud de texto
normalizada, F1 de títulos, precisión de orden de lectura y exactitud de celdas de tabla
(ver `docs/TESTING.md`). Se informa por caso, no solo en promedio.
**Motivo:** las heurísticas PDF arreglan un documento y rompen otros; un promedio esconde
esas regresiones.
**Consecuencias:** curar `expected.md` cuesta tiempo humano, que es el cuello de botella
real del proyecto.

## ADR-008 — Lotes idempotentes con manifiesto SQLite
**Estado:** Aceptada
**Decisión:** la clave de idempotencia es el par (sha256 de la entrada, versión del
conversor). El manifiesto SQLite registra estado, diagnósticos, duración y memoria pico.
Se usan workers por proceso con timeout y límite de memoria.
**Motivo:** con miles de PDF habrá fallos, interrupciones y reconversiones tras mejorar
heurísticas. Hay que poder reprocesar solo lo afectado.

## ADR-009 — Sin manejo de DRM
**Estado:** Aceptada
**Decisión:** los EPUB/PDF con DRM se detectan y se rechazan con un error explícito. No
se implementa ni se integra ningún mecanismo de eliminación de DRM.

## ADR-010 — Front matter orientado a RAG y salida determinista
**Estado:** Aceptada
**Decisión:** el front matter incluye `title`, `source_type`, `source`, `source_sha256`,
`converter_version`, `backend`, `language`, `authors`, `license`, `revision_id`
(Wikipedia) y `diagnostics_count`. No incluye fecha de conversión, que va al manifiesto.
**Motivo:** el sistema de IA local necesita filtrar y citar por origen, y el determinismo
byte a byte permite detectar cambios reales con un simple diff o hash.

## ADR-011 — Fase 1: dependencias solo de test y huecos de la especificación
**Estado:** Aceptada
**Contexto:** La Fase 1 exige tests de propiedad del escapado (hypothesis) y re-parsear la
salida con `markdown-it-py`. Al implementar el Writer aparecieron huecos en
`MARKDOWN_SPEC.md` que había que cerrar antes de escribir código. Licencias verificadas
el 2026-10-15.
**Decisión:**
1. Dependencias **solo de desarrollo**: `hypothesis` (MPL-2.0), `markdown-it-py` y
   `mdit-py-plugins` (MIT), `pyyaml` (MIT, para validar el front matter). El conversor no
   tiene dependencias de ejecución nuevas: el front matter YAML se emite a mano.
2. Orden de claves del front matter: el de ADR-010. Se corrige el ejemplo de
   `MARKDOWN_SPEC.md`, que situaba `revision_id` tras `source_sha256`.
3. Política de H1: el Writer emite un único H1 = `meta.title`. Si el documento trae H1
   propios, todos los títulos bajan un nivel (tope H6, diagnóstico `heading.level_clamped`);
   un H1 idéntico al título se funde con él. Sin título, el primer H1 se promueve a título;
   sin ninguno, error `doc.title_missing`.
4. Dos listas consecutivas del mismo tipo se separan con `<!-- -->` (si no, el parser las
   funde y la ordenada pierde su `start`).
5. Los saltos de línea en celdas de tabla GFM se emiten como `<br>`.
6. El Writer colapsa secuencias de espacios ASCII en `Text` a un espacio; los espacios
   Unicode (NBSP, espacio fino) se conservan. Los saltos de línea solo vienen de `LineBreak`.
7. Nuevos códigos de diagnóstico: `table.header_promoted`, `table.empty_dropped`,
   `footnote.orphan_definition`, `footnote.missing_definition`,
   `footnote.duplicate_definition`, `heading.level_clamped`, `doc.title_missing`.
**Alternativas consideradas:**
- PyYAML en ejecución para el front matter: su estilo de comillas y plegado de líneas
  depende de la versión, lo que pone en riesgo el determinismo byte a byte (M22).
- Tablas con saltos de línea → HTML embebido: la mayoría de tablas de Wikipedia caerían
  al fallback, con más ruido para el RAG.
- Fundir listas consecutivas en un transform: pierde la numeración de la segunda lista.
**Consecuencias:** Un emisor YAML propio, pequeño pero que hay que mantener correcto (se
valida con PyYAML en tests). `<!-- -->` y `<br>` añaden HTML mínimo a la salida. Bajar todos
los niveles cuando hay varios H1 puede llevar libros muy anidados a H6 y aplanar niveles.

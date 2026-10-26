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

## ADR-012 — lxml para HTML/XML; rapidfuzz para evaluación
**Estado:** Aceptada
**Contexto:** Los lectores EPUB y Wikipedia necesitan un árbol HTML/XML. El XHTML de los
EPUB suele estar mal formado (entidades como `&nbsp;` sin DTD) y el HTML de Parsoid es
HTML5. La evaluación del corpus necesita distancia de Levenshtein rápida. Licencias
verificadas el 2026-10-16.
**Decisión:** `lxml` (BSD-3) como dependencia de ejecución para XML (OPF, container) y
HTML, configurado siempre con `resolve_entities=False` y `no_network=True` (sin XXE ni red).
`rapidfuzz` (MIT) solo como dependencia de desarrollo para `scripts/eval_corpus.py`. El
cliente HTTP de Wikipedia usa `urllib` de la biblioteca estándar.
**Alternativas consideradas:**
- `ebooklib`: AGPL-3.0.
- Solo biblioteca estándar: `xml.etree` falla con entidades no declaradas y `html.parser`
  no construye árbol.
- BeautifulSoup: capa extra sobre lxml sin ganancia para un mapeo por handlers.
- `httpx`/`requests`: nada que aporten a peticiones secuenciales con límite de tasa.
**Consecuencias:** Extensión en C: depende de que existan wheels para cada plataforma.
Un parser más potente de lo necesario, con superficie de ataque que hay que acotar por
configuración.

## ADR-013 — Wikipedia: REST API del núcleo, fijada por revisión
**Estado:** Aceptada (seguimiento de ADR-004; verificado el 2026-10-16)
**Contexto:** RESTBase (`/api/rest_v1/page/html/…`) está en proceso de deprecación y su
sustituto es la REST API del núcleo (`/w/rest.php/v1/…`), que sirve HTML de Parsoid
conforme a Specs/HTML. Los endpoints nuevos tienen TTL de caché bajo y sin purga.
**Decisión:** `GET /w/rest.php/v1/page/{título}/bare` para obtener la última revisión y
`GET /w/rest.php/v1/revision/{id}/html` para el contenido. Con `--revision N` se omite la
primera petición. Peticiones secuenciales, ≤ 1/s, con User-Agent identificable, respeto
de `429`/`Retry-After` y reintentos acotados. Las imágenes (servidas por
`upload.wikimedia.org`, fuera de la API) no se descargan: se conserva el pie y se emite
`image.dropped` (info).
**Alternativas consideradas:**
- RESTBase: en deprecación.
- `page/{título}/with_html` en una sola petición: no garantiza poder reproducir la misma
  revisión más tarde ni que contenido y `revision_id` correspondan.
**Consecuencias:** Dos peticiones por artículo. Si el HTML por revisión difiriera del de
página, los fixtures grabados lo mostrarán. Los artículos convertidos no llevan imágenes.

## ADR-014 — PDF: pypdfium2 para extracción y Pillow para imágenes
**Estado:** Aceptada (verificado el 2026-10-16)
**Contexto:** El backend `heuristic` (ADR-005) necesita caracteres con caja, fuente,
tamaño y peso; la Fase 5 extrae imágenes y la Fase 6 renderiza páginas para OCR. Volumen
de miles de PDF con < 4 GB por worker.
**Decisión:** `pypdfium2` (Apache-2.0 / BSD-3, binding de PDFium) para abrir, extraer
caracteres y renderizar. `Pillow` (HPND) para codificar como PNG las imágenes que no sean
JPEG (los JPEG se copian byte a byte).
**Alternativas consideradas:**
- `pdfplumber`/`pdfminer.six` (MIT): Python puro, ~10× más lento y con más memoria por
  página; inviable para lotes de miles.
- PyMuPDF: AGPL-3.0 (ver CLAUDE.md y ADR-005).
**Consecuencias:** Tamaño, peso y nombre de fuente por carácter solo están en la API de
bajo nivel de PDFium (una llamada por carácter): más código propio y coste de llamadas
ctypes. El árbol de estructura (*tagged PDF*) también requiere la API de bajo nivel.

## ADR-015 — Corpus PDF/Wikipedia, docling opcional y guiones sin diccionario
**Estado:** Aceptada
**Contexto:** Curar `expected.md` es el cuello de botella (ADR-007) y la skill
`golden-corpus` prohíbe redactarlos a partir de la salida del conversor. La Fase 5 exige
un benchmark contra docling, que arrastra PyTorch y modelos de varios GB.
**Decisión:**
1. Corpus PDF mixto: PDFs sintéticos generados con `reportlab` (BSD, solo desarrollo),
   cuyo `expected.md` sale del mismo contenido que los genera, más extractos de 1–2
   páginas de PDFs reales con licencia CC BY.
2. Para Wikipedia, Pandoc (vía `pypandoc-binary`, solo desarrollo; GPL, nunca enlazado
   ni distribuido con tomd) como oráculo independiente para redactar borradores, que se
   adaptan a mano al dialecto. Extractos de una sección por caso.
3. Todos los borradores llevan `# DRAFT` hasta la revisión humana.
4. docling como extra opcional `tomd[docling]`, solo para el benchmark; el backend por
   defecto sigue siendo `heuristic` hasta el ADR del benchmark.
5. Guiones de corte sin diccionario: se unen si la línea siguiente empieza en minúscula,
   se conservan en otro caso, y la confianza baja. Diccionarios Hunspell, en un ADR futuro.
**Alternativas consideradas:** transcripción manual pura (lenta y propensa a errores);
docling siempre instalado (entorno de desarrollo de varios GB para todos); Hunspell ya
(es_ES tri-licencia GPL/LGPL/MPL, a evaluar aparte).
**Consecuencias:** Los sintéticos no capturan las rarezas de generadores reales (fuentes
sin ToUnicode, kerning extremo); por eso se combinan con PDFs reales. Sin diccionario,
compuestos con guion que caen a final de línea pueden perder el guion
(`socio-económico` → `socioeconómico` solo si la siguiente línea empieza en minúscula).

## ADR-016 — Backend PDF por defecto: `heuristic`; `docling` opcional por documento
**Estado:** Propuesta (resuelve ADR-005 cuando se acepte)
**Contexto:** Benchmark de la Fase 5 (`scripts/benchmark_pdf_backends.py`, informe en
`docs/benchmarks/2026-10-16-pdf-backends.md`). En los 6 PDFs sintéticos del corpus ambos
backends alcanzan similitud ≥ 0.97, F1 de títulos 1.0 y orden de lectura 1.0 (docling tras
aplicar la misma regla de numeración de títulos). En PDFs reales de 9–22 páginas:
`heuristic` 0.07–0.27 s/página y 50–500 MB de pico; `docling` 1.4–2.8 s/página y
1.8–2.1 GB de pico, dentro del presupuesto de 4 GB por worker pero 10–20× más lento.
**Decisión:** `heuristic` es el backend por defecto; `docling` queda como extra opcional
(`tomd[docling]`) seleccionable con `--backend docling` para documentos que el heurístico
resuelve mal (tablas sin líneas, maquetación muy irregular).
**Alternativas consideradas:**
- `docling` por defecto: en un lote de miles de PDF de ~20 páginas supone del orden de
  30 s por documento frente a 2 s, y ~2 GB por worker deja sitio para un solo worker junto
  al modelo local (< 8 GB en total).
- Selección automática por documento (heurístico y, si los diagnósticos son malos,
  docling): atractiva, pero sin corpus real revisado no hay datos para fijar el criterio.
**Consecuencias:** Las tablas sin rejilla de líneas (§9.2) y las maquetaciones irregulares
dependen de usar `--backend docling` a mano. **Pendiente para aceptar:** el corpus
sintético favorece al heurístico (fuentes limpias, maquetación regular); hace falta
repetir el benchmark de calidad sobre PDFs reales de dos columnas con `expected.md`
revisado antes de pasar este ADR a Aceptada.

## ADR-017 — OCR con Tesseract (dependencia de sistema opcional) vía pytesseract
**Estado:** Aceptada (verificado el 2026-10-16: Tesseract 5.4.0, tessdata_fast)
**Contexto:** La Fase 6 exige OCR local (sin APIs externas) para páginas escaneadas o
con texto basura, en español e inglés, dentro del presupuesto de memoria por worker.
**Decisión:**
- Motor: Tesseract (Apache-2.0), instalado como software de sistema; no se distribuye con
  tomd. Si falta y una página lo necesita, la conversión sigue y se emite
  `pdf.ocr_unavailable` (no es un error fatal).
- Binding: `pytesseract` (Apache-2.0, Python puro, llama al ejecutable). Se usa la salida
  TSV (cajas y confianza por palabra) para construir `Line` en coordenadas PDF, de modo
  que el resto del pipeline heurístico (bloques, títulos, orden) no cambia.
- Datos de idioma `spa+eng` de `tessdata_fast` (Apache-2.0), buscados en
  `TOMD_TESSDATA` o en `%LOCALAPPDATA%/tomd/tessdata` (o la instalación por defecto).
- Renderizado a 300 ppp con pypdfium2; `OMP_THREAD_LIMIT=1` por determinismo y memoria.
- Detector por página: menos de 20 caracteres con una imagen que cubre > 50 % de la
  página, o > 30 % de caracteres basura; `--ocr always|never|auto` para forzarlo.
**Alternativas consideradas:**
- `tesserocr` (MIT, API de C): más rápido, pero sin wheels fiables para Windows.
- docling con OCR: arrastra modelos y memoria (ADR-016); innecesario para escaneados simples.
- Modelos de visión (Nougat y similares): fuera del presupuesto de memoria (PLAN.md).
**Consecuencias:** Un proceso de Tesseract por página (coste fijo de arranque). La
calidad depende de los datos de idioma instalados. Sin diccionario, un texto OCR previo
legible pero erróneo no se detecta automáticamente: hay que forzar `--ocr always`.

## ADR-018 — Libros divididos por capítulos: una carpeta por libro, un `.md` por capítulo
**Estado:** Aceptada (2026-10-20)
**Contexto:** Un libro técnico de cientos de páginas produce un único `.md` enorme: difícil
de revisar y de navegar, y poco útil para el sistema de IA local, que trabaja mejor con
unidades temáticas acotadas. El usuario pide dividirlo por capítulos (2026-10-20) y que el
resultado sea una carpeta con el nombre del libro. Hoy la salida es un archivo por fuente
(ARCHITECTURE §7, M06, M20) y el manifiesto guarda una sola salida por documento (ADR-008).
El lector PDF no lee los marcadores (*outline*) del PDF; el lector EPUB ya lee su índice.
**Decisión:**
- **Dónde:** la división es un paso del pipeline *después* de los transforms, una función
  pura `split_document(doc, opciones)` que devuelve un libro (índice + capítulos, cada uno
  un `Document`) o nada. Los lectores siguen produciendo un único `Document`; el writer
  serializa cada parte. Ningún lector decide la división.
- **IR:** `Document` gana `outline: tuple[OutlineEntry, ...]` (`title`, `level`, `page` en
  PDF, `target` en EPUB), rellenado con los marcadores del PDF (pypdfium2) y el índice del
  EPUB. Es información de la fuente, no de presentación.
- **Fronteras, por fiabilidad:** (1) entradas del `outline` del nivel pedido, ancladas al
  título de la IR que coincide en su página o destino; (2) si no hay `outline`, títulos del
  nivel más alto que se repite («Capítulo N», «Chapter N», «N Título»). Si el reparto no es
  plausible (menos de 2 partes, o partes vacías o desproporcionadas, con umbrales con nombre
  y motivo), no se divide y se emite `doc.split_skipped` (info): la salida única no pierde
  nada.
- **Cuándo:** `--split auto|chapters|none`, por defecto `auto`: divide solo libros PDF o
  EPUB de al menos ~150 000 caracteres de texto (≈ 80 páginas; el criterio es el texto
  porque un EPUB no tiene páginas) con fronteras fiables. Wikipedia nunca se divide.
  `--split-level 1|2` (capítulos por defecto; 2 = secciones).
- **Salida:** carpeta `<slug-del-libro>/` con `index.md` (título, autoría y enlaces a los
  capítulos en orden), `NN-<slug-del-capítulo>.md` (`00-` para lo anterior al primer
  capítulo, si tiene texto) y `assets/` compartido. El nombre de la carpeta se reserva en
  el manifiesto como hoy los slugs.
- **Especificación (por archivo):** cada capítulo cumple MARKDOWN_SPEC por sí mismo: un H1
  igual a su `title` (el título del capítulo; los subniveles suben), notas numeradas 1..n
  dentro del capítulo (una nota agrupada al final del libro va al capítulo que la cita) e
  imágenes en `assets/<hash>.<ext>`. El front matter de ADR-010 se amplía con `book` (título
  del libro), `chapter` (número) y `chapters` (total), tras `title`; el mismo
  `source_sha256` en todos. Los diagnósticos con página van al capítulo que la contiene; el
  resto, al índice.
- **Manifiesto (esquema 3):** tabla `outputs` con las partes de cada documento (ruta,
  título, páginas); `documents.output` apunta a `<carpeta>/index.md`; los anclajes de la
  revisión llevan la parte a la que pertenecen.
**Alternativas consideradas:**
- No dividir y dejar el troceado al sistema de IA: lo descarta el requisito del usuario, y
  un archivo de varios MB es inmanejable en la revisión lado a lado.
- Dividir dentro de cada lector: duplica la lógica en PDF y EPUB y obliga a los lectores a
  conocer la disposición de la salida (rompe ADR-002).
- Dividir por tamaño fijo o por número de páginas: corta capítulos y párrafos por la mitad;
  una unidad sin sentido temático empeora la búsqueda en lugar de mejorarla.
- Solo títulos detectados, sin marcadores: en el corpus los títulos PDF son una inferencia
  por tamaño de fuente (ADR-005); los marcadores, cuando existen, los escribió el editor.
**Consecuencias:** Una fuente deja de ser siempre un archivo: el manifiesto, la biblioteca
y la revisión de la app (fila por libro, selector de capítulo), `eval_corpus.py` (casos con
varios archivos esperados) y `remove_output` deben conocer las carpetas. M06 y M20 pasan a
leerse «por archivo». Cambiar `--split` o `--split-level` no reconvierte un libro ya hecho
(la clave sigue siendo sha256 + versión, como ya ocurre con `--ocr`): hace falta `--force`.
La división no reduce la memoria: el libro entero sigue en la IR antes de dividirse, así que
hay que medir el pico con un libro de ~500 páginas frente al límite de 4 GB, y el timeout
por documento del lote debe crecer con el número de páginas.

## ADR-019 — Modo «Para RAG»: quitar las secciones que no son contenido del libro
**Estado:** Aceptada (2026-10-26; opciones elegidas por el usuario)
**Contexto:** La biblioteca alimenta un sistema de IA local. Las secciones que no son
contenido (cubierta, copyright, «sobre el autor», agradecimientos, índices, bibliografía,
contracubierta…) ensucian la búsqueda: aparecen en resultados por sus palabras sin aportar
conocimiento. El usuario pide poder quitarlas, conservando el `index.md` que tomd genera
para un libro dividido (ADR-018).
**Decisión:**
- Opción `--rag` (y «Para RAG» en la app), **desactivada por defecto**: sin ella la salida
  no cambia.
- Un transform puro, `drop_non_content`, aplicado tras los transforms por defecto y antes
  de dividir el libro. Quita, por su título (es y en): cubierta y contracubierta,
  portadilla y página de título, copyright y página legal, dedicatoria, índice de
  contenidos (también «Contents at a Glance») e índice alfabético, «sobre el autor/los
  editores/colaboradores/revisores», agradecimientos, bibliografía y referencias (también
  «Further Reading» al final de un capítulo), listas de figuras y tablas, elogios y
  anuncios («Praise for», «Also by»), colofón y licencias del distribuidor (EULA). Se
  conservan prefacio, introducción, apéndices, glosario y notas.
- Una sección es el título y todo lo que sigue hasta otro título de su nivel o superior.
  Las entradas del índice del PDF con esos títulos quitan además sus páginas (lo que no
  tiene título propio, como el texto de una página de copyright).
- Toda sección quitada emite `doc.section_dropped` (info) con su título (ADR-006: ninguna
  pérdida silenciosa). Las notas y las imágenes que solo estaban en lo quitado se van con
  ello, sin avisos de notas huérfanas.
**Alternativas consideradas:**
- Quitarlas siempre: pierde información sin pedirlo y rompe la salida de quien quiere el
  libro entero (por eso el usuario lo eligió desactivado).
- Marcarlas en el front matter en lugar de quitarlas: el sistema de RAG tendría que
  conocer y filtrar esa marca, y los archivos seguirían llevando el ruido.
- Detectarlas por posición (todo lo anterior al capítulo 1): también quitaría el prefacio
  y la introducción, que el usuario quiere conservar.
**Consecuencias:** La detección por título es por idioma y por costumbre editorial: una
sección con un título inusual no se quita, y una con un título de la lista pero contenido
útil sí (queda anotada en los avisos). Activar o desactivar `--rag` no reconvierte lo ya
convertido (la clave es sha256 + versión, como con `--split`): hace falta `--force` o
«Empezar de cero».

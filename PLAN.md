# PLAN.md — Hoja de ruta de tomd

Cada fase tiene un **criterio de terminado** verificable. Una fase no se cierra porque
"el código existe", sino cuando el criterio se cumple sobre el corpus dorado.
Los umbrales numéricos son **provisionales**: se calibran al final de la Fase 2, cuando
exista un baseline real, y el ajuste se registra en `DECISIONS.md`.

Orden elegido: de menor a mayor incertidumbre estructural. EPUB obliga a diseñar bien la
IR con poco ruido; PDF va al final porque es reconstrucción, no traducción (ver ADR-002).

---

## Fase 0 — Andamiaje
- [x] `pyproject.toml` con uv, ruff, mypy estricto, pytest (marcador `slow`)
- [x] Estructura de paquetes según `CLAUDE.md`
- [x] CLI mínima `tomd --version`
- [x] CI local: script `scripts/check.sh` (ruff + mypy + pytest -m "not slow")
- [x] Esqueleto de `scripts/eval_corpus.py` que recorre `tests/corpus/` y emite un informe vacío

**Terminado cuando:** `scripts/check.sh` pasa en un repositorio limpio.

## Fase 1 — IR y serializador
- [x] Nodos de bloque e inline según `docs/ARCHITECTURE.md`
- [x] `Diagnostic` y `Provenance` en la IR
- [x] Serializador IR→Markdown que cumple `docs/MARKDOWN_SPEC.md` completo
- [x] Política de tablas: GFM si es simple, HTML embebido si no (ADR-006)
- [x] Transforms: normalizar niveles de título, renumerar notas al pie
- [x] Tests de propiedad (hypothesis): el escapado nunca altera el texto renderizado

**Terminado cuando:** existe un test por cada regla de `MARKDOWN_SPEC.md`, y la salida
del serializador re-parseada con `markdown-it-py` produce el mismo árbol de bloques.

## Fase 2 — Lector EPUB
- [ ] `container.xml` → OPF → spine → XHTML en orden
- [ ] Mapeo HTML→IR (compartido con Wikipedia; vive en `readers/html_common.py`)
- [ ] Heurística de títulos por clases CSS cuando no hay `<h1..h6>`
- [ ] Notas al pie entre archivos: resolución y renumeración
- [ ] Imágenes extraídas a `assets/` con nombre por hash
- [ ] Detección de DRM y de maquetación fija → error / diagnóstico
- [ ] Corpus: ≥ 8 EPUB (Project Gutenberg y Standard Ebooks, dominio público)

**Terminado cuando:** similitud de texto ≥ 0.98 y F1 de títulos ≥ 0.95 en el corpus EPUB.
**Al cerrar:** calibrar umbrales del resto del plan con este baseline (ADR).

## Fase 3 — Lector Wikipedia
- [x] Cliente de la API de Wikimedia con User-Agent identificable y límite de tasa
- [x] Verificar el endpoint vigente de HTML Parsoid (ver ADR-004) y fijarlo
- [x] Reutilizar `html_common`; reglas específicas: infoboxes, navboxes, referencias, `<math>`
- [x] Recuperar LaTeX de las fórmulas desde las anotaciones de Parsoid
- [x] Front matter con licencia CC BY-SA, URL y `revision_id` (reproducibilidad)
- [x] Fixtures HTTP grabadas; ningún test toca la red
- [ ] Corpus: ≥ 10 artículos (tablas complejas, fórmulas, listas anidadas, idiomas es/en)
      (11 entradas grabadas en `tests/corpus/wikipedia/`; faltan los `expected.md` revisados)

**Terminado cuando:** similitud ≥ 0.97, todas las fórmulas del corpus salen como LaTeX,
y las tablas no representables generan diagnóstico + HTML embebido.

## Fase 4 — PDF digital simple
- [x] Backend `heuristic` sobre extracción de caracteres con posición y fuente (ADR-005)
- [x] Agrupación caracteres → líneas → bloques
- [x] Unión de líneas en párrafos y reparación de guiones de corte
- [x] Normalización Unicode (ligaduras, espacios raros) y detección de texto basura
- [x] Títulos por estadística de tamaño/peso de fuente en el documento
- [x] Listas por viñetas y numeración
- [ ] Corpus: ≥ 10 PDF de una columna (artículos arXiv con licencia CC, manuales)
      (5 sintéticos en `tests/corpus/pdf/` con borrador; faltan PDFs reales y la revisión)

**Terminado cuando:** similitud ≥ 0.95 y F1 de títulos ≥ 0.85 en PDFs de una columna.

## Fase 5 — PDF de maquetación compleja
- [x] Orden de lectura multicolumna (XY-cut recursivo como base)
- [x] Eliminación de cabeceras y pies repetidos
- [ ] Detección y extracción de tablas
      (hecho: tablas con rejilla de líneas, con spans; falta la estrategia por alineación, §9.2)
- [x] Figuras y pies de figura
- [ ] **Benchmark de backends:** `heuristic` vs `docling` sobre el mismo corpus,
      midiendo calidad, tiempo por página y memoria pico → ADR que decide el default
      (benchmark hecho: `docs/benchmarks/2026-10-16-pdf-backends.md`; ADR-016 Propuesta,
      pendiente de repetir la calidad sobre PDFs reales de dos columnas revisados)
- [x] Interfaz `PdfBackend` que permite elegir backend por CLI

**Terminado cuando:** el ADR del benchmark está aceptado y el backend por defecto alcanza
precisión de orden de lectura ≥ 0.90 en el corpus de dos columnas.

## Fase 6 — OCR
- [ ] Detector "¿necesita OCR?" por página (densidad de texto, proporción de basura)
- [ ] Integración Tesseract (spa+eng) para páginas escaneadas
- [ ] La confianza OCR viaja en `Provenance` y genera diagnósticos bajo un umbral
- [ ] Corpus: ≥ 5 PDF escaneados de calidades distintas

**Terminado cuando:** el detector no marca para OCR ninguna página digital del corpus,
y la similitud en escaneados limpios es ≥ 0.90.

## Fase 7 — Lotes a escala (miles de PDF)
- [ ] Manifiesto SQLite: hash de entrada, versión del conversor, estado, diagnósticos, tiempo
- [ ] Idempotencia: saltar entradas cuyo hash y versión ya están convertidos
- [ ] Reanudación tras interrupción sin reprocesar lo terminado
- [ ] Pool de workers con límite de memoria por worker y timeout por documento
- [ ] Aislamiento de fallos: un PDF corrupto no detiene el lote
- [ ] Informe final: conteos por estado, top diagnósticos, documentos más lentos

**Terminado cuando:** un lote de ≥ 500 PDF del corpus privado termina, se interrumpe y se
reanuda sin duplicados, y la memoria pico se mantiene dentro del presupuesto.

## Fase 8 — Endurecimiento
- [ ] Fuzzing de entradas malformadas (ZIP corruptos, PDF truncados)
- [ ] Revisión de diagnósticos: mensajes accionables
- [ ] README de uso y ejemplos
- [ ] Etiquetar v1.0

---

## Fuera de alcance (por ahora)
Conversión inversa (MD→PDF/EPUB), DOCX/HTML genérico (la arquitectura lo permite, pero
no es objetivo), fórmulas desde PDF mediante modelos de visión (Nougat y similares
exceden el presupuesto de memoria; reevaluar si cambia el hardware).

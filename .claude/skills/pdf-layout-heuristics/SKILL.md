---
name: pdf-layout-heuristics
description: Guía de trabajo para el lector PDF de tomd: extracción de caracteres, agrupación en líneas y bloques, orden de lectura multicolumna, cabeceras y pies, títulos, listas, reparación de guiones, ligaduras, detección de texto corrupto y decisión de OCR, además de la interfaz de backends (heuristic/docling). Úsala siempre que se toque cualquier archivo en src/tomd/readers/pdf/, se depure una mala conversión de PDF o se ajuste un umbral, incluso para cambios que parezcan pequeños.
---

# Heurísticas de layout PDF

Un PDF no contiene párrafos ni títulos: contiene glifos dibujados en coordenadas. Todo lo
que el lector produce es una **inferencia**, y cada etapa hereda los errores de la
anterior. Trabaja siempre con eso en mente: mide antes y después, y prefiere heurísticas
que fallen de forma detectable (con diagnóstico) a heurísticas que fallen en silencio.

## Flujo de trabajo obligatorio

1. Reproduce el problema en un caso del corpus (skill `golden-corpus`). Si no existe, créalo.
2. Localiza la **etapa** que falla. Usa `uv run tomd debug-pdf <archivo> --page N --stage
   lines|blocks|order|classify`, que vuelca un SVG con las cajas de cada etapa superpuestas.
   Si este comando aún no existe, constrúyelo primero: depurar layout sin verlo es adivinar.
3. Cambia **una** heurística o **un** umbral.
4. Ejecuta el corpus completo de PDF, no solo el caso, y reporta regresiones.

## Pipeline y responsabilidades

| Etapa | Módulo | Detalle en |
|---|---|---|
| Extracción de caracteres | `pdf/extract.py` | `references/heuristics.md` §1 |
| Líneas y espaciado | `pdf/lines.py` | §2 |
| Bloques | `pdf/blocks.py` | §3 |
| Orden de lectura | `pdf/order.py` | §4 |
| Cabeceras y pies | `pdf/furniture.py` | §5 |
| Clasificación (título, lista…) | `pdf/classify.py` | §6 |
| Texto: guiones, ligaduras | `pdf/text.py` | §7 |
| ¿Necesita OCR? | `pdf/ocr_gate.py` | §8 |
| Tablas | backend | §9 |

Lee la sección de `references/heuristics.md` correspondiente a la etapa que vayas a tocar.

## Reglas

- **Umbrales relativos, no absolutos.** Expresa las distancias en múltiplos del tamaño de
  fuente o del interlineado mediano *del documento*. Un umbral en puntos funciona en un
  documento y falla en el siguiente.
- **Parámetros con nombre en `PdfHeuristicsConfig`**, cada uno con un comentario de por qué
  tiene ese valor y qué caso del corpus lo motivó.
- **Estadísticas por documento** (tamaño de cuerpo, interlineado) calculadas una vez y
  pasadas explícitamente, no recalculadas por página.
- **Si existe árbol de estructura** (*tagged PDF*, `StructTreeRoot`) y supera la
  validación de cordura (§1), úsalo como fuente primaria y las heurísticas como respaldo.
- **Confianza:** cada bloque lleva `Provenance.confidence`. Las decisiones con poco margen
  (por ejemplo, un tamaño apenas mayor que el cuerpo) bajan la confianza; por debajo de
  0.5 se emite diagnóstico.
- **Backends:** todo lo específico de docling vive tras `PdfBackend`. Ninguna etapa común
  importa docling directamente.
- **Memoria:** procesa página a página y libera objetos de la librería de extracción; no
  cargues el documento entero como lista de caracteres. Presupuesto en `CLAUDE.md`.

## Antipatrones

- Ajustar un umbral hasta que pase un caso sin ejecutar el resto del corpus.
- Aplicar NFKC al texto de salida: además de las ligaduras, convierte `²` en `2` y `½`
  en `1⁄2`. Usa el mapa dirigido de §7.
- Detectar columnas con un valor fijo como "la mitad de la página".
- Tratar como error lo que es un límite conocido (fórmulas complejas): documenta el límite
  en `case.yaml` y emite el diagnóstico.

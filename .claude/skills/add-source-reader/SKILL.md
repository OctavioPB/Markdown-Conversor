---
name: add-source-reader
description: Procedimiento para crear, ampliar o reestructurar un lector de fuente en tomd (EPUB, Wikipedia, PDF o un formato nuevo) que convierte la entrada a la IR. Úsala siempre que la tarea implique tocar src/tomd/readers/, mapear HTML a IR, añadir soporte para un elemento de la fuente (notas, tablas, infoboxes, fórmulas) o registrar un formato nuevo en el pipeline, aunque el usuario no diga "lector".
---

# Añadir o ampliar un lector

Un lector traduce una fuente a `Document` (IR). Su único contrato es: **IR correcta +
diagnósticos honestos**. No sabe nada de Markdown.

## Antes de empezar

1. Lee `docs/ARCHITECTURE.md` (secciones 3 y 5) y la regla de ADR-006 que aplique.
2. Comprueba si el fenómeno ya se resuelve en `readers/html_common.py`. Para EPUB y
   Wikipedia, casi todo debería resolverse ahí o en un *handler* registrado, no en una
   bifurcación del mapeo base.
3. Identifica o crea un caso del corpus que contenga el fenómeno (skill `golden-corpus`).
   Escribe el caso **antes** del código: así sabrás cuándo has terminado.

## Pasos

1. **Ubicación:** `src/tomd/readers/<fuente>/`, con `reader.py` (punto de entrada que
   implementa `Reader.read(source) -> Document`) y módulos por responsabilidad.
2. **Mapeo:** cada construcción de la fuente produce el nodo IR más específico disponible.
   Si ninguno encaja, **no** generes texto con sintaxis Markdown. Elige entre:
   - ampliar la IR (propón el cambio y su efecto en el Writer antes de hacerlo), o
   - degradar según ADR-006 y emitir el `Diagnostic` correspondiente.
3. **Procedencia:** adjunta `Provenance` a cada bloque (archivo/URL; página y bbox en PDF).
   Es lo que permite depurar un error en un lote de miles de documentos.
4. **Metadatos:** rellena `DocumentMeta` con todo lo que ADR-010 pide y la fuente ofrezca.
   No inventes valores; omite lo desconocido.
5. **Errores:**
   - Entrada ilegible, corrupta o con DRM → excepción tipada de `tomd.errors`.
   - Contenido parcialmente recuperable → `Diagnostic` y seguir.
6. **Registro:** añade la detección en `pipeline/dispatch.py` (extensión, MIME o patrón
   de URL) con su test.
7. **Tests:** unitarios para cada regla de mapeo nueva, más el caso del corpus.
8. **Verificación:** `scripts/check.sh` y `scripts/eval_corpus.py`; reporta las métricas
   por caso, incluidas las de otros lectores si tocaste `html_common`.

## Señales de que vas mal

- Aparecen `"**"`, `"#"` o `"[^"` en cadenas dentro de un lector.
- Un `if` compara el título o el nombre de archivo de un documento concreto.
- Un elemento desconocido se descarta sin diagnóstico.
- El test solo comprueba que "no lanza excepción".

## Notas por fuente

- **EPUB:** respeta el orden del *spine*, no el orden del ZIP. Resuelve las rutas
  relativas al XHTML que las contiene. Las notas pueden estar en otro archivo, así que
  usa claves `archivo#id` y deja la numeración al transform.
- **Wikipedia:** trabaja sobre el HTML de Parsoid. Los atributos `typeof`/`data-mw`
  identifican plantillas y fórmulas (el LaTeX está en las anotaciones). Elimina navboxes,
  enlaces de edición y "véase también" vacíos mediante handlers, no con borrados por regex.
- **PDF:** lee también la skill `pdf-layout-heuristics`.

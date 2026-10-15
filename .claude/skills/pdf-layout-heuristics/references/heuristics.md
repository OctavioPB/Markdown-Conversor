# Referencia de heurísticas PDF

Valores iniciales razonables, a calibrar con el corpus. Notación: `s` es el tamaño de
fuente del carácter o línea, `S` el tamaño de cuerpo del documento y `L` el interlineado
mediano del documento.

## Índice
1. Extracción y estructura nativa
2. Líneas y espaciado
3. Bloques
4. Orden de lectura
5. Cabeceras, pies y números de página
6. Clasificación de bloques
7. Normalización de texto
8. Decisión de OCR
9. Tablas
10. Notas al pie

---

## 1. Extracción y estructura nativa

Extrae por carácter: `(text, x0, y0, x1, y1, fontname, size, flags, color)`. Obtén
`bold`/`italic` de los flags o, si faltan, del nombre de la fuente (`-Bold`, `-It`,
`Black`, `Semibold`).

**Tagged PDF:** si existe `StructTreeRoot`, compara el texto que cubre el árbol con el
extraído por coordenadas. Si cubre al menos el 90 % y contiene etiquetas `H1..H6`, `P`,
`L` o `Table`, úsalo. Muchos generadores producen árboles vacíos o con todo como `P`; en
ese caso descártalo con un diagnóstico `pdf.struct_tree_ignored`.

**Tamaño de cuerpo `S`:** la moda de `size` ponderada por número de caracteres, con
redondeo a 0.5 pt. **Interlineado `L`:** la mediana de las distancias entre líneas base
consecutivas dentro de bloques de tamaño `S`.

## 2. Líneas y espaciado

- Agrupa caracteres en la misma línea si sus líneas base difieren menos de `0.3·s` y se
  solapan verticalmente. Los superíndices (llamadas de nota, exponentes) quedan desplazados
  hacia arriba con `s` menor: asócialos a la línea cuyo rango vertical contenga su base.
- Ordena por `x0`. Muchos PDF **no contienen glifos de espacio**: inserta un espacio si el
  hueco entre caracteres supera `0.25 × ancho medio de carácter` de esa fuente. Si ya hay
  un espacio explícito, no dupliques.
- Un hueco mayor que `~2·s` dentro de una "línea" suele indicar dos columnas o una celda
  de tabla: parte la línea ahí.

## 3. Bloques

Abre un bloque nuevo cuando ocurra cualquiera de estas condiciones:
- hueco vertical mayor que `1.4·L`;
- cambio de tamaño de fuente mayor que `0.1·S`;
- cambio de alineación izquierda mayor que `1·S`, salvo una sangría de primera línea
  (solo la primera línea desplazada y las siguientes alineadas: es el mismo párrafo);
- la línea anterior termina mucho antes del margen derecho y en puntuación final (fin de
  párrafo en texto justificado).

## 4. Orden de lectura

**XY-cut recursivo** sobre las cajas de los bloques:
1. Proyecta las cajas sobre el eje X. Si existe un valle (franja sin cobertura) de anchura
   al menos `1.5·S` que atraviesa toda la región, corta verticalmente: son columnas, y se
   leen de izquierda a derecha.
2. Si no, proyecta sobre Y y corta en el valle mayor que `L`: son franjas, leídas de
   arriba abajo.
3. Recurre en cada subregión hasta que no haya cortes.

Caso problemático: elementos a lo ancho de la página (título, figura) dentro de un texto a
dos columnas. Antes del XY-cut, separa en franjas Y los bloques cuya anchura supere el
70 % del área de texto. Dentro de cada franja, aplica el XY-cut.

Las páginas con más de dos niveles de cortes ambiguos (valles de anchura similar) llevan el
diagnóstico `pdf.reading_order_uncertain`.

## 5. Cabeceras, pies y números de página

Candidatos: bloques en el 8 % superior o inferior del área de la página. Normaliza el texto
(dígitos → `#`, minúsculas, espacios colapsados). Si un texto normalizado aparece en al
menos el 40 % de las páginas, en una posición Y parecida (±`1·S`), es mobiliario y se
elimina. Considera páginas pares e impares por separado, porque en los libros la cabecera
alterna entre título del libro y título del capítulo.

Números de página sueltos (`#`, `- # -`, `Página #`) se eliminan aunque no se repitan
literalmente.

## 6. Clasificación de bloques

**Títulos.** Señales, de mayor a menor fuerza:
1. Numeración jerárquica al inicio (`3.2.1 `), que fija el nivel directamente (número de
   componentes).
2. Tamaño: `s ≥ 1.15·S`. Agrupa los tamaños de títulos distintos y ordénalos de mayor a
   menor: el mayor es H1 (título del documento, si está en la primera página) o H2, y así
   sucesivamente.
3. Negrita a tamaño `S`, bloque de una sola línea, sin puntuación final y seguido de un
   hueco: título de nivel bajo, con confianza moderada.

Un bloque largo (más de ~15 palabras o más de 2 líneas) casi nunca es un título, aunque
sea grande: suele ser un resumen o una cita destacada.

**Listas.** Línea que empieza con viñeta (`•◦▪‣–-*`) o patrón `^\(?(\d+|[a-z]|[ivx]+)[.)]\s`,
con sangría francesa (las líneas de continuación alineadas con el texto, no con la
viñeta). El nivel de anidación se infiere del `x0` de la viñeta, agrupado por tolerancia `0.5·S`.

**Pies de figura o tabla.** Bloque que empieza con `Figura|Fig.|Figure|Tabla|Table`
seguido de número, adyacente a una imagen o tabla.

**Código.** Fuente monoespaciada (nombre con `Mono`, `Courier`, `Consol`, `Code`) en todas
las líneas del bloque. Conserva los espacios iniciales tal cual.

## 7. Normalización de texto

- **Ligaduras:** mapa dirigido `ﬀ→ff, ﬁ→fi, ﬂ→fl, ﬃ→ffi, ﬄ→ffl, ﬅ→st, ﬆ→st`. No uses
  NFKC sobre el texto de salida (ver SKILL.md).
- **Guion blando** U+00AD: se elimina siempre.
- **Guion de corte a final de línea:** si la línea termina en `-` y la siguiente empieza
  en minúscula, une las partes. Mantén el guion si la palabra unida no está en el
  diccionario y la versión con guion sí lo está (`bien-estar` frente a `socio-económico`).
  Usa listas de palabras de Hunspell es/en, cargadas una sola vez. Sin diccionario, une
  por defecto y reduce la confianza.
- **Espacios:** NBSP y espacios finos → espacio normal; colapsa repeticiones.
- **Comillas y apóstrofes:** se conservan tal cual (no "corrijas" la tipografía).

## 8. Decisión de OCR

Una página necesita OCR si se cumple alguna de estas condiciones:
- menos de 20 caracteres extraídos y una imagen que cubre más del 50 % de la página;
- proporción de "basura" mayor que 0.3, siendo basura los caracteres U+FFFD, los del área
  de uso privado (U+E000–U+F8FF), los de control, o el porcentaje de tokens alfabéticos
  ausentes del diccionario cuando el idioma esperado es es/en;
- el usuario fuerza `--ocr always`.

El último caso cubre fuentes sin tabla ToUnicode: el texto *se ve* bien en el visor pero
se extrae como símbolos sin sentido. Es más común en PDFs antiguos y de editoriales
pequeñas.

Tesseract con `-l spa+eng` y renderizado a 300 dpi. La confianza media por palabra viaja
en `Provenance.confidence`.

## 9. Tablas

Es la parte menos fiable de cualquier enfoque heurístico. Estrategia:
1. Si hay líneas vectoriales que forman una rejilla, úsalas para definir celdas.
2. Si no, detecta columnas por alineación consistente de `x0`/`x1` en al menos 3 filas
   consecutivas.
3. Si ninguna de las dos funciona con confianza, emite el bloque como párrafos con el
   diagnóstico `pdf.table_not_reconstructed`. Es mejor perder la estructura de una tabla
   que inventar una falsa.

El benchmark de la Fase 5 decide si las tablas se delegan al backend `docling` por defecto.

## 10. Notas al pie

Bloques en la parte inferior de la página con `s < 0.9·S`, que empiezan con un número o
símbolo coincidente con un superíndice del cuerpo de la misma página. Emparéjalos y
genera `FootnoteRef`/`FootnoteDef`. Si no se emparejan, deja el texto como párrafo con el
diagnóstico `pdf.footnote_unmatched`.

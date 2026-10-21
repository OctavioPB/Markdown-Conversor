# Especificación de la salida Markdown

Contrato del Writer. Cada regla numerada debe tener al menos un test en
`tests/unit/writers/` cuyo nombre incluya su identificador (p. ej. `test_M05_...`).

## Documento

- **M01** — Codificación UTF-8, saltos de línea `\n` y exactamente un `\n` final.
- **M02** — Front matter YAML entre `---` al inicio, con claves en el orden fijo de ADR-010.
  Las claves sin valor se omiten (no se escribe `null`).
- **M03** — Una línea en blanco entre bloques, nunca dos.
- **M04** — Sin ajuste de línea: un párrafo ocupa una sola línea. Esto hace que el texto
  no dependa de un ancho arbitrario y que el troceado posterior opere sobre párrafos completos.

## Títulos

- **M05** — Solo estilo ATX (`#`). Nunca subrayados Setext.
- **M06** — Exactamente un H1, igual al `title` del front matter.
- **M07** — Sin saltos de nivel (los garantiza el transform `normalize_headings`).
- **M08** — Sin marcas de énfasis dentro de un título; se aplanan a texto.

## Inline

- **M09** — Énfasis `*texto*`, fuerte `**texto**`, código `` `x` `` (se amplían las
  comillas invertidas si el contenido las contiene).
- **M10** — Escapado mínimo y correcto. Se escapa solo lo que cambiaría el render en su
  posición (`*`, `_`, `` ` ``, `[`, `]`, `<`, `\`, `#`/`-`/`+`/`1.` al inicio de línea,
  `|` dentro de tablas, `$` cuando no es matemática). Propiedad testeada con hypothesis:
  `render(escape(s)) == s` para texto arbitrario.
- **M11** — Enlaces en línea `[texto](url "título")`. Las URLs con espacios o paréntesis
  van entre `<>`.
- **M12** — Matemáticas inline `$tex$`, sin espacios junto a los delimitadores.

## Bloques

- **M13** — Listas con `-`; las ordenadas con `1.` respetando `start`; la indentación del
  hijo se alinea con el contenido del padre.
- **M14** — Código cercado con ``` y lenguaje si se conoce. Se amplía el cercado si el
  contenido contiene ```.
- **M15** — Matemáticas de bloque:
  ```
  $$
  tex
  $$
  ```
- **M16** — Citas con `> ` en cada línea, incluidas las líneas en blanco internas (`>`).

## Tablas

- **M17** — Tabla GFM solo si: no hay spans, todas las celdas son inline y hay una fila
  de cabecera. Si falta la cabecera, la primera fila se promueve y se emite diagnóstico.
- **M18** — En cualquier otro caso, HTML embebido mínimo (`table/tr/th/td` con
  `rowspan`/`colspan`, sin estilos) y diagnóstico `table.html_fallback`.
- **M19** — El pie de tabla va como párrafo en cursiva justo antes de la tabla.

## Imágenes y notas

- **M20** — `![alt](<slug>.assets/<hash>.<ext>)`; el pie va como párrafo en cursiva
  después de la imagen.
- **M21** — Notas `[^n]` numeradas 1..n. Las definiciones se agrupan al final del
  documento, en orden numérico.

## Determinismo

- **M22** — La misma IR produce bytes idénticos. Se prohíbe iterar sobre `set` o depender
  del orden de inserción de dicts construidos a partir de fuentes no ordenadas.

## Libros divididos por capítulos (ADR-018)

- **M23** — Un libro dividido es una carpeta `<slug-del-libro>/` con `index.md`, un
  `NN-<slug-del-capítulo>.md` por capítulo y `assets/` compartido. `NN` tiene dos cifras
  (tres si hay más de 99 capítulos) y sigue el orden de lectura; `00-` es el texto anterior
  al capítulo 1, cuando es lo bastante largo para un archivo propio.
- **M24** — Cada archivo de capítulo cumple M01–M22 por sí mismo: un único H1, igual a su
  `title` (el título del capítulo, cuyas secciones suben un nivel); notas `[^n]` numeradas
  1..n dentro del capítulo (una nota agrupada al final del libro va al capítulo que la
  cita); imágenes en `assets/<hash>.<ext>`.
- **M25** — El front matter de un capítulo añade, justo después de `title`, `book` (título
  del libro), `chapter` (número; se omite en `00-`) y `chapters` (total). El de `index.md`
  lleva `title` (el del libro) y `chapters`. El resto de claves, como en ADR-010.
- **M26** — `index.md` tiene el título del libro como H1, el texto breve anterior al
  capítulo 1 si lo hay, y una lista con un enlace a cada archivo de capítulo, en orden.

## Ejemplo de front matter

```yaml
---
title: "Teorema de Bayes"
source_type: wikipedia
source: "https://es.wikipedia.org/wiki/Teorema_de_Bayes"
source_sha256: "9f2c…"
converter_version: "0.3.0"
backend: parsoid
language: es
license: "CC BY-SA 4.0"
revision_id: 158234411
diagnostics_count: 2
---
```

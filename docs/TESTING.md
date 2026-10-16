# Estrategia de pruebas

## Tres niveles

| Nivel | Qué valida | Dónde | Velocidad |
|---|---|---|---|
| Unitario | Reglas del Writer (M01–M22), transforms, heurísticas aisladas | `tests/unit/` | segundos |
| Corpus dorado | Conversión de extremo a extremo contra un `expected.md` humano | `tests/corpus/` | minutos |
| Corpus privado | Robustez y rendimiento sobre PDFs reales del usuario | fuera del repo | horas |

No existe un "round-trip" posible (Markdown→PDF→Markdown no es identidad), así que la
verdad de referencia tiene que ser humana. Eso convierte la curación del corpus en el
recurso más caro del proyecto: elige casos que representen **un fenómeno cada uno**.

## Estructura de un caso

```
tests/corpus/pdf/two-column-arxiv-01/
  input.pdf
  expected.md
  case.yaml
```

```yaml
# case.yaml
source: "arXiv:2106.09685 (CC BY 4.0)"
phenomena: [two_column, footnotes, inline_math_lost]
backend: heuristic            # opcional; por defecto el del lector
known_limits:                  # pérdidas aceptadas y documentadas
  - "Ecuaciones de la sección 3 salen como texto plano"
thresholds:                    # opcional; sustituye los de la fase
  text_similarity: 0.93
```

Los PDF escaneados viven en `tests/corpus/pdf-ocr/` (umbral propio: similitud ≥ 0.90).
Necesitan Tesseract (ADR-017): sus casos llevan la marca `slow` y se saltan si el motor no
está instalado. Los cinco actuales los genera `scripts/make_synthetic_pdfs.py` rasterizando
un PDF digital y degradándolo de forma determinista (semilla fija).

`phenomena` permite agrupar resultados ("¿cómo vamos en `two_column`?"), lo que es más
informativo que el promedio global.

## Métricas (`scripts/eval_corpus.py`)

Antes de comparar, ambos textos se normalizan: NFKC, espacios colapsados y front matter
eliminado.

- **Similitud de texto:** `1 − distancia_Levenshtein / max(len)` a nivel de carácter
  (rapidfuzz). Mide pérdida o corrupción de contenido.
- **F1 de títulos:** se emparejan títulos por (nivel, texto normalizado). Mide la
  recuperación de estructura.
- **Orden de lectura:** τ de Kendall entre el orden de los párrafos emparejados (por
  similitud ≥ 0.9) en salida y esperado. Solo PDF. Detecta columnas mezcladas, que la
  similitud de texto apenas penaliza.
- **Exactitud de celdas:** proporción de celdas iguales en tablas emparejadas.
- **Diagnósticos:** número y códigos. Una mejora de métricas que dispara diagnósticos
  `error` merece revisión.

El informe muestra una fila por caso con el delta respecto al último baseline guardado en
`tests/corpus/.baseline.json`. El baseline solo se actualiza con
`eval_corpus.py --update-baseline` y con aprobación del usuario.

## Por qué la similitud de texto no basta

Una salida con las dos columnas de un PDF intercaladas línea a línea conserva casi todos
los caracteres: su similitud puede superar 0.9 y ser ilegible. Por eso el orden de lectura
se mide aparte. Del mismo modo, una salida sin ningún título puede tener similitud alta, y
es el F1 de títulos el que lo delata. Cada métrica cubre un modo de fallo que las demás no ven.

## Fuentes del corpus (licencias)

- EPUB: Project Gutenberg, Standard Ebooks (dominio público).
- Wikipedia: CC BY-SA; se registra `revision_id` en `case.yaml`.
- PDF: arXiv con licencia CC BY, documentación pública, documentos generados ad hoc para
  fenómenos concretos (por ejemplo, con LaTeX o LibreOffice).

## Corpus privado

Los PDFs personales nunca entran al repositorio. Viven en `~/tomd-private-corpus/`, con
un `sample.txt` que lista una muestra estratificada (por época, editor y escaneado o no).
Se usa para:

1. `tomd batch` de humo en cada fase desde la 4 (¿cuántos fallan? ¿qué diagnósticos dominan?).
2. Promover casos al corpus público: cuando un fenómeno aparece a menudo en el privado, se
   busca o se fabrica un equivalente con licencia libre.

## Fixtures de red

El lector de Wikipedia se prueba con respuestas grabadas (`tests/fixtures/wiki/*.html` y
sus cabeceras). Un fixture de pytest bloquea cualquier socket en tests, de modo que una
llamada accidental a la red falla en lugar de pasar en silencio.

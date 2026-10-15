# CLAUDE.md — tomd

Conversor local de **PDF, EPUB y artículos de Wikipedia a Markdown**. Su destino principal
es alimentar una biblioteca Markdown que consume un sistema de IA local (búsqueda, tutoría,
redacción), así que la salida debe ser **estable, determinista y semánticamente limpia**,
no solo "legible".

Lee este archivo completo al empezar cada sesión. Es corto a propósito; el detalle vive en `docs/`.

## Antes de tocar código

1. Lee `PLAN.md` y localiza la fase activa (la primera con tareas sin marcar).
2. Si la tarea toca arquitectura, IR o formato de salida, lee `docs/ARCHITECTURE.md` y
   `docs/MARKDOWN_SPEC.md`.
3. Revisa `DECISIONS.md`. No contradigas una decisión con estado *Aceptada* sin proponer
   antes un ADR nuevo que la sustituya (skill `record-decision`).
4. Para tareas no triviales, propón un plan breve y espera confirmación antes de implementar.

## Comandos

```bash
uv sync                                   # instalar dependencias
uv run pytest                             # tests unitarios + corpus
uv run pytest -m "not slow"               # sin OCR ni backends pesados
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run tomd convert <entrada> -o out/     # convertir un archivo o URL
uv run tomd batch <carpeta> -o out/       # lote reanudable (Fase 7)
uv run python scripts/eval_corpus.py      # métricas sobre el corpus dorado
```

Ejecuta `ruff`, `mypy` y `pytest -m "not slow"` antes de dar una tarea por terminada.

## Estructura

```
src/tomd/
  ir/            # Representación intermedia (nodos, diagnósticos, procedencia)
  readers/       # Un paquete por fuente: epub/, wikipedia/, pdf/
  transforms/    # Pasadas IR→IR (normalizar títulos, renumerar notas, etc.)
  writers/       # Serializador IR→Markdown (único punto que emite Markdown)
  pipeline/      # Despacho por tipo de fuente, lotes, manifiesto
  cli.py
tests/
  unit/
  corpus/<fuente>/<caso>/{input.*, expected.md, case.yaml}
docs/            # ARCHITECTURE, MARKDOWN_SPEC, TESTING
.claude/skills/  # Procedimientos reutilizables para Claude Code
```

## Invariantes (no negociables)

- **Los lectores nunca emiten Markdown.** Producen IR. Solo `writers/markdown.py` genera
  texto Markdown, escapa caracteres y decide sintaxis. Si un lector necesita "algo que
  Markdown no tiene", amplía la IR o emite un diagnóstico; no metas cadenas Markdown en nodos.
- **Toda pérdida de información emite un `Diagnostic`** (tabla aplanada, fórmula
  irrecuperable, imagen omitida, orden de lectura dudoso). Una pérdida silenciosa es un bug.
- **Determinismo:** la misma entrada y la misma versión producen bytes idénticos. Nada de
  marcas de tiempo, orden de `dict`/`set` no controlado ni rutas absolutas en la salida.
  La fecha de conversión va al manifiesto, no al front matter.
- **100 % local.** Ninguna llamada a APIs de modelos externos. La única red permitida es
  la API de Wikimedia en el lector de Wikipedia, y nunca en tests (usa fixtures grabadas).
- **No se manejan archivos con DRM.** Si se detecta DRM, falla con un error claro.
- **Presupuesto de memoria:** el proceso de conversión no debe superar ~4 GB de RSS por
  worker, porque comparte máquina con el modelo local (menos de 8 GB disponibles en total).

## Corpus dorado

- `expected.md` es la verdad de referencia hecha a mano. **Nunca regeneres ni edites un
  `expected.md` para que pase un test** sin aprobación explícita del usuario. Si crees que
  el esperado está mal, dilo y justifícalo.
- Cualquier cambio en lectores o heurísticas exige ejecutar `scripts/eval_corpus.py` y
  reportar mejoras y regresiones por caso, no solo el promedio.
- Usa la skill `golden-corpus` para añadir casos o interpretar regresiones.

## Estilo de código

- Python 3.12, tipado estricto (`mypy --strict` en `src/`), dataclasses `frozen` para la IR.
- Funciones puras en transforms y heurísticas; I/O solo en readers, writers y pipeline.
- Las heurísticas PDF llevan sus umbrales como parámetros con nombre (no números mágicos)
  agrupados en una dataclass de configuración, con un comentario de por qué ese valor.
- Identificadores y docstrings en inglés; documentación del proyecto en español.
- Mensajes de commit: Conventional Commits (`feat(pdf): detect two-column layout`).

## Skills del proyecto

| Skill | Cuándo |
|---|---|
| `add-source-reader` | Crear o reestructurar un lector de fuente |
| `golden-corpus` | Añadir casos, evaluar, interpretar regresiones |
| `pdf-layout-heuristics` | Cualquier trabajo en `readers/pdf/` |
| `record-decision` | Toda decisión de arquitectura, dependencia o formato |

## Qué NO hacer

- No añadas dependencias sin registrar el motivo y la licencia en un ADR (ojo: PyMuPDF es AGPL).
- No "arregles" un caso del corpus con un `if` específico para ese documento. Si una
  heurística necesita una excepción, generalízala o documenta el límite.
- No subas al repositorio PDFs personales del usuario; el corpus privado vive en
  `~/tomd-private-corpus/` (fuera del repo, ver `docs/TESTING.md`).

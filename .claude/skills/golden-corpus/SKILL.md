---
name: golden-corpus
description: Cómo añadir casos al corpus dorado de tomd, ejecutar scripts/eval_corpus.py e interpretar mejoras y regresiones por caso y por fenómeno. Úsala siempre que se añada o modifique un caso en tests/corpus/, tras cualquier cambio en lectores, transforms, writer o heurísticas, cuando un test de corpus falle, o cuando el usuario pregunte "¿mejoró?", "¿qué rompí?" o por la calidad de la conversión.
---

# Corpus dorado

El corpus es la definición operativa de "funciona". Las métricas están explicadas en
`docs/TESTING.md`; esta skill cubre el procedimiento.

## Regla de oro

`expected.md` lo escribe o aprueba un humano. **No lo regeneres a partir de la salida del
conversor ni lo edites para que un test pase.** Hacerlo convierte el test en una
tautología y oculta exactamente los errores que el corpus debía detectar. Si crees que un
esperado está mal, explícalo con evidencia (qué dice la fuente en esa página o sección) y
deja que el usuario decida.

## Añadir un caso

1. Elige **un fenómeno principal** por caso (dos columnas, notas entre archivos, tabla con
   spans, escaneado limpio…). Si un documento tiene muchos fenómenos, recorta páginas.
2. Verifica la licencia (ver `docs/TESTING.md`). Nada del corpus privado del usuario.
3. Crea `tests/corpus/<fuente>/<slug>/` con `input.*` y `case.yaml` (`source`,
   `phenomena`, `known_limits`).
4. Para el `expected.md` propón un **borrador** a partir de la fuente (no de la salida del
   conversor) y márcalo con `# DRAFT — requiere revisión humana` en la primera línea.
   `eval_corpus.py` excluye de las métricas los casos marcados como DRAFT.
5. Pide al usuario que revise y quite la marca.

## Evaluar un cambio

```bash
uv run python scripts/eval_corpus.py                  # contra el baseline
uv run python scripts/eval_corpus.py --phenomenon two_column
uv run python scripts/eval_corpus.py --case pdf/two-column-arxiv-01 --diff
```

Informe al usuario, en este orden:
1. **Regresiones** (cualquier métrica que baje más de 0.01 en algún caso), con su causa
   probable y un fragmento del diff.
2. Mejoras por fenómeno.
3. Promedio global (al final; es la cifra menos informativa).
4. Diagnósticos nuevos o desaparecidos.

Una mejora global con una regresión local no es automáticamente aceptable. Explica el
compromiso y deja la decisión al usuario.

## Interpretar patrones

| Síntoma | Causa típica |
|---|---|
| Similitud alta, τ de Kendall baja | Columnas intercaladas u orden de bloques erróneo |
| Similitud baja de forma uniforme | Extracción de texto corrupta (ToUnicode) → candidato a OCR |
| F1 de títulos baja, similitud alta | Umbral de tamaño de fuente mal calibrado para ese documento |
| Texto repetido al principio o al final de cada sección | Cabeceras o pies no eliminados |
| Palabras partidas o pegadas | Reparación de guiones o espaciado entre glifos |

## Actualizar el baseline

Solo con `--update-baseline` y tras confirmación explícita del usuario. Incluye en el
commit el informe que justifica el nuevo baseline.

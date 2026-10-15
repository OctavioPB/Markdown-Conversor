---
name: record-decision
description: Cómo proponer y registrar una decisión de arquitectura (ADR) en DECISIONS.md de tomd. Úsala siempre que se añada, cambie o elimine una dependencia, se modifique la IR o el dialecto de salida, se elija entre alternativas técnicas (backend, librería, algoritmo), se calibren umbrales de fase, o se quiera hacer algo que contradice un ADR existente, aunque parezca una decisión menor.
---

# Registrar una decisión

`DECISIONS.md` explica *por qué* el proyecto es como es. Sin ese registro, cada sesión
nueva de Claude Code puede reabrir debates ya cerrados o deshacer compromisos sin saberlo.

## Cuándo hace falta un ADR

Hace falta para: dependencias nuevas (incluida su licencia), cambios en la IR, cambios en
`MARKDOWN_SPEC.md`, elección de backend o algoritmo, umbrales de calidad de una fase,
cambios en el formato del manifiesto o del front matter.

No hace falta para: refactors internos sin efecto observable ni ajustes de una heurística
dentro de `PdfHeuristicsConfig` (estos se justifican en el comentario del parámetro y en
el commit).

## Procedimiento

1. Lee `DECISIONS.md` completo y localiza los ADR relacionados.
2. Si la decisión contradice un ADR *Aceptado*, no lo edites: el nuevo ADR lo sustituye,
   y al antiguo solo se le cambia el estado a `Sustituida por ADR-NNN`.
3. Redacta con el formato de abajo, con estado **Propuesta**.
4. Presenta al usuario la decisión, las alternativas y la consecuencia más incómoda.
5. Solo tras su confirmación, pasa el estado a **Aceptada** e implementa.

## Formato

```markdown
## ADR-NNN — Título en forma de decisión, no de pregunta
**Estado:** Propuesta
**Contexto:** Qué fuerza la decisión. Restricciones reales (memoria, licencia, volumen,
requisito de localidad). Datos si existen (benchmark, métricas del corpus).
**Decisión:** Qué se hace, en una o dos frases.
**Alternativas consideradas:** Cada una con el motivo concreto del descarte.
**Consecuencias:** Lo que se gana y, sobre todo, lo que se pierde o queda más difícil.
```

## Criterios de calidad

- Las alternativas son reales y su descarte tiene un motivo verificable ("consume 6 GB en
  el benchmark"), no estético ("es más limpio").
- Las consecuencias incluyen al menos un coste.
- Si la decisión se basa en un hecho externo que puede cambiar (un endpoint, una
  licencia, una versión), indícalo y la fecha en que se verificó.

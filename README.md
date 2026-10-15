# tomd

Conversor local de PDF, EPUB y artículos de Wikipedia a Markdown limpio y determinista,
pensado para alimentar una biblioteca consumida por un sistema de IA local.

## Documentación del proyecto

| Archivo | Para qué |
|---|---|
| `CLAUDE.md` | Contexto e invariantes que Claude Code lee en cada sesión |
| `PLAN.md` | Fases, tareas y criterios de terminado |
| `DECISIONS.md` | Registro de decisiones de arquitectura (ADR) |
| `docs/ARCHITECTURE.md` | Pipeline, IR, lectores, lotes |
| `docs/MARKDOWN_SPEC.md` | Contrato exacto del Markdown de salida (reglas M01–M22) |
| `docs/TESTING.md` | Corpus dorado, métricas, corpus privado |
| `.claude/skills/` | Procedimientos reutilizables para Claude Code |
| `.claude/settings.json` | Permisos: qué puede hacer Claude Code sin preguntar |

## Cómo empezar con Claude Code

```bash
cd tomd && git init
claude
> Lee CLAUDE.md y PLAN.md y propón el plan de implementación de la Fase 0.
```

Trabaja fase por fase. Al cerrar cada una, pide a Claude Code que marque las tareas en
`PLAN.md` y registre en `DECISIONS.md` cualquier decisión tomada por el camino.

"""Generate docs/DIAGNOSTICS.md from the diagnostic catalog (src/tomd/ir/diagnostics.py).

uv run python scripts/gen_diagnostics_doc.py            # rewrite the document
uv run python scripts/gen_diagnostics_doc.py --check    # exit 1 if it is out of date
"""

import argparse
import sys
from pathlib import Path

from tomd.ir.diagnostics import CATALOG

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "DIAGNOSTICS.md"
_ORDER = {"error": 0, "warning": 1, "info": 2}

HEADER = """# Diagnósticos

<!-- Generado por scripts/gen_diagnostics_doc.py; no editar a mano. -->

Toda pérdida de información o inferencia dudosa genera un diagnóstico (CLAUDE.md). El
número total va en el front matter (`diagnostics_count`); `tomd convert` imprime cada
código con su recuento y, si hay algo que hacer, la acción recomendada.

| Severidad | Significado |
|---|---|
| `error` | La salida no es un documento utilizable tal como se pidió. |
| `warning` | Se perdió contenido o probablemente es incorrecto: conviene revisarlo. |
| `info` | Transformación deliberada o inferencia que conviene conocer; nada que corregir. |

Los textos de cada código están en inglés, como los mensajes del programa.

"""


def render() -> str:
    rows = ["| Código | Severidad | Qué significa | Qué hacer |", "|---|---|---|---|"]
    for code, info in sorted(CATALOG.items(), key=lambda kv: (_ORDER[kv[1].severity], kv[0])):
        action = info.action.replace("|", "\\|") or "—"
        rows.append(
            f"| `{code}` | {info.severity} | {info.meaning.replace('|', '\\|')} | {action} |"
        )
    return HEADER + "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = render()
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    if args.check:
        if current != text:
            print(f"{TARGET.relative_to(ROOT)} is out of date; run this script.", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

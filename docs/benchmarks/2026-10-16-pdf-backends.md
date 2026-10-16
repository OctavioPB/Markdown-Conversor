# Benchmark de backends PDF

Cada conversión corre en un proceso nuevo: el tiempo incluye el arranque y, para
docling, la carga de modelos. La memoria es el pico del proceso.

| Caso | Backend | Similitud | F1 títulos | Orden | s/página | Pico MB |
|---|---|---|---|---|---|---|
| pdf/synthetic-headings | heuristic | 1.000 | 1.000 | 1.000 | 0.40 | 42 |
| pdf/synthetic-headings | docling | 1.000 | 1.000 | 1.000 | 12.89 | 1057 |
| pdf/synthetic-hyphenation | heuristic | 1.000 | 1.000 | — | 0.37 | 41 |
| pdf/synthetic-hyphenation | docling | 0.995 | 1.000 | — | 13.01 | 1056 |
| pdf/synthetic-lists | heuristic | 1.000 | 1.000 | 1.000 | 0.37 | 41 |
| pdf/synthetic-lists | docling | 1.000 | 1.000 | 1.000 | 13.04 | 1056 |
| pdf/synthetic-page-break | heuristic | 1.000 | 1.000 | 1.000 | 0.22 | 42 |
| pdf/synthetic-page-break | docling | 1.000 | 1.000 | — | 6.78 | 1056 |
| pdf/synthetic-styles-code | heuristic | 1.000 | 1.000 | 1.000 | 0.38 | 42 |
| pdf/synthetic-styles-code | docling | 0.971 | 1.000 | 1.000 | 12.96 | 1057 |
| pdf/synthetic-two-columns | heuristic | 1.000 | 1.000 | 1.000 | 0.14 | 44 |
| pdf/synthetic-two-columns | docling | 1.000 | 1.000 | 1.000 | 3.87 | 1456 |

## Promedios por backend

- heuristic: similitud 1.000, 0.25 s/página, pico 44 MB
- docling: similitud 0.994, 8.09 s/página, pico 1456 MB

## PDFs reales (sin `expected.md` revisado: solo tiempo y memoria)

Medido el 2026-10-16 en la máquina de desarrollo (Windows 11, CPU), un proceso por
conversión. docling 2.x con `do_ocr=False` y `generate_picture_images=True`.

| PDF | Páginas | Backend | s/página | Total s | Pico MB |
|---|---|---|---|---|---|
| arXiv 2310.06825 (Mistral 7B, CC BY 4.0) | 9 | heuristic | 0.27 | 2.4 | 500 |
| arXiv 2310.06825 (Mistral 7B, CC BY 4.0) | 9 | docling | 2.76 | 24.8 | 2075 |
| arXiv 2501.14108 (math.AP, CC BY 4.0) | 22 | heuristic | 0.07 | 1.5 | 50 |
| arXiv 2501.14108 (math.AP, CC BY 4.0) | 22 | docling | 1.39 | 30.5 | 1785 |
| Rev. Complutense de Educación 1997 (CC BY 4.0) | 19 | heuristic | 0.09 | 1.8 | 60 |
| Rev. Complutense de Educación 1997 (CC BY 4.0) | 19 | docling | 1.43 | 27.1 | 2111 |

El pico de 500 MB del backend heurístico en Mistral 7B viene de decodificar figuras
grandes a PNG; el resto de documentos se queda en 50–60 MB.

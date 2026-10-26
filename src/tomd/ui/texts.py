"""What the desktop UI says about each diagnostic, in plain Spanish.

The catalog (`tomd.ir.diagnostics`) speaks to developers and the command line; these texts
speak to someone reviewing a document: a short title (with the page) and the question to
answer or the thing to do. Codes with severity `info` are automatic, lossless changes,
listed apart as such. A test requires a text for every catalog code.
"""

from dataclasses import dataclass

from tomd.ir import diagnostics as codes


@dataclass(frozen=True, slots=True)
class Text:
    title: str  # short, for the list and the checklist; "{page}" becomes " · pág. N"
    hint: str  # the question to answer, or what to do


REVIEW: dict[str, Text] = {
    codes.DOC_TITLE_MISSING: Text(
        "Sin título", "No se encontró el título; el archivo se llama como el original."
    ),
    codes.TEXT_INVALID_UNICODE: Text(
        "Caracteres dañados", "Algunos caracteres no se pudieron leer y salen como «�». ¿Se entiende el texto?"
    ),
    codes.HEADING_LEVEL_CLAMPED: Text(
        "Subtítulos muy anidados", "Algunos subtítulos muy profundos quedaron al mismo nivel. ¿Se entiende la estructura?"
    ),
    codes.MATH_UNRECOVERABLE: Text(
        "Fórmula sin recuperar{page}", "Una fórmula quedó como texto o falta. Compárala con el original."
    ),
    codes.IMAGE_DROPPED: Text(
        "Imagen que falta{page}", "Una imagen no se pudo guardar. ¿Era importante para entender el texto?"
    ),
    codes.FOOTNOTE_ORPHAN_DEFINITION: Text(
        "Nota sin llamada", "Una nota no se citaba en el texto y se quitó. ¿Faltaba su llamada?"
    ),
    codes.FOOTNOTE_MISSING_DEFINITION: Text(
        "Llamada sin nota", "Una llamada a nota no tenía su texto y se quitó."
    ),
    codes.FOOTNOTE_DUPLICATE_DEFINITION: Text(
        "Nota repetida", "Una nota aparecía dos veces; se dejó la primera."
    ),
    codes.EPUB_FIXED_LAYOUT: Text(
        "Libro de páginas fijas", "Está pensado como páginas (cómic, álbum). ¿El texto tiene sentido sin el diseño?"
    ),
    codes.EPUB_MISSING_RESOURCE: Text(
        "Parte del libro dañada", "Un capítulo no se pudo leer y falta. Consigue una copia nueva del libro."
    ),
    codes.PDF_PAGE_UNREADABLE: Text(
        "Página ilegible{page}", "Esta página no se pudo leer y falta en el resultado."
    ),
    codes.PDF_NO_TEXT_LAYER: Text(
        "Página escaneada sin texto{page}", "Es una imagen. Activa la lectura de escaneos en Opciones y vuelve a convertir."
    ),
    codes.PDF_GARBAGE_TEXT: Text(
        "Texto ilegible{page}", "El texto salió como símbolos raros. Prueba «Volver a convertir con OCR»."
    ),
    codes.PDF_OCR_LOW_CONFIDENCE: Text(
        "Escaneo de baja calidad{page}", "Puede haber palabras mal leídas. Compara con el original."
    ),
    codes.PDF_OCR_UNAVAILABLE: Text(
        "Falta el lector de escaneos{page}", "Esta página necesita OCR, que no está disponible en este equipo."
    ),
    codes.PDF_READING_ORDER_UNCERTAIN: Text(
        "Orden de las columnas{page}", "¿Los párrafos siguen el orden del original?"
    ),
    codes.PDF_TABLE_NOT_RECONSTRUCTED: Text(
        "Tabla sin reconstruir{page}", "Parece una tabla, pero quedó como texto. ¿Se entienden los datos sin las columnas?"
    ),
}  # fmt: skip

AUTOMATIC: dict[str, str] = {
    codes.TABLE_HTML_FALLBACK: "Una tabla compleja se guardó como tabla HTML",
    codes.TABLE_HEADER_PROMOTED: "La primera fila de una tabla pasó a ser su encabezado",
    codes.TABLE_EMPTY_DROPPED: "Se quitó una tabla vacía",
    codes.IMAGE_SKIPPED_SMALL: "Se omitieron imágenes decorativas pequeñas",
    codes.HTML_UNKNOWN_ELEMENT: "Se conservó el texto de elementos poco comunes",
    codes.HTML_DEFINITION_LIST_FLATTENED: "Las listas de definiciones pasaron a párrafos",
    codes.WIKI_INFOBOX_FLATTENED: "La ficha del artículo pasó a una tabla",
    codes.WIKI_TEMPLATE_DROPPED: "Se quitaron menús y avisos de Wikipedia",
    codes.EPUB_HEADING_INFERRED: "Se dedujeron títulos por el índice o el estilo",
    codes.EPUB_INTERNAL_LINK_DROPPED: "Los enlaces entre partes del libro quedaron como texto",
    codes.EPUB_COVER_SKIPPED: "Se omitió la página de portada",
    codes.EPUB_TOC_SKIPPED: "Se omitió el índice (lo sustituyen los títulos)",
    codes.EPUB_BOILERPLATE_DROPPED: "Se quitó la cabecera y la licencia del distribuidor",
    codes.EPUB_PAGE_MARKERS_DROPPED: "Se quitaron las marcas de página de la edición impresa",
    codes.EPUB_NOTES_HEADING_DROPPED: "La sección de notas pasó a notas al pie",
    codes.PDF_PAGE_EMPTY: "Una página no tenía contenido",
    codes.PDF_PERMISSIONS_RESTRICTED: "El PDF tiene restricciones de copia (no impiden convertirlo)",
    codes.PDF_STRUCT_TREE_IGNORED: "La estructura se dedujo de la página, no de las etiquetas del PDF",
    codes.PDF_OCR_APPLIED: "El texto se leyó del escaneo (OCR)",
    codes.PDF_LOW_CONFIDENCE: "Algún título o unión de palabras es una deducción",
    codes.PDF_ROTATED_TEXT_DROPPED: "Se quitó texto girado de los márgenes",
    codes.PDF_FURNITURE_REMOVED: "Se quitaron encabezados, pies y números de página repetidos",
    codes.PDF_FOOTNOTE_UNMATCHED: "Un texto con aspecto de nota se dejó en su sitio",
    codes.DOC_SECTION_DROPPED: "Se omitió una sección que no es contenido (modo RAG)",
    codes.DOC_SPLIT_SKIPPED: "No se dividió por capítulos: no se encontraron capítulos fiables",
}


def review_title(code: str, page: int | None) -> str:
    text = REVIEW.get(code)
    title = text.title if text else code
    return title.replace("{page}", f" · pág. {page}" if page else "")


def review_hint(code: str) -> str:
    text = REVIEW.get(code)
    return text.hint if text else ""


def automatic_text(code: str) -> str:
    return AUTOMATIC.get(code, code)


def failure_text(error: str | None) -> str:
    """Why a document was not converted, from the manifest's error, in plain words."""
    error = error or ""
    if error.startswith("DrmProtectedError"):
        return "Está protegido con DRM: no se puede convertir"
    if error.startswith("UnsupportedSourceError"):
        return "Este tipo de archivo no se puede convertir"
    if error.startswith("UnreadableInputError"):
        return "El archivo está dañado o incompleto: descárgalo o expórtalo de nuevo"
    if error.startswith("No result after"):
        return "Tardó demasiado en convertirse"
    if "Memory" in error or "memory" in error:
        return "Necesita más memoria de la permitida"
    return "No se pudo convertir (fallo interno)"

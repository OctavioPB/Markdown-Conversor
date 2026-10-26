# Diagnósticos

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

| Código | Severidad | Qué significa | Qué hacer |
|---|---|---|---|
| `doc.title_missing` | error | The document has no title in its metadata and no top-level heading; the output file is named after the source file. | Set a title in the source (PDF document properties) or check that the first heading was recognised. |
| `epub.fixed_layout` | warning | The EPUB has a fixed layout (pre-paginated: comics, picture books, magazines); its text is converted, but its meaning may depend on the page design. | Check the output against the book; a PDF edition may convert better. |
| `epub.missing_resource` | warning | A content document listed in the spine is missing from the EPUB or damaged; its text is missing. The rest of the book was converted. | The EPUB is damaged: obtain a fresh copy. |
| `footnote.duplicate_definition` | warning | A footnote is defined more than once; the first definition was kept. | — |
| `footnote.missing_definition` | warning | A footnote reference has no definition; the reference was dropped. | — |
| `footnote.orphan_definition` | warning | A footnote definition is never referenced in the text; it was dropped. | — |
| `heading.level_clamped` | warning | A heading is nested deeper than H6, which Markdown cannot express; it was set to H6. | — |
| `image.dropped` | warning | An image is missing from the output: unreadable, too large to decode safely, or not downloaded (Wikipedia). Its caption, if any, is kept. | — |
| `math.unrecoverable` | warning | A formula had no recoverable LaTeX; its plain-text rendering (or nothing) is in the output. | Check the formula against the source. |
| `pdf.garbage_text` | warning | A page's text layer is unreadable (broken font encoding); the extracted text is mostly garbage. | Convert with `--ocr always` to read the page image instead. |
| `pdf.no_text_layer` | warning | A page is a scan without a text layer; without OCR its text is missing. | Convert with `--ocr auto` (the default) and Tesseract installed. Install Tesseract and the spa/eng language data (README, section OCR), or point TOMD_TESSERACT and TOMD_TESSDATA at them, then convert again. |
| `pdf.ocr_low_confidence` | warning | OCR confidence on a page is low (poor scan quality): expect wrong words. | Proofread the page, or rescan it at 300 dpi or more. |
| `pdf.ocr_unavailable` | warning | A page needs OCR but Tesseract or its language data was not found; the page's text is missing or garbage. | Install Tesseract and the spa/eng language data (README, section OCR), or point TOMD_TESSERACT and TOMD_TESSDATA at them, then convert again. |
| `pdf.page_unreadable` | warning | A page could not be loaded (damaged page object); its content is missing. | Re-export the PDF from its source, or repair it (`qpdf in.pdf out.pdf`), and convert again. |
| `pdf.reading_order_uncertain` | warning | The column layout of a page is ambiguous; paragraphs may be out of order. | Check the paragraph order of that page against the source. |
| `pdf.table_not_reconstructed` | warning | Something on the page looks like a table (a table caption, or rows aligned in columns) but it was not rebuilt with confidence; its content stays as paragraphs and the row and column structure is lost. | Compare that page with the source. If the table matters, convert with `--backend docling` or copy it by hand. |
| `text.invalid_unicode` | warning | The source contains invalid characters (broken UTF-8, lone surrogates); each was replaced by U+FFFD. | The source file is damaged: obtain a fresh copy (fetch the article again). |
| `doc.section_dropped` | info | In RAG mode (ADR-019), a section that is not the book's content (cover, copyright, about the author, acknowledgments, contents, index, bibliography...) was left out on purpose. | — |
| `doc.split_skipped` | info | The book was not split by chapters: no reliable chapter boundaries were found (no usable bookmarks or table of contents, and no recognisable chapter headings). The whole book is one file; nothing is lost. | — |
| `epub.boilerplate_dropped` | info | Distributor boilerplate (e.g. the Project Gutenberg header and license) was removed. | — |
| `epub.cover_skipped` | info | The cover page (only the cover image) was left out of the text. | — |
| `epub.heading_inferred` | info | Headings were inferred from the table of contents or from CSS class names, because the book does not mark them with <h1>-<h6>. | — |
| `epub.internal_link_dropped` | info | Links between parts of the book point to places that do not exist in a single Markdown file; their text was kept, the link removed. | — |
| `epub.notes_heading_dropped` | info | A notes section became Markdown footnotes; its now-empty heading was removed. | — |
| `epub.page_markers_dropped` | info | Print page-break markers (page numbers of the paper edition) were removed. | — |
| `epub.toc_skipped` | info | A table of contents (its own page, or typed into the text) or a list of illustrations was left out: its links lead nowhere in Markdown, and the headings carry the structure. | — |
| `html.definition_list_flattened` | info | Definition lists have no Markdown syntax; terms and definitions became paragraphs. | — |
| `html.unknown_element` | info | An HTML element tomd does not know was converted from its content (text kept, semantics lost). | — |
| `image.skipped_small` | info | Images too small to be figures (icons, logos, decorations) were skipped. | — |
| `pdf.footnote_unmatched` | info | Text that looks like a footnote has no matching call on its page; it was kept as ordinary text. | — |
| `pdf.furniture_removed` | info | Running headers, footers and page numbers repeated across pages were removed. | — |
| `pdf.low_confidence` | info | A block was classified with low confidence (a heading guessed from font size only, a guessed hyphen join). | — |
| `pdf.ocr_applied` | info | A page was read by OCR; its text may contain recognition errors. The message gives the mean confidence and any orientation, skew or resolution fix. | — |
| `pdf.page_empty` | info | A page has no content: it is blank, or its content stream is damaged and could not be decoded. | If the page shows text in a PDF viewer: Re-export the PDF from its source, or repair it (`qpdf in.pdf out.pdf`), and convert again. |
| `pdf.permissions_restricted` | info | The PDF sets permission flags (copy, print); they do not stop a local conversion. Password-protected PDFs are rejected instead (ADR-009). | — |
| `pdf.rotated_text_dropped` | info | Rotated text (margin stamps such as the arXiv identifier, rotated axis labels) was dropped. | — |
| `pdf.struct_tree_ignored` | info | The PDF is tagged, but its structure tree is not used yet; the layout was inferred from the page. | — |
| `table.empty_dropped` | info | A table without any text was dropped. | — |
| `table.header_promoted` | info | A table had no header row; its first row became the header (GFM requires one). | — |
| `table.html_fallback` | info | A table with merged cells, several header rows or block content in cells cannot be a GFM table; it is emitted as an HTML table. | — |
| `wiki.infobox_flattened` | info | The infobox became a two-column table placed after the title (ADR-006). | — |
| `wiki.template_dropped` | info | Navigation and maintenance templates (navboxes, hatnotes, edit links) were removed. | — |

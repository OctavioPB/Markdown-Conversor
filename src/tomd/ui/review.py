"""The review screen (design D2): the original and the result side by side, and below
them what to review, as a checklist whose items jump to their place in both."""

import io
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import font, ttk

import pypdfium2 as pdfium
from PIL import Image as PILImage
from PIL import ImageTk

from tomd.ir import Document
from tomd.pipeline.manifest import Finding
from tomd.ui.job import Failed, PreviewJob
from tomd.ui.session import Review, preview_runs, split_like
from tomd.ui.texts import review_hint, review_title
from tomd.ui.theme import PALETTE, STATES, Fonts

_POLL_MS = 150
# Zoom steps for the page view, relative to "fit to width".
_ZOOMS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)
_PREVIEW_IMAGE_WIDTH = 520
# Checklist rows shown before it scrolls: enough to see the work, the panes keep the room.
_CHECKLIST_ROWS = 4


class ReviewActions:
    """What the review screen asks the application to do (set by the app)."""

    back: Callable[[], None]
    open_markdown: Callable[[Review], None]
    reconvert_ocr: Callable[[Review], None]
    next_pending: Callable[[], None]
    set_checked: Callable[[Review, Finding, bool], None]
    set_reviewed: Callable[[Review, bool], None]
    preview_options: Callable[[], tuple[str, str]]  # (PDF backend, OCR mode)


class ReviewView(ttk.Frame):
    def __init__(self, parent: tk.Misc, fonts: Fonts, actions: ReviewActions) -> None:
        super().__init__(parent, padding=(24, 16, 24, 16))
        self.fonts = fonts
        self.actions = actions
        self.review: Review | None = None
        self.pdf: pdfium.PdfDocument | None = None
        self.page = 1
        self.zoom_index = _ZOOMS.index(1.0)
        self.page_label = tk.StringVar(value="—")
        self.mode = "text"
        self._preview: PreviewJob | None = None
        self._page_image: ImageTk.PhotoImage | None = None
        self._preview_images: list[ImageTk.PhotoImage] = []
        self._highlight: tuple[int, tuple[float, float, float, float]] | None = None
        self._scale = 1.0  # pixels per PDF point of the page on screen
        self._view_height = 0
        self._checks: list[tuple[Finding, tk.BooleanVar]] = []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._header()
        self._panes()
        self._checklist()

    # --- Building --------------------------------------------------------------------

    def _header(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        bar.columnconfigure(2, weight=1)
        ttk.Button(bar, text="‹  Biblioteca", command=lambda: self.actions.back()).grid(
            row=0, column=0, padx=(0, 16)
        )
        self.title_label = ttk.Label(
            bar, text="", style="Title.TLabel", font=self.fonts(15, "bold")
        )
        self.chip = ttk.Label(bar, text="")
        self.chip.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self.title_label.grid(row=0, column=2, sticky="w")
        self.md_button = ttk.Button(
            bar, text="Abrir el .md", command=lambda: self._with_review(self.actions.open_markdown)
        )
        self.md_button.grid(row=0, column=3, padx=(0, 8))
        self.ocr_button = ttk.Button(
            bar, text="Volver a convertir con OCR",
            command=lambda: self._with_review(self.actions.reconvert_ocr),
        )  # fmt: skip
        self.ocr_button.grid(row=0, column=4, padx=(0, 8))
        self.next_button = ttk.Button(
            bar, text="Siguiente por revisar  ›", style="Outline.TButton",
            command=lambda: self.actions.next_pending(),
        )  # fmt: skip
        self.next_button.grid(row=0, column=5)

    def _panes(self) -> None:
        self.panes = ttk.PanedWindow(self, orient="horizontal")
        self.panes.grid(row=1, column=0, sticky="nsew")
        self.panes.add(self._original(self.panes), weight=1)
        self.panes.add(self._result(self.panes), weight=1)

    def _pane_header(self, card: ttk.Frame, title: str) -> ttk.Frame:
        bar = ttk.Frame(card, style="Surface.TFrame", padding=(12, 8))
        bar.pack(fill="x")
        ttk.Label(bar, text=title, style="Surface.Strong.TLabel").pack(side="left")
        ttk.Separator(card).pack(fill="x")
        return bar

    def _original(self, parent: tk.Misc) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=1)
        bar = self._pane_header(card, "Original")
        self.nav = ttk.Frame(bar, style="Surface.TFrame")
        self.nav.pack(side="right")
        for text, command in (("+", lambda: self.zoom(1)), ("−", lambda: self.zoom(-1))):
            ttk.Button(self.nav, text=text, width=3, command=command).pack(side="right", padx=2)
        ttk.Button(self.nav, text="▶", width=3, command=lambda: self.show_page(self.page + 1)).pack(
            side="right", padx=(2, 14)
        )
        ttk.Label(self.nav, textvariable=self.page_label, style="Surface.Muted.TLabel", width=16,
                  anchor="center").pack(side="right")  # fmt: skip
        ttk.Button(self.nav, text="◀", width=3, command=lambda: self.show_page(self.page - 1)).pack(
            side="right", padx=2
        )
        holder = ttk.Frame(card, style="Surface.TFrame")
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, background=PALETTE.page_ground, highlightthickness=0,
                                width=200, height=200)  # fmt: skip
        ys = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        xs = ttk.Scrollbar(holder, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._render_page())
        self.canvas.bind("<Prior>", lambda _e: self.show_page(self.page - 1))
        self.canvas.bind("<Next>", lambda _e: self.show_page(self.page + 1))
        self.canvas.bind("<Button-1>", lambda _e: self.canvas.focus_set())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.about = tk.Text(holder, wrap="word", relief="flat", padx=20, pady=20,
                            font=self.fonts(11), background=PALETTE.surface,
                            foreground=PALETTE.ink, borderwidth=0)  # fmt: skip
        self.about.tag_configure("title", font=self.fonts(14, "bold"), spacing3=8)
        self.about.tag_configure("muted", foreground=PALETTE.muted)
        return card

    def _result(self, parent: tk.Misc) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=1)
        bar = self._pane_header(card, "Resultado")
        toggle = ttk.Frame(bar, style="Surface.TFrame")
        toggle.pack(side="right")
        self.part_choice = tk.StringVar()
        self.part_box = ttk.Combobox(bar, textvariable=self.part_choice, state="readonly",
                                     width=34, font=self.fonts(10))  # fmt: skip
        self.part_box.bind("<<ComboboxSelected>>", lambda _e: self._part_chosen())
        self.mode_buttons = {
            mode: ttk.Button(
                toggle, text=label, style="Filter.TButton", command=self._mode_command(mode)
            )
            for mode, label in (("text", "Texto"), ("markdown", "Markdown"))
        }
        for button in self.mode_buttons.values():
            button.pack(side="left", padx=(4, 0))
        holder = ttk.Frame(card, style="Surface.TFrame")
        holder.pack(fill="both", expand=True)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.preview = self._text(holder, self.fonts(11))
        self.raw = self._text(holder, self.fonts.code(10))
        self._raw_tags()
        self._preview_tags()
        self.raw.bind("<ButtonRelease-1>", lambda _e: self._markdown_clicked())
        self.raw.bind("<KeyRelease>", lambda _e: self._markdown_clicked())
        self.preview.bind("<ButtonRelease-1>", lambda _e: self._preview_clicked())
        self._show_mode()
        return card

    def _text(self, holder: ttk.Frame, spec: tuple[str, int] | tuple[str, int, str]) -> tk.Text:
        frame = ttk.Frame(holder, style="Surface.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        text = tk.Text(frame, width=40, height=10, wrap="word", undo=False, padx=20, pady=16,
                       font=spec, relief="flat", borderwidth=0, background=PALETTE.surface,
                       foreground=PALETTE.ink, selectbackground=PALETTE.accent_soft,
                       selectforeground=PALETTE.ink, inactiveselectbackground=PALETTE.accent_soft,
                       insertbackground=PALETTE.ink, spacing1=2, spacing3=4)  # fmt: skip
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        # Read-only, but selectable and copyable (Ctrl shortcuts and navigation keys pass).
        text.bind("<Key>", _read_only_key)
        return text

    def _raw_tags(self) -> None:
        raw, mono = self.raw, self.fonts.mono
        raw.tag_configure("frontmatter", foreground="#6A737D")
        raw.tag_configure("heading", foreground=PALETTE.accent_hover, font=(mono, 10, "bold"))
        raw.tag_configure("fence", foreground="#6F42C1")
        raw.tag_configure("table", foreground="#22863A")
        raw.tag_configure("comment", foreground="#6A737D")
        raw.tag_configure("current", background=PALETTE.warn_soft)

    def _preview_tags(self) -> None:
        p, f = self.preview, self.fonts
        for level, size in zip(range(1, 7), (20, 16, 14, 12, 11, 11), strict=True):
            p.tag_configure(f"h{level}", font=f(size, "bold"), spacing1=12, spacing3=6)
        p.tag_configure("bold", font=f(11, "bold"))
        p.tag_configure("italic", font=(f.family, 11, "italic"))
        p.tag_configure("code", font=f.code(10), background="#F3F4F6", lmargin1=12, lmargin2=12)
        p.tag_configure("code-inline", font=f.code(10), background="#F0F1F3")
        p.tag_configure("math", font=f.code(10), foreground="#6F42C1")
        p.tag_configure("link", foreground=PALETTE.accent, underline=True)
        p.tag_configure("sup", offset=4, font=f(8))
        p.tag_configure("table", font=f.code(10), lmargin1=8, lmargin2=8)
        p.tag_configure("caption", font=(f.family, 10, "italic"), foreground=PALETTE.muted)
        p.tag_configure("footnote", font=f(10), foreground=PALETTE.muted)
        p.tag_configure("rule", foreground=PALETTE.border_strong)
        p.tag_configure("current", background=PALETTE.warn_soft)
        for depth in range(1, 5):
            p.tag_configure(f"indent-{depth}", lmargin1=18 * depth, lmargin2=18 * depth + 14)

    def _checklist(self) -> None:
        self.check_card = ttk.Frame(self, style="Warn.TFrame", padding=(16, 12))
        self.check_card.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.check_card.columnconfigure(0, weight=1)
        head = ttk.Frame(self.check_card, style="Warn.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.check_title = ttk.Label(head, text="", style="Warn.Strong.TLabel")
        self.check_title.grid(row=0, column=0, sticky="w")
        self.automatic_button = ttk.Button(head, text="", style="Link.TButton",
                                           command=self._show_automatic)  # fmt: skip
        self.automatic_button.grid(row=0, column=1, padx=(0, 12))
        self.mark_button = ttk.Button(head, text="Marcar como revisado", style="Primary.TButton",
                                      command=self._toggle_reviewed)  # fmt: skip
        self.mark_button.grid(row=0, column=2)

        holder = ttk.Frame(self.check_card, style="Warn.TFrame")
        holder.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        holder.columnconfigure(0, weight=1)
        self.list_canvas = tk.Canvas(holder, background=PALETTE.warn_row, highlightthickness=0,
                                     height=10)  # fmt: skip
        self.list_scroll = ttk.Scrollbar(holder, orient="vertical",
                                         command=self.list_canvas.yview)  # fmt: skip
        self.list_canvas.configure(yscrollcommand=self.list_scroll.set)
        self.list_canvas.grid(row=0, column=0, sticky="ew")
        self.items = ttk.Frame(self.list_canvas, style="Warn.TFrame")
        self.items.columnconfigure(1, weight=1)
        window = self.list_canvas.create_window(0, 0, window=self.items, anchor="nw")
        self.list_canvas.bind(
            "<Configure>", lambda e: self.list_canvas.itemconfigure(window, width=e.width)
        )
        self.items.bind("<Configure>", lambda _e: self._fit_checklist())
        self.list_canvas.bind("<MouseWheel>", self._checklist_wheel)

    # --- Loading ---------------------------------------------------------------------

    def load(self, review: Review) -> None:
        self.stop_preview()
        self.review = review
        self._highlight = None
        title = review.title
        # Long titles would push the buttons out: the full one is in the library.
        self.title_label.configure(text=title if len(title) <= 60 else title[:58] + "…")
        self._show_state()
        self.ocr_button.state(["!disabled"] if review.is_pdf else ["disabled"])
        self.md_button.state(["!disabled"] if review.markdown else ["disabled"])
        self._load_original(review)
        self._show_parts(review)
        self._fill_raw(review.markdown)
        self._fill_preview(review)
        self._fill_checklist(review)
        self.page = 1
        first = min((a.page for a in review.anchors), default=1)
        self.show_page(first if review.is_book else 1)
        if review.document is None and Path(review.entry.source).is_file():
            backend, ocr = self.actions.preview_options()
            self._preview = PreviewJob(review.entry.source, backend, ocr)
            self.after(_POLL_MS, self._poll_preview)

    def _show_state(self) -> None:
        if self.review is None:
            return
        state = "reviewed" if self.review.entry.reviewed_at else self.review.entry.state
        self.chip.configure(text=STATES[state][0], style=f"{state}.Chip.TLabel")
        reviewed = bool(self.review.entry.reviewed_at)
        self.mark_button.configure(
            text="Quitar marca de revisado" if reviewed else "Marcar como revisado",
            style="TButton" if reviewed else "Primary.TButton",
        )

    def set_has_next(self, has_next: bool) -> None:
        self.next_button.state(["!disabled"] if has_next else ["disabled"])

    def _poll_preview(self) -> None:
        job = self._preview
        if job is None or self.review is None:
            return
        outcome = job.poll()
        if outcome is None:
            self.after(_POLL_MS, self._poll_preview)
            return
        self._preview = None
        if isinstance(outcome, Document):
            self.review.document = outcome
            if self.review.is_book:
                self.review.part_documents = split_like(outcome, self.review.parts)
            self._fill_preview(self.review)
            if self.pdf is None:
                self._show_info(self.review)
        elif isinstance(outcome, Failed):
            self.preview.delete("1.0", "end")
            self.preview.insert("end", "No se pudo preparar la vista de texto. La vista "
                                "Markdown muestra el archivo tal cual.", ("caption",))  # fmt: skip

    def stop_preview(self) -> None:
        if self._preview is not None:
            self._preview.cancel()
            self._preview = None

    def close(self) -> None:
        self.stop_preview()
        if self.pdf is not None:
            self.pdf.close()
            self.pdf = None

    def _load_original(self, review: Review) -> None:
        if self.pdf is not None:
            self.pdf.close()
            self.pdf = None
        self.about.place_forget()
        if review.is_pdf:
            try:
                self.pdf = pdfium.PdfDocument(review.entry.source)
            except pdfium.PdfiumError:
                self.pdf = None
        if self.pdf is None:
            self.nav.pack_forget()
            self._show_info(review)
        else:
            self.nav.pack(side="right")

    def _show_info(self, review: Review) -> None:
        """For what has no pages (EPUB, Wikipedia) or a PDF no longer where it was."""
        self.canvas.delete("all")
        meta = review.document.meta if review.document else None
        about = self.about
        about.configure(state="normal")
        about.delete("1.0", "end")
        source = review.entry.source
        if meta and meta.source_type == "wikipedia":
            about.insert("end", f"{meta.title}\n", ("title",))
            about.insert("end", f"Artículo de Wikipedia · revisión {meta.revision_id}\n"
                        f"{meta.license}\n\n", ("muted",))  # fmt: skip
            link = ttk.Button(about, text="Abrir el artículo en el navegador",
                              command=lambda: webbrowser.open(meta.source))  # fmt: skip
            about.window_create("end", window=link)
        elif meta and meta.source_type == "epub":
            about.insert("end", f"{meta.title or Path(source).stem}\n", ("title",))
            lines = []
            if meta.authors:
                lines.append(", ".join(meta.authors))
            if meta.language:
                lines.append(f"Idioma: {meta.language}")
            if meta.license:
                lines.append(f"Derechos: {meta.license}")
            about.insert("end", "\n".join(lines) + "\n\n", ("muted",))
            about.insert("end", "Un libro EPUB no tiene páginas fijas: compara con el texto de "
                        "la derecha y, si dudas, ábrelo en tu lector de libros.")  # fmt: skip
        elif not Path(source).is_file():
            about.insert("end", "No se encuentra el original\n", ("title",))
            about.insert("end", f"Estaba en {source}. Si lo has movido, el resultado sigue "
                        "disponible a la derecha.", ("muted",))  # fmt: skip
        else:
            about.insert("end", f"{Path(source).name}\n", ("title",))
            about.insert("end", "Preparando la información del original…", ("muted",))
        about.configure(state="disabled")
        about.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _fill_raw(self, markdown: str) -> None:
        raw = self.raw
        raw.delete("1.0", "end")
        raw.insert("1.0", markdown)
        in_front = in_fence = False
        for number, line in enumerate(markdown.split("\n"), start=1):
            start, end = f"{number}.0", f"{number}.end"
            if number == 1 and line == "---":
                in_front = True
            if in_front:
                raw.tag_add("frontmatter", start, end)
                if number > 1 and line == "---":
                    in_front = False
                continue
            if line.lstrip().startswith(("```", "~~~")):
                raw.tag_add("fence", start, end)
                in_fence = not in_fence
            elif in_fence:
                raw.tag_add("fence", start, end)
            elif line.startswith("#"):
                raw.tag_add("heading", start, end)
            elif line.startswith("|"):
                raw.tag_add("table", start, end)
            elif line.startswith("<!--"):
                raw.tag_add("comment", start, end)

    def _fill_preview(self, review: Review) -> None:
        p = self.preview
        p.delete("1.0", "end")
        self._preview_images.clear()
        document = review.part_document()
        if document is None:
            if not Path(review.entry.source).is_file():
                text = "No se encuentra el original, así que solo está la vista Markdown."
            elif review.document is None:
                text = "Preparando la vista de texto…"
            elif review.part == 0:
                text = "Este es el índice del libro: elige un capítulo arriba a la derecha."
            else:
                text = (
                    "La vista de texto no coincide con los capítulos guardados (el libro se "
                    "convirtió con otras opciones). La vista Markdown muestra el archivo."
                )
            p.insert("end", text, ("caption",))
            return
        assets = {a.sha256: a for a in document.assets}
        for run in preview_runs(document):
            if run.image is not None:
                self._insert_image(assets.get(run.image), run.tags)
            elif run.text:
                p.insert("end", run.text, run.tags)

    def _insert_image(self, asset: object, tags: tuple[str, ...]) -> None:
        data = getattr(asset, "data", None)
        if data is None:
            self.preview.insert("end", "[imagen no disponible]", ("caption", *tags))
            return
        try:
            image = PILImage.open(io.BytesIO(data))
            image.thumbnail((_PREVIEW_IMAGE_WIDTH, _PREVIEW_IMAGE_WIDTH * 2))
            photo = ImageTk.PhotoImage(image)
        except (OSError, ValueError, PILImage.DecompressionBombError):
            self.preview.insert("end", "[imagen no se puede mostrar]", ("caption", *tags))
            return
        self._preview_images.append(photo)  # Tk keeps no reference of its own
        index = self.preview.index("end-1c")
        self.preview.image_create(index, image=photo)
        for tag in tags:  # the page tag lets the original follow the preview
            self.preview.tag_add(tag, index)

    # --- Checklist -------------------------------------------------------------------

    def _fill_checklist(self, review: Review) -> None:
        for child in self.items.winfo_children():
            child.destroy()
        self._checks.clear()
        to_review = review.to_review
        for row, finding in enumerate(to_review):
            var = tk.BooleanVar(value=finding.checked)
            self._checks.append((finding, var))
            ttk.Checkbutton(self.items, variable=var, style="Warn.TCheckbutton",
                            command=self._check_command(finding, var)).grid(
                row=row, column=0, sticky="nw", padx=(0, 4), pady=3)  # fmt: skip
            line = ttk.Frame(self.items, style="Warn.TFrame")
            line.grid(row=row, column=1, sticky="ew", pady=3)
            title = ttk.Label(line, text=review_title(finding.code, finding.page),
                              style="Warn.Strong.TLabel", cursor="hand2")  # fmt: skip
            title.pack(side="left")
            title.bind("<Button-1>", self._go_command(finding))
            hint = ttk.Label(line, text="  " + review_hint(finding.code), style="Warn.Muted.TLabel")
            hint.pack(side="left", fill="x")
            for widget in (line, title, hint):
                widget.bind("<MouseWheel>", self._checklist_wheel)
        if not to_review:
            ttk.Label(self.items, text="tomd no encontró nada dudoso en este documento.",
                      style="Warn.Muted.TLabel").grid(row=0, column=0, columnspan=2,
                                                      sticky="w")  # fmt: skip
        automatic = len(review.automatic)
        self.automatic_button.configure(text=f"Ver {automatic} cambios automáticos")
        if automatic:
            self.automatic_button.grid()
        else:
            self.automatic_button.grid_remove()
        self._count_checks()
        self.list_canvas.yview_moveto(0)

    def _check_command(self, finding: Finding, var: tk.BooleanVar) -> Callable[[], None]:
        def toggled() -> None:
            if self.review is not None:
                self.actions.set_checked(self.review, finding, var.get())
            self._count_checks()

        return toggled

    def _go_command(self, finding: Finding) -> Callable[["tk.Event[tk.Misc]"], None]:
        return lambda _e: self.go_to(finding)

    def _count_checks(self) -> None:
        total = len(self._checks)
        done = sum(var.get() for _, var in self._checks)
        if total:
            self.check_title.configure(text=f"Qué revisar  ·  {done} de {total} comprobado")
        else:
            self.check_title.configure(text="Qué revisar")

    def _fit_checklist(self) -> None:
        self.update_idletasks()
        rows = self.items.grid_slaves(column=1) or self.items.grid_slaves()
        row_height = max((w.winfo_reqheight() for w in rows), default=20) + 6
        wanted = min(self.items.winfo_reqheight(), row_height * _CHECKLIST_ROWS)
        self.list_canvas.configure(height=wanted, scrollregion=self.list_canvas.bbox("all"))
        if self.items.winfo_reqheight() > wanted:
            self.list_scroll.grid(row=0, column=1, sticky="ns")
        else:
            self.list_scroll.grid_remove()

    def _checklist_wheel(self, event: "tk.Event[tk.Misc]") -> None:
        if self.list_scroll.winfo_ismapped():
            self.list_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def go_to(self, finding: Finding) -> None:
        """Show where a finding is: its block on the page, and its text on the right."""
        if finding.page is None or self.review is None:
            return
        self._follow_part(finding.page)
        if self.pdf is not None:
            self._point_at(finding.page, finding.bbox)
        line = self.review.line_for_page(finding.page)
        if line is not None:
            self._mark_line(line)
        self._mark_preview_page(finding.page)

    def _toggle_reviewed(self) -> None:
        if self.review is not None:
            self.actions.set_reviewed(self.review, not self.review.entry.reviewed_at)

    def _show_automatic(self) -> None:
        if self.review is None:
            return
        from tomd.ui.texts import automatic_text

        counts: dict[str, int] = {}
        for finding in self.review.automatic:
            counts[finding.code] = counts.get(finding.code, 0) + 1
        dialog = tk.Toplevel(self)
        dialog.title("Cambios automáticos")
        dialog.configure(background=PALETTE.surface)
        dialog.transient(self.winfo_toplevel())
        body = ttk.Frame(dialog, style="Surface.TFrame", padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Cambios automáticos", style="Surface.Title.TLabel").pack(anchor="w")
        ttk.Label(body, text="tomd los hizo a propósito para que el texto quede limpio; no "
                  "hace falta revisarlos.", style="Surface.Muted.TLabel", wraplength=460,
                  justify="left").pack(anchor="w", pady=(4, 12))  # fmt: skip
        for code in sorted(counts, key=lambda c: (-counts[c], c)):
            times = f"  ×{counts[code]}" if counts[code] > 1 else ""
            ttk.Label(body, text=f"•  {automatic_text(code)}{times}", style="Surface.TLabel",
                      wraplength=460, justify="left").pack(anchor="w", pady=2)  # fmt: skip
        ttk.Button(body, text="Cerrar", style="Primary.TButton", command=dialog.destroy).pack(
            anchor="e", pady=(16, 0)
        )
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.grab_set()

    # --- Result ----------------------------------------------------------------------

    def _mode_command(self, mode: str) -> Callable[[], None]:
        def chosen() -> None:
            self.mode = mode
            self._show_mode()

        return chosen

    def _show_mode(self) -> None:
        shown = self.preview if self.mode == "text" else self.raw
        shown.master.tkraise()
        for mode, button in self.mode_buttons.items():
            button.configure(style="FilterOn.TButton" if mode == self.mode else "Filter.TButton")

    def _mark_line(self, line: int) -> None:
        self.raw.tag_remove("current", "1.0", "end")
        self.raw.tag_add("current", f"{line}.0", f"{line}.end")
        self.raw.see(f"{line}.0")

    def _mark_preview_page(self, page: int) -> None:
        tag = f"page-{page}"
        self.preview.tag_remove("current", "1.0", "end")
        ranges = self.preview.tag_ranges(tag)
        if ranges:
            first = self.preview.index(f"{tag}.first")
            self.preview.tag_add("current", first, f"{first} lineend")
            self.preview.see(first)

    def _markdown_clicked(self) -> None:
        if self.review is None or self.pdf is None:
            return
        line = int(self.raw.index("insert").split(".")[0])
        anchor = self.review.anchor_for_line(line)
        if anchor is not None:
            self._point_at(anchor.page, anchor.bbox)

    def _preview_clicked(self) -> None:
        if self.pdf is None:
            return
        for tag in self.preview.tag_names("insert"):
            if tag.startswith("page-") and tag[5:].isdigit():
                self.show_page(int(tag[5:]), follow=False)
                return

    # --- Original --------------------------------------------------------------------

    def show_page(self, number: int, *, follow: bool = True) -> None:
        if self.pdf is None:
            self.page_label.set("—")
            return
        self.page = max(1, min(number, len(self.pdf)))
        self.page_label.set(f"pág. {self.page} de {len(self.pdf)}")
        self._render_page()
        if follow and self.review is not None:
            self._follow_part(self.page)
            line = self.review.line_for_page(self.page)
            if line is not None:
                self._mark_line(line)
            self._mark_preview_page(self.page)

    # --- Chapters of a book ------------------------------------------------------------

    def _show_parts(self, review: Review) -> None:
        if not review.is_book:
            self.part_box.pack_forget()
            return
        self.part_box.configure(values=[review.part_label(i) for i in range(len(review.parts))])
        self.part_box.current(review.part)
        self.part_box.pack(side="right", padx=(0, 12))

    def _part_chosen(self) -> None:
        if self.review is not None:
            self.select_part(self.part_box.current())

    def select_part(self, part: int) -> None:
        """Show another file of the book; the original follows to its first page."""
        review = self.review
        if review is None or not review.is_book or part == review.part or part < 0:
            return
        review.show_part(part)
        self.part_box.current(part)
        self.md_button.state(["!disabled"] if review.markdown else ["disabled"])
        self._fill_raw(review.markdown)
        self._fill_preview(review)
        pages = [a.page for a in review.anchors]
        if pages and self.pdf is not None and not min(pages) <= self.page <= max(pages):
            self.show_page(min(pages), follow=False)

    def _follow_part(self, page: int) -> None:
        """Turning to a page of another chapter shows that chapter's file."""
        if self.review is None or not self.review.is_book:
            return
        part = self.review.part_for_page(page)
        if part is not None and part != self.review.part:
            self.select_part(part)

    def zoom(self, step: int) -> None:
        self.zoom_index = max(0, min(len(_ZOOMS) - 1, self.zoom_index + step))
        self._render_page()

    def _wheel(self, event: "tk.Event[tk.Canvas]") -> None:
        if isinstance(event.state, int) and event.state & 4:  # Ctrl + wheel zooms
            self.zoom(1 if event.delta > 0 else -1)
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _render_page(self) -> None:
        if self.pdf is None:
            return
        page = self.pdf[self.page - 1]
        try:
            width, height = page.get_size()
            available = max(self.canvas.winfo_width() - 32, 200)
            scale = available / width * _ZOOMS[self.zoom_index]
            image = page.render(scale=scale).to_pil()
        finally:
            page.close()
        self._page_image = ImageTk.PhotoImage(image)
        self._scale, self._view_height = scale, image.height + 32
        self.canvas.delete("all")
        self.canvas.create_rectangle(17, 17, 17 + image.width, 17 + image.height,
                                     fill="#C9CED6", outline="")  # fmt: skip
        self.canvas.create_image(16, 16, image=self._page_image, anchor="nw")
        if self._highlight and self._highlight[0] == self.page:
            x0, y0, x1, y1 = self._highlight[1]
            self.canvas.create_rectangle(
                16 + x0 * scale - 4, 16 + (height - y1) * scale - 4,
                16 + x1 * scale + 4, 16 + (height - y0) * scale + 4,
                outline=PALETTE.warn_strong, width=3,
            )  # fmt: skip
        self.canvas.configure(scrollregion=(0, 0, image.width + 32, image.height + 32))

    def _point_at(self, page: int, bbox: tuple[float, float, float, float] | None) -> None:
        self._highlight = (page, bbox) if bbox else None
        self.show_page(page, follow=False)
        if bbox and self.pdf is not None and self._view_height:
            height = self.pdf.get_page_size(page - 1)[1]
            top = 16 + (height - bbox[3]) * self._scale  # canvas y of the block's top
            # Leave a little context above the highlighted block.
            self.canvas.yview_moveto(max(0.0, (top - 60) / self._view_height))

    def _with_review(self, action: Callable[[Review], None]) -> None:
        if self.review is not None:
            action(self.review)


# Keys that move or select without editing the read-only text views.
_NAV_KEYS = frozenset(
    {"Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next", "Shift_L", "Shift_R",
     "Control_L", "Control_R"}
)  # fmt: skip


def _read_only_key(event: "tk.Event[tk.Text]") -> str | None:
    control = isinstance(event.state, int) and bool(event.state & 4)
    return None if control or event.keysym in _NAV_KEYS else "break"


def char_width(spec: tuple[str, int] | tuple[str, int, str]) -> int:
    return int(font.Font(font=spec).measure("0"))

"""Desktop window to run conversions and review them (Tkinter, standard library).

Layout: a toolbar to pick the source and options; the session history on the left; the
original (PDF page, with the selected block or diagnostic highlighted) in the middle; the
Markdown (raw file and preview) on the right; diagnostics, with the catalog's advice,
along the bottom. Clicking a diagnostic or a line of Markdown shows its place in the
original, and turning pages scrolls the Markdown along.
"""

import io
import os
import re
import subprocess
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk
from typing import Any

import pypdfium2 as pdfium
from PIL import Image as PILImage
from PIL import ImageTk

from tomd.readers.pdf.reader import DEFAULT_BACKEND
from tomd.ui.job import BatchEnded, BatchJob, BatchLine, ConversionJob, Failed, Succeeded
from tomd.ui.session import ConversionRequest, DiagnosticRow, Review, preview_runs

_POLL_MS = 150
_SEVERITY_COLOURS = {"error": "#b00020", "warning": "#9a6700", "info": "#555555"}
_HIGHLIGHT = "#e8590c"
# Zoom steps for the page view, relative to "fit to width".
_ZOOMS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)
_PREVIEW_IMAGE_WIDTH = 520


class TomdApp:
    def __init__(self, root: tk.Tk, out_dir: Path | None = None) -> None:
        self.root = root
        root.title("tomd — conversión y revisión")
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            root.iconbitmap(default=sys.executable)  # type: ignore[no-untyped-call]
        # Sized from the screen: with DPI awareness, fixed pixel sizes are tiny on scaled
        # displays.
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width, height = int(screen_w * 0.88), int(screen_h * 0.85)
        root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{(screen_h - height) // 3}")
        root.minsize(int(screen_w * 0.5), int(screen_h * 0.5))
        self._fonts()

        self.source = tk.StringVar()
        self.revision = tk.StringVar()
        self.backend = tk.StringVar(value=DEFAULT_BACKEND)
        self.ocr = tk.StringVar(value="auto")
        self.out_dir = tk.StringVar(value=str(out_dir or default_out_dir()))
        self.status = tk.StringVar(
            value="Arrastra aquí archivos PDF o EPUB, o una carpeta; o usa «Abrir archivo…» y "
            "«Convertir carpeta…». También acepta artículos de Wikipedia en «Origen»."
        )
        self.page_label = tk.StringVar(value="—")
        self.sync = tk.BooleanVar(value=True)
        self.find_text = tk.StringVar()

        self.job: ConversionJob | None = None
        self.batch: BatchJob | None = None
        self._drop_handled = False
        self.reviews: list[Review] = []
        self.review: Review | None = None
        self.pdf: pdfium.PdfDocument | None = None
        self.page = 1
        self.zoom_index = _ZOOMS.index(1.0)
        self._page_image: ImageTk.PhotoImage | None = None
        self._preview_images: list[ImageTk.PhotoImage] = []
        self._highlight: tuple[int, tuple[float, float, float, float]] | None = None
        self._scale = 1.0  # pixels per PDF point of the page on screen
        self._view_height = 0
        self._rows: dict[str, DiagnosticRow] = {}

        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)

    # --- Layout --------------------------------------------------------------------------

    def _fonts(self) -> None:
        base = font.nametofont("TkDefaultFont")
        # Treeview rows do not grow with the font on scaled displays; size them from it.
        ttk.Style(self.root).configure("Treeview", rowheight=int(base.metrics("linespace") * 1.4))
        family = base.actual("family")
        self.mono = font.nametofont("TkFixedFont").actual("family")
        self.font_body = (family, 11)
        self.font_mono = (self.mono, 10)
        self.font_headings = {
            level: (family, size, "bold")
            for level, size in zip(range(1, 7), (20, 16, 14, 12, 11, 11), strict=True)
        }

    def _build(self) -> None:
        self._toolbar().pack(fill="x", padx=8, pady=(8, 4))
        vertical = self._vpane = ttk.PanedWindow(self.root, orient="vertical")
        vertical.pack(fill="both", expand=True, padx=8)
        horizontal = self._hpane = ttk.PanedWindow(vertical, orient="horizontal")
        horizontal.add(self._history(horizontal), weight=0)
        horizontal.add(self._original(horizontal), weight=3)
        horizontal.add(self._markdown(horizontal), weight=3)
        vertical.add(horizontal, weight=4)
        vertical.add(self._diagnostics(vertical), weight=1)
        self.root.after_idle(self._initial_split)
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=(2, 6))
        self.progress = ttk.Progressbar(bar, mode="determinate", length=160, value=0)
        self.progress.pack(side="right")
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(side="left", fill="x")
        self.root.bind("<Control-o>", lambda _e: self.choose_file())
        self.root.bind("<Control-Return>", lambda _e: self.start_conversion())
        self.root.bind("<Control-f>", lambda _e: self.find_entry.focus_set())

    def _initial_split(self) -> None:
        """Pane sizes as shares of the window: history, original, Markdown; diagnostics."""
        self.root.update_idletasks()
        width, height = self._hpane.winfo_width(), self._vpane.winfo_height()
        if width < 100 or height < 100:  # not mapped yet
            self.root.after(50, self._initial_split)
            return
        self._hpane.sashpos(0, int(width * 0.14))  # type: ignore[no-untyped-call]
        self._hpane.sashpos(1, int(width * 0.57))  # type: ignore[no-untyped-call]
        self._vpane.sashpos(0, int(height * 0.72))  # type: ignore[no-untyped-call]

    def _toolbar(self) -> ttk.Frame:
        bar = ttk.Frame(self.root)
        ttk.Label(bar, text="Origen").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(bar, textvariable=self.source, width=60)
        entry.grid(row=0, column=1, sticky="ew", padx=4)
        entry.bind("<Return>", lambda _e: self.start_conversion())
        ttk.Button(bar, text="Abrir archivo…", command=self.choose_file).grid(row=0, column=2)
        self.folder_button = ttk.Button(bar, text="Convertir carpeta…", command=self.convert_folder)
        self.folder_button.grid(
            row=1, column=9, columnspan=2, sticky="ew", padx=(12, 0), pady=(4, 0)
        )
        ttk.Label(bar, text="Revisión").grid(row=0, column=3, padx=(12, 2))
        ttk.Entry(bar, textvariable=self.revision, width=12).grid(row=0, column=4)
        ttk.Label(bar, text="Backend").grid(row=0, column=5, padx=(12, 2))
        ttk.Combobox(
            bar,
            textvariable=self.backend,
            values=available_backends(),
            width=10,
            state="readonly",
        ).grid(row=0, column=6)
        ttk.Label(bar, text="OCR").grid(row=0, column=7, padx=(12, 2))
        ttk.Combobox(
            bar,
            textvariable=self.ocr,
            values=("auto", "always", "never"),
            width=8,
            state="readonly",
        ).grid(row=0, column=8)
        self.convert_button = ttk.Button(bar, text="Convertir", command=self.start_conversion)
        self.convert_button.grid(row=0, column=9, padx=(12, 2))
        self.cancel_button = ttk.Button(
            bar, text="Cancelar", command=self.cancel_conversion, state="disabled"
        )
        self.cancel_button.grid(row=0, column=10)

        ttk.Label(bar, text="Salida").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(bar, textvariable=self.out_dir, width=60).grid(
            row=1, column=1, sticky="ew", padx=4, pady=(4, 0)
        )
        ttk.Button(bar, text="Elegir…", command=self.choose_out_dir).grid(
            row=1, column=2, pady=(4, 0)
        )
        ttk.Button(bar, text="Abrir carpeta", command=self.open_out_dir).grid(
            row=1, column=3, columnspan=2, sticky="w", padx=(12, 0), pady=(4, 0)
        )
        ttk.Button(bar, text="Revisar .md existente…", command=self.open_markdown).grid(
            row=1, column=5, columnspan=3, sticky="w", padx=(12, 0), pady=(4, 0)
        )
        bar.columnconfigure(1, weight=1)
        return bar

    def _history(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent)
        ttk.Label(frame, text="Conversiones").pack(anchor="w")
        self.history = tk.Listbox(frame, width=18, activestyle="none", exportselection=False)
        self.history.pack(fill="both", expand=True)
        self.history.bind("<<ListboxSelect>>", lambda _e: self._history_selected())
        return frame

    def _original(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent)
        nav = ttk.Frame(frame)
        nav.pack(fill="x")
        ttk.Label(nav, text="Original").pack(side="left")
        ttk.Button(nav, text="◀", width=3, command=lambda: self.show_page(self.page - 1)).pack(
            side="left", padx=(12, 0)
        )
        ttk.Label(nav, textvariable=self.page_label, width=10, anchor="center").pack(side="left")
        ttk.Button(nav, text="▶", width=3, command=lambda: self.show_page(self.page + 1)).pack(
            side="left"
        )
        ttk.Button(nav, text="-", width=3, command=lambda: self.zoom(-1)).pack(
            side="left", padx=(12, 0)
        )
        ttk.Button(nav, text="+", width=3, command=lambda: self.zoom(1)).pack(side="left")
        ttk.Checkbutton(nav, text="Sincronizar con el Markdown", variable=self.sync).pack(
            side="right"
        )
        holder = ttk.Frame(frame)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, background="#8a8a8a", highlightthickness=0)
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
        self.info = tk.Text(
            holder, wrap="word", height=8, relief="flat", padx=16, pady=16, font=self.font_body
        )
        return frame

    def _markdown(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent)
        find = ttk.Frame(frame)
        find.pack(fill="x")
        ttk.Label(find, text="Markdown").pack(side="left")
        self.find_entry = ttk.Entry(find, textvariable=self.find_text, width=24)
        self.find_entry.pack(side="right")
        self.find_entry.bind("<Return>", lambda _e: self.find_next())
        ttk.Label(find, text="Buscar (Ctrl+F)").pack(side="right", padx=4)
        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill="both", expand=True)
        raw_holder, self.raw = self._text(self.tabs, mono=True)
        preview_holder, self.preview = self._text(self.tabs, mono=False)
        self.tabs.add(raw_holder, text="Markdown")
        self.tabs.add(preview_holder, text="Vista previa")
        self.raw.bind("<ButtonRelease-1>", lambda _e: self._markdown_clicked())
        self.raw.bind("<KeyRelease>", lambda _e: self._markdown_clicked())
        self._raw_tags()
        self._preview_tags()
        return frame

    def _text(self, parent: tk.Misc, *, mono: bool) -> tuple[ttk.Frame, tk.Text]:
        holder = ttk.Frame(parent)
        text = tk.Text(
            holder,
            width=40,  # small requests: the panes, not the text, decide the size
            height=10,
            wrap="word",
            undo=False,
            padx=10,
            pady=8,
            font=self.font_mono if mono else self.font_body,
        )
        scroll = ttk.Scrollbar(holder, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        # Read-only, but selectable and copyable (Ctrl shortcuts and navigation keys pass).
        text.bind("<Key>", _read_only_key)
        return holder, text

    def _raw_tags(self) -> None:
        raw = self.raw
        raw.tag_configure("frontmatter", foreground="#6a737d")
        raw.tag_configure("heading", foreground="#0b4f9c", font=(self.mono, 10, "bold"))
        raw.tag_configure("fence", foreground="#6f42c1")
        raw.tag_configure("table", foreground="#22863a")
        raw.tag_configure("comment", foreground="#6a737d")
        raw.tag_configure("current", background="#fff3bf")
        raw.tag_configure("found", background="#ffd43b")

    def _preview_tags(self) -> None:
        p = self.preview
        for level, spec in self.font_headings.items():
            p.tag_configure(f"h{level}", font=spec, spacing1=10, spacing3=4, foreground="#1c2b40")
        family = self.font_body[0]
        p.tag_configure("bold", font=(family, 11, "bold"))
        p.tag_configure("italic", font=(family, 11, "italic"))
        p.tag_configure("code", font=self.font_mono, background="#f3f3f3", lmargin1=12, lmargin2=12)
        p.tag_configure("code-inline", font=self.font_mono, background="#f0f0f0")
        p.tag_configure("math", font=self.font_mono, foreground="#6f42c1")
        p.tag_configure("link", foreground="#0b62d6", underline=True)
        p.tag_configure("sup", offset=4, font=(family, 8))
        p.tag_configure("table", font=self.font_mono, lmargin1=8, lmargin2=8)
        p.tag_configure("caption", font=(family, 10, "italic"), foreground="#444444")
        p.tag_configure("footnote", font=(family, 10), foreground="#333333")
        p.tag_configure("rule", foreground="#aaaaaa")
        for depth in range(1, 5):
            p.tag_configure(f"indent-{depth}", lmargin1=18 * depth, lmargin2=18 * depth + 14)

    def _diagnostics(self, parent: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(parent)
        ttk.Label(frame, text="Diagnósticos").pack(anchor="w")
        columns = ("severity", "code", "page", "message")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=7)
        # Widths in characters of the UI font, so they scale with the display (DPI).
        char = font.nametofont("TkDefaultFont").measure("0")
        for name, title, chars in (
            ("severity", "Severidad", 10),
            ("code", "Código", 30),
            ("page", "Pág.", 6),
            ("message", "Mensaje", 80),
        ):
            self.tree.heading(name, text=title, anchor="w")
            self.tree.column(
                name, width=char * chars, minwidth=char * 4, anchor="w", stretch=name == "message"
            )
        for severity, colour in _SEVERITY_COLOURS.items():
            self.tree.tag_configure(severity, foreground=colour)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.advice = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.advice, wraplength=1200, foreground="#0b4f9c").pack(
            side="bottom", fill="x", pady=(2, 0)
        )
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._diagnostic_selected())
        return frame

    # --- Actions -------------------------------------------------------------------------

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Elegir documento",
            filetypes=[
                ("PDF, EPUB y HTML de Wikipedia", "*.pdf *.epub *.html *.htm"),
                ("Todos", "*.*"),
            ],
        )
        if path:
            self.source.set(path)

    def choose_out_dir(self) -> None:
        path = filedialog.askdirectory(title="Carpeta de salida", mustexist=False)
        if path:
            self.out_dir.set(path)

    def open_out_dir(self) -> None:
        folder = Path(self.out_dir.get())
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # Windows only
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])

    def start_conversion(self) -> None:
        if self.job is not None or self.batch is not None:
            return
        source = self.source.get().strip()
        if not source:
            self.status.set("Indica un archivo o un artículo de Wikipedia.")
            return
        revision = self.revision.get().strip()
        if revision and not revision.isdigit():
            messagebox.showerror("tomd", "La revisión debe ser un número entero.")
            return
        request = ConversionRequest(
            source,
            Path(self.out_dir.get()),
            int(revision) if revision else None,
            self.backend.get(),
            self.ocr.get(),
        )
        self.job = ConversionJob(request)
        self.convert_button.state(["disabled"])
        self.cancel_button.state(["!disabled"])
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status.set(f"Convirtiendo {Path(source).name if Path(source).exists() else source}…")
        self.root.after(_POLL_MS, self._poll)

    def cancel_conversion(self) -> None:
        if self.job is not None:
            self.job.cancel()
            self._job_finished()
            self.status.set("Conversión cancelada.")
        if self.batch is not None:
            self.batch.cancel()
            self._batch_finished()
            self.status.set(
                "Carpeta cancelada. Lo ya convertido se conserva: al convertir de nuevo la "
                "misma carpeta se continúa donde se dejó."
            )

    # --- Folder conversion ---------------------------------------------------------------

    def convert_folder(self, folder: str | None = None) -> None:
        if self.job is not None or self.batch is not None:
            return
        folder = folder or filedialog.askdirectory(title="Carpeta con PDF y EPUB", mustexist=True)
        if folder:
            self._start_batch([folder])

    def _start_batch(self, sources: list[str]) -> None:
        self.batch = BatchJob(sources, self.out_dir.get(), self.ocr.get(), self.backend.get())
        for button in (self.convert_button, self.folder_button):
            button.state(["disabled"])
        self.cancel_button.state(["!disabled"])
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status.set(f"Preparando {self.batch.label}…")
        self.root.after(_POLL_MS, self._poll_batch)

    # --- Drag and drop -------------------------------------------------------------------

    def enable_drop(self) -> bool:
        """Accept files and folders dropped on the window (tkdnd, through tkinterdnd2).
        False when the window was not created with tkdnd: the rest works the same."""
        if not hasattr(self.root, "drop_target_register"):
            return False
        from tkinterdnd2 import DND_FILES

        # Tk areas are separate native windows, so each large one is registered, and the
        # main window for the rest. One drop can then run more than one handler (an
        # area's, then its toplevel's): `_on_drop` handles it once.
        # tkinterdnd2 adds drop_target_register/dnd_bind at run time.
        areas: tuple[Any, ...] = (
            self.root, self.canvas, self.info, self.raw, self.preview, self.history, self.tree,
        )  # fmt: skip
        for widget in areas:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        return True

    def _on_drop(self, event: Any) -> str:
        if not self._drop_handled:
            self._drop_handled = True  # until this event dispatch is over
            self.root.after_idle(self._end_drop)
            self.handle_drop([str(p) for p in self.root.tk.splitlist(event.data)])
        return str(event.action)

    def _end_drop(self) -> None:
        self._drop_handled = False

    def handle_drop(self, paths: list[str]) -> None:
        """One document: convert it and show it. A folder, or several items: convert them
        as a batch (resumable, with progress and a report)."""
        if self.job is not None or self.batch is not None:
            self.status.set("Hay una conversión en curso: espera a que termine o cancélala.")
            return
        usable = [p for p in paths if Path(p).is_dir() or Path(p).suffix.lower() in _DROPPABLE]
        if not usable:
            names = ", ".join(Path(p).name for p in paths[:3])
            messagebox.showinfo(
                "tomd",
                f"No se puede convertir: {names}.\n\nSuelta archivos PDF o EPUB, páginas de "
                "Wikipedia guardadas (.html) o carpetas que los contengan.",
            )
            return
        if len(usable) == 1 and Path(usable[0]).is_file():
            self.source.set(usable[0])
            self.start_conversion()
        else:
            self._start_batch(usable)

    def _poll_batch(self) -> None:
        batch = self.batch
        if batch is None:
            return
        for event in batch.events():
            if isinstance(event, BatchLine):
                self._batch_progress(event.text)
            elif isinstance(event, BatchEnded):
                self._batch_finished()
                self._batch_done(batch, event)
                return
            elif isinstance(event, Failed):
                self._batch_finished()
                self.status.set("La conversión de la carpeta falló.")
                messagebox.showerror("Fallo interno", event.message)
                return
        self.root.after(_POLL_MS, self._poll_batch)

    def _batch_progress(self, line: str) -> None:
        match = _BATCH_STEP.match(line)
        if match is None:
            self.status.set(line)
            return
        done, total = int(match.group(1)), int(match.group(2))
        if str(self.progress.cget("mode")) != "determinate":
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=total)
        self.progress.configure(value=done)
        self.status.set(f"{done} de {total}: {line.split(maxsplit=3)[-1]}")

    def _batch_done(self, batch: BatchJob, ended: BatchEnded) -> None:
        done = ended.statuses.get("done", 0)
        failed = sum(n for status, n in ended.statuses.items() if status != "done")
        if not ended.planned:
            self.status.set("Nada nuevo que convertir en esa carpeta: todo estaba ya convertido.")
        else:
            self.status.set(f"Carpeta terminada: {done} convertidos, {failed} con problemas.")
        if ended.report:
            report = Path(ended.report)
            request = ConversionRequest(batch.sources[0], report.parent)
            review = Review.from_result(request, report, (), None)
            self._add_review(review)
            self.history.delete("end")
            self.history.insert("end", f"Informe: {batch.label}")
            self.history.selection_set("end")
        messagebox.showinfo(
            "Carpeta terminada",
            f"{done} documento(s) convertido(s)"
            + (f", {failed} con problemas (ver el informe)" if failed else "")
            + f".\n\nLos archivos están en:\n{self.out_dir.get()}",
        )

    def _batch_finished(self) -> None:
        self.batch = None
        self.progress.stop()
        self.progress.configure(mode="indeterminate", value=0)
        for button in (self.convert_button, self.folder_button):
            button.state(["!disabled"])
        self.cancel_button.state(["disabled"])

    def _poll(self) -> None:
        job = self.job
        if job is None:
            return
        outcome = job.poll()
        if outcome is None:
            self.root.after(_POLL_MS, self._poll)
            return
        self._job_finished()
        if isinstance(outcome, Succeeded):
            result = outcome.result
            review = Review.from_result(
                job.request, result.markdown_path, result.diagnostics, result.document
            )
            self._add_review(review)
            counts = {s: sum(r.severity == s for r in review.rows()) for s in _SEVERITY_COLOURS}
            self.status.set(
                f"Escrito {result.markdown_path} — {counts['error']} errores, "
                f"{counts['warning']} avisos, {counts['info']} informativos."
            )
        elif isinstance(outcome, Failed):
            self.status.set(f"Error ({outcome.kind}).")
            title = "Fallo interno" if outcome.kind == "crash" else "No se pudo convertir"
            messagebox.showerror(title, outcome.message)

    def _job_finished(self) -> None:
        self.job = None
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.convert_button.state(["!disabled"])
        self.cancel_button.state(["disabled"])

    def open_markdown(self) -> None:
        path = filedialog.askopenfilename(
            title="Markdown generado por tomd", filetypes=[("Markdown", "*.md")]
        )
        if not path:
            return
        review = Review.from_result(
            ConversionRequest(path, Path(path).parent), Path(path), (), None
        )
        original = self._guess_original(Path(path), review.markdown)
        if original is not None:
            review.request = ConversionRequest(str(original), Path(path).parent)
        self._add_review(review)
        self.status.set(
            "Revisando un .md existente: sin diagnósticos ni vista previa "
            "(solo están para conversiones de esta sesión)."
        )

    @staticmethod
    def _guess_original(markdown_path: Path, markdown: str) -> Path | None:
        """The source PDF if it sits next to the output folder under its recorded name."""
        for line in markdown.splitlines()[1:12]:
            if line.startswith("source: "):
                name = line.removeprefix("source: ").strip().strip('"')
                for folder in (markdown_path.parent, markdown_path.parent.parent):
                    candidate = folder / name
                    if candidate.suffix.lower() == ".pdf" and candidate.is_file():
                        return candidate
        return None

    # --- Showing a review ----------------------------------------------------------------

    def _add_review(self, review: Review) -> None:
        self.reviews.append(review)
        self.history.insert("end", review.title)
        self.history.selection_clear(0, "end")
        self.history.selection_set("end")
        self.load(review)

    def _history_selected(self) -> None:
        selection = self.history.curselection()  # type: ignore[no-untyped-call]
        if selection and self.reviews[selection[0]] is not self.review:
            self.load(self.reviews[selection[0]])

    def load(self, review: Review) -> None:
        self.review = review
        self._highlight = None
        self._load_original(review)
        self._fill_raw(review.markdown)
        self._fill_preview(review)
        self._fill_diagnostics(review)
        self.show_page(1)

    def _load_original(self, review: Review) -> None:
        if self.pdf is not None:
            self.pdf.close()
            self.pdf = None
        self.info.place_forget()
        if review.request.is_pdf:
            try:
                self.pdf = pdfium.PdfDocument(review.request.source)
            except pdfium.PdfiumError:
                self.pdf = None
        if self.pdf is None:
            self._show_info(review)

    def _show_info(self, review: Review) -> None:
        self.canvas.delete("all")
        self.page_label.set("—")
        meta = review.document.meta if review.document else None
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        if meta and meta.source_type == "wikipedia":
            self.info.insert(
                "end",
                f"{meta.title}\n\n{meta.source}\nRevisión {meta.revision_id} · {meta.license}\n\n",
            )
            link = ttk.Button(
                self.info,
                text="Abrir en el navegador",
                command=lambda: webbrowser.open(meta.source),
            )
            self.info.window_create("end", window=link)
        elif meta and meta.source_type == "epub":
            lines = [meta.title or "(sin título)", ""]
            if meta.authors:
                lines.append("Autoría: " + ", ".join(meta.authors))
            if meta.language:
                lines.append(f"Idioma: {meta.language}")
            if meta.license:
                lines.append(f"Derechos: {meta.license}")
            lines += [
                "",
                f"Archivo: {review.request.source}",
                "",
                "Un EPUB no tiene páginas: la vista previa muestra el libro convertido.",
            ]
            self.info.insert("end", "\n".join(lines))
        else:
            self.info.insert(
                "end",
                "No hay vista del original para esta conversión.\n\n"
                f"Origen: {review.request.source}",
            )
        self.info.configure(state="disabled")
        self.info.place(relx=0, rely=0, relwidth=1, relheight=1)

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
        if review.document is None:
            p.insert(
                "end",
                "La vista previa solo está disponible para conversiones hechas en "
                "esta sesión. La pestaña Markdown muestra el archivo.",
            )
            return
        assets = {a.sha256: a for a in review.document.assets}
        for run in preview_runs(review.document):
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
        for tag in tags:  # the page tag lets "sync" scroll the preview to this image
            self.preview.tag_add(tag, index)

    def _fill_diagnostics(self, review: Review) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        self.advice.set("")
        for row in review.rows():
            item = self.tree.insert(
                "",
                "end",
                values=(row.severity, row.code, row.page or "", row.message),
                tags=(row.severity,),
            )
            self._rows[item] = row

    # --- Original view -------------------------------------------------------------------

    def show_page(self, number: int, *, follow: bool = True) -> None:
        if self.pdf is None:
            return
        self.page = max(1, min(number, len(self.pdf)))
        self.page_label.set(f"{self.page} / {len(self.pdf)}")
        self._render_page()
        if follow and self.sync.get() and self.review is not None:
            line = self.review.line_for_page(self.page)
            if line is not None:
                self._scroll_markdown(line)
            if self.preview.tag_ranges(f"page-{self.page}"):
                self.preview.see(f"page-{self.page}.first")

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
            available = max(self.canvas.winfo_width() - 24, 200)
            scale = available / width * _ZOOMS[self.zoom_index]
            image = page.render(scale=scale).to_pil()
        finally:
            page.close()
        self._page_image = ImageTk.PhotoImage(image)
        self._scale, self._view_height = scale, image.height + 24
        self.canvas.delete("all")
        self.canvas.create_image(12, 12, image=self._page_image, anchor="nw")
        if self._highlight and self._highlight[0] == self.page:
            x0, y0, x1, y1 = self._highlight[1]
            self.canvas.create_rectangle(
                12 + x0 * scale - 3,
                12 + (height - y1) * scale - 3,
                12 + x1 * scale + 3,
                12 + (height - y0) * scale + 3,
                outline=_HIGHLIGHT,
                width=3,
            )
        self.canvas.configure(scrollregion=(0, 0, image.width + 24, image.height + 24))

    def _point_at(self, page: int, bbox: tuple[float, float, float, float] | None) -> None:
        self._highlight = (page, bbox) if bbox else None
        self.show_page(page, follow=False)
        if bbox and self.pdf is not None and self._view_height:
            height = self.pdf.get_page_size(page - 1)[1]
            top = 12 + (height - bbox[3]) * self._scale  # canvas y of the block's top
            # Leave a little context above the highlighted block.
            self.canvas.yview_moveto(max(0.0, (top - 60) / self._view_height))

    # --- Markdown view -------------------------------------------------------------------

    def _scroll_markdown(self, line: int) -> None:
        self.raw.tag_remove("current", "1.0", "end")
        self.raw.tag_add("current", f"{line}.0", f"{line}.end")
        self.raw.see(f"{line}.0")

    def _markdown_clicked(self) -> None:
        if self.review is None or not self.sync.get():
            return
        line = int(self.raw.index("insert").split(".")[0])
        anchor = self.review.anchor_for_line(line)
        if anchor is not None:
            self._point_at(anchor.page, anchor.bbox)

    def find_next(self) -> None:
        needle = self.find_text.get()
        self.raw.tag_remove("found", "1.0", "end")
        if not needle:
            return
        self.tabs.select(0)  # type: ignore[no-untyped-call]
        start = self.raw.index("insert +1c")
        where = self.raw.search(needle, start, nocase=True, stopindex="end") or self.raw.search(
            needle, "1.0", nocase=True, stopindex="end"
        )
        if not where:
            self.status.set(f"«{needle}» no aparece en el Markdown.")
            return
        end = f"{where}+{len(needle)}c"
        self.raw.tag_add("found", where, end)
        self.raw.mark_set("insert", end)
        self.raw.see(where)

    # --- Diagnostics ---------------------------------------------------------------------

    def _diagnostic_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        row = self._rows[selection[0]]
        self.advice.set(f"Qué hacer: {row.action}" if row.action else "")
        if row.page is not None:
            self._point_at(row.page, row.bbox)
            if self.review is not None:
                line = self.review.line_for_page(row.page)
                if line is not None:
                    self._scroll_markdown(line)

    def close(self) -> None:
        if self.job is not None:
            self.job.cancel()
        if self.batch is not None:
            self.batch.cancel()
        if self.pdf is not None:
            self.pdf.close()
        self.root.destroy()


# Keys that move or select without editing the read-only text views.
_NAV_KEYS = frozenset(
    {
        "Left",
        "Right",
        "Up",
        "Down",
        "Home",
        "End",
        "Prior",
        "Next",
        "Shift_L",
        "Shift_R",
        "Control_L",
        "Control_R",
    }
)


# What a drop may contain besides folders.
_DROPPABLE = frozenset({".pdf", ".epub", ".html", ".htm"})
_BATCH_STEP = re.compile(r"^\[(\d+)/(\d+)\]")


def available_backends() -> tuple[str, ...]:
    """PDF backends that can run here: docling only when its optional extra is installed
    (the packaged app does not include it)."""
    import importlib.util

    return ("heuristic", "docling") if importlib.util.find_spec("docling") else ("heuristic",)


def default_out_dir() -> Path:
    """Where conversions go: `out/` next to the work when running from source; in the
    installed app (frozen), Documents\\tomd, which a non-technical user can find."""
    if getattr(sys, "frozen", False):
        documents = Path.home() / "Documents"
        return (documents if documents.is_dir() else Path.home()) / "tomd"
    return Path.cwd() / "out"


def _read_only_key(event: "tk.Event[tk.Text]") -> str | None:
    control = isinstance(event.state, int) and bool(event.state & 4)
    return None if control or event.keysym in _NAV_KEYS else "break"


def enable_dpi_awareness() -> None:
    """On Windows, draw at the screen's real resolution; without this, a scaled display
    (125 %, 150 %) stretches the window as a bitmap and the text looks blurred."""
    if sys.platform != "win32":
        return
    import contextlib
    import ctypes

    with contextlib.suppress(AttributeError, OSError):  # older Windows: stay unaware
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # per-monitor aware (Windows 8.1+)


def make_root() -> tk.Tk:
    """The main window, with drag and drop when tkdnd can load; a plain Tk otherwise."""
    try:
        from tkinterdnd2 import TkinterDnD

        root: tk.Tk = TkinterDnD.Tk()
        return root
    except (ImportError, RuntimeError, tk.TclError):
        return tk.Tk()


def run(source: str | None = None, out_dir: Path | None = None) -> int:
    enable_dpi_awareness()
    root = make_root()
    app = TomdApp(root, out_dir)
    app.enable_drop()
    if source:
        app.source.set(source)
    root.mainloop()
    return 0

"""Desktop app to convert documents and review them (Tkinter, standard library).

Two screens (design D): the library, every document with its state (Convirtiendo, Por
revisar, Revisado, Listo, No convertido) and what to review; and the review of one
document, the original beside the result with a checklist. Every conversion goes
through the batch, so the library is the batch manifest and survives restarts.
"""

import contextlib
import json
import os
import re
import subprocess
import sys
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from tomd import __version__
from tomd.pipeline.batch import MANIFEST_NAME
from tomd.pipeline.manifest import Finding, LibraryEntry, Manifest
from tomd.readers.pdf.reader import DEFAULT_BACKEND
from tomd.ui import theme
from tomd.ui.job import BatchEnded, BatchJob, BatchLine, BatchOptionsUI, Failed
from tomd.ui.library import LibraryActions, LibraryView
from tomd.ui.review import ReviewActions, ReviewView
from tomd.ui.session import Review
from tomd.ui.texts import failure_text
from tomd.ui.theme import PALETTE

_POLL_MS = 150
# While a batch runs the library is re-read at most this often: each read is a query on
# the manifest the batch is writing, and thousands of rows need not redraw per document.
_REFRESH_MS = 1000
# What a drop may contain besides folders.
_DROPPABLE = frozenset({".pdf", ".epub", ".html", ".htm"})
_BATCH_STEP = re.compile(r"^\[(\d+)/(\d+)\]")
_OCR_CHOICES = (
    ("auto", "Cuando haga falta", "Lee como imagen solo las páginas escaneadas. Recomendado."),
    ("always", "Siempre", "Lee todas las páginas como imagen. Más lento; para PDF con "
     "texto dañado."),
    ("never", "Nunca", "Más rápido; las páginas escaneadas quedan vacías."),
)  # fmt: skip


class _Pending:
    """A batch waiting for the one in progress (only one runs at a time)."""

    def __init__(self, sources: list[str], options: BatchOptionsUI, open_after: bool) -> None:
        self.sources = sources
        self.options = options
        self.open_after = open_after


class TomdApp:
    def __init__(
        self, root: tk.Tk, out_dir: Path | None = None, settings_path: Path | None = None
    ) -> None:
        self.root = root
        root.title("tomd")
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            root.iconbitmap(default=sys.executable)  # type: ignore[no-untyped-call]
        # Sized from the screen: with DPI awareness, fixed pixel sizes are tiny on scaled
        # displays.
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width, height = int(screen_w * 0.86), int(screen_h * 0.84)
        root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{(screen_h - height) // 3}")
        root.minsize(int(screen_w * 0.5), int(screen_h * 0.5))
        self.fonts = theme.apply(root)

        self.settings_path = settings_path
        settings = self._load_settings()
        saved = settings.get("out_dir")
        self.out_dir = out_dir or (Path(saved) if isinstance(saved, str) else default_out_dir())
        self.ocr = str(settings.get("ocr") or "auto")
        backend = str(settings.get("backend") or DEFAULT_BACKEND)
        self.backend = backend if backend in available_backends() else DEFAULT_BACKEND
        self.version = __version__
        self.manifest = Manifest(self.out_dir / MANIFEST_NAME)
        self.entries: list[LibraryEntry] = []

        self.batch: BatchJob | None = None
        self.batch_open_after = False
        self.queue: list[_Pending] = []
        self._refresh_due = False
        self._drop_handled = False
        self.review: Review | None = None

        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.show_library()

    # --- Layout ----------------------------------------------------------------------

    def _build(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.sidebar = self._sidebar()
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.content = ttk.Frame(self.root)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        library_actions = LibraryActions()
        library_actions.add_files = self.add_files
        library_actions.add_folder = self.add_folder
        library_actions.add_article = self.add_article
        library_actions.open_entry = self.open_entry
        library_actions.review_pending = self.review_pending
        library_actions.show_in_folder = self.show_in_folder
        library_actions.retry = self.retry
        library_actions.stop = self.stop
        self.library = LibraryView(self.content, self.fonts, library_actions)
        self.library.grid(row=0, column=0, sticky="nsew")

        review_actions = ReviewActions()
        review_actions.back = self.show_library
        review_actions.open_markdown = self.open_markdown
        review_actions.reconvert_ocr = self.reconvert_ocr
        review_actions.next_pending = self.next_pending
        review_actions.set_checked = self.set_checked
        review_actions.set_reviewed = self.set_reviewed
        review_actions.preview_options = lambda: (self.backend, self.ocr)
        self.review_view = ReviewView(self.content, self.fonts, review_actions)
        self.review_view.grid(row=0, column=0, sticky="nsew")

        self.status = tk.StringVar(value="")
        self.status_bar = ttk.Label(self.root, textvariable=self.status, style="Muted.TLabel",
                                    padding=(24, 4, 24, 8))  # fmt: skip
        self.status_bar.grid(row=1, column=1, sticky="ew")
        self.root.bind("<Control-o>", lambda _e: self.add_files())

    def _sidebar(self) -> ttk.Frame:
        bar = ttk.Frame(self.root, style="Sidebar.TFrame", padding=(14, 18))
        brand = ttk.Frame(bar, style="Sidebar.TFrame")
        brand.pack(fill="x", pady=(0, 22), padx=4)
        ttk.Label(brand, text="md", style="Logo.TLabel").pack(side="left")
        ttk.Label(brand, text="tomd", style="Brand.TLabel").pack(side="left", padx=(8, 0))
        self.nav: dict[str, ttk.Button] = {}
        for key, command in (
            ("all", lambda: self.show_library("all")),
            ("to_review", lambda: self.show_library("to_review")),
            ("failed", lambda: self.show_library("failed")),
        ):
            button = ttk.Button(bar, text="", style="Nav.TButton", command=command, width=20)
            button.pack(fill="x", pady=1)
            self.nav[key] = button
        ttk.Separator(bar).pack(fill="x", pady=12)
        ttk.Button(bar, text="Opciones", style="Nav.TButton", command=self.show_options).pack(
            fill="x", pady=1
        )
        ttk.Button(bar, text="Abrir la carpeta de resultados", style="Nav.TButton",
                   command=self.open_out_dir).pack(fill="x", pady=1)  # fmt: skip
        ttk.Label(bar, text=f"Versión {__version__}", style="Surface.Muted.TLabel").pack(
            side="bottom", anchor="w", padx=12
        )
        return bar

    def _nav_labels(self) -> None:
        counts = {key: sum(e.state == key for e in self.entries) for key in ("to_review", "failed")}
        labels = {
            "all": f"Biblioteca   {len(self.entries)}",
            "to_review": f"Por revisar   {counts['to_review']}",
            "failed": f"No convertidos   {counts['failed']}",
        }
        on_library = self.review is None
        for key, button in self.nav.items():
            active = on_library and self.library.filter == key
            button.configure(
                text=labels[key], style="NavActive.TButton" if active else "Nav.TButton"
            )

    # --- Screens ---------------------------------------------------------------------

    def show_library(self, filter_key: str | None = None) -> None:
        self.review_view.stop_preview()
        self.review = None
        if filter_key is not None:
            self.library.set_filter(filter_key)
        self.refresh()
        self.library.tkraise()
        self.library.tree.focus_set()
        self._nav_labels()

    def refresh(self) -> None:
        self.entries = self.manifest.library(self.version)
        self.library.update_entries(self.entries, self.out_dir)
        self._nav_labels()

    def _entry(self, sha256: str) -> LibraryEntry | None:
        return next((e for e in self.entries if e.sha256 == sha256), None)

    def open_entry(self, entry: LibraryEntry) -> None:
        if entry.state in ("failed", "converting"):
            return
        review = Review.load(self.manifest, self.version, entry, self.out_dir)
        if not review.markdown:
            messagebox.showwarning(
                "tomd", "No se encuentra el archivo .md de este documento en la carpeta de "
                "resultados. Puedes volver a convertirlo desde la biblioteca."
            )  # fmt: skip
            return
        self.review = review
        self.library.select(entry.sha256)
        self.review_view.load(review)
        self.review_view.set_has_next(self._next_pending(entry.sha256) is not None)
        self.review_view.tkraise()
        self._nav_labels()

    def _next_pending(self, after: str | None) -> LibraryEntry | None:
        """The next document to review, in library order, after `after` (wrapping)."""
        pending = [e for e in self.entries if e.state == "to_review" and e.sha256 != after]
        if not pending:
            return None
        order = [e.sha256 for e in self.entries]
        if after in order:
            start = order.index(after)
            later = [e for e in pending if order.index(e.sha256) > start]
            if later:
                return later[0]
        return pending[0]

    def review_pending(self) -> None:
        entry = self._next_pending(None)
        if entry is not None:
            self.open_entry(entry)

    def next_pending(self) -> None:
        current = self.review.entry.sha256 if self.review else None
        entry = self._next_pending(current)
        if entry is not None:
            self.open_entry(entry)
        else:
            self.show_library()

    # --- Review actions --------------------------------------------------------------

    def set_checked(self, review: Review, finding: Finding, checked: bool) -> None:
        self.manifest.set_checked(review.entry.sha256, self.version, finding.idx, checked)
        review.findings = [
            replace(f, checked=checked) if f.idx == finding.idx else f for f in review.findings
        ]
        self.refresh()

    def set_reviewed(self, review: Review, reviewed: bool) -> None:
        """Marking is allowed with items unchecked: the person decides it is good enough."""
        sha = review.entry.sha256
        self.manifest.set_reviewed(sha, self.version, reviewed)
        self.refresh()
        entry = self._entry(sha)
        if entry is not None:
            review.entry = entry
        if not reviewed:
            self.review_view._show_state()
            self.status.set("Se quitó la marca de revisado.")
            return
        self.status.set(f"«{review.title}» marcado como revisado.")
        following = self._next_pending(sha)
        if following is not None:
            self.open_entry(following)
        else:
            self.show_library()
            self.library.select(sha)

    def open_markdown(self, review: Review) -> None:
        _open_path(review.markdown_path)

    def reconvert_ocr(self, review: Review) -> None:
        if not messagebox.askokcancel(
            "Volver a convertir con OCR",
            "Se leerán todas las páginas como imagen. Tarda más, pero arregla el texto que "
            "sale como símbolos raros.\n\nLas casillas marcadas de este documento se "
            "reinician.",
        ):
            return
        options = BatchOptionsUI(str(self.out_dir), ocr="always", backend=self.backend, force=True)
        self.start_batch([review.entry.source], options, open_after=True)

    # --- Adding documents ------------------------------------------------------------

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Añadir documentos",
            filetypes=[("PDF, EPUB y páginas de Wikipedia", "*.pdf *.epub *.html *.htm"),
                       ("Todos los archivos", "*.*")],
        )  # fmt: skip
        if paths:
            self.add([str(p) for p in paths])

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Añadir una carpeta", mustexist=True)
        if folder:
            self.add([folder])

    def add_article(self) -> None:
        source = ask_article(self.root, self.fonts)
        if source:
            self.add([source], articles=True)

    def add(self, sources: list[str], *, articles: bool = False) -> None:
        """Convert documents, folders or (with `articles`) Wikipedia references. One file
        opens its review when done; several fill the library."""
        if not articles:
            usable = [
                s for s in sources if Path(s).is_dir() or Path(s).suffix.lower() in _DROPPABLE
            ]
            if not usable:
                names = ", ".join(Path(p).name for p in sources[:3])
                messagebox.showinfo(
                    "tomd", f"No se puede convertir: {names}.\n\nAñade archivos PDF o EPUB, "
                    "páginas de Wikipedia guardadas (.html) o carpetas que los contengan.",
                )  # fmt: skip
                return
            sources = usable
        single = len(sources) == 1 and (articles or Path(sources[0]).is_file())
        options = BatchOptionsUI(str(self.out_dir), ocr=self.ocr, backend=self.backend)
        self.start_batch(sources, options, open_after=single)

    def retry(self, entry: LibraryEntry) -> None:
        options = BatchOptionsUI(str(self.out_dir), ocr=self.ocr, backend=self.backend,
                                 retry_failed=True)  # fmt: skip
        self.start_batch([entry.source], options, open_after=False)

    # --- Batches ---------------------------------------------------------------------

    def start_batch(self, sources: list[str], options: BatchOptionsUI, *, open_after: bool) -> None:
        if self.batch is not None:
            self.queue.append(_Pending(sources, options, open_after))
            self.status.set("Se convertirá cuando termine lo que está en curso.")
            return
        self.batch = BatchJob(sources, options)
        self.batch_open_after = open_after
        self.library.show_progress(self.batch.label, None, None)
        self.status.set("")
        self.root.after(_POLL_MS, self._poll_batch)

    def stop(self) -> None:
        self.queue.clear()
        if self.batch is not None:
            self.batch.cancel()
            self._batch_finished()
            self.status.set("Detenido. Lo ya convertido se conserva; al añadir lo mismo de "
                            "nuevo, sigue donde lo dejó.")  # fmt: skip

    def _poll_batch(self) -> None:
        batch = self.batch
        if batch is None:
            return
        for event in batch.events():
            if isinstance(event, BatchLine):
                self._batch_progress(batch, event.text)
            elif isinstance(event, BatchEnded):
                self._batch_finished()
                self._batch_done(event)
                return
            elif isinstance(event, Failed):
                self._batch_finished()
                title = "Fallo interno" if event.kind == "crash" else "No se pudo convertir"
                messagebox.showerror(title, event.message)
                return
        self.root.after(_POLL_MS, self._poll_batch)

    def _batch_progress(self, batch: BatchJob, line: str) -> None:
        match = _BATCH_STEP.match(line)
        if match is None:
            self.status.set(line)
            return
        done, total = int(match.group(1)), int(match.group(2))
        self.library.show_progress(batch.label, done, total)
        if not self._refresh_due:
            self._refresh_due = True
            self.root.after(_REFRESH_MS, self._refresh_during_batch)

    def _refresh_during_batch(self) -> None:
        self._refresh_due = False
        if self.review is None:
            self.refresh()

    def _batch_finished(self) -> None:
        self.batch = None
        self.library.hide_progress()

    def _batch_done(self, ended: BatchEnded) -> None:
        self.refresh()
        done = ended.statuses.get("done", 0)
        failed = sum(n for status, n in ended.statuses.items() if status != "done")
        if not ended.planned:
            self.status.set("Ya estaba todo convertido.")
        else:
            self.status.set(f"Terminado: {done} convertido(s)"
                            + (f", {failed} sin convertir" if failed else "") + ".")  # fmt: skip
        if self.batch_open_after and len(ended.sources) == 1:
            entry = self._entry_for_source(ended.sources[0])
            if entry is not None and entry.state == "failed":
                messagebox.showwarning("No se pudo convertir", failure_text(entry.error))
            elif entry is not None:
                self.open_entry(entry)
        if self.queue:
            pending = self.queue.pop(0)
            self.start_batch(pending.sources, pending.options, open_after=pending.open_after)

    def _entry_for_source(self, source: str) -> LibraryEntry | None:
        target = _resolved(source)
        return next((e for e in self.entries if _resolved(e.source) == target), None)

    # --- Drag and drop ---------------------------------------------------------------

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
        view = self.review_view
        areas: tuple[Any, ...] = (
            self.root, self.library.tree, self.library.zone, view.canvas, view.about,
            view.raw, view.preview,
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
        self.add(paths)

    # --- Options and folders ---------------------------------------------------------

    def show_options(self) -> None:
        dialog = OptionsDialog(self.root, self.fonts, self.ocr, self.backend, self.out_dir)
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        ocr, backend, out_dir = dialog.result
        self.ocr, self.backend = ocr, backend
        if out_dir != self.out_dir:
            if self.batch is not None:
                messagebox.showinfo(
                    "tomd", "La carpeta de resultados no se puede cambiar durante una "
                    "conversión. Vuelve a elegirla cuando termine.",
                )  # fmt: skip
            else:
                self.set_out_dir(out_dir)
        self._save_settings()

    def set_out_dir(self, out_dir: Path) -> None:
        self.manifest.close()
        self.out_dir = out_dir
        self.manifest = Manifest(out_dir / MANIFEST_NAME)
        self.show_library("all")

    def open_out_dir(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        _open_path(self.out_dir)

    def show_in_folder(self, entry: LibraryEntry) -> None:
        path = self.out_dir / (entry.output or "")
        if sys.platform == "win32" and path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            _open_path(path.parent if path.is_file() else self.out_dir)

    def _load_settings(self) -> dict[str, object]:
        if self.settings_path is None:
            return {}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_settings(self) -> None:
        if self.settings_path is None:
            return
        data = {"out_dir": str(self.out_dir), "ocr": self.ocr, "backend": self.backend}
        with contextlib.suppress(OSError):
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def close(self) -> None:
        if self.batch is not None:
            if not messagebox.askokcancel(
                "Salir de tomd", "Hay una conversión en curso. Si sales se detiene; lo ya "
                "convertido se conserva y al volver puedes seguir donde lo dejaste."
            ):  # fmt: skip
                return
            self.batch.cancel()
        self.review_view.close()
        self.manifest.close()
        self.root.destroy()


class OptionsDialog(tk.Toplevel):
    """OCR, the results folder and, under «Avanzado», the PDF engine."""

    def __init__(self, parent: tk.Misc, fonts: theme.Fonts, ocr: str, backend: str,
                 out_dir: Path) -> None:  # fmt: skip
        super().__init__(parent)
        self.title("Opciones")
        self.configure(background=PALETTE.surface)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.result: tuple[str, str, Path] | None = None
        self.ocr = tk.StringVar(value=ocr)
        self.backend = tk.StringVar(value=backend)
        self.out_dir = tk.StringVar(value=str(out_dir))
        body = ttk.Frame(self, style="Surface.TFrame", padding=24)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Opciones", style="Surface.Title.TLabel").pack(anchor="w")

        ttk.Label(body, text="Leer páginas escaneadas (OCR)", style="Surface.Strong.TLabel").pack(
            anchor="w", pady=(18, 6)
        )
        for value, label, help_text in _OCR_CHOICES:
            ttk.Radiobutton(body, text=label, value=value, variable=self.ocr).pack(anchor="w")
            ttk.Label(body, text=help_text, style="Surface.Muted.TLabel").pack(
                anchor="w", padx=(26, 0), pady=(0, 6)
            )

        ttk.Label(body, text="Carpeta de resultados", style="Surface.Strong.TLabel").pack(
            anchor="w", pady=(14, 6)
        )
        row = ttk.Frame(body, style="Surface.TFrame")
        row.pack(fill="x")
        ttk.Label(row, textvariable=self.out_dir, style="Surface.TLabel").pack(side="left")
        ttk.Button(row, text="Cambiar…", command=self._choose).pack(side="right", padx=(12, 0))
        ttk.Label(body, text="Cada carpeta tiene su propia biblioteca.",
                  style="Surface.Muted.TLabel").pack(anchor="w", pady=(4, 0))  # fmt: skip

        backends = available_backends()
        if len(backends) > 1:
            ttk.Label(body, text="Avanzado · motor para PDF", style="Surface.Strong.TLabel").pack(
                anchor="w", pady=(14, 6)
            )
            ttk.Combobox(body, textvariable=self.backend, values=backends, state="readonly",
                         width=16).pack(anchor="w")  # fmt: skip

        buttons = ttk.Frame(body, style="Surface.TFrame")
        buttons.pack(fill="x", pady=(22, 0))
        ttk.Button(buttons, text="Guardar", style="Primary.TButton", command=self._save).pack(
            side="right"
        )
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right", padx=8)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._save())
        self.grab_set()

    def _choose(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Carpeta de resultados",
                                         mustexist=False)  # fmt: skip
        if folder:
            self.out_dir.set(folder)

    def _save(self) -> None:
        self.result = (self.ocr.get(), self.backend.get(), Path(self.out_dir.get()))
        self.destroy()


def ask_article(parent: tk.Misc, fonts: theme.Fonts) -> str | None:
    """A Wikipedia article as `lang:Title` (or its address), checked before converting."""
    from tomd.errors import UnsupportedSourceError
    from tomd.readers.wikipedia.client import parse_article_ref

    dialog = tk.Toplevel(parent)
    dialog.title("Artículo de Wikipedia")
    dialog.configure(background=PALETTE.surface)
    dialog.transient(parent.winfo_toplevel())
    dialog.resizable(False, False)
    result: list[str] = []
    text = tk.StringVar()
    language = tk.StringVar(value="es")
    error = tk.StringVar()
    body = ttk.Frame(dialog, style="Surface.TFrame", padding=24)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="Artículo de Wikipedia", style="Surface.Title.TLabel").pack(anchor="w")
    ttk.Label(body, text="Pega la dirección del artículo, o escribe su título.",
              style="Surface.Muted.TLabel").pack(anchor="w", pady=(4, 12))  # fmt: skip
    entry = ttk.Entry(body, textvariable=text, width=56, font=fonts(10))
    entry.pack(fill="x")
    row = ttk.Frame(body, style="Surface.TFrame")
    row.pack(fill="x", pady=(10, 0))
    ttk.Label(row, text="Idioma (si escribes el título)", style="Surface.TLabel").pack(side="left")
    ttk.Combobox(row, textvariable=language, values=("es", "en", "ca", "gl", "eu", "pt", "fr",
                 "de", "it"), width=6).pack(side="left", padx=8)  # fmt: skip
    ttk.Label(body, textvariable=error, style="Surface.TLabel", foreground=PALETTE.error_ink,
              wraplength=440).pack(anchor="w", pady=(8, 0))  # fmt: skip
    ttk.Label(body, text="Es lo único que tomd descarga de internet: el artículo, de la "
              "API de Wikipedia.", style="Surface.Muted.TLabel", wraplength=440,
              justify="left").pack(anchor="w", pady=(4, 0))  # fmt: skip

    def accept() -> None:
        value = text.get().strip()
        if not value:
            return
        if not value.lower().startswith(("http://", "https://")) and not re.match(
            r"^[a-z\-]{2,12}:", value
        ):
            value = f"{language.get().strip() or 'es'}:{value}"
        try:
            parse_article_ref(value)
        except UnsupportedSourceError:
            error.set("No parece un artículo de Wikipedia. Prueba con la dirección completa, "
                      "por ejemplo https://es.wikipedia.org/wiki/Markdown")  # fmt: skip
            return
        result.append(value)
        dialog.destroy()

    buttons = ttk.Frame(body, style="Surface.TFrame")
    buttons.pack(fill="x", pady=(18, 0))
    ttk.Button(buttons, text="Convertir", style="Primary.TButton", command=accept).pack(
        side="right"
    )
    ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="right", padx=8)
    dialog.bind("<Return>", lambda _e: accept())
    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    entry.focus_set()
    dialog.grab_set()
    parent.wait_window(dialog)
    return result[0] if result else None


def _resolved(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return path


def _open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # Windows only
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


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


def settings_file() -> Path:
    """The app's few settings, in the user's own configuration folder (never the output)."""
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    return Path(base or Path.home() / ".config") / "tomd" / "settings.json"


def enable_dpi_awareness() -> None:
    """On Windows, draw at the screen's real resolution; without this, a scaled display
    (125 %, 150 %) stretches the window as a bitmap and the text looks blurred."""
    if sys.platform != "win32":
        return
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
    app = TomdApp(root, out_dir, settings_file())
    app.enable_drop()
    if source:  # `tomd ui <file or article>`: convert it and open its review
        is_path = Path(source).exists()
        root.after(200, lambda: app.add([source], articles=not is_path))
    root.mainloop()
    return 0

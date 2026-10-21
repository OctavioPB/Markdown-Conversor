"""The library screen: every converted document with its state and what to review
(design D1), and the first-use screen when there is none yet (design D3)."""

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import font, ttk

from tomd.pipeline.manifest import LibraryEntry
from tomd.ui.session import short_date, what_to_review
from tomd.ui.theme import PALETTE, STATES, Fonts

# Filters of the list, in display order: key, label (counts are added when known).
FILTERS = (
    ("all", "Todos"),
    ("to_review", "Por revisar"),
    ("reviewed", "Revisados"),
    ("ready", "Listos"),
    ("failed", "No convertidos"),
)
_KINDS = {".pdf": "PDF", ".epub": "EPUB", ".html": "Wikipedia", ".htm": "Wikipedia"}


class LibraryActions:
    """What the library asks the application to do (set by the app)."""

    add_files: Callable[[], None]
    add_folder: Callable[[], None]
    add_article: Callable[[], None]
    open_entry: Callable[[LibraryEntry], None]
    review_pending: Callable[[], None]
    show_in_folder: Callable[[LibraryEntry], None]
    retry: Callable[[list[LibraryEntry]], None]
    forget: Callable[[list[LibraryEntry]], None]
    stop: Callable[[], None]


class LibraryView(ttk.Frame):
    def __init__(self, parent: tk.Misc, fonts: Fonts, actions: LibraryActions) -> None:
        super().__init__(parent, padding=(28, 22))
        self.fonts = fonts
        self.actions = actions
        self.entries: list[LibraryEntry] = []
        self.filter = "all"
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._fill())
        self._rows: dict[str, LibraryEntry] = {}
        self.columnconfigure(0, weight=1)
        self._header()
        self._progress()
        self._list_area()
        self._empty_area()

    # --- Building --------------------------------------------------------------------

    def _header(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, text="Biblioteca", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.subtitle = ttk.Label(bar, text="", style="Muted.TLabel")
        self.subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))
        buttons = ttk.Frame(bar)
        buttons.grid(row=0, column=1, rowspan=2, sticky="e")
        self.pending_button = ttk.Button(
            buttons, text="Revisar pendientes", style="Warn.TButton",
            command=lambda: self.actions.review_pending(),
        )  # fmt: skip
        self.pending_button.pack(side="left", padx=(0, 10))
        self.add_button = ttk.Button(
            buttons, text="Añadir documentos  ▾", style="Primary.TButton", command=self._add_menu
        )
        self.add_button.pack(side="left")
        self.menu = tk.Menu(self, tearoff=False, font=self.fonts(10))
        self.menu.add_command(
            label="Archivos PDF o EPUB…", command=lambda: self.actions.add_files()
        )
        self.menu.add_command(
            label="Una carpeta entera…", command=lambda: self.actions.add_folder()
        )
        self.menu.add_command(
            label="Un artículo de Wikipedia…", command=lambda: self.actions.add_article()
        )

    def _add_menu(self) -> None:
        x = self.add_button.winfo_rootx()
        y = self.add_button.winfo_rooty() + self.add_button.winfo_height()
        self.menu.tk_popup(x, y)

    def _progress(self) -> None:
        self.strip = ttk.Frame(self, style="Card.TFrame", padding=(16, 12))
        self.strip.columnconfigure(1, weight=1)
        self.strip_label = ttk.Label(self.strip, text="", style="Surface.Strong.TLabel")
        self.strip_label.grid(row=0, column=0, sticky="w", padx=(0, 14))
        self.strip_bar = ttk.Progressbar(
            self.strip, mode="indeterminate", style="Accent.Horizontal.TProgressbar"
        )
        self.strip_bar.grid(row=0, column=1, sticky="ew")
        self.strip_count = ttk.Label(self.strip, text="", style="Surface.Muted.TLabel")
        self.strip_count.grid(row=0, column=2, padx=14)
        ttk.Button(self.strip, text="Detener", command=lambda: self.actions.stop()).grid(
            row=0, column=3
        )

    def _list_area(self) -> None:
        self.list_area = ttk.Frame(self)
        self.list_area.columnconfigure(0, weight=1)
        self.list_area.rowconfigure(1, weight=1)
        tools = ttk.Frame(self.list_area)
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        tools.columnconfigure(1, weight=1)
        ttk.Label(tools, text="Buscar", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.search_entry = ttk.Entry(tools, textvariable=self.search, font=self.fonts(10))
        self.search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 16))
        self.filter_buttons: dict[str, ttk.Button] = {}
        filters = ttk.Frame(tools)
        filters.grid(row=0, column=2, sticky="e")
        for key, label in FILTERS:
            button = ttk.Button(filters, text=label, style="Filter.TButton",
                                command=self._filter_command(key))  # fmt: skip
            button.pack(side="left", padx=(0, 6))
            self.filter_buttons[key] = button

        card = ttk.Frame(self.list_area, style="Card.TFrame", padding=1)
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        columns = ("kind", "state", "review", "date")
        self.tree = ttk.Treeview(
            card, columns=columns, style="Library.Treeview", selectmode="extended"
        )
        char = font.Font(font=self.fonts(10)).measure("0")
        self.tree.heading("#0", text="Documento", anchor="w")
        self.tree.column("#0", width=char * 60, minwidth=char * 16, stretch=True)
        columns_spec: tuple[tuple[str, str, int, bool], ...] = (
            ("kind", "Tipo", 15, False),  # "PDF · 12 partes"
            ("state", "Estado", 15, False),
            ("review", "Qué revisar", 34, True),
            ("date", "Convertido", 12, False),
        )
        for name, title, width, stretch in columns_spec:
            self.tree.heading(name, text=title, anchor="w")
            self.tree.column(
                name, width=char * width, minwidth=char * 6, stretch=stretch, anchor="w"
            )
        for state in STATES:
            self.tree.tag_configure(state, foreground=PALETTE.ink)
        self.tree.tag_configure("to_review", background=PALETTE.warn_row)
        self.tree.tag_configure("failed", background="#FFF7F6")
        self.tree.tag_configure("converting", foreground=PALETTE.muted)
        scroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _e: self._open_selected())
        self.tree.bind("<Return>", lambda _e: self._open_selected())
        self.tree.bind("<Delete>", lambda _e: self._forget_selected())
        self.tree.bind("<Control-a>", self._select_all)
        self.tree.bind("<Control-A>", self._select_all)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._selection_changed())
        self.tree.bind("<Button-3>", self._context_menu)

        actions = ttk.Frame(self.list_area)
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(4, weight=1)
        self.open_button = ttk.Button(actions, text="Abrir", style="Primary.TButton",
                                      command=self._open_selected)  # fmt: skip
        self.open_button.grid(row=0, column=0, padx=(0, 8))
        self.folder_button = ttk.Button(
            actions, text="Mostrar en la carpeta",
            command=lambda: self._with_selected(self.actions.show_in_folder),
        )  # fmt: skip
        self.folder_button.grid(row=0, column=1, padx=(0, 8))
        self.retry_button = ttk.Button(actions, text="Reintentar", command=self._retry_selected)
        self.retry_button.grid(row=0, column=2, padx=(0, 8))
        self.forget_button = ttk.Button(
            actions, text="Quitar de la biblioteca…", command=self._forget_selected
        )
        self.forget_button.grid(row=0, column=3)
        ttk.Label(
            actions, text="Ctrl o Mayús + clic para elegir varios. Suelta archivos o carpetas "
            "en la ventana para añadirlos.", style="Muted.TLabel",
        ).grid(row=0, column=4, sticky="e")  # fmt: skip
        self.context = tk.Menu(self, tearoff=False, font=self.fonts(10))

    def _empty_area(self) -> None:
        """First use (D3): the drop zone takes the screen, with how it works below."""
        self.empty = ttk.Frame(self)
        self.empty.columnconfigure(0, weight=1)
        self.empty.rowconfigure(1, weight=1)
        ttk.Label(self.empty, text="Añade documentos y tomd los convierte a Markdown, listos "
                  "para tu asistente de IA. Nada sale de tu equipo.",
                  style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 16))  # fmt: skip
        self.zone = tk.Canvas(
            self.empty, background=PALETTE.ground, highlightthickness=0, height=300
        )
        self.zone.grid(row=1, column=0, sticky="nsew")
        inner = ttk.Frame(self.zone, style="Surface.TFrame", padding=24)
        ttk.Label(inner, text="⬆", style="Step.Surface.TLabel", font=self.fonts(28, "bold")).pack()
        ttk.Label(inner, text="Suelta aquí archivos PDF o EPUB, o una carpeta entera",
                  style="Big.Surface.TLabel").pack(pady=(6, 14))  # fmt: skip
        buttons = ttk.Frame(inner, style="Surface.TFrame")
        buttons.pack()
        ttk.Button(buttons, text="Elegir archivos", style="Primary.TButton",
                   command=lambda: self.actions.add_files()).pack(side="left", padx=6)  # fmt: skip
        ttk.Button(buttons, text="Elegir carpeta",
                   command=lambda: self.actions.add_folder()).pack(side="left", padx=6)  # fmt: skip
        ttk.Button(inner, text="o un artículo de Wikipedia", style="Link.TButton",
                   command=lambda: self.actions.add_article()).pack(pady=(10, 0))  # fmt: skip
        self._zone_window = self.zone.create_window(0, 0, window=inner)
        self.zone.bind("<Configure>", self._draw_zone)

        steps = ttk.Frame(self.empty)
        steps.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        for column, (title, text) in enumerate((
            ("1 · Añade", "Archivos sueltos o carpetas enteras. Si cierras la app, al volver "
             "sigue donde lo dejó."),
            ("2 · Revisa lo que lo necesite", "tomd marca «Por revisar» lo dudoso y te enseña "
             "el original al lado del resultado."),
            ("3 · Úsalo", "Los archivos .md quedan en la carpeta de resultados, listos para tu "
             "asistente."),
        )):  # fmt: skip
            steps.columnconfigure(column, weight=1, uniform="steps")
            card = ttk.Frame(steps, style="Card.TFrame", padding=16)
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 12, 0))
            ttk.Label(card, text=title, style="Step.Surface.TLabel").pack(anchor="w")
            label = ttk.Label(card, text=text, style="Surface.TLabel", justify="left")
            label.pack(anchor="w", fill="x", pady=(4, 0))
            # Wrap to the card, whatever the window and the display scale.
            card.bind("<Configure>", self._wrap_command(label))

    @staticmethod
    def _wrap_command(label: ttk.Label) -> Callable[["tk.Event[tk.Misc]"], None]:
        def wrap(event: "tk.Event[tk.Misc]") -> None:
            label.configure(wraplength=max(event.width - 40, 100))

        return wrap

    def _draw_zone(self, event: "tk.Event[tk.Canvas]") -> None:
        self.zone.delete("frame")
        width, height = event.width, event.height
        self.zone.create_rectangle(3, 3, width - 3, height - 3, outline="#9AA6B8", width=2,
                                   dash=(8, 6), fill=PALETTE.surface, tags="frame")  # fmt: skip
        self.zone.tag_lower("frame")
        self.zone.coords(self._zone_window, width / 2, height / 2)

    # --- Filling ---------------------------------------------------------------------

    def update_entries(self, entries: list[LibraryEntry], out_dir: Path) -> None:
        self.entries = entries
        counts = {key: sum(e.state == key for e in entries) for key, _ in FILTERS}
        counts["all"] = len(entries)
        for key, label in FILTERS:
            shown = (
                f"{label} · {counts[key]}"
                if key in ("to_review", "failed") and counts[key]
                else label
            )
            self.filter_buttons[key].configure(text=shown)
        pending = counts["to_review"]
        self.pending_button.configure(text=f"Revisar pendientes ({pending})")
        if pending:
            self.pending_button.pack(side="left", padx=(0, 10), before=self.add_button)
        else:
            self.pending_button.pack_forget()
        self.subtitle.configure(
            text=f"{len(entries)} documento(s) en {_short_path(out_dir)}" if entries else ""
        )
        if entries:
            self.empty.grid_forget()
            self.list_area.grid(row=2, column=0, sticky="nsew")
            self.rowconfigure(2, weight=1)
        else:
            self.list_area.grid_forget()
            self.empty.grid(row=2, column=0, sticky="nsew")
            self.rowconfigure(2, weight=1)
        self._fill()

    def _filter_command(self, key: str) -> Callable[[], None]:
        return lambda: self.set_filter(key)

    def set_filter(self, key: str) -> None:
        self.filter = key
        self._fill()

    def _visible(self) -> list[LibraryEntry]:
        needle = self.search.get().strip().casefold()
        return [
            e for e in self.entries
            if (self.filter == "all" or e.state == self.filter)
            and (not needle or needle in (e.title or "").casefold()
                 or needle in Path(e.source).name.casefold())
        ]  # fmt: skip

    def _fill(self) -> None:
        for key, button in self.filter_buttons.items():
            button.configure(style="FilterOn.TButton" if key == self.filter else "Filter.TButton")
        selected = {e.sha256 for e in self.selected_entries()}
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for entry in self._visible():
            label = STATES[entry.state][0]
            mark = "●  " if entry.state in ("to_review", "failed") else "   "
            item = self.tree.insert(
                "", "end", text="  " + (entry.title or Path(entry.source).stem),
                values=(_kind(entry), mark + label,
                        what_to_review(entry), short_date(entry.finished_at)),
                tags=(entry.state,),
            )  # fmt: skip
            self._rows[item] = entry
            if entry.sha256 in selected:
                self.tree.selection_add(item)
        self._selection_changed()

    def show_progress(self, label: str, done: int | None, total: int | None) -> None:
        self.strip.grid(row=1, column=0, sticky="ew", pady=(16, 4))
        self.strip_label.configure(text=f"Convirtiendo «{label}»")
        if done is None or total is None:
            if str(self.strip_bar.cget("mode")) != "indeterminate":
                self.strip_bar.configure(mode="indeterminate")
            self.strip_bar.start(12)
            self.strip_count.configure(text="Preparando…")
        else:
            self.strip_bar.stop()
            self.strip_bar.configure(mode="determinate", maximum=max(total, 1), value=done)
            self.strip_count.configure(text=f"{done} de {total}")

    def hide_progress(self) -> None:
        self.strip_bar.stop()
        self.strip.grid_forget()

    # --- Selection -------------------------------------------------------------------

    def selected_entries(self) -> list[LibraryEntry]:
        """The selected documents, in list order."""
        return [self._rows[item] for item in self.tree.selection() if item in self._rows]

    def selected(self) -> LibraryEntry | None:
        """The selected document when exactly one is (open, show in the folder)."""
        entries = self.selected_entries()
        return entries[0] if len(entries) == 1 else None

    def select(self, sha256: str) -> None:
        for item, entry in self._rows.items():
            if entry.sha256 == sha256:
                self.tree.selection_set(item)
                self.tree.see(item)

    def _select_all(self, _event: object = None) -> str:
        self.tree.selection_set(self.tree.get_children())
        return "break"  # not the Treeview's own Ctrl+A

    def _selection_changed(self) -> None:
        entries = self.selected_entries()
        entry = self.selected()
        state = entry.state if entry else None
        self.open_button.configure(text="Revisar" if state == "to_review" else "Abrir")
        count = len(entries)
        self.forget_button.configure(
            text=f"Quitar de la biblioteca ({count})…" if count > 1 else "Quitar de la biblioteca…"
        )
        failed = [e for e in entries if e.state == "failed"]
        self.retry_button.configure(text=f"Reintentar ({len(failed)})" if len(failed) > 1
                                    else "Reintentar")  # fmt: skip
        enabled = {
            self.open_button: state not in (None, "failed", "converting"),
            self.folder_button: bool(entry and entry.output),
            self.retry_button: bool(failed),
            # Not while one converts: the batch is writing its record.
            self.forget_button: bool(entries) and all(e.state != "converting" for e in entries),
        }
        for button, on in enabled.items():
            button.state(["!disabled"] if on else ["disabled"])

    def _open_selected(self) -> None:
        entry = self.selected()
        if entry is not None and entry.state not in ("failed", "converting"):
            self.actions.open_entry(entry)

    def _with_selected(self, action: Callable[[LibraryEntry], None]) -> None:
        entry = self.selected()
        if entry is not None:
            action(entry)

    def _forget_selected(self) -> None:
        entries = self.selected_entries()
        if entries:
            self.actions.forget(entries)

    def _retry_selected(self) -> None:
        failed = [e for e in self.selected_entries() if e.state == "failed"]
        if failed:
            self.actions.retry(failed)

    def _context_menu(self, event: "tk.Event[ttk.Treeview]") -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        if item not in self.tree.selection():  # a right click outside the selection
            self.tree.selection_set(item)
        self.update_idletasks()
        entries = self.selected_entries()
        self.context.delete(0, "end")
        if len(entries) == 1:
            entry = entries[0]
            if entry.state not in ("failed", "converting"):
                self.context.add_command(
                    label="Revisar" if entry.state == "to_review" else "Abrir",
                    command=lambda: self.actions.open_entry(entry),
                )
            if entry.output:
                self.context.add_command(
                    label="Mostrar en la carpeta",
                    command=lambda: self.actions.show_in_folder(entry),
                )
        failed = [e for e in entries if e.state == "failed"]
        if failed:
            label = f"Reintentar ({len(failed)})" if len(failed) > 1 else "Reintentar"
            self.context.add_command(label=label, command=lambda: self.actions.retry(failed))
        if entries and all(e.state != "converting" for e in entries):
            if self.context.index("end") is not None:
                self.context.add_separator()
            label = (f"Quitar {len(entries)} de la biblioteca…" if len(entries) > 1
                     else "Quitar de la biblioteca…")  # fmt: skip
            self.context.add_command(label=label, command=lambda: self.actions.forget(entries))
        self.context.tk_popup(event.x_root, event.y_root)


def _kind(entry: LibraryEntry) -> str:
    """The source type, and for a book split by chapters how many files it has."""
    kind = _KINDS.get(Path(entry.source).suffix.lower(), "—")
    return f"{kind} · {entry.parts - 1} partes" if entry.parts > 1 else kind


def _short_path(path: Path, limit: int = 70) -> str:
    """A long folder path shortened in the middle, keeping its start and its name."""
    text = str(path)
    if len(text) <= limit:
        return text
    return text[: limit // 3] + " … " + text[-(limit - limit // 3 - 3) :]

"""The look of the desktop app: one palette, one type family, ttk styles (no extra
dependencies). Built on ttk's `clam` theme, the one whose colours and borders can all be
set. Status colours always come with a word ("Por revisar"), never colour alone.
"""

import tkinter as tk
from dataclasses import dataclass
from tkinter import font, ttk


@dataclass(frozen=True, slots=True)
class Palette:
    ground: str = "#F4F5F7"
    surface: str = "#FFFFFF"
    border: str = "#DCE0E5"
    border_strong: str = "#B9C1CC"
    ink: str = "#161A1F"
    muted: str = "#4A5361"
    accent: str = "#1F4FBF"
    accent_hover: str = "#163B91"
    accent_soft: str = "#E8EEFB"
    warn_ink: str = "#8A3D00"
    warn_soft: str = "#FFF1E3"
    warn_row: str = "#FFF8F0"
    warn_strong: str = "#D9730D"
    ok_ink: str = "#0B5C4C"
    ok_soft: str = "#E3F4EF"
    error_ink: str = "#A12A1E"
    error_soft: str = "#FDECEA"
    page_ground: str = "#E9ECF0"


PALETTE = Palette()

# How each library state is shown: label, text colour, background.
STATES: dict[str, tuple[str, str, str]] = {
    "to_review": ("Por revisar", PALETTE.warn_ink, PALETTE.warn_soft),
    "reviewed": ("Revisado", PALETTE.accent_hover, PALETTE.accent_soft),
    "ready": ("Listo", PALETTE.ok_ink, PALETTE.ok_soft),
    "failed": ("No convertido", PALETTE.error_ink, PALETTE.error_soft),
    "converting": ("Convirtiendo…", PALETTE.muted, PALETTE.ground),
}


@dataclass(frozen=True, slots=True)
class Fonts:
    family: str
    mono: str

    def __call__(self, size: int = 10, weight: str = "normal") -> tuple[str, int, str]:
        return (self.family, size, weight)

    def code(self, size: int = 10) -> tuple[str, int]:
        return (self.mono, size)


def fonts(root: tk.Misc) -> Fonts:
    families = set(font.families(root))
    family = next(
        (f for f in ("Segoe UI", "IBM Plex Sans", "Cantarell", "Helvetica Neue") if f in families),
        font.nametofont("TkDefaultFont").actual("family"),
    )
    mono = next(
        (f for f in ("Cascadia Mono", "Consolas", "IBM Plex Mono", "Menlo") if f in families),
        font.nametofont("TkFixedFont").actual("family"),
    )
    return Fonts(str(family), str(mono))


def apply(root: tk.Misc) -> Fonts:
    """Configure every ttk style the app uses; returns the fonts for plain Tk widgets."""
    p, f = PALETTE, fonts(root)
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=p.ground)  # type: ignore[call-arg]
    line = font.Font(root, font=f(10)).metrics("linespace")

    style.configure(".", font=f(10), background=p.ground, foreground=p.ink, bordercolor=p.border,
                    focuscolor=p.accent, troughcolor="#E3E7ED")  # fmt: skip
    style.configure("TFrame", background=p.ground)
    style.configure("Surface.TFrame", background=p.surface)
    style.configure("Card.TFrame", background=p.surface, relief="solid", borderwidth=1,
                    bordercolor=p.border)  # fmt: skip
    style.configure("Warn.TFrame", background=p.warn_row, relief="solid", borderwidth=1,
                    bordercolor="#F2C9A0")  # fmt: skip
    style.configure("Sidebar.TFrame", background=p.surface)

    for name, background in (("", p.ground), ("Surface.", p.surface), ("Warn.", p.warn_row)):
        style.configure(f"{name}TLabel", background=background, foreground=p.ink)
        style.configure(f"{name}Muted.TLabel", background=background, foreground=p.muted)
        style.configure(f"{name}Strong.TLabel", background=background, font=f(10, "bold"))
    style.configure("Title.TLabel", background=p.ground, font=f(18, "bold"))
    style.configure("Surface.Title.TLabel", background=p.surface, font=f(14, "bold"))
    style.configure("Brand.TLabel", background=p.surface, font=f(13, "bold"))
    style.configure("Logo.TLabel", background=p.accent, foreground="#FFFFFF", font=f(10, "bold"),
                    padding=(7, 4))  # fmt: skip
    style.configure("Big.Surface.TLabel", background=p.surface, font=f(15, "bold"))
    style.configure("Step.Surface.TLabel", background=p.surface, foreground=p.accent,
                    font=f(10, "bold"))  # fmt: skip
    for state, (_, ink, soft) in STATES.items():
        style.configure(f"{state}.Chip.TLabel", background=soft, foreground=ink,
                        font=f(9, "bold"), padding=(8, 2))  # fmt: skip

    def button(name: str, bg: str, fg: str, border: str, hover: str, *, bold: bool = False) -> None:
        style.configure(name, background=bg, foreground=fg, bordercolor=border, lightcolor=bg,
                        darkcolor=bg, relief="solid", borderwidth=1, padding=(14, 7),
                        font=f(10, "bold" if bold else "normal"), focusthickness=1)  # fmt: skip
        style.map(name, background=[("disabled", p.ground), ("active", hover)],
                  foreground=[("disabled", "#8A93A0")],
                  lightcolor=[("active", hover)], darkcolor=[("active", hover)])  # fmt: skip

    button("Primary.TButton", p.accent, "#FFFFFF", p.accent, p.accent_hover, bold=True)
    button("TButton", p.surface, p.ink, p.border_strong, "#EEF1F5")
    button("Outline.TButton", p.surface, p.accent, p.accent, p.accent_soft, bold=True)
    button("Warn.TButton", p.warn_row, p.warn_ink, p.warn_strong, p.warn_soft, bold=True)
    button("Nav.TButton", p.surface, p.ink, p.surface, "#F1F3F6")
    button("NavActive.TButton", p.accent_soft, p.accent_hover, p.accent_soft, p.accent_soft,
           bold=True)  # fmt: skip
    button("Filter.TButton", p.surface, p.ink, p.border_strong, "#EEF1F5")
    button("FilterOn.TButton", p.accent, "#FFFFFF", p.accent, p.accent_hover, bold=True)
    button("Link.TButton", p.surface, p.accent, p.surface, p.surface)
    style.configure("Nav.TButton", anchor="w", padding=(12, 9))
    style.configure("NavActive.TButton", anchor="w", padding=(12, 9))
    style.configure("Filter.TButton", padding=(12, 4))
    style.configure("FilterOn.TButton", padding=(12, 4))
    style.configure("Link.TButton", padding=(2, 2))

    style.configure("TEntry", fieldbackground=p.surface, bordercolor=p.border_strong,
                    lightcolor=p.border_strong, padding=(8, 6))  # fmt: skip
    style.map("TEntry", bordercolor=[("focus", p.accent)], lightcolor=[("focus", p.accent)])
    style.configure("TCombobox", fieldbackground=p.surface, padding=(8, 5))
    for name, background in (("TCheckbutton", p.surface), ("Warn.TCheckbutton", p.warn_row),
                             ("TRadiobutton", p.surface)):  # fmt: skip
        style.configure(name, background=background, foreground=p.ink, indicatorcolor=p.surface,
                        indicatorbackground=p.surface,
                        indicatorsize=int(line * 0.75))  # fmt: skip
        style.map(
            name, indicatorcolor=[("selected", p.accent)], background=[("active", background)]
        )
    style.configure("Accent.Horizontal.TProgressbar", background=p.accent, bordercolor="#E3E7ED",
                    lightcolor=p.accent, darkcolor=p.accent, thickness=8)  # fmt: skip

    style.configure("Library.Treeview", background=p.surface, fieldbackground=p.surface,
                    foreground=p.ink, bordercolor=p.border, rowheight=int(line * 2.1),
                    font=f(10))  # fmt: skip
    style.configure("Library.Treeview.Heading", background=p.surface, foreground=p.muted,
                    font=f(9, "bold"), relief="flat", padding=(10, 8),
                    bordercolor=p.border)  # fmt: skip
    style.map("Library.Treeview", background=[("selected", p.accent_soft)],
              foreground=[("selected", p.ink)])  # fmt: skip
    style.map("Library.Treeview.Heading", background=[("active", p.surface)])
    style.configure("Vertical.TScrollbar", background=p.ground, troughcolor=p.surface,
                    bordercolor=p.surface, arrowcolor=p.muted)  # fmt: skip
    style.configure("TPanedwindow", background=p.ground)
    return f

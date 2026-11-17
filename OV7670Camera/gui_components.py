"""Common camera/recycler section styling and collapsible containers."""
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import sys

TITLE_FONT = ("Segoe UI", 17, "bold")
APP_ICON = Path(__file__).resolve().parent.parent / "assets" / "app_icon.ico"


class Tooltip:
    """Delayed, non-focusable help that also works on disabled controls."""
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.timer = self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        for event in ("<Leave>", "<ButtonPress>", "<KeyPress>", "<MouseWheel>",
                      "<FocusOut>", "<Configure>", "<Unmap>", "<Destroy>"):
            widget.bind(event, self.hide, add="+")

    def schedule(self, event=None):
        self.hide()
        self.timer = self.widget.after(500, self.show)

    def show(self):
        self.timer = None
        if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
            return
        text = self.text() if callable(self.text) else self.text
        if not text:
            return
        popup = self.window = tk.Toplevel(self.widget)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.widget.winfo_toplevel())
        if sys.platform == "win32":
            popup.attributes("-toolwindow", True)
        tk.Label(popup, text=text, justify="left", wraplength=280, padx=8, pady=5,
                 background="#fffbe6", foreground="#111827", relief="solid", borderwidth=1,
                 font=("Segoe UI", 9)).pack()
        popup.update_idletasks()
        x, y = self.widget.winfo_pointerxy()
        left, top, right, bottom = self.screen_bounds(x, y)
        width, height = popup.winfo_reqwidth(), popup.winfo_reqheight()
        x = max(left, min(x+12, right-width))
        y = y+20 if y+20+height <= bottom else y-height-12
        y = max(top, min(y, bottom-height))
        popup.geometry(f"+{x}+{y}")
        popup.deiconify()
        popup.lift()

    def screen_bounds(self, x, y):
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            class MonitorInfo(ctypes.Structure):
                _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                            ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
            api = ctypes.windll.user32
            api.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            api.MonitorFromPoint.restype = wintypes.HANDLE
            api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            monitor = api.MonitorFromPoint(wintypes.POINT(x, y), 2)
            if api.GetMonitorInfoW(monitor, ctypes.byref(info)):
                r = info.work
                return r.left, r.top, r.right, r.bottom
        return 0, 0, self.widget.winfo_screenwidth(), self.widget.winfo_screenheight()

    def hide(self, event=None):
        if self.timer is not None:
            try:
                self.widget.after_cancel(self.timer)
            except tk.TclError:
                pass
            self.timer = None
        if self.window is not None:
            popup, self.window = self.window, None
            try:
                popup.destroy()
            except tk.TclError:
                pass


def add_tooltip(widget, text):
    widget.tooltip = Tooltip(widget, text)
    return widget


def configure_app_identity(app_id):
    """Give Windows a stable app identity instead of grouping under Python."""
    if sys.platform == "win32":
        import ctypes
        # Match the launcher's physical coordinates. Otherwise Windows can
        # rescale a Tk window after tiling it onto a differently scaled monitor.
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


def configure_styles(root):
    style = ttk.Style(root)
    style.configure("Section.TButton", anchor="w", font=("Segoe UI", 10, "bold"))
    # Native Windows buttons ignore custom backgrounds. Use a Clam border for
    # these action styles without changing the theme of the rest of the GUI.
    if "ActionButton.border" not in style.element_names():
        style.element_create("ActionButton.border", "from", "clam", "Button.border")
    for name, color, hover, pressed in (("Start.TButton", "#15803d", "#166534", "#14532d"),
                                        ("Stop.TButton", "#b91c1c", "#991b1b", "#7f1d1d")):
        style.layout(name, [("ActionButton.border", {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})])
        style.configure(name, background=color, foreground="white", bordercolor=color,
                        lightcolor=color, darkcolor=color, padding=(8, 4), borderwidth=1)
        style.map(name, background=[("disabled", "#e5e7eb"), ("pressed", pressed), ("active", hover)],
                  foreground=[("disabled", "#6b7280"), ("!disabled", "white")],
                  bordercolor=[("disabled", "#d1d5db")],
                  lightcolor=[("disabled", "#e5e7eb")], darkcolor=[("disabled", "#e5e7eb")])
    style.configure("Emergency.Stop.TButton", font=("Segoe UI", 12, "bold"), foreground="black")
    style.map("Emergency.Stop.TButton", foreground=[("disabled", "black"), ("!disabled", "black")])
    if sys.platform == "win32" and APP_ICON.is_file():
        root.iconbitmap(str(APP_ICON))
        root.iconbitmap(default=str(APP_ICON))


class CollapsibleSection(ttk.Frame):
    """Hide contents without destroying widgets or changing their values."""
    def __init__(self, parent, title, expanded=True):
        super().__init__(parent)
        self.title = title
        self.expanded = expanded
        self.toggle_button = ttk.Button(self, text=("- " if expanded else "+ ") + title,
                                        style="Section.TButton", command=self.toggle)
        self.toggle_button.pack(fill="x")
        add_tooltip(self.toggle_button, lambda: f"{'Hide' if self.expanded else 'Show'} {self.title}.")
        self.content = ttk.Frame(self, padding=7)
        if expanded:
            self.content.pack(fill="x")

    def toggle(self):
        self.expanded = not self.expanded
        if self.expanded:
            self.content.pack(fill="x")
        else:
            self.content.pack_forget()
        self.toggle_button.configure(text=("- " if self.expanded else "+ ") + self.title)
        self.event_generate("<<SectionToggled>>", when="tail")

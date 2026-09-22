"""Small shared widgets with explicit colours for Windows high-contrast themes."""

import tkinter as tk
from tkinter import ttk


class ModernScrollbar(ttk.Scrollbar):
    """Slim, arrowless scrollbar used by list and picker surfaces."""

    def __init__(self, parent, *, orient=tk.VERTICAL, **kwargs):
        orientation = str(orient).lower()
        style = (
            "Modern.Horizontal.TScrollbar"
            if orientation in {"horizontal", "h"}
            else "Modern.Vertical.TScrollbar"
        )
        kwargs.setdefault("style", style)
        super().__init__(parent, orient=orient, **kwargs)


def center_window(window, parent=None) -> None:
    """Place a newly created window at the center of its owner."""
    owner = parent or getattr(window, "master", None)
    window.update_idletasks()
    width = max(window.winfo_width(), window.winfo_reqwidth())
    height = max(window.winfo_height(), window.winfo_reqheight())
    if owner is not None and owner.winfo_exists():
        owner.update_idletasks()
        x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2
        y = owner.winfo_rooty() + (owner.winfo_height() - height) // 2
    else:
        x = (window.winfo_screenwidth() - width) // 2
        y = (window.winfo_screenheight() - height) // 2
    max_x = max(0, window.winfo_screenwidth() - width)
    max_y = max(0, window.winfo_screenheight() - height)
    window.geometry(f"+{max(0, min(x, max_x))}+{max(0, min(y, max_y))}")


class HoverTooltip:
    """Delayed help bubble for compact toolbar buttons."""

    def __init__(self, widget, text, *, delay=450):
        self.widget, self.text, self.delay = widget, str(text), delay
        self.tip = None
        self.job = None
        self.owner = widget.winfo_toplevel()
        self._owner_unmap_bind = None
        widget.bind('<Enter>', self._enter, add='+')
        widget.bind('<Leave>', self._leave, add='+')
        widget.bind('<Destroy>', self._destroy, add='+')
        try:
            self._owner_unmap_bind = self.owner.bind(
                "<Unmap>", self._owner_unmapped, add="+"
            )
        except tk.TclError:
            pass

    def _enter(self, _event=None):
        self._cancel()
        self.job = self.widget.after(self.delay, self._show)

    def _leave(self, _event=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def _show(self):
        self.job = None
        if (
            self.tip is not None
            or not self.widget.winfo_exists()
            or not self.widget.winfo_ismapped()
        ):
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes('-topmost', True)
        tip.configure(background='#1e293b')
        try:
            tip.attributes('-alpha', 0.98)
        except tk.TclError:
            pass
        tk.Label(tip, text=self.text, justify='left', wraplength=340,
                 background='#1e293b', foreground='#ffffff', padx=10, pady=7,
                 font=('Microsoft YaHei UI', 9)).pack()
        tip.update_idletasks()
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        screen_width = self.widget.winfo_screenwidth()
        screen_height = self.widget.winfo_screenheight()
        tip_width = tip.winfo_width()
        tip_height = tip.winfo_height()
        x = min(x, max(0, screen_width - tip_width - 8))
        y = min(y, max(0, screen_height - tip_height - 8))
        tip.geometry(f'+{x}+{y}')
        self.tip = tip

    def _owner_unmapped(self, _event=None):
        """Do not leave an override-redirect tooltip alive during minimize."""
        self._cancel()
        self._hide()

    def _hide(self):
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None

    def _destroy(self, _event=None):
        self._cancel()
        self._hide()
        if self._owner_unmap_bind is not None:
            try:
                self.owner.unbind("<Unmap>", self._owner_unmap_bind)
            except tk.TclError:
                pass
            self._owner_unmap_bind = None


class CheckmarkOption(ttk.Frame):
    """Card-friendly Boolean option with an explicit tick instead of an X."""

    def __init__(
        self,
        parent,
        *,
        text: str,
        variable: tk.BooleanVar,
        palette: dict,
        wraplength: int = 640,
    ):
        super().__init__(parent, style="Card.TFrame")
        self.variable = variable
        self.palette = palette
        self.indicator = tk.Label(
            self,
            width=2,
            height=1,
            font=("Segoe UI Symbol", 10, "bold"),
            cursor="hand2",
            padx=0,
            pady=0,
            takefocus=True,
            highlightthickness=1,
        )
        self.indicator.pack(side=tk.LEFT, anchor=tk.N, pady=1)
        self.label = ttk.Label(
            self,
            text=text,
            style="Card.TLabel",
            cursor="hand2",
            wraplength=wraplength,
        )
        self.label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(9, 0))
        for widget in (self, self.indicator, self.label):
            widget.bind("<Button-1>", self._toggle, add="+")
        self.indicator.bind("<space>", self._toggle, add="+")
        self.indicator.bind("<Return>", self._toggle, add="+")
        self._trace_id = self.variable.trace_add("write", self._refresh)
        self._refresh()

    def _toggle(self, _event=None):
        self.variable.set(not bool(self.variable.get()))
        return "break"

    def _refresh(self, *_args) -> None:
        selected = bool(self.variable.get())
        self.indicator.configure(
            text="✓" if selected else "",
            bg=self.palette["primary"] if selected else self.palette["card"],
            fg="#ffffff" if selected else self.palette["text"],
            activebackground=(
                self.palette["primary_hover"] if selected else self.palette["primary_soft"]
            ),
            activeforeground="#ffffff" if selected else self.palette["primary"],
            highlightbackground=(
                self.palette["primary"] if selected else self.palette["border_strong"]
            ),
            highlightcolor=self.palette["primary"],
        )


class UsageTreeview(ttk.Treeview):
    """Draw quota bars only for visible cells, retaining normal tree behaviour."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._bar_widgets = []
        self._bar_refresh = None
        for event in ('<Configure>', '<MouseWheel>', '<Button-4>', '<Button-5>', '<ButtonRelease-1>', '<KeyRelease>'):
            self.bind(event, self.redraw_bars, add='+')
        self.bind('<<TreeviewSelect>>', self._sync_selection_bars, add='+')
        self.bind('<Unmap>', self._hide_bars_for_unmap, add='+')
        self.bind('<Map>', self._restore_bars_after_map, add='+')
        self.bind('<Destroy>', self._cancel_bar_refresh, add='+')

    def _cancel_bar_refresh(self, event):
        if event.widget is self and self._bar_refresh:
            self.after_cancel(self._bar_refresh)
            self._bar_refresh = None

    def insert(self, *args, **kwargs):
        result = super().insert(*args, **kwargs)
        self.redraw_bars()
        return result

    def delete(self, *items):
        result = super().delete(*items)
        self.redraw_bars()
        return result

    def yview(self, *args):
        result = super().yview(*args)
        if args: self.redraw_bars()
        return result

    def xview(self, *args):
        result = super().xview(*args)
        if args: self.redraw_bars()
        return result

    def xview_moveto(self, fraction):
        super().xview_moveto(fraction)
        self.redraw_bars()

    def yview_moveto(self, fraction):
        super().yview_moveto(fraction)
        self.redraw_bars()

    def see(self, item):
        super().see(item)
        self.redraw_bars()

    def redraw_bars(self, _event=None):
        if self._bar_refresh is None:
            self._bar_refresh = self.after_idle(self._draw_bars)

    def _sync_selection_bars(self, _event=None):
        """Paint the quota overlay in the same event as the row selection."""
        if self._bar_refresh is not None:
            try:
                self.after_cancel(self._bar_refresh)
            except tk.TclError:
                pass
            self._bar_refresh = None
        if not self.winfo_ismapped():
            return
        self.update_idletasks()
        self._draw_bars()

    def _hide_bars_for_unmap(self, _event=None):
        """Hide overlay child windows while Windows composites the minimize."""
        if self._bar_refresh is not None:
            try:
                self.after_cancel(self._bar_refresh)
            except tk.TclError:
                pass
            self._bar_refresh = None
        for canvas in self._bar_widgets:
            try:
                canvas.place_forget()
            except tk.TclError:
                pass

    def _restore_bars_after_map(self, _event=None):
        if self.winfo_ismapped():
            self.redraw_bars()

    def _select_bar_row(self, event, row):
        if event.state & 4:
            if row in self.selection(): self.selection_remove(row)
            else: self.selection_add(row)
        elif event.state & 1 and self.focus():
            rows=list(self.get_children())
            a,b=sorted((rows.index(self.focus()), rows.index(row)))
            self.selection_set(rows[a:b+1])
        else:
            self.selection_set(row)
        self.focus(row)
        self.focus_set()

    def _scroll_bar(self, event):
        self.yview('scroll', -int(event.delta / 120), 'units')
        return 'break'

    def _draw_bars(self):
        self._bar_refresh = None
        if not self.winfo_ismapped():
            return
        selected=set(self.selection())
        used=0
        for row in self.get_children():
            for column in ('quota7',):
                box=self.bbox(row,column)
                if not box: continue
                x,y,w,h=box
                # Do not cover headers or paint outside horizontally clipped cells.
                if x < 0 or x+w > self.winfo_width() or y+h > self.winfo_height(): continue
                while used >= len(self._bar_widgets):
                    self._bar_widgets.append(
                        tk.Canvas(
                            self,
                            highlightthickness=0,
                            borderwidth=0,
                            background="#ffffff",
                            takefocus=False,
                        )
                    )
                canvas=self._bar_widgets[used];used+=1
                canvas.delete('all')
                canvas.configure(background='#e8f0ff' if row in selected else '#ffffff')
                text=self.set(row,column)
                try: percent=float(text.rstrip('%')) if text.endswith('%') else None
                except ValueError: percent=None
                if percent is not None:
                    color='#16a34a' if percent<70 else '#d97706' if percent<90 else '#dc2626'
                    canvas.create_line(8,h-8,max(8,w-10),h-8,width=5,fill='#e5eaf1',capstyle=tk.ROUND)
                    if percent>0:
                        canvas.create_line(8,h-8,8+max(0,w-18)*min(percent,100)/100,h-8,width=5,fill=color,capstyle=tk.ROUND)
                    canvas.create_text((w-2)/2,max(7,(h-13)/2),text=text,fill='#203039',font=('Segoe UI',9))
                else:
                    canvas.create_text((w-2)/2,(h-2)/2,text=text,fill='#647681',font=('Microsoft YaHei UI',9))
                canvas.bind('<Button-1>',lambda e,r=row:self._select_bar_row(e,r))
                canvas.bind('<MouseWheel>',self._scroll_bar)
                # Draw and configure before mapping the child window. This
                # avoids a default black frame during Windows minimize/restore.
                canvas.place(x=x+1,y=y+1,width=max(1,w-2),height=max(1,h-2))
        for canvas in self._bar_widgets[used:]: canvas.place_forget()


class CheckList(ttk.Frame):
    def __init__(self, parent, choices, selected=()):
        super().__init__(parent)
        self.variables = {}
        canvas = tk.Canvas(self, height=180, highlightthickness=0, bg="#ffffff")
        scroll = ModernScrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", padx=(4, 0))
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, padding=8, style="Card.TFrame")
        item = canvas.create_window(0, 0, anchor="nw", window=body)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        body.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        selected = {str(value) for value in selected}
        for value, label in choices:
            variable = tk.BooleanVar(value=str(value) in selected)
            self.variables[value] = variable
            ttk.Checkbutton(body, text=label, variable=variable).pack(
                anchor="w", pady=4
            )
        if not choices:
            ttk.Label(body, text="暂无可选项，请先读取远端列表").pack(anchor="w")

    def selected(self):
        return [value for value, variable in self.variables.items() if variable.get()]

    def select_all(self, enabled=True):
        for variable in self.variables.values():
            variable.set(enabled)


class ScrollableFrame(ttk.Frame):
    """A tab page whose long settings content stays reachable on small screens."""

    def __init__(self, parent, *, height=420):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            self,
            height=height,
            highlightthickness=0,
            borderwidth=0,
            background="#ffffff",
        )
        self.scrollbar = ModernScrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.body = ttk.Frame(self.canvas, padding=2, style="Card.TFrame")
        self.window_id = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self.window_id, width=event.width),
        )
        self.body.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )

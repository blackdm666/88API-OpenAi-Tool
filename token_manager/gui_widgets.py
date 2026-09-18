"""Small shared widgets with explicit colours for Windows high-contrast themes."""

import tkinter as tk
from tkinter import ttk


class UsageTreeview(ttk.Treeview):
    """Draw quota bars only for visible cells, retaining normal tree behaviour."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._bar_widgets = []
        self._bar_refresh = None
        for event in ('<Configure>', '<Map>', '<MouseWheel>', '<Button-4>', '<Button-5>', '<ButtonRelease-1>', '<KeyRelease>', '<<TreeviewSelect>>'):
            self.bind(event, self.redraw_bars, add='+')
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
            for column in ('quota5','quota7'):
                box=self.bbox(row,column)
                if not box: continue
                x,y,w,h=box
                # Do not cover headers or paint outside horizontally clipped cells.
                if x < 0 or x+w > self.winfo_width() or y+h > self.winfo_height(): continue
                while used >= len(self._bar_widgets):
                    self._bar_widgets.append(tk.Canvas(self,highlightthickness=0,borderwidth=0))
                canvas=self._bar_widgets[used];used+=1
                canvas.place(x=x+1,y=y+1,width=max(1,w-2),height=max(1,h-2))
                canvas.delete('all')
                canvas.configure(background='#d9f0ec' if row in selected else '#ffffff')
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
        for canvas in self._bar_widgets[used:]: canvas.place_forget()


class CheckList(ttk.Frame):
    def __init__(self, parent, choices, selected=()):
        super().__init__(parent)
        self.variables = {}
        canvas = tk.Canvas(self, height=180, highlightthickness=0, bg="#ffffff")
        scroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
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

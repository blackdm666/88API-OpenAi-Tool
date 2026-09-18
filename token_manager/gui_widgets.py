"""Small shared widgets with explicit colours for Windows high-contrast themes."""

import tkinter as tk
from tkinter import ttk


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

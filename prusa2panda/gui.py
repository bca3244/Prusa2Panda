"""Minimal Tkinter GUI: python -m prusa2panda.gui

It inspects the file first (producer, beds, extruders, settings), lets you pick which
bed(s) to convert, the target slicer and which printer preset the output should use,
asks how to map the extruders to filament slots, and reports what was written.
"""

from __future__ import annotations

import os
import threading
import traceback
from tkinter import (
    END,
    DISABLED,
    NORMAL,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    Radiobutton,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)

from .convert import (
    BEDS_PLATES,
    BEDS_SPLIT,
    PRINTER_AUTO,
    PRINTER_PRUSA,
    SLICER_BAMBU,
    SLICER_ORCA,
    convert_file,
    current_printer,
    describe,
    detect_slicers,
    filament_label,
    installed_printers,
    slicer_display_name,
    suggest_filament_map,
    used_extruders,
)

_AUTO_LABEL = "Auto: printer currently selected in {slicer}"
_PRUSA_LABEL = "PrusaSlicer printer as-is (only if {slicer} is not installed)"
_BROWSE_LABEL = "Browse for an exported .json preset / {slicer} .3mf…"


def _center_on_screen(win, min_w: int = 0, min_h: int = 0) -> None:
    """Place a window in the middle of the monitor."""
    win.update_idletasks()
    w = max(win.winfo_reqwidth(), min_w)
    h = max(win.winfo_reqheight(), min_h)
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 2
    win.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")


class App:
    def __init__(self, master: Tk):
        self.master = master
        master.title("Prusa2Panda - Prusa to Bambu / Orca 3mf converter")
        master.minsize(640, 500)

        self.input_var = StringVar()
        self.output_var = StringVar()
        self.bed_var = StringVar(value="all")
        self.split_var = BooleanVar(value=False)
        self.settings_var = StringVar(value="presets")  # presets | prusa | none
        found = detect_slicers()
        self.slicer_var = StringVar(value=found[0] if found else SLICER_BAMBU)
        self.printer_var = StringVar()
        self.printer_file: str | None = None
        self.slicer_installed = False

        row = 0
        Label(master, text="Input PrusaSlicer .3mf:").grid(row=row, column=0, sticky="w", padx=8, pady=(10, 2))
        row += 1
        Entry(master, textvariable=self.input_var, width=60).grid(row=row, column=0, padx=8, sticky="we")
        Button(master, text="Browse…", command=self.pick_input).grid(row=row, column=1, padx=(4, 8))
        row += 1
        Label(master, text="Output Bambu / Orca .3mf:").grid(row=row, column=0, sticky="w", padx=8, pady=(8, 2))
        row += 1
        Entry(master, textvariable=self.output_var, width=60).grid(row=row, column=0, padx=8, sticky="we")
        Button(master, text="Browse…", command=self.pick_output).grid(row=row, column=1, padx=(4, 8))
        row += 1
        Label(master, text="Beds (e.g. 'all' or '1' or '1,2'):").grid(row=row, column=0, sticky="w", padx=8, pady=(8, 2))
        row += 1
        bed_row = Frame(master)
        bed_row.grid(row=row, column=0, columnspan=2, padx=8, sticky="w")
        Entry(bed_row, textvariable=self.bed_var, width=20).pack(side="left")
        Checkbutton(
            bed_row,
            text="One file per bed; default: one file, each PrusaSlicer bed on its own plate",
            variable=self.split_var,
        ).pack(side="left", padx=(12, 0))
        row += 1
        Label(master, text="Target slicer:").grid(row=row, column=0, sticky="w", padx=8, pady=(8, 2))
        row += 1
        slicer_row = Frame(master)
        slicer_row.grid(row=row, column=0, columnspan=2, padx=8, sticky="w")
        for value in (SLICER_BAMBU, SLICER_ORCA):
            label = slicer_display_name(value) + ("" if value in found else " (not found)")
            Radiobutton(
                slicer_row, text=label, value=value, variable=self.slicer_var, command=self._fill_printers,
            ).pack(side="left", padx=(0, 12))
        row += 1
        Label(master, text="Settings:").grid(row=row, column=0, sticky="w", padx=8, pady=(8, 2))
        for value, text in (
            ("presets", "Keep my process and filament presets; bring over the PrusaSlicer colours, "
                        "extruders, painting, text, per-part settings and colour changes (recommended)"),
            ("prusa", "Use the PrusaSlicer print and filament settings as project presets"),
            ("none", "Geometry only (no slicer settings)"),
        ):
            row += 1
            Radiobutton(
                master, text=text, value=value, variable=self.settings_var, command=self._toggle_settings,
                wraplength=580, justify="left",
            ).grid(row=row, column=0, columnspan=2, sticky="w", padx=8)
        row += 1
        self.printer_label = Label(master, text="Target printer preset:")
        self.printer_label.grid(row=row, column=0, sticky="w", padx=8, pady=(4, 2))
        row += 1
        self.printer_box = ttk.Combobox(master, textvariable=self.printer_var, state="readonly", width=58)
        self.printer_box.grid(row=row, column=0, padx=8, sticky="we")
        self.printer_box.bind("<<ComboboxSelected>>", self._printer_selected)
        row += 1
        Button(master, text="Convert", command=self.convert, height=2).grid(
            row=row, column=0, columnspan=2, padx=8, pady=8, sticky="we"
        )
        row += 1
        self.log = Text(master, height=12, wrap="word", state=DISABLED)
        self.log.grid(row=row, column=0, columnspan=2, padx=8, pady=(0, 10), sticky="nsew")
        master.grid_columnconfigure(0, weight=1)
        master.grid_rowconfigure(row, weight=1)
        self._fill_printers()
        _center_on_screen(master, 640, 500)

    # -- helpers --------------------------------------------------------------
    def _slicer_name(self) -> str:
        return slicer_display_name(self.slicer_var.get())

    def _labels(self) -> tuple:
        name = self._slicer_name()
        return (_AUTO_LABEL.format(slicer=name), _PRUSA_LABEL.format(slicer=name),
                _BROWSE_LABEL.format(slicer=name))

    def _log(self, msg: str) -> None:
        self.log.config(state=NORMAL)
        self.log.insert(END, msg + "\n")
        self.log.see(END)
        self.log.config(state=DISABLED)

    def _fill_printers(self) -> None:
        auto_label, prusa_label, browse_label = self._labels()
        slicer = self.slicer_var.get()
        values = []
        try:
            cur = current_printer(slicer)
        except Exception:
            cur = None
        self.slicer_installed = bool(cur)
        if cur:
            values.append(f"{auto_label} ({cur})")
        try:
            names = installed_printers(slicer)
        except Exception:
            names = []
        values.extend(names)
        values.append(prusa_label)
        values.append(browse_label)
        self.printer_box["values"] = values
        self.printer_var.set(values[0])
        self.printer_file = None
        self.printer_label.config(text=f"Target {self._slicer_name()} printer preset:")

    def _toggle_settings(self) -> None:
        self.printer_box.config(state="readonly" if self.settings_var.get() != "none" else "disabled")

    def _printer_selected(self, _event=None) -> None:
        _auto, prusa_label, browse_label = self._labels()
        name = self._slicer_name()
        if self.printer_var.get() == prusa_label and self.slicer_installed:
            keep = messagebox.askyesno(
                "PrusaSlicer printer profile",
                "This keeps the PrusaSlicer printer in the converted file as a custom printer: "
                "its bed size, extruders and start/end G-code.\n\n"
                f"It is meant for PCs without {name}, where the printer is switched to a real "
                "Bambu Lab / Orca printer preset after opening the file.\n\n"
                f"{name} is installed here, so 'Auto' (your current printer) or a printer from "
                "the list gives a file that is ready to slice.\n\n"
                "Use the PrusaSlicer printer anyway?",
                icon="warning",
            )
            if not keep:
                self.printer_var.set(self.printer_box["values"][0])
            return
        if self.printer_var.get() == browse_label:
            path = filedialog.askopenfilename(
                title=f"{name} printer preset",
                filetypes=[(f"{name} preset / project", "*.json *.3mf"), ("All files", "*.*")],
            )
            if path:
                self.printer_file = path
                values = list(self.printer_box["values"])
                label = f"File: {path}"
                if label not in values:
                    values.insert(len(values) - 1, label)
                    self.printer_box["values"] = values
                self.printer_var.set(label)
            else:
                self.printer_var.set(self.printer_box["values"][0])

    def _printer_spec(self) -> str:
        auto_label, prusa_label, _browse = self._labels()
        choice = self.printer_var.get()
        if choice.startswith(auto_label):
            return PRINTER_AUTO
        if choice == prusa_label:
            return PRINTER_PRUSA
        if choice.startswith("File: "):
            return choice[len("File: "):]
        return choice

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("3mf files", "*.3mf")])
        if not path:
            return
        self.input_var.set(path)
        base, _ = os.path.splitext(path)
        if not self.output_var.get():
            self.output_var.set(base + "_bambu.3mf")
        try:
            project = describe(path)
            self._log(f"Loaded: {os.path.basename(path)}")
            self._log(f"  producer: {project.producer or 'unknown'}")
            self._log(f"  beds:     {', '.join(str(b) for b in project.bed_ids)}")
            used = used_extruders(project)
            if used:
                self._log(f"  extruders: {', '.join(filament_label(project, e) for e in used)}")
            cfg = project.config
            if cfg:
                self._log(
                    f"  settings: {cfg.get('print_settings_id') or '?'} on "
                    f"{cfg.get('printer_settings_id') or '?'}"
                )
            else:
                self._log("  settings: none in file")
        except Exception as exc:
            self._log(f"  could not inspect: {exc}")

    def pick_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".3mf", filetypes=[("3mf files", "*.3mf")])
        if path:
            self.output_var.set(path)

    def convert(self) -> None:
        inp, outp = self.input_var.get(), self.output_var.get()
        if not inp or not outp:
            messagebox.showwarning("Missing paths", "Choose both an input and an output file.")
            return
        beds = None
        bv = self.bed_var.get().strip().lower()
        if bv and bv != "all":
            try:
                beds = [int(p) for p in bv.replace(",", " ").split()]
            except ValueError:
                messagebox.showwarning("Bad bed", "Beds must be 'all' or numbers like 1 or 1,2.")
                return
        try:
            project = describe(inp)
        except Exception as exc:
            messagebox.showerror("Cannot read file", str(exc))
            return
        used = used_extruders(project, beds)
        if used not in ([], [1]):
            self._ask_mapping(project, used, inp, outp, beds)
        else:
            self._start(inp, outp, beds, None)

    def _ask_mapping(self, project, used, inp, outp, beds) -> None:
        """This file has N extruders: ask how to map them to filament slots."""
        dlg = Toplevel(self.master)
        dlg.title("Filament mapping")
        dlg.transient(self.master)
        dlg.grab_set()
        Label(
            dlg,
            text=(
                f"This file uses {len(used)} extruder(s).\n"
                f"Choose the {self._slicer_name()} filament slot for each "
                "(e.g. AMS slots 1-4):"
            ),
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 6))

        suggestion = suggest_filament_map(used)
        entries: dict = {}
        for i, e in enumerate(used, start=1):
            row = Frame(dlg)
            row.grid(row=i, column=0, columnspan=2, sticky="w", padx=10, pady=2)
            color = project.extruder_colour(e)
            sw = Label(row, text="    ", relief="solid", bd=1)
            if color:
                try:
                    sw.config(bg=color[:7])
                except Exception:
                    pass
            sw.pack(side="left", padx=(0, 6))
            Label(row, text=f"PrusaSlicer extruder {filament_label(project, e)}  →  filament").pack(side="left")
            ent = Entry(row, width=4)
            ent.insert(0, str(suggestion[e]))
            ent.pack(side="left", padx=(6, 0))
            entries[e] = ent

        def start_with(mapping) -> None:
            dlg.destroy()
            self._start(inp, outp, beds, mapping)

        def ok() -> None:
            try:
                mapping = {e: int(ent.get()) for e, ent in entries.items()}
            except ValueError:
                messagebox.showwarning("Bad mapping", "Filament numbers must be integers.", parent=dlg)
                return
            if any(v < 1 for v in mapping.values()):
                messagebox.showwarning("Bad mapping", "Filament numbers start at 1.", parent=dlg)
                return
            start_with(mapping)

        btns = Frame(dlg)
        btns.grid(row=len(used) + 1, column=0, columnspan=2, pady=10, padx=10, sticky="we")
        Button(btns, text="Convert", command=ok, width=12).pack(side="left")
        Button(btns, text="Keep original numbers", command=lambda: start_with({})).pack(side="left", padx=6)
        Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
        _center_on_screen(dlg)

    def _start(self, inp, outp, beds, filament_map) -> None:
        self._log(f"Converting → {os.path.basename(outp)} …")
        mode = self.settings_var.get()
        kwargs = dict(
            beds=beds,
            filament_map=filament_map,
            settings=mode != "none",
            prusa_settings=mode == "prusa",
            printer=self._printer_spec(),
            slicer=self.slicer_var.get(),
            bed_layout=BEDS_SPLIT if self.split_var.get() else BEDS_PLATES,
        )
        threading.Thread(target=self._run, args=(inp, outp, kwargs), daemon=True).start()

    def _run(self, inp, outp, kwargs) -> None:
        try:
            result = convert_file(inp, outp, **kwargs)
            s = result.stats
            lines = [
                f"Done ({result.slicer_name}). {s.objects_written} objects, {s.parts_written} parts, "
                f"{s.build_items_written} build items, {s.painted_triangles} painted triangles."
            ]
            if len(result.outputs) > 1:
                lines.append("  Written: " + ", ".join(os.path.basename(p) for p in result.outputs))
            elif s.plates_written > 1:
                lines.append(f"  {s.plates_written} beds placed on plates 1-{s.plates_written}")
            if s.filament_map:
                lines.append("  Filaments remapped: " + ", ".join(
                    f"{k}→{v}" for k, v in sorted(s.filament_map.items())
                ))
            if s.settings_written:
                lines.append(f"  Settings: {result.target_description}")
                lines.append(f"  Printer preset: {s.printer_name}")
                lines.append(f"  Process preset: {s.print_name}")
                for i, name in enumerate(s.filament_names, start=1):
                    lines.append(f"  Filament {i}: {name}")
                if s.custom_gcodes_written:
                    lines.append(f"  Colour changes/pauses carried over: {s.custom_gcodes_written}")
                if s.layer_ranges_written:
                    lines.append(f"  Layer height ranges carried over: {s.layer_ranges_written}")
                if s.text_parts:
                    lines.append(f"  Editable text parts: {s.text_parts}")
            else:
                lines.append(f"  Settings not converted ({result.target_description})")
            for w in s.warnings:
                lines.append(f"  note: {w}")
            self.master.after(0, lambda: self._log("\n".join(lines)))
        except Exception:
            err = traceback.format_exc()
            self.master.after(0, lambda: self._log("ERROR:\n" + err))


def main() -> None:
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

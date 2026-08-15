import os
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from wavfinder import config as config_module
from wavfinder import fileops, indexer
from wavfinder.index_cache import IndexCache
from wavfinder.models import WavMetadata
from wavfinder.search import FuzzySearchEngine, find_match_spans

# How long to wait (ms) after the user stops typing before running a search.
SEARCH_DEBOUNCE_MS = 200
# How often (ms) the main thread drains messages from the worker threads.
QUEUE_POLL_MS = 100
# Most rows we put in the table at once.
RESULT_LIMIT = 200

ALL_LIBRARIES = "All libraries"
ADD_LIBRARY = "Add library…"
MANAGE_LIBRARIES = "Manage libraries…"


class WavFinderApp:
    def __init__(self, extra_roots: list[Path] | None = None) -> None:
        self.config = config_module.load()
        for root in extra_roots or []:
            self.config.add_library(root)

        self.engine = FuzzySearchEngine()
        self.cache = IndexCache()
        self._queue: queue.Queue = queue.Queue()
        self._cancel_scan = threading.Event()
        self._scan_thread: threading.Thread | None = None
        # Bumped on every search so results from an abandoned query are dropped.
        self._search_generation = 0
        self._displayed: list[WavMetadata] = []
        self._displayed_terms: list[str] = []
        self._item_to_meta: dict[str, int] = {}
        self._debounce_id: str | None = None
        self._refresh_pending = False
        self._selected: WavMetadata | None = None
        self._poll_id: str | None = None

        self.window = tk.Tk()
        self.window.title("WavFinder")
        self.window.geometry("1050x680")
        self.window.minsize(760, 440)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._bind_keys()
        self._refresh_library_choices()

        self._poll_id = self.window.after(QUEUE_POLL_MS, self._drain_queue)
        self.start_scan()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        top = ttk.Frame(self.window, padding=5)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Library:").grid(row=0, column=0, sticky=tk.W)
        self._library_var = tk.StringVar(value=ALL_LIBRARIES)
        self._library_box = ttk.Combobox(
            top, textvariable=self._library_var, state="readonly", width=60
        )
        self._library_box.grid(row=0, column=1, sticky=tk.EW, padx=(5, 0))
        self._library_box.bind("<<ComboboxSelected>>", self._on_library_selected)

        ttk.Button(top, text="Rescan", command=self.start_scan).grid(
            row=0, column=2, padx=(5, 0)
        )

        ttk.Label(top, text="Search:").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_changed)
        self._search_entry = ttk.Entry(top, textvariable=self._search_var)
        self._search_entry.grid(row=1, column=1, sticky=tk.EW, padx=(5, 0), pady=(5, 0))
        self._search_entry.focus_set()

        self._case_var = tk.BooleanVar(value=self.config.case_sensitive)
        ttk.Checkbutton(
            top,
            text="Match case",
            variable=self._case_var,
            command=self._on_case_toggled,
        ).grid(row=1, column=2, sticky=tk.W, padx=(5, 0), pady=(5, 0))

        top.columnconfigure(1, weight=1)

        # --- Results table ---
        mid = ttk.Frame(self.window)
        mid.pack(fill=tk.BOTH, expand=True, padx=5)

        self._columns = ("filename", "duration", "description", "path")
        self.tree = ttk.Treeview(mid, columns=self._columns, show="headings", selectmode="browse")
        headings = {
            "filename": "Filename",
            "duration": "Length",
            "description": "Description",
            "path": "Location",
        }
        for col, text in headings.items():
            self.tree.heading(col, text=text, command=lambda c=col: self._sort_column(c))
        self.tree.column("filename", width=230, minwidth=120)
        self.tree.column("duration", width=80, minwidth=60, anchor=tk.E)
        self.tree.column("description", width=330, minwidth=120)
        self.tree.column("path", width=330, minwidth=120)

        scrollbar = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Double-1>", lambda _event: self._open_selected())
        self.tree.bind("<Return>", lambda _event: self._open_selected())

        # --- Preview + actions ---
        bot = ttk.LabelFrame(self.window, text="File Details", padding=5)
        bot.pack(fill=tk.X, padx=5, pady=(0, 5))

        self._preview_text = tk.Text(bot, height=9, wrap=tk.WORD, state=tk.DISABLED)
        self._preview_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Explicit colours: the default selection colours are unreadable against
        # a highlight, and they differ between macOS light and dark mode.
        self._preview_text.tag_configure("match", background="#ffe066", foreground="#000000")

        btn_frame = ttk.Frame(bot, padding=(5, 0))
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Button(btn_frame, text="Open File", command=self._open_selected).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Move To…", command=self._move_selected).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Copy To…", command=self._copy_selected).pack(fill=tk.X, pady=2)

        self._status_var = tk.StringVar(value="Starting…")
        ttk.Label(
            self.window, textvariable=self._status_var, relief=tk.SUNKEN, anchor=tk.W, padding=2
        ).pack(fill=tk.X, side=tk.BOTTOM)

        self._sort_reverse: dict[str, bool] = {c: False for c in self._columns}

    def _bind_keys(self) -> None:
        accelerator = "Command" if sys.platform == "darwin" else "Control"
        self.window.bind(f"<{accelerator}-f>", lambda _event: self._focus_search())
        self.window.bind(f"<{accelerator}-r>", lambda _event: self.start_scan())
        self.window.bind("<Escape>", lambda _event: self._clear_search())

    def _focus_search(self) -> str:
        self._search_entry.focus_set()
        self._search_entry.select_range(0, tk.END)
        return "break"

    def _clear_search(self) -> None:
        self._search_var.set("")

    # -------------------------------------------------------- Libraries --
    def _refresh_library_choices(self) -> None:
        paths = [lib.path for lib in self.config.libraries]
        values = [ALL_LIBRARIES, *paths, ADD_LIBRARY, MANAGE_LIBRARIES]
        self._library_box.configure(values=values)
        if self._library_var.get() not in values:
            self._library_var.set(ALL_LIBRARIES)

    def _on_library_selected(self, _event: object = None) -> None:
        choice = self._library_var.get()
        if choice == ADD_LIBRARY:
            self._library_var.set(ALL_LIBRARIES)
            self._add_library()
        elif choice == MANAGE_LIBRARIES:
            self._library_var.set(ALL_LIBRARIES)
            self._manage_libraries()
        else:
            self._request_refresh()

    def _selected_root(self) -> str | None:
        """The library the results are limited to, or None for all of them."""
        choice = self._library_var.get()
        return None if choice in (ALL_LIBRARIES, ADD_LIBRARY, MANAGE_LIBRARIES) else choice

    def _add_library(self) -> None:
        chosen = filedialog.askdirectory(title="Choose a sound library folder")
        if not chosen:
            return
        if not self.config.add_library(Path(chosen)):
            messagebox.showinfo("WavFinder", "That folder is already a library.")
            return
        config_module.save(self.config)
        self._refresh_library_choices()
        self.start_scan()

    def _manage_libraries(self) -> None:
        LibraryDialog(self.window, self.config, on_change=self._on_libraries_changed)

    def _on_libraries_changed(self) -> None:
        config_module.save(self.config)
        self._refresh_library_choices()
        self.start_scan()

    # --------------------------------------------------------------- Scan --
    def start_scan(self) -> None:
        """Restart indexing from scratch, cancelling any scan already running."""
        self._stop_scan()
        self.engine.clear()
        self._displayed = []
        self._refresh_table_widget()

        roots = indexer.iter_roots(self.config.enabled_paths())
        if not roots:
            self._status_var.set("No libraries yet — pick one from the Library menu.")
            return

        self._cancel_scan = threading.Event()
        cancel = self._cancel_scan
        self._scan_thread = threading.Thread(
            target=self._scan_worker, args=(roots, cancel), daemon=True
        )
        self._scan_thread.start()
        self._status_var.set("Scanning…")

    def _scan_worker(self, roots: list[Path], cancel: threading.Event) -> None:
        """Runs off the main thread. Talks to the UI only through the queue.

        Tk is not thread-safe, so nothing here may touch a widget directly.
        """

        def on_batch(batch: list[WavMetadata]) -> None:
            # A cancelled scan must not push results into the index the
            # replacement scan has just cleared.
            if cancel.is_set():
                return
            self.engine.add_entries(batch)
            self._queue.put(("batch", len(batch)))

        try:
            stats = indexer.scan_library(
                roots, on_batch, cache=self.cache, cancel=cancel
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._queue.put(("error", str(exc)))
            return
        if not stats.cancelled:
            self._queue.put(("scan_done", stats))

    def _drain_queue(self) -> None:
        """Main-thread pump: apply whatever the workers have produced."""
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "batch":
                    self._on_scan_batch()
                elif kind == "scan_done":
                    self._on_scan_done(payload)
                elif kind == "results":
                    self._on_results(*payload)
                elif kind == "error":
                    self._status_var.set(f"Error: {payload}")
        except queue.Empty:
            pass
        self._poll_id = self.window.after(QUEUE_POLL_MS, self._drain_queue)

    def _on_scan_batch(self) -> None:
        self._status_var.set(f"Scanning… {len(self.engine):,} files indexed")
        self._request_refresh()

    def _on_scan_done(self, stats: indexer.ScanStats) -> None:
        parts = [f"Ready — {stats.indexed:,} files"]
        if stats.from_cache:
            parts.append(f"{stats.from_cache:,} from cache")
        if stats.unreadable:
            parts.append(f"{stats.unreadable:,} unreadable")
        self._status_var.set(" · ".join(parts))
        self._request_refresh()

    # ------------------------------------------------------------ Search --
    def _on_search_changed(self, *_args: object) -> None:
        """Debounce: wait SEARCH_DEBOUNCE_MS after the user stops typing."""
        if self._debounce_id is not None:
            self.window.after_cancel(self._debounce_id)
        self._debounce_id = self.window.after(SEARCH_DEBOUNCE_MS, self._run_search)

    def _on_case_toggled(self) -> None:
        self.config.case_sensitive = self._case_var.get()
        config_module.save(self.config)
        self._run_search()

    def _request_refresh(self) -> None:
        """Re-run the current search soon, coalescing repeated requests."""
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.window.after(SEARCH_DEBOUNCE_MS, self._run_search)

    def _run_search(self) -> None:
        self._refresh_pending = False
        self._debounce_id = None
        self._search_generation += 1
        generation = self._search_generation
        query = self._search_var.get()
        case_sensitive = self._case_var.get()
        root = self._selected_root()

        def worker() -> None:
            outcome = self.engine.search(
                query,
                limit=RESULT_LIMIT,
                case_sensitive=case_sensitive,
                root=root,
            )
            self._queue.put(("results", (generation, outcome)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_results(self, generation: int, outcome) -> None:
        # A slower search from an earlier keystroke must not overwrite a newer one.
        if generation != self._search_generation:
            return
        self._displayed = [result.entry for result in outcome.results]
        self._displayed_terms = outcome.results[0].terms if outcome.results else []
        self._refresh_table_widget()
        if outcome.truncated:
            self._status_var.set(
                f"Showing the best {len(self._displayed):,} matches — refine the search to narrow it"
            )

    def _refresh_table_widget(self) -> None:
        """Redraw the table from self._displayed, preserving the selection."""
        previous = self._selected.file_path if self._selected else None

        self.tree.delete(*self.tree.get_children())
        self._item_to_meta.clear()

        reselect = None
        for idx, meta in enumerate(self._displayed):
            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    meta.file_name,
                    meta.format_duration(),
                    meta.description_summary(),
                    meta.parent_dir,
                ),
            )
            self._item_to_meta[item_id] = idx
            if previous is not None and meta.file_path == previous:
                reselect = item_id

        if reselect is not None:
            self.tree.selection_set(reselect)
            self.tree.see(reselect)

    # --------------------------------------------------- Row selection --
    def _on_row_select(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        meta = self._current_meta()
        if meta is not None:
            self._selected = meta
            self._show_preview(meta)

    def _current_meta(self) -> WavMetadata | None:
        sel = self.tree.selection()
        if not sel:
            return None
        idx = self._item_to_meta.get(sel[0])
        if idx is None or idx >= len(self._displayed):
            return None
        return self._displayed[idx]

    def _show_preview(self, meta: WavMetadata) -> None:
        lines = [
            f"Name:      {meta.file_name}",
            f"Location:  {meta.parent_dir}",
            f"Length:    {meta.format_duration()}",
        ]
        descriptive = meta.descriptive_tags()
        if descriptive:
            lines.append("")
            for key, value in descriptive.items():
                lines.append(f"{key}: {value}")
        text = "\n".join(lines)

        self._preview_text.configure(state=tk.NORMAL)
        self._preview_text.delete("1.0", tk.END)
        self._preview_text.insert("1.0", text)
        self._highlight_preview(text)
        self._preview_text.configure(state=tk.DISABLED)

    def _highlight_preview(self, text: str) -> None:
        """Paint the search terms wherever they appear in the details pane."""
        self._preview_text.tag_remove("match", "1.0", tk.END)
        if not self._displayed_terms:
            return
        spans = find_match_spans(
            text, self._displayed_terms, case_sensitive=self._case_var.get()
        )
        for start, end in spans:
            self._preview_text.tag_add(
                "match", f"1.0 + {start} chars", f"1.0 + {end} chars"
            )

    # -------------------------------------------------------- File actions --
    def _open_selected(self) -> None:
        meta = self._current_meta()
        if meta is None:
            return
        if not meta.file_path.exists():
            messagebox.showwarning("WavFinder", f"{meta.file_name} is no longer there.")
            return
        _open_system(meta.file_path)

    def _move_selected(self) -> None:
        self._transfer_selected(copy=False)

    def _copy_selected(self) -> None:
        self._transfer_selected(copy=True)

    def _transfer_selected(self, *, copy: bool) -> None:
        meta = self._current_meta()
        if meta is None:
            messagebox.showinfo("WavFinder", "Select a file first.")
            return
        verb = "Copy" if copy else "Move"
        dest = filedialog.askdirectory(
            title=f"{verb} {meta.file_name} to…",
            initialdir=self.config.last_move_dir or str(meta.file_path.parent),
        )
        if not dest:
            return

        kwargs: dict = {"copy": copy}
        while True:
            try:
                result = fileops.transfer(meta.file_path, Path(dest), **kwargs)
                break
            except fileops.CollisionError as exc:
                choice = _ask_collision(self.window, exc.destination)
                if choice is None:
                    return
                kwargs[choice] = True
            except (OSError, ValueError) as exc:
                messagebox.showerror("WavFinder", f"Could not {verb.lower()} the file:\n{exc}")
                return

        self.config.last_move_dir = dest
        config_module.save(self.config)
        self._after_transfer(meta, result, copy=copy)

    def _after_transfer(self, meta: WavMetadata, result, *, copy: bool) -> None:
        """Keep the index honest about where the file now lives."""
        if not copy:
            self.cache.forget(meta.file_path)
            moved = indexer.reindex_file(result.destination, cache=self.cache)
            if moved is not None and self.engine.replace_entry(meta, moved):
                self._selected = moved
                for idx, entry in enumerate(self._displayed):
                    if entry is meta:
                        self._displayed[idx] = moved
                        break
                self._refresh_table_widget()
                self._show_preview(moved)

        verb = "Copied" if copy else "Moved"
        extra = f" (+{len(result.sidecars)} sidecar)" if result.sidecars else ""
        self._status_var.set(f"{verb} {result.destination.name} to {result.destination.parent}{extra}")

    # ------------------------------------------------------------ Sort --
    def _sort_column(self, col: str) -> None:
        """Sort on the underlying values, not the strings shown in the cells.

        Sorting the rendered text puts '9.5s' after '10.2s', which is exactly
        the sort a user reaches for the Length column to avoid.
        """
        keys = {
            "filename": lambda m: m.file_name.lower(),
            "duration": lambda m: m.duration_seconds,
            "description": lambda m: m.description.lower(),
            "path": lambda m: str(m.file_path).lower(),
        }
        reverse = self._sort_reverse[col]
        self._displayed.sort(key=keys[col], reverse=reverse)
        self._sort_reverse[col] = not reverse
        self._refresh_table_widget()

    # ------------------------------------------------------------- Run --
    def run(self) -> None:
        self.window.mainloop()

    def _on_close(self) -> None:
        self._stop_scan()
        # Stop the pump before the widgets go, or Tk complains about an "after"
        # callback whose command no longer exists.
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        config_module.save(self.config)
        self.cache.close()
        self.window.destroy()

    def _stop_scan(self) -> None:
        """Cancel the scan and wait for it to actually stop.

        The worker holds the same SQLite connection the cache closes, so
        returning before it has finished risks tearing the connection out from
        under a query that is still running.
        """
        self._cancel_scan.set()
        thread = self._scan_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._scan_thread = None


class LibraryDialog(tk.Toplevel):
    """Add, remove and switch off library folders."""

    def __init__(self, parent: tk.Misc, config: config_module.Config, on_change) -> None:
        super().__init__(parent)
        self.title("Libraries")
        self.geometry("620x300")
        self.transient(parent)
        self.config_obj = config
        self.on_change = on_change
        self._dirty = False

        frame = ttk.Frame(self, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame, text="Untick a library to leave it out of the search."
        ).pack(anchor=tk.W, pady=(0, 6))

        self._list_frame = ttk.Frame(frame)
        self._list_frame.pack(fill=tk.BOTH, expand=True)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(buttons, text="Add…", command=self._add).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Close", command=self._close).pack(side=tk.RIGHT)

        self._render()
        self.grab_set()

    def _render(self) -> None:
        for child in self._list_frame.winfo_children():
            child.destroy()
        if not self.config_obj.libraries:
            ttk.Label(self._list_frame, text="No libraries yet.").pack(anchor=tk.W)
            return
        for lib in self.config_obj.libraries:
            row = ttk.Frame(self._list_frame)
            row.pack(fill=tk.X, pady=1)
            var = tk.BooleanVar(value=lib.enabled)
            ttk.Checkbutton(
                row,
                variable=var,
                command=lambda p=lib.path, v=var: self._toggle(p, v.get()),
            ).pack(side=tk.LEFT)
            missing = "" if Path(lib.path).is_dir() else "  (not found)"
            ttk.Label(row, text=lib.path + missing).pack(side=tk.LEFT, padx=4)
            ttk.Button(
                row, text="Remove", width=8, command=lambda p=lib.path: self._remove(p)
            ).pack(side=tk.RIGHT)

    def _add(self) -> None:
        chosen = filedialog.askdirectory(title="Choose a sound library folder", parent=self)
        if chosen and self.config_obj.add_library(Path(chosen)):
            self._dirty = True
            self._render()

    def _remove(self, path: str) -> None:
        self.config_obj.remove_library(path)
        self._dirty = True
        self._render()

    def _toggle(self, path: str, enabled: bool) -> None:
        self.config_obj.set_enabled(path, enabled)
        self._dirty = True

    def _close(self) -> None:
        self.grab_release()
        self.destroy()
        if self._dirty:
            self.on_change()


def _ask_collision(parent: tk.Misc, destination: Path) -> str | None:
    """Ask what to do about an existing file. Returns a transfer() kwarg name."""
    dialog = tk.Toplevel(parent)
    dialog.title("File already exists")
    dialog.transient(parent)
    dialog.resizable(False, False)

    choice: dict[str, str | None] = {"value": None}

    ttk.Label(
        dialog,
        text=f"“{destination.name}” already exists in\n{destination.parent}",
        padding=12,
        justify=tk.LEFT,
    ).pack()

    buttons = ttk.Frame(dialog, padding=(12, 0, 12, 12))
    buttons.pack(fill=tk.X)

    def pick(value: str | None) -> None:
        choice["value"] = value
        dialog.grab_release()
        dialog.destroy()

    ttk.Button(buttons, text="Keep both", command=lambda: pick("rename_on_collision")).pack(
        side=tk.LEFT
    )
    ttk.Button(buttons, text="Replace", command=lambda: pick("overwrite")).pack(
        side=tk.LEFT, padx=6
    )
    ttk.Button(buttons, text="Cancel", command=lambda: pick(None)).pack(side=tk.RIGHT)

    dialog.protocol("WM_DELETE_WINDOW", lambda: pick(None))
    dialog.grab_set()
    parent.wait_window(dialog)
    return choice["value"]


def _open_system(path: Path) -> None:
    """Open a file with the OS default application."""
    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", str(path)])
    elif system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def main() -> None:
    roots = []
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.is_dir():
            print(f"Error: {path} is not a directory", file=sys.stderr)
            sys.exit(1)
        roots.append(path)
    WavFinderApp(roots).run()


if __name__ == "__main__":
    main()

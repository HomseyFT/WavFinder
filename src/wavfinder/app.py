import os
import platform
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from wavfinder.metadata import extract_metadata
from wavfinder.scanner import scan_wav_files
from wavfinder.search import FuzzySearchEngine

# How long to wait (ms) after the user stops typing before running a search.
SEARCH_DEBOUNCE_MS = 200


class WavFinderApp:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.engine = FuzzySearchEngine()
        self._scan_complete = False

        # --- Window setup ---
        self.window = tk.Tk()
        self.window.title(f"WavFinder — {root_dir}")
        self.window.geometry("1000x650")
        self.window.minsize(700, 400)

        self._build_ui()
        self._bind_keys()

        # Kick off background scan
        self._scan_thread = threading.Thread(target=self._scan, daemon=True)
        self._scan_thread.start()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        # --- Top frame: search bar ---
        top = ttk.Frame(self.window, padding=5)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Search:").pack(side=tk.LEFT)
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_changed)
        self._search_entry = ttk.Entry(top, textvariable=self._search_var)
        self._search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        self._search_entry.focus_set()

        # --- Middle frame: results table ---
        mid = ttk.Frame(self.window)
        mid.pack(fill=tk.BOTH, expand=True, padx=5)

        columns = ("filename", "duration", "sample_rate", "channels", "bit_depth", "tags")
        self.tree = ttk.Treeview(mid, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("filename", text="Filename", command=lambda: self._sort_column("filename"))
        self.tree.heading("duration", text="Duration", command=lambda: self._sort_column("duration"))
        self.tree.heading("sample_rate", text="Sample Rate", command=lambda: self._sort_column("sample_rate"))
        self.tree.heading("channels", text="Ch", command=lambda: self._sort_column("channels"))
        self.tree.heading("bit_depth", text="Bits", command=lambda: self._sort_column("bit_depth"))
        self.tree.heading("tags", text="Tags", command=lambda: self._sort_column("tags"))

        self.tree.column("filename", width=220, minwidth=120)
        self.tree.column("duration", width=80, minwidth=60, anchor=tk.E)
        self.tree.column("sample_rate", width=90, minwidth=60, anchor=tk.E)
        self.tree.column("channels", width=40, minwidth=30, anchor=tk.CENTER)
        self.tree.column("bit_depth", width=45, minwidth=30, anchor=tk.CENTER)
        self.tree.column("tags", width=300, minwidth=100)

        scrollbar = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tree.bind("<Double-1>", self._on_open_file)

        # --- Bottom frame: metadata preview + buttons ---
        bot = ttk.LabelFrame(self.window, text="Metadata Preview", padding=5)
        bot.pack(fill=tk.X, padx=5, pady=(0, 5))

        self._preview_text = tk.Text(bot, height=8, wrap=tk.WORD, state=tk.DISABLED)
        self._preview_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(bot, padding=(5, 0))
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y)
        ttk.Button(btn_frame, text="Open File", command=self._open_selected).pack(pady=2)

        # --- Status bar ---
        self._status_var = tk.StringVar(value="Scanning…")
        status_bar = ttk.Label(self.window, textvariable=self._status_var, relief=tk.SUNKEN, anchor=tk.W, padding=2)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # Lookup: treeview item id -> WavMetadata index
        self._item_to_meta: dict[str, int] = {}
        # Currently displayed results (for opening files)
        self._displayed: list = []
        # Debounce handle
        self._debounce_id: str | None = None
        # Sort state
        self._sort_reverse: dict[str, bool] = {c: False for c in columns}

    # --------------------------------------------------------------- Keys --
    def _bind_keys(self) -> None:
        self.window.bind("<Control-Shift-F>", lambda _: self._search_entry.focus_set())
        self.window.bind("<Control-Shift-f>", lambda _: self._search_entry.focus_set())
        self.window.bind("<Return>", lambda _: self._open_selected())

    # --------------------------------------------------------------- Scan --
    def _scan(self) -> None:
        """Run in a background thread: scan files and push updates to the UI."""
        count = 0
        for path in scan_wav_files(self.root_dir):
            meta = extract_metadata(path)
            if meta is None:
                continue
            self.engine.add_entry(meta)
            count += 1
            # Schedule a UI update on the main thread
            self.window.after(0, self._update_status, f"Scanning… found {count} files")
            # Refresh the table every 20 files (batched for performance)
            if count % 20 == 0:
                self.window.after(0, self._refresh_table)
        self._scan_complete = True
        self.window.after(0, self._refresh_table)
        self.window.after(0, self._update_status, f"Ready — {count} files indexed")

    def _update_status(self, text: str) -> None:
        self._status_var.set(text)

    # ------------------------------------------------------------ Search --
    def _on_search_changed(self, *_args: object) -> None:
        """Debounce: wait SEARCH_DEBOUNCE_MS after the user stops typing."""
        if self._debounce_id is not None:
            self.window.after_cancel(self._debounce_id)
        self._debounce_id = self.window.after(SEARCH_DEBOUNCE_MS, self._refresh_table)

    def _refresh_table(self) -> None:
        """Re-populate the treeview based on the current search query."""
        query = self._search_var.get()
        results = self.engine.search(query, limit=200)

        self.tree.delete(*self.tree.get_children())
        self._item_to_meta.clear()
        self._displayed = [meta for meta, _ in results]

        for idx, (meta, _score) in enumerate(results):
            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    meta.file_name,
                    meta.format_duration(),
                    f"{meta.sample_rate} Hz",
                    meta.channels,
                    meta.bit_depth,
                    meta.tags_summary(),
                ),
            )
            self._item_to_meta[item_id] = idx

    # --------------------------------------------------- Row selection --
    def _on_row_select(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        sel = self.tree.selection()
        if not sel:
            return
        idx = self._item_to_meta.get(sel[0])
        if idx is None:
            return
        meta = self._displayed[idx]
        self._show_preview(meta)

    def _show_preview(self, meta) -> None:
        self._preview_text.configure(state=tk.NORMAL)
        self._preview_text.delete("1.0", tk.END)

        lines = [
            f"File:        {meta.file_path}",
            f"Duration:    {meta.format_duration()}",
            f"Sample Rate: {meta.sample_rate} Hz",
            f"Channels:    {meta.channels}",
            f"Bit Depth:   {meta.bit_depth}",
        ]
        if meta.tags:
            lines.append("")
            lines.append("--- Tags ---")
            for key, value in meta.tags.items():
                lines.append(f"  {key}: {value}")

        self._preview_text.insert("1.0", "\n".join(lines))
        self._preview_text.configure(state=tk.DISABLED)

    # -------------------------------------------------------- Open file --
    def _on_open_file(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        self._open_selected()

    def _open_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = self._item_to_meta.get(sel[0])
        if idx is None:
            return
        meta = self._displayed[idx]
        _open_system(meta.file_path)

    # ------------------------------------------------------------ Sort --
    def _sort_column(self, col: str) -> None:
        reverse = self._sort_reverse[col]
        items = [(self.tree.set(iid, col), iid) for iid in self.tree.get_children()]
        items.sort(key=lambda t: t[0].lower(), reverse=reverse)
        for i, (_, iid) in enumerate(items):
            self.tree.move(iid, "", i)
        self._sort_reverse[col] = not reverse

    # ------------------------------------------------------------- Run --
    def run(self) -> None:
        self.window.mainloop()


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
    import sys

    root_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    if not root_dir.is_dir():
        print(f"Error: {root_dir} is not a directory", file=sys.stderr)
        sys.exit(1)
    app = WavFinderApp(root_dir)
    app.run()


if __name__ == "__main__":
    main()

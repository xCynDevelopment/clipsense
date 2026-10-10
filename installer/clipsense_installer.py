import glob
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

SYSTEM = platform.system()
PACKAGE = os.environ.get("CLIPSENSE_INSTALL_SOURCE", "clipsense[all]")
EXAMPLE = "Watch https://www.youtube.com/watch?v=GvijmN66dZQ and tell me how people kept food cold before fridges"
NO_WINDOW = 0x08000000 if SYSTEM == "Windows" else 0

EXTRA_PATHS = [Path.home() / ".local/bin", Path.home() / ".claude/local", Path.home() / ".cargo/bin"]
if SYSTEM != "Windows":
    EXTRA_PATHS += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), *map(str, EXTRA_PATHS)])


def has_claude_desktop() -> bool:
    if SYSTEM == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        return bool(glob.glob(os.path.join(local, "Packages", "Claude_*"))) or Path(local, "AnthropicClaude").exists()
    if SYSTEM == "Darwin":
        return Path("/Applications/Claude.app").exists() or Path.home().joinpath("Applications/Claude.app").exists()
    return Path.home().joinpath(".config/Claude").exists()


def has_claude_code() -> bool:
    return shutil.which("claude") is not None


def has_cursor() -> bool:
    return Path.home().joinpath(".cursor").exists()


def find_uv() -> str | None:
    exe = "uv.exe" if SYSTEM == "Windows" else "uv"
    for candidate in (shutil.which("uv"), Path.home() / ".local/bin" / exe, Path.home() / ".cargo/bin" / exe):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("clipsense Setup")
        self.s = self.ui_scale()
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Body.TLabel", font=("Segoe UI", 10))
        style.configure("Alert.TLabel", font=("Segoe UI", 10, "bold"), foreground="#b45309")

        self.use_desktop = tk.BooleanVar(value=has_claude_desktop())
        self.use_code = tk.BooleanVar(value=has_claude_code())
        self.use_cursor = tk.BooleanVar(value=has_cursor())
        self.engine = tk.StringVar(value="local")
        self.groq_key = tk.StringVar()
        self.events: queue.Queue = queue.Queue()
        self.wrapping: list[ttk.Label] = []

        self.nav = ttk.Frame(self, padding=(self.px(28), self.px(8), self.px(28), self.px(16)))
        self.nav.pack(side="bottom", fill="x")
        self.back_btn = ttk.Button(self.nav, text="Back", command=self.back)
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(self.nav, text="Next", command=self.next)
        self.next_btn.pack(side="right")

        holder = ttk.Frame(self)
        holder.pack(side="top", fill="both", expand=True)
        background = style.lookup("TFrame", "background") or self.cget("background")
        self.canvas = tk.Canvas(holder, highlightthickness=0, borderwidth=0, background=background)
        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas, padding=(self.px(28), self.px(24), self.px(28), self.px(8)))
        self.body_window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.relayout())
        self.canvas.bind("<Configure>", lambda e: self.relayout())
        self.bind_all("<MouseWheel>", self.on_wheel)

        width, height = self.size = self.fit(620, 480)
        self.geometry(f"{width}x{height}+{(self.winfo_screenwidth() - width) // 2}+{(self.winfo_screenheight() - height) // 3}")
        self.minsize(*self.fit(360, 300))

        self.pages = [self.page_welcome, self.page_apps, self.page_engine, self.page_install, self.page_done]
        self.index = 0
        self.show()

    def ui_scale(self) -> float:
        if SYSTEM == "Darwin":
            return 1.0
        return max(1.0, self.winfo_fpixels("1i") / 96)

    def px(self, n: float) -> int:
        return int(n * self.s)

    def fit(self, width: int, height: int) -> tuple[int, int]:
        return (min(self.px(width), int(self.winfo_screenwidth() * 0.92)),
                min(self.px(height), int(self.winfo_screenheight() * 0.85)))

    def relayout(self):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width <= 1:
            return
        wrap = max(self.px(160), width - self.px(60))
        for label in self.wrapping:
            if int(str(label.cget("wraplength") or 0)) != wrap:
                label.configure(wraplength=wrap)
        needed = self.body.winfo_reqheight()
        self.canvas.itemconfigure(self.body_window, width=width, height=max(height, needed))
        self.canvas.configure(scrollregion=(0, 0, width, max(height, needed)))
        overflow = needed > height + 2
        if overflow and not self.scrollbar.winfo_ismapped():
            self.scrollbar.pack(side="right", fill="y", before=self.canvas)
        elif not overflow and self.scrollbar.winfo_ismapped():
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)

    def grow_to_fit(self):
        self.update_idletasks()
        if self.winfo_ismapped():
            self.size = (self.winfo_width(), self.winfo_height())
        width, height = self.size
        needed = self.body.winfo_reqheight() + self.nav.winfo_reqheight()
        limit = int(self.winfo_screenheight() * 0.85)
        if needed > height and height < limit:
            self.size = (width, min(needed, limit))
            self.geometry("{}x{}".format(*self.size))

    def on_wheel(self, event):
        if isinstance(event.widget, tk.Text) or not self.scrollbar.winfo_ismapped():
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def wrap_label(self, text: str, style: str = "Body.TLabel", **pack) -> ttk.Label:
        label = ttk.Label(self.body, text=text, style=style, justify="left", wraplength=self.px(540))
        label.pack(anchor="w", **pack)
        self.wrapping.append(label)
        return label

    def clear(self):
        self.wrapping.clear()
        for child in self.body.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)

    def heading(self, title: str, text: str):
        self.wrap_label(title, "Title.TLabel")
        self.wrap_label(text, pady=(self.px(8), self.px(16)))

    def show(self):
        self.clear()
        self.back_btn.state(["!disabled"] if 0 < self.index < 3 else ["disabled"])
        self.next_btn.configure(text="Next", command=self.next)
        self.next_btn.state(["!disabled"])
        self.pages[self.index]()
        self.relayout()
        self.after_idle(self.grow_to_fit)

    def next(self):
        if self.index == 1 and not (self.use_desktop.get() or self.use_code.get() or self.use_cursor.get()):
            self.alert.configure(text="Pick at least one app.")
            return
        if self.index == 2 and self.engine.get() == "groq" and not self.groq_key.get().strip():
            self.alert.configure(text="Paste your Groq key, or choose the free option.")
            return
        self.index += 1
        self.show()

    def back(self):
        self.index -= 1
        self.show()

    def page_welcome(self):
        self.heading(
            "Let your AI watch videos",
            "clipsense lets Claude and other AI apps watch and listen to videos: it hears everything "
            "that's said, sees each new shot, and notices sounds like laughter or music.\n\n"
            "This will set everything up in a few minutes. You'll need an internet connection.",
        )

    def page_apps(self):
        self.heading("Which apps do you use?", "clipsense will be added to each app you pick.")
        for text, var, found in (
            ("Claude Desktop", self.use_desktop, has_claude_desktop()),
            ("Claude Code", self.use_code, has_claude_code()),
            ("Cursor", self.use_cursor, has_cursor()),
        ):
            label = text if found else f"{text}  (not found on this computer)"
            ttk.Checkbutton(self.body, text=label, variable=var).pack(anchor="w", pady=self.px(4))
        self.alert = self.wrap_label("", "Alert.TLabel", pady=(self.px(12), 0))

    def page_engine(self):
        self.heading("How should speech be transcribed?", "You can change this later by running setup again.")
        ttk.Radiobutton(self.body, text="Free, on this computer (recommended)", value="local",
                        variable=self.engine, command=self.toggle_key).pack(anchor="w", pady=self.px(4))
        hint = self.wrap_label("Private and free. Slower on older computers.", padx=(self.px(24), 0))
        hint.configure(foreground="#555")
        ttk.Radiobutton(self.body, text="Groq (faster and more accurate, free key)", value="groq",
                        variable=self.engine, command=self.toggle_key).pack(anchor="w", pady=(self.px(14), self.px(4)))
        row = ttk.Frame(self.body)
        row.pack(anchor="w", padx=(self.px(24), 0), fill="x")
        link = ttk.Label(row, text="Get a free key", foreground="#2563eb", cursor="hand2")
        link.pack(side="right", padx=(self.px(10), 0))
        link.bind("<Button-1>", lambda e: webbrowser.open("https://console.groq.com/keys"))
        self.key_entry = ttk.Entry(row, textvariable=self.groq_key, width=20, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True)
        self.alert = self.wrap_label("", "Alert.TLabel", pady=(self.px(12), 0))
        self.toggle_key()

    def toggle_key(self):
        self.key_entry.state(["!disabled"] if self.engine.get() == "groq" else ["disabled"])

    def page_install(self):
        self.heading("Installing", "This can take a few minutes the first time.")
        self.step = self.wrap_label("Starting...")
        self.bar = ttk.Progressbar(self.body, mode="indeterminate")
        self.bar.pack(fill="x", pady=(self.px(8), self.px(10)))
        self.bar.start(12)
        self.alert = self.wrap_label("", "Alert.TLabel")
        self.log = tk.Text(self.body, height=8, font=("Consolas", 9), relief="flat", background="#f4f4f5", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(self.px(8), 0))
        self.next_btn.state(["disabled"])
        threading.Thread(target=self.install, daemon=True).start()
        self.after(100, self.pump)

    def page_done(self):
        self.heading(
            "You're all set!",
            "Open Claude (or your other app), start a new chat, and give it a video file or a link. Try:",
        )
        box = tk.Text(self.body, height=3, font=("Segoe UI", 10), wrap="word", relief="flat", background="#f4f4f5")
        box.insert("1.0", EXAMPLE)
        box.configure(state="disabled")
        box.pack(fill="x")
        ttk.Button(self.body, text="Copy", command=self.copy_example).pack(anchor="w", pady=self.px(8))
        self.next_btn.configure(text="Finish", command=self.destroy)

    def copy_example(self):
        self.clipboard_clear()
        self.clipboard_append(EXAMPLE)

    def pump(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "step":
                    self.step.configure(text=value)
                elif kind == "log":
                    self.log.insert("end", value + "\n")
                    self.log.see("end")
                    if "Please quit" in value:
                        self.alert.configure(text=self.quit_hint())
                    elif "Added clipsense to" in value:
                        self.alert.configure(text="")
                elif kind == "done":
                    self.bar.stop()
                    self.index += 1
                    self.show()
                    return
                elif kind == "error":
                    self.bar.stop()
                    self.step.configure(text="Something went wrong.")
                    self.alert.configure(text=value)
                    self.next_btn.configure(text="Close", command=self.destroy)
                    self.next_btn.state(["!disabled"])
                    return
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def quit_hint(self) -> str:
        if SYSTEM == "Windows":
            return ("Almost done: quit Claude Desktop so clipsense can be added. Right-click the Claude icon "
                    "in the system tray (bottom-right, near the clock) and choose Quit. It will reopen by itself.")
        return "Almost done: quit Claude Desktop (Claude menu > Quit Claude). It will reopen by itself."

    def run(self, cmd: list[str]) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", NO_COLOR="1")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", env=env, creationflags=NO_WINDOW)
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.events.put(("log", line))
        if proc.wait() != 0:
            raise RuntimeError(f"`{' '.join(cmd[:3])}` failed. See the log above.")

    def install(self):
        try:
            self.events.put(("step", "Step 1 of 3: Getting the uv installer"))
            uv = find_uv()
            if not uv:
                if SYSTEM == "Windows":
                    self.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                              "irm https://astral.sh/uv/install.ps1 | iex"])
                else:
                    self.run(["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"])
                uv = find_uv()
                if not uv:
                    raise RuntimeError("Couldn't install uv. Check your internet connection and try again.")

            self.events.put(("step", "Step 2 of 3: Installing clipsense (a few minutes)"))
            self.run([uv, "tool", "install", "--force", "--python", "3.12", PACKAGE])

            self.events.put(("step", "Step 3 of 3: Downloading models and connecting your apps"))
            bin_dir = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True, text=True,
                                     creationflags=NO_WINDOW).stdout.strip()
            clipsense = str(Path(bin_dir) / ("clipsense.exe" if SYSTEM == "Windows" else "clipsense"))
            apps = [name for name, var in (("claude-desktop", self.use_desktop), ("claude-code", self.use_code),
                                           ("cursor", self.use_cursor)) if var.get()]
            cmd = [clipsense, "setup", *apps]
            if self.engine.get() == "groq":
                cmd += ["--groq-key", self.groq_key.get().strip()]
            self.run(cmd)
            self.events.put(("done", None))
        except Exception as e:
            self.events.put(("error", str(e)))


def selftest() -> None:
    app = Installer()
    app.install = lambda: None
    app.use_desktop.set(True)
    for expected in (1, 2, 3):
        app.next()
        app.update()
        assert app.index == expected, f"stuck on page {app.index}"
    app.index = 4
    app.show()
    app.update()
    app.destroy()
    print("selftest ok")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    if "--selftest" in sys.argv:
        selftest()
    else:
        Installer().mainloop()

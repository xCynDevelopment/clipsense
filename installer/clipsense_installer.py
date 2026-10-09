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
        self.geometry("600x460")
        self.resizable(False, False)
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

        self.body = ttk.Frame(self, padding=(28, 24, 28, 8))
        self.body.pack(fill="both", expand=True)
        nav = ttk.Frame(self, padding=(28, 8, 28, 20))
        nav.pack(fill="x")
        self.back_btn = ttk.Button(nav, text="Back", command=self.back)
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(nav, text="Next", command=self.next)
        self.next_btn.pack(side="right")

        self.pages = [self.page_welcome, self.page_apps, self.page_engine, self.page_install, self.page_done]
        self.index = 0
        self.show()

    def clear(self):
        for child in self.body.winfo_children():
            child.destroy()

    def heading(self, title: str, text: str):
        ttk.Label(self.body, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text=text, style="Body.TLabel", wraplength=540, justify="left").pack(anchor="w", pady=(8, 16))

    def show(self):
        self.clear()
        self.back_btn.state(["!disabled"] if 0 < self.index < 3 else ["disabled"])
        self.next_btn.configure(text="Next", command=self.next)
        self.next_btn.state(["!disabled"])
        self.pages[self.index]()

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
            ttk.Checkbutton(self.body, text=label, variable=var).pack(anchor="w", pady=4)
        self.alert = ttk.Label(self.body, text="", style="Alert.TLabel")
        self.alert.pack(anchor="w", pady=(12, 0))

    def page_engine(self):
        self.heading("How should speech be transcribed?", "You can change this later by running setup again.")
        ttk.Radiobutton(self.body, text="Free, on this computer (recommended)", value="local",
                        variable=self.engine, command=self.toggle_key).pack(anchor="w", pady=4)
        ttk.Label(self.body, text="Private and free. Slower on older computers.", style="Body.TLabel",
                  foreground="#555").pack(anchor="w", padx=24)
        ttk.Radiobutton(self.body, text="Groq (faster and more accurate, free key)", value="groq",
                        variable=self.engine, command=self.toggle_key).pack(anchor="w", pady=(14, 4))
        row = ttk.Frame(self.body)
        row.pack(anchor="w", padx=24, fill="x")
        self.key_entry = ttk.Entry(row, textvariable=self.groq_key, width=48, show="•")
        self.key_entry.pack(side="left")
        link = ttk.Label(row, text="Get a free key", foreground="#2563eb", cursor="hand2")
        link.pack(side="left", padx=10)
        link.bind("<Button-1>", lambda e: webbrowser.open("https://console.groq.com/keys"))
        self.alert = ttk.Label(self.body, text="", style="Alert.TLabel")
        self.alert.pack(anchor="w", pady=(12, 0))
        self.toggle_key()

    def toggle_key(self):
        self.key_entry.state(["!disabled"] if self.engine.get() == "groq" else ["disabled"])

    def page_install(self):
        self.heading("Installing", "This can take a few minutes the first time.")
        self.step = ttk.Label(self.body, text="Starting...", style="Body.TLabel")
        self.step.pack(anchor="w")
        self.bar = ttk.Progressbar(self.body, mode="indeterminate", length=540)
        self.bar.pack(anchor="w", pady=(8, 10))
        self.bar.start(12)
        self.alert = ttk.Label(self.body, text="", style="Alert.TLabel", wraplength=540, justify="left")
        self.alert.pack(anchor="w")
        self.log = tk.Text(self.body, height=10, font=("Consolas", 9), relief="flat", background="#f4f4f5")
        self.log.pack(fill="both", expand=True, pady=(8, 0))
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
        ttk.Button(self.body, text="Copy", command=self.copy_example).pack(anchor="w", pady=8)
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


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    Installer().mainloop()

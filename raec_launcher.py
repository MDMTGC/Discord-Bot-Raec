import customtkinter as ctk
import subprocess
import threading
import sys
import os
import signal
from datetime import datetime

# =============================================================================
# CONFIGURATION
# =============================================================================
BOT_FILENAME = "raec_v3_organic.py"
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

# Colors
COLOR_BG = "#0f0f0f"        # Deep void black
COLOR_PANEL = "#1a1a1a"     # Dark grey panels
COLOR_ACCENT = "#1f6aa5"    # Raec Blue/Cyan
COLOR_TEXT_LOG = "#00ff99"  # Matrix green for logs
COLOR_TEXT_ERR = "#ff3333"  # Red for errors

class RaecConsole(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Setup
        self.title("RAEC // CONTROL DECK")
        self.geometry("900x600")
        self.configure(fg_color=COLOR_BG)
        self.process = None
        self.is_running = False

        # --- LAYOUT ---
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 1. SIDEBAR (Controls)
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0, fg_color=COLOR_PANEL)
        self.sidebar.grid(row=0, column=0, rowspan=3, sticky="nsew")
        self.sidebar.grid_rowconfigure(4, weight=1)

        self.lbl_title = ctk.CTkLabel(self.sidebar, text="R.A.E.C.", font=("Roboto Medium", 24))
        self.lbl_title.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.lbl_subtitle = ctk.CTkLabel(self.sidebar, text="v3.0 ORGANIC", font=("Roboto", 12), text_color="gray")
        self.lbl_subtitle.grid(row=1, column=0, padx=20, pady=(0, 20))

        # Status Indicator
        self.lbl_status = ctk.CTkLabel(self.sidebar, text="● SYSTEM OFFLINE", font=("Roboto Medium", 12), text_color="gray")
        self.lbl_status.grid(row=2, column=0, padx=20, pady=10)

        # Buttons
        self.btn_start = ctk.CTkButton(self.sidebar, text="INITIALIZE", command=self.start_bot, fg_color=COLOR_ACCENT)
        self.btn_start.grid(row=3, column=0, padx=20, pady=10)

        self.btn_stop = ctk.CTkButton(self.sidebar, text="SEVER CONNECTION", command=self.stop_bot, fg_color="#444", state="disabled")
        self.btn_stop.grid(row=4, column=0, padx=20, pady=10)

        # 2. CONSOLE WINDOW (The "Monitor")
        self.console_frame = ctk.CTkFrame(self, fg_color=COLOR_BG)
        self.console_frame.grid(row=0, column=1, rowspan=3, sticky="nsew", padx=10, pady=10)
        
        self.lbl_log = ctk.CTkLabel(self.console_frame, text=" > NEURAL LINK ESTABLISHED...", font=("Consolas", 12), anchor="w")
        self.lbl_log.pack(fill="x", padx=5, pady=5)

        self.log_box = ctk.CTkTextbox(self.console_frame, font=("Consolas", 11), text_color="#cccccc", fg_color="#000000")
        self.log_box.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_box.insert("0.0", "Waiting for command...\n")
        self.log_box.configure(state="disabled")

        # 3. ENV CHECKER
        self.check_env()

    def log(self, message, is_error=False):
        """Thread-safe logging to the GUI text box."""
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        full_msg = f"{timestamp} {message}\n"
        
        self.log_box.configure(state="normal")
        self.log_box.insert("end", full_msg)
        
        # Colorize specific outputs (Thoughts vs Impressions)
        if "🧠" in message:
            # Highlight thoughts
            pass 
        
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def check_env(self):
        if not os.path.exists(".env"):
            self.log("CRITICAL: .env file missing!", is_error=True)
            self.log("Please create .env with DISCORD_TOKEN and GEMINI_API_KEY.")
            self.btn_start.configure(state="disabled")

    def start_bot(self):
        if self.is_running: return

        self.is_running = True
        self.btn_start.configure(state="disabled", text="RUNNING...")
        self.btn_stop.configure(state="normal", fg_color="#990000", hover_color="#cc0000")
        self.lbl_status.configure(text="● ONLINE", text_color="#00ff00")
        
        self.log("--- INITIATING RAEC PROTOCOL ---")

        # Run the bot in a separate thread to keep UI responsive
        self.thread = threading.Thread(target=self.run_process)
        self.thread.daemon = True
        self.thread.start()

  def run_process(self):
        # Ensure the filename matches EXACTLY what you named your bot script
        if not os.path.exists(BOT_FILENAME):
            self.after(0, self.log, f"ERROR: Could not find file '{BOT_FILENAME}'", True)
            self.after(0, self.on_process_exit)
            return

        command = [sys.executable, "-u", BOT_FILENAME]
        
        creation_flags = 0
        if os.name == 'nt':
            creation_flags = subprocess.CREATE_NO_WINDOW

        # CHANGE HERE: We map stderr to stdout so errors show up in the GUI
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # <--- IMPORTANT CHANGE
            text=True,
            creationflags=creation_flags
        )

        while True:
            output = self.process.stdout.readline()
            if output == '' and self.process.poll() is not None:
                break
            if output:
                self.after(0, self.log, output.strip())
        
        self.after(0, self.on_process_exit)
    def stop_bot(self):
        if self.process and self.is_running:
            self.log("--- SEVERING CONNECTION ---")
            self.process.terminate()
            self.is_running = False

    def on_process_exit(self):
        self.is_running = False
        self.btn_start.configure(state="normal", text="INITIALIZE")
        self.btn_stop.configure(state="disabled", fg_color="#444")
        self.lbl_status.configure(text="● DISCONNECTED", text_color="red")
        self.log("Process Terminated.")

    def on_closing(self):
        self.stop_bot()
        self.destroy()

if __name__ == "__main__":
    app = RaecConsole()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
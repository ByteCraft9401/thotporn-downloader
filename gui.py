#!/usr/bin/env python3

import contextlib
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import thotp_downloader as downloader


class QueueWriter:
    def __init__(self, output_queue):
        self.output_queue = output_queue

    def write(self, text):
        if text:
            self.output_queue.put(("log", text))

    def flush(self):
        pass


class DownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("THOTP Downloader")
        self.root.geometry("820x580")
        self.root.minsize(720, 520)

        self.output_queue = queue.Queue()
        self.worker = None
        self.active_control = None
        self.folder_var = tk.StringVar(value=downloader.DOWNLOADS_ROOT)
        self.url_var = tk.StringVar()
        self.page_var = tk.StringVar()
        self.video_pause_var = tk.StringVar(value=str(downloader.VIDEO_SUCCESS_PAUSE_SECONDS))
        self.status_var = tk.StringVar(value=downloader.active_license_label())
        self.task_status_var = tk.StringVar(value="Estado: Finalizado")
        self.progress_var = tk.StringVar(value="0%")
        self.version_var = tk.StringVar(value=f"Version: {downloader.CONFIG.version}")

        self.build_ui()
        self.root.after(100, self.process_queue)

    def build_ui(self):
        self.root.configure(bg="#f5f7fb")

        style = ttk.Style()
        style.configure("TFrame", background="#f5f7fb")
        style.configure("Header.TLabel", background="#f5f7fb", font=("Segoe UI", 16, "bold"))
        style.configure("TLabel", background="#f5f7fb", font=("Segoe UI", 10))
        style.configure("Status.TLabel", background="#f5f7fb", font=("Segoe UI", 10, "bold"))
        style.configure("TButton", font=("Segoe UI", 10))

        main = ttk.Frame(self.root, padding=18)
        main.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 14))

        ttk.Label(header, text="THOTP Downloader", style="Header.TLabel").pack(side=tk.LEFT)
        version_actions = ttk.Frame(header)
        version_actions.pack(side=tk.RIGHT)
        ttk.Label(version_actions, textvariable=self.version_var).pack(anchor=tk.E)
        self.update_button = ttk.Button(version_actions, text="Actualizar", command=self.start_update)
        self.update_button.pack(anchor=tk.E, pady=(4, 0))

        status_row = ttk.Frame(main)
        status_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(status_row, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.LEFT)
        ttk.Label(status_row, textvariable=self.task_status_var, style="Status.TLabel").pack(side=tk.RIGHT)

        url_row = ttk.Frame(main)
        url_row.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(url_row, text="URL").pack(anchor=tk.W)
        ttk.Entry(url_row, textvariable=self.url_var).pack(fill=tk.X, pady=(4, 0))

        options = ttk.Frame(main)
        options.pack(fill=tk.X, pady=(0, 10))

        page_frame = ttk.Frame(options)
        page_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        ttk.Label(page_frame, text="Paginas (ej: 1 o 1,3,5)").pack(anchor=tk.W)
        ttk.Entry(page_frame, textvariable=self.page_var).pack(fill=tk.X, pady=(4, 0))

        pause_frame = ttk.Frame(options)
        pause_frame.pack(side=tk.LEFT, fill=tk.X, padx=(0, 10))
        ttk.Label(pause_frame, text="Pausa videos (seg)").pack(anchor=tk.W)
        ttk.Entry(pause_frame, textvariable=self.video_pause_var, width=12).pack(fill=tk.X, pady=(4, 0))

        folder_frame = ttk.Frame(options)
        folder_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(folder_frame, text="Carpeta de descarga").pack(anchor=tk.W)
        folder_controls = ttk.Frame(folder_frame)
        folder_controls.pack(fill=tk.X, pady=(4, 0))
        ttk.Entry(folder_controls, textvariable=self.folder_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(folder_controls, text="Elegir", command=self.choose_folder).pack(side=tk.LEFT, padx=(8, 0))
        self.open_folder_button = ttk.Button(folder_controls, text="Abrir", command=self.open_folder)
        self.open_folder_button.pack(side=tk.LEFT, padx=(8, 0))

        self.actions = ttk.Frame(main)
        self.actions.pack(fill=tk.X, pady=(4, 12))
        self.download_button = ttk.Button(self.actions, text="Descargar", command=self.start_download)
        self.download_button.pack(side=tk.LEFT)
        self.pause_button = ttk.Button(self.actions, text="Pausar", command=self.pause_download)
        self.resume_button = ttk.Button(self.actions, text="Reanudar", command=self.resume_download)
        self.cancel_button = ttk.Button(self.actions, text="Cancelar", command=self.cancel_download)

        self.progress = ttk.Progressbar(main, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(main, textvariable=self.progress_var, style="Status.TLabel").pack(anchor=tk.W, pady=(0, 12))

        log_frame = ttk.Frame(main)
        log_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(log_frame, text="Logs").pack(anchor=tk.W)

        text_frame = ttk.Frame(log_frame)
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.log_text = tk.Text(
            text_frame,
            height=16,
            wrap=tk.WORD,
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            relief=tk.FLAT,
            font=("Consolas", 10),
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def choose_folder(self):
        selected = filedialog.askdirectory(initialdir=self.folder_var.get() or os.getcwd())
        if selected:
            self.folder_var.set(selected)

    def open_folder(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            self.append_log(f"\n[gui] La carpeta no existe: {folder or '(vacia)'}\n")
            return

        if sys.platform.startswith("win"):
            os.startfile(folder)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", folder])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])

    def set_task_state(self, state):
        self.task_status_var.set(f"Estado: {state}")
        self.update_action_buttons(state)

    def update_action_buttons(self, state=None):
        current_state = state or self.task_status_var.get().replace("Estado: ", "", 1)

        for button in (
            self.download_button,
            self.pause_button,
            self.resume_button,
            self.cancel_button,
        ):
            button.pack_forget()

        if current_state in ("Descargando", "Reanudando"):
            self.pause_button.configure(state=tk.NORMAL)
            self.cancel_button.configure(state=tk.NORMAL)
            self.pause_button.pack(side=tk.LEFT)
            self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))
            self.update_button.configure(state=tk.DISABLED)
        elif current_state == "Pausando...":
            self.pause_button.configure(state=tk.DISABLED)
            self.cancel_button.configure(state=tk.NORMAL)
            self.pause_button.pack(side=tk.LEFT)
            self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))
            self.update_button.configure(state=tk.DISABLED)
        elif current_state == "Pausado":
            self.resume_button.configure(state=tk.NORMAL)
            self.cancel_button.configure(state=tk.NORMAL)
            self.resume_button.pack(side=tk.LEFT)
            self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))
            self.update_button.configure(state=tk.DISABLED)
        elif current_state == "Cancelando...":
            self.cancel_button.configure(state=tk.DISABLED)
            self.cancel_button.pack(side=tk.LEFT)
            self.update_button.configure(state=tk.DISABLED)
        else:
            self.download_button.configure(state=tk.NORMAL)
            self.download_button.pack(side=tk.LEFT)
            self.update_button.configure(state=tk.NORMAL)

    def set_busy(self, busy):
        if busy:
            self.set_progress(0, "0%")
        else:
            self.active_control = None

    def set_progress(self, percent, label=None):
        bounded = max(0, min(100, percent))
        self.progress.configure(value=bounded)
        if label:
            self.progress_var.set(label)

    def append_log(self, text):
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def process_queue(self):
        try:
            while True:
                event, payload = self.output_queue.get_nowait()
                if event == "log":
                    self.append_log(payload)
                elif event == "state":
                    self.set_task_state(payload)
                elif event == "progress":
                    self.update_progress(payload)
                elif event == "done":
                    self.set_progress(100, "100%")
                    self.set_busy(False)
                    self.set_task_state("Finalizado")
                elif event == "error":
                    self.set_busy(False)
                    self.set_task_state("Finalizado")
                    self.append_log(f"\n[gui] Error: {payload}\n")
                    messagebox.showerror("THOTP Downloader", str(payload))
        except queue.Empty:
            pass

        self.root.after(100, self.process_queue)

    def run_worker(self, target, control=None):
        if self.worker and self.worker.is_alive():
            return

        self.active_control = control
        self.set_busy(True)
        self.worker = threading.Thread(target=self.capture_output, args=(target,), daemon=True)
        self.worker.start()

    def update_progress(self, payload):
        if isinstance(payload, dict):
            percent = payload.get("percent")
            label = payload.get("label") or ""
            if percent is None:
                self.progress_var.set(label or "Procesando...")
                return

            display_percent = self.format_percent(percent)
            self.set_progress(percent, display_percent)
            return

        self.progress_var.set(str(payload))

    def format_percent(self, percent):
        bounded = max(0, min(100, percent))
        if bounded == int(bounded):
            return f"{int(bounded)}%"
        return f"{bounded:.2f}%"

    def pause_download(self):
        if not self.active_control:
            return

        self.active_control.request_pause()
        self.set_task_state("Pausando...")
        self.append_log("\n[info] Pausa solicitada por el usuario\n")

    def resume_download(self):
        if not self.active_control:
            return

        self.set_task_state("Reanudando...")
        self.append_log("\n[info] Descarga reanudada\n")
        self.active_control.request_resume()
        self.set_task_state("Descargando")

    def cancel_download(self):
        if not self.active_control:
            return

        self.active_control.request_cancel()
        self.set_task_state("Cancelando...")
        self.append_log("\n[info] Cancelación solicitada por el usuario\n")

    def capture_output(self, target):
        writer = QueueWriter(self.output_queue)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                target()
            self.output_queue.put(("done", None))
        except Exception as exc:
            downloader.logging.exception("Error en GUI")
            self.output_queue.put(("error", exc))

    def parse_pages(self):
        raw_pages = self.page_var.get().strip()
        if not raw_pages:
            return None

        return [int(value.strip()) for value in raw_pages.split(",") if value.strip()]

    def parse_video_pause(self):
        raw_pause = self.video_pause_var.get().strip()
        if not raw_pause:
            return downloader.VIDEO_SUCCESS_PAUSE_SECONDS

        value = float(raw_pause)
        if value < 0:
            raise ValueError("La pausa entre videos no puede ser negativa.")
        return value

    def start_download(self):
        url = self.url_var.get().strip()
        folder = self.folder_var.get().strip()

        if not url:
            messagebox.showwarning("THOTP Downloader", "Pega una URL para descargar.")
            return

        if not folder:
            messagebox.showwarning("THOTP Downloader", "Elige una carpeta de descarga.")
            return

        self.progress_var.set("")

        control = downloader.DownloadControl(
            state_callback=lambda state: self.output_queue.put(("state", state))
        )
        stats = downloader.TaskStats(
            progress_callback=lambda label: None,
            ui_progress_callback=lambda payload: self.output_queue.put(("progress", payload))
        )
        self.set_task_state("Descargando")
        self.run_worker(lambda: self.download(url, folder, control, stats), control=control)

    def download(self, url, folder, control, stats):
        selected_pages = self.parse_pages()
        downloader.VIDEO_SUCCESS_PAUSE_SECONDS = self.parse_video_pause()
        os.makedirs(folder, exist_ok=True)

        downloader.check_for_update_notice()

        profile, typ, item_id = downloader.extract_profile_from_url(url)
        stats.single_page_task = downloader.is_single_manual_page_task(
            typ,
            item_id,
            selected_pages,
        )
        stats.page_progress_enabled = (
            typ in ("photo", "video")
            and item_id is None
            and selected_pages is not None
        )
        profile_folder = os.path.join(folder, profile)
        os.makedirs(profile_folder, exist_ok=True)

        print(downloader.active_license_label())
        print(f"[gui] Carpeta: {folder}")

        if not control.wait_until_can_start():
            return

        if typ == "photo" and item_id:
            downloader.process_single_photo_by_id(profile, item_id, profile_folder)
        elif typ == "video" and item_id:
            downloader.process_single_video_by_id(profile, item_id, profile_folder)
        elif typ == "photo":
            downloader.crawl_collection(
                profile,
                "photos",
                folder,
                selected_pages,
                control=control,
                stats=stats,
            )
        elif typ == "video":
            downloader.crawl_collection(
                profile,
                "videos",
                folder,
                selected_pages,
                control=control,
                stats=stats,
            )
        else:
            downloader.crawl_collection(
                profile,
                "photos",
                folder,
                selected_pages,
                control=control,
                stats=stats,
            )
            if control.is_cancel_requested():
                return
            if downloader.IS_PREMIUM:
                downloader.crawl_collection(
                    profile,
                    "videos",
                    folder,
                    selected_pages,
                    control=control,
                    stats=stats,
                )
            else:
                downloader.log_info(downloader.profile_premium_message())

        control.wait_until_can_start()
        downloader.log_task_summary(stats)

    def start_update(self):
        self.run_worker(self.update_downloader)

    def update_downloader(self):
        updated = downloader.run_update()
        if not updated:
            print("[UPDATE] No se aplico ninguna actualizacion.")


def main():
    root = tk.Tk()
    app = DownloaderGUI(root)
    root.mainloop()
    return app


if __name__ == "__main__":
    main()

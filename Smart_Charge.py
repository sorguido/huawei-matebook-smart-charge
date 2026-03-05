#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

ACPI_CALL_PATH = "/proc/acpi/call"
BAT_PATH = "/sys/class/power_supply/BAT1"

APP_NAME = "smart-charge"
TITLE = "Huawei Smart Charge (ACPI / EC)"

# Dove installiamo una copia "stabile" del file (consigliato per systemd)
INSTALL_DIR = f"/opt/{APP_NAME}"
INSTALL_PATH = f"{INSTALL_DIR}/Smart_Charge.py"

SERVICE_BOOT = f"{APP_NAME}.service"
SERVICE_RESUME = f"{APP_NAME}-resume.service"

SERVICE_BOOT_PATH = f"/etc/systemd/system/{SERVICE_BOOT}"
SERVICE_RESUME_PATH = f"/etc/systemd/system/{SERVICE_RESUME}"


# -------------------------
# Utility / Privilegi
# -------------------------

def is_root() -> bool:
    return os.geteuid() == 0


def which(cmd: str):
    return shutil.which(cmd)


def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 999, "", str(e)


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception as e:
        return f"<errore: {e}>"


def relaunch_with_pkexec_or_exit(extra_args: list[str] | None = None):
    """
    Rilancia immediatamente con pkexec se non siamo root, preservando DISPLAY/XAUTHORITY.
    extra_args: argomenti aggiuntivi da passare allo script (oltre a quelli già presenti).
    """
    if is_root():
        return

    pkexec = which("pkexec")
    if not pkexec:
        print("Errore: pkexec non trovato. Installa policykit oppure avvia da terminale con sudo.")
        sys.exit(1)

    # Evita loop
    if os.environ.get("ACPI_GUI_PKEXEC") == "1":
        print("Errore: rilancio pkexec già tentato, ma non sono root.")
        sys.exit(1)

    env = os.environ.copy()
    env["ACPI_GUI_PKEXEC"] = "1"

    display = env.get("DISPLAY") or ":0"
    xauth = env.get("XAUTHORITY")
    if not xauth:
        # fallback tipico: Xauthority dell'utente che ha lanciato la GUI
        home = env.get("HOME", "/home/guido")
        xauth = os.path.join(home, ".Xauthority")

    argv0 = os.path.abspath(sys.argv[0])
    args = sys.argv[1:]
    if extra_args:
        args = args + extra_args

    cmd = [
        pkexec,
        "env",
        f"DISPLAY={display}",
        f"XAUTHORITY={xauth}",
        sys.executable,
        argv0,
    ] + args

    try:
        subprocess.check_call(cmd, env=env)
        sys.exit(0)
    except subprocess.CalledProcessError:
        sys.exit(1)


# -------------------------
# Logica ACPI (apply)
# -------------------------

def ensure_acpi_call() -> int:
    rc, _, err = run_cmd(["modprobe", "acpi_call"])
    if rc != 0:
        sys.stderr.write(f"modprobe acpi_call fallito: {err}\n")
        return rc
    if not os.path.exists(ACPI_CALL_PATH):
        sys.stderr.write("Errore: /proc/acpi/call non trovato dopo modprobe.\n")
        return 2
    return 0


def acpi_write(expr: str) -> int:
    # usiamo tee perché è semplice e compatibile
    cmd = ["sh", "-lc", f"echo '{expr}' | tee {ACPI_CALL_PATH} >/dev/null"]
    rc, _, err = run_cmd(cmd)
    if rc != 0:
        sys.stderr.write(f"Scrittura ACPI fallita: {expr}\n{err}\n")
    return rc


def apply_4070() -> int:
    """
    Applica la sequenza 40/70 (nota funzionante sul tuo modello).
    """
    rc = ensure_acpi_call()
    if rc != 0:
        return rc

    seq = [
        r"\SBTT (Buffer(){0x00,0x00,0x28,0x46})",
        r"\SBCM (Buffer(){0x00,0x00,0x01,0x00,0x28,0x46})",
        r"\GBCM (Buffer(){0x00})",
    ]
    for expr in seq:
        rc = acpi_write(expr)
        if rc != 0:
            return rc
    return 0


# -------------------------
# Persistenza via systemd (auto-install)
# -------------------------

def service_boot_text() -> str:
    return f"""[Unit]
Description=Huawei Smart Charge apply thresholds 40/70 (acpi_call)
After=multi-user.target
Wants=multi-user.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {INSTALL_PATH} --apply
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""


def service_resume_text() -> str:
    return f"""[Unit]
Description=Huawei Smart Charge re-apply on resume
After=suspend.target hibernate.target hybrid-sleep.target
Wants=suspend.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {INSTALL_PATH} --apply

[Install]
WantedBy=suspend.target hibernate.target hybrid-sleep.target
"""


def write_root_file(path: str, content: str) -> int:
    """
    Scrive un file come root (noi siamo già root quando chiamiamo).
    """
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return 0
    except Exception as e:
        sys.stderr.write(f"Errore scrittura {path}: {e}\n")
        return 1


def install_persistence() -> int:
    """
    Installa persistenza:
      - crea /opt/smart-charge/Smart_Charge.py (copia di questo file)
      - crea servizi systemd (boot + resume)
      - abilita i servizi
    """
    if not is_root():
        sys.stderr.write("install_persistence richiede root\n")
        return 1

    # 1) crea dir install
    try:
        os.makedirs(INSTALL_DIR, exist_ok=True)
    except Exception as e:
        sys.stderr.write(f"Errore creazione {INSTALL_DIR}: {e}\n")
        return 1

    # 2) copia lo script in path stabile
    src = os.path.abspath(sys.argv[0])
    try:
        shutil.copy2(src, INSTALL_PATH)
        os.chmod(INSTALL_PATH, 0o755)
    except Exception as e:
        sys.stderr.write(f"Errore copia script in {INSTALL_PATH}: {e}\n")
        return 1

    # 3) crea unit files
    rc = write_root_file(SERVICE_BOOT_PATH, service_boot_text())
    if rc != 0:
        return rc
    rc = write_root_file(SERVICE_RESUME_PATH, service_resume_text())
    if rc != 0:
        return rc

    # 4) daemon-reload + enable
    for cmd in [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", SERVICE_BOOT],
        ["systemctl", "enable", SERVICE_RESUME],
    ]:
        rc, _, err = run_cmd(cmd)
        if rc != 0:
            sys.stderr.write(f"Errore: {' '.join(cmd)}\n{err}\n")
            return rc

    return 0


def uninstall_persistence() -> int:
    """
    Rimuove persistenza:
      - disable servizi
      - rimuove unit files
      - rimuove copia in /opt
    """
    if not is_root():
        sys.stderr.write("uninstall_persistence richiede root\n")
        return 1

    # disable (non fallire se non esistono)
    run_cmd(["systemctl", "disable", "--now", SERVICE_BOOT])
    run_cmd(["systemctl", "disable", "--now", SERVICE_RESUME])

    # rimuovi file unit
    for p in [SERVICE_BOOT_PATH, SERVICE_RESUME_PATH]:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            sys.stderr.write(f"Errore rimozione {p}: {e}\n")

    run_cmd(["systemctl", "daemon-reload"])

    # rimuovi /opt dir
    try:
        if os.path.exists(INSTALL_PATH):
            os.remove(INSTALL_PATH)
        if os.path.isdir(INSTALL_DIR):
            # rimuovi dir se vuoto
            try:
                os.rmdir(INSTALL_DIR)
            except OSError:
                pass
    except Exception as e:
        sys.stderr.write(f"Errore rimozione {INSTALL_DIR}: {e}\n")

    return 0


def is_persistence_installed() -> bool:
    return os.path.exists(SERVICE_BOOT_PATH) and os.path.exists(INSTALL_PATH)


def systemctl_is_enabled(unit: str) -> bool:
    rc, out, _ = run_cmd(["systemctl", "is-enabled", unit])
    return rc == 0 and out.strip() == "enabled"


def systemctl_is_active(unit: str) -> bool:
    rc, out, _ = run_cmd(["systemctl", "is-active", unit])
    return rc == 0 and out.strip() == "active"


def persistence_status_text() -> str:
    installed = is_persistence_installed()
    if not installed:
        return "Persistenza: NON installata."

    boot_en = systemctl_is_enabled(SERVICE_BOOT)
    boot_ac = systemctl_is_active(SERVICE_BOOT)
    res_en = systemctl_is_enabled(SERVICE_RESUME)

    return (
        "Persistenza: installata.\n"
        f"- {SERVICE_BOOT}: enabled={boot_en}, active={boot_ac}\n"
        f"- {SERVICE_RESUME}: enabled={res_en}\n"
        f"- Script installato: {INSTALL_PATH}\n"
    )


# -------------------------
# GUI
# -------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE)
        self.geometry("1020x740")
        self.minsize(920, 620)

        self._build_ui()
        self._intro()

    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        header = ttk.Label(
            root,
            text="Huawei MateBook – Controllo soglie batteria via ACPI",
            font=("Sans", 15, "bold"),
        )
        header.pack(anchor="w", pady=(0, 10))

        info = ttk.LabelFrame(root, text="Avviso", padding=10)
        info.pack(fill="x", pady=(0, 10))

        ttk.Label(
            info,
            text=(
                "Se vuoi disattivare lo “Smart Charge”/protezione firmware in modo certo:\n"
                "• Spegni il PC\n"
                "• Tieni premuto POWER ~15 secondi\n"
                "• Rilascia e riaccendi (reset EC)\n\n"
                "Nota: la persistenza al riavvio su Linux si ottiene riapplicando i comandi a boot/resume (systemd)."
            ),
            justify="left",
        ).pack(anchor="w")

        ctrl = ttk.Frame(root)
        ctrl.pack(fill="x", pady=(0, 10))

        ttk.Button(ctrl, text="Verifica stato", command=self.action_status).pack(side="left")
        ttk.Button(ctrl, text="Imposta 40/70", command=self.action_set_4070).pack(side="left", padx=6)

        # Persistenza
        self.persist_var = tk.BooleanVar(value=is_persistence_installed())
        persist_box = ttk.Checkbutton(
            ctrl,
            text="Rendi permanente al riavvio (systemd)",
            variable=self.persist_var,
            command=self.on_toggle_persist_checkbox,
        )
        persist_box.pack(side="left", padx=14)

        ttk.Button(ctrl, text="Installa persistenza", command=self.action_install_persist).pack(side="left", padx=6)
        ttk.Button(ctrl, text="Rimuovi persistenza", command=self.action_uninstall_persist).pack(side="left", padx=6)

        ttk.Button(ctrl, text="Esci", command=self.destroy).pack(side="right")

        outbox = ttk.LabelFrame(root, text="Log / Output", padding=8)
        outbox.pack(fill="both", expand=True)

        self.text = tk.Text(outbox, wrap="word", height=30)
        self.text.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(outbox, command=self.text.yview)
        scrollbar.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scrollbar.set)

        self.log(persistence_status_text() + "\n")

    def log(self, msg: str):
        self.text.insert("end", msg)
        self.text.see("end")
        self.update_idletasks()

    def _intro(self):
        self.log(
            "\nComandi implementati:\n"
            "• modprobe acpi_call\n"
            r"• \SBTT  (40/70)" "\n"
            r"• \SBCM  (mode=1, 40/70)" "\n"
            r"• \GBCM  (commit)" "\n\n"
        )

    def run_and_log(self, cmd: list[str]):
        self.log(f"$ {' '.join(cmd)}\n")
        rc, out, err = run_cmd(cmd)
        if out:
            self.log(out + ("" if out.endswith("\n") else "\n"))
        if err:
            self.log("STDERR:\n" + err + ("" if err.endswith("\n") else "\n"))
        if rc != 0:
            self.log(f"❌ exit={rc}\n")
        return rc, out, err

    def ensure_acpi_call_gui(self) -> bool:
        rc, _, _ = self.run_and_log(["modprobe", "acpi_call"])
        if rc != 0:
            messagebox.showerror("Errore", "modprobe acpi_call fallito. Serve acpi_call-dkms.")
            return False
        if not os.path.exists(ACPI_CALL_PATH):
            messagebox.showerror("Errore", "/proc/acpi/call non trovato.")
            return False
        self.log("✅ acpi_call disponibile\n\n")
        return True

    def acpi_write_gui(self, expr: str) -> bool:
        rc, _, _ = self.run_and_log(["sh", "-lc", f"echo '{expr}' | tee {ACPI_CALL_PATH} >/dev/null"])
        if rc != 0:
            return False
        return True

    def snapshot(self, title: str):
        self.log(f"\n=== {title} ===\n")
        for label, expr in [
            ("CHAM", r"\_SB.PC00.LPCB.EC0.CHAM"),
            ("BCCL", r"\_SB.PC00.LPCB.EC0.BCCL"),
            ("BCCH", r"\_SB.PC00.LPCB.EC0.BCCH"),
            ("BADS", r"\_SB.PC00.LPCB.EC0.BADS"),
            ("SPMD", r"\_SB.PC00.LPCB.EC0.SPMD"),
        ]:
            if self.acpi_write_gui(expr):
                self.log(f"{label}={read_file(ACPI_CALL_PATH)}\n")
        self.log(f"STATUS={read_file(os.path.join(BAT_PATH,'status'))}\n")
        self.log(f"CAPACITY={read_file(os.path.join(BAT_PATH,'capacity'))}\n")
        self.log(f"CURRENT_NOW={read_file(os.path.join(BAT_PATH,'current_now'))}\n")
        self.log("\n")

    def action_status(self):
        self.log("\n=== VERIFICA STATO ===\n")
        if not self.ensure_acpi_call_gui():
            return
        self.snapshot("SNAPSHOT CORRENTE")
        self.log(persistence_status_text() + "\n")

    def action_set_4070(self):
        self.log("\n=== IMPOSTA 40/70 ===\n")
        if not self.ensure_acpi_call_gui():
            return
        self.snapshot("PRIMA")

        if not self.acpi_write_gui(r"\SBTT (Buffer(){0x00,0x00,0x28,0x46})"):
            return
        if not self.acpi_write_gui(r"\SBCM (Buffer(){0x00,0x00,0x01,0x00,0x28,0x46})"):
            return
        if not self.acpi_write_gui(r"\GBCM (Buffer(){0x00})"):
            return

        self.snapshot("DOPO 40/70")
        messagebox.showinfo("OK", "Sequenza 40/70 applicata.")

        # Se l'utente ha spuntato persistenza ma non è installata, suggerisci installazione
        if self.persist_var.get() and not is_persistence_installed():
            if messagebox.askyesno(
                "Persistenza",
                "Hai spuntato la persistenza ma non è ancora installata.\nVuoi installarla ora?"
            ):
                self.action_install_persist()

    def on_toggle_persist_checkbox(self):
        # Non installiamo/rimuoviamo automaticamente solo con la spunta: serve conferma (e root).
        self.log(f"(checkbox) Persistenza richiesta={self.persist_var.get()}\n")

    def action_install_persist(self):
        # Serve root: se GUI non è root, rilancia con pkexec aggiungendo --install
        if not is_root():
            relaunch_with_pkexec_or_exit(extra_args=["--install"])
            return

        self.log("\n=== INSTALLAZIONE PERSISTENZA (systemd) ===\n")
        rc = install_persistence()
        if rc == 0:
            self.persist_var.set(True)
            self.log("✅ Persistenza installata.\n")
            self.log(persistence_status_text() + "\n")
            messagebox.showinfo("OK", "Persistenza installata (boot + resume).")
        else:
            self.log(f"❌ Installazione fallita (exit={rc}).\n")
            messagebox.showerror("Errore", "Installazione persistenza fallita. Vedi log.")

    def action_uninstall_persist(self):
        if not is_root():
            relaunch_with_pkexec_or_exit(extra_args=["--uninstall"])
            return

        if not messagebox.askyesno("Conferma", "Vuoi rimuovere la persistenza (systemd)?"):
            return

        self.log("\n=== RIMOZIONE PERSISTENZA (systemd) ===\n")
        rc = uninstall_persistence()
        if rc == 0:
            self.persist_var.set(False)
            self.log("✅ Persistenza rimossa.\n")
            self.log(persistence_status_text() + "\n")
            messagebox.showinfo("OK", "Persistenza rimossa.")
        else:
            self.log(f"❌ Rimozione fallita (exit={rc}).\n")
            messagebox.showerror("Errore", "Rimozione persistenza fallita. Vedi log.")


# -------------------------
# Entry point
# -------------------------

def main():
    # Modalità CLI (per systemd o installazione)
    if "--apply" in sys.argv:
        if not is_root():
            print("Errore: --apply richiede root")
            sys.exit(1)
        sys.exit(apply_4070())

    if "--install" in sys.argv:
        if not is_root():
            print("Errore: --install richiede root")
            sys.exit(1)
        rc = install_persistence()
        print("OK" if rc == 0 else f"FAIL exit={rc}")
        sys.exit(rc)

    if "--uninstall" in sys.argv:
        if not is_root():
            print("Errore: --uninstall richiede root")
            sys.exit(1)
        rc = uninstall_persistence()
        print("OK" if rc == 0 else f"FAIL exit={rc}")
        sys.exit(rc)

    if "--status" in sys.argv:
        print(persistence_status_text())
        sys.exit(0)

    # GUI: chiedi password subito (pkexec) per evitare problemi DISPLAY con sudo
    relaunch_with_pkexec_or_exit()

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

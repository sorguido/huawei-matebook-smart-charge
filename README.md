# Reverse Engineering & Hardware Optimization: Huawei Smart Charge on Linux

This repository documents and automates how to set a **battery charge upper limit** (e.g. stop charging at **70%**) on a **Huawei MateBook D 15 (BohrD-WDH9D, 2021)** under Linux, by invoking vendor ACPI methods through `acpi_call`.

> **Key findings**
> - The Embedded Controller (EC) firmware effectively enforces **only the upper threshold** in real behavior.
> - **Persistence:** settings persist across **reboot**, but reset after a **full shutdown** (EC runtime state clears).

---

## What this project does

- Provides a Python GUI tool (`Smart_Charge.py`) to apply and verify the charge limit.
- Documents the ACPI reverse engineering steps and the firmware behavior (reboot vs shutdown).
- Includes diagrams and a technical report.

---

## Target machine

- Laptop: **Huawei MateBook D 15**
- Model: **BohrD-WDH9D**
- Year: **2021**
- Linux: Mint (tested on kernel **6.x**, incl. 6.17)
- Battery device: `BAT1`
- Embedded Controller ACPI path: `\_SB.PC00.LPCB.EC0`

---

## Requirements

### Packages / modules
- `acpi_call-dkms` (kernel module)
- Python 3 + Tkinter (for GUI)

On Debian/Ubuntu/Mint:
```bash
sudo apt update
sudo apt install acpi-call-dkms python3-tk

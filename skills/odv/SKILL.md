---
name: odv
description: >-
  Shortcut command (/odv or /odv-mcp) to operate the Ocean Data View (ODV 5) Model
  Context Protocol (MCP) Server and AI Automation Engine (https://github.com/dzakyzahy/odv-mcp).
  Import and convert raw oceanographic data (World Ocean Database WOD ASCII, .gz,
  Sea-Bird CTD .cnv, CSV/Excel .xlsx, NetCDF .nc) into ready-to-open ODV Collections
  (.odv) and ODV Generic Spreadsheets (.txt), author and live-edit ODV View XML files
  (.xview) with DIVA / Weighted-Average / Quick gridding, render 300 DPI publication
  graphics (Section, Iso-Surface Map, T-S Diagram with Isopycnals, Vertical Profiles,
  4-Panel TEOS-10 Dashboards) via odv.exe, and launch interactive ODV GUI sessions.
---

# Ocean Data View (ODV 5) MCP & AI Automation Skill (`/odv`)

Skill pintasan cepat (`/odv`) untuk mengoperasikan **`odv-mcp`** (**Ocean Data View 5 Model Context Protocol Server & AI Automation Engine**).

- **Repositori GitHub:** `https://github.com/dzakyzahy/odv-mcp`
- **Server MCP Entrypoint:** `python -m odv_mcp serve`

---

## 🚀 Perilaku Default Saat Dipanggil (`/odv`)

1. **`/odv` (tanpa argumen)**: Jalankan `python -m odv_mcp status` untuk memeriksa `odv.exe`, `diva.exe`, batimetri `GEBCO`, dan palet warna, serta deteksi file `.odv` / data oseanografi di direktori kerja.
2. **`/odv import <file>`**: Jalankan tool `odv_import_to_collection` untuk menghasilkan koleksi `.odv` siap buka dan spreadsheet `.txt` V4.8.
3. **`/odv inspect <file.odv|.txt>`**: Jalankan tool `odv_inspect_collection` untuk membaca variabel, jumlah stasiun, *bounding box*, dan daftar `.xview`.
4. **`/odv section <var> [diva|weighted_average|quick|original]`**: Jalankan `odv_create_plot` (`plot_type="section"`) dengan interpolasi pilihan dan render PNG 300 DPI via `odv.exe`.
5. **`/odv surface <var> [depth_m] [diva|weighted_average]`**: Jalankan `odv_create_plot` (`plot_type="surface"`) pada kedalaman `iso_depth` dan render PNG 300 DPI via `odv.exe`.
6. **`/odv ts`**: Jalankan `odv_create_plot` (`plot_type="ts_diagram"`, `draw_isopycnals=True`) dan render diagram T-S 300 DPI.
7. **`/odv edit <file.xview> ...`**: Jalankan `odv_edit_view` untuk memodifikasi interpolasi, variabel, palet warna, kontur, atau batas sumbu pada `.xview` dan render ulang.
8. **`/odv dashboard <file.odv|.txt>`**: Jalankan `odv_render_dashboard` untuk menghasilkan dashboard sintesis 4-panel (Peta + T-S TEOS-10 + Section + Iso-Surface).
9. **`/odv open <file.odv> [view.xview]`**: Jalankan `odv_open_gui` untuk membuka aplikasi desktop ODV langsung di layar user.

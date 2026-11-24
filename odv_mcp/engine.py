"""
Ocean Data View (ODV 5) Desktop & Batch Execution Engine.

Automates:
- Discovering `odv.exe` / `odv` and `diva.exe` across Windows, macOS, and Linux
- Generating and executing ODV Command Files (`.cmd`) in headless batch mode (`odv -x file.cmd -q`)
- Lock-safe snapshot execution: if a target `.odv` collection is currently open in the user's ODV GUI,
  automatically runs headless batch exports/graphics against an isolated lightweight snapshot while
  saving `.xview` view files directly into the user's live `<collection>.Data/views/` folder!
- Launching interactive ODV GUI sessions (`odv.exe collection.odv -view view.xview`) so the user can
  immediately explore and edit the AI-generated view.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from odv_mcp.converters import (
    convert_tabular_or_cnv_to_odv_txt,
    detect_import_format,
    ensure_decompressed,
    inspect_odv_collection,
    scan_odv_spreadsheet_metadata,
)
from odv_mcp.xview import POPULAR_PALETTES, build_xview_xml, edit_xview_file, parse_xview_summary


def find_odv_executable() -> Optional[str]:
    """
    Locate the Ocean Data View executable (`odv.exe` on Windows, `odv` on Linux/macOS).
    Checks `ODV_EXECUTABLE` / `ODV_PATH` environment variables first, then standard install paths.
    """
    for env_var in ("ODV_EXECUTABLE", "ODV_PATH"):
        val = os.environ.get(env_var)
        if val and os.path.isfile(val):
            return os.path.abspath(val)

    candidates = [
        r"C:\Program Files\Ocean Data View\bin_w64\odv.exe",
        r"C:\Program Files\Ocean Data View\bin\odv.exe",
        r"C:\Program Files (x86)\Ocean Data View\bin\odv.exe",
        "/Applications/Ocean Data View.app/Contents/MacOS/odv",
        "/usr/local/bin/odv",
        "/usr/bin/odv",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)

    # Search Program Files wildcard
    for root in (r"C:\Program Files", r"C:\Program Files (x86)", os.path.expanduser("~")):
        if os.path.isdir(root):
            matches = glob.glob(os.path.join(root, "*Ocean Data View*", "**", "odv.exe"), recursive=True)
            if matches:
                return os.path.abspath(matches[0])

    which_odv = shutil.which("odv") or shutil.which("odv.exe")
    return os.path.abspath(which_odv) if which_odv else None


def get_odv_environment_info() -> Dict[str, Any]:
    """Return paths and capabilities of the local ODV 5 installation."""
    exe = find_odv_executable()
    install_dir = os.path.dirname(os.path.dirname(exe)) if exe else None
    diva_exe = os.path.join(os.path.dirname(exe), "diva", "diva.exe") if exe else None
    palettes_dir = os.path.join(install_dir, "include", "palettes") if install_dir else None
    bathy_nc = os.path.join(install_dir, "include", "GEBCO_2023_6x6min_Global.nc") if install_dir else None

    palettes = list(POPULAR_PALETTES)
    if palettes_dir and os.path.isdir(palettes_dir):
        found = [
            os.path.splitext(os.path.basename(p))[0]
            for p in sorted(glob.glob(os.path.join(palettes_dir, "*.pal")))
        ]
        if found:
            palettes = found

    return {
        "odv_installed": exe is not None,
        "odv_executable": exe,
        "install_dir": install_dir,
        "diva_installed": bool(diva_exe and os.path.isfile(diva_exe)),
        "diva_executable": diva_exe if (diva_exe and os.path.isfile(diva_exe)) else None,
        "global_bathymetry": bathy_nc if (bathy_nc and os.path.isfile(bathy_nc)) else None,
        "available_palettes": palettes,
        "platform": platform.system(),
    }


def _odv_path(p: str) -> str:
    """Convert Windows backslashes to forward slashes required by ODV .cmd files."""
    return os.path.abspath(p).replace("\\", "/")


def is_odv_gui_running() -> bool:
    """Check whether an interactive `odv.exe` process is currently running."""
    if platform.system() != "Windows":
        return False
    try:
        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq odv.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "odv.exe" in res.stdout.lower()
    except Exception:
        return False


def create_temp_collection_clone(odv_path: str) -> Tuple[str, str]:
    """
    Create a fast temporary clone of `<name>.odv` and `<name>.Data/` so headless batch
    commands (`odv.exe -x ... -q`) never collide with a collection that is currently locked/open
    in an interactive ODV GUI session.
    """
    abs_odv = os.path.abspath(odv_path)
    base_no_ext = os.path.splitext(abs_odv)[0]
    src_data = base_no_ext + ".Data"
    coll_name = os.path.basename(base_no_ext)

    tmp_dir = tempfile.mkdtemp(prefix="odv_mcp_batch_")
    dst_odv = os.path.join(tmp_dir, f"{coll_name}.odv")
    dst_data = os.path.join(tmp_dir, f"{coll_name}.Data")

    shutil.copy2(abs_odv, dst_odv)
    if os.path.isdir(src_data):
        shutil.copytree(src_data, dst_data)
    return tmp_dir, dst_odv


def run_odv_command_script(
    cmd_lines: List[str],
    cmd_file_path: Optional[str] = None,
    headless_quit: bool = True,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Write and execute an ODV `.cmd` command file using `odv.exe -x <cmd_file> [-q]`.
    """
    exe = find_odv_executable()
    if not exe:
        raise RuntimeError(
            "Ocean Data View executable (odv.exe) not found. Set ODV_EXECUTABLE environment variable."
        )

    lines = [line.strip() for line in cmd_lines if line.strip()]
    if headless_quit and (not lines or lines[-1].lower() != "quit"):
        lines.append("quit")

    if cmd_file_path is None:
        fd, cmd_file_path = tempfile.mkstemp(prefix="odv_mcp_", suffix=".cmd")
        os.close(fd)
    else:
        cmd_file_path = os.path.abspath(cmd_file_path)
        os.makedirs(os.path.dirname(cmd_file_path), exist_ok=True)

    with open(cmd_file_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    args = [exe, "-x", cmd_file_path]
    if headless_quit:
        args.append("-q")

    t0 = time.time()
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    elapsed = round(time.time() - t0, 2)

    return {
        "command_file": cmd_file_path,
        "commands": lines,
        "return_code": proc.returncode,
        "elapsed_seconds": elapsed,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def import_raw_file_to_odv_collection(
    input_file: str,
    output_collection_path: Optional[str] = None,
    export_spreadsheet: bool = True,
    compact_spreadsheet: bool = True,
    timeout: int = 180,
) -> Dict[str, Any]:
    """
    Take ANY raw oceanographic data file:
    - World Ocean Database native ASCII (`CTDO1012` or `CTDO1012.gz`)
    - ODV Generic Spreadsheet (`.txt`)
    - CSV / TSV / Excel (`.xlsx`)
    - Sea-Bird CTD (`.cnv`)
    - Argo / NetCDF (`.nc`)
    and produce a ready-to-open native ODV Collection (`.odv` + `.Data/`) AND an ODV Generic
    Spreadsheet (`.txt`).
    """
    decompressed = ensure_decompressed(input_file)
    fmt = detect_import_format(decompressed)
    in_dir = os.path.dirname(decompressed)
    base_name = os.path.splitext(os.path.basename(decompressed))[0]

    if fmt == "ODV_COLLECTION":
        target_odv = decompressed
    else:
        if output_collection_path:
            target_odv = os.path.abspath(output_collection_path)
            if not target_odv.lower().endswith(".odv"):
                target_odv += ".odv"
        else:
            target_odv = os.path.join(in_dir, f"{base_name}_ODV.odv")

    spreadsheet_path = os.path.splitext(target_odv)[0] + "_Spreadsheet.txt"

    if fmt == "TABULAR_CSV_EXCEL" or fmt == "SEABIRD_CNV":
        spreadsheet_path = convert_tabular_or_cnv_to_odv_txt(decompressed, spreadsheet_path)
        cmd_lines = [
            f"open_data_file {_odv_path(spreadsheet_path)}",
            "quit",
        ]
        # open_data_file creates <spreadsheet_base>.odv in the same directory
        target_odv = os.path.splitext(spreadsheet_path)[0] + ".odv"
        run_odv_command_script(cmd_lines, headless_quit=True, timeout=timeout)
    elif fmt == "SPREADSHEET":
        spreadsheet_path = decompressed
        target_odv = os.path.splitext(spreadsheet_path)[0] + ".odv"
        cmd_lines = [
            f"open_data_file {_odv_path(spreadsheet_path)}",
            "quit",
        ]
        run_odv_command_script(cmd_lines, headless_quit=True, timeout=timeout)
    elif fmt == "WORLDOCEANDATABASE":
        cmd_lines = [
            f"create_collection {_odv_path(target_odv)}, WORLDOCEANDATABASE",
            f"import_data {_odv_path(decompressed)}, WORLDOCEANDATABASE",
        ]
        if export_spreadsheet:
            if compact_spreadsheet:
                # Export core oceanographic variables [1:10] in compact format without redundant QF columns
                cmd_lines.append(
                    f"export_data {_odv_path(spreadsheet_path)}, [1:10], [], [], 0, 0, 0, 0, 1, 0"
                )
            else:
                cmd_lines.append(f"export_data {_odv_path(spreadsheet_path)}")
        cmd_lines.append("quit")
        run_odv_command_script(cmd_lines, headless_quit=True, timeout=timeout)
    elif fmt == "NETCDF":
        cmd_lines = [
            f"create_collection {_odv_path(target_odv)}, ARGOPROFILES",
            f"import_data {_odv_path(decompressed)}, ARGOPROFILES",
        ]
        if export_spreadsheet:
            cmd_lines.append(
                f"export_data {_odv_path(spreadsheet_path)}, [:], [], [], 0, 0, 0, 0, 1, 0"
            )
        cmd_lines.append("quit")
        run_odv_command_script(cmd_lines, headless_quit=True, timeout=timeout)

    info = inspect_odv_collection(target_odv) if os.path.exists(target_odv) else {}
    meta_scan = (
        scan_odv_spreadsheet_metadata(spreadsheet_path)
        if os.path.exists(spreadsheet_path)
        else {}
    )

    return {
        "input_file": os.path.abspath(input_file),
        "decompressed_file": decompressed,
        "detected_format": fmt,
        "collection_path": target_odv,
        "spreadsheet_path": spreadsheet_path if os.path.exists(spreadsheet_path) else None,
        "collection_info": info,
        "dataset_summary": meta_scan,
    }


def ensure_collection_spreadsheet(
    odv_path: str,
    spreadsheet_path: Optional[str] = None,
    var_range: str = "1:10",
    timeout: int = 90,
) -> str:
    """
    Ensure a compact ODV Generic Spreadsheet (.txt) exists for an `.odv` collection so AI tools
    can inspect exact coordinate domains and variable ranges.
    Uses a temporary collection clone if ODV GUI is currently running to avoid lock conflicts.
    """
    abs_odv = os.path.abspath(odv_path)
    base_no_ext = os.path.splitext(abs_odv)[0]
    if spreadsheet_path is None:
        # Check if a spreadsheet already exists in the folder
        existing_txts = glob.glob(os.path.join(os.path.dirname(abs_odv), "*Spreadsheet*.txt"))
        if existing_txts and os.path.getsize(existing_txts[0]) > 1000:
            return os.path.abspath(existing_txts[0])
        spreadsheet_path = f"{base_no_ext}_Compact.txt"

    abs_txt = os.path.abspath(spreadsheet_path)
    if os.path.exists(abs_txt) and os.path.getsize(abs_txt) > 1000:
        return abs_txt

    tmp_dir = None
    target_odv = abs_odv
    try:
        if is_odv_gui_running():
            tmp_dir, target_odv = create_temp_collection_clone(abs_odv)
        cmd_lines = [
            f"open_collection {_odv_path(target_odv)}",
            f"export_data {_odv_path(abs_txt)}, [{var_range}], [], [], 0, 0, 0, 0, 1, 0",
            "quit",
        ]
        run_odv_command_script(cmd_lines, headless_quit=True, timeout=timeout)
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return abs_txt


def create_and_render_odv_view(
    collection_or_file: str,
    view_name: str = "AI_Generated_View",
    plot_type: str = "section",
    x_var: Optional[Union[str, int]] = None,
    y_var: Optional[Union[str, int]] = None,
    z_var: Optional[Union[str, int]] = None,
    interpolation: str = "diva",
    x_scale_length: float = 25.0,
    y_scale_length: float = 25.0,
    auto_scale_lengths: bool = True,
    diva_snr: float = 2.0,
    isopycnic_gridding: bool = False,
    palette: Optional[str] = None,
    contours: Union[bool, List[float]] = True,
    draw_isopycnals: bool = False,
    map_domain: Optional[Dict[str, float]] = None,
    section_config: Optional[Dict[str, Any]] = None,
    iso_surfaces: Optional[List[Dict[str, Any]]] = None,
    x_range: Optional[Tuple[float, float]] = None,
    y_range: Optional[Tuple[float, float]] = None,
    z_range: Optional[Tuple[float, float]] = None,
    title: Optional[str] = None,
    extra_windows: Optional[List[Dict[str, Any]]] = None,
    picked_stations: Optional[List[int]] = None,
    output_image_path: Optional[str] = None,
    dpi: int = 300,
    open_in_gui: bool = False,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    End-to-end AI workflow for ODV:
    1. Resolves or imports `collection_or_file` into an `.odv` collection.
    2. Scans station coordinates and variable ranges to auto-configure `<MapDomain>`, `<Section>`, and `<ColorScale>`.
    3. Builds a native `.xview` XML file inside `<collection>.Data/views/<view_name>.xview`.
    4. Runs `odv.exe` in headless batch mode (using an isolated clone if ODV GUI is already open)
       with `set_axis_full_ranges` (if ranges are auto), `save_view`, and `export_graphics` to render
       a high-resolution image (`.png` / `.gif` / `.jpg` / `.eps` / `.tif`).
    5. Optionally launches `odv.exe` in interactive GUI mode with the new `.xview` loaded!
    """
    abs_in = os.path.abspath(collection_or_file)
    if not abs_in.lower().endswith(".odv"):
        imp = import_raw_file_to_odv_collection(abs_in)
        odv_path = imp["collection_path"]
    else:
        odv_path = abs_in

    coll_info = inspect_odv_collection(odv_path)
    variables = coll_info["data_variables"]
    coll_name = coll_info["collection_name"]
    views_dir = coll_info["views_dir"]
    os.makedirs(views_dir, exist_ok=True)

    # Auto-detect map_domain and section nodes from spreadsheet metadata if not explicitly provided
    meta_scan: Dict[str, Any] = {}
    if map_domain is None or (resolve_plot_type_is_section(plot_type) and section_config is None):
        try:
            txt_path = ensure_collection_spreadsheet(odv_path)
            if os.path.exists(txt_path):
                meta_scan = scan_odv_spreadsheet_metadata(txt_path)
        except Exception:
            meta_scan = {}

    eff_domain = map_domain or meta_scan.get("map_domain")
    eff_section = section_config
    if eff_section is None and resolve_plot_type_is_section(plot_type):
        eff_section = meta_scan.get("suggested_section")

    clean_view_name = view_name[:-6] if view_name.lower().endswith(".xview") else view_name
    xview_path = os.path.join(views_dir, f"{clean_view_name}.xview")

    xml_str = build_xview_xml(
        collection_name=coll_name,
        variables=variables,
        plot_type=plot_type,
        x_var=x_var,
        y_var=y_var,
        z_var=z_var,
        interpolation=interpolation,
        x_scale_length=x_scale_length,
        y_scale_length=y_scale_length,
        auto_scale_lengths=auto_scale_lengths,
        diva_snr=diva_snr,
        isopycnic_gridding=isopycnic_gridding,
        palette=palette,
        contours=contours,
        draw_isopycnals=draw_isopycnals,
        map_domain=eff_domain,
        section_config=eff_section,
        iso_surfaces=iso_surfaces,
        x_range=x_range,
        y_range=y_range,
        z_range=z_range,
        title=title,
        extra_windows=extra_windows,
        picked_stations=picked_stations,
    )

    with open(xview_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(xml_str)

    if output_image_path is None:
        output_image_path = os.path.join(
            os.path.dirname(odv_path),
            f"{coll_name}_{clean_view_name}.png",
        )
    else:
        output_image_path = os.path.abspath(output_image_path)
        os.makedirs(os.path.dirname(output_image_path), exist_ok=True)

    # Execute ODV batch rendering (using lock-safe clone if ODV GUI is currently running)
    tmp_dir = None
    batch_odv = odv_path
    batch_xview = xview_path
    batch_result: Dict[str, Any] = {}
    try:
        if is_odv_gui_running():
            tmp_dir, batch_odv = create_temp_collection_clone(odv_path)
            batch_xview = os.path.join(
                os.path.splitext(batch_odv)[0] + ".Data",
                "views",
                f"{clean_view_name}.xview",
            )
            os.makedirs(os.path.dirname(batch_xview), exist_ok=True)
            shutil.copy2(xview_path, batch_xview)

        cmd_lines = [
            f"open_collection {_odv_path(batch_odv)}",
            f"load_view {_odv_path(batch_xview)}",
        ]
        if x_range is None and y_range is None and z_range is None:
            cmd_lines.append("set_axis_full_ranges -1")
        if map_domain is None:
            cmd_lines.append("set_axis_full_ranges 0")
        if x_range is not None:
            cmd_lines.append(f"set_axis_range 1, x, {x_range[0]}, {x_range[1]}")
        if y_range is not None:
            cmd_lines.append(f"set_axis_range 1, y, {y_range[0]}, {y_range[1]}")
        if z_range is not None:
            cmd_lines.append(f"set_axis_range 1, z, {z_range[0]}, {z_range[1]}")

        cmd_lines.extend([
            f"save_view {_odv_path(batch_xview)}",
            f"export_graphics -1, {_odv_path(output_image_path)}, {int(dpi)}",
            "quit",
        ])

        batch_result = run_odv_command_script(cmd_lines, headless_quit=True, timeout=timeout)
        # Copy the ODV-normalized .xview back to the live collection's views folder
        if os.path.exists(batch_xview) and batch_xview != xview_path:
            shutil.copy2(batch_xview, xview_path)
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    gui_pid = None
    if open_in_gui:
        gui_info = launch_odv_gui(odv_path, view_path=xview_path)
        gui_pid = gui_info.get("pid")

    return {
        "collection_path": odv_path,
        "xview_path": xview_path,
        "output_image_path": output_image_path if os.path.exists(output_image_path) else None,
        "image_size_bytes": os.path.getsize(output_image_path) if os.path.exists(output_image_path) else 0,
        "view_summary": parse_xview_summary(xview_path),
        "batch_execution": batch_result,
        "opened_in_gui": open_in_gui,
        "gui_pid": gui_pid,
    }


def edit_and_render_odv_view(
    collection_path: str,
    xview_path: str,
    output_xview_path: Optional[str] = None,
    window_index: int = 1,
    interpolation: Optional[str] = None,
    x_scale_length: Optional[float] = None,
    y_scale_length: Optional[float] = None,
    auto_scale_lengths: Optional[bool] = None,
    diva_snr: Optional[float] = None,
    isopycnic_gridding: Optional[bool] = None,
    palette: Optional[str] = None,
    z_range: Optional[Tuple[float, float]] = None,
    x_var: Optional[Union[str, int]] = None,
    y_var: Optional[Union[str, int]] = None,
    z_var: Optional[Union[str, int]] = None,
    contours: Optional[Union[bool, List[float]]] = None,
    draw_isopycnals: Optional[bool] = None,
    map_domain: Optional[Dict[str, float]] = None,
    section_nodes: Optional[Dict[str, Any]] = None,
    output_image_path: Optional[str] = None,
    dpi: int = 300,
    open_in_gui: bool = False,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Modify an existing `.xview` file and immediately re-render its graphics via ODV 5.
    """
    from odv_mcp.xview import resolve_variable_id

    coll_info = inspect_odv_collection(collection_path)
    variables = coll_info["data_variables"]

    x_id = resolve_variable_id(x_var, variables)[0] if x_var is not None else None
    y_id = resolve_variable_id(y_var, variables)[0] if y_var is not None else None
    z_id = resolve_variable_id(z_var, variables)[0] if z_var is not None else None

    updated_xview = edit_xview_file(
        source_xview_path=xview_path,
        output_xview_path=output_xview_path,
        window_index=window_index,
        interpolation=interpolation,
        x_scale_length=x_scale_length,
        y_scale_length=y_scale_length,
        auto_scale_lengths=auto_scale_lengths,
        diva_snr=diva_snr,
        isopycnic_gridding=isopycnic_gridding,
        palette=palette,
        z_range=z_range,
        x_var_id=x_id,
        y_var_id=y_id,
        z_var_id=z_id,
        contours=contours,
        draw_isopycnals=draw_isopycnals,
        map_domain=map_domain,
        section_nodes=section_nodes,
    )

    view_stem = os.path.splitext(os.path.basename(updated_xview))[0]
    if output_image_path is None:
        output_image_path = os.path.join(
            os.path.dirname(os.path.abspath(collection_path)),
            f"{coll_info['collection_name']}_{view_stem}.png",
        )
    else:
        output_image_path = os.path.abspath(output_image_path)

    tmp_dir = None
    batch_odv = os.path.abspath(collection_path)
    batch_xview = updated_xview
    try:
        if is_odv_gui_running():
            tmp_dir, batch_odv = create_temp_collection_clone(collection_path)
            batch_xview = os.path.join(
                os.path.splitext(batch_odv)[0] + ".Data",
                "views",
                os.path.basename(updated_xview),
            )
            os.makedirs(os.path.dirname(batch_xview), exist_ok=True)
            shutil.copy2(updated_xview, batch_xview)

        cmd_lines = [
            f"open_collection {_odv_path(batch_odv)}",
            f"load_view {_odv_path(batch_xview)}",
        ]
        if z_range is not None:
            cmd_lines.append(f"set_axis_range {window_index}, z, {z_range[0]}, {z_range[1]}")
        cmd_lines.extend([
            f"save_view {_odv_path(batch_xview)}",
            f"export_graphics -1, {_odv_path(output_image_path)}, {int(dpi)}",
            "quit",
        ])
        batch_res = run_odv_command_script(cmd_lines, headless_quit=True, timeout=timeout)
        if os.path.exists(batch_xview) and batch_xview != updated_xview:
            shutil.copy2(batch_xview, updated_xview)
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    gui_pid = None
    if open_in_gui:
        gui_pid = launch_odv_gui(collection_path, view_path=updated_xview).get("pid")

    return {
        "collection_path": os.path.abspath(collection_path),
        "updated_xview_path": updated_xview,
        "output_image_path": output_image_path if os.path.exists(output_image_path) else None,
        "view_summary": parse_xview_summary(updated_xview),
        "batch_execution": batch_res,
        "opened_in_gui": open_in_gui,
        "gui_pid": gui_pid,
    }


def launch_odv_gui(
    collection_path: str,
    view_path: Optional[str] = None,
    read_only: bool = False,
) -> Dict[str, Any]:
    """
    Launch the interactive Ocean Data View desktop application (`odv.exe`) with the given
    collection and optional `.xview` or template view loaded.
    """
    exe = find_odv_executable()
    if not exe:
        raise RuntimeError("Ocean Data View executable (odv.exe) not found.")

    abs_coll = os.path.abspath(collection_path)
    args = [exe, abs_coll]
    if read_only:
        args.extend(["-access", "ReadOnly"])
    if view_path:
        args.extend(["-view", os.path.abspath(view_path) if os.path.exists(view_path) else view_path])

    proc = subprocess.Popen(args, close_fds=True)
    return {
        "pid": proc.pid,
        "executable": exe,
        "collection_path": abs_coll,
        "view_path": view_path,
        "command": args,
    }


def resolve_plot_type_is_section(plot_type: str) -> bool:
    return str(plot_type).strip().lower() in ("section", "transect", "2")

"""
Model Context Protocol (MCP) JSON-RPC 2.0 Server & CLI for Ocean Data View (ODV 5).

Implements a zero-dependency MCP stdio server compatible with protocol versions
`2024-11-05` and `2025-03-26` (works out-of-the-box in Claude Desktop, Antigravity,
Cursor, Windsurf, Cline, and any MCP client), plus a full CLI interface.

Exposed MCP Tools:
1. `odv_environment_status`   - Check local ODV 5, DIVA gridding, GEBCO bathymetry, and color palettes.
2. `odv_import_to_collection` - Convert/import raw oceanographic files (WOD ASCII, .gz, .cnv, .csv, .xlsx, .nc, .txt)
                                into ready-to-open `.odv` collections and ODV Generic Spreadsheets (`.txt`).
3. `odv_inspect_collection`   - Inspect an `.odv` collection or `.txt` spreadsheet (variables, station count,
                                coordinates, depth domain, and existing `.xview` views).
4. `odv_create_plot`          - Create a native ODV `.xview` view (station/profile, scatter/T-S diagram, vertical
                                section with bathymetry, or horizontal iso-surface map) with chosen interpolation
                                (`diva`, `weighted_average`, `quick`, `original`), render high-res graphics via
                                `odv.exe`, and optionally open interactively in ODV GUI.
5. `odv_edit_view`            - Live-edit an existing `.xview` file (change interpolation, gridding scale lengths,
                                variables, color palette, contours, isopycnals, section transect, or map domain)
                                and re-render via `odv.exe`.
6. `odv_render_dashboard`     - Render a 4-panel publication hydrographic dashboard (Map + TEOS-10 T-S Diagram +
                                Interpolated Section + Iso-Surface Map) with DIVA/RBF or Barnes gridding.
7. `odv_run_command_file`     - Run custom ODV batch `.cmd` command scripts directly on `odv.exe`.
8. `odv_open_gui`             - Open any `.odv` collection and `.xview` view interactively in the ODV desktop GUI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

from odv_mcp import __version__
from odv_mcp.converters import (
    convert_tabular_or_cnv_to_odv_txt,
    ensure_decompressed,
    inspect_odv_collection,
    scan_odv_spreadsheet_metadata,
)
from odv_mcp.engine import (
    create_and_render_odv_view,
    edit_and_render_odv_view,
    ensure_collection_spreadsheet,
    get_odv_environment_info,
    import_raw_file_to_odv_collection,
    launch_odv_gui,
    run_odv_command_script,
)
from odv_mcp.plotting import render_scientific_dashboard
from odv_mcp.xview import parse_xview_summary


MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "odv_environment_status",
        "description": (
            "Check the local Ocean Data View (ODV 5) installation status, executable path, "
            "DIVA optimal interpolation binary (diva.exe), GEBCO global bathymetry, and available color palettes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "odv_import_to_collection",
        "description": (
            "Convert and import any raw oceanographic data file (World Ocean Database native ASCII such as CTDO1012, "
            "GZIP archives .gz, Sea-Bird CTD .cnv, CSV/TSV/Excel .xlsx, NetCDF .nc, or ODV Generic Spreadsheet .txt) "
            "into a ready-to-open ODV Collection (.odv + .Data/) and an ODV Generic Spreadsheet (.txt)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_file": {
                    "type": "string",
                    "description": "Absolute path to the raw oceanographic file (e.g., CTDO1012.gz, CTDO1012, data.csv, cast.cnv, profiles.nc).",
                },
                "output_collection_path": {
                    "type": "string",
                    "description": "Optional target path for the created .odv collection file.",
                },
                "export_spreadsheet": {
                    "type": "boolean",
                    "description": "Also export a clean ODV Generic Spreadsheet (.txt) for direct inspection. Default true.",
                },
            },
            "required": ["input_file"],
        },
    },
    {
        "name": "odv_inspect_collection",
        "description": (
            "Inspect an ODV collection (.odv) or ODV spreadsheet (.txt). Returns all meta variables, "
            "data variables (with 0-based xview_id and units), station count, geographic bounding box "
            "(lon_min..lon_max, lat_min..lat_max), suggested principal transect coordinates, variable statistics, "
            "and existing saved .xview files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the .odv collection or .txt spreadsheet file.",
                },
                "scan_data_stats": {
                    "type": "boolean",
                    "description": "Scan station coordinates and variable min/max/percentiles from the collection data. Default true.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "odv_create_plot",
        "description": (
            "Create a native Ocean Data View (.xview) XML configuration and render high-resolution graphics via odv.exe. "
            "Supports plot_type: 'section' (vertical transect with bathymetry), 'surface' (horizontal iso-surface map at a depth), "
            "'scatter' / 'ts_diagram' (property-property or T-S diagram with isopycnals), or 'station' (vertical profile). "
            "Supports interpolation: 'diva' (DIVA finite-element optimal interpolation), 'weighted_average' (Barnes gridding), "
            "'quick' (fast gridding), or 'original' (discrete points/lines). Saves the .xview inside the collection's views folder "
            "so it can be opened and edited in ODV GUI."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection_path": {
                    "type": "string",
                    "description": "Absolute path to the .odv collection (or raw data file to auto-import).",
                },
                "view_name": {
                    "type": "string",
                    "description": "Name for the saved .xview file (e.g., 'Section_Temperature_DIVA', 'TS_Diagram_Isopycnal').",
                },
                "plot_type": {
                    "type": "string",
                    "enum": ["section", "surface", "scatter", "ts_diagram", "station"],
                    "description": "Type of ODV data window to create.",
                },
                "x_var": {
                    "type": "string",
                    "description": "X-axis variable name or ID (e.g. 'Salinity', 'Temperature', 'Section Coordinate', 'Longitude').",
                },
                "y_var": {
                    "type": "string",
                    "description": "Y-axis variable name or ID (e.g. 'Depth', 'Temperature', 'Latitude').",
                },
                "z_var": {
                    "type": "string",
                    "description": "Color/Contour Z-axis variable name or ID (e.g. 'Temperature', 'Salinity', 'Oxygen', 'Phosphate', 'Depth').",
                },
                "interpolation": {
                    "type": "string",
                    "enum": ["diva", "weighted_average", "quick", "original"],
                    "description": "Spatial interpolation / gridding algorithm. Use 'diva' or 'weighted_average' for smooth contour fields.",
                },
                "x_scale_length": {
                    "type": "number",
                    "description": "Horizontal smoothing length scale in per-mille of axis range (default 25.0).",
                },
                "y_scale_length": {
                    "type": "number",
                    "description": "Vertical smoothing length scale in per-mille of axis range (default 25.0).",
                },
                "auto_scale_lengths": {
                    "type": "boolean",
                    "description": "Let ODV automatically determine optimal gridding scale lengths (default true).",
                },
                "diva_snr": {
                    "type": "number",
                    "description": "DIVA signal-to-noise ratio (default 2.0).",
                },
                "palette": {
                    "type": "string",
                    "description": "ODV color palette name (e.g., 'ODV_Default', 'cmocean_thermal', 'cmocean_haline', 'cmocean_oxy', 'cmocean_dense', 'Viridis', 'BlueRed').",
                },
                "contours": {
                    "type": "boolean",
                    "description": "Draw contour lines over the gridded color field (default true).",
                },
                "draw_isopycnals": {
                    "type": "boolean",
                    "description": "Draw potential density anomaly (sigma_theta) isopycnal curves on T-S scatter plots.",
                },
                "section_lons": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Optional list of longitude waypoints [lon1, lon2, ...] defining the vertical section track.",
                },
                "section_lats": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Optional list of latitude waypoints [lat1, lat2, ...] defining the vertical section track.",
                },
                "section_width_deg": {
                    "type": "number",
                    "description": "Section corridor width in degrees (default auto ~0.5-1.5 deg).",
                },
                "iso_depth": {
                    "type": ["number", "string"],
                    "description": "For 'surface' plot_type: depth horizon in meters (e.g. 0, 50, 100, 200) or 'first'.",
                },
                "y_max_depth": {
                    "type": "number",
                    "description": "Optional maximum depth [m] for vertical profile or section y-axis (e.g. 500 or 1000).",
                },
                "z_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Optional [z_min, z_max] color scale limits.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional figure title annotation on the ODV canvas.",
                },
                "output_image_path": {
                    "type": "string",
                    "description": "Optional output path for the rendered image (.png, .gif, .jpg, .eps, .tif).",
                },
                "dpi": {
                    "type": "integer",
                    "description": "Output image resolution in DPI (default 300).",
                },
                "open_in_gui": {
                    "type": "boolean",
                    "description": "Launch the interactive ODV desktop window with this new view loaded (default false).",
                },
            },
            "required": ["collection_path"],
        },
    },
    {
        "name": "odv_edit_view",
        "description": (
            "Edit an existing ODV view file (.xview) created by the user or AI — update interpolation mode "
            "('diva', 'weighted_average', 'quick', 'original'), gridding scale lengths, displayed variables, "
            "color palette, z_range, contours, T-S isopycnals, or section waypoints — and immediately re-render "
            "the updated graphics via odv.exe."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection_path": {
                    "type": "string",
                    "description": "Absolute path to the .odv collection.",
                },
                "xview_path": {
                    "type": "string",
                    "description": "Absolute path to the existing .xview file to modify.",
                },
                "output_xview_path": {
                    "type": "string",
                    "description": "Optional path to save as a new .xview file (defaults to updating xview_path in-place).",
                },
                "interpolation": {
                    "type": "string",
                    "enum": ["diva", "weighted_average", "quick", "original"],
                    "description": "New interpolation / gridding mode.",
                },
                "x_scale_length": {"type": "number"},
                "y_scale_length": {"type": "number"},
                "auto_scale_lengths": {"type": "boolean"},
                "palette": {"type": "string"},
                "x_var": {"type": "string"},
                "y_var": {"type": "string"},
                "z_var": {"type": "string"},
                "z_range": {
                    "type": "array",
                    "items": {"type": "number"},
                },
                "contours": {"type": "boolean"},
                "draw_isopycnals": {"type": "boolean"},
                "output_image_path": {"type": "string"},
                "dpi": {"type": "integer"},
                "open_in_gui": {"type": "boolean"},
            },
            "required": ["collection_path", "xview_path"],
        },
    },
    {
        "name": "odv_render_dashboard",
        "description": (
            "Render a 4-panel publication-grade oceanographic synthesis dashboard (Station Map + TEOS-10 T-S Diagram "
            "with sigma_0 isopycnals + 2D Interpolated Vertical Section + 2D Interpolated Horizontal Iso-Surface Map) "
            "from any ODV collection (.odv) or spreadsheet (.txt)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection_or_spreadsheet": {
                    "type": "string",
                    "description": "Absolute path to the .odv collection or .txt ODV spreadsheet.",
                },
                "output_image_path": {
                    "type": "string",
                    "description": "Output PNG path for the 4-panel synthesis figure.",
                },
                "z_var": {
                    "type": "string",
                    "description": "Primary hydrographic variable to grid (e.g. 'Temperature', 'Salinity', 'Oxygen', 'sigma0', 'SoundSpeed').",
                },
                "iso_depth": {
                    "type": "number",
                    "description": "Depth horizon [m] for the horizontal iso-surface map panel (default 50.0).",
                },
                "max_depth": {
                    "type": "number",
                    "description": "Maximum vertical depth [m] for the section and T-S panels (default 1000.0).",
                },
                "interpolation": {
                    "type": "string",
                    "enum": ["diva", "weighted_average", "linear"],
                    "description": "Gridding method ('diva' for RBF optimal interpolation, 'weighted_average' for Barnes Gaussian gridding).",
                },
                "palette": {
                    "type": "string",
                    "description": "Color palette name (e.g. 'ODV_Default', 'cmocean_thermal', 'cmocean_haline', 'Viridis').",
                },
                "dpi": {
                    "type": "integer",
                    "description": "Resolution in DPI (default 300).",
                },
            },
            "required": ["collection_or_spreadsheet", "output_image_path"],
        },
    },
    {
        "name": "odv_run_command_file",
        "description": (
            "Execute a list of native ODV command lines (.cmd syntax: open_collection, load_view, "
            "set_axis_full_ranges, set_axis_range, export_graphics, export_data, import_data, etc.) on odv.exe."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of ODV command file lines to execute.",
                },
                "headless_quit": {
                    "type": "boolean",
                    "description": "Append 'quit' and run with '-q' so ODV exits automatically after execution (default true).",
                },
            },
            "required": ["commands"],
        },
    },
    {
        "name": "odv_open_gui",
        "description": (
            "Launch the interactive Ocean Data View (ODV 5) desktop application on the user's screen "
            "with the specified .odv collection and optional .xview view file pre-loaded."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection_path": {
                    "type": "string",
                    "description": "Absolute path to the .odv collection.",
                },
                "view_path": {
                    "type": "string",
                    "description": "Optional path to a .xview file or template view name (e.g., '$OneSectionWin$').",
                },
                "read_only": {
                    "type": "boolean",
                    "description": "Open collection in ReadOnly access mode (default false).",
                },
            },
            "required": ["collection_path"],
        },
    },
]


def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch an MCP tool call by name and return a structured JSON serializable dictionary."""
    if name == "odv_environment_status":
        return get_odv_environment_info()

    if name == "odv_import_to_collection":
        return import_raw_file_to_odv_collection(
            input_file=arguments["input_file"],
            output_collection_path=arguments.get("output_collection_path"),
            export_spreadsheet=bool(arguments.get("export_spreadsheet", True)),
        )

    if name == "odv_inspect_collection":
        path = os.path.abspath(arguments["path"])
        scan_stats = bool(arguments.get("scan_data_stats", True))
        if path.lower().endswith(".odv"):
            info = inspect_odv_collection(path)
            if scan_stats:
                try:
                    txt_path = ensure_collection_spreadsheet(path)
                    info["data_summary"] = scan_odv_spreadsheet_metadata(txt_path)
                except Exception as e:
                    info["data_summary_error"] = str(e)
            return info
        else:
            return scan_odv_spreadsheet_metadata(path)

    if name == "odv_create_plot":
        coll_path = arguments["collection_path"]
        sec_cfg = None
        if arguments.get("section_lons") and arguments.get("section_lats"):
            sec_cfg = {
                "lons": arguments["section_lons"],
                "lats": arguments["section_lats"],
                "width_deg": arguments.get("section_width_deg", 0.6),
                "coordinate": "distance",
            }
        iso_list = None
        if arguments.get("iso_depth") is not None and str(arguments.get("plot_type", "")).lower() in ("surface", "isosurface", "map"):
            from odv_mcp.xview import resolve_variable_id
            abs_c = os.path.abspath(coll_path)
            if abs_c.lower().endswith(".odv"):
                c_info = inspect_odv_collection(abs_c)
                vars_list = c_info["data_variables"]
            else:
                vars_list = []
            tid, tlbl = resolve_variable_id(arguments.get("z_var", "Temperature"), vars_list, default_id=1)
            sid, slbl = resolve_variable_id("Depth", vars_list, default_id=0)
            iso_list = [{
                "var_id": tid,
                "var_label": tlbl,
                "var_name": tlbl.split("[")[0].strip(),
                "surface_var_id": sid,
                "surface_var_label": slbl,
                "surface_var_name": slbl.split("[")[0].strip(),
                "surface_value": arguments["iso_depth"],
            }]

        y_range = None
        if arguments.get("y_max_depth") is not None:
            y_range = (0.0, float(arguments["y_max_depth"]))

        z_range = None
        if arguments.get("z_range") and len(arguments["z_range"]) == 2:
            z_range = (float(arguments["z_range"][0]), float(arguments["z_range"][1]))

        return create_and_render_odv_view(
            collection_or_file=coll_path,
            view_name=arguments.get("view_name", "AI_Generated_View"),
            plot_type=arguments.get("plot_type", "section"),
            x_var=arguments.get("x_var"),
            y_var=arguments.get("y_var"),
            z_var=arguments.get("z_var"),
            interpolation=arguments.get("interpolation", "diva"),
            x_scale_length=float(arguments.get("x_scale_length", 25.0)),
            y_scale_length=float(arguments.get("y_scale_length", 25.0)),
            auto_scale_lengths=bool(arguments.get("auto_scale_lengths", True)),
            diva_snr=float(arguments.get("diva_snr", 2.0)),
            palette=arguments.get("palette"),
            contours=arguments.get("contours", True),
            draw_isopycnals=bool(arguments.get("draw_isopycnals", False)),
            section_config=sec_cfg,
            iso_surfaces=iso_list,
            y_range=y_range,
            z_range=z_range,
            title=arguments.get("title"),
            output_image_path=arguments.get("output_image_path"),
            dpi=int(arguments.get("dpi", 300)),
            open_in_gui=bool(arguments.get("open_in_gui", False)),
        )

    if name == "odv_edit_view":
        z_range = None
        if arguments.get("z_range") and len(arguments["z_range"]) == 2:
            z_range = (float(arguments["z_range"][0]), float(arguments["z_range"][1]))
        return edit_and_render_odv_view(
            collection_path=arguments["collection_path"],
            xview_path=arguments["xview_path"],
            output_xview_path=arguments.get("output_xview_path"),
            interpolation=arguments.get("interpolation"),
            x_scale_length=arguments.get("x_scale_length"),
            y_scale_length=arguments.get("y_scale_length"),
            auto_scale_lengths=arguments.get("auto_scale_lengths"),
            palette=arguments.get("palette"),
            x_var=arguments.get("x_var"),
            y_var=arguments.get("y_var"),
            z_var=arguments.get("z_var"),
            z_range=z_range,
            contours=arguments.get("contours"),
            draw_isopycnals=arguments.get("draw_isopycnals"),
            output_image_path=arguments.get("output_image_path"),
            dpi=int(arguments.get("dpi", 300)),
            open_in_gui=bool(arguments.get("open_in_gui", False)),
        )

    if name == "odv_render_dashboard":
        return render_scientific_dashboard(
            collection_or_spreadsheet=arguments["collection_or_spreadsheet"],
            output_image_path=arguments["output_image_path"],
            z_var=arguments.get("z_var", "Temperature"),
            iso_depth=float(arguments.get("iso_depth", 50.0)),
            max_depth=float(arguments.get("max_depth", 1000.0)),
            interpolation=arguments.get("interpolation", "diva"),
            palette=arguments.get("palette"),
            dpi=int(arguments.get("dpi", 300)),
        )

    if name == "odv_run_command_file":
        return run_odv_command_script(
            cmd_lines=arguments["commands"],
            headless_quit=bool(arguments.get("headless_quit", True)),
        )

    if name == "odv_open_gui":
        return launch_odv_gui(
            collection_path=arguments["collection_path"],
            view_path=arguments.get("view_path"),
            read_only=bool(arguments.get("read_only", False)),
        )

    raise ValueError(f"Unknown MCP tool: {name}")


def run_mcp_stdio_server() -> None:
    """
    Run the Model Context Protocol (MCP) JSON-RPC 2.0 server over stdin/stdout.
    Supports newline-delimited JSON-RPC messages as specified by the MCP stdio transport.
    """
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no 'id' and expect no response
        if msg_id is None:
            continue

        try:
            if method == "initialize":
                client_proto = params.get("protocolVersion", "2024-11-05")
                resp_result = {
                    "protocolVersion": client_proto,
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "odv-mcp",
                        "version": __version__,
                    },
                }
                _send_jsonrpc_result(msg_id, resp_result)
            elif method == "ping":
                _send_jsonrpc_result(msg_id, {})
            elif method == "tools/list":
                _send_jsonrpc_result(msg_id, {"tools": MCP_TOOLS})
            elif method == "resources/list":
                _send_jsonrpc_result(msg_id, {"resources": []})
            elif method == "prompts/list":
                _send_jsonrpc_result(msg_id, {"prompts": []})
            elif method == "tools/call":
                t_name = params.get("name", "")
                t_args = params.get("arguments") or {}
                try:
                    out = execute_tool(t_name, t_args)
                    _send_jsonrpc_result(
                        msg_id,
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(out, indent=2, ensure_ascii=False),
                                }
                            ],
                            "isError": False,
                        },
                    )
                except Exception as tool_err:
                    _send_jsonrpc_result(
                        msg_id,
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(
                                        {
                                            "error": str(tool_err),
                                            "traceback": traceback.format_exc(),
                                        },
                                        indent=2,
                                        ensure_ascii=False,
                                    ),
                                }
                            ],
                            "isError": True,
                        },
                    )
            else:
                _send_jsonrpc_error(msg_id, -32601, f"Method not found: {method}")
        except Exception as err:
            _send_jsonrpc_error(msg_id, -32603, str(err))


def _send_jsonrpc_result(msg_id: Any, result: Any) -> None:
    payload = {"jsonrpc": "2.0", "id": msg_id, "result": result}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _send_jsonrpc_error(msg_id: Any, code: int, message: str) -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="odv-mcp",
        description="Ocean Data View (ODV 5) Model Context Protocol (MCP) Server & AI CLI",
    )
    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("serve", help="Run the MCP JSON-RPC 2.0 server over stdio (default)")
    sub.add_parser("status", help="Check local ODV 5, DIVA, and palette installation status")

    p_imp = sub.add_parser("import", help="Import raw WOD/CTD/CSV/NetCDF file into a ready-to-open .odv collection")
    p_imp.add_argument("input_file", help="Path to input file (e.g. CTDO1012.gz, CTDO1012, data.csv)")
    p_imp.add_argument("-o", "--output", help="Output .odv collection path")

    p_ins = sub.add_parser("inspect", help="Inspect an .odv collection or .txt spreadsheet")
    p_ins.add_argument("path", help="Path to .odv or .txt file")

    p_plt = sub.add_parser("plot", help="Create an ODV .xview view and render high-res graphics via odv.exe")
    p_plt.add_argument("collection_path", help="Path to .odv collection or raw file")
    p_plt.add_argument("--type", default="section", choices=["section", "surface", "scatter", "ts_diagram", "station"])
    p_plt.add_argument("--view-name", default="AI_View")
    p_plt.add_argument("--x-var", default=None)
    p_plt.add_argument("--y-var", default=None)
    p_plt.add_argument("--z-var", default="Temperature")
    p_plt.add_argument("--interp", default="diva", choices=["diva", "weighted_average", "quick", "original"])
    p_plt.add_argument("--palette", default=None)
    p_plt.add_argument("--iso-depth", type=float, default=None)
    p_plt.add_argument("--max-depth", type=float, default=None)
    p_plt.add_argument("--open-gui", action="store_true", help="Open the resulting view interactively in ODV GUI")
    p_plt.add_argument("-o", "--output", default=None, help="Output PNG image path")

    p_dash = sub.add_parser("dashboard", help="Render a 4-panel hydrographic synthesis dashboard")
    p_dash.add_argument("input_path", help="Path to .odv collection or .txt spreadsheet")
    p_dash.add_argument("-o", "--output", required=True, help="Output PNG path")
    p_dash.add_argument("--z-var", default="Temperature")
    p_dash.add_argument("--iso-depth", type=float, default=50.0)
    p_dash.add_argument("--max-depth", type=float, default=1000.0)
    p_dash.add_argument("--interp", default="diva", choices=["diva", "weighted_average", "linear"])

    args = parser.parse_args()

    if args.subcommand in (None, "serve"):
        run_mcp_stdio_server()
    elif args.subcommand == "status":
        print(json.dumps(execute_tool("odv_environment_status", {}), indent=2))
    elif args.subcommand == "import":
        res = execute_tool(
            "odv_import_to_collection",
            {"input_file": args.input_file, "output_collection_path": args.output},
        )
        print(json.dumps(res, indent=2))
    elif args.subcommand == "inspect":
        res = execute_tool("odv_inspect_collection", {"path": args.path})
        print(json.dumps(res, indent=2))
    elif args.subcommand == "plot":
        res = execute_tool(
            "odv_create_plot",
            {
                "collection_path": args.collection_path,
                "view_name": args.view_name,
                "plot_type": args.type,
                "x_var": args.x_var,
                "y_var": args.y_var,
                "z_var": args.z_var,
                "interpolation": args.interp,
                "palette": args.palette,
                "iso_depth": args.iso_depth,
                "y_max_depth": args.max_depth,
                "output_image_path": args.output,
                "open_in_gui": args.open_gui,
            },
        )
        print(json.dumps(res, indent=2))
    elif args.subcommand == "dashboard":
        res = execute_tool(
            "odv_render_dashboard",
            {
                "collection_or_spreadsheet": args.input_path,
                "output_image_path": args.output,
                "z_var": args.z_var,
                "iso_depth": args.iso_depth,
                "max_depth": args.max_depth,
                "interpolation": args.interp,
            },
        )
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()

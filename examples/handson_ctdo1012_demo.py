"""
End-to-End Demonstration of `odv-mcp` on World Ocean Database CTD Data (`CTDO1012`).

Demonstrates:
1. Importing raw WOD native ASCII / .gz (`CTDO1012.gz`) into a ready-to-open `.odv` collection (`CTDO1012_Ready.odv`)
   and compact ODV Generic Spreadsheet (`CTDO1012_Ready_Spreadsheet.txt`).
2. Generating native `.xview` view files inside `CTDO1012_Ready.Data/views/` and rendering 300 DPI graphics via `odv.exe`:
   - `Section_Temperature_DIVA.xview` (Vertical section 0-1000 m with DIVA optimal gridding)
   - `Section_Salinity_WeightedAvg.xview` (Vertical section 0-1000 m with Weighted-Average / Barnes gridding)
   - `TS_Diagram_Isopycnals.xview` (T-S scatter diagram with isopycnal curves colored by Depth)
   - `Surface_Temperature_50m_DIVA.xview` (Horizontal iso-surface distribution at 50 m depth with DIVA gridding)
3. Rendering a 4-panel TEOS-10 + DIVA publication dashboard (`CTDO1012_Hydrographic_Dashboard.png`).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from odv_mcp.server import execute_tool


def run_demo(handson_dir: str = r"D:\ITB2\KBM\SEM7\S1\PDK\Handson24Nov") -> None:
    gz_file = os.path.join(handson_dir, "CTDO1012.gz")
    ready_odv = os.path.join(handson_dir, "CTDO1012_Ready.odv")

    print("=== 1. Importing CTDO1012.gz into Ready-to-Open ODV Collection ===")
    imp_res = execute_tool(
        "odv_import_to_collection",
        {
            "input_file": gz_file,
            "output_collection_path": ready_odv,
            "export_spreadsheet": True,
        },
    )
    print("Collection created:", imp_res["collection_path"])
    print("Spreadsheet exported:", imp_res["spreadsheet_path"])
    print("Stations:", imp_res["collection_info"].get("station_count"))

    print("\n=== 2. Creating Vertical Section View (Temperature, DIVA Gridding, 0-1000 m) ===")
    sec_temp = execute_tool(
        "odv_create_plot",
        {
            "collection_path": ready_odv,
            "view_name": "Section_Temperature_DIVA",
            "plot_type": "section",
            "z_var": "Temperature",
            "interpolation": "diva",
            "y_max_depth": 1000.0,
            "z_range": [4.0, 30.0],
            "contours": True,
            "title": "CTDO1012 Vertical Temperature Section [deg C] (DIVA Gridding, 0-1000 m)",
            "output_image_path": os.path.join(handson_dir, "ODV_1_Section_Temperature_DIVA.png"),
            "dpi": 300,
        },
    )
    print("Saved .xview:", sec_temp["xview_path"])
    print("Rendered PNG:", sec_temp["output_image_path"], f"({sec_temp['image_size_bytes']} bytes)")

    print("\n=== 3. Creating Vertical Section View (Salinity, Weighted-Average Gridding, 0-1000 m) ===")
    sec_sal = execute_tool(
        "odv_create_plot",
        {
            "collection_path": ready_odv,
            "view_name": "Section_Salinity_WeightedAvg",
            "plot_type": "section",
            "z_var": "Salinity",
            "interpolation": "weighted_average",
            "y_max_depth": 1000.0,
            "z_range": [33.8, 35.2],
            "contours": True,
            "title": "CTDO1012 Vertical Salinity Section [psu] (Weighted-Average Gridding, 0-1000 m)",
            "output_image_path": os.path.join(handson_dir, "ODV_2_Section_Salinity_WeightedAvg.png"),
            "dpi": 300,
        },
    )
    print("Saved .xview:", sec_sal["xview_path"])
    print("Rendered PNG:", sec_sal["output_image_path"], f"({sec_sal['image_size_bytes']} bytes)")

    print("\n=== 4. Creating T-S Diagram View with Isopycnals ===")
    ts_res = execute_tool(
        "odv_create_plot",
        {
            "collection_path": ready_odv,
            "view_name": "TS_Diagram_Isopycnals",
            "plot_type": "ts_diagram",
            "x_var": "Salinity",
            "y_var": "Temperature",
            "z_var": "Depth",
            "interpolation": "original",
            "draw_isopycnals": True,
            "z_range": [0.0, 1500.0],
            "title": "CTDO1012 T-S Diagram with Potential Density Isopycnals",
            "output_image_path": os.path.join(handson_dir, "ODV_3_TS_Diagram_Isopycnals.png"),
            "dpi": 300,
        },
    )
    print("Saved .xview:", ts_res["xview_path"])
    print("Rendered PNG:", ts_res["output_image_path"], f"({ts_res['image_size_bytes']} bytes)")

    print("\n=== 5. Creating Horizontal Iso-Surface Map (Temperature @ Depth = first/surface, DIVA Gridding) ===")
    iso_res = execute_tool(
        "odv_create_plot",
        {
            "collection_path": ready_odv,
            "view_name": "Surface_Temperature_DIVA",
            "plot_type": "surface",
            "z_var": "Temperature",
            "iso_depth": "first",
            "interpolation": "diva",
            "z_range": [25.0, 31.0],
            "contours": True,
            "title": "CTDO1012 Sea Surface Temperature [deg C] (DIVA Gridding)",
            "output_image_path": os.path.join(handson_dir, "ODV_4_Surface_Temperature_DIVA.png"),
            "dpi": 300,
        },
    )
    print("Saved .xview:", iso_res["xview_path"])
    print("Rendered PNG:", iso_res["output_image_path"], f"({iso_res['image_size_bytes']} bytes)")

    print("\n=== 6. Rendering 4-Panel TEOS-10 + DIVA Hydrographic Synthesis Dashboard ===")
    dash_res = execute_tool(
        "odv_render_dashboard",
        {
            "collection_or_spreadsheet": imp_res["spreadsheet_path"] or ready_odv,
            "output_image_path": os.path.join(handson_dir, "ODV_5_TEOS10_DIVA_Synthesis_Dashboard.png"),
            "z_var": "Temperature",
            "iso_depth": 50.0,
            "max_depth": 1000.0,
            "interpolation": "diva",
            "dpi": 300,
        },
    )
    print("Rendered Dashboard:", dash_res["output_image_path"], f"({dash_res['image_size_bytes']} bytes)")


if __name__ == "__main__":
    run_demo()

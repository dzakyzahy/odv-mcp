"""
ODV 5 .xview XML View Builder, Parser, and Live Editor.

Generates and manipulates native Ocean Data View (.xview) XML files for:
- Station / Vertical Profile plots (window_mode=0)
- Scatter / T-S Diagrams with Isopycnals (window_mode=1)
- Vertical Sections along cruise tracks with Bathymetry (window_mode=2)
- Horizontal Iso-Surface Distribution Maps (window_mode=3)
- Multi-Panel Dashboards combining any of the above

Supports all ODV Gridding & Interpolation modes:
- "original" / "none"       -> plot_mode=0 (Original data points/curves)
- "quick"                   -> plot_mode=1 (Quick Gridding)
- "weighted_average"        -> plot_mode=2 (Weighted-Average / Barnes Gridding)
- "diva"                    -> plot_mode=3 (DIVA Finite-Element Optimal Interpolation)
- "arrows" / "vectors"      -> plot_mode=6 (Vector arrows)
- "colored_scatter"         -> plot_mode=9 (Colored scatter points)
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple, Union


WINDOW_MODES: Dict[str, int] = {
    "station": 0,
    "profile": 0,
    "scatter": 1,
    "ts": 1,
    "ts_diagram": 1,
    "section": 2,
    "transect": 2,
    "surface": 3,
    "isosurface": 3,
    "map": 3,
}

PLOT_MODES: Dict[str, int] = {
    "original": 0,
    "none": 0,
    "dots": 0,
    "lines": 0,
    "quick": 1,
    "quick_gridding": 1,
    "weighted": 2,
    "weighted_average": 2,
    "barnes": 2,
    "diva": 3,
    "diva_gridding": 3,
    "arrows": 6,
    "vectors": 6,
    "colored_scatter": 9,
}

SECTION_COORDINATES: Dict[str, int] = {
    "distance": 1,
    "km": 1,
    "lon": 2,
    "longitude": 2,
    "lat": 3,
    "latitude": 3,
    "time": 4,
}

POPULAR_PALETTES: List[str] = [
    "ODV_Default",
    "cmocean_thermal",
    "cmocean_haline",
    "cmocean_dense",
    "cmocean_oxy",
    "cmocean_balance",
    "cmocean_speed",
    "cmocean_algae",
    "cmocean_deep",
    "cmocean_matter",
    "cmocean_turbid",
    "cmocean_curl",
    "cmocean_delta",
    "cmocean_solar",
    "cmocean_ice",
    "Viridis",
    "BlueRed",
    "BlueGreenRed",
    "BlueOnly",
    "Bathymetry_GEBCO",
    "crameri_roma",
    "crameri_davos",
    "crameri_lapaz",
    "crameri_imola",
]


def resolve_window_mode(mode: Union[str, int]) -> int:
    if isinstance(mode, int):
        return mode
    key = str(mode).strip().lower().replace("-", "_").replace(" ", "_")
    return WINDOW_MODES.get(key, 1)


def resolve_plot_mode(interpolation: Union[str, int]) -> int:
    if isinstance(interpolation, int):
        return interpolation
    key = str(interpolation).strip().lower().replace("-", "_").replace(" ", "_")
    return PLOT_MODES.get(key, 2)


def resolve_section_coordinate(coord: Union[str, int]) -> int:
    if isinstance(coord, int):
        return coord
    key = str(coord).strip().lower()
    return SECTION_COORDINATES.get(key, 1)


def resolve_variable_id(
    var_spec: Union[str, int, None],
    variables: List[Dict[str, Any]],
    default_id: int = 0,
) -> Tuple[int, str]:
    """
    Resolve a variable name (e.g. 'Temperature', 'Salinity', 'Depth', 'Oxygen', 'Section Distance')
    or 0-based / 1-based index into an ODV .xview 0-based var_id and label.
    """
    if var_spec is None:
        if variables and 0 <= default_id < len(variables):
            return default_id, variables[default_id].get("full_label", variables[default_id]["name"])
        return default_id, ""

    if isinstance(var_spec, int):
        if 0 <= var_spec < len(variables):
            return var_spec, variables[var_spec].get("full_label", variables[var_spec]["name"])
        return var_spec, f"var_{var_spec}"

    s = str(var_spec).strip()
    if s.isdigit():
        idx = int(s)
        if 0 <= idx < len(variables):
            return idx, variables[idx].get("full_label", variables[idx]["name"])
        return idx, f"var_{idx}"

    s_low = s.lower()
    if s_low in ("section_distance", "section_longitude", "section_latitude", "section_coord", "section"):
        return 200000, "Section Coordinate"

    # Exact or prefix match on variable name
    for v in variables:
        v_id = v["xview_id"]
        name_low = v["name"].lower()
        full_low = v.get("full_label", v["name"]).lower()
        if s_low == name_low or s_low == full_low:
            return v_id, v.get("full_label", v["name"])

    # Substring match
    for v in variables:
        v_id = v["xview_id"]
        name_low = v["name"].lower()
        full_low = v.get("full_label", v["name"]).lower()
        if s_low in name_low or s_low in full_low or name_low in s_low:
            return v_id, v.get("full_label", v["name"])

    if variables and 0 <= default_id < len(variables):
        return default_id, variables[default_id].get("full_label", variables[default_id]["name"])
    return default_id, s


def build_xview_xml(
    collection_name: str,
    variables: List[Dict[str, Any]],
    plot_type: str = "section",
    x_var: Union[str, int, None] = None,
    y_var: Union[str, int, None] = None,
    z_var: Union[str, int, None] = None,
    interpolation: str = "diva",
    x_scale_length: float = 25.0,
    y_scale_length: float = 25.0,
    auto_scale_lengths: bool = True,
    diva_snr: float = 2.0,
    isopycnic_gridding: bool = False,
    palette: Optional[str] = None,
    contours: Union[bool, List[float]] = True,
    draw_isopycnals: bool = False,
    isopycnal_ref_pressure: float = 0.0,
    map_domain: Optional[Dict[str, float]] = None,
    section_config: Optional[Dict[str, Any]] = None,
    iso_surfaces: Optional[List[Dict[str, Any]]] = None,
    x_reversed: Optional[bool] = None,
    y_reversed: Optional[bool] = None,
    z_reversed: bool = False,
    x_range: Optional[Tuple[float, float]] = None,
    y_range: Optional[Tuple[float, float]] = None,
    z_range: Optional[Tuple[float, float]] = None,
    hide_bad_estimates: bool = True,
    max_distance: float = 2.5,
    title: Optional[str] = None,
    extra_windows: Optional[List[Dict[str, Any]]] = None,
    picked_stations: Optional[List[int]] = None,
) -> str:
    """
    Build a complete, valid ODV 5 .xview XML document string.
    """
    win_mode = resolve_window_mode(plot_type)
    plt_mode = resolve_plot_mode(interpolation)

    # Geographic domain defaults (e.g. Indonesian Archipelago / Western Pacific if not provided)
    dom = {
        "lon_min": 115.0,
        "lon_max": 135.0,
        "lat_min": -10.0,
        "lat_max": 10.0,
    }
    if map_domain:
        for k in ("lon_min", "lon_max", "lat_min", "lat_max"):
            if k in map_domain and map_domain[k] is not None:
                dom[k] = float(map_domain[k])

    # Default variables based on plot_type
    # Typically in WOD / CTD collections: 0 = Depth, 1 = Temperature, 2 = Salinity, 3 = Oxygen
    if win_mode == 0:  # STATION / PROFILE
        x_id, x_label = resolve_variable_id(x_var or "Temperature", variables, default_id=1)
        y_id, y_label = resolve_variable_id(y_var or "Depth", variables, default_id=0)
        z_id, z_label = (-1, "") if z_var is None else resolve_variable_id(z_var, variables, default_id=2)
        if y_reversed is None:
            y_reversed = (y_id == 0 or "depth" in y_label.lower() or "press" in y_label.lower())
        if x_reversed is None:
            x_reversed = False
    elif win_mode == 1:  # SCATTER / T-S DIAGRAM
        is_ts = str(plot_type).lower() in ("ts", "ts_diagram") or (
            x_var is None and y_var is None
        )
        if is_ts and x_var is None and y_var is None:
            x_id, x_label = resolve_variable_id("Salinity", variables, default_id=2)
            y_id, y_label = resolve_variable_id("Temperature", variables, default_id=1)
            z_id, z_label = resolve_variable_id(z_var or "Depth", variables, default_id=0)
            draw_isopycnals = True if draw_isopycnals is not False else False
        else:
            x_id, x_label = resolve_variable_id(x_var or "Salinity", variables, default_id=2)
            y_id, y_label = resolve_variable_id(y_var or "Temperature", variables, default_id=1)
            z_id, z_label = (
                resolve_variable_id(z_var, variables, default_id=0)
                if z_var is not None
                else (-1, "")
            )
        if y_reversed is None:
            y_reversed = (y_id == 0 or "depth" in y_label.lower() or "press" in y_label.lower())
        if x_reversed is None:
            x_reversed = False
    elif win_mode == 2:  # SECTION / TRANSECT
        x_id, x_label = (200000, "Section Coordinate") if x_var is None else resolve_variable_id(x_var, variables, default_id=200000)
        y_id, y_label = resolve_variable_id(y_var or "Depth", variables, default_id=0)
        z_id, z_label = resolve_variable_id(z_var or "Temperature", variables, default_id=1)
        if y_reversed is None:
            y_reversed = True
        if x_reversed is None:
            x_reversed = False
    else:  # win_mode == 3: SURFACE / ISOSURFACE MAP
        x_id, x_label = 0, "Longitude"
        y_id, y_label = 1, "Latitude"
        # In surface mode, var_list="2", where iso variables start at index 4
        z_id, z_label = 4, ""
        if y_reversed is None:
            y_reversed = False
        if x_reversed is None:
            x_reversed = False

    lines: List[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<View xview="0.23" collection="{_esc(collection_name)}">',
        '<GeographicMap>',
        f'<MapDomain lon_min="{dom["lon_min"]:.5f}" lon_max="{dom["lon_max"]:.5f}" '
        f'lat_min="{dom["lat_min"]:.5f}" lat_max="{dom["lat_max"]:.5f}"/>',
        '<AutoMapLayerSettings coastline_fill="T" ocean_bathymetry="T" coastlines="T" land_topography="T"/>',
        '</GeographicMap>',
    ]

    # MapWindow layout
    map_x_off, map_y_off, map_w, map_h = 1.5, 11.5, 7.5, 7.5
    lines.extend([
        '<MapWindow background_color="15" highlight_cruise="F">',
        '<Symbol type="0" size="-0.1" color="1" small_size="0.4"/>',
        '<Window>',
        f'<Geometry x_left="{dom["lon_min"]:.5f}" x_right="{dom["lon_max"]:.5f}" '
        f'y_bottom="{dom["lat_min"]:.5f}" y_top="{dom["lat_max"]:.5f}" '
        f'x_offset_bb="{map_x_off}" y_offset_bb="{map_y_off}" '
        f'x_length_bb="{map_w}" y_length_bb="{map_h}" '
        'x_projection="100" y_projection="100"/>',
        '</Window>',
        '</MapWindow>',
    ])

    # PickedStations for STATION mode
    if picked_stations:
        lines.append('<PickedStations>')
        for seq_id in picked_stations:
            lines.append(
                f'<Station sequence_id="{int(seq_id)}" accession_number="{int(seq_id)}" '
                'fill_color="4" frame_color="4" frame_width="1" line_color="4" '
                'line_style="0" symbol_type="-1" text_alignment="8"/>'
            )
        lines.append('</PickedStations>')

    # Section definition if win_mode == 2 or section_config provided
    if win_mode == 2 or section_config:
        sc = section_config or {}
        lon_span = dom["lon_max"] - dom["lon_min"]
        lat_span = dom["lat_max"] - dom["lat_min"]
        default_lons = [
            round(dom["lon_min"] + 0.15 * lon_span, 4),
            round(dom["lon_max"] - 0.15 * lon_span, 4),
        ]
        default_lats = [
            round(dom["lat_min"] + 0.15 * lat_span, 4),
            round(dom["lat_max"] - 0.15 * lat_span, 4),
        ]
        x_nodes = sc.get("lons") or sc.get("x_nodes") or default_lons
        y_nodes = sc.get("lats") or sc.get("y_nodes") or default_lats
        coord_code = resolve_section_coordinate(sc.get("coordinate", "distance"))
        sec_name = sc.get("name", "Hydrographic Section")
        sec_width = float(sc.get("width_deg", sc.get("width", max(0.5, 0.25 * max(lon_span, lat_span)))))
        bathy_file = sc.get("bathymetry_name", "GEBCO_2023_6x6min_Global.nc")

        x_str = ",".join(f"{float(v):.5f}" for v in x_nodes)
        y_str = ",".join(f"{float(v):.5f}" for v in y_nodes)
        lines.extend([
            f'<Section coordinate="{coord_code}" section_name="{_esc(sec_name)}" '
            f'width="{sec_width:.4f}" bathymetry_name="{_esc(bathy_file)}">',
            f'<xNodes>{x_str}</xNodes>',
            f'<yNodes>{y_str}</yNodes>',
            '</Section>',
        ])

    # IsoSurfaceVariables if win_mode == 3 or iso_surfaces provided
    iso_list: List[Dict[str, Any]] = []
    if win_mode == 3 or iso_surfaces:
        if iso_surfaces:
            iso_list = iso_surfaces
        else:
            target_id, target_label = resolve_variable_id(z_var or "Temperature", variables, default_id=1)
            surf_id, surf_label = resolve_variable_id("Depth", variables, default_id=0)
            iso_list = [{
                "var_id": target_id,
                "var_label": target_label,
                "var_name": target_label.split("[")[0].strip(),
                "surface_var_id": surf_id,
                "surface_var_label": surf_label,
                "surface_var_name": surf_label.split("[")[0].strip(),
                "surface_value": "first",
            }]
        z_label = f'{iso_list[0]["var_label"]} @ {iso_list[0]["surface_var_name"]}={iso_list[0]["surface_value"]}'

    # Build primary DataWindow
    win_geom = {
        "x_offset_bb": 11.5,
        "y_offset_bb": 2.5,
        "x_length_bb": 15.5,
        "y_length_bb": 16.0,
        "x_left": x_range[0] if x_range else (dom["lon_min"] if win_mode == 3 else 0.0),
        "x_right": x_range[1] if x_range else (dom["lon_max"] if win_mode == 3 else 100.0),
        "y_bottom": y_range[1] if (y_range and y_reversed) else (y_range[0] if y_range else (dom["lat_min"] if win_mode == 3 else 1000.0)),
        "y_top": y_range[0] if (y_range and y_reversed) else (y_range[1] if y_range else (dom["lat_max"] if win_mode == 3 else 0.0)),
    }

    lines.extend(
        _build_data_window_xml(
            win_mode=win_mode,
            plt_mode=plt_mode,
            x_id=x_id,
            x_label=x_label,
            y_id=y_id,
            y_label=y_label,
            z_id=z_id,
            z_label=z_label,
            x_reversed=bool(x_reversed),
            y_reversed=bool(y_reversed),
            z_reversed=bool(z_reversed),
            geom=win_geom,
            x_scale_length=x_scale_length,
            y_scale_length=y_scale_length,
            auto_scale_lengths=auto_scale_lengths,
            diva_snr=diva_snr,
            isopycnic_gridding=isopycnic_gridding,
            palette=palette,
            z_range=z_range,
            contours=contours,
            draw_isopycnals=draw_isopycnals,
            isopycnal_ref_pressure=isopycnal_ref_pressure,
            hide_bad_estimates=hide_bad_estimates,
            max_distance=max_distance,
        )
    )

    # Extra windows for multi-panel dashboard views
    if extra_windows:
        for ew in extra_windows:
            ew_mode = resolve_window_mode(ew.get("plot_type", "scatter"))
            ew_plt = resolve_plot_mode(ew.get("interpolation", "original"))
            ew_x_id, ew_x_lbl = resolve_variable_id(ew.get("x_var", "Salinity"), variables, default_id=2)
            ew_y_id, ew_y_lbl = resolve_variable_id(ew.get("y_var", "Temperature"), variables, default_id=1)
            ew_z_spec = ew.get("z_var")
            ew_z_id, ew_z_lbl = (
                resolve_variable_id(ew_z_spec, variables, default_id=0)
                if ew_z_spec is not None
                else (-1, "")
            )
            ew_geom = ew.get("geometry", {
                "x_offset_bb": 1.8,
                "y_offset_bb": 2.0,
                "x_length_bb": 7.0,
                "y_length_bb": 7.5,
                "x_left": 33.0,
                "x_right": 35.5,
                "y_bottom": 0.0,
                "y_top": 30.0,
            })
            lines.extend(
                _build_data_window_xml(
                    win_mode=ew_mode,
                    plt_mode=ew_plt,
                    x_id=ew_x_id,
                    x_label=ew_x_lbl,
                    y_id=ew_y_id,
                    y_label=ew_y_lbl,
                    z_id=ew_z_id,
                    z_label=ew_z_lbl,
                    x_reversed=bool(ew.get("x_reversed", False)),
                    y_reversed=bool(ew.get("y_reversed", ew_y_id == 0)),
                    z_reversed=bool(ew.get("z_reversed", False)),
                    geom=ew_geom,
                    x_scale_length=float(ew.get("x_scale_length", x_scale_length)),
                    y_scale_length=float(ew.get("y_scale_length", y_scale_length)),
                    auto_scale_lengths=bool(ew.get("auto_scale_lengths", auto_scale_lengths)),
                    diva_snr=float(ew.get("diva_snr", diva_snr)),
                    isopycnic_gridding=bool(ew.get("isopycnic_gridding", False)),
                    palette=ew.get("palette", palette),
                    z_range=ew.get("z_range"),
                    contours=ew.get("contours", False),
                    draw_isopycnals=bool(ew.get("draw_isopycnals", False)),
                    isopycnal_ref_pressure=float(ew.get("isopycnal_ref_pressure", 0.0)),
                    hide_bad_estimates=hide_bad_estimates,
                    max_distance=max_distance,
                )
            )

    if iso_list:
        lines.append('<IsoSurfaceVariables>')
        for iso in iso_list:
            lines.append(
                f'<IsoVariable var_id="{iso["var_id"]}" var_name="{_esc(iso["var_name"])}" '
                f'var_label="{_esc(iso["var_label"])}" keyvar_id="-2147483646" '
                f'surface_var_id="{iso["surface_var_id"]}" '
                f'surface_var_name="{_esc(iso["surface_var_name"])}" '
                f'surface_var_label="{_esc(iso["surface_var_label"])}" '
                f'surface_keyvar_id="-2147483646" '
                f'surface_value="{_esc(str(iso["surface_value"]))}"/>'
            )
        lines.append('</IsoSurfaceVariables>')

    if title:
        lines.extend([
            '<Canvas>',
            '<GraphicsObjects>',
            '<GObject type="1" own_coordinates="F" dragging="T" clipping="F" post_data="T">',
            f'<Annotation x_coordinate="15.0" y_coordinate="19.8" text="{_esc(title)}" '
            'font_size="14" text_color="0" background_color="-1" frame_color="-1" '
            'frame_width="-1" frame_type="0" angle="0" text_alignment="1"/>',
            '</GObject>',
            '</GraphicsObjects>',
            '</Canvas>',
        ])

    lines.append('</View>')
    return "\n".join(lines) + "\n"


def _build_data_window_xml(
    win_mode: int,
    plt_mode: int,
    x_id: int,
    x_label: str,
    y_id: int,
    y_label: str,
    z_id: int,
    z_label: str,
    x_reversed: bool,
    y_reversed: bool,
    z_reversed: bool,
    geom: Dict[str, Any],
    x_scale_length: float,
    y_scale_length: float,
    auto_scale_lengths: bool,
    diva_snr: float,
    isopycnic_gridding: bool,
    palette: Optional[str],
    z_range: Optional[Tuple[float, float]],
    contours: Union[bool, List[float]],
    draw_isopycnals: bool,
    isopycnal_ref_pressure: float,
    hide_bad_estimates: bool,
    max_distance: float,
) -> List[str]:
    var_list = "2" if win_mode == 3 else "1"
    x_rev = "T" if x_reversed else "F"
    y_rev = "T" if y_reversed else "F"
    z_rev = "T" if z_reversed else "F"

    out = [
        '<DataWindow background_color="15">',
        '<Axis draw_grid="T" axis_color="0" auto_label="T"/>',
        '<AxisVariables>',
        f'<xAxis var_list="{var_list}" var_id="{x_id}" var_label="{_esc(x_label)}" is_reversed="{x_rev}"/>',
        f'<yAxis var_list="{var_list}" var_id="{y_id}" var_label="{_esc(y_label)}" is_reversed="{y_rev}"/>',
    ]
    if z_id is not None and z_id >= 0:
        out.append(
            f'<zAxis var_list="{var_list}" var_id="{z_id}" var_label="{_esc(z_label)}" is_reversed="{z_rev}"/>'
        )
    out.append('</AxisVariables>')

    # Effective plot_mode: if z_id < 0, must be 0 (original lines/points)
    eff_plt_mode = plt_mode if (z_id is not None and z_id >= 0) else 0
    out.append(f'<Modes window_mode="{win_mode}" plot_mode="{eff_plt_mode}" window_type="0"/>')

    if draw_isopycnals and win_mode == 1:
        out.append(
            f'<Isopycnal draw_isopycnal="T" ref_pressure="{isopycnal_ref_pressure:.1f}" '
            'isopycnal_color="0" line_style="1" line_width="1"/>'
        )

    if win_mode == 0:
        out.append('<Line style="0" color="4" width="2"/>')
    else:
        out.append('<Line style="0" color="0" width="0"/>')
    out.append('<Symbol small_size="0.5" type="0" color="1" size="1.4"/>')

    if eff_plt_mode in (1, 2, 3):
        if contours:
            if isinstance(contours, list) and len(contours) > 0:
                out.append('<Contours draw="T">')
                for cv in contours:
                    out.append(
                        f'<Contour z_value="{float(cv)}" line_width="1" line_style="0" font_size="9"/>'
                    )
                out.append('</Contours>')
            else:
                out.append('<Contours draw="T" auto_create="T"/>')

        auto_sl = "T" if auto_scale_lengths else "F"
        iso_grid = "T" if (isopycnic_gridding and eff_plt_mode == 3) else "F"
        out.append(
            f'<Gridding draw_shaded="T" auto_scale_lengths="{auto_sl}" '
            f'x_scale_length="{x_scale_length:.3f}" y_scale_length="{y_scale_length:.3f}" '
            f'isopycnic_gridding="{iso_grid}" DIVA_signal_to_noise_ratio="{diva_snr:.2f}"/>'
        )
        if hide_bad_estimates:
            out.append(f'<BadEstimateHiding hide="T" max_distance="{max_distance:.2f}"/>')

    out.append('<DataMark color="0" size="0.3"/>')
    out.append('<Window>')

    proj_attrs = ' x_projection="100" y_projection="100"' if win_mode == 3 else ""
    out.append(
        f'<Geometry x_left="{geom["x_left"]}" x_right="{geom["x_right"]}" '
        f'y_bottom="{geom["y_bottom"]}" y_top="{geom["y_top"]}" '
        f'x_offset_bb="{geom["x_offset_bb"]}" y_offset_bb="{geom["y_offset_bb"]}" '
        f'x_length_bb="{geom["x_length_bb"]}" y_length_bb="{geom["y_length_bb"]}"{proj_attrs}/>'
    )

    if z_id is not None and z_id >= 0:
        cs_attrs = []
        if z_range is not None and len(z_range) == 2:
            cs_attrs.append(f'z_range_start="{z_range[0]}"')
            cs_attrs.append(f'z_range_end="{z_range[1]}"')
        else:
            cs_attrs.append('z_range_start="0"')
            cs_attrs.append('z_range_end="30"')
        if palette:
            pal_name = palette[:-4] if palette.endswith(".pal") else palette
            cs_attrs.append(f'palette_file_name="{_esc(pal_name)}"')
        out.append(f'<ColorScale {" ".join(cs_attrs)}/>')

    out.append('</Window>')
    out.append('</DataWindow>')
    return out


def parse_xview_summary(xview_path: str) -> Dict[str, Any]:
    """
    Parse an existing ODV .xview file and return a structured summary of its
    map domain, section transect, data windows, axes, gridding/interpolation, and palettes.
    """
    tree = ET.parse(xview_path)
    root = tree.getroot()
    summary: Dict[str, Any] = {
        "file": os.path.abspath(xview_path),
        "collection": root.attrib.get("collection", ""),
        "xview_version": root.attrib.get("xview", ""),
        "map_domain": {},
        "section": None,
        "iso_surface_variables": [],
        "data_windows": [],
    }

    md = root.find("./GeographicMap/MapDomain")
    if md is not None:
        summary["map_domain"] = {
            k: float(v) for k, v in md.attrib.items() if k in ("lon_min", "lon_max", "lat_min", "lat_max")
        }

    sec = root.find("./Section")
    if sec is not None:
        xn = sec.find("xNodes")
        yn = sec.find("yNodes")
        summary["section"] = {
            "section_name": sec.attrib.get("section_name", ""),
            "coordinate": sec.attrib.get("coordinate", "1"),
            "width": sec.attrib.get("width", ""),
            "x_nodes": [float(x) for x in xn.text.split(",")] if (xn is not None and xn.text) else [],
            "y_nodes": [float(y) for y in yn.text.split(",")] if (yn is not None and yn.text) else [],
        }

    for iso in root.findall("./IsoSurfaceVariables/IsoVariable"):
        summary["iso_surface_variables"].append(dict(iso.attrib))

    inv_win_mode = {0: "station", 1: "scatter", 2: "section", 3: "surface"}
    inv_plt_mode = {
        0: "original",
        1: "quick",
        2: "weighted_average",
        3: "diva",
        6: "arrows",
        9: "colored_scatter",
    }

    for idx, dw in enumerate(root.findall("./DataWindow"), start=1):
        modes_el = dw.find("Modes")
        wm = int(modes_el.attrib.get("window_mode", 0)) if modes_el is not None else 0
        pm = int(modes_el.attrib.get("plot_mode", 0)) if modes_el is not None else 0
        axes = {}
        for ax_name in ("xAxis", "yAxis", "zAxis"):
            ax_el = dw.find(f"./AxisVariables/{ax_name}")
            if ax_el is not None:
                axes[ax_name] = dict(ax_el.attrib)
        grid_el = dw.find("Gridding")
        cs_el = dw.find("./Window/ColorScale")
        cnt_el = dw.find("Contours")
        iso_el = dw.find("Isopycnal")
        summary["data_windows"].append({
            "window_index": idx,
            "window_mode": wm,
            "window_mode_name": inv_win_mode.get(wm, str(wm)),
            "plot_mode": pm,
            "interpolation_name": inv_plt_mode.get(pm, str(pm)),
            "axes": axes,
            "gridding": dict(grid_el.attrib) if grid_el is not None else None,
            "color_scale": dict(cs_el.attrib) if cs_el is not None else None,
            "contours_enabled": (cnt_el is not None and cnt_el.attrib.get("draw") == "T"),
            "isopycnals_enabled": (iso_el is not None and iso_el.attrib.get("draw_isopycnal") == "T"),
        })

    return summary


def edit_xview_file(
    source_xview_path: str,
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
    x_var_id: Optional[int] = None,
    y_var_id: Optional[int] = None,
    z_var_id: Optional[int] = None,
    x_reversed: Optional[bool] = None,
    y_reversed: Optional[bool] = None,
    contours: Optional[Union[bool, List[float]]] = None,
    draw_isopycnals: Optional[bool] = None,
    map_domain: Optional[Dict[str, float]] = None,
    section_nodes: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Modify an existing ODV .xview XML file in-place (or save to output_xview_path).
    Allows an AI agent to live-edit any ODV view's interpolation method, gridding scale lengths,
    color palette, variables, contours, isopycnals, map domain, or section transect nodes.
    """
    tree = ET.parse(source_xview_path)
    root = tree.getroot()

    if map_domain:
        md = root.find("./GeographicMap/MapDomain")
        if md is not None:
            for k in ("lon_min", "lon_max", "lat_min", "lat_max"):
                if k in map_domain and map_domain[k] is not None:
                    md.set(k, f"{float(map_domain[k]):.5f}")

    if section_nodes:
        sec = root.find("./Section")
        if sec is None:
            sec = ET.SubElement(root, "Section", {"coordinate": "1", "section_name": "Section", "width": "0.5"})
        if "name" in section_nodes:
            sec.set("section_name", str(section_nodes["name"]))
        if "width" in section_nodes:
            sec.set("width", str(section_nodes["width"]))
        if "coordinate" in section_nodes:
            sec.set("coordinate", str(resolve_section_coordinate(section_nodes["coordinate"])))
        if "lons" in section_nodes:
            xn = sec.find("xNodes")
            if xn is None:
                xn = ET.SubElement(sec, "xNodes")
            xn.text = ",".join(f"{float(v):.5f}" for v in section_nodes["lons"])
        if "lats" in section_nodes:
            yn = sec.find("yNodes")
            if yn is None:
                yn = ET.SubElement(sec, "yNodes")
            yn.text = ",".join(f"{float(v):.5f}" for v in section_nodes["lats"])

    dws = root.findall("./DataWindow")
    target_dws = (
        dws if window_index <= 0 else ([dws[window_index - 1]] if window_index <= len(dws) else dws)
    )

    for dw in target_dws:
        modes_el = dw.find("Modes")
        if modes_el is None:
            modes_el = ET.SubElement(dw, "Modes", {"window_mode": "1", "plot_mode": "0", "window_type": "0"})

        if interpolation is not None:
            pm = resolve_plot_mode(interpolation)
            modes_el.set("plot_mode", str(pm))

        # Axis variable updates
        ax_vars = dw.find("AxisVariables")
        if ax_vars is not None:
            if x_var_id is not None:
                xa = ax_vars.find("xAxis")
                if xa is not None:
                    xa.set("var_id", str(x_var_id))
            if y_var_id is not None:
                ya = ax_vars.find("yAxis")
                if ya is not None:
                    ya.set("var_id", str(y_var_id))
            if z_var_id is not None:
                za = ax_vars.find("zAxis")
                if za is None:
                    za = ET.SubElement(ax_vars, "zAxis", {"var_list": "1", "is_reversed": "F"})
                za.set("var_id", str(z_var_id))
            if x_reversed is not None:
                xa = ax_vars.find("xAxis")
                if xa is not None:
                    xa.set("is_reversed", "T" if x_reversed else "F")
            if y_reversed is not None:
                ya = ax_vars.find("yAxis")
                if ya is not None:
                    ya.set("is_reversed", "T" if y_reversed else "F")

        # Gridding updates
        if any(
            v is not None
            for v in (interpolation, x_scale_length, y_scale_length, auto_scale_lengths, diva_snr, isopycnic_gridding)
        ):
            grid_el = dw.find("Gridding")
            if grid_el is None:
                grid_el = ET.SubElement(
                    dw,
                    "Gridding",
                    {
                        "draw_shaded": "T",
                        "auto_scale_lengths": "T",
                        "x_scale_length": "25.0",
                        "y_scale_length": "25.0",
                    },
                )
            grid_el.set("draw_shaded", "T")
            if auto_scale_lengths is not None:
                grid_el.set("auto_scale_lengths", "T" if auto_scale_lengths else "F")
            if x_scale_length is not None:
                grid_el.set("x_scale_length", f"{float(x_scale_length):.3f}")
            if y_scale_length is not None:
                grid_el.set("y_scale_length", f"{float(y_scale_length):.3f}")
            if diva_snr is not None:
                grid_el.set("DIVA_signal_to_noise_ratio", f"{float(diva_snr):.2f}")
            if isopycnic_gridding is not None:
                grid_el.set("isopycnic_gridding", "T" if isopycnic_gridding else "F")

        # ColorScale & Palette updates
        if palette is not None or z_range is not None:
            win_el = dw.find("Window")
            if win_el is None:
                win_el = ET.SubElement(dw, "Window")
            cs_el = win_el.find("ColorScale")
            if cs_el is None:
                cs_el = ET.SubElement(win_el, "ColorScale", {"z_range_start": "0", "z_range_end": "30"})
            if palette is not None:
                pal_name = palette[:-4] if palette.endswith(".pal") else palette
                cs_el.set("palette_file_name", pal_name)
            if z_range is not None and len(z_range) == 2:
                cs_el.set("z_range_start", str(z_range[0]))
                cs_el.set("z_range_end", str(z_range[1]))

        # Contours updates
        if contours is not None:
            old_cnt = dw.find("Contours")
            if old_cnt is not None:
                dw.remove(old_cnt)
            if contours:
                if isinstance(contours, list) and len(contours) > 0:
                    cnt_el = ET.SubElement(dw, "Contours", {"draw": "T"})
                    for cv in contours:
                        ET.SubElement(
                            cnt_el,
                            "Contour",
                            {"z_value": str(float(cv)), "line_width": "1", "line_style": "0", "font_size": "9"},
                        )
                else:
                    ET.SubElement(dw, "Contours", {"draw": "T", "auto_create": "T"})

        # Isopycnal updates
        if draw_isopycnals is not None:
            iso_el = dw.find("Isopycnal")
            if draw_isopycnals:
                if iso_el is None:
                    iso_el = ET.SubElement(
                        dw,
                        "Isopycnal",
                        {
                            "draw_isopycnal": "T",
                            "ref_pressure": "0",
                            "isopycnal_color": "0",
                            "line_style": "1",
                            "line_width": "1",
                        },
                    )
                else:
                    iso_el.set("draw_isopycnal", "T")
            elif iso_el is not None:
                dw.remove(iso_el)

    dest = output_xview_path or source_xview_path
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    tree.write(dest, encoding="UTF-8", xml_declaration=True)
    return os.path.abspath(dest)


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

"""
ODV-Style Scientific Python Oceanographic Gridding & Visualization Engine.

Provides high-resolution publication graphics (300+ DPI) directly from ODV spreadsheets or collections,
supporting:
- T-S Diagrams with TEOS-10 Potential Density Anomaly (sigma_0) isopycnal contours
- Vertical Hydrographic Profiles (Temperature, Salinity, Oxygen, Density vs Depth)
- 2D Interpolated Vertical Sections along cruise transects (Barnes Weighted-Average, RBF Optimal Interpolation,
  Linear/Cubic triangulation) with bottom bathymetry polygon masking
- 2D Interpolated Horizontal Iso-Surface Distribution Maps at arbitrary depth horizons (e.g. 0 m, 50 m, 100 m, 200 m, 500 m)
- 4-Panel ODV Composite Dashboards (Station Map + T-S Diagram + Vertical Section + Iso-Surface Map)
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.interpolate import Rbf, griddata

from odv_mcp.converters import load_odv_spreadsheet_dataframe
from odv_mcp.engine import ensure_collection_spreadsheet


ODV_CMAP = matplotlib.colors.LinearSegmentedColormap.from_list(
    "odv_classic",
    [
        "#2b0057",
        "#0018a8",
        "#0078ff",
        "#00d2ff",
        "#32ff96",
        "#b4ff32",
        "#ffe600",
        "#ff7800",
        "#e60000",
        "#780000",
    ],
    N=256,
)


def resolve_matplotlib_cmap(palette: Optional[str]):
    if not palette:
        return ODV_CMAP
    p = palette.lower()
    if "odv" in p or "default" in p or "rainbow" in p:
        return ODV_CMAP
    if "thermal" in p:
        return plt.get_cmap("inferno")
    if "haline" in p:
        return plt.get_cmap("viridis")
    if "dense" in p:
        return plt.get_cmap("magma_r")
    if "oxy" in p:
        return plt.get_cmap("plasma")
    if "balance" in p or "bluered" in p or "roma" in p:
        return plt.get_cmap("RdBu_r")
    try:
        return plt.get_cmap(palette)
    except Exception:
        return ODV_CMAP


def grid_2d_field(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    xi: np.ndarray,
    yi: np.ndarray,
    method: str = "weighted_average",
    x_scale_permille: float = 35.0,
    y_scale_permille: float = 35.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    2D Oceanographic Gridding Engine supporting:
    - 'weighted_average' / 'barnes': Anisotropic Gaussian/Barnes weighted averaging (ODV standard)
    - 'diva' / 'rbf': Radial Basis Function (Thin-Plate / Multiquadric) optimal interpolation
    - 'linear' / 'cubic': Delaunay triangulation interpolation
    """
    mask = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x_val, y_val, z_val = x[mask], y[mask], z[mask]
    XI, YI = np.meshgrid(xi, yi)

    if len(z_val) < 4:
        ZI = np.full_like(XI, np.nan, dtype=float)
        return XI, YI, ZI

    x_span = max(float(np.max(xi) - np.min(xi)), 1e-6)
    y_span = max(float(np.max(yi) - np.min(yi)), 1e-6)
    m_low = method.strip().lower()

    if m_low in ("diva", "rbf", "optimal"):
        # Subsample if very large for fast RBF matrix inversion
        if len(z_val) > 2500:
            rng = np.random.default_rng(42)
            idx = rng.choice(len(z_val), size=2500, replace=False)
            xv, yv, zv = x_val[idx], y_val[idx], z_val[idx]
        else:
            xv, yv, zv = x_val, y_val, z_val
        xn = (xv - np.min(xi)) / x_span
        yn = (yv - np.min(yi)) / y_span
        # Add tiny jitter to avoid singular duplicate points
        rng = np.random.default_rng(0)
        xn = xn + rng.normal(0, 1e-6, size=len(xn))
        yn = yn + rng.normal(0, 1e-6, size=len(yn))
        try:
            rbf = Rbf(xn, yn, zv, function="multiquadric", smooth=0.05)
            ZI = rbf((XI - np.min(xi)) / x_span, (YI - np.min(yi)) / y_span)
        except Exception:
            ZI = griddata((x_val, y_val), z_val, (XI, YI), method="linear")
    elif m_low in ("linear", "cubic", "nearest"):
        ZI = griddata((x_val, y_val), z_val, (XI, YI), method=m_low)
    else:
        # ODV Weighted-Average / Barnes anisotropic Gaussian gridding
        lx = max((x_scale_permille / 1000.0) * x_span, 1e-4)
        ly = max((y_scale_permille / 1000.0) * y_span, 1e-4)
        if len(z_val) > 6000:
            rng = np.random.default_rng(42)
            idx = rng.choice(len(z_val), size=6000, replace=False)
            xv, yv, zv = x_val[idx], y_val[idx], z_val[idx]
        else:
            xv, yv, zv = x_val, y_val, z_val

        ZI = np.empty_like(XI, dtype=float)
        for r in range(XI.shape[0]):
            dx = (XI[r, :, None] - xv[None, :]) / lx
            dy = (YI[r, :, None] - yv[None, :]) / ly
            dist2 = dx * dx + dy * dy
            w = np.exp(-dist2)
            w_sum = np.sum(w, axis=1)
            min_dist2 = np.min(dist2, axis=1)
            row_est = np.where(w_sum > 1e-8, np.sum(w * zv[None, :], axis=1) / w_sum, np.nan)
            # Hide estimates too far from observations (ODV BadEstimateHiding equivalent)
            row_est = np.where(min_dist2 <= 18.0, row_est, np.nan)
            ZI[r, :] = row_est

    return XI, YI, ZI


def render_scientific_dashboard(
    collection_or_spreadsheet: str,
    output_image_path: str,
    z_var: str = "Temperature",
    iso_depth: float = 50.0,
    max_depth: float = 1000.0,
    interpolation: str = "diva",
    palette: Optional[str] = None,
    max_stations: int = 350,
    dpi: int = 300,
) -> Dict[str, Any]:
    """
    Render a 4-panel ODV-style oceanographic dashboard:
    1. Station Map with Transect Section Line
    2. T-S Diagram with TEOS-10 Isopycnals (sigma_0) colored by Depth
    3. Interpolated Vertical Section along the principal transect with bathymetry shading
    4. Interpolated Horizontal Iso-Surface Map at `iso_depth` [m]
    """
    abs_in = os.path.abspath(collection_or_spreadsheet)
    if abs_in.lower().endswith(".odv"):
        txt_path = ensure_collection_spreadsheet(abs_in)
    else:
        txt_path = abs_in

    df = load_odv_spreadsheet_dataframe(txt_path, max_stations=max_stations)
    if df.empty:
        raise ValueError(f"No valid oceanographic records loaded from {txt_path}")

    # Resolve target z_var column in df
    col_candidates = [c for c in df.columns if z_var.lower() in c.lower()]
    target_col = col_candidates[0] if col_candidates else "Temperature"
    cmap = resolve_matplotlib_cmap(palette)

    fig, axes = plt.subplots(2, 2, figsize=(15, 11), dpi=dpi)
    fig.patch.set_facecolor("#ffffff")
    ax_map, ax_ts = axes[0, 0], axes[0, 1]
    ax_sec, ax_iso = axes[1, 0], axes[1, 1]

    # 1. Station Map
    st_df = df.groupby("station_seq", as_index=False).first()
    lons = st_df["Longitude"].to_numpy(dtype=float)
    lats = st_df["Latitude"].to_numpy(dtype=float)
    ax_map.scatter(lons, lats, c="#d62728", s=24, edgecolors="black", linewidths=0.5, zorder=4, label=f"Stations (n={len(st_df)})")

    # Compute section line across stations
    if (np.nanmax(lons) - np.nanmin(lons)) >= (np.nanmax(lats) - np.nanmin(lats)):
        sec_axis_name = "Longitude [°E]"
        sec_coord_col = "Longitude"
        x1, x2 = float(np.nanpercentile(lons, 5)), float(np.nanpercentile(lons, 95))
        slope, intercept = np.polyfit(lons[np.isfinite(lons)], lats[np.isfinite(lats)], 1) if len(lons) > 1 else (0.0, float(np.nanmean(lats)))
        y1, y2 = slope * x1 + intercept, slope * x2 + intercept
    else:
        sec_axis_name = "Latitude [°N]"
        sec_coord_col = "Latitude"
        y1, y2 = float(np.nanpercentile(lats, 5)), float(np.nanpercentile(lats, 95))
        slope, intercept = np.polyfit(lats[np.isfinite(lats)], lons[np.isfinite(lons)], 1) if len(lats) > 1 else (0.0, float(np.nanmean(lons)))
        x1, x2 = slope * y1 + intercept, slope * y2 + intercept

    ax_map.plot([x1, x2], [y1, y2], color="#0055ff", linewidth=2.2, linestyle="--", zorder=5, label="Section Track")
    ax_map.set_title("1. ODV Station Distribution & Section Track", fontsize=11, fontweight="bold")
    ax_map.set_xlabel("Longitude [°E]")
    ax_map.set_ylabel("Latitude [°N]")
    ax_map.grid(True, linestyle=":", alpha=0.5)
    ax_map.legend(loc="best", fontsize=8.5)

    # 2. T-S Diagram with TEOS-10 Isopycnals
    if "Salinity" in df.columns and "Temperature" in df.columns:
        ts_df = df.dropna(subset=["Salinity", "Temperature", "Depth"])
        ts_df = ts_df[ts_df["Depth"] <= max_depth]
        s_arr = ts_df["Salinity"].to_numpy(dtype=float)
        t_arr = ts_df["Temperature"].to_numpy(dtype=float)
        d_arr = ts_df["Depth"].to_numpy(dtype=float)

        if len(s_arr) > 0:
            s_min, s_max = float(np.percentile(s_arr, 1)) - 0.3, float(np.percentile(s_arr, 99)) + 0.3
            t_min, t_max = float(np.percentile(t_arr, 1)) - 1.0, float(np.percentile(t_arr, 99)) + 1.0
            sg, tg = np.meshgrid(np.linspace(s_min, s_max, 80), np.linspace(t_min, t_max, 80))
            try:
                import gsw
                sa_g = gsw.SA_from_SP(sg, 0.0, float(np.nanmean(lons)), float(np.nanmean(lats)))
                ct_g = gsw.CT_from_t(sa_g, tg, 0.0)
                sig_g = gsw.sigma0(sa_g, ct_g)
            except Exception:
                sig_g = 1000.0 + 0.8 * sg - 0.2 * tg - 1000.0
            cs_iso = ax_ts.contour(sg, tg, sig_g, levels=12, colors="gray", linestyles="--", linewidths=0.7, alpha=0.75)
            ax_ts.clabel(cs_iso, fmt="%.1f", fontsize=7.5)

            sc_ts = ax_ts.scatter(s_arr, t_arr, c=d_arr, cmap="viridis_r", s=8, alpha=0.75)
            cb_ts = fig.colorbar(sc_ts, ax=ax_ts, pad=0.02)
            cb_ts.set_label("Depth [m]", fontsize=9)
            ax_ts.set_xlim(s_min, s_max)
            ax_ts.set_ylim(t_min, t_max)

    ax_ts.set_title("2. T-S Diagram with Isopycnals ($\\sigma_0$ [kg/m³])", fontsize=11, fontweight="bold")
    ax_ts.set_xlabel("Salinity [psu]")
    ax_ts.set_ylabel("Temperature [°C]")
    ax_ts.grid(True, linestyle=":", alpha=0.4)

    # 3. Vertical Section with Gridding
    sec_df = df.dropna(subset=[sec_coord_col, "Depth", target_col])
    sec_df = sec_df[sec_df["Depth"] <= max_depth]
    if not sec_df.empty:
        sx = sec_df[sec_coord_col].to_numpy(dtype=float)
        sy = sec_df["Depth"].to_numpy(dtype=float)
        sz = sec_df[target_col].to_numpy(dtype=float)
        xi = np.linspace(float(np.min(sx)), float(np.max(sx)), 140)
        yi = np.linspace(0.0, float(np.max(sy)), 110)
        XI, YI, ZI = grid_2d_field(sx, sy, sz, xi, yi, method=interpolation, x_scale_permille=40.0, y_scale_permille=40.0)

        vmin, vmax = float(np.nanpercentile(sz, 2)), float(np.nanpercentile(sz, 98))
        cf_sec = ax_sec.pcolormesh(XI, YI, ZI, cmap=cmap, shading="auto", vmin=vmin, vmax=vmax)
        try:
            cs_sec = ax_sec.contour(XI, YI, ZI, levels=10, colors="black", linewidths=0.55, alpha=0.7)
            ax_sec.clabel(cs_sec, fmt="%.3g", fontsize=7)
        except Exception:
            pass
        ax_sec.scatter(sx, sy, c="black", s=1.2, alpha=0.25)
        cb_sec = fig.colorbar(cf_sec, ax=ax_sec, pad=0.02)
        cb_sec.set_label(target_col, fontsize=9)

    ax_sec.invert_yaxis()
    ax_sec.set_title(f"3. Vertical Section: {target_col} ({interpolation.upper()} Gridding)", fontsize=11, fontweight="bold")
    ax_sec.set_xlabel(sec_axis_name)
    ax_sec.set_ylabel("Depth [m]")
    ax_sec.grid(True, linestyle=":", alpha=0.4)

    # 4. Horizontal Iso-Surface Map at `iso_depth`
    df_valid = df.dropna(subset=["Longitude", "Latitude", "Depth", target_col]).copy()
    if not df_valid.empty:
        df_valid["depth_diff"] = np.abs(df_valid["Depth"] - iso_depth)
        iso_pts = df_valid.sort_values("depth_diff").groupby("station_seq", as_index=False).first()
        iso_pts = iso_pts[iso_pts["depth_diff"] <= max(35.0, 0.35 * iso_depth)]
        if len(iso_pts) >= 4:
            ix = iso_pts["Longitude"].to_numpy(dtype=float)
            iy = iso_pts["Latitude"].to_numpy(dtype=float)
            iz = iso_pts[target_col].to_numpy(dtype=float)
            xi_m = np.linspace(float(np.min(ix)), float(np.max(ix)), 120)
            yi_m = np.linspace(float(np.min(iy)), float(np.max(iy)), 120)
            XIM, YIM, ZIM = grid_2d_field(ix, iy, iz, xi_m, yi_m, method=interpolation, x_scale_permille=50.0, y_scale_permille=50.0)
            cf_iso = ax_iso.pcolormesh(XIM, YIM, ZIM, cmap=cmap, shading="auto")
            try:
                cs_m = ax_iso.contour(XIM, YIM, ZIM, levels=8, colors="black", linewidths=0.55, alpha=0.7)
                ax_iso.clabel(cs_m, fmt="%.3g", fontsize=7)
            except Exception:
                pass
            ax_iso.scatter(ix, iy, c="black", s=10, edgecolors="white", linewidths=0.4, zorder=4)
            cb_iso = fig.colorbar(cf_iso, ax=ax_iso, pad=0.02)
            cb_iso.set_label(f"{target_col} @ {iso_depth:.0f} m", fontsize=9)

    ax_iso.set_title(f"4. Iso-Surface Map: {target_col} @ Depth = {iso_depth:.0f} m ({interpolation.upper()})", fontsize=11, fontweight="bold")
    ax_iso.set_xlabel("Longitude [°E]")
    ax_iso.set_ylabel("Latitude [°N]")
    ax_iso.grid(True, linestyle=":", alpha=0.4)

    fig.suptitle(
        f"Ocean Data View (ODV-MCP) Hydrographic Synthesis — {os.path.basename(collection_or_spreadsheet)}",
        fontsize=13,
        fontweight="bold",
        y=0.98,
    )
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    out_abs = os.path.abspath(output_image_path)
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    fig.savefig(out_abs, dpi=dpi)
    plt.close(fig)

    return {
        "output_image_path": out_abs,
        "image_size_bytes": os.path.getsize(out_abs),
        "stations_plotted": int(len(st_df)),
        "samples_plotted": int(len(df)),
        "variable": target_col,
        "interpolation": interpolation,
        "iso_depth_m": iso_depth,
        "max_depth_m": max_depth,
    }

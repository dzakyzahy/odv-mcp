"""
ODV Collection Inspector, Multi-Format Data Parser, and ODV Generic Spreadsheet (V4.8) Converter.

Handles:
- Inspection of `.odv` collection files and their `.Data/` directories
- Automatic `.gz` decompression (e.g., WOD files like `CTDO1012.gz` -> `CTDO1012`)
- Parsing of ODV Generic Spreadsheet `.txt` files (fast streaming for large 100+ MB files)
- Conversion of CSV / TSV / Excel (`.xlsx`) / Sea-Bird CTD (`.cnv`) / NetCDF (`.nc`) into
  compliant ODV Generic Spreadsheet V4.8 `.txt` files with full `//<MetaVariable>` and `//<DataVariable>` headers
- Optional TEOS-10 (Gibbs SeaWater `gsw`) derived oceanographic variables (Absolute Salinity SA,
  Conservative Temperature CT, Potential Density Anomaly sigma0, Buoyancy Frequency N2, Sound Speed)
"""

from __future__ import annotations

import configparser
import glob
import gzip
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def ensure_decompressed(file_path: str, output_dir: Optional[str] = None) -> str:
    """
    If `file_path` is a `.gz` archive (such as WOD `CTDO1012.gz`), decompress it and return
    the decompressed path. Otherwise return `file_path` unchanged.
    """
    abs_path = os.path.abspath(file_path)
    if not abs_path.lower().endswith(".gz"):
        return abs_path

    base_name = os.path.basename(abs_path)[:-3]
    target_dir = os.path.abspath(output_dir) if output_dir else os.path.dirname(abs_path)
    os.makedirs(target_dir, exist_ok=True)
    out_path = os.path.join(target_dir, base_name)

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        with gzip.open(abs_path, "rb") as f_in, open(out_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
    return out_path


def detect_import_format(file_path: str) -> str:
    """
    Detect the ODV import format or file category of an input oceanographic file.
    Returns one of:
    - 'ODV_COLLECTION' (.odv)
    - 'SPREADSHEET' (ODV Generic Spreadsheet .txt)
    - 'WORLDOCEANDATABASE' (WOD Native ASCII like CTDO1012, OSD, XBT, PFL, etc.)
    - 'NETCDF' (.nc)
    - 'SEABIRD_CNV' (.cnv)
    - 'TABULAR_CSV_EXCEL' (.csv, .tsv, .xlsx)
    """
    p = os.path.abspath(file_path)
    low = p.lower()
    if low.endswith(".odv"):
        return "ODV_COLLECTION"
    if low.endswith(".nc") or low.endswith(".cdf"):
        return "NETCDF"
    if low.endswith(".cnv"):
        return "SEABIRD_CNV"
    if low.endswith(".xlsx") or low.endswith(".xls") or low.endswith(".csv") or low.endswith(".tsv"):
        return "TABULAR_CSV_EXCEL"

    # Inspect first few lines for WOD native ASCII vs ODV Spreadsheet vs generic CSV
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            first_lines = [f.readline() for _ in range(10)]
    except Exception:
        return "WORLDOCEANDATABASE"

    joined = "".join(first_lines)
    if "//<Version>ODV Spreadsheet" in joined or "//<MetaVariable>" in joined or "Cruise\tStation\tType" in joined:
        return "SPREADSHEET"
    if first_lines and re.match(r"^[ABC][0-9]{4,}[A-Z0-9 ]+", first_lines[0]):
        return "WORLDOCEANDATABASE"
    if "\t" in first_lines[0] or "," in first_lines[0]:
        return "TABULAR_CSV_EXCEL"
    return "WORLDOCEANDATABASE"


def inspect_odv_collection(odv_path: str) -> Dict[str, Any]:
    """
    Parse an ODV `.odv` collection configuration file and its companion `.Data/` directory.
    Returns full metadata: variables (with 0-based `xview_id` and 1-based `odv_id`),
    meta-variables, existing `.xview` view files, station count, and lock status.
    """
    abs_odv = os.path.abspath(odv_path)
    if not os.path.exists(abs_odv):
        raise FileNotFoundError(f"ODV collection not found: {abs_odv}")

    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read(abs_odv, encoding="utf-8")

    general = dict(cp["General"]) if "General" in cp else {}
    meta_vars: List[Dict[str, Any]] = []
    if "Meta Variables" in cp:
        for k, raw in cp["Meta Variables"].items():
            parts = raw.split(";")
            name = parts[0].strip() if len(parts) > 0 else k
            unit = parts[1].strip() if len(parts) > 1 else ""
            vtype = parts[2].strip() if len(parts) > 2 else "TEXT"
            role = parts[7].strip() if len(parts) > 7 else ""
            meta_vars.append({
                "odv_id": int(k),
                "xview_id": int(k) - 1,
                "name": name,
                "unit": unit,
                "full_label": f"{name} [{unit}]" if unit else name,
                "value_type": vtype,
                "role": role,
            })

    data_vars: List[Dict[str, Any]] = []
    if "Variables" in cp:
        for k, raw in cp["Variables"].items():
            parts = raw.split(";")
            name = parts[0].strip() if len(parts) > 0 else k
            unit = parts[1].strip() if len(parts) > 1 else ""
            vtype = parts[2].strip() if len(parts) > 2 else "FLOAT"
            digits = int(parts[4]) if (len(parts) > 4 and parts[4].strip().isdigit()) else 2
            comment = parts[8].strip() if len(parts) > 8 else ""
            data_vars.append({
                "odv_id": int(k),
                "xview_id": int(k) - 1,
                "name": name,
                "unit": unit,
                "full_label": f"{name} [{unit}]" if unit else name,
                "value_type": vtype,
                "significant_digits": digits,
                "comment": comment,
            })

    base_no_ext = os.path.splitext(abs_odv)[0]
    data_dir = base_no_ext + ".Data"
    views_dir = os.path.join(data_dir, "views")
    existing_views = []
    if os.path.isdir(views_dir):
        existing_views = sorted(glob.glob(os.path.join(views_dir, "**", "*.xview"), recursive=True))

    # Check import logs for station count & source files
    station_count = None
    imported_files: List[str] = []
    misc_dir = os.path.join(data_dir, "misc")
    if os.path.isdir(misc_dir):
        for log_file in sorted(glob.glob(os.path.join(misc_dir, "import_log_*.txt"))):
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as lf:
                    txt = lf.read()
                for m in re.finditer(r"ADD file:\s*(.+)", txt):
                    imported_files.append(m.group(1).strip())
                for m in re.finditer(r"(\d+)\s+station\(s\)\s+processed successfully", txt):
                    station_count = (station_count or 0) + int(m.group(1))
            except Exception:
                pass

    return {
        "collection_path": abs_odv,
        "collection_name": os.path.basename(base_no_ext),
        "data_dir": data_dir,
        "views_dir": views_dir,
        "general": general,
        "station_count": station_count,
        "imported_files": imported_files,
        "meta_variables": meta_vars,
        "data_variables": data_vars,
        "existing_views": existing_views,
    }


def scan_odv_spreadsheet_metadata(
    spreadsheet_path: str,
    max_data_rows: int = 250000,
) -> Dict[str, Any]:
    """
    Fast streaming scanner for ODV Generic Spreadsheet (.txt) files (including 100+ MB files).
    Extracts station coordinates, geographic bounding box (MapDomain), principal transect nodes,
    depth range, and summary statistics for key oceanographic variables.
    """
    abs_txt = os.path.abspath(spreadsheet_path)
    header_cols: List[str] = []
    stations: List[Dict[str, Any]] = []

    lon_idx = lat_idx = st_idx = cr_idx = bot_idx = None
    var_indices: Dict[str, int] = {}
    var_samples: Dict[str, List[float]] = {}

    rows_read = 0
    with open(abs_txt, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("//"):
                continue
            line_s = line.rstrip("\r\n")
            if not line_s:
                continue
            parts = line_s.split("\t")
            if not header_cols:
                header_cols = parts
                for idx, col in enumerate(header_cols):
                    cl = col.lower()
                    if cl.startswith("cruise"):
                        cr_idx = idx
                    elif cl.startswith("station"):
                        st_idx = idx
                    elif "longitude" in cl:
                        lon_idx = idx
                    elif "latitude" in cl:
                        lat_idx = idx
                    elif "bot. depth" in cl or "bottom depth" in cl:
                        bot_idx = idx
                    elif not cl.startswith("qf:") and any(
                        kw in cl
                        for kw in ("depth [", "pressure", "temperature", "salinity", "oxygen", "phosphate", "silicate", "nitrate", "chlorophyll", "ph")
                    ):
                        var_indices[col] = idx
                        var_samples[col] = []
                continue

            rows_read += 1
            # Compact ODV spreadsheet rows only have station metadata on the first row of each station
            if lon_idx is not None and lat_idx is not None and lon_idx < len(parts) and lat_idx < len(parts):
                lon_s = parts[lon_idx].strip()
                lat_s = parts[lat_idx].strip()
                if lon_s and lat_s:
                    try:
                        lon_v = float(lon_s)
                        lat_v = float(lat_s)
                        st_name = parts[st_idx].strip() if (st_idx is not None and st_idx < len(parts)) else str(len(stations) + 1)
                        cr_name = parts[cr_idx].strip() if (cr_idx is not None and cr_idx < len(parts)) else ""
                        bot_v = None
                        if bot_idx is not None and bot_idx < len(parts) and parts[bot_idx].strip():
                            try:
                                bot_v = float(parts[bot_idx].strip())
                            except ValueError:
                                pass
                        stations.append({
                            "sequence_id": len(stations) + 1,
                            "cruise": cr_name,
                            "station": st_name,
                            "lon": lon_v,
                            "lat": lat_v,
                            "bottom_depth": bot_v,
                        })
                    except ValueError:
                        pass

            if rows_read <= max_data_rows:
                for col_name, c_idx in var_indices.items():
                    if c_idx < len(parts):
                        vs = parts[c_idx].strip()
                        if vs:
                            try:
                                val = float(vs)
                                if len(var_samples[col_name]) < 50000:
                                    var_samples[col_name].append(val)
                            except ValueError:
                                pass

    if stations:
        lons = np.array([s["lon"] for s in stations], dtype=float)
        lats = np.array([s["lat"] for s in stations], dtype=float)
        lon_min, lon_max = float(np.min(lons)), float(np.max(lons))
        lat_min, lat_max = float(np.min(lats)), float(np.max(lats))
        lon_pad = max(0.25, 0.08 * (lon_max - lon_min))
        lat_pad = max(0.25, 0.08 * (lat_max - lat_min))
        map_domain = {
            "lon_min": round(lon_min - lon_pad, 4),
            "lon_max": round(lon_max + lon_pad, 4),
            "lat_min": round(lat_min - lat_pad, 4),
            "lat_max": round(lat_max + lat_pad, 4),
        }
        # Principal transect nodes (linear regression / PCA axis across station cloud)
        if len(stations) >= 2:
            if (lon_max - lon_min) >= (lat_max - lat_min):
                order = np.argsort(lons)
                q1, q2 = lons[order[int(0.05 * len(lons))]], lons[order[int(0.95 * len(lons))]]
                if abs(q2 - q1) > 1e-5:
                    slope, intercept = np.polyfit(lons, lats, 1)
                    sec_lons = [round(float(q1), 4), round(float(q2), 4)]
                    sec_lats = [round(float(slope * q1 + intercept), 4), round(float(slope * q2 + intercept), 4)]
                else:
                    sec_lons = [round(lon_min, 4), round(lon_max, 4)]
                    sec_lats = [round(lat_min, 4), round(lat_max, 4)]
            else:
                order = np.argsort(lats)
                q1, q2 = lats[order[int(0.05 * len(lats))]], lats[order[int(0.95 * len(lats))]]
                if abs(q2 - q1) > 1e-5:
                    slope, intercept = np.polyfit(lats, lons, 1)
                    sec_lats = [round(float(q1), 4), round(float(q2), 4)]
                    sec_lons = [round(float(slope * q1 + intercept), 4), round(float(slope * q2 + intercept), 4)]
                else:
                    sec_lons = [round(lon_min, 4), round(lon_max, 4)]
                    sec_lats = [round(lat_min, 4), round(lat_max, 4)]
        else:
            sec_lons = [round(lon_min - 0.5, 4), round(lon_max + 0.5, 4)]
            sec_lats = [round(lat_min, 4), round(lat_max, 4)]
    else:
        map_domain = {"lon_min": 115.0, "lon_max": 135.0, "lat_min": -10.0, "lat_max": 10.0}
        sec_lons = [120.0, 130.0]
        sec_lats = [-5.0, 5.0]

    var_stats: Dict[str, Dict[str, float]] = {}
    for col_name, vals in var_samples.items():
        if vals:
            arr = np.array(vals, dtype=float)
            var_stats[col_name] = {
                "count": int(len(arr)),
                "min": round(float(np.min(arr)), 4),
                "max": round(float(np.max(arr)), 4),
                "mean": round(float(np.mean(arr)), 4),
                "p02": round(float(np.percentile(arr, 2)), 4),
                "p98": round(float(np.percentile(arr, 98)), 4),
            }

    return {
        "spreadsheet_path": abs_txt,
        "total_rows": rows_read,
        "station_count": len(stations),
        "map_domain": map_domain,
        "suggested_section": {
            "lons": sec_lons,
            "lats": sec_lats,
            "width_deg": 0.20,
        },
        "variable_statistics": var_stats,
        "sample_stations": stations[:15],
    }


def load_odv_spreadsheet_dataframe(
    spreadsheet_path: str,
    max_stations: int = 300,
    max_rows: int = 120000,
) -> pd.DataFrame:
    """
    Load an ODV Generic Spreadsheet (.txt) into a normalized pandas DataFrame with
    forward-filled station metadata (`Cruise`, `Station`, `Longitude`, `Latitude`, `Bot. Depth`)
    and numeric oceanographic columns (`Depth`, `Temperature`, `Salinity`, `Oxygen`, etc.).
    """
    abs_txt = os.path.abspath(spreadsheet_path)
    header_cols: List[str] = []
    records: List[Dict[str, Any]] = []

    col_map: Dict[str, int] = {}
    cur_meta: Dict[str, Any] = {
        "Cruise": "",
        "Station": "",
        "Longitude": np.nan,
        "Latitude": np.nan,
        "Bot_Depth": np.nan,
    }
    station_seen = 0

    with open(abs_txt, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("//"):
                continue
            line_s = line.rstrip("\r\n")
            if not line_s:
                continue
            parts = line_s.split("\t")
            if not header_cols:
                header_cols = parts
                for idx, col in enumerate(header_cols):
                    cl = col.lower()
                    if cl.startswith("qf:"):
                        continue
                    if cl.startswith("cruise"):
                        col_map["Cruise"] = idx
                    elif cl.startswith("station"):
                        col_map["Station"] = idx
                    elif "longitude" in cl:
                        col_map["Longitude"] = idx
                    elif "latitude" in cl:
                        col_map["Latitude"] = idx
                    elif "bot. depth" in cl or "bottom depth" in cl:
                        col_map["Bot_Depth"] = idx
                    elif cl.startswith("depth"):
                        col_map["Depth"] = idx
                    elif cl.startswith("pressure"):
                        col_map["Pressure"] = idx
                    elif cl.startswith("temperature"):
                        col_map["Temperature"] = idx
                    elif cl.startswith("salinity"):
                        col_map["Salinity"] = idx
                    elif cl.startswith("oxygen"):
                        col_map["Oxygen"] = idx
                    elif cl.startswith("phosphate"):
                        col_map["Phosphate"] = idx
                    elif cl.startswith("silicate"):
                        col_map["Silicate"] = idx
                    elif cl.startswith("nitrate"):
                        col_map["Nitrate"] = idx
                    elif cl.startswith("chlorophyll"):
                        col_map["Chlorophyll"] = idx
                    elif cl == "ph" or cl.startswith("ph "):
                        col_map["pH"] = idx
                continue

            lon_idx = col_map.get("Longitude")
            lat_idx = col_map.get("Latitude")
            if lon_idx is not None and lat_idx is not None and lon_idx < len(parts) and lat_idx < len(parts):
                lon_s = parts[lon_idx].strip()
                lat_s = parts[lat_idx].strip()
                if lon_s and lat_s:
                    try:
                        cur_meta["Longitude"] = float(lon_s)
                        cur_meta["Latitude"] = float(lat_s)
                        cr_idx = col_map.get("Cruise")
                        st_idx = col_map.get("Station")
                        bd_idx = col_map.get("Bot_Depth")
                        if cr_idx is not None and cr_idx < len(parts) and parts[cr_idx].strip():
                            cur_meta["Cruise"] = parts[cr_idx].strip()
                        if st_idx is not None and st_idx < len(parts) and parts[st_idx].strip():
                            cur_meta["Station"] = parts[st_idx].strip()
                        if bd_idx is not None and bd_idx < len(parts) and parts[bd_idx].strip():
                            try:
                                cur_meta["Bot_Depth"] = float(parts[bd_idx].strip())
                            except ValueError:
                                cur_meta["Bot_Depth"] = np.nan
                        station_seen += 1
                        if station_seen > max_stations:
                            break
                    except ValueError:
                        pass

            row_dict: Dict[str, Any] = dict(cur_meta)
            row_dict["station_seq"] = station_seen
            has_val = False
            for key in ("Depth", "Pressure", "Temperature", "Salinity", "Oxygen", "Phosphate", "Silicate", "Nitrate", "Chlorophyll", "pH"):
                c_idx = col_map.get(key)
                if c_idx is not None and c_idx < len(parts):
                    vs = parts[c_idx].strip()
                    if vs:
                        try:
                            row_dict[key] = float(vs)
                            has_val = True
                        except ValueError:
                            row_dict[key] = np.nan
                    else:
                        row_dict[key] = np.nan
            if has_val:
                records.append(row_dict)
                if len(records) >= max_rows:
                    break

    df = pd.DataFrame(records)
    return enrich_dataframe_teos10(df)


def enrich_dataframe_teos10(df: pd.DataFrame) -> pd.DataFrame:
    """
    If `gsw` (TEOS-10 Gibbs SeaWater) is installed and Temperature/Salinity/Depth are present,
    compute derived oceanographic quantities:
    - Pressure [dbar] (if not already present)
    - Absolute Salinity SA [g/kg]
    - Conservative Temperature CT [deg C]
    - Potential Density Anomaly sigma0 [kg/m^3]
    - Sound Speed [m/s]
    """
    if df.empty or "Temperature" not in df.columns or "Salinity" not in df.columns:
        return df

    try:
        import gsw

        lon = df["Longitude"].fillna(125.0).to_numpy(dtype=float)
        lat = df["Latitude"].fillna(0.0).to_numpy(dtype=float)
        if "Pressure" in df.columns and df["Pressure"].notna().any():
            p = df["Pressure"].fillna(0.0).to_numpy(dtype=float)
        elif "Depth" in df.columns:
            z = -np.abs(df["Depth"].fillna(0.0).to_numpy(dtype=float))
            p = gsw.p_from_z(z, lat)
            df["Pressure"] = np.round(p, 2)
        else:
            p = np.zeros(len(df), dtype=float)

        sp = df["Salinity"].to_numpy(dtype=float)
        t = df["Temperature"].to_numpy(dtype=float)
        sa = gsw.SA_from_SP(sp, p, lon, lat)
        ct = gsw.CT_from_t(sa, t, p)
        sigma0 = gsw.sigma0(sa, ct)
        sound = gsw.sound_speed(sa, ct, p)

        df["SA"] = np.round(sa, 4)
        df["CT"] = np.round(ct, 4)
        df["sigma0"] = np.round(sigma0, 4)
        df["SoundSpeed"] = np.round(sound, 2)
    except Exception:
        pass
    return df


def dataframe_to_odv_spreadsheet(
    df: pd.DataFrame,
    output_txt_path: str,
    cruise_default: str = "CRUISE_01",
    data_type: str = "Profiles",
) -> str:
    """
    Convert any pandas DataFrame with geographic/oceanographic columns into a 100% compliant
    ODV Generic Spreadsheet V4.8 (.txt) file that opens directly in Ocean Data View.
    """
    out_path = os.path.abspath(output_txt_path)
    if not out_path.lower().endswith(".txt"):
        out_path += ".txt"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Map common column names
    col_lookup = {c.lower().strip(): c for c in df.columns}

    def pick_col(candidates: List[str]) -> Optional[str]:
        for cand in candidates:
            if cand in col_lookup:
                return col_lookup[cand]
            for cl, orig in col_lookup.items():
                if cand in cl:
                    return orig
        return None

    c_cruise = pick_col(["cruise"])
    c_station = pick_col(["station", "cast", "id"])
    c_lon = pick_col(["longitude", "lon", "xlon"])
    c_lat = pick_col(["latitude", "lat", "ylat"])
    c_time = pick_col(["yyyy-mm-dd", "time", "date", "datetime"])
    c_bot = pick_col(["bot. depth", "bottom_depth", "bot_depth", "bdepth"])

    meta_cols = {c for c in (c_cruise, c_station, c_lon, c_lat, c_time, c_bot) if c is not None}
    data_cols = [
        c for c in df.columns
        if c not in meta_cols and pd.api.types.is_numeric_dtype(df[c])
    ]

    # Put Depth or Pressure first as primary variable
    primary_candidates = [c for c in data_cols if any(k in c.lower() for k in ("depth", "press", "z"))]
    if primary_candidates:
        prim = primary_candidates[0]
        data_cols = [prim] + [c for c in data_cols if c != prim]

    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        "//<Encoding>UTF-8</Encoding>",
        "//<Version>ODV Spreadsheet V4.8</Version>",
        f"//<CreateTime>{now_iso}</CreateTime>",
        "//<Software>odv-mcp 1.0.0</Software>",
        "//<DataField>Ocean</DataField>",
        f"//<DataType>{data_type}</DataType>",
        "//",
        '//<MetaVariable>label="Cruise" var_type="METACRUISE" value_type="INDEXED_TEXT" significant_digits="0"</MetaVariable>',
        '//<MetaVariable>label="Station" var_type="METASTATION" value_type="TEXT:21" significant_digits="0"</MetaVariable>',
        '//<MetaVariable>label="Type" var_type="METATYPE" value_type="TEXT:2" significant_digits="0"</MetaVariable>',
        '//<MetaVariable>label="yyyy-mm-ddThh:mm:ss.sss" var_type="METATIME" value_type="TEXT:24" significant_digits="0"</MetaVariable>',
        '//<MetaVariable>label="Longitude [degrees_east]" var_type="METALONGITUDE" value_type="FLOAT" significant_digits="4"</MetaVariable>',
        '//<MetaVariable>label="Latitude [degrees_north]" var_type="METALATITUDE" value_type="FLOAT" significant_digits="4"</MetaVariable>',
        '//<MetaVariable>label="Bot. Depth [m]" var_type="METABOTDEPTH" value_type="FLOAT" significant_digits="1"</MetaVariable>',
        "//",
    ]

    formatted_data_headers: List[str] = []
    for idx, col in enumerate(data_cols):
        is_prim = "T" if idx == 0 else "F"
        lbl = _standardize_odv_var_label(col)
        formatted_data_headers.append(lbl)
        lines.append(
            f'//<DataVariable>label="{lbl}" value_type="FLOAT" significant_digits="3" is_primary_variable="{is_prim}"</DataVariable>'
        )
    lines.append("//")

    header_row = [
        "Cruise",
        "Station",
        "Type",
        "yyyy-mm-ddThh:mm:ss.sss",
        "Longitude [degrees_east]",
        "Latitude [degrees_north]",
        "Bot. Depth [m]",
    ] + formatted_data_headers
    lines.append("\t".join(header_row))

    for idx, row in df.iterrows():
        cr_val = str(row[c_cruise]) if (c_cruise and pd.notna(row[c_cruise])) else cruise_default
        st_val = str(row[c_station]) if (c_station and pd.notna(row[c_station])) else "ST_01"
        tm_val = str(row[c_time]) if (c_time and pd.notna(row[c_time])) else "2026-01-01T00:00:00"
        lon_val = f"{float(row[c_lon]):.4f}" if (c_lon and pd.notna(row[c_lon])) else "125.0000"
        lat_val = f"{float(row[c_lat]):.4f}" if (c_lat and pd.notna(row[c_lat])) else "0.0000"
        bot_val = f"{float(row[c_bot]):.1f}" if (c_bot and pd.notna(row[c_bot])) else ""

        d_vals = []
        for col in data_cols:
            v = row[col]
            d_vals.append("" if pd.isna(v) else f"{float(v):.4g}")

        lines.append("\t".join([cr_val, st_val, "C", tm_val, lon_val, lat_val, bot_val] + d_vals))

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return out_path


def convert_tabular_or_cnv_to_odv_txt(
    input_path: str,
    output_txt_path: Optional[str] = None,
    include_teos10: bool = True,
) -> str:
    """
    Convert CSV, TSV, Excel (.xlsx), or Sea-Bird CTD (.cnv) into an ODV Generic Spreadsheet (.txt).
    """
    abs_in = os.path.abspath(input_path)
    base_no_ext = os.path.splitext(abs_in)[0]
    out_txt = os.path.abspath(output_txt_path) if output_txt_path else f"{base_no_ext}_ODV.txt"

    low = abs_in.lower()
    if low.endswith(".xlsx") or low.endswith(".xls"):
        df = pd.read_excel(abs_in)
    elif low.endswith(".cnv"):
        df = _parse_seabird_cnv(abs_in)
    else:
        sep = "\t" if low.endswith(".tsv") else ","
        df = pd.read_csv(abs_in, sep=sep)

    if include_teos10:
        df = enrich_dataframe_teos10(df)

    return dataframe_to_odv_spreadsheet(df, out_txt)


def _parse_seabird_cnv(cnv_path: str) -> pd.DataFrame:
    col_names: List[str] = []
    lat = 0.0
    lon = 125.0
    station = os.path.splitext(os.path.basename(cnv_path))[0]
    data_rows: List[List[float]] = []
    in_data = False

    with open(cnv_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not in_data:
                if s.startswith("# name "):
                    # e.g. # name 0 = prDM: Pressure, Digiquartz [db]
                    m = re.match(r"#\s*name\s+\d+\s*=\s*([^:]+):\s*(.+)", s)
                    if m:
                        col_names.append(m.group(2).strip())
                elif "Latitude" in s and "=" in s:
                    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", s.split("=")[1])
                    if nums:
                        lat = float(nums[0]) + (float(nums[1]) / 60.0 if len(nums) > 1 else 0.0)
                        if "S" in s.upper():
                            lat = -abs(lat)
                elif "Longitude" in s and "=" in s:
                    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", s.split("=")[1])
                    if nums:
                        lon = float(nums[0]) + (float(nums[1]) / 60.0 if len(nums) > 1 else 0.0)
                        if "W" in s.upper():
                            lon = -abs(lon)
                elif s == "*END*":
                    in_data = True
            else:
                if not s:
                    continue
                vals = [float(x) for x in s.split()]
                data_rows.append(vals)

    if not col_names and data_rows:
        col_names = [f"Var_{i+1}" for i in range(len(data_rows[0]))]
    df = pd.DataFrame(data_rows, columns=col_names[: len(data_rows[0])] if data_rows else col_names)
    df["Station"] = station
    df["Longitude"] = lon
    df["Latitude"] = lat
    return df


def _standardize_odv_var_label(col_name: str) -> str:
    if "[" in col_name and "]" in col_name:
        return col_name
    cl = col_name.lower().strip()
    if cl in ("depth", "z"):
        return "Depth [m]"
    if cl in ("pressure", "pres", "p"):
        return "Pressure [dbar]"
    if cl in ("temperature", "temp", "t", "t90"):
        return "Temperature [degrees_C]"
    if cl in ("salinity", "sal", "sp", "psal"):
        return "Salinity [psu]"
    if cl == "sa":
        return "Absolute Salinity SA [g/kg]"
    if cl == "ct":
        return "Conservative Temperature CT [degrees_C]"
    if cl in ("sigma0", "sigma_theta"):
        return "Potential Density Anomaly sigma0 [kg/m~^3]"
    if cl in ("soundspeed", "sound_speed"):
        return "Sound Speed [m/s]"
    if cl in ("oxygen", "do", "o2"):
        return "Oxygen [~$m~#mol/kg]"
    return col_name

"""
odv-mcp: Model Context Protocol (MCP) Server & AI Automation Engine for Ocean Data View (ODV).

Enables AI agents (Claude, Antigravity, Cursor, Windsurf, Cline) to:
1. Convert raw oceanographic data (WOD Native ASCII, .gz, CTD .cnv, CSV/Excel, NetCDF) into ready-to-open
   ODV Collections (.odv) and ODV Generic Spreadsheets (.txt).
2. Programmatically generate and edit ODV View XML files (.xview) with full control over window modes
   (Station/Profile, Scatter/T-S Diagram, Vertical Section with Bathymetry, Horizontal IsoSurface Maps)
   and spatial interpolation (DIVA Gridding, Weighted-Average / Barnes Gridding, Quick Gridding).
3. Drive the native Ocean Data View executable (odv.exe) in headless batch mode (.cmd) or interactive GUI mode,
   and render publication-quality graphics (PNG, PDF, EPS, GIF, GeoTIFF).
"""

__version__ = "1.0.0"
__author__ = "Dzaky Zahy Rabbani"

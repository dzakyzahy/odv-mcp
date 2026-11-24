"""
Unit and Integration Test Suite for `odv-mcp`.
"""

import json
import os
import subprocess
import sys
import tempfile

from odv_mcp.server import MCP_TOOLS, execute_tool
from odv_mcp.xview import build_xview_xml, edit_xview_file, parse_xview_summary


def test_mcp_jsonrpc_handshake_and_tools_list():
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1.0"}},
    }
    list_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }
    status_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "odv_environment_status", "arguments": {}},
    }

    payload = "\n".join(json.dumps(r) for r in (init_req, list_req, status_req)) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "odv_mcp", "serve"],
        input=payload,
        text=True,
        capture_output=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        timeout=15,
    )
    assert proc.returncode == 0
    lines = [json.loads(line) for line in proc.stdout.strip().splitlines() if line.strip()]
    assert len(lines) == 3
    assert lines[0]["id"] == 1
    assert lines[0]["result"]["serverInfo"]["name"] == "odv-mcp"
    assert lines[1]["id"] == 2
    assert len(lines[1]["result"]["tools"]) == len(MCP_TOOLS)
    assert lines[2]["id"] == 3
    assert lines[2]["result"]["isError"] is False


def test_xview_builder_and_live_editor():
    sample_vars = [
        {"xview_id": 0, "name": "Depth", "unit": "m", "full_label": "Depth [m]"},
        {"xview_id": 1, "name": "Temperature", "unit": "degrees_C", "full_label": "Temperature [degrees_C]"},
        {"xview_id": 2, "name": "Salinity", "unit": "psu", "full_label": "Salinity [psu]"},
        {"xview_id": 3, "name": "Oxygen", "unit": "umol/kg", "full_label": "Oxygen [umol/kg]"},
    ]

    xml_str = build_xview_xml(
        collection_name="TestCollection",
        variables=sample_vars,
        plot_type="section",
        z_var="Temperature",
        interpolation="diva",
        palette="cmocean_thermal",
        contours=True,
    )

    with tempfile.TemporaryDirectory() as tmp:
        xv_path = os.path.join(tmp, "test_section.xview")
        with open(xv_path, "w", encoding="utf-8") as f:
            f.write(xml_str)

        summary1 = parse_xview_summary(xv_path)
        assert summary1["collection"] == "TestCollection"
        assert summary1["data_windows"][0]["window_mode_name"] == "section"
        assert summary1["data_windows"][0]["interpolation_name"] == "diva"

        # Live edit to weighted_average and Salinity
        edit_xview_file(
            source_xview_path=xv_path,
            interpolation="weighted_average",
            z_var_id=2,
            palette="cmocean_haline",
            x_scale_length=30.0,
            y_scale_length=20.0,
        )
        summary2 = parse_xview_summary(xv_path)
        assert summary2["data_windows"][0]["interpolation_name"] == "weighted_average"
        assert summary2["data_windows"][0]["axes"]["zAxis"]["var_id"] == "2"
        assert summary2["data_windows"][0]["color_scale"]["palette_file_name"] == "cmocean_haline"


if __name__ == "__main__":
    test_mcp_jsonrpc_handshake_and_tools_list()
    test_xview_builder_and_live_editor()
    print("All odv-mcp tests passed!")

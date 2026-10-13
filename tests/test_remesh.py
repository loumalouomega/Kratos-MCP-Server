"""Adaptive remeshing (MeshingApplication / MMG): expressions, request building,
reports, in-simulation process blocks, rejections and the mesh_remesh /
add_remeshing_process tools. The Kratos-free part uses a fake worker; the
kratos-marked tests at the end run real MMG and skip themselves when the
configured build has no MMG-enabled MeshingApplication."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import time

import pytest

from kratos_mcp import (bridge, checkpoints, flowgraph, jobs, mdpa, mpi_launch, project_explain,
                        remesh, studies)
from kratos_mcp.tools import mesh as mesh_tools
from kratos_mcp.tools import resources as resources_mod
from kratos_mcp.tools import scaffold
from kratos_mcp.tools.resources import EXAMPLES_DIR

EXAMPLE = EXAMPLES_DIR / "adaptive_remeshing"
SETUP_CASE = EXAMPLE / "setup_remesh"


def _tools(module):
    captured: dict = {}

    class FakeMCP:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    module.register(FakeMCP())
    return captured


@pytest.fixture
def plate():
    return mdpa.read(EXAMPLE / "plate.mdpa")


# --- expressions -------------------------------------------------------------

def test_expression_evaluates():
    f = remesh.compile_expression("sqrt((x - 0.5)**2 + (y - 0.5)**2) - 0.25 + 0*z")
    assert f(0.5, 0.75, 0.0) == pytest.approx(0.0)
    assert remesh.compile_expression("tanh(30*(x-0.5))")(0.5) == pytest.approx(0.0)
    assert remesh.compile_expression("max(x, y, 2.0) + pi*0")(1.0, 1.5) == pytest.approx(2.0)
    assert remesh.compile_expression("-x**2")(3.0) == pytest.approx(-9.0)


@pytest.mark.parametrize("bad", [
    "__import__('os').system('true')", "open('/etc/passwd')", "x.real", "x[0]", "lambda: 1",
    "[x for x in (1,)]", "x if y else z", "'text'", "True", "9**9**9", "2**x", "x**17",
    "foo(x)", "unknown + 1", "", "x +", "sqrt(x, y=1)", "x" * 600,
])
def test_expression_rejects_unsafe_or_invalid(bad):
    with pytest.raises(ValueError):
        remesh.compile_expression(bad)


def test_sphere_region_distance():
    inside = remesh.compile_expression(remesh.region_expression(
        {"type": "sphere", "center": [0.5, 0.5], "radius": 0.2}, 2))
    assert inside(0.5, 0.5) == 0.0 and inside(0.55, 0.5) == 0.0     # clamped inside
    assert inside(0.9, 0.5) == pytest.approx(0.2)
    surface = remesh.compile_expression(remesh.region_expression(
        {"type": "sphere", "center": [0.5, 0.5], "radius": 0.2, "mode": "surface"}, 2))
    assert surface(0.5, 0.5) == pytest.approx(-0.2) and surface(0.9, 0.5) == pytest.approx(0.2)


def test_box_region_distance():
    f = remesh.compile_expression(remesh.region_expression(
        {"type": "box", "min": [0.2, 0.2], "max": [0.4, 0.6], "mode": "surface"}, 2))
    assert f(0.3, 0.4) == pytest.approx(-0.1)            # centre: 0.1 from the nearest face
    assert f(0.5, 0.4) == pytest.approx(0.1)             # outside a side
    assert f(0.5, 0.7) == pytest.approx(math.hypot(0.1, 0.1))   # outside a corner
    inside = remesh.compile_expression(remesh.region_expression(
        {"type": "box", "min": [0.2, 0.2], "max": [0.4, 0.6]}, 2))
    assert inside(0.3, 0.4) == 0.0


def test_region_3d_uses_all_axes():
    f = remesh.compile_expression(remesh.region_expression(
        {"type": "sphere", "center": [0, 0, 0], "radius": 1.0, "mode": "surface"}, 3))
    assert f(0.0, 0.0, 2.0) == pytest.approx(1.0)


@pytest.mark.parametrize("region", [
    {"type": "cone"}, {"type": "sphere", "center": [0, 0], "radius": -1},
    {"type": "sphere", "center": [0], "radius": 1}, {"type": "sphere", "radius": 1},
    {"type": "box", "min": [1, 1], "max": [0, 0]}, {"type": "sphere", "center": [0, 0],
                                                    "radius": 1, "mode": "weird"},
])
def test_region_rejects_bad_input(region):
    with pytest.raises(ValueError):
        remesh.region_expression(region, 2)


# --- request building ---------------------------------------------------------

def test_domain_size_and_sizes(plate):
    assert remesh.domain_size(plate) == 2
    sizes = remesh.element_sizes(plate)
    assert len(sizes) == 200 and min(sizes) < max(sizes)
    summary = remesh.summarize_mesh(plate)
    lo, mean, hi = (summary["element_size"][k] for k in ("min", "mean", "max"))
    # The mean is a float sum: on a near-uniform mesh it can land one ulp outside
    # [min, max] on Python < 3.12, where sum() is not compensated.
    assert lo - 1e-12 <= mean <= hi + 1e-12


def test_levelset_request_defaults(plate):
    req = remesh.build_request(plate, region={"type": "sphere", "center": [0.5, 0.5], "radius": 0.1})
    s = req["settings"]
    mean = sum(remesh.element_sizes(plate)) / 200
    assert s["maximal_size"] == pytest.approx(mean) and s["minimal_size"] == pytest.approx(mean / 4)
    assert s["strategy"] == "levelset" and s["initial_remeshing"] and not s["automatic_remesh"]
    assert s["sizing_parameters"]["boundary_layer_max_distance"] == pytest.approx(3 * mean)
    assert req["assign"][0]["variable"] == "DISTANCE" and req["domain_size"] == 2


def test_hessian_and_uniform_requests(plate):
    h = remesh.build_request(plate, strategy="hessian", field_variable="TEMPERATURE",
                             field_expression="x*y", interpolation_error=0.01,
                             check_fields=[{"variable": "PRESSURE", "expression": "x"}])
    hp = h["settings"]["hessian_strategy_parameters"]
    assert hp["metric_variable"] == ["TEMPERATURE"] and hp["interpolation_error"] == 0.01
    assert h["assign"] == [{"variable": "TEMPERATURE", "expression": "x*y"}]
    assert h["check"] == [{"variable": "PRESSURE", "expression": "x"}]
    u = remesh.build_request(plate, strategy="uniform", min_size=0.05)
    assert u["settings"]["minimal_size"] == u["settings"]["maximal_size"] == 0.05
    assert u["assign"] == [{"variable": "DISTANCE", "expression": "1.0"}]


@pytest.mark.parametrize("kwargs, match", [
    ({"strategy": "voronoi"}, "strategy"),
    ({}, "exactly one of region or distance_expression"),
    ({"region": {"type": "sphere", "center": [0, 0], "radius": 1}, "distance_expression": "x"},
     "exactly one"),
    ({"strategy": "hessian"}, "field_variable and field_expression"),
    ({"strategy": "uniform"}, "min_size"),
    ({"strategy": "uniform", "min_size": 0.1, "max_size": 0.2}, "single target size"),
    ({"distance_expression": "x", "min_size": 0.5, "max_size": 0.1}, "must not exceed"),
    ({"distance_expression": "x", "min_size": -1.0}, "positive"),
    ({"distance_expression": "x", "min_size": float("nan")}, "positive"),
    ({"distance_expression": "x", "freeze_sub_model_parts": ["nope"]}, "not found"),
    ({"distance_expression": "x", "check_fields": [{"variable": "T"}]}, "check_fields"),
    ({"distance_expression": "evil()"}, "functions"),
])
def test_request_rejects_bad_input(plate, kwargs, match):
    with pytest.raises(ValueError, match=match):
        remesh.build_request(plate, **kwargs)


def test_freeze_is_passed_as_fix_contour(plate):
    req = remesh.build_request(plate, distance_expression="x", freeze_sub_model_parts=["left"])
    assert req["settings"]["fix_contour_model_parts"] == ["left"]


# --- reports ------------------------------------------------------------------

def test_preservation_report_detects_problems(plate):
    names = remesh.boundary_parts(plate)
    assert sorted(names) == ["bottom", "left", "right", "top"]
    same = remesh.preservation_report(plate, plate, names)
    assert all(item["ok"] for item in same.values())

    moved = mdpa.read(EXAMPLE / "plate.mdpa")
    for nid in moved.sub_model_parts["right"].nodes:
        x, y, z = moved.nodes[nid]
        moved.nodes[nid] = (x + 0.01, y, z)
    moved.sub_model_parts["top"].conditions.clear()
    del moved.sub_model_parts["bottom"]
    report = remesh.preservation_report(plate, moved, [*names, "ghost"])
    assert report["left"]["ok"]
    assert not report["right"]["ok"] and "bounding box moved" in report["right"]["problems"][0]
    assert not report["top"]["ok"] and "conditions lost" in report["top"]["problems"]
    assert report["bottom"]["reason"] == "missing after remeshing"
    assert report["ghost"]["reason"] == "not present in the input mesh"


def test_report_counts(plate):
    finer = mdpa.create_rectangle_mesh(1.0, 1.0, 20, 20, element_name="Element2D3N",
                                       condition_name="LineCondition2D2N", triangles=True)
    r = remesh.report(plate, finer, ["left"])
    assert r["change"]["elements"] == 800 - 200 and r["change"]["element_count_ratio"] == 4.0
    assert r["after"]["element_size"]["mean"] < r["before"]["element_size"]["mean"]
    assert r["preserved"]["left"]["ok"] and r["issues"] == []


# --- in-simulation process ----------------------------------------------------

def test_process_block_and_validation():
    block = remesh.process_block("Thermal", strategy="hessian", variable="TEMPERATURE",
                                 min_size=0.01, max_size=0.1)
    p = block["Parameters"]
    assert block["python_module"] == "mmg_process" and block["kratos_module"].endswith("MeshingApplication")
    assert p["initial_remeshing"] and not p["automatic_remesh"] and p["force_min"] and p["force_max"]
    assert p["hessian_strategy_parameters"] == {"metric_variable": ["TEMPERATURE"]}
    level = remesh.process_block("Thermal", strategy="levelset", variable="DISTANCE")
    assert level["Parameters"]["automatic_remesh"] is True
    assert level["Parameters"]["level_set_strategy_parameters"]["gradient_variable"] == "DISTANCE_GRADIENT"
    issues, warnings = remesh.validate({"processes": {"a": [block]}}, "Thermal", True)
    assert issues == [] and warnings == []
    issues, warnings = remesh.validate({"processes": {"a": [block]}}, "Other", False)
    assert len(issues) == 1 and "MMG" in issues[0] and "root model part" in warnings[0]


@pytest.mark.parametrize("kwargs", [
    {"strategy": "spr", "variable": "T"}, {"variable": None}, {"model_part": ""},
    {"min_size": 0.5, "max_size": 0.1}, {"min_size": -1.0}, {"step_frequency": -1},
])
def test_process_block_rejects_bad_input(kwargs):
    args = {"model_part": "Thermal", "strategy": "hessian", "variable": "TEMPERATURE", **kwargs}
    with pytest.raises(ValueError):
        remesh.process_block(**args)


def test_midrun_remeshing_warns():
    block = remesh.process_block("Thermal", variable="TEMPERATURE", initial_remeshing=False,
                                 step_frequency=5)
    _, warnings = remesh.validate({"processes": {"a": [block]}}, "Thermal", None)
    assert any("SIGSEGV" in w for w in warnings)


def test_detection_and_summary():
    params = json.loads((SETUP_CASE / "ProjectParameters.json").read_text())
    assert remesh.has_remeshing(params) and not remesh.has_remeshing({"processes": {}})
    assert len(remesh.find_processes(params)) == 1
    info = remesh.summarize(params)[0]
    assert info["strategy"] == "levelset" and info["minimal_size"] == 0.02
    explained = project_explain.explain(params)
    assert explained["remeshing"][0]["model_part"] == "ThermalModelPart"
    assert not remesh.has_remeshing(json.loads(
        (EXAMPLES_DIR / "cantilever" / "ProjectParameters.json").read_text()))


def test_example_case_validates_statically():
    result = scaffold.validate_case_files(SETUP_CASE, deep=False)
    assert result["valid"], result
    assert result["warnings"] == []


def test_validate_flags_remeshing_without_meshing(tmp_path, monkeypatch):
    case = tmp_path / "case"
    shutil.copytree(SETUP_CASE, case)
    monkeypatch.setattr(bridge, "run_op", lambda op, *a, **k: (
        {"meshing": False, "mmg": False} if op == "meshing_support"
        else (_ for _ in ()).throw(bridge.BridgeError("no kratos"))))
    result = scaffold.validate_case_files(case, deep=True)
    assert not result["valid"] and any("MMG" in i for i in result["issues"])


def test_remesh_cases_are_rejected_elsewhere():
    params = json.loads((SETUP_CASE / "ProjectParameters.json").read_text())
    with pytest.raises(ValueError, match="mmg_process"):
        studies.serial_case(params)
    with pytest.raises(ValueError, match="mmg_process"):
        checkpoints.serial_single(params)
    params["problem_data"]["parallel_type"] = "MPI"
    with pytest.raises(ValueError, match="mmg_process"):
        mpi_launch.validate_request(params, 2, None)
    # Flowgraph keeps it as a generic process, and the round trip stays lossless.
    params["problem_data"]["parallel_type"] = "OpenMP"
    assert flowgraph.graph_to_project(flowgraph.project_to_graph(params)) == params


# --- add_remeshing_process ------------------------------------------------------

def test_add_remeshing_process_tool(tmp_path):
    case = tmp_path / "case"
    shutil.copytree(SETUP_CASE, case)
    pfile = case / "ProjectParameters.json"
    params = json.loads(pfile.read_text())
    params["processes"]["list_other_processes"] = []
    pfile.write_text(json.dumps(params))
    tool = _tools(scaffold)["add_remeshing_process"]
    result = tool(str(pfile), strategy="levelset", variable="DISTANCE", min_size=0.02, max_size=0.1,
                  freeze_sub_model_parts=["left"])
    assert "error" not in result and result["warnings"] == [], result
    block = json.loads(pfile.read_text())["processes"]["list_other_processes"][0]
    assert block["Parameters"]["model_part_name"] == "ThermalModelPart"
    assert block["Parameters"]["fix_contour_model_parts"] == ["left"]
    assert "error" in tool(str(pfile), strategy="hessian")            # variable missing
    assert "error" in tool(str(case / "nope.json"))
    assert "error" in tool(str(pfile), strategy="bogus", variable="X")


def test_add_remeshing_process_rejects_special_cases(tmp_path):
    case = tmp_path / "fsi"
    shutil.copytree(EXAMPLES_DIR / "fsi_flexible_flap", case, ignore=shutil.ignore_patterns("*.mdpa"))
    assert "error" in _tools(scaffold)["add_remeshing_process"](
        str(case / "ProjectParameters.json"), variable="PRESSURE")


# --- mesh_remesh with a fake worker ----------------------------------------------

class FakeWorker:
    """Stands in for bridge.run_op: copies a finer mesh to the output path."""

    def __init__(self, tmp_path, support=True):
        self.calls: list[tuple[str, dict]] = []
        self.support = support
        self.finer = mdpa.create_rectangle_mesh(1.0, 1.0, 14, 14, element_name="Element2D3N",
                                                condition_name="LineCondition2D2N", triangles=True)

    def __call__(self, op, args=None, timeout=120.0, use_cache=True):
        self.calls.append((op, args or {}))
        if op == "meshing_support":
            return {"meshing": self.support, "mmg": self.support}
        assert op == "remesh_mdpa" and use_cache is False
        self.finer.write(args["output"])
        fields = {name: {str(nid): float(nid) for nid in self.finer.nodes}
                  for name in args["request"].get("write_fields", [])}
        return {"written_to": args["output"], "fields": fields,
                "transfer": {v["variable"]: {"max_abs_error": 0.0} for v in args["request"]["check"]}}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def remesh_tool():
    return _tools(mesh_tools)["mesh_remesh"]


def test_mesh_remesh_with_fake_worker(tmp_path, monkeypatch, remesh_tool):
    worker = FakeWorker(tmp_path)
    monkeypatch.setattr(bridge, "run_op", worker)
    out = tmp_path / "out.mdpa"
    result = _run(remesh_tool(str(EXAMPLE / "plate.mdpa"), str(out), strategy="uniform",
                              min_size=0.07, passes=2,
                              check_fields=[{"variable": "TEMPERATURE", "expression": "x"}]))
    assert "error" not in result, result
    assert result["written_to"] == str(out) and out.is_file()
    assert [p["elements"] for p in result["passes"]] == [392, 392]
    assert result["change"]["elements"] == 192 and result["transfer"]["TEMPERATURE"]["max_abs_error"] == 0.0
    assert all(item["ok"] for item in result["preserved"].values()) and result["issues"] == []
    ops = [c[0] for c in worker.calls]
    assert ops == ["meshing_support", "remesh_mdpa", "remesh_mdpa"]
    second = worker.calls[2][1]
    assert second["input"] != str(EXAMPLE / "plate.mdpa") and second["output"] == str(out)


def test_mesh_remesh_input_errors(tmp_path, monkeypatch, remesh_tool):
    worker = FakeWorker(tmp_path)
    monkeypatch.setattr(bridge, "run_op", worker)
    src = str(EXAMPLE / "plate.mdpa")
    out = tmp_path / "out.mdpa"
    assert "does not exist" in _run(remesh_tool(str(tmp_path / "no.mdpa"), str(out)))["error"]
    assert "differ" in _run(remesh_tool(src, src))["error"]
    assert "strategy" in _run(remesh_tool(src, str(out), strategy="x"))["error"]
    assert "passes" in _run(remesh_tool(src, str(out), strategy="uniform", min_size=0.1, passes=0))["error"]
    out.write_text("keep")
    assert "overwrite" in _run(remesh_tool(src, str(out), strategy="uniform", min_size=0.1))["error"]
    assert out.read_text() == "keep"
    broken = tmp_path / "broken.mdpa"
    bad = mdpa.read(src)
    bad.elements["Element2D3N"][999] = (1, [1, 2, 9999])      # dangling node reference
    bad.write(broken)
    assert "invalid" in _run(remesh_tool(str(broken), str(tmp_path / "o2.mdpa"), strategy="uniform",
                                         min_size=0.1))["error"]
    assert worker.calls == []                                           # nothing reached Kratos


def test_mesh_remesh_without_mmg(tmp_path, monkeypatch, remesh_tool):
    monkeypatch.setattr(bridge, "run_op", FakeWorker(tmp_path, support=False))
    result = _run(remesh_tool(str(EXAMPLE / "plate.mdpa"), str(tmp_path / "o.mdpa"),
                              strategy="uniform", min_size=0.1))
    assert "not available" in result["error"] and result["meshing_support"]["mmg"] is False
    assert not (tmp_path / "o.mdpa").exists()


def test_mesh_remesh_reports_worker_failure(tmp_path, monkeypatch, remesh_tool):
    def failing(op, *a, **k):
        if op == "meshing_support":
            return {"mmg": True}
        raise bridge.BridgeError("Worker op 'remesh_mdpa' failed: boom", stderr="trace")

    monkeypatch.setattr(bridge, "run_op", failing)
    result = _run(remesh_tool(str(EXAMPLE / "plate.mdpa"), str(tmp_path / "o.mdpa"),
                              strategy="uniform", min_size=0.1))
    assert "boom" in result["error"] and "trace" in result["error"]


# --- solved fields: NodalData, request, result mapping, tool ----------------------------

def _plate():
    return mdpa.read(EXAMPLE / "plate.mdpa")


def _write_vtk(tmp_path, plate, name="result_1.vtk", order=None, variable="TEMPERATURE"):
    import meshio
    import numpy as np

    ids = sorted(plate.nodes)
    order = order if order is not None else list(range(len(ids)))
    points = np.array([plate.nodes[ids[i]] for i in order])
    values = np.array([float(ids[i]) * 0.5 for i in order])
    cells = [("triangle", np.array([[order.index(ids.index(n)) for n in conn]
                                    for _, conn in plate_elements(plate)]))]
    path = tmp_path / name
    meshio.write(path, meshio.Mesh(points, cells, point_data={variable: values}), file_format="vtk",
                 binary=False)
    return path


def plate_elements(plate):
    return [row for by_id in plate.elements.values() for row in by_id.values()]


def test_nodal_data_roundtrip(tmp_path):
    m = mdpa.create_rectangle_mesh(1.0, 1.0, 2, 2)
    m.nodal_data["TEMPERATURE"] = {nid: 0.1 * nid for nid in m.nodes}
    path = m.write(tmp_path / "m.mdpa")
    assert "Begin NodalData TEMPERATURE" in path.read_text()
    back = mdpa.read(path)
    assert back.nodal_data["TEMPERATURE"] == m.nodal_data["TEMPERATURE"]
    assert back.validate() == [] and back.inspect()["num_nodes"] == len(m.nodes)


def test_nodal_data_vector_block_is_skipped():
    text = ("Begin Nodes\n1 0 0 0\nEnd Nodes\n"
            "Begin NodalData VELOCITY\n1 0 1.0 2.0 3.0\nEnd NodalData\n"
            "Begin NodalData DISTANCE\n1 0 0.5\nEnd NodalData\n")
    m = mdpa.parse(text)
    assert m.nodal_data == {"DISTANCE": {1: 0.5}}


def test_setup_example_mesh_keeps_nodal_data():
    m = mdpa.read(SETUP_CASE / "mesh.mdpa")
    assert set(m.nodal_data["DISTANCE"]) == set(m.nodes)


def test_build_request_with_field_values():
    plate = _plate()
    values = {nid: 1.0 + nid for nid in plate.nodes}
    request = remesh.build_request(plate, strategy="hessian", field_variable="TEMPERATURE",
                                   field_values=values)
    item = request["assign"][0]
    assert item["variable"] == "TEMPERATURE" and item["values"][str(min(plate.nodes))] > 1.0
    assert request["write_fields"] == ["TEMPERATURE"]
    with pytest.raises(ValueError, match="exactly one"):
        remesh.build_request(plate, strategy="hessian", field_variable="T", field_values=values,
                             field_expression="x")
    with pytest.raises(ValueError, match="exactly one"):
        remesh.build_request(plate, strategy="hessian", field_variable="T")
    with pytest.raises(ValueError, match="every node"):
        remesh.build_request(plate, strategy="hessian", field_variable="T",
                             field_values={1: 0.0})
    bad = {**values, min(values): float("nan")}
    with pytest.raises(ValueError, match="non-finite"):
        remesh.build_request(plate, strategy="hessian", field_variable="T", field_values=bad)


def test_carry_fields_reads_back_from_mesh():
    plate = _plate()
    request = remesh.build_request(plate, strategy="hessian", field_variable="T",
                                   field_values={n: 0.0 for n in plate.nodes})
    later = remesh.carry_fields(request)
    assert later["assign"] == [{"variable": "T", "source": "mesh"}]
    assert later["read_variables"] == ["T"] and request["assign"][0].get("values")


def test_field_from_result_maps_shuffled_points(tmp_path):
    plate = _plate()
    ids = sorted(plate.nodes)
    order = list(reversed(range(len(ids))))
    path = _write_vtk(tmp_path, plate, order=order)
    picked = remesh.field_from_result(plate, str(path), "TEMPERATURE")
    assert picked["values"] == {nid: nid * 0.5 for nid in ids}


def test_field_from_result_index_selects_time_and_errors(tmp_path):
    plate = _plate()
    _write_vtk(tmp_path, plate, "a_1.vtk")
    _write_vtk(tmp_path, plate, "a_2.vtk")
    index = tmp_path / "result-index.json"
    index.write_text(json.dumps({"version": 1, "records": [
        {"file": "a_1.vtk", "series": "s", "time": 0.5, "step": 1},
        {"file": "a_2.vtk", "series": "s", "time": 1.0, "step": 2}]}))
    assert remesh.field_from_result(plate, str(index), "TEMPERATURE")["time"] == 1.0
    assert remesh.field_from_result(plate, str(index), "TEMPERATURE", time=0.5)["step"] == 1
    with pytest.raises(ValueError, match="no result record"):
        remesh.field_from_result(plate, str(index), "TEMPERATURE", step=9)
    with pytest.raises(ValueError, match="Missing point field"):
        remesh.field_from_result(plate, str(index), "PRESSURE")
    with pytest.raises(ValueError, match="only apply"):
        remesh.field_from_result(plate, str(tmp_path / "a_1.vtk"), "TEMPERATURE", step=1)
    moved = _plate()
    moved.nodes[min(moved.nodes)] = (9.0, 9.0, 0.0)
    with pytest.raises(ValueError, match="unique mesh node"):
        remesh.field_from_result(moved, str(tmp_path / "a_1.vtk"), "TEMPERATURE")


def test_mesh_remesh_from_solved_field_with_fake_worker(tmp_path, monkeypatch, remesh_tool):
    plate = _plate()
    vtk = _write_vtk(tmp_path, plate)
    worker = FakeWorker(tmp_path)
    monkeypatch.setattr(bridge, "run_op", worker)
    out = tmp_path / "out.mdpa"
    result = _run(remesh_tool(str(EXAMPLE / "plate.mdpa"), str(out), strategy="hessian",
                              field_variable="TEMPERATURE", field_results=str(vtk), passes=2))
    assert "error" not in result, result
    assert result["fields_written"] == ["TEMPERATURE"]
    first, second = worker.calls[1][1]["request"], worker.calls[2][1]["request"]
    assert "values" in first["assign"][0]
    assert second["assign"] == [{"variable": "TEMPERATURE", "source": "mesh"}]
    assert second["read_variables"] == ["TEMPERATURE"]
    written = mdpa.read(out)
    assert set(written.nodal_data["TEMPERATURE"]) == set(worker.finer.nodes)
    # Both an expression and a result file, or a result file without a variable, are rejected.
    both = _run(remesh_tool(str(EXAMPLE / "plate.mdpa"), str(tmp_path / "x.mdpa"),
                            strategy="hessian", field_variable="T", field_expression="x",
                            field_results=str(vtk)))
    assert "not both" in both["error"]
    novar = _run(remesh_tool(str(EXAMPLE / "plate.mdpa"), str(tmp_path / "y.mdpa"),
                             strategy="hessian", field_results=str(vtk)))
    assert "field_variable" in novar["error"]
    bad = _run(remesh_tool(str(EXAMPLE / "plate.mdpa"), str(tmp_path / "z.mdpa"),
                           strategy="hessian", field_variable="PRESSURE", field_results=str(vtk)))
    assert "Missing point field" in bad["error"]


# --- example resource --------------------------------------------------------------

def test_example_files_and_resource():
    assert mdpa.read(EXAMPLE / "plate.mdpa").validate() == []
    request = json.loads((EXAMPLE / "request.json").read_text())
    assert request["strategy"] == "hessian" and request["passes"] == 3
    setup = mdpa.read(SETUP_CASE / "mesh.mdpa")
    assert setup.validate() == [] and len(setup.nodes) == 231
    assert "Begin NodalData DISTANCE" in (SETUP_CASE / "mesh.mdpa").read_text()
    captured = {}

    class FakeMCP:
        def resource(self, uri):
            def deco(fn):
                captured[uri] = fn
                return fn
            return deco

        def tool(self, *a, **k):
            return lambda fn: fn

        def prompt(self, *a, **k):
            return lambda fn: fn

    resources_mod.register(FakeMCP())
    text = captured["kratos://examples/adaptive-remeshing"]()
    assert "mesh_remesh" in text and "add_remeshing_process" in text and "724" in text
    block = text.split("## setup_remesh/ProjectParameters.json\n\n```json\n", 1)[1].split("\n```", 1)[0]
    assert remesh.has_remeshing(json.loads(block))


# --- real MMG (kratos-marked) -----------------------------------------------------------

def _mmg_or_skip():
    try:
        support = bridge.run_op("meshing_support", use_cache=False)
    except bridge.BridgeError as exc:
        pytest.skip(f"meshing support not queryable: {exc}")
    if not support.get("mmg"):
        pytest.skip("MeshingApplication/MMG not available in this Kratos build")


def _remesh(src, dst, **kwargs):
    return _run(_tools(mesh_tools)["mesh_remesh"](str(src), str(dst), **kwargs))


@pytest.mark.kratos
def test_local_refinement_preserves_boundaries_and_transfers_fields(tmp_path):
    _mmg_or_skip()
    out = tmp_path / "local.mdpa"
    result = _remesh(EXAMPLE / "plate.mdpa", out,
                     region={"type": "sphere", "center": [0.5, 0.5], "radius": 0.15},
                     min_size=0.02, max_size=0.1, transition_distance=0.25,
                     check_fields=[{"variable": "TEMPERATURE", "expression": "x + 2*y"}])
    assert "error" not in result, result
    assert result["issues"] == []
    assert result["change"]["elements"] > 100
    assert all(item["ok"] for item in result["preserved"].values()), result["preserved"]
    # Linear fields are reproduced to round-off by MMG's interpolation.
    assert result["transfer"]["TEMPERATURE"]["max_abs_error"] < 1e-9
    # Localized: elements near the region are much smaller than the ones in the far corner.
    mesh = mdpa.read(out)
    near, far = [], []
    for by_id in mesh.elements.values():
        for _, conn in by_id.values():
            pts = [mesh.nodes[n] for n in conn]
            cx = sum(p[0] for p in pts) / 3
            cy = sum(p[1] for p in pts) / 3
            size = sum(math.dist(a, b) for i, a in enumerate(pts) for b in pts[i + 1:]) / 3
            (near if math.hypot(cx - 0.5, cy - 0.5) < 0.2 else far if math.hypot(cx - 0.5, cy - 0.5) > 0.6
             else []).append(size)
    assert sum(near) / len(near) < 0.5 * sum(far) / len(far)


@pytest.mark.kratos
def test_freeze_keeps_boundary_nodes(tmp_path):
    _mmg_or_skip()
    free = _remesh(EXAMPLE / "plate.mdpa", tmp_path / "free.mdpa", strategy="uniform", min_size=0.05)
    frozen = _remesh(EXAMPLE / "plate.mdpa", tmp_path / "frozen.mdpa", strategy="uniform",
                     min_size=0.05, freeze_sub_model_parts=["left", "right", "top", "bottom"])
    assert frozen["after"]["sub_model_parts"]["left"]["nodes"] == 11      # untouched
    assert free["after"]["sub_model_parts"]["left"]["nodes"] > 11          # boundary refined


@pytest.mark.kratos
def test_hessian_adapted_mesh_beats_uniform_at_equal_element_count(tmp_path):
    import numpy as np

    _mmg_or_skip()

    def field(x, y):
        return np.tanh(30 * (x - 0.5))

    def interpolation_error(path):
        mesh = mdpa.read(path)
        ids = sorted(mesh.nodes)
        index = {nid: k for k, nid in enumerate(ids)}
        coords = np.array([mesh.nodes[nid][:2] for nid in ids])
        values = field(coords[:, 0], coords[:, 1])
        grid = np.linspace(0.0, 1.0, 101)
        gx, gy = np.meshgrid(grid, grid)
        points = np.stack([gx.ravel(), gy.ravel()], axis=1)
        approx = np.full(len(points), np.nan)
        for by_id in mesh.elements.values():
            for _, conn in by_id.values():
                a, b, c = (coords[index[n]] for n in conn)
                matrix = np.array([b - a, c - a]).T
                if abs(np.linalg.det(matrix)) < 1e-14:
                    continue
                lam = np.linalg.solve(matrix, (points - a).T).T
                weights = np.column_stack([1 - lam.sum(axis=1), lam])
                inside = (weights >= -1e-9).all(axis=1)
                approx[inside] = (weights @ values[[index[n] for n in conn]])[inside]
        assert not np.isnan(approx).any()                      # the mesh covers the square
        return float(np.abs(approx - field(points[:, 0], points[:, 1])).max())

    base = interpolation_error(EXAMPLE / "plate.mdpa")
    adapted = _remesh(EXAMPLE / "plate.mdpa", tmp_path / "adapted.mdpa", strategy="hessian",
                      field_variable="TEMPERATURE", field_expression="tanh(30*(x-0.5))",
                      min_size=0.005, max_size=0.25, passes=3)
    assert "error" not in adapted, adapted
    count = adapted["after"]["num_elements"]
    assert [p["elements"] for p in adapted["passes"]] == sorted(p["elements"] for p in adapted["passes"])
    assert all(item["ok"] for item in adapted["preserved"].values())
    # Uniform mesh with (about) the same number of elements: the uniform size that gives it
    # is found by a short search, because MMG's element count is not exactly controllable.
    best = None
    for size in (0.07, 0.065, 0.06, 0.055):
        uniform = _remesh(EXAMPLE / "plate.mdpa", tmp_path / "uniform.mdpa", strategy="uniform",
                          min_size=size, overwrite=True)
        n = uniform["after"]["num_elements"]
        if n >= count and (best is None or n < best[0]):
            best = (n, interpolation_error(tmp_path / "uniform.mdpa"))
    assert best is not None and best[0] < 1.6 * count
    assert interpolation_error(tmp_path / "adapted.mdpa") < 0.75 * best[1]
    assert interpolation_error(tmp_path / "adapted.mdpa") < 0.5 * base


@pytest.mark.kratos
def test_setup_remeshing_runs_inside_a_simulation(tmp_path, monkeypatch):
    import meshio

    _mmg_or_skip()
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "home"))
    case = tmp_path / "case"
    shutil.copytree(SETUP_CASE, case)
    assert scaffold.validate_case_files(case, deep=True)["valid"]
    meta = jobs.start(str(case))
    deadline = time.time() + 180
    status = jobs.status(meta.job_id)
    while time.time() < deadline and status["state"] not in jobs.TERMINAL_STATES:
        time.sleep(1.0)
        status = jobs.status(meta.job_id)
    assert status["state"] == "succeeded", jobs.logs(meta.job_id, tail=40)
    last = sorted((case / "vtk_output").glob("*.vtk"))[-1]
    out = meshio.read(last)
    assert len(out.points) > 3 * 231                          # refined around the disk
    assert out.point_data["TEMPERATURE"].max() == pytest.approx(100.0)   # Dirichlet side kept


@pytest.mark.kratos
def test_remesh_from_a_solved_field(tmp_path, monkeypatch):
    """Run a real thermal job (ASCII VTK), remesh its mesh from the solved TEMPERATURE and
    check the transferred field is written back as NodalData."""
    _mmg_or_skip()
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "home"))
    case = tmp_path / "case"
    shutil.copytree(SETUP_CASE, case)
    params = json.loads((case / "ProjectParameters.json").read_text())
    params["processes"]["list_other_processes"] = []          # solve on the original mesh
    (case / "ProjectParameters.json").write_text(json.dumps(params, indent=1))
    meta = jobs.start(str(case))
    deadline = time.time() + 180
    status = jobs.status(meta.job_id)
    while time.time() < deadline and status["state"] not in jobs.TERMINAL_STATES:
        time.sleep(1.0)
        status = jobs.status(meta.job_id)
    assert status["state"] == "succeeded", jobs.logs(meta.job_id, tail=40)

    source = mdpa.read(case / "mesh.mdpa")
    picked = remesh.field_from_result(source, str(case / "result-index.json"), "TEMPERATURE")
    assert len(picked["values"]) == len(source.nodes) and picked["time"] == pytest.approx(0.3)

    out = tmp_path / "adapted.mdpa"
    result = _remesh(case / "mesh.mdpa", out, strategy="hessian", field_variable="TEMPERATURE",
                     field_results=str(case / "result-index.json"), min_size=0.01, max_size=0.1,
                     passes=2)
    assert "error" not in result, result
    assert result["issues"] == [] and all(i["ok"] for i in result["preserved"].values())
    assert result["change"]["elements"] > 400                        # refined around the front
    assert result["fields_written"] == ["DISTANCE", "TEMPERATURE"]   # input NodalData is kept
    adapted = mdpa.read(out)
    assert set(adapted.nodal_data["TEMPERATURE"]) == set(adapted.nodes)
    # The Dirichlet side keeps its 100 degrees on the new mesh.
    left = adapted.sub_model_parts["left"].nodes
    assert all(adapted.nodal_data["TEMPERATURE"][n] == pytest.approx(100.0, abs=1e-6) for n in left)
    transfer = result["transfer"]["TEMPERATURE"]
    assert transfer["source"] == "field" and transfer["value_range_after"][1] == pytest.approx(100.0)


@pytest.mark.kratos
def test_remesh_case_rejected_by_sessions(tmp_path):
    from kratos_mcp import sessions

    case = tmp_path / "case"
    shutil.copytree(SETUP_CASE, case)
    with pytest.raises(sessions.SessionError, match="mmg_process"):
        sessions.start(str(case), timeout=120)


# --- mesh kind detection and simplex validation ------------------------------------

def _tet_mesh():
    from kratos_mcp import mesh_edit

    m = mdpa.create_box_mesh(1, 1, 1, 2, 2, 2)
    mesh_edit.convert_cells(m, "hex_to_tet")
    return m


def test_mesh_kind_detection():
    plate = mdpa.read(EXAMPLE / "plate.mdpa")
    assert remesh.mesh_kind(plate) == "planar2d"
    assert remesh.mesh_kind(_tet_mesh()) == "volume3d"
    tri = mdpa.create_rectangle_mesh(1.0, 1.0, 2, 2, element_name="Element2D3N",
                                     condition_name="LineCondition2D2N", triangles=True)
    assert remesh.mesh_kind(tri) == "planar2d"
    for nid, (x, y, _) in list(tri.nodes.items()):
        tri.nodes[nid] = (x, y, 0.3 * x)                   # tilted: spans z
    assert remesh.mesh_kind(tri) == "surface3d"
    assert remesh.build_request(tri, strategy="uniform", min_size=0.2)["mesh_kind"] == "surface3d"
    assert remesh.build_request(_tet_mesh(), strategy="uniform", min_size=0.2)["domain_size"] == 3


def test_non_simplex_and_mixed_meshes_are_rejected():
    quad = mdpa.create_rectangle_mesh(1.0, 1.0, 2, 2)
    with pytest.raises(ValueError, match="linear triangle or tetrahedron"):
        remesh.build_request(quad, strategy="uniform", min_size=0.2)
    hexes = mdpa.create_box_mesh(1, 1, 1, 2, 2, 2)
    with pytest.raises(ValueError, match="mdpa_convert_cells"):
        remesh.build_request(hexes, strategy="uniform", min_size=0.2)
    tri6 = mdpa.read(EXAMPLE / "plate.mdpa")
    tri6.elements["Element2D6N"] = {99: (1, [1, 2, 3, 4, 5, 6])}
    with pytest.raises(ValueError, match="6 nodes"):
        remesh.build_request(tri6, strategy="uniform", min_size=0.2)
    mixed = _tet_mesh()
    mixed.nodes.update({9001: (0.0, 0.0, 0.0), 9002: (1.0, 0.0, 0.0), 9003: (0.0, 1.0, 0.0)})
    mixed.elements["Element2D3N"] = {9999: (1, [9001, 9002, 9003])}
    with pytest.raises(ValueError, match="mixes triangles and tetrahedra"):
        remesh.build_request(mixed, strategy="uniform", min_size=0.2)


def test_mesh_remesh_rejects_quad_mesh_before_reaching_kratos(tmp_path, monkeypatch, remesh_tool):
    worker = FakeWorker(tmp_path)
    monkeypatch.setattr(bridge, "run_op", worker)
    src = tmp_path / "quad.mdpa"
    mdpa.create_rectangle_mesh(1.0, 1.0, 2, 2).write(src)
    out = _run(remesh_tool(str(src), str(tmp_path / "o.mdpa"), strategy="uniform", min_size=0.2))
    assert "linear triangle or tetrahedron" in out["error"]
    assert worker.calls == []


def test_mesh_remesh_reports_kind_and_checks_the_matching_mmg_variant(tmp_path, monkeypatch,
                                                                       remesh_tool):
    tets = tmp_path / "tets.mdpa"
    _tet_mesh().write(tets)

    class Volume(FakeWorker):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.finer = _tet_mesh()
            self.variants = {"mmg": True, "mmg2d": True, "mmg3d": True, "mmg3d_surfaces": False}

        def __call__(self, op, args=None, timeout=120.0, use_cache=True):
            if op == "meshing_support":
                self.calls.append((op, {}))
                return {"meshing": True, **self.variants}
            return super().__call__(op, args, timeout, use_cache)

    worker = Volume(tmp_path)
    monkeypatch.setattr(bridge, "run_op", worker)
    out = _run(remesh_tool(str(tets), str(tmp_path / "o.mdpa"), strategy="uniform", min_size=0.3))
    assert "error" not in out, out
    assert out["mesh_kind"] == "volume3d" and "not been verified" in out["warnings"][0]
    # a surface mesh needs MmgProcess3DSurfaces, which this build lacks
    surf = mdpa.create_rectangle_mesh(1.0, 1.0, 2, 2, element_name="Element2D3N",
                                      condition_name="LineCondition2D2N", triangles=True)
    for nid, (x, y, _) in list(surf.nodes.items()):
        surf.nodes[nid] = (x, y, 0.3 * x)
    src = tmp_path / "surf.mdpa"
    surf.write(src)
    out = _run(remesh_tool(str(src), str(tmp_path / "o2.mdpa"), strategy="uniform", min_size=0.3))
    assert "MMG remeshing is not available" in out["error"]
    assert out["meshing_support"]["mmg3d_surfaces"] is False
    # the 2D plate is unaffected and carries no warning
    worker2 = FakeWorker(tmp_path)
    monkeypatch.setattr(bridge, "run_op", worker2)
    ok = _run(remesh_tool(str(EXAMPLE / "plate.mdpa"), str(tmp_path / "o3.mdpa"),
                          strategy="uniform", min_size=0.07))
    assert ok["mesh_kind"] == "planar2d" and "warnings" not in ok


@pytest.mark.kratos
def test_3d_tetrahedral_remesh_smoke(tmp_path):
    """First real-MMG evidence for the 3D path (UNRUN in CI so far: needs MmgProcess3D).
    Asserts only structure - the reference-number probe in the roadmap is still open."""
    _mmg_or_skip()
    src = tmp_path / "tets.mdpa"
    _tet_mesh().write(src)
    result = _remesh(src, tmp_path / "out.mdpa", strategy="uniform", min_size=0.25)
    if "error" in result and "not available" in result["error"]:
        pytest.skip(result["error"])
    assert "error" not in result, result
    assert result["mesh_kind"] == "volume3d" and result["issues"] == []
    assert result["after"]["num_elements"] > 0

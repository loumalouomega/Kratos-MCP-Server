"""End-to-end: cases assembled ONLY from the mesh/geometry/project editing tools,
run with a real Kratos build, physics checked against closed-form solutions."""

from __future__ import annotations

import json
import math
import time

import meshio
import numpy as np
import pytest

from kratos_mcp import jobs, mdpa
from kratos_mcp.tools import scaffold
from test_edit_tools import FakeMCP, T

pytestmark = pytest.mark.kratos


@pytest.fixture(autouse=True)
def isolated_jobs_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRATOS_MCP_HOME", str(tmp_path / "state"))


@pytest.fixture(scope="module")
def S():
    fake = FakeMCP()
    scaffold.register(fake)
    return fake.tools


def run_case(case, timeout=240.0):
    meta = jobs.start(str(case))
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = jobs.status(meta.job_id)
        if status["state"] in jobs.TERMINAL_STATES:
            assert status["state"] == "succeeded", jobs.logs(meta.job_id, tail=40)
            return meta.job_id
        time.sleep(0.5)
    jobs.cancel(meta.job_id)
    pytest.fail("job timed out")


def last_vtk(case):
    files = sorted((case / "vtk_output").glob("*.vtk"))
    assert files, "no VTK output"
    return meshio.read(files[-1])


STRUCT_APPS = ["StructuralMechanicsApplication"]


def test_cantilever_from_plain_mesh(tmp_path, S):
    """Mesh without conditions -> selector parts + extracted boundary conditions."""
    case = tmp_path / "beam"
    case.mkdir()
    mesh_path = str(case / "mesh.mdpa")
    mdpa.create_rectangle_mesh(1.0, 0.2, 20, 4, condition_name=None).write(mesh_path)

    assert T.mdpa_quality(mesh_path)["ok"]
    out = T.mdpa_create_sub_model_part(
        mesh_path, "fixed", selector={"plane": {"point": [0, 0, 0], "normal": [1, 0, 0]}})
    assert out["valid"] and out["result"]["nodes"] == 5
    out = T.mdpa_extract_boundary(
        mesh_path, "LineLoadCondition2D2N", "tip",
        selector={"plane": {"point": [1, 0, 0], "normal": [1, 0, 0]}})
    assert out["result"]["conditions_created"] == 4 and out["valid"]

    S["create_project"](str(case), "structural_static", overrides={"fix_model_part": "Structure.fixed"})
    # create_project wrote its own demo mesh? keep ours
    mdpa.create_rectangle_mesh(1.0, 0.2, 20, 4, condition_name=None).write(mesh_path)
    T.mdpa_create_sub_model_part(
        mesh_path, "fixed", selector={"plane": {"point": [0, 0, 0], "normal": [1, 0, 0]}})
    T.mdpa_extract_boundary(mesh_path, "LineLoadCondition2D2N", "tip",
                            selector={"plane": {"point": [1, 0, 0], "normal": [1, 0, 0]}})
    pf = str(case / "ProjectParameters.json")
    S["add_boundary_condition"](pf, "line_load", "Structure.tip", modulus=1.0e6,
                                direction=[0.0, -1.0, 0.0])
    check = T.materials_check(pf, applications=STRUCT_APPS)
    assert check["valid"], check
    assert scaffold.validate_case_files(case)["valid"]

    run_case(case)
    mesh = last_vtk(case)
    disp = np.asarray(mesh.point_data["DISPLACEMENT"])
    tip = disp[np.argmax(np.asarray(mesh.points)[:, 0])]
    expected = 2.0e5 * 1.0 ** 3 / (3 * 2.1e11 * (0.2 ** 3 / 12))
    assert tip[1] < 0 and abs(abs(tip[1]) - expected) / expected < 0.2, (tip, expected)


def test_thick_cylinder_matches_lame(tmp_path, S):
    """Quarter ring under internal pressure: exercises the annulus generator,
    its boundary orientation, generic fix_scalar BCs and project editing."""
    a, b, p, E, nu = 1.0, 2.0, 1.0e6, 2.1e11, 0.3
    case = tmp_path / "ring"
    S["create_project"](str(case), "structural_static")
    mesh_path = str(case / "mesh.mdpa")
    out = T.mdpa_create_geometry_mesh(
        mesh_path, "annulus", {"r_inner": a, "r_outer": b, "n_radial": 8, "n_theta": 16,
                               "angle": 90.0}, overwrite=True)
    assert out["valid"]
    assert T.mdpa_quality(mesh_path)["ok"]
    pf = str(case / "ProjectParameters.json")
    T.project_set_setting(pf, "/processes/constraints_process_list", [])
    S["add_boundary_condition"](pf, "fix_scalar", "Structure.start", value=0.0,
                                variable="DISPLACEMENT_Y")
    S["add_boundary_condition"](pf, "fix_scalar", "Structure.end", value=0.0,
                                variable="DISPLACEMENT_X")
    # Kratos: a positive POSITIVE_FACE_PRESSURE acts along the outward normal of conditions
    # oriented with the domain on their left (verified on a plate), i.e. as tension; a
    # compressive internal pressure therefore needs the negative value.
    S["add_boundary_condition"](pf, "pressure_load", "Structure.inner", value=-p)
    T.materials_set_property(str(case / "Materials.json"), "Structure.domain", variables={
        "YOUNG_MODULUS": E, "POISSON_RATIO": nu})
    assert T.materials_check(pf, applications=STRUCT_APPS)["valid"]

    run_case(case)
    mesh = last_vtk(case)
    pts = np.asarray(mesh.points)
    disp = np.asarray(mesh.point_data["DISPLACEMENT"])
    radius = np.hypot(pts[:, 0], pts[:, 1])
    ur = (disp[:, 0] * pts[:, 0] + disp[:, 1] * pts[:, 1]) / radius
    A = p * a ** 2 / (b ** 2 - a ** 2)
    B = p * a ** 2 * b ** 2 / (b ** 2 - a ** 2)

    def lame(r):
        return (1 + nu) / E * ((1 - 2 * nu) * A * r + B / r)

    for r_probe in (a, b):
        sel = np.isclose(radius, r_probe)
        assert sel.any()
        assert np.allclose(ur[sel], lame(r_probe), rtol=0.03), (r_probe, ur[sel].mean(), lame(r_probe))


def test_extruded_hex_and_tet_beams(tmp_path, S):
    """2D plate -> extrude -> (optionally) hex_to_tet, boundary + parts via tools.
    Both discretizations must give a tip deflection near beam theory."""
    L, h, t = 1.0, 0.2, 0.2
    expected = 2.0e5 * L ** 3 / (3 * 2.1e11 * (t * h ** 3 / 12))  # P = 2e5 N
    base = tmp_path / "base.mdpa"
    mdpa.create_rectangle_mesh(L, h, 10, 2, condition_name=None).write(base)
    for variant in ("hex", "tet"):
        case = tmp_path / variant
        S["create_project"](str(case), "structural_static",
                            overrides={"domain_size": 3, "fix_model_part": "Structure.fixed",
                                       "constitutive_law": "LinearElastic3DLaw"})
        mesh_path = str(case / "mesh.mdpa")
        out = T.mdpa_extrude(str(base), mesh_path, [t / 2, t / 2], overwrite=True)
        assert out["valid"], out
        if variant == "tet":
            out = T.mdpa_convert_cells(mesh_path, "hex_to_tet")
            assert out["valid"]
            elem = "SmallDisplacementElement3D4N"
        else:
            elem = "SmallDisplacementElement3D8N"
        T.mdpa_create_sub_model_part(
            mesh_path, "fixed", selector={"plane": {"point": [0, 0, 0], "normal": [1, 0, 0]}})
        out = T.mdpa_extract_boundary(
            mesh_path, {"3": "SurfaceLoadCondition3D3N", "4": "SurfaceLoadCondition3D4N"}, "tip",
            selector={"plane": {"point": [L, 0, 0], "normal": [1, 0, 0]}})
        assert out["valid"] and out["result"]["conditions_created"] > 0
        assert T.mdpa_quality(mesh_path)["ok"]
        pf = str(case / "ProjectParameters.json")
        T.project_update_solver(pf, linear_solver_preset="sparse_lu")
        # surface_load is a traction [N/m^2]: total force P = q * (h * t)
        traction = 2.0e5 / (h * t)
        S["add_boundary_condition"](pf, "surface_load", "Structure.tip", modulus=traction,
                                    direction=[0.0, -1.0, 0.0])
        assert scaffold.validate_case_files(case)["valid"]
        run_case(case)
        mesh = last_vtk(case)
        pts = np.asarray(mesh.points)
        disp = np.asarray(mesh.point_data["DISPLACEMENT"])
        tip = disp[pts[:, 0] > L - 1e-6]
        deflection = -tip[:, 1].mean()
        assert deflection > 0 and not np.isnan(deflection)
        # measured on this coarse mesh: hex 0.88 x beam theory, linear tets lock (0.52 x)
        low, high = (0.8, 1.05) if variant == "hex" else (0.4, 0.8)
        assert low * expected < deflection < high * expected, (variant, deflection, expected)

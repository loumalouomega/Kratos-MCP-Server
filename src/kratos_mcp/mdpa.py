"""Pure-Python reader/writer/generators for Kratos .mdpa mesh files.

No Kratos required: this lets inspection, validation and mesh generation
work even without a compiled build (deep validation through the real
Kratos ModelPartIO is available via the bridge).

The mdpa format is line/block based:

    Begin Nodes
      <id> <x> <y> <z>
    End Nodes
    Begin Elements <ElementTypeName>
      <id> <property_id> <node ids...>
    End Elements
    Begin Conditions <ConditionTypeName> ...
    Begin SubModelPart <name>
      Begin SubModelPartNodes / SubModelPartElements / SubModelPartConditions
      Begin SubModelPart <nested> ...
    End SubModelPart
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class SubModelPart:
    name: str
    nodes: list[int] = field(default_factory=list)
    elements: list[int] = field(default_factory=list)
    conditions: list[int] = field(default_factory=list)
    children: dict[str, "SubModelPart"] = field(default_factory=dict)

    def tree(self) -> dict[str, Any]:
        return {
            "nodes": len(self.nodes),
            "elements": len(self.elements),
            "conditions": len(self.conditions),
            "sub_model_parts": {c.name: c.tree() for c in self.children.values()},
        }


@dataclass
class Mdpa:
    nodes: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    # entity type name -> {id: (property_id, [node ids])}
    elements: dict[str, dict[int, tuple[int, list[int]]]] = field(default_factory=dict)
    conditions: dict[str, dict[int, tuple[int, list[int]]]] = field(default_factory=dict)
    properties: dict[int, dict[str, str]] = field(default_factory=dict)
    sub_model_parts: dict[str, SubModelPart] = field(default_factory=dict)
    model_part_data: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ info

    def element_ids(self) -> set[int]:
        return {eid for by_id in self.elements.values() for eid in by_id}

    def condition_ids(self) -> set[int]:
        return {cid for by_id in self.conditions.values() for cid in by_id}

    def bounding_box(self) -> dict[str, list[float]] | None:
        if not self.nodes:
            return None
        xs, ys, zs = zip(*self.nodes.values())
        return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]}

    def inspect(self) -> dict[str, Any]:
        return {
            "num_nodes": len(self.nodes),
            "num_elements": sum(len(v) for v in self.elements.values()),
            "num_conditions": sum(len(v) for v in self.conditions.values()),
            "elements_by_type": {k: len(v) for k, v in self.elements.items()},
            "conditions_by_type": {k: len(v) for k, v in self.conditions.items()},
            "properties_ids": sorted(
                {pid for v in self.elements.values() for pid, _ in v.values()}
                | {pid for v in self.conditions.values() for pid, _ in v.values()}
                | set(self.properties)
            ),
            "bounding_box": self.bounding_box(),
            "sub_model_parts": {s.name: s.tree() for s in self.sub_model_parts.values()},
        }

    def validate(self) -> list[str]:
        issues: list[str] = []
        node_ids = set(self.nodes)
        for kind, groups in (("Element", self.elements), ("Condition", self.conditions)):
            for type_name, by_id in groups.items():
                for eid, (_, conn) in by_id.items():
                    missing = [n for n in conn if n not in node_ids]
                    if missing:
                        issues.append(f"{kind} {eid} ({type_name}) references missing nodes {missing}")
        elem_ids = self.element_ids()
        cond_ids = self.condition_ids()

        def walk(part: SubModelPart, prefix: str) -> None:
            path = f"{prefix}{part.name}"
            for n in part.nodes:
                if n not in node_ids:
                    issues.append(f"SubModelPart {path} references missing node {n}")
            for e in part.elements:
                if e not in elem_ids:
                    issues.append(f"SubModelPart {path} references missing element {e}")
            for c in part.conditions:
                if c not in cond_ids:
                    issues.append(f"SubModelPart {path} references missing condition {c}")
            if not (part.nodes or part.elements or part.conditions or part.children):
                issues.append(f"SubModelPart {path} is empty")
            for child in part.children.values():
                walk(child, path + ".")

        for part in self.sub_model_parts.values():
            walk(part, "")
        return issues

    # ----------------------------------------------------------------- write

    def dumps(self) -> str:
        out: list[str] = ["Begin ModelPartData"]
        out += [f"    {k} {v}" for k, v in self.model_part_data.items()]
        out.append("End ModelPartData\n")

        prop_ids = sorted(
            set(self.properties)
            | {pid for v in self.elements.values() for pid, _ in v.values()}
            | {pid for v in self.conditions.values() for pid, _ in v.values()}
        ) or [0]
        for pid in prop_ids:
            out.append(f"Begin Properties {pid}")
            out += [f"    {k} {v}" for k, v in self.properties.get(pid, {}).items()]
            out.append("End Properties\n")

        out.append("Begin Nodes")
        for nid in sorted(self.nodes):
            x, y, z = self.nodes[nid]
            out.append(f"    {nid} {x:.10g} {y:.10g} {z:.10g}")
        out.append("End Nodes\n")

        for kind, groups in (("Elements", self.elements), ("Conditions", self.conditions)):
            for type_name in sorted(groups):
                out.append(f"Begin {kind} {type_name}")
                by_id = groups[type_name]
                for eid in sorted(by_id):
                    pid, conn = by_id[eid]
                    out.append(f"    {eid} {pid} " + " ".join(str(n) for n in conn))
                out.append(f"End {kind}\n")

        def emit(part: SubModelPart, indent: str) -> None:
            out.append(f"{indent}Begin SubModelPart {part.name}")
            for block, ids in (("Nodes", part.nodes), ("Elements", part.elements),
                               ("Conditions", part.conditions)):
                out.append(f"{indent}    Begin SubModelPart{block}")
                out.extend(f"{indent}        {i}" for i in ids)
                out.append(f"{indent}    End SubModelPart{block}")
            for child in part.children.values():
                emit(child, indent + "    ")
            out.append(f"{indent}End SubModelPart")

        for part in self.sub_model_parts.values():
            emit(part, "")
            out.append("")
        return "\n".join(out) + "\n"

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        if p.suffix != ".mdpa":
            p = p.with_suffix(".mdpa")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.dumps())
        return p


# ---------------------------------------------------------------------- parse

def _clean_lines(text: str) -> Iterator[list[str]]:
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if line:
            yield line.split()


def parse(text: str) -> Mdpa:
    m = Mdpa()
    tokens = list(_clean_lines(text))
    i = 0
    smp_stack: list[SubModelPart] = []

    def skip_block(start: int, block: str) -> int:
        """Return index just past the matching 'End <block>' (handles nesting)."""
        depth = 1
        j = start
        while j < len(tokens):
            t = tokens[j]
            if t[0] == "Begin" and len(t) > 1 and t[1] == block:
                depth += 1
            elif t[0] == "End" and len(t) > 1 and t[1] == block:
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        raise ValueError(f"Unterminated 'Begin {block}' block")

    while i < len(tokens):
        t = tokens[i]
        if t[0] != "Begin":
            i += 1
            continue
        block = t[1] if len(t) > 1 else ""

        if block == "ModelPartData" and not smp_stack:
            i += 1
            while tokens[i][0] != "End":
                if len(tokens[i]) >= 2:
                    m.model_part_data[tokens[i][0]] = " ".join(tokens[i][1:])
                i += 1
            i += 1
        elif block == "Properties" and not smp_stack:
            pid = int(t[2])
            props: dict[str, str] = {}
            i += 1
            while not (tokens[i][0] == "End" and tokens[i][1] == "Properties"):
                if len(tokens[i]) >= 2:
                    props[tokens[i][0]] = " ".join(tokens[i][1:])
                i += 1
            m.properties[pid] = props
            i += 1
        elif block == "Nodes" and not smp_stack:
            i += 1
            while tokens[i][0] != "End":
                nid, x, y, z = tokens[i][:4]
                m.nodes[int(nid)] = (float(x), float(y), float(z))
                i += 1
            i += 1
        elif block in ("Elements", "Conditions") and not smp_stack:
            type_name = t[2]
            target = m.elements if block == "Elements" else m.conditions
            by_id = target.setdefault(type_name, {})
            i += 1
            while tokens[i][0] != "End":
                row = tokens[i]
                by_id[int(row[0])] = (int(row[1]), [int(n) for n in row[2:]])
                i += 1
            i += 1
        elif block == "SubModelPart":
            part = SubModelPart(name=t[2] if len(t) > 2 else "unnamed")
            if smp_stack:
                smp_stack[-1].children[part.name] = part
            else:
                m.sub_model_parts[part.name] = part
            smp_stack.append(part)
            i += 1
        elif block in ("SubModelPartNodes", "SubModelPartElements", "SubModelPartConditions") and smp_stack:
            attr = {"SubModelPartNodes": "nodes", "SubModelPartElements": "elements",
                    "SubModelPartConditions": "conditions"}[block]
            ids = getattr(smp_stack[-1], attr)
            i += 1
            while tokens[i][0] != "End":
                ids.extend(int(x) for x in tokens[i])
                i += 1
            i += 1
        elif t[0] == "Begin":
            # Unknown block (Tables, Geometries, ...): skip it wholesale.
            i = skip_block(i + 1, block)

        # Pop SubModelPart ends (they arrive as separate 'End SubModelPart' tokens).
        while i < len(tokens) and tokens[i][0] == "End" and len(tokens[i]) > 1 \
                and tokens[i][1] == "SubModelPart":
            if smp_stack:
                smp_stack.pop()
            i += 1
    return m


def read(path: str | Path) -> Mdpa:
    return parse(Path(path).read_text())


# ----------------------------------------------------------- mesh generators

def create_line_mesh(
    length: float,
    num_elements: int,
    element_name: str = "TrussLinearElement2D2N",
    condition_name: str | None = None,
    property_id: int = 1,
    root_part: str = "domain",
) -> Mdpa:
    """1D mesh along the x axis with 'start' and 'end' submodelparts."""
    m = Mdpa()
    n = num_elements + 1
    for i in range(n):
        m.nodes[i + 1] = (length * i / num_elements, 0.0, 0.0)
    elems = m.elements.setdefault(element_name, {})
    for e in range(num_elements):
        elems[e + 1] = (property_id, [e + 1, e + 2])

    domain = SubModelPart(root_part, nodes=list(m.nodes), elements=list(elems))
    m.sub_model_parts[root_part] = domain
    m.sub_model_parts["start"] = SubModelPart("start", nodes=[1])
    m.sub_model_parts["end"] = SubModelPart("end", nodes=[n])

    if condition_name:
        conds = m.conditions.setdefault(condition_name, {})
        conds[1] = (property_id, [n])
        m.sub_model_parts["end"].conditions.append(1)
    return m


def create_rectangle_mesh(
    width: float,
    height: float,
    nx: int,
    ny: int,
    element_name: str = "SmallDisplacementElement2D4N",
    condition_name: str | None = "LineLoadCondition2D2N",
    property_id: int = 1,
    root_part: str = "domain",
    triangles: bool = False,
) -> Mdpa:
    """Structured 2D mesh with left/right/bottom/top boundary submodelparts.

    Boundary submodelparts contain both the boundary nodes and, when
    condition_name is given, boundary line conditions (for surface loads).
    """
    m = Mdpa()

    def nid(ix: int, iy: int) -> int:
        return iy * (nx + 1) + ix + 1

    for iy in range(ny + 1):
        for ix in range(nx + 1):
            m.nodes[nid(ix, iy)] = (width * ix / nx, height * iy / ny, 0.0)

    elems = m.elements.setdefault(element_name, {})
    eid = 1
    for iy in range(ny):
        for ix in range(nx):
            n1, n2 = nid(ix, iy), nid(ix + 1, iy)
            n3, n4 = nid(ix + 1, iy + 1), nid(ix, iy + 1)
            if triangles:
                elems[eid] = (property_id, [n1, n2, n3]); eid += 1
                elems[eid] = (property_id, [n1, n3, n4]); eid += 1
            else:
                elems[eid] = (property_id, [n1, n2, n3, n4]); eid += 1

    m.sub_model_parts[root_part] = SubModelPart(
        root_part, nodes=list(m.nodes), elements=list(elems))

    edges = {
        "left":   [nid(0, iy) for iy in range(ny + 1)],
        "right":  [nid(nx, iy) for iy in range(ny + 1)],
        "bottom": [nid(ix, 0) for ix in range(nx + 1)],
        "top":    [nid(ix, ny) for ix in range(nx + 1)],
    }
    conds = m.conditions.setdefault(condition_name, {}) if condition_name else None
    cid = 1
    for name, nodes in edges.items():
        part = SubModelPart(name, nodes=list(nodes))
        if conds is not None:
            for a, b in zip(nodes, nodes[1:]):
                conds[cid] = (property_id, [a, b])
                part.conditions.append(cid)
                cid += 1
        m.sub_model_parts[name] = part
    return m


def create_box_mesh(
    lx: float,
    ly: float,
    lz: float,
    nx: int,
    ny: int,
    nz: int,
    element_name: str = "SmallDisplacementElement3D8N",
    condition_name: str | None = "SurfaceLoadCondition3D4N",
    property_id: int = 1,
    root_part: str = "domain",
) -> Mdpa:
    """Structured hexahedral box with xmin/xmax/ymin/ymax/zmin/zmax faces."""
    m = Mdpa()

    def nid(ix: int, iy: int, iz: int) -> int:
        return iz * (nx + 1) * (ny + 1) + iy * (nx + 1) + ix + 1

    for iz in range(nz + 1):
        for iy in range(ny + 1):
            for ix in range(nx + 1):
                m.nodes[nid(ix, iy, iz)] = (lx * ix / nx, ly * iy / ny, lz * iz / nz)

    elems = m.elements.setdefault(element_name, {})
    eid = 1
    for iz in range(nz):
        for iy in range(ny):
            for ix in range(nx):
                # Kratos Hexahedra3D8 node ordering: bottom face CCW, then top.
                elems[eid] = (property_id, [
                    nid(ix, iy, iz), nid(ix + 1, iy, iz),
                    nid(ix + 1, iy + 1, iz), nid(ix, iy + 1, iz),
                    nid(ix, iy, iz + 1), nid(ix + 1, iy, iz + 1),
                    nid(ix + 1, iy + 1, iz + 1), nid(ix, iy + 1, iz + 1),
                ])
                eid += 1

    m.sub_model_parts[root_part] = SubModelPart(
        root_part, nodes=list(m.nodes), elements=list(elems))

    # face name -> (fixed axis index, fixed value index, quad builder)
    faces: dict[str, list[list[int]]] = {
        "xmin": [[nid(0, iy, iz), nid(0, iy + 1, iz), nid(0, iy + 1, iz + 1), nid(0, iy, iz + 1)]
                 for iy in range(ny) for iz in range(nz)],
        "xmax": [[nid(nx, iy, iz), nid(nx, iy, iz + 1), nid(nx, iy + 1, iz + 1), nid(nx, iy + 1, iz)]
                 for iy in range(ny) for iz in range(nz)],
        "ymin": [[nid(ix, 0, iz), nid(ix, 0, iz + 1), nid(ix + 1, 0, iz + 1), nid(ix + 1, 0, iz)]
                 for ix in range(nx) for iz in range(nz)],
        "ymax": [[nid(ix, ny, iz), nid(ix + 1, ny, iz), nid(ix + 1, ny, iz + 1), nid(ix, ny, iz + 1)]
                 for ix in range(nx) for iz in range(nz)],
        "zmin": [[nid(ix, iy, 0), nid(ix, iy + 1, 0), nid(ix + 1, iy + 1, 0), nid(ix + 1, iy, 0)]
                 for ix in range(nx) for iy in range(ny)],
        "zmax": [[nid(ix, iy, nz), nid(ix + 1, iy, nz), nid(ix + 1, iy + 1, nz), nid(ix, iy + 1, nz)]
                 for ix in range(nx) for iy in range(ny)],
    }
    conds = m.conditions.setdefault(condition_name, {}) if condition_name else None
    cid = 1
    for name, quads in faces.items():
        nodes = sorted({n for quad in quads for n in quad})
        part = SubModelPart(name, nodes=nodes)
        if conds is not None:
            for quad in quads:
                conds[cid] = (property_id, quad)
                part.conditions.append(cid)
                cid += 1
        m.sub_model_parts[name] = part
    return m

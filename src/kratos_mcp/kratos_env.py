"""Resolution of the Kratos Multiphysics environment.

A local compiled build tree (e.g. $KRATOS_ROOT/bin/Release, needing
PYTHONPATH/LD_LIBRARY_PATH) is one way to get Kratos, but it is not
required: Kratos publishes official PyPI wheels for Linux/Windows x86_64
(`KratosMultiphysics` core, `Kratos<AppName>` per application, or the
`KratosMultiphysics-all` omnibus package) that `pip_install()` below can
install straight into this server's own Python environment. Once
installed that way, `resolve()` picks it up automatically through the
plain-import probe. See `tools/environment.py`'s `kratos_install` tool,
which is how an MCP client triggers this.

IMPORTANT: Kratos must NEVER be imported in the MCP server process.
It prints an ASCII banner on import (which would corrupt the stdio
JSON-RPC stream) and its C++ core can abort the whole process. All
Kratos access goes through subprocesses built with `build_env()`."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

DEFAULT_KRATOS_ROOT = "/home/vicente/src/Kratos"

# Official Kratos PyPI package names (Linux/Windows x86_64 wheels only, no
# macOS): https://pypi.org/project/KratosMultiphysics-all/
PYPI_CORE_PACKAGE = "KratosMultiphysics"
PYPI_ALL_PACKAGE = "KratosMultiphysics-all"

# Candidate build subdirectories, in order of preference.
_BUILD_CANDIDATES = ("bin/Release", "bin/FullDebug", "bin/Debug")

# Extra shared-library directories some Kratos applications link against
# (LinearSolversApplication needs MKL when built with USE_EIGEN_MKL).
_EXTRA_LIB_CANDIDATES = (
    "/opt/intel/oneapi/mkl/latest/lib",
    "/opt/intel/oneapi/mkl/latest/lib/intel64",
    "/opt/intel/mkl/lib/intel64",
)


def _extra_lib_dirs() -> list[str]:
    override = os.environ.get("KRATOS_EXTRA_LIBS")
    if override is not None:
        return [d for d in override.split(os.pathsep) if d]
    return [d for d in _EXTRA_LIB_CANDIDATES if Path(d).is_dir()]


@dataclass
class KratosEnv:
    """Resolved paths for a Kratos installation."""

    root: Path | None            # Kratos source/checkout root (or None if pip-installed)
    pythonpath: Path | None      # directory containing the KratosMultiphysics package
    libs: Path | None            # directory with the compiled shared libraries
    source: Path | None          # source tree for macro-based introspection
    pip_installed: bool = False
    python: str = field(default_factory=lambda: sys.executable)

    def build_env(self) -> dict[str, str]:
        """Environment dict for subprocesses that import Kratos."""
        env = os.environ.copy()
        if self.pythonpath is not None:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{self.pythonpath}{os.pathsep}{existing}" if existing else str(self.pythonpath)
        lib_dirs = ([str(self.libs)] if self.libs is not None else []) + _extra_lib_dirs()
        if lib_dirs:
            existing = env.get("LD_LIBRARY_PATH", "")
            joined = os.pathsep.join(lib_dirs)
            env["LD_LIBRARY_PATH"] = f"{joined}{os.pathsep}{existing}" if existing else joined
        return env

    def fingerprint(self) -> str:
        """Cheap identity of the build, used as a cache key component."""
        if self.pythonpath is not None:
            marker = self.pythonpath / "KratosMultiphysics" / "__init__.py"
            try:
                return f"{marker}:{marker.stat().st_mtime_ns}"
            except OSError:
                return str(marker)
        if self.pip_installed:
            # Reading dist-info metadata does not execute the package (safe
            # even though we can't import Kratos here); this lets the bridge
            # cache invalidate itself after kratos_install upgrades/installs.
            try:
                return f"pip:{metadata.version(PYPI_CORE_PACKAGE)}"
            except metadata.PackageNotFoundError:
                return "pip"
        return "unknown"


def resolve() -> KratosEnv:
    """Resolve the Kratos environment from environment variables.

    Precedence:
      1. KRATOS_PYTHONPATH / KRATOS_LIBS explicit overrides
      2. KRATOS_ROOT (default /home/vicente/src/Kratos) + bin/Release
      3. pip-installed KratosMultiphysics (importable without env tweaks;
         this is also what `kratos_install` produces)

    A local build tree is optional: if none is found and nothing is
    pip-installed yet, `is_available()` returns False and tools report it,
    with a hint to call kratos_install rather than requiring KRATOS_ROOT.
    """
    root_str = os.environ.get("KRATOS_ROOT", DEFAULT_KRATOS_ROOT)
    root = Path(root_str).expanduser() if root_str else None
    source_str = os.environ.get("KRATOS_SOURCE", "")
    source = Path(source_str).expanduser() if source_str else root

    explicit_pp = os.environ.get("KRATOS_PYTHONPATH")
    explicit_libs = os.environ.get("KRATOS_LIBS")
    if explicit_pp:
        pp = Path(explicit_pp).expanduser()
        libs = Path(explicit_libs).expanduser() if explicit_libs else pp / "libs"
        return KratosEnv(root=root, pythonpath=pp, libs=libs, source=source)

    if root is not None and root.is_dir():
        for candidate in _BUILD_CANDIDATES:
            build = root / candidate
            if (build / "KratosMultiphysics").is_dir():
                return KratosEnv(root=root, pythonpath=build, libs=build / "libs", source=source)

    # Fall back to a pip-installed Kratos, probed in a subprocess so the
    # banner never reaches our stdout.
    probe = subprocess.run(
        [sys.executable, "-c", "import KratosMultiphysics"],
        capture_output=True,
        timeout=60,
    )
    if probe.returncode == 0:
        return KratosEnv(root=root, pythonpath=None, libs=None, source=source, pip_installed=True)

    # Nothing found: return a best-effort env; tools report the problem.
    return KratosEnv(root=root, pythonpath=None, libs=None, source=source)


def is_available(env: KratosEnv | None = None) -> bool:
    env = env or resolve()
    return env.pip_installed or env.pythonpath is not None


def pypi_package_name(application: str) -> str:
    """Map a Kratos application name (e.g. 'StructuralMechanicsApplication')
    to its PyPI project name (e.g. 'KratosStructuralMechanicsApplication')."""
    return application if application.startswith("Kratos") else f"Kratos{application}"


def pip_install(
    packages: list[str],
    python: str | None = None,
    upgrade: bool = False,
    timeout: float = 1800.0,
) -> subprocess.CompletedProcess:
    """Install packages with pip into the given (or current) interpreter.

    This never imports Kratos, so unlike everything else that touches
    Kratos it is safe to call from the server process; kratos_install still
    runs it off the event loop via anyio.to_thread.run_sync. Kratos wheels
    are large (hundreds of MB for KratosMultiphysics-all), hence the long
    default timeout.
    """
    cmd = [python or sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")
    cmd += packages
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def data_dir() -> Path:
    """Directory for jobs, caches and other server state."""
    base = os.environ.get("KRATOS_MCP_HOME", "~/.kratos-mcp")
    d = Path(base).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d

"""Amorçage automatique de `pip`, `wheel` et `setuptools` dans un environnement virtuel Python.

Gère notamment les distributions Windows `pythoncore-3.12-64` installées par le nouveau
Python Install Manager (`py install 3.12-64`), qui ne contiennent pas le module `ensurepip`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request


def has_working_pip() -> bool:
    res = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return res.returncode == 0


def try_ensurepip() -> bool:
    res = subprocess.run(
        [sys.executable, "-m", "ensurepip", "--upgrade"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return res.returncode == 0 and has_working_pip()


def bootstrap_from_pypi_wheel() -> bool:
    print("  [AUTO] Telechargement de pip depuis PyPI (pythoncore sans ensurepip)...", flush=True)
    req = urllib.request.Request(
        "https://pypi.org/pypi/pip/json",
        headers={"User-Agent": "YelenNotes-Bootstrap/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        metadata = json.loads(resp.read().decode("utf-8"))

    wheel_info = next(
        u for u in metadata.get("urls", []) if u.get("filename", "").endswith("-py3-none-any.whl")
    )
    tmp_dir = tempfile.mkdtemp(prefix="yelen_pip_")
    wheel_path = os.path.join(tmp_dir, wheel_info["filename"])
    urllib.request.urlretrieve(wheel_info["url"], wheel_path)

    pip_runner = os.path.join(wheel_path, "pip")
    subprocess.check_call(
        [
            sys.executable,
            pip_runner,
            "install",
            "--disable-pip-version-check",
            "--no-warn-script-location",
            "--upgrade",
            wheel_path,
            "wheel",
            "setuptools",
        ]
    )
    return has_working_pip()


def bootstrap_from_get_pip() -> bool:
    print("  [AUTO] Telechargement de secours via bootstrap.pypa.io/get-pip.py...", flush=True)
    tmp_dir = tempfile.mkdtemp(prefix="yelen_getpip_")
    script_path = os.path.join(tmp_dir, "get-pip.py")
    urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", script_path)
    subprocess.check_call([sys.executable, script_path, "--no-warn-script-location"])
    return has_working_pip()


def main() -> int:
    if has_working_pip():
        return 0
    if try_ensurepip():
        return 0
    try:
        if bootstrap_from_pypi_wheel():
            return 0
    except Exception as exc:
        print(f"  [INFO] Methode roue PyPI indisponible ({exc}), essai methode secondaire...", flush=True)
    if bootstrap_from_get_pip():
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the pinned W4A8 source and restore the prior package on failed validation."""
from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from services.h3_w4a8_provenance import (
    RUNTIME_REVISION as REVISION, marker_package_matches,
)


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def extract_pinned_source(source: Path, destination: Path) -> None:
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if actual != REVISION:
        raise RuntimeError(f'Unexpected comfy-kitchen W4A8 revision: {actual}')
    archive = subprocess.check_output(['git', 'archive', REVISION], cwd=source)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for item in bundle.getmembers():
            path = Path(item.name)
            if path.is_absolute() or '..' in path.parts or not (item.isfile() or item.isdir()):
                raise RuntimeError('Pinned W4A8 source archive contains an unsafe entry')
        bundle.extractall(destination)


def install_and_validate(wheel: Path, *, uv: str, site: Path, marker: Path,
                         backup: Path, validator: Path, cwd: Path) -> None:
    """Keep an exact rollback of this distribution, never other dependencies."""
    roots = [site/'comfy_kitchen', *sorted(site.glob('comfy_kitchen-*.dist-info'))]
    roots = [path for path in roots if path.exists() or path.is_symlink()]
    if marker.is_symlink() or any(path.is_symlink() or not path.is_dir() for path in roots):
        raise RuntimeError('W4A8 runtime paths must be ordinary directories and files')
    backup.mkdir(parents=True, exist_ok=False)
    for path in roots:
        shutil.copytree(path, backup/path.name, symlinks=True)
    previous_marker = marker.read_bytes() if marker.is_file() else None
    marker.unlink(missing_ok=True)
    new_roots = [site/'comfy_kitchen', site/'comfy_kitchen-0.2.25.dist-info']
    try:
        run(uv, 'pip', 'install', '--python', sys.executable, '--force-reinstall',
            '--no-deps', '--no-index', str(wheel), cwd=cwd)
        run(sys.executable, str(validator), cwd=cwd)
        marker_data = json.loads(marker.read_text())
        if not marker_package_matches(marker_data, site/"comfy_kitchen"):
            raise RuntimeError("W4A8 validation does not match the installed package")
    except BaseException:
        marker.unlink(missing_ok=True)
        for path in set(roots + new_roots):
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        for path in roots:
            shutil.copytree(backup/path.name, path, symlinks=True)
        if previous_marker is not None:
            marker.write_bytes(previous_marker)
        raise


def main() -> None:
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('W4A8 installation requires the selected Pinokio virtual environment.')
    uv = shutil.which('uv')
    if uv is None:
        raise RuntimeError("W4A8 installation requires Pinokio's uv command. Run Update from Pinokio.")
    source = APP_ROOT/'services/comfy_kitchen_w4a8'
    scratch = Path(tempfile.mkdtemp(prefix='.w4a8-build-', dir=APP_ROOT))
    try:
        build = scratch/'source'
        build.mkdir()
        extract_pinned_source(source, build)
        run(sys.executable, 'setup.py', 'bdist_wheel', '--no-cuda', cwd=build)
        wheel = build/'dist/comfy_kitchen-0.2.25-py3-none-any.whl'
        if not wheel.is_file():
            raise RuntimeError('Pinned comfy-kitchen W4A8 wheel was not created')
        install_and_validate(wheel, uv=uv, site=Path(sysconfig.get_path('purelib')),
                             marker=Path(sys.prefix)/'.maestro_h3_w4a8_validated.json',
                             backup=scratch/'rollback', validator=APP_ROOT/'scripts/validate_h3_w4a8.py',
                             cwd=build)
    except BaseException:
        # Retain failed build/rollback files even when restoration itself fails.
        # Remove them only after the previous runtime has been verified restored.
        print(f'W4A8 recovery files retained at {scratch}', file=sys.stderr)
        raise
    else:
        shutil.rmtree(scratch)


if __name__ == '__main__':
    main()

"""Install only Drama 3's codec dependencies; retain the installed AI ABI.

Run with Maestro's selected Pinokio Python. No model download, CUDA probe, or
inference occurs. Fish's pinned native inference source ships in the app.
"""
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Run this helper in Pinokio's terminal, where uv is available.")
    app = Path(__file__).resolve().parents[1]
    abi = ("torch", "torchaudio", "transformers", "tokenizers", "numpy", "einx", "protobuf")
    versions = {}
    for name in abi:
        try: versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: pass
    for package in importlib.metadata.distributions():
        name = package.metadata.get("Name", "").lower().replace("_", "-")
        if name.startswith(("nvidia-", "cuda-")) or name in ("triton", "torchcodec", "xformers"):
            versions[name] = package.version
    if not all(k in versions for k in ("torch", "torchaudio", "transformers", "tokenizers")):
        raise SystemExit("Install Maestro's normal AI runtime first; this helper never installs a replacement.")
    with tempfile.TemporaryDirectory(prefix="maestro-drama3-install-") as folder:
        constraints = Path(folder) / "constraints.txt"
        constraints.write_text("".join(f"{k}=={v}\n" for k, v in versions.items()))
        overrides = Path(folder) / "overrides.txt"
        # Audiotools declares an obsolete protobuf<3.20 bound. Retain the
        # host's installed version rather than downgrade other Maestro models.
        # Torch's wheel metadata can differ from the already qualified CUDA
        # libraries. Freeze those installed bindings rather than resolve them.
        overrides.write_text("".join(f"{k}=={v}\n" for k, v in versions.items()
            if k.startswith(("nvidia-", "cuda-")) or k == "protobuf")
            + ("" if "protobuf" in versions else "protobuf>=3.20,<6\n"))
        subprocess.run([uv, "pip", "install", "--python", sys.executable,
            "--constraint", str(constraints), "--override", str(overrides),
            "-r", str(app / "requirements-drama3.txt")], check=True)
    print("Drama 3 codec dependencies installed; AI ABI retained. No model was loaded.")


if __name__ == "__main__": main()

const { runtimeSecretEnv } = require("./launcher_secret_env")
const {
  isRtx50,
  isSolCapable,
  needsCuda13DriverUpdate,
  runtimeProfile,
} = require("./launcher_profile")

module.exports = async (kernel) => {
  const runtime = runtimeProfile(kernel)
  const cuda13DriverUpdateRequired = (
    isSolCapable(kernel) && needsCuda13DriverUpdate(kernel)
  )
  const alreadyCurrentAndReady =
    `{{/already up[- ]to[- ]date/i.test(input.stdout) && exists('${runtime.marker}') && exists('${runtime.flashMarker}') ? 'uptodate' : 'build'}}`
  return {
    run: [{
    when: cuda13DriverUpdateRequired && isRtx50(kernel),
    method: "input",
    params: {
      title: "NVIDIA driver update required",
      description: `RTX 50 requires NVIDIA driver 580 or newer for Maestro's CUDA 13 runtime (found ${kernel.gpu_driver}). Update the driver, then run Update again.`
    },
    next: null
  }, {
    when: cuda13DriverUpdateRequired && !isRtx50(kernel),
    method: "log",
    params: {
      raw: `NVIDIA driver ${kernel.gpu_driver} cannot use Maestro's preferred CUDA 13 H3 runtime; Update will build or repair the preserved CUDA 12.8 compatibility runtime.`
    }
  }, {
    // Pull the latest launcher + app code (single monorepo, so this one
    // pull covers both `ui/` and `app/`). The NEXT step inspects this
    // pull's output: if the repo was already current, there is nothing
    // new to install or rebuild, so we skip straight to the end instead
    // of spending several minutes on a redundant dependency install +
    // UI build.
    method: "shell.run",
    params: {
      env: runtimeSecretEnv,
      message: "git pull"
    }
  }, {
    // Branch on the git pull output (captured here as input.stdout — a
    // shell.run always returns its raw terminal content as stdout):
    //   - already current  -> jump to "uptodate" (log a notice, then end)
    //   - new commits found -> jump to "build"   (run the full update)
    // Matches both the modern "Already up to date" and the older git
    // "Already up-to-date" spelling, case-insensitively. If detection
    // ever fails (e.g. empty stdout), the regex simply won't match and
    // we fall through to "build" — the safe default is a full update,
    // never a wrongly-skipped rebuild.
    method: "jump",
    params: {
      // An already-current checkout still enters the build path when either
      // its hardware runtime or optional FlashAttention repair marker is
      // missing. This keeps interrupted installs and one-time repairs resumable.
      id: alreadyCurrentAndReady
    }
  }, {
    id: "uptodate",
    method: "shell.run",
    params: {
      env: runtimeSecretEnv,
      message: "python app/scripts/ensure_environment_defaults.py --file ENVIRONMENT"
    }
  }, {
    // Reached ONLY when the repo was already current (the "build" path
    // jumps over this step). Before halting, self-heal the seed-vc
    // component if it's missing (GPL-3.0, cloned from its own repo — see
    // install.js): a failed earlier clone shouldn't leave voice features
    // broken until the next code update.
    when: "{{!exists('app/postprocessing/seedvc/__init__.py')}}",
    method: "shell.run",
    params: {
      env: runtimeSecretEnv,
      message: "git clone --depth 1 --branch v1.0.0 https://github.com/Blizaine/maestro-seedvc app/postprocessing/seedvc"
    }
  }, {
    method: "script.start",
    params: {
      uri: "blender_mcp_install.js",
      params: { venv: runtime.env, venv_python: runtime.python }
    }
  }, {
    method: "script.start",
    params: {
      uri: "blender_runtime_install.js",
      params: { venv: runtime.env, venv_python: runtime.python }
    }
  }, {
    method: "script.start",
    params: {
      uri: "h3_acceleration_install.js",
      params: { venv: runtime.env, venv_python: runtime.python }
    }
  }, {
    method: "script.start",
    params: {
      uri: "h3_w4a8_runtime_install.js",
      params: { venv: runtime.env, venv_python: runtime.python }
    }
  }, {
    method: "log",
    params: {
      raw: "Already up to date — no new commits pulled. Skipped dependency install and UI rebuild."
    },
    next: null
  }, {
    id: "build",
    method: "shell.run",
    params: {
      env: runtimeSecretEnv,
      message: "python app/scripts/ensure_environment_defaults.py --file ENVIRONMENT"
    }
  }, {
    // Fetch the seed-vc component if missing (GPL-3.0, own repository —
    // see install.js). Runs at the top of the build path so the update
    // that removed the formerly-tracked tree clones it right back, and
    // any later update self-heals a failed clone. Keep the pinned tag in
    // sync with install.js.
    when: "{{!exists('app/postprocessing/seedvc/__init__.py')}}",
    method: "shell.run",
    params: {
      env: runtimeSecretEnv,
      message: "git clone --depth 1 --branch v1.0.0 https://github.com/Blizaine/maestro-seedvc app/postprocessing/seedvc"
    }
  }, {
    method: "shell.run",
    params: {
      env: runtimeSecretEnv,
      venv: runtime.env,
      venv_python: runtime.python,
      path: "app",
      message: "uv pip install -r requirements.txt"
    }
  }, {
    // Existing installs may have the main runtime marker but still contain a
    // Windows FlashAttention wheel whose CUDA DLL cannot load. Repair only
    // that optional wheel once; a normal torch.js run writes both markers.
    when: `{{exists('${runtime.marker}') && !exists('${runtime.flashMarker}')}}`,
    method: "script.start",
    params: {
      uri: "torch.js",
      params: {
        venv: runtime.env,
        venv_python: runtime.python,
        path: "app",
        flash_only: true
      }
    }
  }, {
    // Skip the full torch.js path when its hardware-specific marker is
    // present. Deleting the marker and running Update remains the bounded
    // recovery path for a damaged required runtime.
    when: `{{!exists('${runtime.marker}')}}`,
    method: "script.start",
    params: {
      uri: "torch.js",
      params: {
        venv: runtime.env,
        venv_python: runtime.python,
        path: "app",
        xformers: true
      }
    }
  }, {
    // Mirror of the install.js GGUF-kernels step — idempotent, so
    // re-runs cheaply on every update. Catches existing installs
    // up to the new behavior without forcing a reinstall.
    method: "shell.run",
    params: {
      env: runtimeSecretEnv,
      venv: runtime.env,
      venv_python: runtime.python,
      path: "app",
      message: "python scripts/install_gguf_kernels.py"
    }
  }, {
    method: "script.start",
    params: {
      uri: "blender_mcp_install.js",
      params: { venv: runtime.env, venv_python: runtime.python }
    }
  }, {
    method: "script.start",
    params: {
      uri: "blender_runtime_install.js",
      params: { venv: runtime.env, venv_python: runtime.python }
    }
  }, {
    method: "script.start",
    params: {
      uri: "h3_acceleration_install.js",
      params: { venv: runtime.env, venv_python: runtime.python }
    }
  }, {
    method: "script.start",
    params: {
      uri: "h3_w4a8_runtime_install.js",
      params: { venv: runtime.env, venv_python: runtime.python }
    }
  }, {
    when: "{{exists('ui/package.json')}}",
    method: "shell.run",
    params: {
      env: runtimeSecretEnv,
      path: "ui",
      message: [
        "npm install",
        "npm run build"
      ]
    }
  },
  // Update SAM 3.1 service (pull latest + reinstall) ONLY if SAM is
  // already installed. This way:
  //   - Users who never installed SAM (most users) don't get a slow
  //     conda env install they didn't ask for during a regular update.
  //   - Users who DID install SAM keep getting it kept up to date
  //     alongside the main app on every update.
  // Fresh-install path: install.js no longer runs sam_install.js;
  // users who want inpaint click "Install Inpaint Support" from the
  // Pinokio menu, which fires sam_install.js once. After that, this
  // gate is satisfied and SAM updates with every regular update.
  {
    when: "{{exists('app/services/sam/env')}}",
    method: "script.start",
    params: {
      uri: "sam_install.js"
    }
    }]
  }
}

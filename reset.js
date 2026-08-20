// Wipes every install artifact so the next Install starts from scratch.
// Mirrors the directories created by install.js and sam_install.js.
module.exports = {
  run: [
    // Main Python venv
    { method: "fs.rm", params: { path: "app/env" } },
    // RTX 50-series Python 3.11 / CUDA 13 venv
    { method: "fs.rm", params: { path: "app/env-rtx50" } },
    // Preferred RTX 40-series Python 3.11 / CUDA 13 H3 performance venv
    { method: "fs.rm", params: { path: "app/env-sol" } },
    // Optional official LTX-2.5 / Transformers 5.x sidecar venv
    { method: "fs.rm", params: { path: "app/env-ltx25" } },
    // LTX-2.5 sidecars paired to optional CUDA 13 hardware runtimes
    { method: "fs.rm", params: { path: "app/env-ltx25-sol" } },
    { method: "fs.rm", params: { path: "app/env-ltx25-rtx50" } },
    // Pinned official LTX-2.5 source used to build the sidecar
    { method: "fs.rm", params: { path: "app/third_party/ltx25-runtime" } },
    // SAM 3.1 Python 3.12 conda env
    { method: "fs.rm", params: { path: "app/services/sam/env" } },
    // SAM 3 source checkout (will be re-cloned on install)
    { method: "fs.rm", params: { path: "app/services/sam/sam3" } },
    // Pinned official Blender MCP checkout (re-cloned on Install)
    { method: "fs.rm", params: { path: "app/services/blender_mcp" } },
    // Official portable Blender runtime + installed extension/config
    { method: "fs.rm", params: { path: "app/tools/blender" } },
    // Pinned optional Kijai Sol-Attn checkout (re-cloned on Install)
    { method: "fs.rm", params: { path: "app/services/sol_attn_kijai" } },
    // Pinned official SageAttention2++ checkout (source-built only on supported Linux SM120)
    { method: "fs.rm", params: { path: "app/services/sageattention_thu_ml" } },
    // Isolated official NVIDIA CUDA toolkit used only to compile SageAttention2++
    { method: "fs.rm", params: { path: "app/tools/cuda-12.8.1" } },
    { method: "fs.rm", params: { path: "app/services/comfy_kitchen_w4a8" } },
    // UI build artifacts
    { method: "fs.rm", params: { path: "ui/node_modules" } },
    { method: "fs.rm", params: { path: "ui/dist" } }
  ]
}

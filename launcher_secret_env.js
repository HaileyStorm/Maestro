const runtimeSecretEnv = Object.freeze({
  CLOUDFLARE_API_TOKEN: "",
  PINOKIO_STABLE_SHARE_UPDATE_SECRET: "",
})

const shareHelperSecretEnv = Object.freeze({
  CLOUDFLARE_API_TOKEN: "",
})

module.exports = { runtimeSecretEnv, shareHelperSecretEnv }

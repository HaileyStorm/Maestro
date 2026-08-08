export const extractAccountId = (text) => (
  String(text).match(/Account ID[^a-f0-9]*([a-f0-9]{32})/i)?.[1]
  || String(text).match(/\b[a-f0-9]{32}\b/i)?.[0]
  || ""
)

export const extractNamespaceId = (text) => (
  String(text).match(/["']?id["']?\s*[:=]\s*["']([a-f0-9]{32})["']/i)?.[1]
  || ""
)

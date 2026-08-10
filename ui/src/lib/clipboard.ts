export interface ClipboardEnvironment {
  clipboard?: Pick<Clipboard, 'writeText'>
  document: Document
}

export interface AssistantCopyScope {
  token: number
  workspace: string
  projectInstance: string
}

export function isAssistantCopyScopeCurrent(
  request: AssistantCopyScope,
  current: AssistantCopyScope,
): boolean {
  return request.token === current.token
    && request.workspace === current.workspace
    && request.projectInstance === current.projectInstance
}

export function copyTextWithDocumentCommand(
  content: string,
  documentRef: Document = document,
): boolean {
  const area = documentRef.createElement('textarea')
  const previousFocus = documentRef.activeElement instanceof HTMLElement
    ? documentRef.activeElement
    : null
  area.value = content
  area.readOnly = true
  area.tabIndex = -1
  area.setAttribute('aria-hidden', 'true')
  area.style.position = 'fixed'
  area.style.left = '-9999px'
  area.style.top = '0'
  documentRef.body.appendChild(area)
  try {
    area.focus()
    area.select()
    area.setSelectionRange(0, area.value.length)
    return documentRef.execCommand('copy')
  } catch {
    return false
  } finally {
    area.remove()
    previousFocus?.focus()
  }
}

export async function copyTextToClipboard(
  content: string,
  environment?: ClipboardEnvironment,
): Promise<boolean> {
  const clipboard = environment ? environment.clipboard : navigator.clipboard
  const documentRef = environment ? environment.document : document
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(content)
      return true
    } catch {
      // Clipboard API requires a secure context in many browsers. Maestro's
      // local HTTP UI still supports the browser's synchronous copy command.
    }
  }
  return copyTextWithDocumentCommand(content, documentRef)
}

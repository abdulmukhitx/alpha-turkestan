import { useEffect, useRef } from 'react'

const GOOGLE_SCRIPT_ID = 'google-identity-services'
const GOOGLE_SCRIPT_SRC = 'https://accounts.google.com/gsi/client'
let googleScriptPromise = null
let initializedClientId = null
let activeCredentialHandler = null

function loadGoogleIdentityServices() {
  if (window.google?.accounts?.id) return Promise.resolve(window.google)
  if (googleScriptPromise) return googleScriptPromise

  googleScriptPromise = new Promise((resolve, reject) => {
    let script = document.getElementById(GOOGLE_SCRIPT_ID)
    const loaded = () => window.google?.accounts?.id
      ? resolve(window.google)
      : reject(new Error('Google Identity Services did not initialize'))
    const failed = () => reject(new Error('Google Identity Services could not be loaded'))

    if (!script) {
      script = document.createElement('script')
      script.id = GOOGLE_SCRIPT_ID
      script.src = GOOGLE_SCRIPT_SRC
      script.async = true
      script.defer = true
    }
    script.addEventListener('load', loaded, { once: true })
    script.addEventListener('error', failed, { once: true })
    if (!script.isConnected) document.head.appendChild(script)
  })
  return googleScriptPromise
}

export default function GoogleSignInButton({
  clientId,
  locale,
  disabled = false,
  onCredential,
  onError,
}) {
  const containerRef = useRef(null)
  const credentialRef = useRef(onCredential)
  const errorRef = useRef(onError)
  credentialRef.current = onCredential
  errorRef.current = onError

  useEffect(() => {
    if (!clientId || !containerRef.current) return undefined
    let cancelled = false
    const container = containerRef.current
    const receiveCredential = (response) => {
      if (response?.credential) credentialRef.current?.(response.credential)
    }
    activeCredentialHandler = receiveCredential

    loadGoogleIdentityServices()
      .then((google) => {
        if (cancelled) return
        if (initializedClientId !== clientId) {
          google.accounts.id.initialize({
            client_id: clientId,
            callback: (response) => activeCredentialHandler?.(response),
            auto_select: false,
            cancel_on_tap_outside: true,
          })
          initializedClientId = clientId
        }
        container.replaceChildren()
        google.accounts.id.renderButton(container, {
          type: 'standard',
          theme: 'outline',
          size: 'large',
          shape: 'rectangular',
          text: 'continue_with',
          logo_alignment: 'left',
          locale,
          width: Math.min(400, Math.max(240, container.clientWidth || 320)),
        })
      })
      .catch((error) => {
        if (!cancelled) errorRef.current?.(error)
      })

    return () => {
      cancelled = true
      if (activeCredentialHandler === receiveCredential) activeCredentialHandler = null
      container.replaceChildren()
    }
  }, [clientId, locale])

  return (
    <div className={`google-sign-in${disabled ? ' disabled' : ''}`} aria-busy={disabled}>
      <div ref={containerRef} />
    </div>
  )
}

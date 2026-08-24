import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { RootRecoveryBoundary } from './RootRecoveryBoundary.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootRecoveryBoundary>
      <App />
    </RootRecoveryBoundary>
  </StrictMode>,
)

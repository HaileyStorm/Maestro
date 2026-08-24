import { Component, type ReactNode } from 'react'

type RootRecoveryBoundaryProps = {
  children: ReactNode
}

type RootRecoveryBoundaryState = {
  failed: boolean
}

export class RootRecoveryBoundary extends Component<
  RootRecoveryBoundaryProps,
  RootRecoveryBoundaryState
> {
  state: RootRecoveryBoundaryState = { failed: false }

  static getDerivedStateFromError(): RootRecoveryBoundaryState {
    return { failed: true }
  }

  componentDidCatch(): void {
    // Intentionally do not log exception text or component stacks. Render
    // failures can contain account or creative-work data in their values.
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <main
        className="flex h-full w-full items-center justify-center bg-bg-primary px-6 text-text-primary"
        role="alert"
        aria-labelledby="maestro-root-recovery-title"
      >
        <div className="w-full max-w-md rounded-xl border border-border bg-bg-secondary p-6 text-center shadow-xl">
          <h1 id="maestro-root-recovery-title" className="text-base font-semibold">
            Maestro needs to recover this screen
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-text-secondary">
            A screen stopped responding. Your saved work was not changed. If this followed a sign-in or project change, sign in again and choose a project.
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-3">
            <button
              type="button"
              onClick={() => this.setState({ failed: false })}
              className="min-h-11 rounded-lg bg-accent-blue px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            >
              Try again
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            >
              Reload Maestro
            </button>
          </div>
        </div>
      </main>
    )
  }
}

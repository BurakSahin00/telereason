/** Loading state: rows of raised blocks (DESIGN.md, States). */
export function LoadingBlocks({ rows, height = 'h-12' }: { rows: number; height?: string }) {
  return (
    <div aria-label="Loading" className="flex flex-col gap-3">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className={`${height} bg-raised`} />
      ))}
    </div>
  )
}

/** Error state: a square failure mark, what happened and what to do, and a retry. */
export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3.5">
      <span aria-hidden="true" className="mt-2 size-2.5 shrink-0 bg-fault" />
      <div>
        <p>{message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mono-label mt-3 border border-line-strong px-3 py-1.5 text-text hover:bg-raised"
        >
          Try again
        </button>
      </div>
    </div>
  )
}

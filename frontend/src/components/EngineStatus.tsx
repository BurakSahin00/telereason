import { useHealth } from '../api/queries.ts'
import { StatusChip } from './StatusChip.tsx'

/** Engine chip from /api/health, plus a Recorded night chip so a demo in fake mode is never mistaken for a live run. */
export function EngineStatus() {
  const health = useHealth()

  if (health.isPending) {
    return <span aria-label="Checking the engine" className="h-[26px] w-36 bg-raised" />
  }

  // A failed refresh wins over older data: the chip shows the latest answer only.
  if (health.isError) {
    return <StatusChip tone="fault">Engine unreachable</StatusChip>
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      {health.data.engine === 'fake' && <StatusChip>Recorded night</StatusChip>}
      {health.data.ready ? (
        <StatusChip tone="open">Engine ready</StatusChip>
      ) : (
        <StatusChip tone="screening">Engine loading</StatusChip>
      )}
    </div>
  )
}

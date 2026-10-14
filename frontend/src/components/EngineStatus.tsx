import { useQuery } from '@tanstack/react-query'
import { getJson } from '../api/client.ts'
import { StatusChip } from './StatusChip.tsx'

const HEALTH_REFRESH_MS = 15_000

/** Engine chip from /api/health, plus a FAKE DATA chip so a demo is never recorded in fake mode. */
export function EngineStatus() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => getJson('/api/health', { signal }),
    refetchInterval: HEALTH_REFRESH_MS,
  })

  if (health.isPending) {
    return <span aria-label="Checking the engine" className="h-[26px] w-36 bg-raised" />
  }

  // A failed refresh wins over older data: the chip shows the latest answer only.
  if (health.isError) {
    return <StatusChip tone="fault">Engine unreachable</StatusChip>
  }

  return (
    <div className="flex items-center gap-2">
      {health.data.engine === 'fake' && <StatusChip>Fake data</StatusChip>}
      {health.data.ready ? (
        <StatusChip tone="open">Engine ready</StatusChip>
      ) : (
        <StatusChip tone="screening">Engine loading</StatusChip>
      )}
    </div>
  )
}

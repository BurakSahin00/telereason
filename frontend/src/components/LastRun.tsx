import { useLatestReport } from '../api/queries.ts'

/** Last night's run id in the app bar, exactly as the report sends it. */
export function LastRun() {
  const report = useLatestReport()

  if (report.isPending) return <span aria-label="Loading last night's run" className="h-[26px] w-48 bg-raised" />
  if (report.isError) return null

  return (
    <span className="flex items-baseline gap-2.5">
      <span className="mono-label">Last night</span>
      <span className="font-mono text-label text-text">{report.data.run_id}</span>
    </span>
  )
}

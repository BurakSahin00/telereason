import { useLatestReport } from '../api/queries.ts'

/** Last night's run id in the app bar, exactly as the report sends it. */
export function LastRun() {
  const report = useLatestReport()

  if (report.isPending)
    return <span aria-label="Loading last night's run" className="h-[26px] w-48 bg-raised" />
  if (report.isError) return null

  return (
    <span className="flex min-w-0 max-w-full flex-wrap items-baseline gap-x-2.5 gap-y-1 xl:max-w-[240px] xl:flex-nowrap 2xl:max-w-[320px]">
      <span className="mono-label shrink-0 whitespace-nowrap">Last night</span>
      <span className="min-w-0 font-mono text-label text-text [overflow-wrap:anywhere]">
        {report.data.run_id}
      </span>
    </span>
  )
}

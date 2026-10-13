import { PageHeader } from '../components/PageHeader.tsx'
import { Panel } from '../components/Panel.tsx'

// Layout from design/report.png: gate on the left, the report on the right.
export function ReportPage() {
  return (
    <>
      <PageHeader title="Morning report" subtitle="What happened last night, and where to look first." />
      <div className="grid gap-gutter lg:grid-cols-[590px_1fr]">
        <Panel title="Gate" label="What the data can support" brackets className="min-h-96" />
        <div className="flex flex-col gap-gutter">
          <Panel title="Look first" className="min-h-72" />
          <Panel title="Largest deviations" className="min-h-48" />
        </div>
      </div>
    </>
  )
}

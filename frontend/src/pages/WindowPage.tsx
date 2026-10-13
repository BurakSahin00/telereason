import { Link, useParams } from 'react-router'
import { PageHeader } from '../components/PageHeader.tsx'

export function WindowPage() {
  const { windowId = '' } = useParams()

  return (
    <PageHeader
      title={`Window ${windowId}`}
      subtitle="Measured facts, signal chart and note for this window."
      breadcrumb={
        <nav aria-label="Breadcrumb" className="mono-label">
          <Link to="/" className="hover:text-text">
            Report
          </Link>
          <span aria-hidden="true"> / </span>
          <span className="text-text">{windowId}</span>
        </nav>
      }
    />
  )
}

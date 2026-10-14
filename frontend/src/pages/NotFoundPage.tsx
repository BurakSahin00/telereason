import { Link } from 'react-router'
import { PageHeader } from '../components/PageHeader.tsx'

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" subtitle="This address does not match any TeleReason page." />
      <Link to="/" className="mono-label text-text underline underline-offset-4">
        Open the morning report
      </Link>
    </>
  )
}

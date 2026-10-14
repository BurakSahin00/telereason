import type { ReactNode } from 'react'

type PageHeaderProps = {
  title: string
  subtitle?: string
  breadcrumb?: ReactNode
  /** Status chips on the right of the title. */
  chips?: ReactNode
}

export function PageHeader({ title, subtitle, breadcrumb, chips }: PageHeaderProps) {
  return (
    <div className="mt-10 mb-6">
      {breadcrumb && <div className="mb-4">{breadcrumb}</div>}
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <h1 className="text-title [overflow-wrap:anywhere]">{title}</h1>
          {subtitle && <p className="mt-2.5 text-muted">{subtitle}</p>}
        </div>
        {chips && <div className="flex flex-wrap gap-3">{chips}</div>}
      </div>
    </div>
  )
}

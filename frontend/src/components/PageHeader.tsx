import type { ReactNode } from 'react'

type PageHeaderProps = {
  title: string
  subtitle?: string
  breadcrumb?: ReactNode
}

export function PageHeader({ title, subtitle, breadcrumb }: PageHeaderProps) {
  return (
    <div className="mt-10 mb-6">
      {breadcrumb && <div className="mb-4">{breadcrumb}</div>}
      <h1 className="text-title">{title}</h1>
      {subtitle && <p className="mt-2.5 text-muted">{subtitle}</p>}
    </div>
  )
}

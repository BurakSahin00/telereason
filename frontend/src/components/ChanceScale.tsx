type ChanceScaleProps = {
  auroc: number
  /** AUROC interval [low, high] from the gate. */
  interval: [number, number]
  /** Facts-only lines get an outlined band, screening and open lines a filled one. */
  filled: boolean
}

const pct = (value: number) => `${Math.min(Math.max(value, 0), 1) * 100}%`

/** 0 to 1 scale with the AUROC interval as a band and a dashed line at chance (0.5). */
export function ChanceScale({ auroc, interval, filled }: ChanceScaleProps) {
  const [low, high] = interval
  return (
    <div
      role="img"
      aria-label={`AUROC ${auroc.toFixed(3)}, interval ${low.toFixed(3)} to ${high.toFixed(3)}; chance is 0.5.`}
      className="relative mt-5 h-12"
    >
      <span className="absolute inset-x-0 top-[22px] h-px bg-line-strong" />
      <span
        className="absolute inset-x-0 top-3.5 h-[9px]"
        style={{
          background:
            'repeating-linear-gradient(90deg, var(--color-line-strong) 0 1px, transparent 1px 10%)',
        }}
      />
      <span className="absolute right-0 top-3.5 h-[9px] w-px bg-line-strong" />
      <span
        className={`absolute top-[9px] h-3 min-w-1 ${
          filled ? 'bg-screening' : 'border-[1.5px] border-closed bg-raised'
        }`}
        style={{ left: pct(low), width: `calc(${pct(high)} - ${pct(low)})` }}
      />
      <span className="absolute top-1 h-6 w-0.5 -translate-x-1/2 bg-text" style={{ left: pct(auroc) }} />
      <span className="absolute left-1/2 top-0.5 h-7 border-l-[1.5px] border-dashed border-text" />
      <span className="absolute left-0 top-[30px] font-mono text-[12px] text-muted">0</span>
      <span className="absolute left-1/2 top-[30px] -translate-x-1/2 font-mono text-[12px] text-muted">
        CHANCE
      </span>
      <span className="absolute right-0 top-[30px] font-mono text-[12px] text-muted">1</span>
    </div>
  )
}

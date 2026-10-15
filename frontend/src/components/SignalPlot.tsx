import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { SignalSeries } from './signalData.ts'
import { formatSignalNumber, nearestSample } from './signalData.ts'

const TICK = { fill: 'var(--color-muted)', fontFamily: 'var(--font-mono)', fontSize: 13 }
const BOUNDARY = { stroke: 'var(--color-line-strong)', strokeDasharray: '5 5', strokeWidth: 1 }

function SignalTooltip({
  active,
  label,
  series,
  name,
}: {
  active?: boolean
  label?: unknown
  series: SignalSeries
  name: string
}) {
  if (!active || typeof label !== 'number') return null
  const sample = nearestSample(series.points, label)
  const outside = sample.value < sample.low || sample.value > sample.high
  return (
    <div className="max-w-[260px] border border-line-strong bg-panel p-3 font-mono text-id [overflow-wrap:anywhere]">
      <p className="mb-2 text-muted">
        {formatSignalNumber(sample.time)} s · {name}
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt>Value</dt>
        <dd className="text-right">{formatSignalNumber(sample.value)}</dd>
        <dt>Normal low</dt>
        <dd className="text-right">{formatSignalNumber(sample.low)}</dd>
        <dt>Normal high</dt>
        <dd className="text-right">{formatSignalNumber(sample.high)}</dd>
      </dl>
      <p className="mt-2">{outside ? 'Outside normal range' : 'Within normal range'}</p>
    </div>
  )
}

export function SignalPlot({ series, name }: { series: SignalSeries; name: string }) {
  const last = series.points[series.points.length - 1]
  return (
    <div
      className="mt-5 h-[320px] w-full min-w-0 font-mono"
      aria-label={`Signal chart for ${name}`}
    >
      <ResponsiveContainer
        width="100%"
        height="100%"
        minWidth={0}
        initialDimension={{ width: 400, height: 320 }}
      >
        <ComposedChart
          data={series.points}
          margin={{ top: 12, right: 12, bottom: 20, left: 0 }}
          accessibilityLayer
          title={`${name}: signal against its normal band`}
          desc="The white line is the measured signal. The shaded band is the normal range, with dashed low and high boundaries. Amber segments are outside the normal range. Use the arrow keys to inspect samples."
        >
          <CartesianGrid vertical={false} stroke="var(--color-line)" />
          <XAxis
            type="number"
            dataKey="time"
            domain={['dataMin', 'dataMax']}
            tickCount={4}
            tick={TICK}
            tickFormatter={formatSignalNumber}
            axisLine={{ stroke: 'var(--color-line-strong)' }}
            tickLine={false}
            label={{
              value: 'TIME (S)',
              fill: 'var(--color-muted)',
              fontSize: 13,
              position: 'insideBottom',
              offset: -12,
            }}
          />
          <YAxis
            domain={['auto', 'auto']}
            tickCount={4}
            width={68}
            tick={TICK}
            tickFormatter={formatSignalNumber}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            content={(props) => (
              <SignalTooltip
                active={props.active}
                label={props.label}
                series={series}
                name={name}
              />
            )}
            cursor={{ stroke: 'var(--color-line-strong)', strokeDasharray: '3 3' }}
            isAnimationActive={false}
          />
          <Area
            type="linear"
            dataKey="band"
            name="Normal range"
            fill="var(--color-raised)"
            fillOpacity={1}
            stroke="none"
            isAnimationActive={false}
            tooltipType="none"
          />
          <Line
            type="linear"
            dataKey="low"
            name="Normal low"
            {...BOUNDARY}
            dot={false}
            activeDot={false}
            isAnimationActive={false}
            tooltipType="none"
          />
          <Line
            type="linear"
            dataKey="high"
            name="Normal high"
            {...BOUNDARY}
            dot={false}
            activeDot={false}
            isAnimationActive={false}
            tooltipType="none"
          />
          <ReferenceDot
            x={last.time}
            y={last.high}
            r={0}
            stroke="none"
            label={{
              value: last.high === last.low ? 'NORMAL' : 'HIGH',
              position: 'insideTopRight',
              offset: 6,
              ...TICK,
            }}
          />
          {last.high !== last.low && (
            <ReferenceDot
              x={last.time}
              y={last.low}
              r={0}
              stroke="none"
              label={{ value: 'LOW', position: 'insideBottomRight', offset: 6, ...TICK }}
            />
          )}
          <Line
            type="linear"
            dataKey="value"
            name={name}
            stroke="var(--color-text)"
            strokeWidth={1.75}
            dot={series.points.length === 1 ? { r: 3, fill: 'var(--color-text)' } : false}
            activeDot={{ r: 4, fill: 'var(--color-text)', stroke: 'var(--color-panel)' }}
            isAnimationActive={false}
          />
          {series.outside.length > 0 && (
            <Line
              type="linear"
              data={series.outside}
              dataKey="value"
              name="Outside normal range"
              stroke="var(--color-screening)"
              strokeWidth={2.5}
              connectNulls={false}
              dot={series.points.length === 1 ? { r: 4, fill: 'var(--color-screening)' } : false}
              activeDot={false}
              isAnimationActive={false}
              tooltipType="none"
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

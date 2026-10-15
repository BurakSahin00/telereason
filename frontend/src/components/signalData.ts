export type SignalPoint = {
  time: number
  value: number
  low: number
  high: number
  band: [number, number]
}
export type OutsidePoint = { time: number; value: number | null }
export type SignalSeries = {
  points: SignalPoint[]
  outside: OutsidePoint[]
  outsideSamples: number
}

export class SignalDataError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SignalDataError'
  }
}
const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const numbers = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every((n) => typeof n === 'number' && Number.isFinite(n))
const isOutside = (point: SignalPoint) => point.value < point.low || point.value > point.high

function between(a: SignalPoint, b: SignalPoint, fraction: number): SignalPoint {
  if (fraction === 0) return a
  if (fraction === 1) return b
  const value = a.value + (b.value - a.value) * fraction
  const low = a.low + (b.low - a.low) * fraction
  const high = a.high + (b.high - a.high) * fraction
  return { time: a.time + (b.time - a.time) * fraction, value, low, high, band: [low, high] }
}

/** Clip the linear signal to its varying normal band, preserving gaps between excursions. */
export function outsideSegments(points: SignalPoint[]): OutsidePoint[] {
  if (points.length === 1)
    return isOutside(points[0]) ? [{ time: points[0].time, value: points[0].value }] : []
  const result: OutsidePoint[] = []
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1],
      b = points[i]
    const fractions = [0, 1]
    for (const boundary of ['low', 'high'] as const) {
      const start = a.value - a[boundary],
        end = b.value - b[boundary]
      if (start !== end) {
        const crossing = start / (start - end)
        if (crossing > 0 && crossing < 1) fractions.push(crossing)
      }
    }
    const cuts = [...new Set(fractions)].sort((x, y) => x - y)
    for (let j = 1; j < cuts.length; j++) {
      if (!isOutside(between(a, b, (cuts[j - 1] + cuts[j]) / 2))) continue
      const start = between(a, b, cuts[j - 1]),
        end = between(a, b, cuts[j])
      const previous = result[result.length - 1]
      if (!previous || previous.time !== start.time || previous.value !== start.value) {
        if (previous) result.push({ time: start.time, value: null })
        result.push({ time: start.time, value: start.value })
      }
      result.push({ time: end.time, value: end.value })
    }
  }
  return result
}

/** Validate before plotting: a missing sample or band must never become zero. */
export function prepareSignalSeries(
  response: unknown,
  windowId: string,
  name: string,
): SignalSeries | null {
  if (
    !object(response) ||
    response.window_id !== windowId ||
    !numbers(response.t_s) ||
    !Array.isArray(response.channels)
  ) {
    throw new SignalDataError(
      'The signal response does not match this window. Check the backend, then try again.',
    )
  }
  const matches = response.channels.filter((channel) => object(channel) && channel.name === name)
  if (matches.length === 0) return null
  if (matches.length !== 1)
    throw new SignalDataError(
      'The backend returned duplicate signal names. Check the signal data, then try again.',
    )
  const channel = matches[0] as Record<string, unknown>
  const { values, normal_low: lows, normal_high: highs } = channel
  const times = response.t_s
  if (
    !numbers(values) ||
    !numbers(lows) ||
    !numbers(highs) ||
    [values.length, lows.length, highs.length].some((length) => length !== times.length)
  ) {
    throw new SignalDataError(
      'The signal samples and normal band do not align. Check the signal data, then try again.',
    )
  }
  if (times.length === 0) return null
  const points: SignalPoint[] = times.map((time, index) => {
    if ((index > 0 && time <= times[index - 1]) || lows[index] > highs[index]) {
      throw new SignalDataError(
        'The signal times or normal bounds are invalid. Check the signal data, then try again.',
      )
    }
    const value = values[index],
      low = lows[index],
      high = highs[index]
    return { time, value, low, high, band: [low, high] }
  })
  return {
    points,
    outside: outsideSegments(points),
    outsideSamples: points.filter(isOutside).length,
  }
}

/** Tooltips show an actual sample, never an interpolated clipping vertex. */
export function nearestSample(points: SignalPoint[], time: number): SignalPoint {
  let left = 0,
    right = points.length - 1
  while (left < right) {
    const middle = Math.floor((left + right) / 2)
    if (points[middle].time < time) left = middle + 1
    else right = middle
  }
  const next = points[left],
    previous = points[left - 1]
  return previous && time - previous.time <= next.time - time ? previous : next
}

export function formatSignalNumber(value: number): string {
  if (value !== 0 && (Math.abs(value) < 0.001 || Math.abs(value) >= 1_000_000))
    return value.toExponential(3)
  return value.toLocaleString('en-US', { maximumSignificantDigits: 6 })
}

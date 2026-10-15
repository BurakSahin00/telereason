import assert from 'node:assert/strict'
import test from 'node:test'
import {
  formatSignalNumber,
  nearestSample,
  prepareSignalSeries,
  SignalDataError,
} from '../src/components/signalData.ts'

const windowId = 'W-574006'
function response(
  values: number[],
  low = values.map(() => -1),
  high = values.map(() => 1),
  times = values.map((_, i) => i),
) {
  return {
    window_id: windowId,
    t_s: times,
    channels: [{ name: 'signal_a', values, normal_low: low, normal_high: high }],
  }
}
const series = (values: number[], low?: number[], high?: number[], times?: number[]) =>
  prepareSignalSeries(response(values, low, high, times), windowId, 'signal_a')!
const close = (actual: number, expected: number) =>
  assert.ok(Math.abs(actual - expected) < 1e-10, `${actual} != ${expected}`)

test('raw sample values and varying bands are preserved', () => {
  const result = series([0, 2, 0], [-1, -2, -1], [1, 1.5, 1])
  assert.equal(result.outsideSamples, 1)
  assert.deepEqual(
    result.points.map((p) => p.band),
    [
      [-1, 1],
      [-2, 1.5],
      [-1, 1],
    ],
  )
  assert.deepEqual(
    result.points.map((p) => p.value),
    [0, 2, 0],
  )
})

test('a lone excursion is highlighted only from its exact band crossings', () => {
  assert.deepEqual(series([0, 2, 0]).outside, [
    { time: 0.5, value: 1 },
    { time: 1, value: 2 },
    { time: 1.5, value: 1 },
  ])
})

test('jumping from above to below the band leaves the inside section unhighlighted', () => {
  const outside = series([3, -3]).outside
  assert.equal(outside.length, 5)
  close(outside[1].time, 1 / 3)
  close(outside[1].value!, 1)
  assert.equal(outside[2].value, null)
  close(outside[3].time, 2 / 3)
  close(outside[3].value!, -1)
})

test('separate excursions are never connected through normal samples', () => {
  const result = series([0, 2, 0, 2, 0])
  assert.equal(result.outsideSamples, 2)
  assert.equal(result.outside.filter((p) => p.value === null).length, 1)
  assert.equal(result.outside.find((p) => p.value === null)?.time, 2.5)
})

test('continuous outside intervals remain continuous', () => {
  assert.deepEqual(series([2, 3, 2]).outside, [
    { time: 0, value: 2 },
    { time: 1, value: 3 },
    { time: 2, value: 2 },
  ])
})

test('clipping keeps measured endpoints exact across large floating-point differences', () => {
  assert.deepEqual(series([1e16, 1], [0, 0], [0.5, 0.5]).outside, [
    { time: 0, value: 1e16 },
    { time: 1, value: 1 },
  ])
})

test('a changing normal band can cross a constant signal', () => {
  assert.deepEqual(series([0, 0], [-1, -1], [1, -1]).outside, [
    { time: 0.5, value: 0 },
    { time: 1, value: 0 },
  ])
})

test('touching the normal boundaries is within range', () => {
  const result = series([-1, 1, -1])
  assert.equal(result.outsideSamples, 0)
  assert.deepEqual(result.outside, [])
})

test('single samples and zero-width normal bands retain real zero values', () => {
  assert.deepEqual(series([0], [0], [0]).outside, [])
  assert.equal(series([0], [0], [0]).points[0].value, 0)
  assert.deepEqual(series([2]).outside, [{ time: 0, value: 2 }])
})

test('missing channel and empty valid series are empty rather than invented samples', () => {
  assert.equal(prepareSignalSeries(response([1]), windowId, 'unknown'), null)
  assert.equal(prepareSignalSeries(response([]), windowId, 'signal_a'), null)
})

test('wrong window, missing bounds, non-finite values and unequal lengths are rejected', () => {
  const missingBand = response([1])
  delete (missingBand.channels[0] as Partial<(typeof missingBand.channels)[0]>).normal_low
  for (const data of [
    { ...response([1]), window_id: 'W-000000' },
    response([NaN]),
    response([Infinity]),
    response([1], [], [1]),
    missingBand,
    { ...response([1]), t_s: [null] },
  ])
    assert.throws(() => prepareSignalSeries(data, windowId, 'signal_a'), SignalDataError)
})

test('duplicate channels, non-increasing times and reversed bounds are rejected', () => {
  const duplicate = response([1])
  duplicate.channels.push(duplicate.channels[0])
  for (const data of [
    duplicate,
    response([1, 2], [-1, -1], [1, 1], [1, 1]),
    response([1, 2], [-1, -1], [1, 1], [1, 0]),
    response([1], [2], [1]),
  ]) {
    assert.throws(() => prepareSignalSeries(data, windowId, 'signal_a'), SignalDataError)
  }
})

test('tooltips select a measured sample, with clamped ends and stable ties', () => {
  const points = series([0, 2, 0]).points
  assert.equal(nearestSample(points, -5), points[0])
  assert.equal(nearestSample(points, 0.5), points[0])
  assert.equal(nearestSample(points, 0.6), points[1])
  assert.equal(nearestSample(points, 50), points[2])
})

test('tiny measurements never round down to an invented zero', () => {
  assert.equal(formatSignalNumber(0), '0')
  assert.equal(formatSignalNumber(0.00000001), '1.000e-8')
  assert.equal(formatSignalNumber(-0.00000001), '-1.000e-8')
})

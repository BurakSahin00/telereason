// Truncate, never round: a score just under the threshold must never read as the threshold
// (0.8996 shows as 0.899, not 0.90). Two to three decimals, so 1 shows as 1.00 and 0.845 stays 0.845.
const SCORE_FORMAT = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 3,
  roundingMode: 'trunc',
})

/** A screening score as every screen shows it. */
export function formatScore(score: number): string {
  return SCORE_FORMAT.format(score)
}

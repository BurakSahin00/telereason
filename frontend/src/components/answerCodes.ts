import type { StatusTone } from './StatusChip.tsx'

/** Chip word and tone per `answer` code (docs/api.md, "answer is one of these codes"). */
const ANSWER_CODES: Record<string, { label: string; tone?: StatusTone }> = {
  ANSWERED: { label: 'Answered from facts' },
  FACTS_ONLY: { label: 'Facts only', tone: 'closed' },
  DIAGNOSIS_CLOSED: { label: 'Diagnosis closed', tone: 'closed' },
  SCREENING_ONLY: { label: 'Screening only', tone: 'screening' },
  // A refused action is a plain answer, not a fault (DESIGN.md), so no tone here.
  ACTION_REFUSED: { label: 'Action refused' },
  NOT_ANSWERABLE: { label: 'Not answerable' },
}

export function answerCodeInfo(code: string): { label: string; tone?: StatusTone } | undefined {
  return ANSWER_CODES[code]
}

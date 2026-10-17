import assert from 'node:assert/strict'
import test from 'node:test'
import { answerCodeInfo } from '../src/components/answerCodes.ts'

test('known codes map to their chip label and tone', () => {
  assert.deepEqual(answerCodeInfo('ANSWERED'), { label: 'Answered from facts' })
  assert.deepEqual(answerCodeInfo('FACTS_ONLY'), { label: 'Facts only', tone: 'closed' })
  assert.deepEqual(answerCodeInfo('DIAGNOSIS_CLOSED'), {
    label: 'Diagnosis closed',
    tone: 'closed',
  })
  assert.deepEqual(answerCodeInfo('SCREENING_ONLY'), { label: 'Screening only', tone: 'screening' })
  assert.deepEqual(answerCodeInfo('NOT_ANSWERABLE'), { label: 'Not answerable' })
})

test('a refused action carries no tone, since it is a plain answer, not a fault', () => {
  const info = answerCodeInfo('ACTION_REFUSED')
  assert.equal(info?.label, 'Action refused')
  assert.equal(info?.tone, undefined)
})

test('an unknown code renders no chip', () => {
  assert.equal(answerCodeInfo('SOMETHING_NEW'), undefined)
})

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiUrl, getJson, startNightRun } from './client.ts'
import { useHealth } from './queries.ts'

const PROGRESS_REFRESH_MS = 1_000
const ACTIVE_RUN_KEY = `telereason:active-night:${apiUrl('/api/night-runs')}`

function rememberedRun(): string | undefined {
  try {
    return sessionStorage.getItem(ACTIVE_RUN_KEY) || undefined
  } catch {
    return undefined
  }
}

function rememberRun(runId?: string) {
  try {
    if (runId) sessionStorage.setItem(ACTIVE_RUN_KEY, runId)
    else sessionStorage.removeItem(ACTIVE_RUN_KEY)
  } catch {
    // The run can still be followed when browser storage is unavailable.
  }
}

function forgetRun(runId?: string) {
  // A late completion must not erase a newer run started in this tab.
  if (rememberedRun() === runId) rememberRun()
}

export function useSystem() {
  return useQuery({
    queryKey: ['system'],
    queryFn: ({ signal }) => getJson('/api/system', { signal }),
  })
}

export function useNightRun() {
  const queryClient = useQueryClient()
  const health = useHealth()
  const [runId, setRunId] = useState(rememberedRun)
  const start = useMutation({
    mutationFn: () => startNightRun({ max_windows: 300 }),
    // A retry could start a second run if the first response was lost.
    retry: false,
    onSuccess: (run) => {
      queryClient.removeQueries({ queryKey: ['night-run', run.run_id], exact: true })
      rememberRun(run.status === 'done' || run.status === 'failed' ? undefined : run.run_id)
      setRunId(run.run_id)
    },
  })
  const progress = useQuery({
    queryKey: ['night-run', runId],
    queryFn: async ({ signal }) => {
      const run = await getJson('/api/night-runs/{run_id}', {
        path: { run_id: runId! },
        signal,
      })
      // Fake mode replays the stored night; a live result must belong to the requested run.
      if (run.run_id !== runId && health.data?.engine !== 'fake') {
        throw new ApiError(
          502,
          'internal_error',
          'The backend returned progress for a different night run.',
        )
      }
      return run
    },
    enabled: Boolean(runId) && health.isSuccess,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return query.state.status === 'error' || status === 'done' || status === 'failed'
        ? false
        : PROGRESS_REFRESH_MS
    },
  })
  const missing = progress.error instanceof ApiError && progress.error.code === 'not_found'
  const status = progress.data?.status ?? start.data?.status

  useEffect(() => {
    if (status === 'done' || status === 'failed' || missing) forgetRun(runId)
    if (progress.data?.status === 'done') {
      void queryClient.invalidateQueries({ queryKey: ['system'] })
      void queryClient.invalidateQueries({ queryKey: ['report'] })
      void queryClient.invalidateQueries({ queryKey: ['gate'] })
      void queryClient.invalidateQueries({ queryKey: ['window'] })
    }
  }, [runId, status, missing, progress.data?.status, queryClient])

  // A lost progress response must not let the user start another known active run.
  const active = Boolean(runId) && !missing && status !== 'done' && status !== 'failed'
  return { start, progress, active, runId }
}

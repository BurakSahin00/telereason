import { useQuery } from '@tanstack/react-query'
import { getJson } from './client.ts'
import { prepareSignalSeries } from '../components/signalData.ts'

export function useWindowSignals(windowId: string, channel: string) {
  return useQuery({
    queryKey: ['window', windowId, 'signals', channel],
    queryFn: async ({ signal }) => {
      const response = await getJson('/api/windows/{window_id}/signals', {
        path: { window_id: windowId },
        query: { channels: channel },
        signal,
      })
      return prepareSignalSeries(response, windowId, channel)
    },
    enabled: channel.length > 0,
  })
}

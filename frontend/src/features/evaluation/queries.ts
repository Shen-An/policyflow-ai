import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  cleanupEvalDataset,
  createEvalCase,
  createEvalRun,
  createRetrievalItem,
  deleteEvalRun,
  getEvalRun,
  importCrudDataset,
  listEvalCases,
  listEvalRuns,
  listRetrievalItems,
  retrievalDebug,
} from '../../api/eval'

export const evalKeys = {
  all: ['evaluation'] as const,
  cases: () => [...evalKeys.all, 'cases'] as const,
  retrievalItems: () => [...evalKeys.all, 'retrieval-items'] as const,
  runs: () => [...evalKeys.all, 'runs'] as const,
  runList: (page: number, pageSize: number, status: string) =>
    [...evalKeys.runs(), 'list', { page, pageSize, status }] as const,
  run: (id: string) => [...evalKeys.runs(), 'detail', id] as const,
}

export const useEvalCasesQuery = () =>
  useQuery({ queryKey: evalKeys.cases(), queryFn: ({ signal }) => listEvalCases(signal) })

export const useRetrievalItemsQuery = (enabledOnly = true) =>
  useQuery({
    queryKey: [...evalKeys.retrievalItems(), { enabledOnly }] as const,
    queryFn: ({ signal }) => listRetrievalItems(signal, enabledOnly ? true : undefined),
  })

export const useEvalRunsQuery = (page: number, pageSize: number, status: string) =>
  useQuery({
    queryKey: evalKeys.runList(page, pageSize, status),
    queryFn: ({ signal }) => listEvalRuns(page, pageSize, status || undefined, signal),
  })

export function evalRunPollingInterval(status: string | undefined): number | false {
  return status && ['success', 'failed', 'skipped'].includes(status) ? false : 2_000
}

export function useEvalRunQuery(id: string) {
  return useQuery({
    queryKey: evalKeys.run(id),
    queryFn: ({ signal }) => getEvalRun(id, signal),
    enabled: Boolean(id),
    refetchInterval: (query) => evalRunPollingInterval(query.state.data?.status),
    refetchIntervalInBackground: false,
  })
}

function useCreateMutation<TInput>(
  mutationFn: (input: TInput) => Promise<unknown>,
  queryKey: readonly unknown[],
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: async () => queryClient.invalidateQueries({ queryKey }),
  })
}

export const useCreateEvalCaseMutation = () =>
  useCreateMutation(createEvalCase, evalKeys.cases())
export const useCreateRetrievalItemMutation = () =>
  useCreateMutation(createRetrievalItem, evalKeys.retrievalItems())

export function useImportCrudDatasetMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: importCrudDataset,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: evalKeys.cases() }),
        queryClient.invalidateQueries({ queryKey: evalKeys.retrievalItems() }),
        // Refresh KB list so 测试库 document counts / presence update.
        queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] }),
      ])
    },
  })
}

export function useCleanupEvalDatasetMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: cleanupEvalDataset,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: evalKeys.cases() }),
        queryClient.invalidateQueries({ queryKey: evalKeys.retrievalItems() }),
        queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] }),
      ])
    },
  })
}

export function useCreateEvalRunMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: createEvalRun,
    onSuccess: async (run) => {
      queryClient.setQueryData(evalKeys.run(run.id), run)
      await queryClient.invalidateQueries({ queryKey: evalKeys.runs() })
    },
  })
}

export function useDeleteEvalRunMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteEvalRun,
    onSuccess: async (_void, runId) => {
      queryClient.removeQueries({ queryKey: evalKeys.run(runId) })
      await queryClient.invalidateQueries({ queryKey: evalKeys.runs() })
    },
  })
}

export const useRetrievalDebugMutation = () =>
  useMutation({ mutationFn: retrievalDebug })

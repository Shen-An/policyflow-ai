import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getModelProviderSettings, listProviderModels, testModelProvider, updateModelProviderSettings, type ModelCapability, type ModelEndpointSettingsInput } from '../../api/model-settings'
export const modelSettingsKey = ['model-provider-settings'] as const
export function useModelSettingsQuery() { return useQuery({ queryKey: modelSettingsKey, queryFn: ({ signal }) => getModelProviderSettings(signal) }) }
export function useSaveModelSettingsMutation() { const queryClient = useQueryClient(); return useMutation({ mutationFn: ({ capability, input }: { capability: ModelCapability; input: ModelEndpointSettingsInput }) => updateModelProviderSettings(capability, input), onSuccess: async () => queryClient.invalidateQueries({ queryKey: modelSettingsKey }) }) }
export function useProviderModelsMutation() { return useMutation({ mutationFn: listProviderModels }) }
export function useTestModelProviderMutation() { return useMutation({ mutationFn: testModelProvider }) }

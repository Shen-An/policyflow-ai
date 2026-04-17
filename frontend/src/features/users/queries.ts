import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createUser, listUsers, updateUserRoles, type CreateUserInput, type UserListParams } from '../../api/users'
import type { RoleCode } from '../../api/auth'

export const userKeys = {
  all: ['users'] as const,
  list: (params: UserListParams) => [...userKeys.all, params] as const,
}

export function useUsersQuery(params: UserListParams) {
  return useQuery({
    queryKey: userKeys.list(params),
    queryFn: ({ signal }) => listUsers(params, signal),
  })
}

export function useCreateUserMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateUserInput) => createUser(input),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: userKeys.all }) },
  })
}

export function useUpdateRolesMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ userId, roleCodes }: { userId: string; roleCodes: RoleCode[] }) => updateUserRoles(userId, roleCodes),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: userKeys.all }) },
  })
}

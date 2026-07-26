import type { MessageInstance } from 'antd/es/message/interface'

let messageApi: MessageInstance | null = null

// 组件自身已经展示 mutation 错误（内联 Alert / 表单错误 / 自行 message.error）时，
// 在 useMutation 上带上此 meta，跳过全局错误弹层，避免双重提示。
export const selfHandledMutation = { suppressGlobalError: true } as const

export function registerGlobalMessageApi(api: MessageInstance): void {
  messageApi = api
}

export function notifyGlobalError(content: string): void {
  if (messageApi) {
    void messageApi.error(content)
  } else {
    console.error(content)
  }
}

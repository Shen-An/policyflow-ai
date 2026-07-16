import { Modal } from 'antd'
import type { ModalFuncProps } from 'antd'

/**
 * App-styled confirm dialog. Forces no CJK auto-space on buttons so labels
 * like “禁用 / 丢弃” stay one token for a11y queries and visual consistency.
 * Prefer this over bare Modal.confirm / window.confirm.
 */
export function confirmAction(props: ModalFuncProps) {
  return Modal.confirm({
    ...props,
    okButtonProps: {
      autoInsertSpace: false,
      ...props.okButtonProps,
    },
    cancelButtonProps: {
      autoInsertSpace: false,
      ...props.cancelButtonProps,
    },
  })
}

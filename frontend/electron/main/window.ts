import { BrowserWindow } from 'electron'

import { HARDENED_WEB_PREFERENCES } from './hardening'
import { installNavigationGuards } from './security'

export { HARDENED_WEB_PREFERENCES }

export type CreateMainWindowOptions = {
  /** Absolute path to the compiled, sandboxed preload bundle. */
  preloadPath: string
  /** URL the renderer loads and is confined to (app://local/… or the dev server). */
  rendererUrl: string
}

/** Create the single hardened application window and lock its navigation. */
export function createMainWindow(options: CreateMainWindowOptions): BrowserWindow {
  const window = new BrowserWindow({
    width: 1280,
    height: 832,
    minWidth: 960,
    minHeight: 640,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#f1f7f4',
    webPreferences: {
      ...HARDENED_WEB_PREFERENCES,
      preload: options.preloadPath,
    },
  })

  installNavigationGuards(window.webContents, { rendererUrl: options.rendererUrl })

  window.once('ready-to-show', () => window.show())
  void window.loadURL(options.rendererUrl)

  return window
}

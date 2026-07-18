/** Single source of truth for PolicyFlow brand colors.
 *  Direction: cool-gray admin console, white work surfaces,
 *  restrained teal accent — less mint wash, less soft-toy green.
 *  Dark palette keeps the same teal accent on cooler charcoal surfaces.
 */

export const palette = {
  /* restrained teal accent */
  primary: '#0f9a74',
  primaryHover: '#0d8665',
  primaryActive: '#0a7054',
  primarySoft: '#eef8f3',
  primarySoftStrong: '#d8f0e4',
  primaryBorder: '#b4dfcb',
  primaryDeep: '#0a523d',

  success: '#15803d',
  warning: '#c2410c',
  warningSoft: '#fff7ed',
  warningBorder: '#fed7aa',
  danger: '#dc2626',
  info: '#0369a1',

  /* cooler near-neutral ink */
  text: '#18212b',
  textSecondary: '#5a6570',
  textTertiary: '#8a949e',
  textOnPrimary: '#ffffff',

  /* cool gray canvas */
  bgLayout: '#f3f5f4',
  bgSoft: '#e9eeec',
  bgContainer: '#ffffff',
  bgElevated: '#ffffff',
  bgMuted: '#f6f8f7',
  bgInput: '#f7f9f8',
  bgTableHover: '#f3f7f5',

  /* borders */
  border: '#dde3df',
  borderSecondary: '#e8edeb',
  borderStrong: '#c4cdc8',

  /* Light sidebar */
  sidebarBg: '#ffffff',
  sidebarBgSoft: '#f7f9f8',
  sidebarHover: '#f3f7f5',
  sidebarText: '#3d4854',
  sidebarTextMuted: '#8b959e',
  sidebarBorder: '#eef1ef',
  sidebarActiveBg: '#e8f5ef',
  sidebarActiveText: '#0a523d',

  /* secondary accents — use sparingly; prefer primary + neutrals */
  accentTeal: '#0f766e',
  accentBlue: '#0369a1',
  accentPurple: '#6d28d9',
  accentAmber: '#c2410c',
} as const

export const darkPalette = {
  primary: '#34d399',
  primaryHover: '#6ee7b7',
  primaryActive: '#10b981',
  primarySoft: 'rgba(52, 211, 153, 0.12)',
  primarySoftStrong: 'rgba(52, 211, 153, 0.20)',
  primaryBorder: 'rgba(52, 211, 153, 0.28)',
  primaryDeep: '#a7f3d0',

  success: '#4ade80',
  warning: '#fb923c',
  warningSoft: 'rgba(251, 146, 60, 0.12)',
  warningBorder: 'rgba(251, 146, 60, 0.28)',
  danger: '#f87171',
  info: '#38bdf8',

  text: '#e8edf2',
  textSecondary: '#9aa6b2',
  textTertiary: '#6b7785',
  textOnPrimary: '#06281c',

  bgLayout: '#0d1116',
  bgSoft: '#141a21',
  bgContainer: '#151b22',
  bgElevated: '#1a222b',
  bgMuted: '#1a222b',
  bgInput: '#121820',
  bgTableHover: '#1c2530',

  border: '#2a3440',
  borderSecondary: '#222b35',
  borderStrong: '#3a4654',

  sidebarBg: '#11161c',
  sidebarBgSoft: '#171d24',
  sidebarHover: '#1a222b',
  sidebarText: '#b4bec8',
  sidebarTextMuted: '#6b7785',
  sidebarBorder: '#1e262f',
  sidebarActiveBg: 'rgba(52, 211, 153, 0.12)',
  sidebarActiveText: '#a7f3d0',

  accentTeal: '#2dd4bf',
  accentBlue: '#38bdf8',
  accentPurple: '#a78bfa',
  accentAmber: '#fb923c',
} as const

export type Palette = typeof palette

export const gradients = {
  page: 'var(--color-background)',
  login:
    'radial-gradient(circle at 12% 18%, color-mix(in srgb, var(--color-primary) 12%, transparent), transparent 42%), radial-gradient(circle at 88% 82%, color-mix(in srgb, var(--color-info) 8%, transparent), transparent 38%), linear-gradient(180deg, var(--color-background) 0%, var(--color-background-soft) 100%)',
  brandMark: 'linear-gradient(145deg, #1fb888 0%, #0f9a74 52%, #0d8665 100%)',
  sider: 'var(--color-sidebar-bg)',
  card: 'var(--color-surface)',
  cardHead: 'transparent',
  pageHeader: 'transparent',
  noEvidenceCard:
    'linear-gradient(180deg, color-mix(in srgb, var(--color-warning) 10%, var(--color-surface)) 0%, var(--color-surface) 48%)',
  documentContent:
    'linear-gradient(180deg, var(--color-surface) 0%, var(--color-surface-muted) 100%)',
} as const

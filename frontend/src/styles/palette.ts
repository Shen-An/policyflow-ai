/** Single source of truth for PolicyFlow brand colors.
 *  Direction: cool-gray admin console, white work surfaces,
 *  restrained teal accent — less mint wash, less soft-toy green.
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

export type Palette = typeof palette

export const gradients = {
  page: '#f3f5f4',
  login:
    'radial-gradient(circle at 12% 18%, rgba(15,154,116,0.07), transparent 42%), radial-gradient(circle at 88% 82%, rgba(3,105,161,0.04), transparent 38%), linear-gradient(180deg, #f3f5f4 0%, #eef2f0 100%)',
  brandMark: 'linear-gradient(145deg, #1fb888 0%, #0f9a74 52%, #0d8665 100%)',
  sider: '#ffffff',
  card: '#ffffff',
  cardHead: 'transparent',
  pageHeader: 'transparent',
  noEvidenceCard: 'linear-gradient(180deg, #fff7ed 0%, #ffffff 48%)',
  documentContent: 'linear-gradient(180deg, #ffffff 0%, #f6f8f7 100%)',
} as const

/** Single source of truth for PolicyFlow brand colors.
 *  Visual direction: soft mint admin console (reference: pale canvas,
 *  pure white floating work surface, light sidebar, green accent only).
 */

export const palette = {
  /* restrained teal-green accent */
  primary: '#12a37a',
  primaryHover: '#0d8f6a',
  primaryActive: '#0b7a5a',
  primarySoft: '#eef8f3',
  primarySoftStrong: '#d9f0e5',
  primaryBorder: '#b7dfcc',
  primaryDeep: '#0a5c44',

  success: '#16a34a',
  warning: '#d97706',
  warningSoft: '#fffbeb',
  warningBorder: '#fde68a',
  danger: '#dc2626',
  info: '#0284c7',

  /* near-neutral ink */
  text: '#1f2933',
  textSecondary: '#5b6770',
  textTertiary: '#8b959e',
  textOnPrimary: '#ffffff',

  /* soft mint-gray canvas — barely tinted, airy */
  bgLayout: '#eef3f1',
  bgSoft: '#e6eeea',
  bgContainer: '#ffffff',
  bgElevated: '#ffffff',
  bgMuted: '#f5f8f6',
  bgInput: '#f7faf8',
  bgTableHover: '#f3f8f5',

  /* borders almost gray */
  border: '#dce4df',
  borderSecondary: '#e8eeeb',
  borderStrong: '#c5d0ca',

  /* Light sidebar — pure white with mint selection */
  sidebarBg: '#ffffff',
  sidebarBgSoft: '#f7faf8',
  sidebarHover: '#f0f7f3',
  sidebarText: '#334155',
  sidebarTextMuted: '#94a3b8',
  sidebarBorder: '#eef2f0',
  sidebarActiveBg: '#e8f7f0',
  sidebarActiveText: '#0a5c44',

  accentTeal: '#0f766e',
  accentBlue: '#0284c7',
  accentPurple: '#7c3aed',
  accentAmber: '#d97706',
} as const

export type Palette = typeof palette

export const gradients = {
  page: '#eef3f1',
  login:
    'radial-gradient(circle at top left, rgba(18,163,122,0.08), transparent 45%), radial-gradient(circle at bottom right, rgba(2,132,199,0.04), transparent 40%), linear-gradient(180deg, #eef3f1 0%, #f5f8f6 100%)',
  brandMark: 'linear-gradient(135deg, #34c493, #12a37a 55%, #0d8f6a)',
  sider: '#ffffff',
  card: '#ffffff',
  cardHead: 'transparent',
  pageHeader: '#ffffff',
  noEvidenceCard: 'linear-gradient(180deg, #fffbeb 0%, #ffffff 42%)',
  documentContent: 'linear-gradient(180deg, #ffffff 0%, #f7faf8 100%)',
} as const

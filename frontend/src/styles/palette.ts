/** Single source of truth for PolicyFlow brand colors.
 *  Visual direction: pale mint-gray canvas + white floating cards.
 *  Green is an accent only — keep backgrounds near-neutral.
 */

export const palette = {
  /* restrained teal-green accent (not neon) */
  primary: '#0d8f6a',
  primaryHover: '#0b7a5a',
  primaryActive: '#09664b',
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

  /* near-neutral ink (slightly cool, not green-cast) */
  text: '#1f2933',
  textSecondary: '#5b6770',
  textTertiary: '#8b959e',
  textOnPrimary: '#ffffff',

  /* pale mint-gray canvas — barely tinted */
  bgLayout: '#eef4f1',
  bgSoft: '#e4ebe7',
  bgContainer: '#ffffff',
  bgElevated: '#ffffff',
  bgMuted: '#f5f8f6',
  bgInput: '#f7faf8',
  bgTableHover: '#f3f8f5',

  /* borders almost gray, light mint hint */
  border: '#d8e0dc',
  borderSecondary: '#e8eeeb',
  borderStrong: '#c2cdc7',

  /* Light sidebar */
  sidebarBg: '#ffffff',
  sidebarBgSoft: '#f5f8f6',
  sidebarHover: '#f0f7f3',
  sidebarText: '#1f2933',
  sidebarTextMuted: '#7a868f',
  sidebarBorder: '#e8eeeb',
  sidebarActiveBg: '#eef8f3',
  sidebarActiveText: '#0a5c44',

  accentTeal: '#0f766e',
  accentBlue: '#0284c7',
  accentPurple: '#7c3aed',
  accentAmber: '#d97706',
} as const

export type Palette = typeof palette

export const gradients = {
  page:
    'radial-gradient(1000px 420px at 10% -6%, rgba(13, 143, 106, 0.06), transparent 58%), radial-gradient(820px 380px at 100% 0%, rgba(2, 132, 199, 0.04), transparent 50%), #eef4f1',
  login:
    'radial-gradient(circle at top left, rgba(13,143,106,0.08), transparent 45%), radial-gradient(circle at bottom right, rgba(2,132,199,0.05), transparent 40%), linear-gradient(180deg, #eef4f1 0%, #f5f8f6 100%)',
  brandMark: 'linear-gradient(135deg, #2bb88a, #0d8f6a 55%, #0b7a5a)',
  sider: 'linear-gradient(180deg, #ffffff 0%, #fafcfb 100%)',
  card: 'linear-gradient(180deg, #ffffff 0%, #ffffff 100%)',
  cardHead: 'linear-gradient(180deg, rgba(238,248,243,0.45), rgba(255,255,255,0))',
  pageHeader:
    'linear-gradient(135deg, rgba(255,255,255,0.98), rgba(238,248,243,0.55))',
  noEvidenceCard: 'linear-gradient(180deg, #fffbeb 0%, #ffffff 42%)',
  documentContent: 'linear-gradient(180deg, #ffffff 0%, #f7faf8 100%)',
} as const

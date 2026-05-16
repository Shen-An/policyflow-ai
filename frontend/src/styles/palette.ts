/** Single source of truth for PolicyFlow brand colors. */

export const palette = {
  primary: '#4f46e5',
  primaryHover: '#4338ca',
  primaryActive: '#3730a3',
  primarySoft: '#eef2ff',
  primarySoftStrong: '#e0e7ff',
  primaryBorder: '#c7d2fe',
  primaryDeep: '#312e81',

  success: '#16a34a',
  warning: '#d97706',
  warningSoft: '#fffbeb',
  warningBorder: '#fde68a',
  danger: '#dc2626',
  info: '#2563eb',

  text: '#1a2340',
  textSecondary: '#5a6685',
  textTertiary: '#8892ab',
  textOnPrimary: '#ffffff',

  bgLayout: '#e9edf7',
  bgSoft: '#dde4f3',
  bgContainer: '#ffffff',
  bgElevated: '#fcfcff',
  bgMuted: '#f5f7fc',
  bgInput: '#f8faff',
  bgTableHover: '#f5f7ff',

  border: '#d7deee',
  borderSecondary: '#e4e9f5',
  borderStrong: '#b9c3da',

  sidebarBg: '#111827',
  sidebarBgSoft: '#1a2336',
  sidebarHover: '#1f2a40',
  sidebarText: '#d7dff0',
  sidebarTextMuted: '#8090ad',

  accentTeal: '#0f766e',
  accentBlue: '#1d4ed8',
} as const

export type Palette = typeof palette

export const gradients = {
  page:
    'radial-gradient(1200px 500px at 12% -10%, rgba(99, 102, 241, 0.16), transparent 55%), radial-gradient(900px 420px at 100% 0%, rgba(59, 130, 246, 0.10), transparent 45%), #e9edf7',
  login:
    'radial-gradient(circle at top left, rgba(99,102,241,0.22), transparent 42%), radial-gradient(circle at bottom right, rgba(59,130,246,0.14), transparent 38%), linear-gradient(180deg, #e9edf7 0%, #f4f6fb 100%)',
  brandMark: 'linear-gradient(135deg, #6366f1, #4f46e5 55%, #4338ca)',
  sider: 'linear-gradient(180deg, #111827 0%, #0f172a 55%, #111c33 100%)',
  card: 'linear-gradient(180deg, #ffffff 0%, #fbfcff 100%)',
  cardHead: 'linear-gradient(180deg, rgba(238,242,255,0.55), rgba(255,255,255,0))',
  pageHeader:
    'linear-gradient(135deg, rgba(255,255,255,0.92), rgba(238,242,255,0.78))',
  noEvidenceCard: 'linear-gradient(180deg, #fffbeb 0%, #ffffff 42%)',
  documentContent: 'linear-gradient(180deg, #ffffff 0%, #f7f9ff 100%)',
} as const

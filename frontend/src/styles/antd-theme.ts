import type { ThemeConfig } from 'antd'
import { palette } from './palette'

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: palette.primary,
    colorInfo: palette.primary,
    colorSuccess: palette.success,
    colorWarning: palette.warning,
    colorError: palette.danger,
    colorLink: palette.primary,
    borderRadius: 10,
    fontFamily:
      '"Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    colorBgLayout: palette.bgLayout,
    colorBgContainer: palette.bgContainer,
    colorBgElevated: palette.bgContainer,
    colorBorder: palette.border,
    colorBorderSecondary: palette.borderSecondary,
    colorText: palette.text,
    colorTextSecondary: palette.textSecondary,
    colorTextTertiary: palette.textTertiary,
    colorFillAlter: palette.bgMuted,
    colorFillSecondary: '#eef1f8',
    controlOutline: 'rgba(79, 70, 229, 0.12)',
    boxShadow:
      '0 1px 2px 0 rgba(26, 35, 64, 0.04), 0 8px 20px -10px rgba(79, 70, 229, 0.12)',
    boxShadowSecondary:
      '0 10px 28px -12px rgba(79, 70, 229, 0.18), 0 4px 12px -6px rgba(26, 35, 64, 0.08)',
  },
  components: {
    Layout: {
      siderBg: palette.sidebarBg,
      headerBg: 'rgba(255, 255, 255, 0.86)',
      bodyBg: palette.bgLayout,
      triggerBg: palette.sidebarBgSoft,
      headerHeight: 64,
      headerPadding: '0 24px',
    },
    Menu: {
      darkItemBg: palette.sidebarBg,
      darkSubMenuItemBg: palette.sidebarBg,
      darkItemSelectedBg: palette.primary,
      darkItemHoverBg: palette.sidebarHover,
      darkItemColor: 'rgba(215, 223, 240, 0.88)',
      darkItemSelectedColor: palette.textOnPrimary,
      itemBorderRadius: 10,
      itemMarginInline: 10,
      itemHeight: 42,
    },
    Card: {
      borderRadiusLG: 14,
      colorBgContainer: palette.bgContainer,
      colorBorderSecondary: '#e2e8f5',
      paddingLG: 20,
    },
    Button: {
      controlHeight: 36,
      borderRadius: 10,
      primaryShadow: '0 6px 16px rgba(79, 70, 229, 0.24)',
      defaultShadow: '0 1px 2px rgba(26, 35, 64, 0.04)',
      fontWeight: 500,
    },
    Input: {
      controlHeight: 36,
      borderRadius: 10,
      activeBorderColor: palette.primary,
      hoverBorderColor: '#818cf8',
      activeShadow: '0 0 0 3px rgba(79, 70, 229, 0.12)',
    },
    Select: {
      controlHeight: 36,
      borderRadius: 10,
      optionSelectedBg: palette.primarySoft,
    },
    Table: {
      headerBg: palette.primarySoft,
      headerColor: palette.primaryDeep,
      headerSplitColor: palette.primarySoftStrong,
      rowHoverBg: palette.bgTableHover,
      borderColor: palette.borderSecondary,
      headerBorderRadius: 12,
    },
    Tabs: {
      itemSelectedColor: palette.primary,
      inkBarColor: palette.primary,
      itemHoverColor: palette.primaryHover,
    },
    Tag: {
      defaultBg: palette.primarySoft,
      defaultColor: palette.primaryHover,
      borderRadiusSM: 999,
    },
    Modal: {
      borderRadiusLG: 16,
      contentBg: palette.bgContainer,
      headerBg: palette.bgContainer,
    },
    Drawer: {
      colorBgElevated: palette.bgContainer,
    },
    Statistic: {
      titleFontSize: 13,
      contentFontSize: 28,
    },
    Alert: {
      borderRadiusLG: 12,
    },
    Divider: {
      colorSplit: palette.borderSecondary,
    },
    Descriptions: {
      labelBg: palette.primarySoft,
    },
  },
}

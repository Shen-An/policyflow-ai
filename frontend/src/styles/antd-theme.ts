import type { ThemeConfig } from 'antd'
import { palette } from './palette'

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: palette.primary,
    colorInfo: palette.info,
    colorSuccess: palette.success,
    colorWarning: palette.warning,
    colorError: palette.danger,
    colorLink: palette.primary,
    borderRadius: 12,
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
    colorFillSecondary: palette.primarySoft,
    controlOutline: 'rgba(18, 163, 122, 0.12)',
    controlHeight: 36,
    boxShadow:
      '0 1px 2px 0 rgba(31, 41, 51, 0.03), 0 8px 22px -14px rgba(31, 41, 51, 0.08)',
    boxShadowSecondary:
      '0 12px 30px -16px rgba(31, 41, 51, 0.10), 0 4px 12px -6px rgba(31, 41, 51, 0.04)',
  },
  components: {
    Layout: {
      siderBg: palette.sidebarBg,
      headerBg: 'rgba(255, 255, 255, 0.92)',
      bodyBg: palette.bgLayout,
      triggerBg: palette.sidebarBgSoft,
      headerHeight: 64,
      headerPadding: '0 24px',
    },
    Menu: {
      itemBg: 'transparent',
      subMenuItemBg: 'transparent',
      itemSelectedBg: palette.sidebarActiveBg,
      itemHoverBg: palette.sidebarHover,
      itemColor: palette.sidebarText,
      itemSelectedColor: palette.sidebarActiveText,
      itemHoverColor: palette.sidebarActiveText,
      groupTitleColor: palette.sidebarTextMuted,
      itemBorderRadius: 12,
      itemMarginInline: 12,
      itemHeight: 42,
      iconSize: 16,
    },
    Card: {
      borderRadiusLG: 18,
      colorBgContainer: palette.bgContainer,
      colorBorderSecondary: palette.borderSecondary,
      paddingLG: 20,
    },
    Button: {
      controlHeight: 36,
      borderRadius: 12,
      primaryShadow: '0 6px 14px -8px rgba(18, 163, 122, 0.30)',
      defaultShadow: 'none',
      fontWeight: 500,
    },
    Input: {
      controlHeight: 36,
      borderRadius: 12,
      activeBorderColor: palette.primary,
      hoverBorderColor: '#5ec49a',
      activeShadow: '0 0 0 3px rgba(18, 163, 122, 0.10)',
      colorBgContainer: palette.bgInput,
    },
    Select: {
      controlHeight: 36,
      borderRadius: 12,
      optionSelectedBg: palette.primarySoft,
    },
    Table: {
      headerBg: '#f8faf9',
      headerColor: palette.textSecondary,
      headerSplitColor: 'transparent',
      rowHoverBg: palette.bgTableHover,
      borderColor: palette.borderSecondary,
      headerBorderRadius: 12,
      cellPaddingBlock: 14,
      cellPaddingInline: 16,
    },
    Tabs: {
      itemSelectedColor: palette.primary,
      inkBarColor: palette.primary,
      itemHoverColor: palette.primaryHover,
    },
    Tag: {
      defaultBg: palette.primarySoft,
      defaultColor: palette.primaryDeep,
      borderRadiusSM: 999,
    },
    Modal: {
      borderRadiusLG: 18,
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
      borderRadiusLG: 14,
    },
    Divider: {
      colorSplit: palette.borderSecondary,
    },
    Descriptions: {
      labelBg: palette.bgMuted,
    },
    Pagination: {
      itemActiveBg: palette.primarySoft,
      borderRadius: 10,
    },
  },
}

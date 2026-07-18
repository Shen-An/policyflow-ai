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
    borderRadius: 10,
    fontFamily:
      '"Geist Variable", "Geist", "Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif',
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
    controlOutline: 'rgba(15, 154, 116, 0.12)',
    controlHeight: 36,
    boxShadow:
      '0 1px 2px 0 rgba(24, 33, 43, 0.03), 0 6px 18px -12px rgba(24, 33, 43, 0.08)',
    boxShadowSecondary:
      '0 12px 28px -16px rgba(24, 33, 43, 0.10), 0 4px 10px -6px rgba(24, 33, 43, 0.04)',
  },
  components: {
    Layout: {
      siderBg: palette.sidebarBg,
      headerBg: 'rgba(255, 255, 255, 0.86)',
      bodyBg: palette.bgLayout,
      triggerBg: palette.sidebarBgSoft,
      headerHeight: 56,
      headerPadding: '0 20px',
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
      itemBorderRadius: 10,
      itemMarginInline: 10,
      itemHeight: 40,
      iconSize: 16,
    },
    Card: {
      borderRadiusLG: 16,
      colorBgContainer: palette.bgContainer,
      colorBorderSecondary: palette.borderSecondary,
      paddingLG: 20,
    },
    Button: {
      controlHeight: 36,
      borderRadius: 10,
      primaryShadow: '0 6px 14px -8px rgba(15, 154, 116, 0.28)',
      defaultShadow: 'none',
      fontWeight: 500,
    },
    Input: {
      controlHeight: 36,
      borderRadius: 10,
      activeBorderColor: palette.primary,
      hoverBorderColor: '#4db894',
      activeShadow: '0 0 0 3px rgba(15, 154, 116, 0.10)',
      colorBgContainer: palette.bgInput,
    },
    Select: {
      controlHeight: 36,
      borderRadius: 10,
      optionSelectedBg: palette.primarySoft,
    },
    Table: {
      headerBg: '#f7f9f8',
      headerColor: palette.textSecondary,
      headerSplitColor: 'transparent',
      rowHoverBg: palette.bgTableHover,
      borderColor: palette.borderSecondary,
      headerBorderRadius: 10,
      cellPaddingBlock: 11,
      cellPaddingInline: 14,
    },
    Tabs: {
      itemSelectedColor: palette.primary,
      inkBarColor: palette.primary,
      itemHoverColor: palette.primaryHover,
    },
    Tag: {
      defaultBg: palette.primarySoft,
      defaultColor: palette.primaryDeep,
      borderRadiusSM: 6,
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
      titleFontSize: 12,
      contentFontSize: 26,
    },
    Alert: {
      borderRadiusLG: 12,
    },
    Divider: {
      colorSplit: palette.borderSecondary,
    },
    Descriptions: {
      labelBg: palette.bgMuted,
    },
    Pagination: {
      itemActiveBg: palette.primarySoft,
      borderRadius: 8,
    },
  },
}

import type { ThemeConfig } from 'antd'
import { darkPalette, palette } from './palette'

const fontFamily =
  '"Geist Variable", "Geist", "Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans SC", sans-serif'

function buildTheme(p: typeof palette | typeof darkPalette, mode: 'light' | 'dark'): ThemeConfig {
  const isDark = mode === 'dark'
  return {
    token: {
      colorPrimary: p.primary,
      colorInfo: p.info,
      colorSuccess: p.success,
      colorWarning: p.warning,
      colorError: p.danger,
      colorLink: p.primary,
      borderRadius: 10,
      fontFamily,
      colorBgLayout: p.bgLayout,
      colorBgContainer: p.bgContainer,
      colorBgElevated: p.bgElevated,
      colorBorder: p.border,
      colorBorderSecondary: p.borderSecondary,
      colorText: p.text,
      colorTextSecondary: p.textSecondary,
      colorTextTertiary: p.textTertiary,
      colorFillAlter: p.bgMuted,
      colorFillSecondary: p.primarySoft,
      controlOutline: isDark ? 'rgba(52, 211, 153, 0.18)' : 'rgba(15, 154, 116, 0.12)',
      controlHeight: 36,
      boxShadow: isDark
        ? '0 1px 2px 0 rgba(0, 0, 0, 0.24), 0 8px 20px -14px rgba(0, 0, 0, 0.45)'
        : '0 1px 2px 0 rgba(24, 33, 43, 0.03), 0 6px 18px -12px rgba(24, 33, 43, 0.08)',
      boxShadowSecondary: isDark
        ? '0 14px 32px -18px rgba(0, 0, 0, 0.55), 0 6px 14px -8px rgba(0, 0, 0, 0.35)'
        : '0 12px 28px -16px rgba(24, 33, 43, 0.10), 0 4px 10px -6px rgba(24, 33, 43, 0.04)',
    },
    components: {
      Layout: {
        siderBg: p.sidebarBg,
        headerBg: isDark ? 'rgba(17, 22, 28, 0.88)' : 'rgba(255, 255, 255, 0.86)',
        bodyBg: p.bgLayout,
        triggerBg: p.sidebarBgSoft,
        headerHeight: 56,
        headerPadding: '0 20px',
      },
      Menu: {
        itemBg: 'transparent',
        subMenuItemBg: 'transparent',
        itemSelectedBg: p.sidebarActiveBg,
        itemHoverBg: p.sidebarHover,
        itemColor: p.sidebarText,
        itemSelectedColor: p.sidebarActiveText,
        itemHoverColor: p.sidebarActiveText,
        groupTitleColor: p.sidebarTextMuted,
        itemBorderRadius: 10,
        itemMarginInline: 10,
        itemHeight: 40,
        iconSize: 16,
      },
      Card: {
        borderRadiusLG: 16,
        colorBgContainer: p.bgContainer,
        colorBorderSecondary: p.borderSecondary,
        paddingLG: 20,
      },
      Button: {
        controlHeight: 36,
        borderRadius: 10,
        primaryShadow: isDark
          ? '0 6px 14px -8px rgba(52, 211, 153, 0.28)'
          : '0 6px 14px -8px rgba(15, 154, 116, 0.28)',
        defaultShadow: 'none',
        fontWeight: 500,
      },
      Input: {
        controlHeight: 36,
        borderRadius: 10,
        activeBorderColor: p.primary,
        hoverBorderColor: isDark ? '#34d399' : '#4db894',
        activeShadow: isDark
          ? '0 0 0 3px rgba(52, 211, 153, 0.14)'
          : '0 0 0 3px rgba(15, 154, 116, 0.10)',
        colorBgContainer: p.bgInput,
      },
      Select: {
        controlHeight: 36,
        borderRadius: 10,
        optionSelectedBg: p.primarySoft,
      },
      Table: {
        headerBg: isDark ? '#171d24' : '#f7f9f8',
        headerColor: p.textSecondary,
        headerSplitColor: 'transparent',
        rowHoverBg: p.bgTableHover,
        borderColor: p.borderSecondary,
        headerBorderRadius: 10,
        cellPaddingBlock: 11,
        cellPaddingInline: 14,
      },
      Tabs: {
        itemSelectedColor: p.primary,
        inkBarColor: p.primary,
        itemHoverColor: p.primaryHover,
      },
      Tag: {
        defaultBg: p.primarySoft,
        defaultColor: p.primaryDeep,
        borderRadiusSM: 6,
      },
      Modal: {
        borderRadiusLG: 16,
        contentBg: p.bgContainer,
        headerBg: p.bgContainer,
      },
      Drawer: {
        colorBgElevated: p.bgContainer,
      },
      Statistic: {
        titleFontSize: 12,
        contentFontSize: 26,
      },
      Alert: {
        borderRadiusLG: 12,
      },
      Divider: {
        colorSplit: p.borderSecondary,
      },
      Descriptions: {
        labelBg: p.bgMuted,
      },
      Pagination: {
        itemActiveBg: p.primarySoft,
        borderRadius: 8,
      },
    },
  }
}

export const antdThemeLight = buildTheme(palette, 'light')
export const antdThemeDark = buildTheme(darkPalette, 'dark')

/** @deprecated Prefer antdThemeLight / antdThemeDark via ThemeRoot */
export const antdTheme = antdThemeLight

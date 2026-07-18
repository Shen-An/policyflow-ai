from pathlib import Path
import re

MAP = {
  'AlertOutlined': 'Warning',
  'ApiOutlined': 'Plugs',
  'ArrowLeftOutlined': 'ArrowLeft',
  'ArrowRightOutlined': 'ArrowRight',
  'AuditOutlined': 'ClipboardText',
  'BarChartOutlined': 'ChartBar',
  'BookOutlined': 'BookOpen',
  'BugOutlined': 'Bug',
  'CheckCircleOutlined': 'CheckCircle',
  'CloseCircleOutlined': 'XCircle',
  'CloseOutlined': 'X',
  'CloudServerOutlined': 'Cloud',
  'ClusterOutlined': 'SquaresFour',
  'CopyOutlined': 'Copy',
  'DatabaseOutlined': 'Database',
  'DeleteOutlined': 'Trash',
  'DownloadOutlined': 'Download',
  'EditOutlined': 'PencilSimple',
  'EyeOutlined': 'Eye',
  'FileSearchOutlined': 'FileMagnifyingGlass',
  'FileTextOutlined': 'FileText',
  'FormOutlined': 'NotePencil',
  'HistoryOutlined': 'ClockCounterClockwise',
  'InfoCircleOutlined': 'Info',
  'KeyOutlined': 'Key',
  'LoadingOutlined': 'CircleNotch',
  'LockOutlined': 'Lock',
  'LogoutOutlined': 'SignOut',
  'MenuFoldOutlined': 'SidebarSimple',
  'MenuUnfoldOutlined': 'List',
  'MessageOutlined': 'ChatCircle',
  'PlusOutlined': 'Plus',
  'ReloadOutlined': 'ArrowClockwise',
  'SafetyCertificateOutlined': 'ShieldCheck',
  'SaveOutlined': 'FloppyDisk',
  'SearchOutlined': 'MagnifyingGlass',
  'SendOutlined': 'PaperPlaneTilt',
  'SettingOutlined': 'GearSix',
  'TeamOutlined': 'Users',
  'ThunderboltOutlined': 'Lightning',
  'ToolOutlined': 'Wrench',
  'UploadOutlined': 'UploadSimple',
  'UserOutlined': 'User',
  'WarningOutlined': 'Warning',
  'WifiOutlined': 'WifiHigh',
}

LUCIDE_MAP = {
  'Activity': 'Pulse',
  'AlertTriangle': 'Warning',
  'CheckCircle2': 'CheckCircle',
  'Construction': 'HardHat',
  'LoaderCircle': 'CircleNotch',
  'RefreshCw': 'ArrowClockwise',
  'ShieldX': 'ShieldSlash',
  'Upload': 'UploadSimple',
  'X': 'X',
  'Search': 'MagnifyingGlass',
}

SPIN_ICONS = {'CircleNotch'}

root = Path('frontend/src')
files = [p for p in root.rglob('*.tsx') if not p.name.endswith('.test.tsx')]


def insert_phosphor_import(text: str, phosphor_names: list[str]) -> str:
    if "from '@phosphor-icons/react'" in text:
        def merge(m):
            existing = re.findall(r'\b([A-Za-z0-9_]+)\b', m.group(1))
            merged = sorted(set(existing) | set(phosphor_names))
            return "import { " + ", ".join(merged) + " } from '@phosphor-icons/react'"
        return re.sub(
            r"import\s*\{([^}]+)\}\s*from\s*'@phosphor-icons/react'",
            merge,
            text,
            count=1,
        )
    import_line = "import { " + ", ".join(sorted(phosphor_names)) + " } from '@phosphor-icons/react'\n"
    lines = text.splitlines(True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith('import '):
            insert_at = i
            break
    lines.insert(insert_at, import_line)
    return ''.join(lines)


def replace_icon_tags(text: str, old: str, new: str, spin: bool = False) -> str:
    weight = (
        'weight="regular"'
        if new in {'Plus', 'X', 'MagnifyingGlass', 'ArrowLeft', 'ArrowRight', 'List', 'SidebarSimple'}
        else 'weight="duotone"'
    )

    def repl(m):
        attrs = m.group(1) or ''
        keep = []
        for a in re.findall(
            r'(style=\{[^}]+\})|(className="[^"]*")|(className=\{[^}]+\})|(aria-[a-zA-Z-]+="[^"]*")|(aria-hidden(?:="[^"]*")?)',
            attrs,
        ):
            keep.append(next(x for x in a if x))
        classes = []
        if spin or 'spin' in attrs or 'animate-spin' in attrs:
            classes.append('animate-spin')
        for c in re.findall(r'className="([^"]*)"', attrs):
            classes.extend(c.split())
        for c in re.findall(r"className=\{`([^`]*)`\}", attrs):
            classes.extend(c.split())
        # rebuild without duplicate className entries from keep
        keep = [k for k in keep if not k.startswith('className=')]
        base = f'<{new} size={{16}} {weight}'
        if classes:
            # preserve template classes roughly
            unique = []
            for c in classes:
                if c not in unique:
                    unique.append(c)
            base += f' className="{" ".join(unique)}"'
        if keep:
            base += ' ' + ' '.join(keep)
        return base + ' />'

    text = re.sub(rf'<{old}(\s[^/>]*)?\s*/>', repl, text)
    text = re.sub(rf'<{old}(\s[^>]*)?>\s*</{old}>', repl, text)
    return text


def rewrite_ant(text: str) -> tuple[str, bool]:
    if "@ant-design/icons" not in text:
        return text, False
    names = []
    for m in re.finditer(r"import\s*\{([^}]+)\}\s*from\s*'@ant-design/icons'", text, re.S):
        names.extend(re.findall(r'\b([A-Za-z0-9_]+)\b', m.group(1)))
    names = [n for n in names if n in MAP]
    if not names:
        return text, False
    text2 = re.sub(r"import\s*\{[^}]+\}\s*from\s*'@ant-design/icons'\s*;?\n?", '', text)
    phosphor_names = sorted({MAP[n] for n in names})
    text2 = insert_phosphor_import(text2, phosphor_names)
    for ant, ph in MAP.items():
        text2 = replace_icon_tags(text2, ant, ph, spin=(ant == 'LoadingOutlined'))
    return text2, True


def rewrite_lucide_product(text: str, path: Path) -> tuple[str, bool]:
    rel = path.as_posix()
    if '/components/ui/' in rel:
        return text, False
    if "from 'lucide-react'" not in text and 'from "lucide-react"' not in text:
        return text, False
    names = []
    for m in re.finditer(r'import\s*\{([^}]+)\}\s*from\s*[\'"]lucide-react[\'"]', text, re.S):
        names.extend(re.findall(r'\b([A-Za-z0-9_]+)\b', m.group(1)))
    names = [n for n in names if n in LUCIDE_MAP]
    if not names:
        return text, False
    text2 = re.sub(r'import\s*\{[^}]+\}\s*from\s*[\'"]lucide-react[\'"]\s*;?\n?', '', text)
    phosphor_names = sorted({LUCIDE_MAP[n] for n in names})
    text2 = insert_phosphor_import(text2, phosphor_names)
    for luc, ph in LUCIDE_MAP.items():
        text2 = replace_icon_tags(text2, luc, ph, spin=(luc in {'LoaderCircle', 'RefreshCw'}))
        text2 = replace_icon_tags(text2, luc + 'Icon', ph, spin=(luc in {'LoaderCircle', 'RefreshCw'}))
    return text2, True


changed = []
for p in files:
    text = p.read_text(encoding='utf-8')
    orig = text
    text, _ = rewrite_ant(text)
    text, _ = rewrite_lucide_product(text, p)
    if text != orig:
        p.write_text(text, encoding='utf-8', newline='\n')
        changed.append(str(p))

print('changed', len(changed))
for c in changed:
    print(c)

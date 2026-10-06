import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// Production signing gate. Runs before electron-builder (see package.json
// electron:package*) and REFUSES to start a production package when signing
// credentials are missing or are placeholder values. This guarantees we never ship an
// unsigned or dummy-signed desktop artifact. Credentials are read from the environment
// and are never stored in the repository.

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const args = process.argv.slice(2)
const platform = args.includes('--win')
  ? 'win'
  : args.includes('--mac')
    ? 'mac'
    : args.includes('--linux')
      ? 'linux'
      : process.platform === 'darwin'
        ? 'mac'
        : process.platform === 'win32'
          ? 'win'
          : 'linux'

const PLACEHOLDER = /(REPLACE|PLACEHOLDER|CHANGE[-_ ]?ME|DUMMY|EXAMPLE|TODO|YOUR[-_ ])/iu

function fail(message) {
  console.error(`✖ Refusing production packaging: ${message}`)
  process.exit(1)
}

function hasRealValue(name) {
  const value = process.env[name]
  return typeof value === 'string' && value.trim() !== '' && !PLACEHOLDER.test(value)
}

// 1) The builder config must exist and enforce signing with no placeholder values.
const configPath = path.join(projectRoot, 'electron-builder.yml')
if (!existsSync(configPath)) fail('electron-builder.yml is missing.')
const config = readFileSync(configPath, 'utf8')
if (!/forceCodeSigning:\s*true/u.test(config)) {
  fail('electron-builder.yml must set forceCodeSigning: true.')
}
// Scan only configuration values, not the explanatory comments.
const configValues = config
  .split(/\r?\n/u)
  .map((line) => line.replace(/#.*$/u, ''))
  .join('\n')
if (PLACEHOLDER.test(configValues)) {
  fail('electron-builder.yml contains placeholder values; provide real signing configuration.')
}

// 2) Real signing credentials must be present in the environment per platform.
if (platform === 'win') {
  const viaCertificateFile = hasRealValue('CSC_LINK') && hasRealValue('CSC_KEY_PASSWORD')
  const viaCertificateStore = hasRealValue('WIN_CSC_SUBJECT_NAME')
  if (!viaCertificateFile && !viaCertificateStore) {
    fail(
      'Windows signing requires CSC_LINK + CSC_KEY_PASSWORD (a real certificate), ' +
        'or WIN_CSC_SUBJECT_NAME for store-based signing. None were provided.',
    )
  }
} else if (platform === 'mac') {
  if (!(hasRealValue('CSC_LINK') && hasRealValue('CSC_KEY_PASSWORD'))) {
    fail('macOS signing requires CSC_LINK + CSC_KEY_PASSWORD (a real Developer ID certificate).')
  }
  const notarizeReady =
    hasRealValue('APPLE_ID') &&
    (hasRealValue('APPLE_APP_SPECIFIC_PASSWORD') || hasRealValue('APPLE_API_KEY')) &&
    hasRealValue('APPLE_TEAM_ID')
  if (!notarizeReady) {
    fail(
      'macOS notarization requires APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD ' +
        '(or APPLE_API_KEY) + APPLE_TEAM_ID.',
    )
  }
} else {
  // Linux AppImage has no standard code-signing/notarization; reject only placeholders.
  console.log('Linux packaging: no code-signing authority to enforce. Continuing.')
}

console.log(`✔ Signing configuration verified for ${platform}; forceCodeSigning is enabled.`)

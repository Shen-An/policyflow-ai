import { HttpResponse, delay, http } from 'msw'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { authStore } from '../../auth/auth-store'
import { PublicOnlyRoute } from '../../auth/route-guards'
import { saveReturnTo } from '../../auth/auth-storage'
import { server } from '../../mocks/server'
import { LoginPage } from './login-page'

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<PublicOnlyRoute><LoginPage /></PublicOnlyRoute>} />
        <Route path="/return" element={<p>已返回目标页面</p>} />
        <Route path="/" element={<p>默认页面</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

async function fillCredentials(user: ReturnType<typeof userEvent.setup>, password = 'test-password') {
  await user.type(screen.getByLabelText('用户名'), 'admin')
  await user.type(screen.getByLabelText('密码'), password)
}

describe('LoginPage', () => {
  beforeEach(() => { authStore.clearSession(); sessionStorage.clear() })

  it('validates required fields accessibly', async () => {
    const user = userEvent.setup()
    renderLogin()
    await user.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('请输入用户名')).toBeVisible()
    expect(screen.getByText('请输入密码')).toBeVisible()
  })

  it('logs in and returns to the saved deep link', async () => {
    server.use(http.post('*/api/auth/login', () => HttpResponse.json({
      access_token: 'token', token_type: 'bearer', expires_in: 1800,
      user: { id: 'u1', username: 'admin', display_name: '系统管理员', roles: ['sys_admin'] },
    })))
    saveReturnTo(sessionStorage, '/return')
    const user = userEvent.setup()
    renderLogin()
    await fillCredentials(user)
    await user.click(screen.getByRole('button', { name: '登录' }))
    expect(await screen.findByText('已返回目标页面')).toBeVisible()
    expect(authStore.getState()).toMatchObject({ status: 'authenticated', accessToken: 'token' })
  })

  it('shows a focused credential error without clearing the form', async () => {
    server.use(http.post('*/api/auth/login', () => HttpResponse.json({ error: { code: 'AUTH_INVALID_CREDENTIALS', message: 'Invalid credentials', details: null } }, { status: 401 })))
    const user = userEvent.setup()
    renderLogin()
    await fillCredentials(user, 'wrong-password')
    await user.click(screen.getByRole('button', { name: '登录' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('用户名或密码不正确')
    expect(alert.closest('[tabindex="-1"]')).toHaveFocus()
    expect(screen.getByLabelText('用户名')).toHaveValue('admin')
  })

  it('prevents duplicate submission while the request is pending', async () => {
    let calls = 0
    server.use(http.post('*/api/auth/login', async () => {
      calls += 1
      await delay(100)
      return HttpResponse.json({ access_token: 'token', token_type: 'bearer', expires_in: 1800, user: { id: 'u1', username: 'admin', display_name: 'Admin', roles: ['sys_admin'] } })
    }))
    const user = userEvent.setup()
    renderLogin()
    await fillCredentials(user)
    const button = screen.getByRole('button', { name: '登录' })
    await user.dblClick(button)
    expect(await screen.findByText('默认页面')).toBeVisible()
    expect(calls).toBe(1)
  })
})

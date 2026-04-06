import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { authStore } from './auth-store'
import { consumeReturnTo } from './auth-storage'
import { ProtectedRoute, RoleGuard } from './route-guards'

function LocationView() { const location = useLocation(); return <p>{location.pathname}</p> }

describe('route guards', () => {
  beforeEach(() => { authStore.clearSession(); sessionStorage.clear() })

  it('saves an anonymous deep link and redirects to login', () => {
    render(<MemoryRouter initialEntries={['/admin/users?keyword=a']}><Routes>
      <Route path="/admin/users" element={<ProtectedRoute><p>private</p></ProtectedRoute>} />
      <Route path="/login" element={<LocationView />} />
    </Routes></MemoryRouter>)
    expect(screen.getByText('/login')).toBeVisible()
    expect(consumeReturnTo(sessionStorage)).toBe('/admin/users?keyword=a')
  })

  it('redirects an employee away from a sys_admin route', () => {
    authStore.authenticate('token', Date.now() + 60_000, { id: 'u2', username: 'employee', displayName: 'Employee', roles: ['employee'] })
    render(<MemoryRouter initialEntries={['/admin/users']}><Routes>
      <Route path="/admin/users" element={<RoleGuard required={['sys_admin']}><p>admin page</p></RoleGuard>} />
      <Route path="/forbidden" element={<LocationView />} />
    </Routes></MemoryRouter>)
    expect(screen.getByText('/forbidden')).toBeVisible()
  })
})

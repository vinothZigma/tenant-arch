// The super admin console (/superadmin/login, /superadmin/*), same UI as the
// dynamic-db reference app. Companies use the existing IWMS screens instead
// (/<slug>/login -> pages/CompanyAuth.tsx); the console's "Open workspace"
// opens /<slug>#as=<token>, which shows those screens as the company's admin.
// Everything renders inside .tn so tenancy.css never touches the IWMS screens.
import { Route, Routes } from 'react-router-dom'

import './tenancy.css'
import SuperAdminApp from './pages/SuperAdminApp'
import { ToastProvider } from './ui'

export default function TenancyRoot() {
  return (
    <div className="tn">
      <ToastProvider>
        <Routes>
          <Route path="/superadmin/*" element={<SuperAdminApp />} />
        </Routes>
      </ToastProvider>
    </div>
  )
}

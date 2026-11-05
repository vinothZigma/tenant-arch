// Which company the IWMS screens work on (multi-tenancy, one DB per company).
//
// A company user signs in at /<slug>/login: the slug is remembered here and
// every API call goes to /api/t/<slug>/... instead of /api/v1/... The backend
// keeps that company's login in an HttpOnly cookie scoped to /api/t/<slug>/,
// so the screens (old IWMS design) only ever see that company's database.
//
// The super admin's "Open workspace" (from /superadmin) opens /<slug>#as=<token>:
// the company and a Bearer token are kept for that browser tab only
// (sessionStorage), so it never replaces a real login in other tabs.
// Signing in at /auth (no slug) clears the company: the backend then finds it
// from the username, as before.

const COMPANY_KEY = "iwms:company";
const TAB_TOKEN_KEY = "iwms:company-tab-token";

const read = (s: () => Storage, k: string) => { try { return s().getItem(k); } catch { return null; } };
const write = (s: () => Storage, k: string, v: string | null) => {
  try { if (v === null) s().removeItem(k); else s().setItem(k, v); } catch { /* private mode */ }
};

/** The company of this tab (super admin view) or of this browser. */
export const getCompanySlug = (): string | null =>
  read(() => sessionStorage, COMPANY_KEY) ?? read(() => localStorage, COMPANY_KEY);

export const setCompanySlug = (slug: string | null): void => {
  write(() => localStorage, COMPANY_KEY, slug);
  write(() => sessionStorage, COMPANY_KEY, null);
  write(() => sessionStorage, TAB_TOKEN_KEY, null);
};

/** Super admin viewing a company as one of its admins: this tab only. */
export const startTabCompanyView = (slug: string, token: string): void => {
  write(() => sessionStorage, COMPANY_KEY, slug);
  write(() => sessionStorage, TAB_TOKEN_KEY, token);
};

export const getTabToken = (): string | null => read(() => sessionStorage, TAB_TOKEN_KEY);

export const endTabCompanyView = (): void => {
  write(() => sessionStorage, COMPANY_KEY, null);
  write(() => sessionStorage, TAB_TOKEN_KEY, null);
};

/** "http://host/api/v1" -> "http://host/api/t/<slug>" while a company is selected. */
export const apiBaseFor = (apiRoot: string): string => {
  const slug = getCompanySlug();
  return slug ? apiRoot.replace(/\/api\/v1\/?$/, `/api/t/${encodeURIComponent(slug)}`) : apiRoot;
};

/** Where to send a signed-out user: the company's own login page, or /auth. */
export const loginPath = (): string => {
  const slug = getCompanySlug();
  return slug ? `/${slug}/login` : "/auth";
};

/** Where to go after signing out; ends a super admin's company view. */
export const afterSignOutPath = (): string => {
  if (getTabToken()) {
    endTabCompanyView();
    return "/superadmin/companies";
  }
  return loginPath();
};

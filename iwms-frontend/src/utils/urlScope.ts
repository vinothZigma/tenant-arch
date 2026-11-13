// Company + project prefix on every IWMS URL (multi-tenancy):
//
//   /<company>/<project>/login              sign-in for that company, opening that project
//   /<company>/<project>/admin              ... and every other IWMS screen, e.g.
//   /<company>/<project>/U2FsdGVkX1.../...  encrypted master/module routes
//
// The prefix is the React Router `basename` (AppProviders), so the IWMS
// routes and every navigate("/admin") stay exactly as they were: the router
// adds and strips the prefix. The project part is a slug of the project's
// name ("Nagpur City" -> "nagpur-city"); see projectSlugs().
//
// Changing company/project (sign-in from /<company>/login, the project picker)
// calls moveToScope(): it rewrites the URL and remounts the router with the new
// basename, without reloading the page.
import { getCompanySlug, getTabToken, setCompanySlug } from "./companyScope";

export type UrlScope = { company: string; project: string };

// First URL segments that are IWMS routes, never a company or project slug.
// Encrypted IWMS segments (CryptoJS AES, "U2FsdGVkX1...") contain capitals,
// so the lowercase slug pattern never matches them.
const RESERVED = new Set([
  "", "auth", "login", "admin", "dashboard", "district", "localbody", "audits",
  "api", "static", "media", "superadmin",
]);
const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$/;

const isSlug = (s: string | undefined): s is string => !!s && SLUG.test(s) && !RESERVED.has(s);

const parse = (pathname: string): UrlScope | null => {
  const [, company, project] = pathname.split("/");
  return isSlug(company) && isSlug(project) ? { company, project } : null;
};

let scope: UrlScope | null =
  typeof window === "undefined" ? null : parse(window.location.pathname);
const listeners = new Set<() => void>();

export const getUrlScope = (): UrlScope | null => scope;

/** "/<company>/<project>", or "" when the URL has no company/project. */
export const routerBasename = (): string =>
  scope ? `/${scope.company}/${scope.project}` : "";

export const subscribeUrlScope = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

/** A full browser path without the /<company>/<project> prefix. */
export const toAppPath = (fullPath: string): string => {
  const base = routerBasename();
  if (!base) return fullPath;
  if (fullPath === base) return "/";
  return fullPath.startsWith(`${base}/`) ? fullPath.slice(base.length) : fullPath;
};

/** window.location.pathname as the IWMS routes see it (prefix removed). */
export const appPathname = (): string => toAppPath(window.location.pathname);

/**
 * Called once before the app starts: a URL that names a company also selects
 * that company's API (/api/t/<company>/), so a reload or a bookmarked link
 * restores the right session.
 */
export const initUrlScope = (): void => {
  if (!scope) return;
  const viewingThisCompany = getTabToken() && getCompanySlug() === scope.company;
  if (!viewingThisCompany && getCompanySlug() !== scope.company) setCompanySlug(scope.company);
};

/**
 * Show `path` (an IWMS route such as "/admin") under another company/project
 * (or under no prefix when `next` is null), keeping the page loaded.
 */
export const moveToScope = (next: UrlScope | null, path = appPathname()): void => {
  const base = next ? `/${next.company}/${next.project}` : "";
  const url = `${base}${path === "/" && base ? "/" : path}${window.location.search}${window.location.hash}`;
  window.history.replaceState(window.history.state, "", url);
  scope = next;
  listeners.forEach((l) => l());
};

/** Leave the IWMS routes (e.g. for /superadmin): a full page load. */
export const goOutsideScope = (path: string, navigate: (to: string, opts?: { replace?: boolean }) => void): void => {
  if (scope) window.location.assign(path);
  else navigate(path, { replace: true });
};

/** "Nagpur City" -> "nagpur-city". */
export const slugifyName = (name: string): string =>
  name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 50)
    .replace(/-+$/, "");

/**
 * URL slug of each project (unique_id -> slug). The login response carries
 * each project's slug, worked out by the backend over ALL the company's
 * projects (app/tenancy/project_slugs.py); otherwise (developer / console
 * lists, which hold every project) they are computed here by the same rule:
 * slugs come from the names; a reserved word or a repeated name gets -2, -3,
 * ... in unique_id order. Keep both implementations identical.
 */
export const projectSlugs = (
  projects: ReadonlyArray<{ unique_id: string; name: string; slug?: string }>,
): Map<string, string> => {
  const out = new Map<string, string>();
  if (projects.length && projects.every((p) => p.slug)) {
    projects.forEach((p) => out.set(p.unique_id, p.slug as string));
    return out;
  }
  const taken = new Set<string>();
  [...projects]
    .sort((a, b) => (a.unique_id < b.unique_id ? -1 : a.unique_id > b.unique_id ? 1 : 0))
    .forEach((p) => {
      const base = slugifyName(p.name || "") || "project";
      let slug = base;
      for (let n = 2; RESERVED.has(slug) || taken.has(slug); n += 1) slug = `${base}-${n}`;
      taken.add(slug);
      out.set(p.unique_id, slug);
    });
  return out;
};

/** The project the URL names, if it is one of `projects`. */
export const projectIdFromUrl = (
  projects: ReadonlyArray<{ unique_id: string; name: string; slug?: string }>,
): string | null => {
  if (!scope || scope.company !== getCompanySlug()) return null;
  for (const [id, slug] of projectSlugs(projects)) if (slug === scope.project) return id;
  return null;
};

/**
 * IWMS screens a signed-in company user works on (these carry the prefix),
 * as opposed to sign-in pages, the super admin console, the other portals
 * and the unscoped /<company>/login entry pages. `path` is router-relative.
 */
export const isScopedAppPage = (path: string): boolean => {
  if (path === "/login" || path.startsWith("/auth") || path.startsWith("/superadmin")) return false;
  if (path.startsWith("/localbody") || path.startsWith("/district")) return false;
  if (!scope) {
    if (path === "/") return false; // opens the super admin console
    const [, first = "", second] = path.split("/");
    if (isSlug(first) && (second === undefined || second === "" || second === "login")) return false;
  }
  return true;
};

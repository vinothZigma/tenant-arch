// Keeps /<company>/<project> in the URL in step with the signed-in company
// and the selected project (utils/urlScope.ts):
//   - signed in from /<company>/login (no project yet) -> prefix added
//   - project picked in the header -> prefix switches to that project
//   - URL names a project this user can't open -> their selected project
// Only on IWMS screens; sign-in pages, the console and other portals are left alone.
import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { useProjectSelector } from "@/contexts/ProjectSelectorContext";
import { isAuthSessionActive } from "@/utils/authStorage";
import { getCompanySlug } from "@/utils/companyScope";
import { getUrlScope, isScopedAppPage, moveToScope, projectSlugs } from "@/utils/urlScope";

export function UrlScopeSync() {
  const { pathname } = useLocation();
  const { projects, projectId } = useProjectSelector();

  useEffect(() => {
    const company = getCompanySlug();
    if (!company || !projectId || !isAuthSessionActive() || !isScopedAppPage(pathname)) return;
    const project = projectSlugs(projects).get(projectId);
    if (!project) return;
    const current = getUrlScope();
    if (current?.company === company && current.project === project) return;
    moveToScope({ company, project }, pathname);
  }, [pathname, projects, projectId]);

  return null;
}

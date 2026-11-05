// Company entry points (multi-tenancy). Companies use the existing IWMS
// screens; these only pick the company (see utils/companyScope.ts):
//
//   /<slug>/login      the IWMS sign-in page, signing in to that company
//   /<slug>#as=<token> the super admin's "Open workspace": that company's
//                      IWMS screens as one of its admins, in this tab only
//   /<slug>            -> /<slug>/login
import { useEffect } from "react";
import { Navigate, useParams } from "react-router-dom";

import Auth from "@/pages/Auth";
import { getCompanySlug, getTabToken, setCompanySlug, startTabCompanyView } from "@/utils/companyScope";

export function CompanyLogin() {
  const { slug = "" } = useParams();
  // Before Auth renders, so its sign-in request already goes to this company.
  if (getCompanySlug() !== slug || getTabToken()) setCompanySlug(slug);
  return <Auth />;
}

export function CompanyEnter() {
  const { slug = "" } = useParams();
  const hash = window.location.hash;
  const viewAs = hash.startsWith("#as=") ? decodeURIComponent(hash.slice(4)) : null;

  useEffect(() => {
    if (!viewAs) return;
    startTabCompanyView(slug, viewAs);
    // Full reload: the app restores its session from the company's API.
    window.location.replace("/");
  }, [slug, viewAs]);

  if (viewAs) return null;
  return <Navigate to={`/${slug}/login`} replace />;
}

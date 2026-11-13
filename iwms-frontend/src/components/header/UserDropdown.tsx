import { LogOut } from "lucide-react";
import { afterSignOutPath, getCompanySlug } from "@/utils/companyScope";
import { useNavigate } from "react-router-dom";
import { useUser } from "@/contexts/UserContext";
import { clearAuthSession } from "@/utils/authStorage";
import { endLoginSession } from "@/utils/loginSession";
import { useTheme } from "@/contexts/ThemeContext";
import { cn } from "@/lib/utils";
import { glassCapsuleClasses } from "@/layouts/glassStyles";

export default function UserDropdown() {
  const { user, setUser } = useUser();
  const navigate = useNavigate();
  const { theme } = useTheme();
  const isDark = theme === "dark";
  const displayName = user?.name;

  async function handleSignOut() {
    await endLoginSession("admin", "manual");
    clearAuthSession();
    setUser(null);
    const next = afterSignOutPath();
    if (next) navigate(next, { replace: true });
  }

  const initial = displayName?.trim().charAt(0).toUpperCase() || "?";
  // Company logins: avatar + name open the Company profile (plan, usage, upgrades).
  const hasCompany = Boolean(getCompanySlug());

  // User chip: initial avatar, name and sign-out in one pill.
  return (
    <div
      className={cn(
        "flex h-10 items-center gap-2 rounded-xl border py-1 pl-1 pr-1",
        isDark ? glassCapsuleClasses.dark : glassCapsuleClasses.light,
      )}
    >
      <button
        type="button"
        onClick={hasCompany ? () => navigate("/profile") : undefined}
        title={hasCompany ? "Company profile" : undefined}
        className={cn("flex items-center gap-2 rounded-lg", hasCompany ? "cursor-pointer" : "cursor-default")}
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-linear-to-br from-teal-500 to-indigo-500 text-sm font-bold text-white">
          {initial}
        </span>
        <span
          className={cn(
            "max-w-[120px] truncate text-sm font-semibold",
            isDark ? "text-slate-200" : "text-gray-800",
          )}
        >
          {displayName}
        </span>
      </button>

      <button
        onClick={handleSignOut}
        title="Sign out"
        aria-label="Sign out"
        className={cn(
          "flex h-8 w-8 items-center justify-center rounded-lg transition-colors duration-200",
          isDark
            ? "text-slate-400 hover:bg-red-500/15 hover:text-red-400"
            : "text-gray-500 hover:bg-red-50 hover:text-red-500",
        )}
      >
        <LogOut className="h-4 w-4" />
      </button>
    </div>
  );
}

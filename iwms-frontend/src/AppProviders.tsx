import type { ReactNode } from "react";
import { useSyncExternalStore } from "react";
import { BrowserRouter } from "react-router-dom";

import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { NotificationDialog } from "@/components/ui/notification-dialog";
import { GlobalRequestLoader } from "@/components/common/GlobalRequestLoader";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ModuleProvider } from "@/contexts/ModuleContext";
import { UserProvider } from "@/contexts/UserContext";
import { PermissionProvider } from "@/contexts/PermissionContext";
import { RolesProvider } from "@/contexts/RolesContext";
import { ProjectSelectorProvider } from "@/contexts/ProjectSelectorContext";
import { routerBasename, subscribeUrlScope } from "@/utils/urlScope";

type Props = {
  children: ReactNode;
};

export function AppProviders({ children }: Props) {
  // "/<company>/<project>" in front of every IWMS route (utils/urlScope.ts).
  // A new prefix remounts the router so it re-reads the URL.
  const basename = useSyncExternalStore(subscribeUrlScope, routerBasename);
  return (
    <RolesProvider>
      <ThemeProvider>
        <ModuleProvider>
          <UserProvider>
            <PermissionProvider>
              <ProjectSelectorProvider>
              <TooltipProvider>
                <BrowserRouter key={basename || "/"} basename={basename || undefined}>
                  {children}
                  <NotificationDialog />
                  <GlobalRequestLoader />
                  <Toaster />
                  <Sonner />
                </BrowserRouter>
              </TooltipProvider>
              </ProjectSelectorProvider>
            </PermissionProvider>
          </UserProvider>
        </ModuleProvider>
      </ThemeProvider>
    </RolesProvider>
  );
}

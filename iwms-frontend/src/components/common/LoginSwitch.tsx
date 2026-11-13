// "Login" switch on the Staff Creation and Staff Access Configuration lists.
// On: the staff member can sign in. Off: they can't, and an open session ends
// on its next request. PATCH <screen endpoint>/<staff id>/login/ (backend:
// app/utils/staff_login.py) changes only this, nothing else on the record.
import { useEffect, useState } from "react";
import { api } from "@/api";
import Swal from "@/lib/notify";
import { Switch } from "@/components/ui/switch";
import { adminEndpoints } from "@/helpers/admin/endpoints";

type Props = {
  staffId: string;
  checked: boolean;
  screen: "staffCreation" | "staffAccessConfiguration";
  /** The staff member's project. Without it the backend looks only in the
   *  signed-in admin's own project and answers 404 for everyone else. */
  projectId?: string | null;
  disabled?: boolean;
  onChanged?: (value: boolean) => void;
};

const errorText = (error: unknown): string => {
  const data = (error as { response?: { data?: Record<string, unknown> } })?.response?.data;
  const first = data?.login_enabled ?? data?.detail;
  if (Array.isArray(first)) return String(first[0]);
  return first ? String(first) : "Could not change the login. Please try again.";
};

export function LoginSwitch({ staffId, checked, screen, projectId, disabled, onChanged }: Props) {
  const [value, setValue] = useState(checked);
  const [saving, setSaving] = useState(false);
  // The table reuses cells across pages/reloads: follow the row's value.
  useEffect(() => setValue(checked), [checked, staffId]);

  const change = async (next: boolean) => {
    setValue(next);
    setSaving(true);
    try {
      await api.patch(
        `${adminEndpoints[screen]}/${encodeURIComponent(staffId)}/login/`,
        { login_enabled: next },
        projectId ? { params: { project_id: projectId } } : undefined,
      );
      onChanged?.(next);
    } catch (error) {
      setValue(!next);
      Swal.fire("Login", errorText(error), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Switch
      checked={value}
      disabled={disabled || saving || !staffId}
      onCheckedChange={(next) => void change(next)}
      aria-label={value ? "Login enabled" : "Login disabled"}
      title={value ? "Can sign in" : "Can't sign in"}
    />
  );
}

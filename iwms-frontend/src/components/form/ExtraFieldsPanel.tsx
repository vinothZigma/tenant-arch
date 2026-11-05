// "Additional details": the extra fields the super admin added to this form
// (console → Modules → module → forms). Rendered inside the form, above its
// buttons; values are saved together with the form (utils/formConfig.ts).
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { api } from "@/api";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { setPendingExtras, useFormConfig, type ExtraField } from "@/utils/formConfig";

type Values = Record<string, unknown>;

function FieldInput({ field, value, onChange }: { field: ExtraField; value: unknown; onChange: (v: unknown) => void }) {
  const id = `extra-${field.name}`;
  if (field.type === "boolean") {
    return (
      <label htmlFor={id} className="flex items-center gap-2 text-sm">
        <input id={id} type="checkbox" className="h-4 w-4" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        {field.label}
      </label>
    );
  }
  if (field.type === "textarea") {
    return (
      <textarea id={id} rows={3} value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-2" />
    );
  }
  const type = field.type === "number" ? "number" : field.type === "date" ? "date" : field.type === "email" ? "email" : "text";
  return <Input id={id} type={type} value={(value as string | number) ?? ""} onChange={(e) => onChange(e.target.value)} />;
}

export default function ExtraFieldsPanel({ screen, recordId }: { screen: string; recordId?: string }) {
  const { settings, extras } = useFormConfig(screen);
  const [values, setValues] = useState<Values>({});
  const [host, setHost] = useState<HTMLElement | null>(null);
  const anchor = useRef<HTMLDivElement>(null);

  // Saved values of the record being edited.
  useEffect(() => {
    if (!recordId || !extras.length) return;
    api.get<{ data: Values }>(`ws/extras/${encodeURIComponent(screen)}/${encodeURIComponent(recordId)}/`, { silent: true })
      .then((r) => setValues(r.data?.data ?? {})).catch(() => {});
  }, [screen, recordId, extras.length]);

  // Saved with the form's own create/update request.
  useEffect(() => {
    if (!settings || !extras.length) { setPendingExtras(null); return; }
    setPendingExtras({ screen, routes: settings.routes, extras, values, recordId });
  }, [screen, settings, extras, values, recordId]);
  useEffect(() => () => setPendingExtras(null), []);

  // Put the section inside the page's form, above its buttons row.
  useEffect(() => {
    if (!extras.length) return;
    const page = anchor.current?.parentElement;
    if (!page) return;
    let slot: HTMLDivElement | null = null;
    const place = () => {
      const form = page.querySelector("form");
      if (!form || slot) return Boolean(slot);
      slot = document.createElement("div");
      slot.className = "col-span-full";
      const buttons = form.lastElementChild;
      if (buttons && buttons.querySelector("button[type=submit]")) form.insertBefore(slot, buttons);
      else form.appendChild(slot);
      setHost(slot);
      return true;
    };
    // The form page loads lazily: wait for its <form> to appear.
    const observer = place() ? null : new MutationObserver(() => { if (place()) observer?.disconnect(); });
    observer?.observe(page, { childList: true, subtree: true });
    return () => { observer?.disconnect(); slot?.remove(); setHost(null); };
  }, [extras.length]);

  if (!extras.length) return <div ref={anchor} hidden />;
  const section = (
    <div className="col-span-full mt-2 border-t pt-4">
      <h3 className="mb-3 text-sm font-semibold">Additional details</h3>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        {extras.map((field) => (
          <div key={field.name}>
            {field.type !== "boolean" && (
              <Label htmlFor={`extra-${field.name}`}>
                {field.label} {field.required && <span className="text-red-500">*</span>}
              </Label>
            )}
            <FieldInput field={field} value={values[field.name]}
              onChange={(v) => setValues((cur) => ({ ...cur, [field.name]: v }))} />
          </div>
        ))}
      </div>
    </div>
  );
  return (
    <>
      <div ref={anchor} hidden />
      {host ? createPortal(section, host) : section}
    </>
  );
}

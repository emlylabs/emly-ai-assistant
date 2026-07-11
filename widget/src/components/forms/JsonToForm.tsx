import React, { useState } from 'react';

/** A single field as defined by the admin's `form_schema.form` map.
 *
 * The admin UI is loose about this shape — anything they type ends up
 * here as `Record<string, unknown>`. We pluck what we know:
 * `type`, `label`, `placeholder`, `required`, `value`, `options`,
 * `d_type`. Unknown types fall back to `<input type="text">`. */
export interface FormField {
  label?: string;
  type?: string;
  placeholder?: string;
  required?: boolean;
  value?: unknown;
  options?: Array<{ value: string; label: string }>;
  d_type?: string;
  multiple?: boolean;
  max_size_mb?: number;
  allowed_types?: string[];
}

export interface FormSchema {
  id?: string;
  name?: string;
  title?: string;
  description?: string;
  post_submission_message?: string;
  form?: Record<string, FormField>;
}

export interface JsonToFormProps {
  schema: FormSchema;
  /** `submit({...fields, form_title})`. Returns once parent has handled
   * the submission so the form can show its busy state. */
  onSubmit: (values: Record<string, unknown>) => Promise<void> | void;
  onCancel?: () => void;
  busy?: boolean;
  /** Optional remaining-submissions footnote. Shown above the action
   * row as "Note: N of M submissions remaining." Mirrors the
   * `formCount` prop from your-assistant's JsonToForm. */
  formCount?: { current: number; limit: number };
}

const isValidEmail = (s: string): boolean => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s);

const DEFAULT_MAX_FILE_MB = 24;
const DEFAULT_ALLOWED_EXTS = ['.pdf', '.doc', '.docx'];

const fileExtension = (name: string): string => {
  const i = name.lastIndexOf('.');
  return i < 0 ? '' : name.slice(i).toLowerCase();
};

const validateFileField = (
  field: FormField,
  value: unknown,
): string | null => {
  const maxMb = field.max_size_mb ?? DEFAULT_MAX_FILE_MB;
  const allowed = (field.allowed_types ?? DEFAULT_ALLOWED_EXTS).map(e => e.toLowerCase());
  const files: File[] = value instanceof File
    ? [value]
    : Array.isArray(value)
      ? value.filter((f): f is File => f instanceof File)
      : [];
  for (const file of files) {
    if (file.size > maxMb * 1024 * 1024) {
      return `${file.name} exceeds the ${maxMb} MB limit`;
    }
    if (allowed.length > 0 && !allowed.includes(fileExtension(file.name))) {
      return `${file.name} type is not allowed (expected ${allowed.join(', ')})`;
    }
  }
  return null;
};

/** Renders a JSON form schema as fields and runs basic validation on
 * submit. Mirrors `Utility/JsonToForm.js` from your-assistant — same
 * field types (`text` / `email` / `number` / `phone` / `select` /
 * `textarea` / `file` / `checkbox` / `submit` / `cancel`) but trimmed
 * (no country-code phone picker, no payment-link plumbing — those can
 * be added later when the admin UI exposes them). */
const JsonToForm: React.FC<JsonToFormProps> = ({ schema, onSubmit, onCancel, busy, formCount }) => {
  const fields = schema.form ?? {};
  const initial: Record<string, unknown> = {};
  for (const [key, f] of Object.entries(fields)) {
    if (f.type === 'submit' || f.type === 'cancel') continue;
    initial[key] = f.value ?? (f.type === 'checkbox' ? false : '');
  }
  const [values, setValues] = useState<Record<string, unknown>>(initial);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const setVal = (k: string, v: unknown) => setValues(prev => ({ ...prev, [k]: v }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const next: Record<string, string> = {};
    for (const [k, f] of Object.entries(fields)) {
      if (f.type === 'submit' || f.type === 'cancel') continue;
      const v = values[k];
      const required = f.required !== false;
      const empty =
        v === undefined ||
        v === null ||
        (typeof v === 'string' && v.trim() === '') ||
        (f.type === 'checkbox' && v === false);
      if (required && empty) {
        next[k] = `${f.label ?? k} is required`;
        continue;
      }
      if (f.type === 'email' && typeof v === 'string' && v && !isValidEmail(v)) {
        next[k] = 'Email address is not valid';
      }
      if (f.type === 'file') {
        const err = validateFileField(f, v);
        if (err) next[k] = err;
      }
    }
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    await onSubmit({ ...values, form_title: schema.name ?? schema.id ?? 'form' });
  };

  return (
    <form className="emw-form" onSubmit={handleSubmit} noValidate>
      {schema.title && <div className="emw-form-title">{schema.title}</div>}
      {schema.description && <div className="emw-form-desc">{schema.description}</div>}
      {Object.entries(fields).map(([name, f]) => {
        if (f.type === 'submit' || f.type === 'cancel') return null;
        const err = errors[name];
        const labelText = f.label ?? name;
        const common = {
          name,
          required: f.required !== false,
          placeholder: f.placeholder,
          'aria-invalid': err ? true : undefined,
          disabled: busy,
        } as const;
        let control: React.ReactNode;
        if (f.type === 'select') {
          control = (
            <select
              {...common}
              value={(values[name] as string) ?? ''}
              onChange={e => setVal(name, e.target.value)}
            >
              <option value="">Select an option</option>
              {(f.options ?? []).map(o => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          );
        } else if (f.type === 'textarea') {
          control = (
            <textarea
              {...common}
              rows={3}
              value={(values[name] as string) ?? ''}
              onChange={e => setVal(name, e.target.value)}
            />
          );
        } else if (f.type === 'checkbox') {
          control = (
            <label className="emw-form-checkbox">
              <input
                type="checkbox"
                name={name}
                disabled={busy}
                checked={Boolean(values[name])}
                onChange={e => setVal(name, e.target.checked)}
              />
              <span>{labelText}</span>
            </label>
          );
        } else if (f.type === 'file') {
          control = (
            <input
              type="file"
              name={name}
              multiple={f.multiple}
              disabled={busy}
              onChange={e => {
                const files = e.target.files ? Array.from(e.target.files) : [];
                setVal(name, f.multiple ? files : files[0] ?? null);
              }}
            />
          );
        } else {
          // text / email / number / phone / tel / url / unknown — all
          // map to `<input>` with the corresponding native type.
          const inputType =
            f.type === 'phone' || f.d_type === 'cphone' ? 'tel' : f.type ?? 'text';
          control = (
            <input
              {...common}
              type={inputType}
              value={(values[name] as string) ?? ''}
              onChange={e => setVal(name, e.target.value)}
            />
          );
        }
        return (
          <div key={name} className="emw-form-row">
            {f.type !== 'checkbox' && <label htmlFor={name}>{labelText}</label>}
            {control}
            {err && <div className="emw-form-error">{err}</div>}
          </div>
        );
      })}
      {formCount && (
        <div className="emw-form-footnote">
          Note: {Math.max(0, formCount.limit - formCount.current)} of {formCount.limit} submissions remaining.
        </div>
      )}
      <div className="emw-form-actions">
        {onCancel && (
          <button type="button" className="emw-form-btn emw-form-btn--ghost" onClick={onCancel} disabled={busy}>
            {fields.cancel?.label ?? 'Cancel'}
          </button>
        )}
        <button type="submit" className="emw-form-btn" disabled={busy}>
          {busy ? 'Sending…' : fields.submit?.label ?? 'Submit'}
        </button>
      </div>
    </form>
  );
};

export default JsonToForm;

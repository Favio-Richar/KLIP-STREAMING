import { AlertCircle } from "lucide-react";
import {
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type Ref,
  type SelectHTMLAttributes,
} from "react";
import { cn } from "../cn";

const control =
  "w-full rounded-control border border-control bg-bg-2 text-text-1 transition-colors " +
  "duration-[var(--dur-base)] hover:bg-bg-3 disabled:opacity-50 aria-[invalid=true]:border-danger";

export interface FieldProps {
  label: string;
  hint?: ReactNode;
  error?: string | undefined;
  /** Recibe los atributos de accesibilidad que debe llevar el control. */
  children: (a: {
    id: string;
    "aria-describedby": string | undefined;
    "aria-invalid": true | undefined;
  }) => ReactNode;
  className?: string;
}

/** Etiqueta + control + ayuda + error, con las asociaciones ARIA ya resueltas. */
export function Field({ label, hint, error, children, className }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const described = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ");
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-sm font-medium text-text-1">
        {label}
      </label>
      {children({
        id,
        "aria-describedby": described || undefined,
        "aria-invalid": error ? true : undefined,
      })}
      {hint ? (
        <p id={hintId} className="m-0 text-xs text-text-2">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className="m-0 flex items-start gap-1.5 text-xs text-danger">
          <AlertCircle size={14} aria-hidden className="mt-px shrink-0" />
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  dense?: boolean;
  ref?: Ref<HTMLInputElement>;
}

export function Input({ dense, className, ref, ...rest }: InputProps) {
  return (
    <input
      ref={ref}
      className={cn(control, dense ? "h-7" : "h-10 md:h-8", "px-3 text-base", className)}
      {...rest}
    />
  );
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  ref?: Ref<HTMLSelectElement>;
}

export function Select({ className, ref, ...rest }: SelectProps) {
  return (
    <select ref={ref} className={cn(control, "h-10 px-2 text-base md:h-8", className)} {...rest} />
  );
}

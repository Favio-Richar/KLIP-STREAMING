import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";
import { cn } from "../cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "dense" | "md" | "touch";

const variants: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-on-accent border-transparent hover:bg-accent-hover active:bg-accent-press",
  secondary: "bg-bg-2 text-text-1 border-control hover:bg-bg-3",
  ghost: "bg-transparent text-text-1 border-transparent hover:bg-bg-3",
  danger: "bg-bg-2 text-danger border-danger hover:bg-bg-3",
};
const sizes: Record<ButtonSize, string> = {
  dense: "h-7 px-2.5 text-sm gap-1.5",
  md: "h-10 px-3 text-base gap-2 md:h-8",
  touch: "h-10 px-4 text-base gap-2",
};

/** Clases de un botón: sirve también para enlaces (`<a>`) que deben verse como botón. */
export function buttonClasses(
  opts: { variant?: ButtonVariant; size?: ButtonSize; className?: string } = {},
): string {
  const { variant = "secondary", size = "md", className } = opts;
  return cn(
    "inline-flex select-none items-center justify-center rounded-control border font-medium no-underline",
    "transition-colors duration-[var(--dur-base)] aria-disabled:opacity-50 disabled:opacity-50",
    variants[variant],
    sizes[size],
    className,
  );
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
  ref?: Ref<HTMLButtonElement>;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  icon,
  className,
  children,
  disabled,
  type = "button",
  ref,
  ...rest
}: ButtonProps) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={buttonClasses({ variant, size, className })}
      {...rest}
    >
      {loading ? (
        <Loader2 size={16} aria-hidden className="animate-[klick-spin_0.8s_linear_infinite]" />
      ) : (
        icon
      )}
      {children}
    </button>
  );
}

export interface IconButtonProps extends Omit<ButtonProps, "icon" | "children"> {
  label: string;
  children: ReactNode;
}

/** Botón solo con icono: `label` es obligatorio porque es su nombre accesible. */
export function IconButton({ label, children, size = "md", className, ...rest }: IconButtonProps) {
  const box = size === "dense" ? "size-7" : "size-10 md:size-8";
  return (
    <Button
      {...rest}
      size={size}
      aria-label={label}
      title={label}
      className={cn(box, "!px-0", className)}
    >
      {children}
    </Button>
  );
}

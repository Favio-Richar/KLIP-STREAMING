import * as Dialog from "@radix-ui/react-dialog";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../cn";
import { Button, IconButton } from "./Button";

/** Diálogo con velo plano (color uniforme) y borde; sin sombras ni desenfoque. */
export function DialogRoot({
  open,
  onOpenChange,
  title,
  description,
  closeLabel,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  closeLabel: string;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim animate-[klick-fade-in_var(--dur-base)_var(--ease-out)]" />
        <Dialog.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 w-[calc(100vw-32px)] max-w-md -translate-x-1/2 -translate-y-1/2",
            "rounded-dialog border border-border-2 bg-bg-3 p-6",
            "animate-[klick-dialog-in_var(--dur-slow)_var(--ease-out)]",
          )}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-xl font-semibold">{title}</Dialog.Title>
              <Dialog.Description className={description ? "mt-1 text-text-2" : "sr-only"}>
                {description ?? title}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <IconButton label={closeLabel} variant="ghost" size="dense">
                <X size={16} aria-hidden />
              </IconButton>
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Confirmación de acciones destructivas: nombra la acción exacta. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel,
  closeLabel,
  onConfirm,
  loading,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel: string;
  closeLabel: string;
  onConfirm: () => void;
  loading?: boolean;
}) {
  return (
    <DialogRoot
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      closeLabel={closeLabel}
    >
      <div className="flex justify-end gap-2">
        <Button onClick={() => onOpenChange(false)}>{cancelLabel}</Button>
        <Button variant="danger" onClick={onConfirm} loading={loading ?? false}>
          {confirmLabel}
        </Button>
      </div>
    </DialogRoot>
  );
}

export const MenuRoot = Menu.Root;
export const MenuTrigger = Menu.Trigger;

export function MenuContent({
  children,
  align = "end",
}: {
  children: ReactNode;
  align?: "start" | "end";
}) {
  return (
    <Menu.Portal>
      <Menu.Content
        align={align}
        sideOffset={6}
        className="z-50 min-w-48 rounded-card border border-border-2 bg-bg-3 p-1 animate-[klick-fade-in_var(--dur-fast)_var(--ease-out)]"
      >
        {children}
      </Menu.Content>
    </Menu.Portal>
  );
}

export function MenuItem({
  children,
  onSelect,
  tone,
  icon,
}: {
  children: ReactNode;
  onSelect: () => void;
  tone?: "danger";
  icon?: ReactNode;
}) {
  return (
    <Menu.Item
      onSelect={onSelect}
      className={cn(
        "flex h-10 cursor-pointer items-center gap-2 rounded-control px-2 text-base outline-none md:h-8",
        "data-[highlighted]:bg-bg-2 data-[highlighted]:outline-2 data-[highlighted]:-outline-offset-2",
        "data-[highlighted]:outline-[var(--focus-ring)]",
        tone === "danger" ? "text-danger" : "text-text-1",
      )}
    >
      {icon}
      {children}
    </Menu.Item>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <Menu.Label className="px-2 py-1.5 text-xs text-text-2">{children}</Menu.Label>;
}

export function MenuSeparator() {
  return <Menu.Separator className="my-1 h-px bg-border-1" />;
}

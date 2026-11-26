import React from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}

export function Drawer({ open, onOpenChange, title, subtitle, children }: DrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30 backdrop-blur-[1px] transition-opacity" />
        <Dialog.Content className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-xl border-l border-hairline bg-surface text-ink flex flex-col focus:outline-none">
          <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-hairline">
            <div>
              <Dialog.Title className="text-sm font-semibold tracking-tight">{title}</Dialog.Title>
              {subtitle && (
                <Dialog.Description className="text-xs text-muted font-mono mt-0.5">
                  {subtitle}
                </Dialog.Description>
              )}
            </div>
            <Dialog.Close
              aria-label="Close inspector"
              className="rounded p-1 text-muted hover:text-ink hover:bg-subtle transition-colors"
            >
              <X className="h-4 w-4" strokeWidth={1.5} />
            </Dialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto p-5">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

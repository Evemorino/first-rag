import { useToasts } from "../store/toast";

export function Toasts() {
  const { toasts, dismiss } = useToasts();
  if (toasts.length === 0) return null;
  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 right-4 flex w-80 flex-col gap-2"
    >
      {toasts.map((toast) => (
        <button
          key={toast.id}
          className={`rounded border px-3 py-2 text-left text-sm shadow-sm ${
            toast.kind === "error"
              ? "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
              : "border-slate-300 bg-white text-slate-800 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
          }`}
          onClick={() => dismiss(toast.id)}
        >
          {toast.message}
        </button>
      ))}
    </div>
  );
}

import { type ThemeMode, useTheme } from "../store/theme";

const MODES: { mode: ThemeMode; label: string; title: string }[] = [
  { mode: "system", label: "自动", title: "跟随系统" },
  { mode: "light", label: "浅", title: "强制浅色" },
  { mode: "dark", label: "深", title: "强制深色" },
];

/** 主题开关（T125）：三档，当前档位用 `aria-pressed` 标出来。 */
export function ThemeToggle() {
  const mode = useTheme((state) => state.mode);
  const setMode = useTheme((state) => state.setMode);

  return (
    <div
      role="group"
      aria-label="主题"
      className="flex overflow-hidden rounded border border-slate-300 dark:border-slate-600"
    >
      {MODES.map(({ mode: value, label, title }) => (
        <button
          key={value}
          type="button"
          title={title}
          aria-pressed={mode === value}
          className={`px-2 py-0.5 text-xs ${
            mode === value
              ? "bg-slate-200 text-slate-900 dark:bg-slate-600 dark:text-slate-100"
              : "text-slate-500 dark:text-slate-400"
          }`}
          onClick={() => setMode(value)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

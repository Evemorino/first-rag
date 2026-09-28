/**
 * 共用样式令牌（T125 的"排版梯级与限宽"）。
 *
 * 为什么不是到处写 class：同一个控件样式散在 6 个文件里，改一次色调要改 6 处，
 * 而漏掉的那处会在暗色下露出来（深底 + 深字）。这里只放**重复出现**的形状：
 * 控件、按钮、面板、元信息文字、正文宽度。一次性的布局 class 仍留在组件里 ——
 * 令牌表一旦塞进所有东西，读 JSX 就得来回跳文件。
 */
export const CONTROL =
  "rounded border border-slate-300 px-2 py-1 text-sm text-slate-900 " +
  "dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100";

export const BUTTON =
  "rounded border border-slate-300 px-3 py-1 text-sm " +
  "hover:border-blue-500 hover:text-blue-600 disabled:opacity-40 " +
  "dark:border-slate-600 dark:text-slate-100";

export const BUTTON_DANGER = `${BUTTON} text-red-700 dark:text-red-400`;

export const PANEL = "rounded border border-slate-200 dark:border-slate-700";

/** 元信息文字：日期、来源、计数这类"次要但要有"的字。 */
export const META = "text-xs text-slate-500 dark:text-slate-400";

/** 正文行宽上限：整屏宽的一行字读起来会串行（约 80 个字符是常见上限）。 */
export const PROSE = "max-w-[80ch] whitespace-pre-wrap";

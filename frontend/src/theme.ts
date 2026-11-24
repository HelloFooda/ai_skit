/** 主题状态：默认深色，用户切换后写入 localStorage，下次进入沿用。 */
import { ref, watch } from "vue";

export type Theme = "dark" | "light";

const STORAGE_KEY = "ai-skit-theme";
const DEFAULT_THEME: Theme = "dark";

function readInitialTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    // localStorage 不可用（隐私模式等）时回退到默认值
  }
  return DEFAULT_THEME;
}

export const theme = ref<Theme>(readInitialTheme());

function apply(value: Theme) {
  const root = document.documentElement;
  root.dataset.theme = value;
  root.style.colorScheme = value;
}

// 模块加载即应用，避免首屏出现一帧默认样式
apply(theme.value);

watch(theme, (value) => {
  apply(value);
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // 存储失败不影响本次切换
  }
});

export function toggleTheme() {
  theme.value = theme.value === "dark" ? "light" : "dark";
}

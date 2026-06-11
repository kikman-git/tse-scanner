"use client";

import { useTheme, toggleTheme } from "@/lib/theme";

export default function ThemeToggle() {
  const theme = useTheme();
  return (
    <button
      className="secondary themetoggle"
      onClick={toggleTheme}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      aria-label="Toggle light/dark theme"
    >
      {theme === "dark" ? "☀️" : "🌙"}
    </button>
  );
}

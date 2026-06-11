import type { Metadata } from "next";
import "./globals.css";
import ThemeToggle from "./components/ThemeToggle";

export const metadata: Metadata = {
  title: "踏み上げ Scanner",
  description:
    "Short-squeeze setup scanner for the TSE — crowded, trapped shorts + a wound coil → the upside break that runs them.",
};

// Light is the default; honour a saved choice if present. Applied before first paint — no flash.
const NO_FLASH = `(function(){try{document.documentElement.dataset.theme=
localStorage.getItem('tse-theme')||'light';}catch(e){document.documentElement.dataset.theme='light';}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH }} />
      </head>
      <body>
        <ThemeToggle />
        {children}
      </body>
    </html>
  );
}

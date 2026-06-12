"use client";

// Glossary tooltip — wrap any jargon label: <Term k="dtc">DTC</Term>. Dotted underline; hover
// shows the definition, tap toggles it (mobile). The tip is position:fixed so it escapes
// overflow containers (.tablewrap); it flips below the anchor near the viewport top and hides
// on scroll/resize. Unknown keys render children unstyled — never break the UI over a tooltip.

import { useEffect, useRef, useState } from "react";
import { GLOSSARY } from "@/lib/glossary";

const HALF_W = 150; // half the tip width + margin, for viewport clamping

export default function Term({ k, children }: { k: string; children?: React.ReactNode }) {
  const g = GLOSSARY[k];
  const ref = useRef<HTMLSpanElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; below: boolean } | null>(null);

  useEffect(() => {
    if (!tip) return;
    const hide = () => setTip(null);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [tip]);

  if (!g) return <>{children ?? k}</>;

  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const x = Math.min(Math.max(r.left + r.width / 2, HALF_W), window.innerWidth - HALF_W);
    const below = r.top < 150; // not enough room above → open downward
    setTip({ x, y: below ? r.bottom + 8 : r.top - 8, below });
  };

  return (
    <span
      ref={ref}
      className="term"
      onMouseEnter={show}
      onMouseLeave={() => setTip(null)}
      onClick={(e) => {
        e.stopPropagation(); // rows navigate on click — a definition tap must not
        if (tip) setTip(null);
        else show();
      }}
    >
      {children ?? g.label}
      {tip && (
        <span
          className={`termtip${tip.below ? " below" : ""}`}
          style={{ left: tip.x, top: tip.y }}
          role="tooltip"
        >
          <b>{g.label}</b>
          {g.ja && <span className="ja">{g.ja}</span>}
          <span className="def">{g.def}</span>
        </span>
      )}
    </span>
  );
}

#!/usr/bin/env python3
"""Patch the upstream Hermes dashboard for the Android WebView build.

The upstream dashboard is primarily designed for desktop/browser widths.  The
Android shell embeds it inside a phone-width iframe, and Simplified Chinese
labels are generally wider than their English counterparts.  This patch adds a
small, intentionally CSS-only compatibility layer after upstream checkout so
we do not have to fork the dashboard.
"""

from __future__ import annotations

import pathlib
import sys

MARKER = "/* HERMES_ANDROID_MOBILE_PATCH */"

PATCH = r'''

/* HERMES_ANDROID_MOBILE_PATCH */
/* Android WebView / Simplified Chinese narrow-screen compatibility.
   Keep this block appended after upstream styles so it wins without changing
   the desktop dashboard source. */
@media (max-width: 768px) {
  :root {
    /* Chinese glyphs need a little more vertical breathing room than the
       compressed display defaults used by the desktop-oriented dashboard. */
    --theme-base-size: 14px;
    --theme-line-height: 1.5;
  }

  html,
  body,
  #root {
    width: 100%;
    max-width: 100%;
  }

  /* Upstream intentionally enlarges <small> to 1.0625rem for desktop.
     On phone-width cards this makes helper text collide with neighbouring
     labels after Chinese localisation. Restore semantic small text. */
  small {
    display: block;
    font-size: 0.8125rem !important;
    line-height: 1.4 !important;
    letter-spacing: 0 !important;
    white-space: normal !important;
    overflow-wrap: anywhere;
  }

  /* Flex/grid children default to min-width:auto, which lets translated
     labels push through adjacent controls.  Limit this to common dashboard
     content primitives rather than globally changing every element. */
  main,
  section,
  article,
  form,
  dl,
  dt,
  dd,
  label,
  p,
  li {
    min-width: 0;
    max-width: 100%;
  }

  p,
  li,
  dt,
  dd,
  label {
    overflow-wrap: anywhere;
    word-break: break-word;
  }

  /* Controls may contain translated labels. Do not keep an English-sized
     fixed single line at the cost of visual overlap. */
  button,
  [role="button"],
  [role="tab"] {
    min-width: 0;
  }

  button > span,
  [role="button"] > span,
  [role="tab"] > span {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  /* Long API/model/provider identifiers should scroll or wrap inside their
     own box instead of widening the whole dashboard. */
  pre,
  code {
    max-width: 100%;
  }

  pre {
    overflow-x: auto;
  }

  /* Tables are information-dense by nature. Horizontal scrolling is safer
     than squeezing columns until Chinese headers overlap cell contents. */
  table {
    width: max-content;
    min-width: 100%;
  }

  .overflow-x-auto,
  [class*="overflow-x-auto"] {
    -webkit-overflow-scrolling: touch;
  }

  /* Prevent aggressive display typography from producing clipped Chinese
     glyphs in headings while retaining the upstream visual hierarchy. */
  h1,
  h2,
  h3,
  h4,
  h5,
  h6 {
    line-height: 1.25 !important;
    overflow-wrap: anywhere;
  }
}
'''


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_dashboard_android.py <upstream-root>", file=sys.stderr)
        return 2

    root = pathlib.Path(sys.argv[1]).resolve()
    css = root / "web" / "src" / "index.css"
    if not css.is_file():
        print(f"dashboard stylesheet not found: {css}", file=sys.stderr)
        return 1

    text = css.read_text(encoding="utf-8")
    if MARKER in text:
        print("Android dashboard mobile patch already present")
        return 0

    css.write_text(text.rstrip() + PATCH + "\n", encoding="utf-8")
    print(f"patched {css}")

    # Fail loudly if the critical collision fixes somehow disappear while the
    # script is edited later.
    verify = css.read_text(encoding="utf-8")
    required = [MARKER, "small {", "font-size: 0.8125rem", "overflow-wrap: anywhere", "table {"]
    missing = [needle for needle in required if needle not in verify]
    if missing:
        print(f"patch verification failed; missing: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

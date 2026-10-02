"""Contrast ratios computed from the real hex values in web/style.css (WCAG 2.x relative luminance)."""
def lum(h):
    h = h.lstrip('#'); c = [int(h[i:i+2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True); return (la + 0.05) / (lb + 0.05)

DARK = dict(bg="#171512", s1="#201D19", s2="#2A2621", edge="#7A7165", text="#F3EFE7", mut="#B9B0A2", acc="#C8F53C", on_acc="#171512",
            acc_ink="#C8F53C", ok="#8FDDA6", warn="#F5C25B", bad="#FF8C7C", info="#9CC0FF", focus="#C8F53C")
LIGHT = dict(bg="#F4F0E6", s1="#FFFCF5", s2="#EAE4D5", edge="#857B68", text="#1C1915", mut="#5A5347", acc="#C8F53C", on_acc="#171512",
             acc_ink="#364D00", ok="#1D6A38", warn="#7D5300", bad="#A12E1C", info="#2848A0", focus="#1C1915")
PAIRS = [("body text", "text", "bg", 4.5), ("body text on panel", "text", "s1", 4.5), ("muted text on page", "mut", "bg", 4.5),
         ("muted text on panel", "mut", "s1", 4.5), ("muted text on inset", "mut", "s2", 4.5), ("link / live state on panel", "acc_ink", "s1", 4.5),
         ("done state", "ok", "s1", 4.5), ("retry / held state", "warn", "s1", 4.5), ("failed state", "bad", "s1", 4.5),
         ("reconciled-ledger state", "info", "s1", 4.5), ("primary button label", "on_acc", "acc", 4.5), ("button text on inset", "text", "s2", 4.5),
         ("control border vs panel (non-text)", "edge", "s1", 3.0), ("focus ring vs panel (non-text)", "focus", "s1", 3.0),
         ("progress bar, live vs track (non-text)", "acc_ink", "s2", 3.0)]
if __name__ == "__main__":
    print("| Pair | Dark | Light | Needs |\n|---|---|---|---|")
    bad = 0
    for name, f, b, need in PAIRS:
        d, l = ratio(DARK[f], DARK[b]), ratio(LIGHT[f], LIGHT[b])
        flag = "" if d >= need and l >= need else " FAIL"; bad += bool(flag)
        print(f"| {name} | {d:.2f}:1 | {l:.2f}:1 | {need}:1{flag} |")
    raise SystemExit(bad)

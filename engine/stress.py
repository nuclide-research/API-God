"""Adversarial stress of the SHARED engine_core logic (tests exactly what runs)."""
from engine_core import norm_name, cashtag_hit, zone_of, score_resolved

BREAKS = []
def check(name, ok, detail):
    if not ok: BREAKS.append(name)
    print(f"  [{'ok   ' if ok else 'BREAK'}] {name}: {detail}")

print("=== 1. cashtag verification (post-fix) ===")
check("common-word MOON needs $", not cashtag_hit("MOON", "we are going to the moon"),
      f"bare 'moon' -> {cashtag_hit('MOON','we are going to the moon')} (want False)")
check("common-word TRUMP needs $", not cashtag_hit("TRUMP", "Trump announced tariffs"),
      f"bare 'Trump' -> {cashtag_hit('TRUMP','Trump announced tariffs')} (want False)")
check("$MOON still matches", cashtag_hit("MOON", "buy $MOON now"), "explicit cashtag (good)")
check("real ticker still matches", cashtag_hit("WIFHAT", "gm $WIFHAT holders"), "non-dictionary ticker (good)")

print("\n=== 2. dedup normalization (post-fix) ===")
def raw(b): return {"fullwidth":"Ｔｏｋｅｎ","punct":"Token!","trail":"Token ","cyrillic":"Тoken","leet":"T0ken"}[b]
for b in ["fullwidth","punct","trail","cyrillic","leet"]:
    same = norm_name("Token") == norm_name(raw(b))
    want = b in ("fullwidth","punct","trail")   # these we expect to now collapse
    check(f"dedup Token vs {b}", same == want,
          f"collapses={same} (want {want}; cyrillic/leet are accepted residual, wallet-clustering covers)")

print("\n=== 3. handle-mismatch voids verification (post-fix) ===")
s_spoof,_ = score_resolved("amber", refs=True, blue=True, mism=True)
check("spoofed handle cannot net positive", s_spoof <= 0,
      f"verified-looking tweet from mismatched handle -> score={s_spoof} (want <=0)")
s_clean,_ = score_resolved("amber", refs=True, blue=True, mism=False)
check("clean verified still scores", s_clean >= 4, f"legit verified+blue -> {s_clean}")

print("\n=== 4. perfect-fake (still a BUILD, not fixed) ===")
s_fake,_ = score_resolved("red", refs=True, blue=True, mism=False)
check("perfect-fake neutralized", s_fake < 4,
      f"fresh-blue + self-written cashtag + 5SOL -> {s_fake} -> STILL high; self-attested signals need the BUILD (independent signals), not a patch")

print("\n=== 5. SPC baseline poisoning (still a BUILD) ===")
normal=[0.2]*80+[0.5]*15+[2,3,4,5,8]; cheap=normal+[0.01]*300
check("cheap-flood resisted", zone_of(0.5,cheap)==zone_of(0.5,normal),
      f"0.5SOL zones {zone_of(0.5,normal)}->{zone_of(0.5,cheap)} under flood -> BUILD (historical baseline)")

print("\n" + "="*60)
print(f"BREAKS REMAINING: {len(BREAKS)}  -> {BREAKS}")

"""Throwaway C-test: does the council BEAT a single model on hard/divergent Qs?
Shows each proposal so we can see the panel correcting a wrong advisor."""
import time
import jarvis_council as jc

# Questions chosen to make models diverge (classic single-model traps with
# verifiable answers), so we can see whether aggregation fixes wrong proposers.
QUESTIONS = [
    ("How many times does the letter 'r' appear in the word 'strawberry'? "
     "Give just the count and the positions.", "correct: 3"),
    ("I have 3 apples today. Yesterday I ate 1 apple. How many apples do I "
     "have now?", "correct: 3 (yesterday is irrelevant)"),
]

for q, truth in QUESTIONS:
    print("\n" + "=" * 64)
    print("Q:", q)
    print(truth)
    print("=" * 64)

    # Single-model baseline = the fast model JarvisChat would otherwise use
    print("\n--- SINGLE (qwen2.5:7b) ---")
    t0 = time.time()
    try:
        s = jc._strip_think(jc._ollama_chat("qwen2.5:7b",
                [{"role": "user", "content": q}], num_predict=512))
        print(f"[{time.time()-t0:.0f}s] {s[:220]}")
    except Exception as e:
        print("err", e)

    # Council
    print("\n--- COUNCIL ---")
    t0 = time.time()
    res = jc.run_council(q, status_cb=lambda m: print("  ", m))
    print(f"\n[{time.time()-t0:.0f}s] agreement={res['agreement']:.3f} "
          f"disagreed={res['disagreed']}")
    print("\nPER-PROPOSAL (did advisors disagree?):")
    for model, ans in res["proposals"]:
        print(f"  [{model}] {ans[:140].strip()}")
    print("\nAGGREGATED FINAL:")
    print(" ", res["answer"][:300].strip())

import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
s = json.load(open(r"C:\Users\Administrator\200x_commander\rt_paper_v2_state_rune.json", encoding='utf-8'))
eq = sum(v.get("equity", 100) for v in s.values())
tr = sum(len(v.get("trades", [])) for v in s.values())
print(f"Total: {eq:.2f}U | Traded: {tr}/{len(s)}")

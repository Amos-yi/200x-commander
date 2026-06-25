import re
with open(r"C:\Users\Administrator\gate_bot\core\okx_strategies.py", encoding="utf-8") as f:
    text = f.read()
print(f"Core @_reg count: {len(re.findall(r'@_reg', text))}")

with open(r"C:\Users\Administrator\gate_bot\core\okx_strategies_advanced.py", encoding="utf-8") as f:
    text2 = f.read()
print(f"Advanced @_reg count: {len(re.findall(r'@_reg', text2))}")
print(f"Total: {len(re.findall(r'@_reg', text)) + len(re.findall(r'@_reg', text2))}")

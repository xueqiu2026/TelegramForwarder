import re
import os

with open('handlers/bot_handler.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Find all create_.*_buttons functions
functions = re.findall(r'def (create_[a-zA-Z0-9_]+_buttons)\([^)]*\):.*?(?=\ndef |$)', code, re.DOTALL)

with open('settings_debug.txt', 'w', encoding='utf-8') as out:
    for func in functions:
        # Extract the function body
        match = re.search(rf'def {func}\([^)]*\):.*?(?=\ndef |$)', code, re.DOTALL)
        if match:
            out.write(f"=== {func} ===\n")
            out.write(match.group(0))
            out.write("\n\n")

print("Dumped", len(functions), "functions to settings_debug.txt")

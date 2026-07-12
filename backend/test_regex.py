import sys
sys.path.insert(0, 'backend')
from rule_engine import find_all

tests = [
    ('Your test', 'Hello My Name Is Antigravity CLI and My Phone Number is +91 9909092253 and my api is GKS_SHDUGFSIH86875fs7f5sfUGFSsjfhg'),
    ('US Phone', 'Call me at +1 555-867-5309 or +44 20 7946 0958'),
    ('SK Key', 'API key: sk-proj-abc12345678901234567890 and AWS: AKIAIOSFODNN7EXAMPLE'),
    ('Groq Key', 'My Groq key is gsk_abcdefghijklmnopqrstuvwxyz123456'),
]

all_pass = True
for label, text in tests:
    matches = find_all(text)
    print(f'TEST: {label}')
    print(f'  Input: {text[:80]}')
    if not matches:
        print('  *** NO MATCHES FOUND ***')
        all_pass = False
    for m in matches:
        print(f'  -> [{m.entity_type}] "{m.original}"')
    print()

print('ALL PASS' if all_pass else 'SOME TESTS FAILED')

import glob

ficheiros = (
    glob.glob('data/**/*.json', recursive=True) +
    glob.glob('logs/**/*.json', recursive=True)
)

trades = [f for f in ficheiros if 'trade' in f.lower()]
print("Ficheiros de trades encontrados:")
for f in trades:
    print(" ", f)

if not trades:
    print("Nenhum ficheiro encontrado com 'trade' no nome")
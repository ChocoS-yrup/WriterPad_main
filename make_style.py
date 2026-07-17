with open('style.qss', 'r', encoding='utf-8') as f:
    qss_content = f.read()

with open('style_sheet.py', 'w', encoding='utf-8') as f:
    f.write('QSS_STYLE = """\\n')
    f.write(qss_content)
    f.write('\\n"""\\n')

print("Created style_sheet.py")

with open("c:\\Users\\Yash Thadani\\Desktop\\Engineering\\spinedge\\engine\\mvp2\\gui\\main_gui.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

with open("c:\\Users\\Yash Thadani\\Desktop\\Engineering\\spinedge\\engine\\mvp2\\defs3.txt", "w", encoding="utf-8") as out:
    for i, line in enumerate(lines):
        if line.strip().startswith("def "):
            out.write(f"{i+1}: {line}")

from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
pdf = ROOT/'交付材料/论文/C题论文_技术定稿.pdf'
with pymupdf.open(pdf) as doc:
    for i, page in enumerate(doc):
        text = page.get_text()
        print(i+1, len(text.strip()), text[:70].replace('\n', ' / '), ' END: ', text[-110:].replace('\n', ' / '))

"""Text-layer tables, code-anchored borderless forms and numeric text blocks."""
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation


def clean(text):
    return " ".join((text or "").split())


def decimal_text(text):
    text = re.sub(r"\s", "", text).replace("−", "-")
    parentheses = text.startswith("(") and text.endswith(")")
    if parentheses:
        text = "-" + text[1:-1]
    if not re.fullmatch(r"[-+]?\d+(?:[,.]\d+)?", text):
        return None
    try:
        return format(Decimal(text.replace(",", ".")), "f")
    except InvalidOperation:
        return None


def make_table(matrix, boxes, xs, ys, table_id, method, first_numeric_override=None):
    from parse_structure import header_hierarchy
    width = len(xs)-1
    cells = []
    for r, row in enumerate(matrix):
        for c, text in enumerate(row):
            box = boxes[r][c]
            if box is None:
                continue
            c0 = min(range(len(xs)), key=lambda i: abs(xs[i]-box[0]))
            c1 = min(range(len(xs)), key=lambda i: abs(xs[i]-box[2]))
            r1 = min(range(len(ys)), key=lambda i: abs(ys[i]-box[3]))
            cells.append({"id": f"r{r+1}c{c0+1}", "row": r, "column": c0,
                          "row_span": max(1,r1-r), "column_span": max(1,c1-c0),
                          "bbox": [round(v,2) for v in box], "text": clean(text),
                          "confidence": None, "method": method, "needs_review": False})
    start = next((r for r,row in enumerate(matrix) if r > 0 and decimal_text(row[-1] or "") is not None), None)
    if start is None:
        return None
    section_start=next((r for r,row in enumerate(matrix[:start]) if any(re.search(r'ЦК\s*№',str(x or '')) for x in row)),None)
    if section_start is not None:
        start=section_start
    first_numeric = width
    for c in range(width-1,-1,-1):
        fraction = sum(decimal_text(row[c] or "") is not None for row in matrix[start:])/max(1,len(matrix)-start)
        if fraction < .3:
            break
        first_numeric = c
    if first_numeric == width or first_numeric == 0:
        return None
    if first_numeric_override is not None:
        first_numeric=first_numeric_override
    tree, columns = header_hierarchy(cells,start,width)
    for column in columns:
        column["role"] = "row_header" if column["number"] <= first_numeric else "value"
    rows, section = [], None
    for r in range(start,len(matrix)):
        covering = [c for c in cells if c["row"] <= r < c["row"]+c["row_span"]]
        labels = sorted([c for c in covering if c["column"] < first_numeric],key=lambda c:c["column"])
        label_text = " / ".join(c["text"] for c in labels if c["text"])
        sections = re.findall(r"ЦК\s*№\s*\d+[^\n]*", "\n".join(str(x or "") for x in matrix[r][:first_numeric]))
        if sections:
            section = clean(sections[-1])
        numeric = [c for c in covering if c["column"] >= first_numeric]
        if not any(decimal_text(c["text"]) is not None for c in numeric):
            continue
        path = ([section] if section and section != label_text else []) + [c["text"] for c in labels if c["text"]]
        values = []
        for cell in numeric:
            parsed = decimal_text(cell["text"])
            values.append({"column_number": cell["column"]+1,"column_span":cell["column_span"],
                           "column_path":columns[cell["column"]]["header_path"], "cell_id":cell["id"],
                           "raw":cell["text"],"value":parsed if parsed is not None else cell["text"],
                           "value_type":"decimal" if parsed is not None else "string","needs_review":False,
                           "accounting_parentheses":cell["text"].startswith("(")})
        rows.append({"grid_row":r+1,"row_path":path,"row_header_cell_ids":[c["id"] for c in labels],"values":values})
    return {"id":table_id,"bbox":[xs[0],ys[0],xs[-1],ys[-1]],
            "grid":{"rows":len(matrix),"columns":width,"x":xs,"y":ys},"assumptions":[],"warnings":[],
            "extraction_method":method,"header_row_count":start,"column_number_grid_row":None,
            "header_detection":"native_numeric_columns","row_header_column_count":first_numeric,
            "header_tree":tree,"columns":columns,"rows":rows,"cells":cells}


def vector_tables(page, number):
    tables = []
    for table in page.find_tables().tables:
        matrix = table.extract()
        if not matrix or len(matrix)<3:
            continue
        boxes = [row.cells for row in table.rows]
        raw_x = sorted({box[i] for row in boxes for box in row if box for i in (0,2)})
        xs = []
        for x in raw_x:
            if not xs or x-xs[-1] > 2:
                xs.append(x)
        if len(xs) != table.col_count+1:
            continue
        ys = [r.bbox[1] for r in table.rows]+[table.bbox[3]]
        parsed = make_table(matrix,boxes,xs,ys,f"p{number}.t{len(tables)+1}","pdf-vector-grid")
        if parsed:
            tables.append(parsed)
    return tables


def coded_table(page, number, words):
    """Infer a table only when a printed Код header and year columns coexist."""
    anchors = [w for w in words if w[4].strip(" .:").casefold()=="код"]
    if len(anchors)!=1:
        return []
    anchor = anchors[0]
    year_words = [w for w in words if w[0]>anchor[2] and abs(w[1]-anchor[1])<15 and re.search(r"20\d{2}",w[4])]
    if len(year_words)<2:
        return []
    codes = sorted([w for w in words if abs((w[0]+w[2])/2-(anchor[0]+anchor[2])/2)<22
                    and w[1]>anchor[3] and re.fullmatch(r"\d{4}",w[4])],key=lambda w:w[1])
    if len(codes)<3:
        return []
    year_words.sort(key=lambda w:w[0])
    # Column boundaries come from the code/year anchors, independent of values.
    code_center=(anchor[0]+anchor[2])/2
    year_centers=[(w[0]+w[2])/2 for w in year_words]
    xs=[min(w[0] for w in words if anchor[1]-4 <= w[1] <= codes[-1][3]),
        anchor[0]-8,max(w[2] for w in codes)+3,
        (year_centers[0]+year_centers[1])/2,page.rect.width-15]
    # First column combines notes and the printed indicator description.
    ys=[anchor[1]-5,anchor[3]+4]+[(codes[i-1][3]+codes[i][1])/2 for i in range(1,len(codes))]+[codes[-1][3]+8]
    matrix,boxes=[],[]
    header=["Наименование показателя / Пояснения","Код",f"За {re.search(r'20\d{2}',year_words[0][4])[0]} г.",f"За {re.search(r'20\d{2}',year_words[1][4])[0]} г."]
    for r in range(len(ys)-1):
        row=[]; row_boxes=[]
        for c in range(4):
            box=[xs[c],ys[r],xs[c+1],ys[r+1]]
            selected=sorted([w for w in words if box[0] <= (w[0]+w[2])/2 < box[2]
                             and box[1] <= (w[1]+w[3])/2 < box[3]],key=lambda w:(round(w[1]/3),w[0]))
            row.append(header[c] if r==0 else " ".join(w[4] for w in selected));row_boxes.append(box)
        matrix.append(row);boxes.append(row_boxes)
    table=make_table(matrix,boxes,xs,ys,f"p{number}.t1","pdf-code-anchors",first_numeric_override=2)
    if table:
        table["assumptions"].append("borderless_rows_inferred_from_code_baselines")
    return [table] if table else []


def parse_native_page(page, number):
    words=page.get_text("words",sort=False)
    tables=vector_tables(page,number) or coded_table(page,number,words)
    lines=defaultdict(list)
    for w in words:
        lines[tuple(w[5:7])].append(w)
    blocks=[]; numbers=[]
    for line in lines.values():
        line.sort(key=lambda w:w[0])
        bbox=[min(w[0] for w in line),min(w[1] for w in line),max(w[2] for w in line),max(w[3] for w in line)]
        if any(t["bbox"][0] <= (bbox[0]+bbox[2])/2 <= t["bbox"][2] and t["bbox"][1] <= (bbox[1]+bbox[3])/2 <= t["bbox"][3] for t in tables):
            continue
        block={"text":clean(" ".join(w[4] for w in line)),"bbox":bbox,"confidence":100,
               "words":[{"text":w[4],"bbox":list(w[:4])} for w in line]}
        blocks.append(block)
        for index,w in enumerate(line):
            value=decimal_text(w[4])
            if value is not None:
                numbers.append({"id":f"p{number}.n{len(numbers)+1}","value":value,"raw":w[4],"bbox":list(w[:4]),
                                "context":block["text"],"method":"pdf-text-number"})
    return {"number":number,"size_points":[round(page.rect.width,2),round(page.rect.height,2)],
            "coordinate_system":"PDF points, top-left origin; bbox=[x0,y0,x1,y1]",
            "text_source":"pdf-text","text_blocks":blocks,"numeric_fields":numbers,"tables":tables}

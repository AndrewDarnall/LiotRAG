""" Input PDF File Parser """
from fitz import open as fitz_open

def extract_pdf_info(path, parse_forms=True, parse_annotations=True):
    doc = fitz_open(path)
    full_text = ""
    form_data = {}
    annotations = []

    for page in doc:
        full_text += page.get_text("text")

        if parse_forms:
            try:
                widgets = page.widgets()
                if widgets:
                    for w in widgets:
                        form_data[w.field_name] = w.field_value
            except Exception:
                pass

        if parse_annotations:
            try:
                annots = page.annots()
                if annots:
                    for annot in annots:
                        content = annot.info.get("content", "")
                        if content:
                            annotations.append(content.strip())
            except Exception as e:
                print(f"Annotation parsing failed: {e}")

    if annotations:
        full_text += "\n\n[Annotations Extracted]\n" + "\n".join(annotations)

    return full_text, form_data
